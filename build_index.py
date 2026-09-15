"""Build the ChemKeyRAG index from the PDFs in papers/.

    python build_index.py                 # papers/*.pdf -> data/index.json
    python build_index.py --offline       # lexicon only, no PubChem calls
    python build_index.py --image-tables  # same index, plus gitignored table OCR sidecar

Default ingest is native pypdf page text. --image-tables is OFF by default and
does not replace extract_text(). Page-level sparse OCR (an auto fallback, if
added elsewhere) is a different feature — do not alias it to image-table OCR.
"""

import argparse
import glob
import os
import sys

import chemkey as ck


def main():
    parser = argparse.ArgumentParser(description="Build the ChemKeyRAG index.")
    parser.add_argument("--papers", default="papers", help="folder of PDFs")
    parser.add_argument("--out", default="data/index.json", help="index path")
    parser.add_argument("--offline", action="store_true",
                        help="use the shipped lexicon only, never call PubChem")
    parser.add_argument("--max-candidates", type=int, default=500)
    parser.add_argument(
        "--image-tables",
        action="store_true",
        default=False,
        help="opt-in sidecar: OCR image-only tables to data/table_ocr.json. "
             "Off by default. Does not replace pypdf native-text tables.",
    )
    args = parser.parse_args()

    pdf_paths = sorted(glob.glob(os.path.join(args.papers, "*.pdf")))
    if not pdf_paths:
        raise SystemExit(
            f"No PDFs in {args.papers}/. See papers/PAPERS.md for what to download."
        )

    print(f"Reading {len(pdf_paths)} PDFs")
    resolver = ck.NameResolver(online=not args.offline)
    index = ck.build_index(pdf_paths, resolver, max_candidates=args.max_candidates)
    ck.save_index(index, args.out)

    annotated = sum(1 for chunk in index["chunks"] if chunk["blocks"])
    print(f"\nWrote {args.out}")
    print(f"  {len(index['chunks'])} chunks, {annotated} carry at least one structure")
    print(f"  {len(index['compounds'])} names resolved")

    skeletons = {}
    for name, record in index["compounds"].items():
        skeletons.setdefault(record["block"], []).append(name)
    shared = {block: names for block, names in skeletons.items() if len(names) > 1}
    if shared:
        print("\nSynonym groups found in this corpus:")
        for block, names in shared.items():
            print(f"  {block}  {', '.join(sorted(names))}")

    if args.image_tables:
        scripts_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "scripts")
        sys.path.insert(0, scripts_dir)
        import ocr_table_images as table_ocr
        if not table_ocr.tesseract_available():
            print(
                "\n--image-tables: Tesseract/pytesseract not available; "
                "native-text index already written. See requirements-table-ocr.txt."
            )
        else:
            print("\n--image-tables: running image-table OCR sidecar (index already written)")
            summary = table_ocr.run_table_ocr(papers_dir=args.papers)
            if summary:
                table_ocr.print_summary(summary)
                print("Wrote data/table_ocr.json")


if __name__ == "__main__":
    main()
