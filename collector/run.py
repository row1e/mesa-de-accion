"""Lanzador independiente del directorio actual: `.venv/bin/python run.py [run <fuente|all>]`."""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from mesa.__main__ import main  # noqa: E402

main()
