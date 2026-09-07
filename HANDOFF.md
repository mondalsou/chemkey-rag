# ChemKeyRAG — session handoff

**2026-09-07 update:** the index was rebuilt after known-name hyphen recovery.
Current totals: 246 chunks, 159 annotated, 80 names. Paracetamol coverage is
133 structure / 133 spellings / 0 structure-only / 0 text-only. Caffeine is
10 structure / 7 exact name / 3 structure-only / 0 text-only. Earlier tables
below describe the pre-fix snapshot. Git setup remains with the user.


State as of 2026-09-05. Written so this project can be picked up cold, from any
harness, without replaying the conversation that produced it.

## Where things stand

Working end to end: corpus downloaded, index built, Streamlit app running with a
grounded chat. Everything below was verified by running it, not by inspection.

| | |
|---|---|
| Environment | conda env `chemkey-rag` (python 3.11.16, rdkit 2026.03.5, streamlit 1.63.0, pypdf 6.7.4) |
| Jupyter kernel | registered as `Python (chemkey-rag)` |
| Corpus | 3 PDFs in `papers/` (gitignored) |
| Index | 246 chunks, 158 carry a structure, 78 names, 65 skeletons, 9 synonym groups |
| Name cache | `data/name_cache.json`, 319 entries — delete only if you want to re-hit PubChem |
| LLM | `deepseek/deepseek-v4-flash` via OpenRouter, key in `.env` (gitignored) |

## Rebuild from scratch

```bash
conda env create -f environment.yml     # or: conda activate chemkey-rag
python build_index.py                   # online; --offline for lexicon only
streamlit run streamlit_app.py          # http://localhost:8501
```

The PDFs are not redistributed. PMC blocks scripted download with a
proof-of-work challenge; **Europe PMC's open-access endpoint works**:

```bash
curl -sL -A "Mozilla/5.0" --retry 6 --retry-delay 25 --retry-all-errors \
  -o papers/A_paracetamol_cocrystals.pdf \
  "https://europepmc.org/articles/PMC11434482?pdf=render"
# B = PMC9781932, C = PMC4312346  (see papers/PAPERS.md)
```

Verify with `pypdf` that page 1 text matches the expected title before indexing —
a rate-limited request returns a 20-byte text file, not a PDF.

## What the demo actually proves

Measured, not asserted. `structure adds` = chunks the structure route finds that
**no** name string can.

| compound | structure | best single name | all spellings OR'd | only structure |
|---|---|---|---|---|
| Paracetamol | 132 | 83 | 133 | 0 |
| Phenacetin | 48 | 47 | 48 | 0 |
| Caffeine | 9 | 7 | 7 | **3** |
| Benzocaine | 8 | 8 | 8 | 0 |

Two distinct claims, and they are not equally strong:

1. **Query-side invariance (strong, everywhere).** Type `4-hydroxyacetanilide`
   and text search returns **0** chunks while structure returns **132**. Same for
   `tylenol`, `4-acetamidophenol`. You do not need to know the synonyms.
2. **Beyond any name (narrow, real).** Only caffeine, +3 chunks. The PDF split
   the word as `ca ffeine`, so no string search can reach them. This is the only
   place the structure route exceeds a perfect synonym list.

**No chunk in this corpus uses an IUPAC name alone** — each appears as a gloss
beside the trivial name ("Acetaminophen (N-(4-hydroxyphenyl)acetamide, CAS:
103-90-2), also known under the name paracetamol"). The corpus-side version of
the claim needs a fourth paper that only ever writes the systematic name.

## Bugs found and fixed (all had a verifying check)

| # | Bug | Fix |
|---|---|---|
| 1 | PDFs split names across lines (`parace-\ntamol`) — molecule lost | `repair_page_text()` rejoins, folds unicode hyphens |
| 2 | Stray intra-word spaces (`ca ffeine`) unreachable | lexicon-gated token rejoin in `candidate_names(text, vocab=)` |
| 3 | `resolve()` case-sensitive — `Paracetamol` returned `None` | `.strip().lower()` at the single choke point |
| 4 | `--max-candidates 300` silently dropped 47 of 347 | default raised to 500 |
| 5 | Nothing called `load_dotenv()` — `.env` was inert | called in `streamlit_app.py` |
| 6 | Sidebar key widget kept its first empty value | `api_key = typed_key or env_key` |
| 7 | RDKit SVG XML prolog rendered as stray text | stripped in `depict()` |
| 8 | **BM25 ranked question boilerplate over content** (`compound` idf 3.97 vs `solubility` 0.95) | `query_terms()` drops stopwords + question words |
| 9 | **`hybrid_search` was structure-only** — structure filled `top_k`, text never appeared | interleave both routes |
| 10 | **Bibliographies dominated retrieval** — keyword magnets with no data | `looks_like_bibliography()` excludes 32/246 chunks |
| 11 | **Answers drifted to other molecules** — paper B studies acetaminophen + phenacetin side by side | `subject=` pins label/skeleton/names; sources tagged `SUBJECT: yes/NO` |
| 12 | **Retrieval had no memory** — follow-up "Only DMSO+water?" lost the topic | `retrieval_query()` prepends the previous user turn |

