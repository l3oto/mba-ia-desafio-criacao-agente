"""Volta reservas e visitantes ao estado de dados/. Com --sessoes, apaga também as conversas.

    uv run python -m aurora.restaurar [--sessoes]

Rode com a API parada.
"""

from __future__ import annotations

import argparse

from aurora import banco


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--sessoes", action="store_true", help="apaga também o banco de sessões")
    args = parser.parse_args()

    banco.restaurar()
    print(f"reservas e visitantes restaurados em {banco.ARQUIVO}")
    if args.sessoes:
        for arquivo in (banco.RAIZ / "estado").glob("sessoes.db*"):
            arquivo.unlink()
        print("sessoes apagadas")


if __name__ == "__main__":
    main()
