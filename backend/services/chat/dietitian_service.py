from pathlib import Path

from backend.paths import DATA_DIR
from backend.services.contracts import BaseDietitianService


URL_FILE = DATA_DIR / "links" / "url_answer_escort.txt"


class DietitianService(BaseDietitianService):

    def __init__(self, url_file: Path = URL_FILE):
        self.url_file = url_file

    def get_dietitian_url(self) -> str:
        return self.url_file.read_text(encoding="utf-8").strip()
