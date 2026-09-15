"""ChemKeyRAG core pipeline.

Extract -> Standardize -> Connect -> Retrieve.

The idea in one line: resolve every chemical name mentioned in a paper to an
InChIKey at ingest time, store those keys next to the text chunk, and you can
then retrieve documents by *structure* even when they never used the name you
searched for.

No vector database, no embeddings, no trained NER model. Candidate chemical
names are generated with a deliberately dumb lexical pass; PubChem does the
entity linking; RDKit does the normalisation.
"""

import json
import math
import os
import re
import time
from itertools import zip_longest
from urllib.parse import quote

import requests
from pypdf import PdfReader
from rdkit import Chem, RDLogger

RDLogger.DisableLog("rdApp.*")


PUBCHEM_URL = "https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name/{name}/property/{prop}/TXT"
PUBCHEM_DELAY_SECONDS = 0.25  # PubChem asks for <= 5 requests/second
MIN_HEAVY_ATOMS = 3           # drop water, counter-ions and lexical noise


# --------------------------------------------------------------------------
# 1. PDF -> text chunks
# --------------------------------------------------------------------------

# A PDF breaks a word across a line as "parace-\ntamol". Left alone, the
# lexical pass sees "parace" and "tamol" and the molecule is lost.
_UNICODE_HYPHENS = dict.fromkeys(map(ord, "\u2010\u2011\u2012\u2013"), "-")
_LINEBREAK_HYPHEN_RE = re.compile(r"(\w)-[ \t\xa0]*\n[ \t\xa0]*(\w)")


def repair_page_text(text):
    """Undo the two ways a PDF splits a chemical name.

    Rejoins words hyphenated across a line break and folds the unicode
    hyphen variants onto ASCII "-" so locant patterns still match.
    """
    return _LINEBREAK_HYPHEN_RE.sub(r"\1\2", text.translate(_UNICODE_HYPHENS))


def extract_pdf_chunks(pdf_path, chunk_words=180, overlap_words=40):
    """Split a PDF into overlapping word windows, one record per window."""
    reader = PdfReader(pdf_path)
    doc_name = os.path.basename(pdf_path)
    chunks = []

    for page_index, page in enumerate(reader.pages, start=1):
        words = repair_page_text(page.extract_text() or "").split()
        if not words:
            continue

        start = 0
        while start < len(words):
            end = min(start + chunk_words, len(words))
            chunks.append(
                {
                    "source_doc": doc_name,
                    "page_number": page_index,
                    "text": " ".join(words[start:end]),
                }
            )
            if end == len(words):
                break
            start = max(end - overlap_words, start + 1)

    return chunks


# --------------------------------------------------------------------------
# 2. Candidate chemical names (lexical only - PubChem is the real filter)
# --------------------------------------------------------------------------

CHEM_SUFFIXES = (
    "amide", "amine", "aniline", "anilide", "azole", "caine", "cillin",
    "ene", "ine", "ide", "ate", "ite", "one", "ol", "oic", "phen",
    "pine", "pyrine", "thane", "xanthine", "yne", "zine", "zole",
)

