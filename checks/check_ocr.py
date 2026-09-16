"""Run from the project root: python checks/check_ocr.py."""
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import chemkey as ck


class Page:
    def __init__(self, text):
        self.text = text

    def extract_text(self):
        return self.text


class Reader:
    def __init__(self, _path):
        self.pages = [
            Page(""),
            Page("This digital page contains enough embedded text that OCR should not run."),
        ]


with patch.object(ck, "PdfReader", Reader), patch.object(
    ck, "_ocr_pdf_page", return_value="Scanned parace-\ntamol result"
) as ocr:
    chunks = ck.extract_pdf_chunks("example.pdf", chunk_words=100, ocr="auto")

assert ocr.call_count == 1
assert chunks[0]["text"] == "Scanned paracetamol result"
assert chunks[0]["extraction_method"] == "ocr"
assert chunks[1]["extraction_method"] == "native"
assert ck._needs_ocr("123") and not ck._needs_ocr("a" * 40)

with patch.object(ck, "PdfReader", lambda _path: type("R", (), {"pages": [Page("")]})()), patch.object(
    ck, "_ocr_pdf_page", side_effect=RuntimeError("missing OCR")
):
    assert ck.extract_pdf_chunks("example.pdf", ocr="auto") == []
    try:
        ck.extract_pdf_chunks("example.pdf", ocr="force")
    except RuntimeError:
        pass
    else:
        raise AssertionError("force mode must report a missing OCR backend")

print("PASS: sparse-page OCR fallback, native-text preference, and provenance")
