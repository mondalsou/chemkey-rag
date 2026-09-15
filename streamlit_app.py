"""ChemKeyRAG - structure-aware retrieval over a small paper corpus.

Build the index first:

    python build_index.py

then:

    streamlit run streamlit_app.py
"""

import os
import base64
import html
import json
from pathlib import Path
import re
import time

import requests
import streamlit as st
from dotenv import load_dotenv
from rdkit import Chem
from rdkit.Chem.Draw import rdMolDraw2D

import chemkey as ck
import evidence_plots as plots

load_dotenv()  # so OPENROUTER_API_KEY in .env reaches the sidebar default

# The full local index (all papers, gitignored) is preferred when present. The
# committed index covers only the CC BY papers, so a clone deploys without
# republishing text the licence does not cover.
PRIVATE_INDEX = "data/index.json"
PUBLIC_INDEX = "data/index.public.json"
INDEX_PATH = PRIVATE_INDEX if os.path.exists(PRIVATE_INDEX) else PUBLIC_INDEX

# The PDFs are not redistributed - the app links to the open-access record
# instead of serving a copy. Keys are the source_doc names stored in the index.
PAPER_SOURCES = {
    "A_paracetamol_cocrystals.pdf": "PMC11434482",
    "B_acetaminophen_solubility.pdf": "PMC9781932",
    "C_excess_solubility_simulation.pdf": "PMC4312346",
}

# label -> (SMILES, common-name query)
EXAMPLES = {
    "Paracetamol": ("CC(=O)Nc1ccc(O)cc1", "paracetamol"),
    "Phenacetin": ("CCOc1ccc(NC(C)=O)cc1", "phenacetin"),
    "Acetanilide": ("CC(=O)Nc1ccccc1", "acetanilide"),
    "Caffeine": ("Cn1cnc2c1c(=O)n(C)c(=O)n2C", "caffeine"),
    "Benzocaine": ("CCOC(=O)c1ccc(N)cc1", "benzocaine"),
    "Carbamazepine (not in corpus)": ("NC(=O)N1c2ccccc2C=Cc2ccccc21", "carbamazepine"),
}

# (label, question, expected_to_be_answerable) - two the corpus supports, one it
# does not, so the refusal behaviour is visible rather than asserted.
PRESET_QUESTIONS = [
    ("Cocrystals",
     "Which coformers were used to make cocrystals, and what holds the layers together?",
     True),
    ("Solubility table",
     "What are the measured solubility values in DMSO + water mixtures at "
     "different temperatures?",
     True),
    ("Clinical dosing",
     "What are the reported clinical side effects and dosing in humans?",
     False),
]

CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&family=Newsreader:ital,wght@0,500;1,500&family=IBM+Plex+Mono:wght@400;500&display=swap');
/* One ground plane, raised surfaces only where the page takes input or makes a
   claim, and a single accent that is allowed in three places. Every colour,
   radius and family resolves from this block. */
:root {
  --paper:#f2ebdc; --sunken:#e9dfc9; --surface:#fffdf6;
  --line:#e2d6bd; --line-soft:#eae0cc;
  --ink:#16323d; --body:#3c5a66; --muted:#55696f;
  --accent:#b0286c; --accent-deep:#8e1f57; --accent-tint:#f6e2ec;
  --shadow-1:0 1px 2px rgba(22,50,61,.04), 0 8px 24px -12px rgba(22,50,61,.10);
  --r-sm:6px; --r-md:10px; --r-lg:14px;
  --sans:'Inter',system-ui,sans-serif;
  --serif:'Newsreader',Georgia,serif;
  --mono:'IBM Plex Mono',ui-monospace,monospace;
}
.stApp {background:var(--paper);color:var(--body);font-family:var(--sans);font-feature-settings:'tnum';}
.block-container {padding-top:32px;padding-bottom:72px;max-width:1440px;}
h1,h2,h3 {color:var(--ink);font-family:var(--sans)!important;font-weight:600!important;}
h2 {font-size:26px!important;line-height:1.2;letter-spacing:-.6px;}
h3 {font-size:15px!important;line-height:1.3;letter-spacing:0;}
[data-testid="stHeader"] {background:color-mix(in srgb,var(--paper) 85%,transparent);backdrop-filter:blur(6px);}

/* --- surfaces: flat by default, raised only by opt-in key --- */
[data-testid="stVerticalBlockBorderWrapper"]>div {border-radius:var(--r-lg)!important;background:transparent;border:1px solid var(--line-soft);padding:20px 22px;}
.st-key-ck-raised [data-testid="stVerticalBlockBorderWrapper"]>div,
[data-testid="stMetric"] {background:var(--surface);border-color:var(--line);box-shadow:var(--shadow-1);}
[data-testid="stVerticalBlock"] {gap:16px;}
[data-testid="stElementContainer"]:empty {display:none;}
[data-testid="stMetric"] {border:1px solid var(--line);border-radius:var(--r-lg);padding:20px 22px;min-height:112px;}
[data-testid="stMetricValue"] {font:500 44px/1 var(--serif);color:var(--ink);letter-spacing:-1px;}
[data-testid="stMetricLabel"] {color:var(--muted);font-size:11.5px;}
[data-testid="stExpander"] {background:transparent;border:0;border-top:1px solid var(--line-soft);border-radius:0;}
[data-testid="stChatMessage"] {background:var(--surface);border:1px solid var(--line);border-radius:var(--r-lg);}