# Common English words that end in a chemical-looking suffix. Without this the
# candidate list is dominated by "determine", "state", "provide" and friends.
ENGLISH_STOPWORDS = {
    "machine", "medicine", "determine", "examine", "define", "combine",
    "routine", "baseline", "crystalline", "engine", "imagine", "genuine",
    "doctrine", "discipline", "guideline", "outline", "pipeline", "decline",
    "incline", "online", "offline", "turbine", "marine", "cuisine", "famine",
    "provide", "guide", "slide", "divide", "decide", "beside", "inside",
    "outside", "worldwide", "confide", "reside", "subside", "wide", "side",
    "alongside", "provide", "oxide",
    # Generic chemistry vocabulary that is not a compound.
    "solvate", "hydrate", "substrate", "filtrate", "precipitate", "distillate",
    "eluate", "analyte", "electrolyte", "cocrystal", "isomerine",
    "state", "rate", "indicate", "calculate", "estimate", "separate",
    "associate", "appropriate", "moderate", "accurate", "create", "generate",
    "evaluate", "demonstrate", "aggregate", "private", "candidate",
    "duplicate", "coordinate", "intermediate", "approximate", "ultimate",
    "adequate", "operate", "relate", "correlate", "validate", "update",
    "template", "plate", "late", "date", "delegate", "investigate",
    "illustrate", "concentrate", "saturate", "denote", "note", "zone",
    "alone", "none", "done", "phone", "stone", "tone", "prone", "milestone",
    "control", "symbol", "protocol", "scene", "gene", "hygiene", "convene",
    "plane", "membrane", "mundane", "opposite", "composite", "site",
    "despite", "finite", "infinite", "definite", "opposite", "suite",
    "white", "quite", "write", "cite", "excite", "unite", "ignite",
    "profile", "while", "whole", "role", "sole", "pole", "console",
    "sample", "simple", "multiple", "principle", "example", "people",
}

# Chemical morphemes. A word built from two or more of these is almost always a
# compound name, which catches drug names a suffix rule misses ("phenacetin" =
# phen + acet, "acetaminophen" = acet + amino + phen).
CHEM_MORPHEMES = (
    "acet", "amino", "amid", "anil", "azol", "benz", "brom", "carb", "chlor",
    "citr", "cycl", "eth", "fluor", "hydroxy", "iod", "meth", "naphth", "nitr",
    "oxal", "phen", "phosph", "prop", "pyr", "quin", "salic", "sulf", "tartr",
    "thio", "xanth",
)

# Solvents and small molecules a suffix rule will never catch.
SEED_VOCABULARY = {"water", "acetone", "chloroform", "hexane", "toluene"}

_WORD_RE = re.compile(r"[A-Za-z0-9\[\]\(\),'\-]+")
_LOCANT_RE = re.compile(r"(^|[^a-z])\d+[,\-]")


def _clean_token(token):
    return token.strip("[](),.;:'\"").lower()


def _morpheme_count(word):
    """Number of distinct, non-overlapping chemical morphemes in a word."""
    spans = []
    for morpheme in CHEM_MORPHEMES:
        position = word.find(morpheme)
        if position < 0:
            continue
        end = position + len(morpheme)
        if any(position < span_end and span_start < end for span_start, span_end in spans):
            continue
        spans.append((position, end))
    return len(spans)


def _looks_chemical(word):
    """True if a single word is worth spending a PubChem lookup on."""
    if len(word) < 5 or word in ENGLISH_STOPWORDS:
        return False
    if word in SEED_VOCABULARY:
        return True

    has_locant = bool(_LOCANT_RE.search(word)) or word.startswith(("n-", "o-", "s-"))
    has_suffix = word.endswith(CHEM_SUFFIXES)

    if has_locant and (has_suffix or any(character.isdigit() for character in word)):
        return True
    if has_suffix and word.isalpha():
        return True
    return len(word) >= 8 and word.isalpha() and _morpheme_count(word) >= 2


def candidate_names(text, vocab=()):
    """Return the set of candidate chemical names found in a block of text.

    `vocab` is an optional set of known names (the shipped lexicon). A PDF also
    splits a word by inserting a stray space - "ca ffeine" - which no suffix
    rule can catch. Rejoining adjacent tokens is only safe against a closed
    vocabulary, so it is gated on `vocab` and costs no network call.
    """
    words = [_clean_token(token) for token in _WORD_RE.findall(text)]
    found = {word for word in words if word and _looks_chemical(word)}

    # Keep complete systematic names and CAS identifiers intact. Also recover
    # known names joined to neighbouring terms, e.g. Paracetamol-Oxalic.
    # Closed-vocabulary components avoid inventing fragments of chemical names.
    for word in words:
        if "-" in word and word not in vocab:
            found.update(part for token in word.split("-")
                         if (part := _clean_token(token)) in vocab)

    # ponytail: lexicon-gated only. A trained NER model is the real fix for
    # names the corpus splits in ways the shipped vocabulary does not cover.
    for first, second in zip(words, words[1:]):
        if first and second and first + second in vocab:
            found.add(first + second)

    # "oxalic acid", "salicylic acid" - a suffix rule alone misses these.
    for first, second in zip(words, words[1:]):
        if second == "acid" and len(first) > 4 and first.endswith(("ic", "oic")):
            found.add(f"{first} acid")

    return found


