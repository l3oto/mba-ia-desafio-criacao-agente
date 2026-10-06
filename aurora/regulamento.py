"""Busca no regulamento interno: devolve só os artigos do capítulo que trata do assunto."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from functools import cache

from aurora.banco import DADOS

PALAVRAS_VAZIAS = set(
    """
    a o as os um uma uns umas de do da dos das no na nos nas em por para pelo pela com sem
    e ou que qual quais quando como onde se ao aos à às é são ser estar há tem ter pode posso
    meu minha seu sua isso esse essa este esta até sobre mais menos muito qual quais horas hora
    regra regras regulamento condominio pra pro
    """.split()
)


@dataclass(frozen=True)
class Artigo:
    texto: str
    termos: frozenset[str]


@dataclass(frozen=True)
class Capitulo:
    titulo: str
    termos_do_titulo: frozenset[str]
    artigos: tuple[Artigo, ...]


def _normalizar(texto: str) -> str:
    sem_acento = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    return sem_acento.lower()


def _termos(texto: str) -> frozenset[str]:
    palavras = re.findall(r"[a-z0-9]+", _normalizar(texto))
    return frozenset(_raiz(p) for p in palavras if p not in PALAVRAS_VAZIAS and len(p) > 2)


def _raiz(palavra: str) -> str:
    # Radical grosseiro, o suficiente para "domingos" achar "domingo" e
    # "visitantes" achar "visitante".
    for sufixo in ("oes", "aes", "es", "s"):
        if palavra.endswith(sufixo) and len(palavra) - len(sufixo) >= 4:
            return palavra[: -len(sufixo)]
    return palavra


@cache
def capitulos() -> tuple[Capitulo, ...]:
    texto = (DADOS / "regulamento.md").read_text(encoding="utf-8")
    resultado = []
    for bloco in re.split(r"^## ", texto, flags=re.MULTILINE)[1:]:
        titulo, _, corpo = bloco.partition("\n")
        artigos = [a.strip() for a in re.split(r"^(?=\*\*Art\. )", corpo, flags=re.MULTILINE) if a.strip()]
        resultado.append(
            Capitulo(
                titulo=titulo.strip(),
                termos_do_titulo=_termos(titulo),
                artigos=tuple(Artigo(texto=a, termos=_termos(a)) for a in artigos),
            )
        )
    return tuple(resultado)


def consultar(pergunta: str, limite: int = 3) -> dict:
    """Escolhe o capítulo mais aderente à pergunta e devolve até `limite` artigos dele."""
    termos = _termos(pergunta)
    if not termos:
        return {"encontrado": False}

    def pontos_do_capitulo(cap: Capitulo) -> int:
        no_corpo = max((len(termos & art.termos) for art in cap.artigos), default=0)
        return 3 * len(termos & cap.termos_do_titulo) + no_corpo

    melhor = max(capitulos(), key=pontos_do_capitulo)
    if pontos_do_capitulo(melhor) == 0:
        return {"encontrado": False}

    ordenados = sorted(melhor.artigos, key=lambda art: len(termos & art.termos), reverse=True)
    relevantes = [art.texto for art in ordenados[:limite] if termos & art.termos] or [melhor.artigos[0].texto]
    return {"encontrado": True, "capitulo": melhor.titulo, "artigos": relevantes}
