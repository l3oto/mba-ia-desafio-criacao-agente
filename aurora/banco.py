"""Dados do condomínio em SQLite: áreas, reservas e visitantes."""

from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
DADOS = RAIZ / "dados"
ARQUIVO = Path(os.environ.get("AURORA_BANCO", RAIZ / "estado" / "condominio.db"))

ESQUEMA = """
CREATE TABLE IF NOT EXISTS apartamentos (
    numero  TEXT PRIMARY KEY,
    morador TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS areas (
    id   TEXT PRIMARY KEY,
    nome TEXT NOT NULL,
    taxa REAL NOT NULL
);

-- AUTOINCREMENT garante que um id nunca é reaproveitado, nem depois de
-- cancelar ou restaurar, e o código das reservas novas sai desse id.
CREATE TABLE IF NOT EXISTS reservas (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    codigo      TEXT NOT NULL UNIQUE,
    apartamento TEXT NOT NULL REFERENCES apartamentos (numero),
    area        TEXT NOT NULL REFERENCES areas (id),
    data        TEXT NOT NULL,
    cancelada   INTEGER NOT NULL DEFAULT 0
);

-- A exclusividade vale no instante da gravação: duas reservas ativas para a
-- mesma área e data violam este índice, venha de onde vier o INSERT.
CREATE UNIQUE INDEX IF NOT EXISTS uma_reserva_por_area_e_data
    ON reservas (area, data) WHERE cancelada = 0;

CREATE TABLE IF NOT EXISTS visitantes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    apartamento TEXT NOT NULL REFERENCES apartamentos (numero),
    nome        TEXT NOT NULL,
    data        TEXT NOT NULL
);
"""

# Reservas criadas pelo sistema começam aqui, longe dos códigos de quatro
# dígitos que vêm em dados/reservas.json.
PRIMEIRO_ID_NOVO = 10000


@dataclass(frozen=True)
class Area:
    id: str
    nome: str
    taxa: float


class DataOcupada(Exception):
    """A área já tem reserva ativa na data."""


@contextmanager
def conexao():
    ARQUIVO.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(ARQUIVO, timeout=10)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    try:
        with con:
            yield con
    finally:
        con.close()


def _carregar(nome: str) -> list[dict]:
    return json.loads((DADOS / nome).read_text(encoding="utf-8"))


def preparar() -> None:
    """Cria o banco na primeira subida. Não mexe em dados que já existem."""
    with conexao() as con:
        con.execute("PRAGMA journal_mode = WAL")
        con.executescript(ESQUEMA)
        if con.execute("SELECT COUNT(*) FROM apartamentos").fetchone()[0] == 0:
            _semear(con)


def restaurar() -> None:
    """Volta reservas e visitantes ao estado dos arquivos em dados/."""
    with conexao() as con:
        con.executescript(ESQUEMA)
        _semear(con)


def _semear(con: sqlite3.Connection) -> None:
    con.execute("DELETE FROM visitantes")
    con.execute("DELETE FROM reservas")
    con.execute("DELETE FROM areas")
    con.execute("DELETE FROM apartamentos")
    con.executemany("INSERT INTO apartamentos (numero, morador) VALUES (:numero, :morador)", _carregar("apartamentos.json"))
    con.executemany("INSERT INTO areas (id, nome, taxa) VALUES (:id, :nome, :taxa)", _carregar("areas.json"))
    con.executemany(
        "INSERT INTO reservas (codigo, apartamento, area, data) VALUES (:codigo, :apartamento, :area, :data)",
        _carregar("reservas.json"),
    )
    con.executemany(
        "INSERT INTO visitantes (apartamento, nome, data) VALUES (:apartamento, :nome, :data)",
        _carregar("visitantes.json"),
    )
    # Só avança a sequência: depois de uma restauração, os ids já usados
    # continuam fora de alcance.
    con.execute(
        "UPDATE sqlite_sequence SET seq = MAX(seq, ?) WHERE name = 'reservas'",
        (PRIMEIRO_ID_NOVO,),
    )


def areas() -> list[Area]:
    with conexao() as con:
        return [Area(**dict(r)) for r in con.execute("SELECT id, nome, taxa FROM areas ORDER BY nome")]


def area(area_id: str) -> Area | None:
    with conexao() as con:
        linha = con.execute("SELECT id, nome, taxa FROM areas WHERE id = ?", (area_id,)).fetchone()
    return Area(**dict(linha)) if linha else None


def apartamento_existe(numero: str) -> bool:
    with conexao() as con:
        return con.execute("SELECT 1 FROM apartamentos WHERE numero = ?", (numero,)).fetchone() is not None


def reservas_do_apartamento(apartamento: str) -> list[dict]:
    with conexao() as con:
        linhas = con.execute(
            "SELECT codigo, area, data FROM reservas WHERE apartamento = ? AND cancelada = 0 ORDER BY data, codigo",
            (apartamento,),
        )
        return [dict(r) for r in linhas]


def data_livre(area_id: str, data: str) -> bool:
    with conexao() as con:
        ocupada = con.execute(
            "SELECT 1 FROM reservas WHERE area = ? AND data = ? AND cancelada = 0", (area_id, data)
        ).fetchone()
    return ocupada is None


def reservar(apartamento: str, area_id: str, data: str) -> str:
    """Grava a reserva e devolve o código. Levanta DataOcupada se perder a disputa."""
    with conexao() as con:
        try:
            cursor = con.execute(
                "INSERT INTO reservas (codigo, apartamento, area, data) VALUES ('', ?, ?, ?)",
                (apartamento, area_id, data),
            )
        except sqlite3.IntegrityError as erro:
            if "reservas.area" in str(erro):
                raise DataOcupada(area_id, data) from None
            raise
        codigo = f"RSV-{cursor.lastrowid}"
        con.execute("UPDATE reservas SET codigo = ? WHERE id = ?", (codigo, cursor.lastrowid))
    return codigo


def cancelar(apartamento: str, area_id: str, data: str) -> str | None:
    """Cancela a reserva da área na data se ela for do apartamento e devolve o código."""
    with conexao() as con:
        linha = con.execute(
            "UPDATE reservas SET cancelada = 1"
            " WHERE apartamento = ? AND area = ? AND data = ? AND cancelada = 0 RETURNING codigo",
            (apartamento, area_id, data),
        ).fetchone()
    return linha["codigo"] if linha else None


def visitantes_do_apartamento(apartamento: str) -> list[dict]:
    with conexao() as con:
        linhas = con.execute(
            "SELECT nome, data FROM visitantes WHERE apartamento = ? ORDER BY data, nome", (apartamento,)
        )
        return [dict(r) for r in linhas]


def autorizar_visitante(apartamento: str, nome: str, data: str) -> None:
    with conexao() as con:
        con.execute(
            "INSERT INTO visitantes (apartamento, nome, data) VALUES (?, ?, ?)", (apartamento, nome, data)
        )