# --------------------------------------------------------------------------
# 3. Name -> SMILES  (PubChem PUG-REST, cached on disk)
# --------------------------------------------------------------------------

class NameResolver:
    """Resolves chemical names to SMILES.

    Three tiers, cheapest first:
      1. a shipped lexicon  - offline, deterministic, reviewable in git
      2. an on-disk cache   - names resolved online in a previous run
      3. PubChem PUG-REST   - the fallback for anything new

    The lexicon is what makes the demo start instantly and run with no network.
    Everything it resolves is verified by `make_lexicon.py` before it is written.
    """

    def __init__(self, lexicon_path="data/lexicon.json",
                 cache_path="data/name_cache.json", online=True):
        self.cache_path = cache_path
        self.online = online
        self.lexicon = self._load(lexicon_path)
        self.cache = self._load(cache_path)

    @staticmethod
    def _load(path):
        if path and os.path.exists(path):
            with open(path, encoding="utf-8") as handle:
                return json.load(handle)
        return {}

    def save(self):
        os.makedirs(os.path.dirname(self.cache_path) or ".", exist_ok=True)
        with open(self.cache_path, "w", encoding="utf-8") as handle:
            json.dump(self.cache, handle, indent=2, sort_keys=True)

    def resolve(self, name):
        """Return a SMILES string, or None if the name is not a compound."""
        # Lexicon and cache keys are lowercase; a user types "Paracetamol".
        name = name.strip().lower()
        if name in self.lexicon:
            return self.lexicon[name]
        if name in self.cache:
            return self.cache[name]
        if not self.online:
            return None

        smiles = self._fetch(name)
        self.cache[name] = smiles
        time.sleep(PUBCHEM_DELAY_SECONDS)
        return smiles

    def _fetch(self, name):
        # PubChem renamed the property; try the current name, then the legacy one.
        for prop in ("SMILES", "CanonicalSMILES"):
            url = PUBCHEM_URL.format(name=quote(name), prop=prop)
            try:
                response = requests.get(url, timeout=20)
            except requests.RequestException:
                return None
            if response.status_code == 200:
                first_line = response.text.strip().splitlines()[0].strip()
                return first_line or None
            if response.status_code == 404:
                return None
        return None


# --------------------------------------------------------------------------
# 4. SMILES -> InChIKey  (RDKit standardisation)
# --------------------------------------------------------------------------

def to_inchikey(smiles):
    """Return (full_inchikey, skeleton_block) or (None, None).

    The skeleton block is the first 14 characters of the InChIKey: it encodes
    connectivity only, so stereoisomers, salts and tautomer variants of the
    same molecule collapse onto one key. That is the join key for retrieval.
    """
    if not smiles:
        return None, None

    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        return None, None

    # Keep the largest fragment - strips counter-ions and solvates.
    fragments = Chem.GetMolFrags(mol, asMols=True, sanitizeFrags=False)
    if fragments:
        mol = max(fragments, key=lambda frag: frag.GetNumHeavyAtoms())

    if mol.GetNumHeavyAtoms() < MIN_HEAVY_ATOMS:
        return None, None

    try:
        key = Chem.MolToInchiKey(mol)
    except Exception:
        return None, None

    if not key:
        return None, None
    return key, key.split("-")[0]


# --------------------------------------------------------------------------
# 5. Index build
# --------------------------------------------------------------------------