Bug 12 is the pattern to watch: the chat had memory, the retrieval underneath
did not. Same shape as bug 11.

## Ground truth worth keeping (checked against the PDFs)

Solvent systems for paracetamol are **three different categories** — collapsing
them is the main way answers mislead:

- **Measured in paper B:** DMF, DMSO, 4FM neat; DMSO+water, DMF+water, 4FM+water binaries
- **Collected from literature (paper B):** ~20 neat solvents (water, methanol, ethanol, propanols, butanol, octanol, propylene glycol, transcutol, ethyl acetate, acetone, acetonitrile, 1,4-dioxane, hexane, cyclohexane, chloroform…) and ~10 binaries including ethanol+water
- **Predicted only (paper B):** ethyltriglycol, methyltriglycol, 2-pyridin-2-ylethanol
- **Simulated only (paper C):** water/ethanol binaries — that paper's *entire* scope

Table 1 (paper B p10) holds four systems: `A+DMSO+water`, `A+DMF+water`,
`A+4FM+water`, `P+4FM+water`, each at 25/30/35/40 °C. Values verified against
raw PDF text. There is **no** numeric ethanol/water value for paracetamol in this
corpus — a refusal there is correct.

## Known limits (deliberate, not oversights)

- **710 embedded images never read** (paper A alone has 692). A molecule that is
  only drawn contributes nothing. Biggest untapped source; needs DECIMER/MolScribe.
- Enumeration answers can still be incomplete when a list straddles a chunk
  boundary (p9→p10). Fix would be neighbour expansion in `hybrid_search`.
- One reference chunk on B p19 escapes the bibliography filter — starts
  mid-citation. Tightening the threshold started eating body text.
- The corpus favours common names, so text search is a genuinely strong baseline.

## Next steps, in order of value

1. **Neighbour expansion** — pull the adjacent chunk of a retrieved hit so split
   lists come back whole. Small change in `hybrid_search`.
2. **A fourth paper** that names paracetamol only systematically, to demonstrate
   the corpus-side claim rather than only the query-side one.
3. **Structure extraction from figures** (DECIMER/MolScribe) over those 710 images.
4. Trained chemical NER to replace the lexical candidate pass.

## Conventions this project follows

- Every non-trivial change leaves one runnable check (asserts in a heredoc, no
  test framework). Retrieval claims are verified by measurement, not assertion.
- Root-cause fixes at the single choke point, not per-caller patches.
- Refusal is a correct answer; the prompt forbids inventing identifiers or values.

## Chat correction — 2026-09-06

Chat now uses `conversation_search`, which restricts every source to the selected
connectivity block before ranking. The general `hybrid_search` remains available
for mixed-route comparisons, but is no longer used for molecule-pinned chat.
Retrieval carries up to three user questions and, for short follow-ups, a bounded
excerpt of the latest answer for referents. That excerpt is a search hint, never
evidence. Current and contextual rankings are interleaved within structure hits.
DeepSeek returns an optional `<followups>` JSON list; the UI separates it from
answer prose and renders buttons that continue the existing conversation.
`python checks/check_chat.py` checks structure scoping, absent molecules, context,
parsing, and actual follow-up button interaction without API calls.
Typography now uses Plus Jakarta Sans headings and DM Sans body text.

## Solubility table retrieval fix — 2026-09-06

Reproduced failure after coformer history: six slots included Table 1's caption
but not chunk 138 containing DMSO/water values. Self-contained questions now use
current-question ranking; short/referential follow-ups retain contextual ranking.
Chat expands each structure-matched anchor to its same-page indexed text and
deduplicates pages. This preserves captions, numeric continuations, compound
abbreviations and units. Continuation chunks need not contain a chemical name.
Anchor text and expansion metadata remain attached; identity applies to the
anchor, not every table row. The prompt explicitly distinguishes these.
Run `python checks/check_solubility.py` for PDF-grounded values at source limits
2, 4, 6, and 8 following a coformer conversation.
