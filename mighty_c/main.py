"""
mighty_c/main.py — Entry Point
===============================

Invokes the Typer CLI application defined in ``mighty_c.cli``.

Usage:
    python -m mighty_c compile test.mc --emit-llvm
    python -m mighty_c compile test.mc -o test
    python -m mighty_c dump-ast test.mc
    python -m mighty_c version
"""

from mighty_c.cli import app


def main() -> None:
    """Entry point for the ``mighty-c`` command."""
    app()


if __name__ == "__main__":
    main()