def build_index(pdf_paths, resolver, max_candidates=500, verbose=True):
    """Build the searchable index: chunks annotated with InChIKey skeletons."""
    chunks = []
    for path in pdf_paths:
        page_chunks = extract_pdf_chunks(path)
        chunks.extend(page_chunks)
        if verbose:
            print(f"  {os.path.basename(path)}: {len(page_chunks)} chunks")

    # Count candidates across the whole corpus, then resolve the most frequent.
    frequency = {}
    per_chunk = []
    for chunk in chunks:
        names = candidate_names(chunk["text"], vocab=resolver.lexicon)
        per_chunk.append(names)
        for name in names:
            frequency[name] = frequency.get(name, 0) + 1

    ranked = sorted(frequency, key=lambda name: -frequency[name])[:max_candidates]
    if verbose:
        print(f"  {len(frequency)} candidate names, resolving top {len(ranked)}")

    compounds = {}   # name -> {smiles, inchikey, block}
    for name in ranked:
        smiles = resolver.resolve(name)
        full_key, block = to_inchikey(smiles)
        if block:
            compounds[name] = {"smiles": smiles, "inchikey": full_key, "block": block}
    resolver.save()

    if verbose:
        print(f"  {len(compounds)} names resolved to structures")

    for chunk, names in zip(chunks, per_chunk):
        hits = {name: compounds[name] for name in names if name in compounds}
        chunk["compounds"] = sorted(hits)
        chunk["blocks"] = sorted({hit["block"] for hit in hits.values()})

    return {"chunks": chunks, "compounds": compounds, "image_structures": []}


def attach_image_structures(index, report):
    """Add RDKit-validated OCSR results as structure-searchable anchors.

    The crop is the evidence that a structure depiction exists.  It is not
    treated as evidence for a property or experimental claim; same-page text is
    still retrieved separately and remains subject to the normal grounding
    rules.
    """
    chunks = [
        chunk for chunk in index.get("chunks", [])
        if chunk.get("extraction_method") != "structure_ocr"
    ]
    structures = [
        dict(record) for record in report.get("structures", [])
        if record.get("status") == "accepted"
        and record.get("block")
        and record.get("smiles")
    ]
    for record in structures:
        chunks.append(
            {
                "source_doc": record["source_doc"],
                "page_number": record["page_number"],
                "text": (
                    "Machine-recognized chemical structure depiction. "
                    "Inspect the retained source crop before treating the "
                    "recognized connectivity as confirmed."
                ),
                "extraction_method": "structure_ocr",
                "compounds": [],
                "blocks": [record["block"]],
                "image_structure_ids": [record["id"]],
            }
        )
    index["chunks"] = chunks
    index["image_structures"] = structures
    # Keep every processed crop in the local index for visual audit. Rejected
    # candidates never receive a retrieval anchor and are never returned by
    # structure search.
    index["structure_ocr_candidates"] = [
        dict(record) for record in report.get("structures", [])
    ]
    index["structure_ocr"] = {
        key: report.get(key)
        for key in ("pipeline", "source_mode", "recognizer", "segmenter", "counts")
    }
    return index


def table_rows_to_text(cells):
    """Flatten a recovered grid to searchable text, one row per line."""
    return "\n".join(
        " | ".join(value for value in row if value.strip()) for row in cells or []
    ).strip()


