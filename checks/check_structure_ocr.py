"""Run: python checks/check_structure_ocr.py

No model download, network call, PDF requirement, or index mutation.  Small
injected functions exercise candidate routing, chemistry validation, crop
provenance, deduplication, and searchable index integration.
"""

import sys
import tempfile
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import chemkey as ck
import recognize_structure_images as ocsr


inventory = {
    "images": [
        {"label": "structure_drawing", "width": 400, "height": 300, "image_index": 0},
        {"label": "scheme_or_reaction", "width": 900, "height": 300, "image_index": 1},
        {"label": "table_image", "width": 800, "height": 500, "image_index": 2},
        {"label": "table_image", "width": 1601, "height": 5, "image_index": 3},
        {"label": "other", "width": 300, "height": 200, "image_index": 4},
    ]
}
candidates = ocsr.candidate_inventory_records(inventory)
assert [row["image_index"] for row in candidates] == [0, 1, 2]

paracetamol = "CC(=O)Nc1ccc(O)cc1"
accepted = ocsr.recognize_crop(
    Image.new("RGB", (220, 160), "white"), lambda _image: paracetamol
)
assert accepted["status"] == "accepted"
assert accepted["block"] == "RZVAJINKPMORJF"
assert accepted["inchikey"].startswith(accepted["block"])

invalid = ocsr.recognize_crop(
    Image.new("RGB", (220, 160), "white"), lambda _image: "not-smiles"
)
assert invalid["status"] == "refused" and invalid["reason"] == "invalid_smiles"

failed = ocsr.recognize_crop(
    Image.new("RGB", (220, 160), "white"),
    lambda _image: (_ for _ in ()).throw(RuntimeError("model failed")),
)
assert failed["status"] == "refused"
assert failed["reason"] == "recognizer_error:RuntimeError"

with tempfile.TemporaryDirectory() as tmp:
    crop = Image.new("RGB", (220, 160), "white")
    record = ocsr._record_for_crop(
        crop,
        filename="paper.pdf",
        page=4,
        source_kind="page_segment",
        source_index=4,
        crop_index=0,
        source_label="page_render",
        recognizer_name="fixture",
        recognize_fn=lambda _image: {"smiles": paracetamol, "confidence": 0.91},
        crops_dir=tmp,
    )
    assert Path(record["image_path"]).exists()
    assert record["confidence"] == 0.91

report = ocsr.summarize([record, {"status": "refused", "reason": "invalid_smiles"}],
                        "both", "fixture", "fixture-segmenter")
index = {
    "chunks": [{
        "source_doc": "paper.pdf",
        "page_number": 4,
        "text": "The page discusses a measured crystal structure.",
        "compounds": [],
        "blocks": [],
    }],
    "compounds": {},
}
ck.attach_image_structures(index, report)
assert len(index["image_structures"]) == 1
assert len(ck.image_structures_for_block(index, record["block"])) == 1
hits, block = ck.structure_search(paracetamol, index)
assert block == record["block"] and hits
assert hits[0]["extraction_method"] == "structure_ocr"
assert hits[0]["image_structure_ids"] == [record["id"]]

# Reattaching replaces old OCSR anchors rather than duplicating them.
ck.attach_image_structures(index, report)
anchors = [c for c in index["chunks"] if c.get("extraction_method") == "structure_ocr"]
assert len(anchors) == 1

print("PASS: OCSR candidates, refusal, RDKit validation, provenance and retrieval")
