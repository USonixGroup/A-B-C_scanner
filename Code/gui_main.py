"""Compatibility launcher for the PySide6 scanner GUI."""

import sys


def _load_main():
    try:
        from .pyside_gui import main as qt_main
    except ImportError:
        try:
            from pyside_gui import main as qt_main
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