def attach_image_tables(index, report, resolver=None):
    """Add accepted OCR'd tables as retrievable passages; keep every candidate.

    An accepted grid is OCR output, not a transcription anyone checked: the
    passage carries that warning, and the crop stays on disk for review.
    Refused rasters never become passages.
    """
    chunks = [
        chunk for chunk in index.get("chunks", [])
        if chunk.get("extraction_method") != "table_ocr"
    ]
    accepted = [
        dict(record) for record in report.get("tables", [])
        if record.get("status") == "accepted" and record.get("cells")
    ]
    compounds = index.setdefault("compounds", {})
    for record in accepted:
        body = table_rows_to_text(record["cells"])
        names = []
        if resolver is not None:
            for name in candidate_names(body, vocab=resolver.lexicon):
                if name not in compounds:
                    smiles = resolver.resolve(name)
                    full_key, block = to_inchikey(smiles)
                    if not block:
                        continue
                    compounds[name] = {
                        "smiles": smiles, "inchikey": full_key, "block": block,
                    }
                names.append(name)
        chunks.append(
            {
                "source_doc": record["filename"],
                "page_number": record["page"],
                "text": (
                    "Table recovered by OCR from an image-only page. "
                    "Cell values are machine-read; check the retained crop "
                    "before quoting a number.\n" + body
                ),
                "extraction_method": "table_ocr",
                "compounds": sorted(set(names)),
                "blocks": sorted({compounds[n]["block"] for n in names}),
                "table_ocr_id": record["id"],
            }
        )
    if resolver is not None:
        resolver.save()
    index["chunks"] = chunks
    index["image_tables"] = accepted
    index["table_ocr_candidates"] = [
        dict(record) for record in report.get("tables", [])
        if record.get("status") != "skipped"
    ]
    index["table_ocr"] = {
        key: report.get(key)
        for key in (
            "classifier", "confidence_threshold", "min_rows", "min_cols",
            "min_fill_fraction", "column_gap_scale", "column_support_fraction",
            "tesseract_version", "counts",
        )
    }
    return index


def image_structures_for_block(index, block):
    """Return accepted, reviewable image recognitions for one connectivity key."""
    if not block:
        return []
    return [
        record for record in index.get("image_structures", [])
        if record.get("block") == block and record.get("status") == "accepted"
    ]


def save_index(index, path="data/index.json"):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(index, handle, indent=2)


def load_index(path="data/index.json"):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


# --------------------------------------------------------------------------
# 6. Retrieval
# --------------------------------------------------------------------------

def tokenize(text):
    return re.findall(r"[a-z0-9]+", text.lower())


# BM25 gives a rare token a large IDF. Question words are rare in a chemistry
# corpus, so "what ... about ... compound" outscores the one word that carries
# the intent. Both lists are dropped from the query before scoring.
STOPWORDS = frozenset("""
a about all also an and any are as at be because been between both but by can could
did do does for from had has have how i if in into is it its may might more most no
not of on or other our out over same should so some such than that the their them
then there these they this those to too under up us use used very was we were what
when where which while who why will with within would you your
""".split())

QUESTION_WORDS = frozenset("""
compound compounds molecule molecules substance paper papers article study studies
report reported reports work described describe explain tell give list show find
information data results
""".split())


def query_terms(question):
    """Content words only. Falls back to the raw tokens if nothing survives."""
    tokens = tokenize(question)
    kept = [t for t in tokens if t not in STOPWORDS and t not in QUESTION_WORDS]
    return kept or tokens


# A reference list is a keyword magnet: it repeats every compound name and every
# topic word in the paper while carrying no findings. Left in, it crowds out the
# chunks that hold the actual data.
_REF_MARKER = re.compile(r"\[(?:CrossRef|PubMed)\]|\bdoi\b|https?://doi\.org", re.I)
_REF_CITATION = re.compile(r"\b\d{1,3}\.\s+[A-Z][A-Za-z\-']+,\s*[A-Z]\.")
_REF_INITIALS = re.compile(r"\b[A-Z]\.(?:\s?[A-Z]\.)*\s*[;,]")


def looks_like_bibliography(text):
    """True for a chunk that is a reference list rather than prose."""
    markers = len(_REF_MARKER.findall(text))
    citations = len(_REF_CITATION.findall(text))
    initials = len(_REF_INITIALS.findall(text))
    return markers >= 3 or citations >= 3 or (initials >= 6 and markers >= 1)


