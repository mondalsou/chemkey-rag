"""Recognize molecular drawings in papers and write reviewable provenance.

This is optical chemical-structure recognition (OCSR), not text OCR.  The
default DECIMER path renders complete PDF pages so vector drawings are visible,
segments molecular depictions, predicts SMILES, and accepts only structures
that RDKit can parse and convert to an InChIKey.

    python scripts/recognize_structure_images.py --source both

The optional model stack is installed separately with
``requirements-structure-ocr.txt``.  Tests inject small fake recognizers and do
not download models.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from collections import Counter
from pathlib import Path

from PIL import Image
from rdkit import Chem

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import chemkey as ck

sys.path.insert(0, str(Path(__file__).resolve().parent))
import inventory_pdf_images as inv


DEFAULT_PAPERS_DIR = "papers"
DEFAULT_INVENTORY = "data/image_inventory.json"
DEFAULT_OUT = "data/structure_ocr.json"
DEFAULT_CROPS_DIR = "data/extracted_structures"
DEFAULT_DPI = 300

# Run the structure detector across schemes and real table rasters as well as
# images that the cheap inventory heuristic already calls a structure.  This is
# deliberately recall-oriented; RDKit validation is the precision gate.
OCSR_LABELS = frozenset({"structure_drawing", "scheme_or_reaction", "table_image"})


def stable_structure_id(filename, page, source_kind, source_index, crop_index):
    raw = f"{filename}|{page}|{source_kind}|{source_index}|{crop_index}".encode()
    return "ocsr_" + hashlib.sha1(raw).hexdigest()[:14]


def candidate_inventory_records(inventory):
    """Return recall-oriented embedded-image candidates, excluding table rules."""
    rows = []
    for rec in inventory.get("images") or []:
        if rec.get("label") not in OCSR_LABELS:
            continue
        if inv.is_thin_table_rule(rec.get("width"), rec.get("height")):
            continue
        rows.append(rec)
    return rows


def normalize_prediction(prediction):
    """Turn a backend result into (SMILES, confidence-or-None)."""
    if isinstance(prediction, str):
        return prediction.strip(), None
    if isinstance(prediction, dict):
        smiles = prediction.get("smiles") or prediction.get("SMILES") or ""
        confidence = prediction.get("confidence")
        try:
            confidence = float(confidence) if confidence is not None else None
        except (TypeError, ValueError):
            confidence = None
        return str(smiles).strip(), confidence
    if isinstance(prediction, (tuple, list)) and prediction:
        smiles = prediction[0]
        confidence = prediction[1] if len(prediction) > 1 else None
        try:
            confidence = float(confidence) if confidence is not None else None
        except (TypeError, ValueError):
            confidence = None
        return str(smiles).strip(), confidence
    return "", None


def validate_smiles(smiles):
    """Return a canonical, connectivity-keyed structure or a refusal reason."""
    if not smiles:
        return None, "empty_prediction"
    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        return None, "invalid_smiles"
    fragments = Chem.GetMolFrags(mol, asMols=True, sanitizeFrags=True)
    if fragments:
        mol = max(fragments, key=lambda item: item.GetNumHeavyAtoms())
    if mol.GetNumHeavyAtoms() < ck.MIN_HEAVY_ATOMS:
        return None, "too_small"
    canonical = Chem.MolToSmiles(mol, canonical=True, isomericSmiles=True)
    inchikey, block = ck.to_inchikey(canonical)
    if not inchikey or not block:
        return None, "inchikey_failed"
    return {
        "smiles": canonical,
        "inchikey": inchikey,
        "block": block,
        "heavy_atoms": mol.GetNumHeavyAtoms(),
    }, None


def recognize_crop(image, recognize_fn):
    """Recognize one crop; invalid chemistry is retained as a refusal."""
    try:
        predicted_smiles, confidence = normalize_prediction(recognize_fn(image))
    except Exception as exc:
        return {
            "status": "refused",
            "reason": f"recognizer_error:{type(exc).__name__}",
            "predicted_smiles": None,
            "confidence": None,
        }
    structure, reason = validate_smiles(predicted_smiles)
    if structure is None:
        return {
            "status": "refused",
            "reason": reason,
            "predicted_smiles": predicted_smiles or None,
            "confidence": confidence,
        }
    return {
        "status": "accepted",
        "reason": None,
        "predicted_smiles": predicted_smiles,
        "confidence": confidence,
        **structure,
    }


class DecimerRecognizer:
    """Lazy local DECIMER adapter. Importing it may load model weights."""

    name = "DECIMER"

    def __init__(self):
        try:
            from DECIMER import predict_SMILES
        except ImportError as exc:
            raise RuntimeError(
                "DECIMER is unavailable. Install requirements-structure-ocr.txt "
                "in a separate environment."
            ) from exc
        self._predict = predict_SMILES

    def __call__(self, image):
        import numpy as np

        # Confidence output differs across DECIMER releases; keep the stable
        # string API and let RDKit provide the non-negotiable chemistry gate.
        return self._predict(np.asarray(image.convert("RGB")), confidence=False)


class DecimerSegmenter:
    """Lazy local DECIMER-Segmentation adapter."""

    name = "DECIMER-Segmentation"

    def __init__(self):
        try:
            from decimer_segmentation import segment_chemical_structures
        except ImportError as exc:
            raise RuntimeError(
                "DECIMER-Segmentation is unavailable. Install "
                "requirements-structure-ocr.txt in a separate environment."
            ) from exc
        self._segment = segment_chemical_structures

    def __call__(self, image):
        import numpy as np

        segments = self._segment(np.asarray(image.convert("RGB")), expand=True)
        return [Image.fromarray(segment).convert("RGB") for segment in segments]


def render_pdf_pages(pdf_path, dpi=DEFAULT_DPI):
    """Yield (one-based page, PIL image); rendering exposes vector drawings."""
    try:
        import fitz
    except ImportError as exc:
        raise RuntimeError(
            "Full-page structure detection needs PyMuPDF. Install "
            "requirements-structure-ocr.txt."
        ) from exc
    scale = dpi / 72.0
    with fitz.open(pdf_path) as document:
        for page_index, page in enumerate(document, start=1):
            pixmap = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
            yield page_index, Image.frombytes(
                "RGB", (pixmap.width, pixmap.height), pixmap.samples
            )


def _save_crop(image, crops_dir, structure_id):
    path = Path(crops_dir) / f"{structure_id}.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    image.convert("RGB").save(path, format="PNG", optimize=True)
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def _record_for_crop(
    image,
    *,
    filename,
    page,
    source_kind,
    source_index,
    crop_index,
    source_label,
    recognizer_name,
    recognize_fn,
    crops_dir,
):
    structure_id = stable_structure_id(
        filename, page, source_kind, source_index, crop_index
    )
    image_path = _save_crop(image, crops_dir, structure_id)
    result = recognize_crop(image, recognize_fn)
    return {
        "id": structure_id,
        "source_doc": filename,
        "page_number": page,
        "source_kind": source_kind,
        "source_index": source_index,
        "crop_index": crop_index,
        "source_label": source_label,
        "image_path": image_path,
        "width": image.width,
        "height": image.height,
        "recognizer": recognizer_name,
        **result,
    }


def recognize_embedded_images(
    inventory,
    papers_dir,
    recognize_fn,
    crops_dir,
    recognizer_name="injected",
    segment_fn=None,
):
    """Recognize embedded structure, scheme and non-trivial table rasters."""
    output = []
    for rec in candidate_inventory_records(inventory):
        pdf_path = os.path.join(papers_dir, rec.get("filename") or "")
        image = inv.extract_embedded_raster(
            pdf_path, rec.get("page"), rec.get("image_index")
        )
        if image is None:
            output.append({
                "id": stable_structure_id(
                    rec.get("filename"), rec.get("page"), "embedded",
                    rec.get("image_index"), 0,
                ),
                "source_doc": rec.get("filename"),
                "page_number": rec.get("page"),
                "source_kind": "embedded",
                "source_index": rec.get("image_index"),
                "crop_index": 0,
                "source_label": rec.get("label"),
                "image_path": None,
                "recognizer": recognizer_name,
                "status": "refused",
                "reason": "decode_failed",
                "predicted_smiles": None,
                "confidence": None,
            })
            continue
        try:
            # A structure-labelled XObject is already a crop.  Schemes/tables
            # first go through segmentation when available.
            if rec.get("label") == "structure_drawing" or segment_fn is None:
                segments = [image.copy()]
            else:
                segments = segment_fn(image)
            for crop_index, crop in enumerate(segments):
                output.append(_record_for_crop(
                    crop,
                    filename=rec.get("filename"),
                    page=rec.get("page"),
                    source_kind="embedded",
                    source_index=rec.get("image_index"),
                    crop_index=crop_index,
                    source_label=rec.get("label"),
                    recognizer_name=recognizer_name,
                    recognize_fn=recognize_fn,
                    crops_dir=crops_dir,
                ))
                crop.close()
        except Exception as exc:
            output.append({
                "id": stable_structure_id(
                    rec.get("filename"), rec.get("page"), "embedded",
                    rec.get("image_index"), 0,
                ),
                "source_doc": rec.get("filename"),
                "page_number": rec.get("page"),
                "source_kind": "embedded",
                "source_index": rec.get("image_index"),
                "crop_index": 0,
                "source_label": rec.get("label"),
                "image_path": None,
                "recognizer": recognizer_name,
                "status": "refused",
                "reason": f"segmentation_error:{type(exc).__name__}",
                "predicted_smiles": None,
                "confidence": None,
            })
        finally:
            image.close()
    return output


def recognize_rendered_pages(
    pdf_paths,
    segment_fn,
    recognize_fn,
    crops_dir,
    recognizer_name="injected",
    dpi=DEFAULT_DPI,
    page_renderer=render_pdf_pages,
):
    """Render every page, segment depictions, then recognize each crop."""
    output = []
    for pdf_path in pdf_paths:
        filename = os.path.basename(pdf_path)
        for page_number, page_image in page_renderer(pdf_path, dpi=dpi):
            try:
                segments = segment_fn(page_image)
                for crop_index, crop in enumerate(segments):
                    output.append(_record_for_crop(
                        crop,
                        filename=filename,
                        page=page_number,
                        source_kind="page_segment",
                        source_index=page_number,
                        crop_index=crop_index,
                        source_label="page_render",
                        recognizer_name=recognizer_name,
                        recognize_fn=recognize_fn,
                        crops_dir=crops_dir,
                    ))
                    crop.close()
            except Exception as exc:
                output.append({
                    "id": stable_structure_id(
                        filename, page_number, "page_segment", page_number, 0
                    ),
                    "source_doc": filename,
                    "page_number": page_number,
                    "source_kind": "page_segment",
                    "source_index": page_number,
                    "crop_index": 0,
                    "source_label": "page_render",
                    "image_path": None,
                    "recognizer": recognizer_name,
                    "status": "refused",
                    "reason": f"segmentation_error:{type(exc).__name__}",
                    "predicted_smiles": None,
                    "confidence": None,
                })
            finally:
                page_image.close()
    return output


def deduplicate_structures(records):
    """Keep one accepted crop per molecule per page; retain every refusal."""
    seen = set()
    output = []
    for rec in records:
        if rec.get("status") != "accepted":
            output.append(rec)
            continue
        key = (rec.get("source_doc"), rec.get("page_number"), rec.get("block"))
        if key in seen:
            continue
        seen.add(key)
        output.append(rec)
    return output


def summarize(records, source, recognizer_name, segmenter_name):
    counts = Counter(rec.get("status") for rec in records)
    refusals = Counter(
        rec.get("reason") for rec in records if rec.get("status") == "refused"
    )
    return {
        "generated_by": "scripts/recognize_structure_images.py",
        "pipeline": "page/image segmentation -> OCSR -> RDKit -> InChIKey",
        "source_mode": source,
        "recognizer": recognizer_name,
        "segmenter": segmenter_name,
        "counts": {
            "crops": len(records),
            "accepted": int(counts.get("accepted", 0)),
            "refused": int(counts.get("refused", 0)),
            "refused_by_reason": dict(refusals),
        },
        "structures": records,
        "notes": (
            "Machine-read structures are accepted only after RDKit parsing and "
            "InChIKey generation. The original crop is retained for visual review."
        ),
    }


def run_structure_ocr(
    *,
    papers_dir=DEFAULT_PAPERS_DIR,
    inventory_path=DEFAULT_INVENTORY,
    out_path=DEFAULT_OUT,
    crops_dir=DEFAULT_CROPS_DIR,
    source="both",
    dpi=DEFAULT_DPI,
    recognize_fn=None,
    segment_fn=None,
    recognizer_name=None,
    segmenter_name=None,
    page_renderer=render_pdf_pages,
):
    pdf_paths = inv.find_pdfs(papers_dir)
    if not pdf_paths:
        raise RuntimeError(f"No PDFs found in {papers_dir}/")
    if recognize_fn is None:
        recognizer = DecimerRecognizer()
        recognize_fn = recognizer
        recognizer_name = recognizer.name
    if segment_fn is None and source in {"pages", "both"}:
        segmenter = DecimerSegmenter()
        segment_fn = segmenter
        segmenter_name = segmenter.name
    recognizer_name = recognizer_name or getattr(recognize_fn, "name", "custom")
    segmenter_name = segmenter_name or (
        getattr(segment_fn, "name", "custom") if segment_fn else "none"
    )

    records = []
    if source in {"embedded", "both"}:
        if inventory_path and os.path.exists(inventory_path):
            with open(inventory_path, encoding="utf-8") as handle:
                inventory = json.load(handle)
        else:
            inventory = inv.build_inventory(pdf_paths, papers_dir=papers_dir)
        records.extend(recognize_embedded_images(
            inventory,
            papers_dir,
            recognize_fn,
            crops_dir,
            recognizer_name=recognizer_name,
            segment_fn=segment_fn,
        ))
    if source in {"pages", "both"}:
        records.extend(recognize_rendered_pages(
            pdf_paths,
            segment_fn,
            recognize_fn,
            crops_dir,
            recognizer_name=recognizer_name,
            dpi=dpi,
            page_renderer=page_renderer,
        ))
    records = deduplicate_structures(records)
    report = summarize(records, source, recognizer_name, segmenter_name)
    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


def print_summary(report, file=sys.stdout):
    counts = report["counts"]
    print("Chemical structure image recognition", file=file)
    print(f"  recognizer: {report['recognizer']}", file=file)
    print(f"  segmenter: {report['segmenter']}", file=file)
    print(f"  candidate crops: {counts['crops']}", file=file)
    print(f"  RDKit-validated structures: {counts['accepted']}", file=file)
    print(f"  refused: {counts['refused']}", file=file)
    for reason, count in sorted(counts["refused_by_reason"].items()):
        print(f"    {reason}: {count}", file=file)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Detect and recognize molecular drawings in chemistry PDFs."
    )
    parser.add_argument("--papers", default=DEFAULT_PAPERS_DIR)
    parser.add_argument("--inventory", default=DEFAULT_INVENTORY)
    parser.add_argument("--out", default=DEFAULT_OUT)
    parser.add_argument("--crops", default=DEFAULT_CROPS_DIR)
    parser.add_argument(
        "--source", choices=("embedded", "pages", "both"), default="both",
        help="Both includes full-page renders, which catch vector drawings.",
    )
    parser.add_argument("--dpi", type=int, default=DEFAULT_DPI)
    args = parser.parse_args(argv)
    try:
        report = run_structure_ocr(
            papers_dir=args.papers,
            inventory_path=args.inventory,
            out_path=args.out,
            crops_dir=args.crops,
            source=args.source,
            dpi=args.dpi,
        )
    except RuntimeError as exc:
        parser.error(str(exc))
    print_summary(report)
    print(f"Wrote {args.out}; crops are under {args.crops}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
