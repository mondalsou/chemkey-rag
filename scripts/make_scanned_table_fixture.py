"""Rasterise a table out of a digital PDF into an image-only one-page PDF.

    python scripts/make_scanned_table_fixture.py --source <paper.pdf> --out papers/D_....pdf

The result is scan-LIKE, not an original scanner capture: it has the right
property for this pipeline (no extractable text layer, one embedded raster) and
none of the noise, skew, or bleed-through of a real scan. Say so wherever the
numbers it produces are reported.

Needs PyMuPDF; papers/*.pdf is gitignored, so this script is how the fixture is
reproduced rather than shipped.
"""

from __future__ import annotations

import argparse
import sys

DEFAULT_SOURCE = "data/ocr_demo/raman_acetaminophen.pdf"
DEFAULT_OUT = "papers/D_raman_table1_scan.pdf"
DEFAULT_PAGE = 7          # 1-based; Table 1 of Shende et al., Pharmaceutics 2014
DEFAULT_CLIP = "60,335,540,590"
DEFAULT_DPI = 200


def make_fixture(source, out, page, clip, dpi):
    import pymupdf

    document = pymupdf.open(source)
    pixmap = document[page - 1].get_pixmap(dpi=dpi, clip=pymupdf.Rect(*clip))
    scan = pymupdf.open()
    sheet = scan.new_page(width=pixmap.width * 72 / dpi, height=pixmap.height * 72 / dpi)
    sheet.insert_image(sheet.rect, pixmap=pixmap)
    scan.save(out)
    return {"out": out, "width": pixmap.width, "height": pixmap.height, "dpi": dpi}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--source", default=DEFAULT_SOURCE)
    parser.add_argument("--out", default=DEFAULT_OUT)
    parser.add_argument("--page", type=int, default=DEFAULT_PAGE, help="1-based")
    parser.add_argument("--clip", default=DEFAULT_CLIP, help="x0,y0,x1,y1 in points")
    parser.add_argument("--dpi", type=int, default=DEFAULT_DPI)
    args = parser.parse_args(argv)

    clip = [float(value) for value in args.clip.split(",")]
    if len(clip) != 4:
        parser.error("--clip needs four comma-separated numbers")
    try:
        result = make_fixture(args.source, args.out, args.page, clip, args.dpi)
    except ImportError:
        print("SKIP: PyMuPDF is not installed; no fixture written.", file=sys.stderr)
        return 0
    print(
        f"Wrote {result['out']}: one image-only page, "
        f"{result['width']}x{result['height']} px at {result['dpi']} dpi"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
