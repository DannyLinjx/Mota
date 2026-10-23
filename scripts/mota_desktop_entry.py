"""PyInstaller entry point: desktop GUI and the engine subprocess."""

import sys


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "engine":
        from mota.cli.main import app

        app()
        return 0
    from mota.desktop.main import run_desktop

    return run_desktop()


raise SystemExit(main())
