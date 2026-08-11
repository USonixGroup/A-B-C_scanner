"""Compatibility launcher for the PySide6 scanner GUI."""

import os
import sys


def _load_main():
    current_dir = os.path.dirname(os.path.abspath(__file__))
    parent_dir = os.path.dirname(current_dir)
    for candidate in [current_dir, parent_dir]:
        if candidate not in sys.path:
            sys.path.insert(0, candidate)

    try:
        from api.src.pyside_gui import main as qt_main
    except ImportError:
        try:
            from src.pyside_gui import main as qt_main
        except ModuleNotFoundError as exc:
            if exc.name in {"PySide6", "matplotlib"}:
                raise SystemExit(
                    "Missing GUI dependency: "
                    f"{exc.name}. Install the required packages with: "
                    "pip install PySide6 matplotlib pandas"
                )
            raise
    return qt_main


if __name__ == "__main__":
    _load_main()()
