import sys


def main() -> int:
    if "--version" in sys.argv:
        from . import __version__

        print(__version__)
        return 0
    from .gui.main_window import run

    return run(simulate="--simulate" in sys.argv)


if __name__ == "__main__":
    sys.exit(main())