def bm25_search(question, chunks, top_k=4, drop_references=True):
    """Plain lexical retrieval - the baseline the structure path has to beat."""
    if drop_references:
        chunks = [c for c in chunks if not looks_like_bibliography(c["text"])]
    if not chunks:
        return []

    doc_tokens = [tokenize(chunk["text"]) for chunk in chunks]
    query_tokens = query_terms(question)
    avg_len = sum(len(tokens) for tokens in doc_tokens) / len(doc_tokens)
    k1, b = 1.5, 0.75

    document_frequency = {}
    for tokens in doc_tokens:
        for token in set(tokens):
            document_frequency[token] = document_frequency.get(token, 0) + 1

    scored = []
    for chunk, tokens in zip(chunks, doc_tokens):
        counts = {token: tokens.count(token) for token in set(tokens)}
        score = 0.0
        for token in query_tokens:
            if token not in counts:
                continue
            df = document_frequency.get(token, 0)
            idf = math.log(1 + (len(chunks) - df + 0.5) / (df + 0.5))
            tf = counts[token]
            score += idf * (tf * (k1 + 1)) / (tf + k1 * (1 - b + b * len(tokens) / avg_len))
        if score > 0:
            scored.append({**chunk, "score": round(score, 3), "route": "text"})

    return sorted(scored, key=lambda row: row["score"], reverse=True)[:top_k]


def structure_search(query_smiles, index, question="", top_k=4, drop_references=True):
    """Retrieve by structure: exact InChIKey skeleton match, then rank by text."""
    _, block = to_inchikey(query_smiles)
    if not block:
        return [], None

    matches = [chunk for chunk in index["chunks"] if block in chunk.get("blocks", [])]
    if drop_references:
        matches = [c for c in matches if not looks_like_bibliography(c["text"])]
    if not matches:
        return [], block

    if question:
        ranked = bm25_search(question, matches, top_k=top_k)
        if ranked:
            for row in ranked:
                row["route"] = "structure"
            return ranked, block

    ranked = sorted(matches, key=lambda chunk: -len(chunk["compounds"]))[:top_k]
    return [{**chunk, "score": None, "route": "structure"} for chunk in ranked], block


def retrieval_query(question, history=(), carry=3):
    """Retain the topic and recent answer referents for conversational retrieval.

    Assistant prose is a retrieval hint only; answers must still be supported by
    freshly retrieved sources. Short follow-ups need the entities in that prose.
    """
    recent = list(history)[-MAX_HISTORY_TURNS:]
    prior = [turn["content"] for turn in recent
             if turn.get("role") == "user"][-carry:] if carry else []
    answers = [turn["content"] for turn in recent if turn.get("role") == "assistant"]
    if carry and answers and len(query_terms(question)) <= 8:
        prior.append(re.sub(r"\[Source \d+\]", "", answers[-1])[:1600])
    return " ".join(prior + [question])


def expand_source_pages(sources, index):
    """Keep page-local tables and definitions with a structure-matched anchor.

    Context chunks need not repeat the molecule name (numeric table continuations
    often don't). Retain the anchor's identity and mark expanded provenance.
    """
    expanded = []
    seen = set()
    for source in sources:
        page = (source["source_doc"], source["page_number"])
        if page in seen:
            continue
        seen.add(page)
        parts = [c for c in index["chunks"]
                 if (c["source_doc"], c["page_number"]) == page
                 and not looks_like_bibliography(c["text"])]
        words = []
        for part in parts:
            following = part["text"].split()
            overlap = 0
            for size in range(min(len(words), len(following)), 0, -1):
                if words[-size:] == following[:size]:
                    overlap = size
                    break
            words.extend(following[overlap:])
        expanded.append({**source, "text": " ".join(words) or source["text"],
                         "anchor_text": source["text"],
                         "context_scope": "same-page expansion",
                         "context_chunks": len(parts)})
    return expanded


