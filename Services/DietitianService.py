from pathlib import Path


URL_FILE = (
    Path(__file__).resolve().parent.parent
    / "Url_docs"
    / "url_answer_escort.txt"
)


class DietitianService:

    def __init__(self, url_file: Path = URL_FILE):
        self.url_file = url_file

    def get_dietitian_url(self) -> str:
        return self.url_file.read_text(encoding="utf-8").strip()
