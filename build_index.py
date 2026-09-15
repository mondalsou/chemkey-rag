"""Build the ChemKeyRAG index from the PDFs in papers/.

    python build_index.py                 # papers/*.pdf -> data/index.json
    python build_index.py --offline       # lexicon only, no PubChem calls
    python build_index.py --image-tables  # accepted image-only tables -> searchable passages
    python build_index.py --structure-images  # DECIMER -> RDKit -> searchable image structures

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
        help="opt-in: OCR image-only tables, retain crops, and add accepted "
             "grids to the index as reviewable passages. Off by default. "
             "Does not replace pypdf native-text tables.",
    )
    parser.add_argument(
        "--structure-images",
        action="store_true",
        default=False,
        help="opt-in OCSR: segment molecular drawings with DECIMER, validate "
             "with RDKit, retain crops, and add valid structures to the index.",
    )
    parser.add_argument(
        "--structure-source",
        choices=("embedded", "pages", "both"),
        default="both",
        help="OCSR input. 'both' also renders pages to catch vector drawings.",
    )
    parser.add_argument(
        "--structure-ocr-out",
        default="data/structure_ocr.json",
        help="gitignored OCSR provenance sidecar",
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
            print("\n--image-tables: OCR of image-only tables; retaining source crops")
            summary = table_ocr.run_table_ocr(papers_dir=args.papers)
            if summary:
                ck.attach_image_tables(index, summary, resolver=resolver)
                table_ocr.print_summary(summary)
                print("Wrote data/table_ocr.json")

    if args.structure_images:
        scripts_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "scripts")
        if scripts_dir not in sys.path:
            sys.path.insert(0, scripts_dir)
        import recognize_structure_images as structure_ocr

        print(
            "\n--structure-images: DECIMER segmentation and recognition; "
            "RDKit validation; retaining source crops"
        )
        try:
            report = structure_ocr.run_structure_ocr(
                papers_dir=args.papers,
                out_path=args.structure_ocr_out,
                source=args.structure_source,
            )
        except RuntimeError as exc:
            raise SystemExit(f"--structure-images failed: {exc}") from exc
        ck.attach_image_structures(index, report)
        structure_ocr.print_summary(report)

    ck.save_index(index, args.out)

    annotated = sum(1 for chunk in index["chunks"] if chunk["blocks"])
    print(f"\nWrote {args.out}")
    print(f"  {len(index['chunks'])} chunks, {annotated} carry at least one structure")
    print(f"  {len(index['compounds'])} names resolved")
    print(f"  {len(index.get('image_structures', []))} image structures accepted")
    print(f"  {len(index.get('image_tables', []))} image tables accepted")

    skeletons = {}
    for name, record in index["compounds"].items():
        skeletons.setdefault(record["block"], []).append(name)
    shared = {block: names for block, names in skeletons.items() if len(names) > 1}
    if shared:
        print("\nSynonym groups found in this corpus:")
        for block, names in shared.items():
            print(f"  {block}  {', '.join(sorted(names))}")


if __name__ == "__main__":
    main()