def conversation_search(question, query_smiles, index, history=(), top_k=6):
    """Chat is always structure-scoped, including empty and follow-up queries.

    Interleave current-question and contextual rankings to preserve both a new
    topic and antecedents from earlier turns. No whole-corpus text fallback.
    """
    _, block = to_inchikey(query_smiles)
    if not block:
        return [], None
    scoped = [c for c in index["chunks"] if block in c.get("blocks", [])
              and not looks_like_bibliography(c["text"])]
    if not scoped:
        return [], block
    current = bm25_search(question, scoped, top_k=top_k)
    # A complete new question should not compete with the preceding topic.
    followup = bool(re.search(
        r"\b(it|its|those|these|they|them|that|more|continue|above|former|latter)\b", question, re.I)
        or len(query_terms(question)) <= 4)
    contextual = (bm25_search(retrieval_query(question, history), scoped, top_k=top_k)
                  if history and followup else current)
    ordered = []
    seen = set()
    for pair in zip_longest(current, contextual):
        for row in pair:
            if row is None:
                continue
            marker = (row["source_doc"], row["page_number"], row["text"])
            if marker not in seen:
                seen.add(marker)
                ordered.append({**row, "route": "structure"})
    if not ordered:
        ordered, _ = structure_search(query_smiles, index, top_k=top_k)
    return expand_source_pages(ordered[:top_k], index), block


def split_answer_followups(text):
    """Separate optional model suggestions from cited answer text; fail softly."""
    body, separator, tail = text.partition("<followups>")
    if not separator:
        return text, []
    try:
        items = json.loads(tail.split("</followups>", 1)[0].strip())
    except (ValueError, TypeError):
        return body.strip(), []
    if not isinstance(items, list):
        return body.strip(), []
    questions = list(dict.fromkeys(q.strip() for q in items
                                  if isinstance(q, str) and 0 < len(q.strip()) <= 180))
    return body.strip(), questions[:3]


def hybrid_search(question, query_smiles, index, top_k=4):
    """Both routes, interleaved so neither can crowd the other out."""
    structure_hits, block = structure_search(query_smiles, index, question, top_k)
    text_hits = bm25_search(question, index["chunks"], top_k)

    # Taking structure first and then truncating made this structure-only
    # whenever the structure route filled top_k, which is almost always.
    ordered = []
    for pair in zip_longest(structure_hits, text_hits):
        ordered.extend(row for row in pair if row is not None)

    seen = set()
    merged = []
    for row in ordered:
        marker = (row["source_doc"], row["page_number"], row["text"][:60])
        if marker in seen:
            continue
        seen.add(marker)
        merged.append(row)
    return merged[:top_k], block


def documents_for_structure(query_smiles, index):
    """Which papers mention this structure, and under which names."""
    _, block = to_inchikey(query_smiles)
    if not block:
        return {}

    by_doc = {}
    for chunk in index["chunks"]:
        if block not in chunk.get("blocks", []):
            continue
        names = {
            name for name in chunk["compounds"]
            if index["compounds"][name]["block"] == block
        }
        by_doc.setdefault(chunk["source_doc"], set()).update(names)
    return {doc: sorted(names) for doc, names in by_doc.items()}


# --------------------------------------------------------------------------
# 7. Grounded answer
# --------------------------------------------------------------------------

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
MODEL_NAME = "deepseek/deepseek-v4-flash"


def build_context(sources, subject_block=None):
    blocks = []
    for index_number, source in enumerate(sources, start=1):
        if subject_block:
            on_subject = subject_block in source.get("blocks", [])
            flag = " | SUBJECT: " + ("yes" if on_subject else "NO - different molecule")
        else:
            flag = ""
        blocks.append(
            f"[Source {index_number}] {source['source_doc']} | page {source['page_number']} | "
            f"route: {source['route']} | compounds: {', '.join(source['compounds']) or 'none'}"
            f"{flag} | context: {source.get('context_scope', 'matched chunk')}\n{source['text']}"
        )
    return "\n\n".join(blocks)