/* --- sidebar is chrome, not a surface --- */
[data-testid="stSidebar"] {background:var(--paper);border-right:1px solid var(--line-soft);}
[data-testid="stSidebar"] .block-container {padding-top:32px;}
[data-testid="stSidebar"] [data-testid="stExpander"],
[data-testid="stSidebar"] [data-testid="stExpander"] details {background:transparent!important;border:0!important;border-radius:0!important;box-shadow:none!important;}
[data-testid="stSidebar"] [data-testid="stExpander"] {border-top:1px solid var(--line-soft)!important;}

/* --- tabs --- */
[data-baseweb="tab-list"] {gap:28px;margin:48px 0 24px;border-bottom:1px solid var(--line);}
[data-baseweb="tab"] {padding:12px 0;font-size:13px;color:var(--muted);}
[data-baseweb="tab"][aria-selected="true"] {color:var(--ink);font-weight:500;}
[data-baseweb="tab-highlight"] {background:var(--ink);height:2px;}

/* --- controls: accent lives on the button, nowhere else --- */
.stButton>button,.stDownloadButton>button {background:var(--accent);color:#fffdf6;border:1px solid var(--accent);border-radius:999px;padding:9px 24px;font-weight:500;}
.stButton>button:hover,.stDownloadButton>button:hover {background:var(--accent-deep);border-color:var(--accent-deep);color:#fff;}
[data-testid="stTextInput"] input {font-size:14px;color:var(--ink);}
[data-testid="stTextInput"] [data-baseweb="input"], [data-baseweb="select"]>div {background:var(--paper);border-color:var(--line);border-radius:var(--r-md);}
[data-testid="stCaptionContainer"] {color:var(--muted);font-size:11.5px;}

/* --- page furniture --- */
.ck-brand {font:600 30px var(--sans);letter-spacing:-1px;margin-bottom:8px;color:var(--ink);}
.ck-brand span {color:var(--ink);font-size:16px;vertical-align:12px;margin-left:3px;}
.ck-eyebrow {font:600 10px var(--sans);letter-spacing:1.6px;color:var(--muted);text-transform:uppercase;margin:8px 0 24px;}
.ck-sub {color:var(--body);font-size:14px;line-height:1.7;max-width:74ch;margin:16px 0;}
.ck-key {display:inline-block;background:var(--sunken);padding:4px 9px;border-radius:var(--r-sm);font:400 12.5px/1 var(--mono);letter-spacing:.3px;color:var(--ink);margin:10px 0;}
.ck-topbar {display:flex;justify-content:space-between;align-items:center;border-bottom:1px solid var(--line-soft);padding-bottom:16px;margin-bottom:48px;color:var(--muted);font-size:11px;letter-spacing:1px;}
.ck-topbar strong {color:var(--ink);letter-spacing:1.6px;font-weight:600;}
.ck-hero-title {font:600 clamp(38px,4vw,56px)/1.08 var(--sans);color:var(--ink);letter-spacing:-1.8px;margin:0 0 48px;}
.ck-hero-title em {font-family:var(--serif);font-style:italic;font-weight:500;letter-spacing:-.5px;color:var(--ink);}
.ck-hero-note {font-size:11.5px;color:var(--muted);border-left:2px solid var(--line);padding-left:12px;margin:24px 0 10px;}

/* --- molecule panel: raised, warm --- */
.ck-mol {position:relative;isolation:isolate;overflow:hidden;background:var(--surface);border:1px solid var(--line);border-radius:var(--r-lg);box-shadow:var(--shadow-1);text-align:center;padding:24px 16px;}
.ck-mol:before {content:'';position:absolute;inset:14px;border:1px solid var(--line-soft);border-radius:50%;z-index:-1;transform:rotate(-25deg) scale(.84);}
.ck-mol svg {width:100%;height:auto;max-width:370px;}

/* --- evidence --- */
.ck-pill {display:inline-block;padding:4px 10px;margin:3px 5px 3px 0;border:1px solid transparent;border-radius:var(--r-sm);font-size:11.5px;background:var(--sunken);color:var(--body);}
.ck-snip {font-size:14px;line-height:1.7;color:var(--body);margin:12px 0;max-width:74ch;}
.ck-snip mark {background:var(--accent-tint);color:var(--ink);border-radius:3px;padding:0 2px;}
.ck-meta {font-size:11.5px;color:var(--muted);letter-spacing:.2px;}
.ck-status {font-size:11px;color:var(--body);border:1px solid var(--line);background:var(--sunken);padding:8px 13px;border-radius:999px;display:inline-block;}
.ck-paper {padding:16px 0;border-bottom:1px solid var(--line-soft);font-size:12px;line-height:1.7;color:var(--body);}

/* --- recovered tables --- */
.ck-grid {overflow-x:auto;margin:12px 0;}
.ck-grid table {border-collapse:collapse;font:400 12.5px var(--mono);color:var(--body);width:100%;}
.ck-grid th, .ck-grid td {border:1px solid var(--line);padding:6px 10px;text-align:left;vertical-align:top;}
.ck-grid th {color:var(--ink);font-weight:500;background:var(--sunken);}
.ck-grid em {color:var(--muted);font-family:var(--sans);font-style:italic;}

/* --- name comparison: the one place the accent means something --- */
.ck-name-row {display:grid;grid-template-columns:1fr auto;gap:5px 15px;margin:12px 0;font-size:13px;color:var(--body);}
.ck-name-row.selected {color:var(--ink);}
.ck-name-row small {font-size:9px;letter-spacing:1px;color:var(--muted);margin-left:6px;}
.ck-name-row b {font-weight:500;color:var(--ink);}
.ck-name-track {grid-column:1/-1;height:3px;background:var(--sunken);border-radius:var(--r-sm);overflow:hidden;}
.ck-name-track i {display:block;height:100%;background:#a8b6b9;border-radius:var(--r-sm);}
.ck-name-row.selected .ck-name-track i {background:var(--accent);}
.ck-recovery {color:var(--muted);font-size:12px;margin:15px 0;}
.ck-recovery strong {font:500 30px/1 var(--serif);color:var(--accent);margin-right:5px;}
@media(max-width:700px) {.block-container{padding:24px 16px;}.ck-hero-title{font-size:34px;}.ck-topbar{gap:15px;font-size:9px;}[data-baseweb="tab-list"]{gap:16px;margin-top:32px;}.ck-mol{padding:16px;}}
</style>
"""


# --------------------------------------------------------------------------
# Data helpers
# --------------------------------------------------------------------------

@st.cache_data(show_spinner=False)
def load_index(path, mtime):
    return ck.load_index(path)


@st.cache_resource(show_spinner=False)
def get_resolver(online):
    return ck.NameResolver(online=online)


@st.cache_data(show_spinner=False)
def resolve_name(name, online):
    """Name -> SMILES. Lexicon and disk cache first, PubChem only if allowed."""
    try:
        return get_resolver(online).resolve(name)
    except requests.RequestException:
        return None


def depict(smiles, size=(300, 190)):
    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        return None
    drawer = rdMolDraw2D.MolDraw2DSVG(*size)
    drawer.drawOptions().clearBackground = False
    drawer.drawOptions().useBWAtomPalette()
    # Slate-teal ink with the page's magenta on O, so a molecule reads as part
    # of the page rather than a pasted-in image.
    # Monochrome, the way a structure is set in a journal. The accent is
    # reserved for the three places it carries meaning; a molecule is not one.
    drawer.drawOptions().setAtomPalette({-1: (0.086, 0.196, 0.239)})
    drawer.drawOptions().setSymbolColour((0.086, 0.196, 0.239))
    drawer.drawOptions().bondLineWidth = 2
    rdMolDraw2D.PrepareAndDrawMolecule(drawer, mol)
    drawer.FinishDrawing()
    # RDKit emits an XML prolog; it renders as stray text when inlined into HTML.
    return re.sub(r"^<\?xml[^>]*\?>\s*", "", drawer.GetDrawingText())


def spellings_for_block(index, block):
    """Every name in the corpus that resolved onto this skeleton."""
    return sorted(n for n, r in index["compounds"].items() if r["block"] == block)


def image_structures_for_block(index, block):
    return ck.image_structures_for_block(index, block)


def chunks_with_block(chunks, block):
    return {i for i, c in enumerate(chunks) if block in c["blocks"]}


def chunks_with_text(chunks, word):
    pattern = re.compile(r"\b" + re.escape(word) + r"\b", re.I)
    return {i for i, c in enumerate(chunks) if pattern.search(c["text"])}


def loose_find(text, name):
    """Locate a name a PDF split apart: 'caffeine' matches 'ca ffeine'."""
    return re.search(r"[\s\-]?".join(map(re.escape, name)), text, re.I)


@st.cache_data(show_spinner=False)
def reach_report(path, mtime, block, extra_query):
    """Structure recall vs the best possible name-based recall."""
    index = load_index(path, mtime)
    chunks = index["chunks"]
    names = spellings_for_block(index, block)
    queries = sorted(set(names + ([extra_query] if extra_query else [])))

    structure = chunks_with_block(chunks, block)
    per_name = {q: chunks_with_text(chunks, q) for q in queries}
    union = set().union(*per_name.values()) if per_name else set()
    best = max(per_name.items(), key=lambda kv: len(kv[1]), default=("-", set()))

    unreachable = []
    for i in sorted(structure - union):
        chunk = chunks[i]
        hit = next((loose_find(chunk["text"], n) for n in names
                    if loose_find(chunk["text"], n)), None)
        start = max(0, hit.start() - 90) if hit else 0
        end = min(len(chunk["text"]), hit.end() + 90) if hit else 240
        unreachable.append({
            "source_doc": chunk["source_doc"],
            "page_number": chunk["page_number"],
            "fragment": hit.group(0) if hit else None,
            "snippet": chunk["text"][start:end],
        })

    return {
        "names": names,
        "counts": {n: len(s) for n, s in per_name.items()},
        "structure": len(structure),
        "best_name": best[0],
        "best_count": len(best[1]),
        "union": len(union),
        "adds": len(structure - union),      # only the structure route finds these
        "misses": len(union - structure),    # a name finds these, the structure does not
        "unreachable": unreachable,
    }


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------

def render_sources(sources, empty_message):
    if not sources:
        st.info(empty_message)
        return
    for number, source in enumerate(sources, start=1):
        with st.container(border=True):
            st.markdown(
                f"<div class='ck-meta'>SOURCE {number:02d} &nbsp; / &nbsp; "
                f"{html.escape(source['source_doc'])} &nbsp; · &nbsp; PAGE {source['page_number']}"
                f" &nbsp; · &nbsp; {html.escape(source.get('route', 'indexed').upper())}</div>",
                unsafe_allow_html=True,
            )
            st.markdown("".join(f"<span class='ck-pill'>{html.escape(c)}</span>"
                                for c in source["compounds"][:4]), unsafe_allow_html=True)
            body = source["text"][:240] + ("…" if len(source["text"]) > 240 else "")
            st.markdown(f"<div class='ck-snip'>{html.escape(body)}</div>", unsafe_allow_html=True)
            with st.expander("Read full passage"):
                if source.get("context_scope"):
                    st.caption("Includes surrounding page text to preserve tables and definitions.")
                st.text(source["text"])
                if source.get("score") is not None:
                    st.caption(f"BM25 relevance score: {source['score']} · scores are not confidence probabilities")


@st.cache_data(show_spinner=False)
def cached_plot(table, axis):
    return plots.render_svg(table, axis)


def render_plots(message, key):
    tables = message.get("plots", [])
    if not tables:
        return
    try:
        with st.expander("Plot source data", expanded=message.get("plot_requested", False)):
            selected = st.selectbox("Solvent system", range(len(tables)),
                                    format_func=lambda i: tables[i]["title"], key=f"plot_system_{key}")
            table = tables[selected]
            axis = st.radio("Horizontal axis", ["Composition", "Temperature"], horizontal=True,
                            key=f"plot_axis_{key}")
            with st.spinner("Drawing source data…"):
                svg = cached_plot(table, axis)
            encoded = base64.b64encode(svg.encode()).decode()
            st.markdown(f'<img alt="Solubility measurements with uncertainty bars" style="width:100%" src="data:image/svg+xml;base64,{encoded}">', unsafe_allow_html=True)
            st.download_button("Download plot (SVG)", svg, file_name=f"{table['solvent']}-solubility.svg",
                               mime="image/svg+xml", key=f"plot_svg_{key}")
            st.caption(f"Measured · Table 1, page 10 · [Source {table['source']}] · bars show reported ± uncertainty. Lines connect measurements; they are not predictions. Values are mole fractions × 100.")
            st.download_button("Download plotted data", plots.table_csv(table),
                               file_name=f"{table['solvent']}-solubility.csv", mime="text/csv",
                               key=f"plot_csv_{key}")
    except Exception:
        # Chart failure must never prevent reading an answer or its evidence.
        st.info("The plot could not be displayed. The answer and original sources are still available.")


def render_evidence(item):
    """A chunk only the structure route can reach, with the broken name marked."""
    snippet = html.escape(item["snippet"])
    if item["fragment"]:
        snippet = snippet.replace(html.escape(item["fragment"]), f"<mark>{html.escape(item['fragment'])}</mark>", 1)
    with st.container(border=True):
        st.markdown(
            f"<span class='ck-meta'><b>{item['source_doc']}</b> &nbsp;·&nbsp; "
            f"page {item['page_number']} &nbsp;·&nbsp; "
            f"split by the PDF as "
            f"<code>{item['fragment'] or 'n/a'}</code></span>",
            unsafe_allow_html=True,
        )
        st.markdown(f"<div class='ck-snip'>...{snippet}...</div>", unsafe_allow_html=True)


def render_image_structures(records):
    """Show source crop beside the connectivity reconstructed by OCSR."""
    if not records:
        st.info("No accepted image structure matches the selected molecule.")
        return
    for record in records:
        with st.container(border=True):
            st.markdown(
                f"<div class='ck-meta'>{html.escape(record['source_doc'])} &nbsp;·&nbsp; "
                f"PAGE {record['page_number']} &nbsp;·&nbsp; "
                f"{html.escape(record.get('source_kind', 'image')).upper()} &nbsp;·&nbsp; "
                f"{html.escape(record.get('recognizer', 'OCSR'))}</div>",
                unsafe_allow_html=True,
            )
            source_col, reconstructed_col = st.columns(2, gap="large")
            with source_col:
                st.markdown("**Source crop**")
                image_path = record.get("image_path")
                if image_path and Path(image_path).exists():
                    st.image(image_path, use_container_width=True)
                else:
                    st.caption("The local source crop is not available in this deployment.")
            with reconstructed_col:
                st.markdown("**Machine-read connectivity**")
                svg = depict(record["smiles"], (360, 220))
                if svg:
                    st.markdown(svg, unsafe_allow_html=True)
                st.code(record["smiles"], language=None)
            confidence = record.get("confidence")
            confidence_text = (
                f" · model confidence {confidence:.3f}" if isinstance(confidence, (int, float))
                else ""
            )
            st.caption(
                f"RDKit-valid · InChIKey {record['inchikey']}{confidence_text}. "
                "Compare the reconstruction with the crop; validity does not prove recognition accuracy."
            )


def render_ocr_candidates(records, limit=24):
    """Show local crop evidence for accepted and refused OCSR candidates."""
    if not records:
        st.info("No structure-image candidates were retained in this index.")
        return
    shown = records[:limit]
    for record in shown:
        with st.container(border=True):
            st.markdown(
                f"<div class='ck-meta'>{html.escape(record.get('source_doc', 'unknown source'))} "
                f"&nbsp;·&nbsp; PAGE {record.get('page_number', '?')} &nbsp;·&nbsp; "
                f"{html.escape(record.get('source_kind', 'image')).upper()}</div>",
                unsafe_allow_html=True,
            )
            image_path = record.get("image_path")
            if record.get("status") == "accepted":
                source_col, reconstructed_col = st.columns(2, gap="large")
                with source_col:
                    st.markdown("**Retained source crop**")
                    if image_path and Path(image_path).exists():
                        st.image(image_path, use_container_width=True)
                    else:
                        st.caption("The local source crop is not available in this deployment.")
                with reconstructed_col:
                    st.markdown("**Recognised connectivity**")
                    svg = depict(record.get("smiles", ""), (360, 220))
                    if svg:
                        st.markdown(svg, unsafe_allow_html=True)
                st.success("Accepted: RDKit parsed the predicted structure.")
                st.code(record.get("smiles", ""), language=None)
            else:
                if image_path and Path(image_path).exists():
                    st.image(image_path, caption="Retained source crop", use_container_width=True)
                else:
                    st.caption("The local source crop is not available in this deployment.")
                reason = record.get("reason", "not accepted")
                st.warning(f"Refused: {reason}. This crop is not searchable.")
                if record.get("predicted_smiles"):
                    st.code(record["predicted_smiles"], language=None)
    if len(records) > len(shown):
        st.caption(f"Showing {len(shown)} of {len(records)} retained candidates.")


def render_recovered_grid(cells, spanning_rows=None):
    """Show a recovered grid as-is. Empty cells stay visibly empty."""
    spanning = set(spanning_rows or [])
    rows = []
    for number, row in enumerate(cells or []):
        # No header row is assumed: OCR cannot tell a heading from a value.
        if number in spanning:
            body = (
                f"<td colspan='{len(row)}'><em>{html.escape(row[0])}</em></td>"
            )
        else:
            body = "".join(f"<td>{html.escape(value)}</td>" for value in row)
        rows.append(f"<tr>{body}</tr>")
    st.markdown(
        "<div class='ck-grid'><table>" + "".join(rows) + "</table></div>",
        unsafe_allow_html=True,
    )


def render_table_candidates(records, limit=12):
    """Show crop evidence for every raster the table OCR actually attempted."""
    if not records:
        st.info("No image-only tables were attempted in this index.")
        return
    shown = records[:limit]
    for record in shown:
        with st.container(border=True):
            st.markdown(
                f"<div class='ck-meta'>{html.escape(record.get('filename', 'unknown source'))} "
                f"&nbsp;·&nbsp; PAGE {record.get('page', '?')} &nbsp;·&nbsp; "
                f"{record.get('width', '?')}×{record.get('height', '?')} PX</div>",
                unsafe_allow_html=True,
            )
            image_path = record.get("image_path")
            if image_path and Path(image_path).exists():
                st.image(image_path, caption="Retained source raster", use_container_width=True)
            else:
                st.caption("The local source raster is not available in this deployment.")
            confidence = record.get("mean_confidence")
            if record.get("status") == "accepted":
                st.success(
                    f"Accepted: {record.get('n_rows')}×{record.get('n_cols')} grid at "
                    f"{confidence:.1f} mean word confidence."
                )
                render_recovered_grid(record.get("cells"), record.get("spanning_rows"))
                spanning_share = record.get("spanning_fraction") or 0.0
                st.caption(
                    "Machine-read cells, italic rows span the table. "
                    f"Column edges voted at x = {record.get('column_boundaries')}; "
                    f"{spanning_share:.0%} of rows spanned and were kept whole. "
                    "Check this crop before quoting a number."
                )
            else:
                reason = record.get("reason", "not accepted")
                seen = f" at {confidence:.1f} mean confidence" if confidence else ""
                st.warning(f"Refused: {reason}{seen}. No cells are indexed.")
            if record.get("raw_text"):
                with st.expander("Raw OCR text, before any layout recovery"):
                    st.code(record["raw_text"], language=None)
    if len(records) > len(shown):
        st.caption(f"Showing {len(shown)} of {len(records)} attempted rasters.")


# --------------------------------------------------------------------------

def main():
    st.set_page_config(page_title="ChemKey · Research workspace", page_icon="⌬", layout="wide")
    st.markdown(CSS, unsafe_allow_html=True)
    if not os.path.exists(INDEX_PATH):
        st.error(
            f"No index at `{PRIVATE_INDEX}` or `{PUBLIC_INDEX}`. "
            "Add PDFs to papers/ and run `python build_index.py`."
        )
        return
    mtime = os.path.getmtime(INDEX_PATH)
    index = load_index(INDEX_PATH, mtime)
    chunks = index["chunks"]
    documents = sorted({c["source_doc"] for c in chunks})
    with st.sidebar:
        st.markdown("<div class='ck-brand'>⌬ ChemKey<span>&#10022;</span></div>", unsafe_allow_html=True)
        st.caption("THE CHEMISTRY RESEARCH WORKSPACE")
        st.divider()
        st.markdown("**Your library**")
        st.caption(f"{len(documents)} papers · {len(chunks)} indexed passages")
        for i, name in enumerate(documents, 1):
            st.markdown(f"<div class='ck-paper'>0{i} &nbsp; {html.escape(name.removesuffix('.pdf').replace('_', ' '))}</div>", unsafe_allow_html=True)
        st.divider()
        st.markdown("**Retrieval settings**")
        top_k = st.slider("Passages per search", 2, 14, 6, help="Chat includes the surrounding page for each matched passage to keep tables intact.")
        allow_network = st.toggle("Resolve with PubChem", value=False,
                                  help="Allow network lookup for names missing from the local lexicon and cache.")
        with st.expander("Answer model"):
            typed_key = st.text_input("OpenRouter API key", type="password")
            st.caption(f"Model: {ck.MODEL_NAME}")
        api_key = typed_key or os.getenv("OPENROUTER_API_KEY", "")
        st.caption("● Answer model configured" if api_key else "○ Add a key for generated answers")
        st.divider()
        with st.expander("Scope & limits"):
            if index.get("image_structures"):
                st.caption("Text plus RDKit-validated image structures. Every accepted OCSR result keeps its source crop for review. Connectivity matching does not distinguish stereoisomers.")
            else:
                st.caption("No image structures in this index. Run build_index.py --structure-images to add reviewed OCSR evidence. Connectivity matching does not distinguish stereoisomers.")
    st.markdown("<div class='ck-topbar'><strong>CHEMKEY / RESEARCH</strong><span>LITERATURE EXPLORER</span><span class='ck-status'>● &nbsp; Local index ready</span></div>", unsafe_allow_html=True)
    st.markdown("<h1 class='ck-hero-title'>One structure. <em>Every name.</em></h1>", unsafe_allow_html=True)
    left, right = st.columns([2.5, 1], gap="large")
    with left:
        with st.container(border=True, key="ck-raised"):
            st.markdown("**Find a molecule**")
            choice = st.selectbox("Start with an example", list(EXAMPLES))
            default_smiles, default_name = EXAMPLES[choice]
            mode = st.radio("Search by", ["Chemical name", "SMILES"], horizontal=True)
            text_query = st.text_input("Chemical name", value=default_name, key=f"common_name_{choice}",
                                       disabled=mode == "SMILES")
            if mode == "SMILES":
                query_smiles = st.text_input("SMILES", value=default_smiles, key=f"smiles_{choice}")
                subject_label = "Selected structure"
            else:
                query_smiles = resolve_name(text_query, allow_network) if text_query.strip() else None
                subject_label = text_query
            if not query_smiles:
                st.info("No structure resolved. Try another name, enable PubChem, or search by SMILES.")
                return
            full_key, block = ck.to_inchikey(query_smiles)
            if not block:
                st.error("That SMILES could not be parsed. Check the structure and try again.")
                return

    with right:
        svg = depict(query_smiles, (370, 220))
        if svg:
            st.markdown(f"<div class='ck-mol'>{svg}<div class='ck-meta'>MOLECULAR CONNECTIVITY</div><div class='ck-key'>{block}</div></div>", unsafe_allow_html=True)
        with st.expander("Structure details"):
            st.code(query_smiles, language=None)
            st.caption(full_key)
    structural_ids = chunks_with_block(chunks, block)
    literal_ids = chunks_with_text(chunks, text_query) if text_query and mode == "Chemical name" else set()
    mentions = ck.documents_for_structure(query_smiles, index)
    names = spellings_for_block(index, block)
    with st.container(border=True):
        st.markdown("### Different names. Same molecule.")
        name_col, structure_col = st.columns([1.7, 1], gap="large")
        with name_col:
            st.markdown("<div class='ck-meta'>EXACT-NAME SEARCH · PASSAGES PER SPELLING</div>", unsafe_allow_html=True)
            comparison_names = list(names)
            if mode == "Chemical name" and not any(
                    name.casefold() == text_query.casefold() for name in comparison_names):
                comparison_names.append(text_query)
            counts = [(name, len(chunks_with_text(chunks, name))) for name in comparison_names]
            maximum = max([len(structural_ids)] + [count for _, count in counts] + [1])
            for name, count in counts:
                is_cas = bool(re.fullmatch(r"\d{2,7}-\d{2}-\d", name))
                selected = mode == "Chemical name" and name.casefold() == text_query.casefold()
                label = ("CAS " if is_cas else "") + html.escape(name)
                st.markdown(f"<div class='ck-name-row{' selected' if selected else ''}'><span>{label}{' <small>YOUR QUERY</small>' if selected else ''}</span><b>{count}</b><div class='ck-name-track'><i style='width:{100*count/maximum:.2f}%'></i></div></div>", unsafe_allow_html=True)
            if not counts:
                st.caption("No names indexed for this structure.")
        with structure_col:
            st.metric("Structure passages", len(structural_ids))
            st.caption(f"One connectivity key · {len(mentions)} papers · {len(names)} indexed names")
            if mode == "Chemical name":
                recovered = len(structural_ids - literal_ids)
                st.markdown(f"<div class='ck-recovery'><strong>+{recovered}</strong> passages beyond this exact name</div>", unsafe_allow_html=True)
            st.caption("Names and CAS identifiers resolve to the same structure.")
        with st.expander("How to read this comparison"):
            st.caption("Each name matches only that exact phrase. Structure retrieval joins indexed names by connectivity key. Counts include references; ranked results filter detected bibliographies. Name counts overlap and should not be added.")
            st.caption("This shows the benefit over one name, not over a complete synonym list. See Retrieval insights for that comparison. Accepted image structures are shown separately with their original crops.")
    evidence_tab, image_tab, table_tab, chat_tab, compare_tab, library_tab = st.tabs(["Evidence explorer", "Image structures", "Image tables", "Ask the library", "Retrieval insights", "Source library"])
    with evidence_tab:
        c1, c2 = st.columns([3, 2])
        question_filter = c1.text_input("Narrow by topic", placeholder="e.g. solubility, hydrogen bonds, DMSO")
        selected_docs = c2.multiselect("Filter papers", documents, placeholder="All papers")
        scoped = {**index, "chunks": [c for c in chunks if not selected_docs or c["source_doc"] in selected_docs]}
        hits, _ = ck.structure_search(query_smiles, scoped, question_filter, top_k=top_k)
        st.caption(f"{len(hits)} ranked passages · pinned to {subject_label}")
        st.download_button("↓ Export evidence", json.dumps({"query":subject_label,"smiles":query_smiles,"block":block,"sources":hits}, indent=2),
                           file_name="chemkey-evidence.json", mime="application/json", disabled=not hits)
        render_sources(hits, "No indexed passages for this structure in the selected papers.")
    with image_tab:
        st.subheader("Structures captured from figures and tables")
        report = index.get("structure_ocr") or {}
        counts = report.get("counts") or {}
        accepted_records = index.get("image_structures") or []
        candidates = index.get("structure_ocr_candidates") or accepted_records
        m1, m2, m3 = st.columns(3)
        m1.metric("Candidate crops", counts.get("crops", 0))
        m2.metric("RDKit-valid", counts.get("accepted", len(accepted_records)))
        m3.metric("Match selected molecule", len(image_structures_for_block(index, block)))
        if report:
            st.caption(
                f"{report.get('segmenter', 'segmenter')} → "
                f"{report.get('recognizer', 'recognizer')} → RDKit → InChIKey. "
                "Invalid predictions are refused, not indexed."
            )
        else:
            st.caption("This index was built without the optional structure-image pipeline.")
        st.markdown("**Retained crop review**")
        render_ocr_candidates(candidates)
    with table_tab:
        st.subheader("Tables read out of image-only pages")
        table_report = index.get("table_ocr") or {}
        table_counts = table_report.get("counts") or {}
        accepted_tables = index.get("image_tables") or []
        table_candidates = index.get("table_ocr_candidates") or accepted_tables
        table_chunks = [c for c in chunks if c.get("extraction_method") == "table_ocr"]
        t1, t2, t3 = st.columns(3)
        t1.metric("Rasters OCR'd", table_counts.get("ocr_attempted", len(table_candidates)))
        t2.metric("Grids accepted", table_counts.get("accepted", len(accepted_tables)))
        t3.metric("Searchable passages added", len(table_chunks))
        if table_report:
            st.caption(
                "Tesseract word boxes → column edges voted per row → grid. "
                f"Mean word confidence below {table_report.get('confidence_threshold')} "
                "is refused, and so is a grid that cannot be recovered. "
                "Cell values are never invented."
            )
        else:
            st.caption("This index was built without the optional image-table pipeline.")
        st.markdown("**Attempted raster review**")
        render_table_candidates(table_candidates)
    with compare_tab:
        st.subheader("What does structure actually add?")
        reach = reach_report(INDEX_PATH, mtime, block, text_query if mode == "Chemical name" else "")
        c1,c2,c3 = st.columns(3)
        c1.metric("All indexed spellings combined", reach["union"])
        c2.metric("Only structure finds", reach["adds"])
        c3.metric("Only spellings find", reach["misses"])
        st.caption("Exact phrase coverage across all chunks. This is a synonym baseline, not BM25 ranking.")
        if not reach["adds"] and structural_ids:
            st.info("For this molecule, a complete synonym list matches or exceeds structure coverage. Structure helps you search without knowing that list.")
        for item in reach["unreachable"]:
            render_evidence(item)
        st.markdown("**Names linked to this structure**")
        st.markdown("".join(f"<span class='ck-pill'>{html.escape(n)} · {reach['counts'][n]}</span>" for n in names), unsafe_allow_html=True)
        if mode == "Chemical name":
            st.divider()
            c1,c2 = st.columns(2)
            with c1:
                st.markdown("**Text ranking · BM25**")
                st.caption("Token matches can include partial names; these are not exact phrase counts.")
                render_sources(ck.bm25_search(text_query, chunks, top_k=top_k), "No matching text tokens.")
            with c2:
                st.markdown("**Structure ranking**")
                results, _ = ck.structure_search(query_smiles, index, text_query, top_k=top_k)
                render_sources(results, "No matching structure.")
    with library_tab:
        st.subheader("The papers behind the answers")

        for doc in documents:
            with st.container(border=True):
                st.markdown(f"**{doc.removesuffix('.pdf').replace('_', ' ')}**")
                dc = [c for c in chunks if c['source_doc'] == doc]
                st.caption(f"{len(dc)} indexed passages · {len({c['page_number'] for c in dc})} pages with indexed text")
                accession = PAPER_SOURCES.get(doc)
                if accession:
                    st.markdown(
                        f"[Open on PubMed Central &rarr;]"
                        f"(https://pmc.ncbi.nlm.nih.gov/articles/{accession}/) "
                        f"&nbsp;<span class='ck-meta'>{accession}</span>",
                        unsafe_allow_html=True,
                    )
                else:
                    st.caption("No accession recorded for this file.")
        st.info("Answers must distinguish measurements, collected literature values, and predictions. Retrieved passages may be incomplete; check the original paper before treating a list as exhaustive.")
    with chat_tab:
        # ---------------- chat ----------------
        st.subheader("Ask the library")

        if st.session_state.get("chat_block") != block:
            st.session_state.chat_block = block
            st.session_state.chat = []
            st.session_state.pop("pending", None)

        st.caption(f"About **{subject_label}** · cited answers · follow-ups supported")

        buttons = st.columns(len(PRESET_QUESTIONS) + 1)
        for column, (label, preset, answerable) in zip(buttons, PRESET_QUESTIONS):
            hint = "Evidence availability depends on the selected molecule"
            if column.button(label, help=f"{preset}\n\n({hint})", use_container_width=True):
                st.session_state.pending = preset
        if buttons[-1].button("Clear", use_container_width=True):
            st.session_state.chat = []
            st.session_state.pop("pending", None)

        for message_number, message in enumerate(st.session_state.chat):
            with st.chat_message(message["role"]):
                st.markdown(message["content"])
                render_plots(message, f"{block}_{message_number}")
                if message.get("sources"):
                    with st.expander(f"{len(message['sources'])} sources"):
                        render_sources(message["sources"], "No sources.")

        if st.session_state.chat and st.session_state.chat[-1].get("followups"):
            st.caption("Continue exploring")
            for number, suggestion in enumerate(st.session_state.chat[-1]["followups"]):
                if st.button(suggestion, key=f"followup_{len(st.session_state.chat)}_{number}"):
                    st.session_state.pending = suggestion

        question = st.chat_input("Ask about solubility, coformers, or experimental conditions…") or st.session_state.pop("pending", None)
        if not question:
            return
        if not api_key:
            st.warning("Add an OpenRouter API key in the sidebar to generate the answer.")
            return

        st.session_state.chat.append({"role": "user", "content": question})
        with st.chat_message("user"):
            st.markdown(question)

        history = [{"role": m["role"], "content": m["content"]}
                   for m in st.session_state.chat[:-1]]
        sources, _ = ck.conversation_search(
            question, query_smiles, index, history=history, top_k=top_k
        )
        started = time.perf_counter()

        with st.chat_message("assistant"):
            try:
                with st.spinner(f"Asking {ck.MODEL_NAME} over {len(sources)} chunks..."):
                    answer = ck.ask_openrouter(
                        question, sources, api_key, history=history,
                        subject={"label": subject_label,
                                 "block": block,
                                 "names": spellings_for_block(index, block)},
                    )
            except requests.HTTPError as error:
                st.session_state.chat.pop()
                st.error(f"OpenRouter returned an error: {error.response.text[:400]}")
                return
            except requests.RequestException as error:
                st.session_state.chat.pop()
                st.error(f"Could not reach OpenRouter: {error}")
                return

            answer, followups = ck.split_answer_followups(answer)
            st.markdown(answer)
            routes = ", ".join(sorted({s["route"] for s in sources})) or "none"
            st.caption(f"{len(sources)} chunks · routes: {routes} · "
                       f"{int((time.perf_counter() - started) * 1000)} ms")
            with st.expander(f"{len(sources)} sources"):
                render_sources(sources, "No sources.")

        plot_requested = bool(re.search(r"\b(plot|chart|graph|visuali[sz]e)\b", question, re.I))
        try:
            tables = plots.solubility_tables(sources, block)
        except (ValueError, TypeError, KeyError):
            tables = []
        if plot_requested and not tables:
            answer += "\n\nA verified numerical plot is not available for this question. The chart feature currently supports Table 1 solubility data; qualitative coformer findings remain in the cited answer."
        st.session_state.chat.append(
            {"role": "assistant", "content": answer, "sources": sources, "followups": followups,
             "plots": tables, "plot_requested": plot_requested}
        )
        st.rerun()

if __name__ == "__main__":
    main()
