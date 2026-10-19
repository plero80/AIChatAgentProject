"""Run a CLI task: python main.py chat or python main.py ingest --file PATH."""

from backend.bootstrap import main


if __name__ == "__main__":
    raise SystemExit(main())