SYSTEM_PROMPT = """
You are ChemKeyRAG, a structure-aware literature assistant.

Answer the question using only the context supplied with it. Cite every claim
with [Source N]. If the context does not contain the answer, say so plainly
rather than filling the gap - a refusal is a correct answer here.

Never invent a chemical identifier, a CAS Registry Number or a numeric value
that is not present in the context.

When the answer enumerates things - solvents, coformers, conditions, measured
values - present them as a Markdown table whose last column is `Source` holding
the [Source N] citations, with at most two lines of prose before it. When the
answer is a single fact, a comparison in words, or a refusal, use plain prose;
do not force a table.

These papers mix three very different kinds of result, and collapsing them is
the most misleading thing you can do. Whenever the context makes it clear, say
which kind each row is, in its own column:
  - measured in this work   (the authors' own new experiments)
  - from the literature     (values the authors collected from other papers)
  - predicted / simulated   (model or simulation output, not measurement)
Different papers in the corpus also cover different solvent sets. If a source
covers only part of the picture, say so rather than presenting its list as the
complete answer, and never imply a list is exhaustive when the context shows
other systems exist elsewhere.

If a plot is requested, report the available numerical data and source normally.
Do not emit code, ASCII charts, or claim to have rendered a chart. The application
can separately plot verified Table 1 solubility data from paper B. For other data,
state any evidence limitation without inventing numeric scores for qualitative findings.

After the answer, suggest up to three concise follow-up questions based on the
current discussion and supplied evidence, always about the pinned molecule.
Use explicit subjects/topics so each question is searchable. Do not assert that
unseen evidence exists. If there is no useful supported follow-up, use an empty
list. Append exactly this machine-readable block after all answer prose:
<followups>["Question one?", "Question two?"]</followups>
Do not put the block in a code fence. These are suggested next questions, not
questions that the user has already asked.

Earlier turns of the conversation are provided for reference only. Sources are
renumbered every turn, so cite only the context given with the current question.
"""

MAX_HISTORY_TURNS = 6


SUBJECT_PROMPT = """
The user is asking about ONE molecule, fixed by the structure they queried:

  subject: {label}
  InChIKey skeleton: {block}
  names this corpus uses for it: {names}

Every question in this conversation is pinned to that molecule. If the user asks
about a different molecule, ask them to change the selected structure first. A pronoun, "it", "this compound", or a bare question
with no compound named all refer to the subject.

The papers study several compounds side by side, so a single source - a table, a
sentence, a figure caption - often carries values for other molecules too. Report
only what belongs to the subject. Never give a value, solvent or property that
belongs to a different compound, and never merge them into one row. Page-expanded sources include neighbouring table rows and definitions. The
structure match applies to the anchor passage, not every row on that page.
Read the table caption, compound abbreviations, column headers and scale factors
before extracting numbers; never assign another compound's rows to the subject.
A source
marked "SUBJECT: NO" is about a different molecule: use it only for background,
never for a reported value.

If the context holds the answer for a different compound but not for the subject,
say that plainly instead of substituting the other compound's data.
"""


def ask_openrouter(question, sources, api_key, model=MODEL_NAME, history=(),
                   subject=None):
    """`history` is prior [{"role", "content"}] turns; sources are per-turn.

    `subject` pins the answer to one molecule:
    {"label": str, "block": str, "names": [str, ...]}.
    """
    if not sources:
        return "No chunk in the corpus matched this query, by name or by structure."

    subject_block = subject.get("block") if subject else None
    prompt = f"""Context:
{build_context(sources, subject_block)}

Question: {question}
"""

    system = SYSTEM_PROMPT
    if subject:
        system += SUBJECT_PROMPT.format(
            label=subject.get("label") or subject.get("block"),
            block=subject.get("block"),
            names=", ".join(subject.get("names") or []) or "none recorded",
        )

    messages = [{"role": "system", "content": system}]
    for turn in list(history)[-MAX_HISTORY_TURNS:]:
        messages.append({"role": turn["role"], "content": turn["content"]})
    messages.append({"role": "user", "content": prompt})

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "http://localhost:8501",
        "X-Title": "ChemKeyRAG",
    }
    payload = {"model": model, "messages": messages, "temperature": 0.2}

    for _ in range(3):
        response = requests.post(OPENROUTER_URL, headers=headers, json=payload, timeout=180)
        if response.status_code == 429:
            time.sleep(20)
            continue
        response.raise_for_status()
        return response.json()["choices"][0]["message"]["content"]

    response.raise_for_status()
