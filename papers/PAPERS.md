# Corpus

Three open-access papers. They are **not** committed to this repo — download the
PDFs yourself into this folder, then run `python build_index.py`.

The set is chosen for one reason: **all three study the same molecule, and none of
them agree on what to call it.**

| # | Save as | Paper | Calls the compound |
|---|---------|-------|--------------------|
| 1 | `A_paracetamol_cocrystals.pdf` | *Driving Forces in the Formation of Paracetamol Cocrystals and Solvate with Naphthalene, Quinoline and Acridine* — [PMC11434482](https://pmc.ncbi.nlm.nih.gov/articles/PMC11434482/) | **paracetamol**, 4-hydroxyacetanilide |
| 2 | `B_acetaminophen_solubility.pdf` | *Solubility Characteristics of Acetaminophen and Phenacetin in Binary Mixtures of Aqueous Organic Solvents: Experimental and Deep Machine Learning Screening of Green Dissolution Media* — [PMC9781932](https://pmc.ncbi.nlm.nih.gov/articles/PMC9781932/) | **acetaminophen** |
| 3 | `C_excess_solubility_simulation.pdf` | *Predicting the Excess Solubility of Acetanilide, Acetaminophen, Phenacetin, Benzocaine and Caffeine in Binary Water/Ethanol Mixtures via Molecular Simulation* — [PMC4312346](https://pmc.ncbi.nlm.nih.gov/articles/PMC4312346/) | **acetaminophen** |

Optional fourth, if you want a second compound family that appears in only one
paper (a clean negative control for the structure query):

- *Virtual Screening, Structural Analysis, and Formation Thermodynamics of
  Carbamazepine Cocrystals* — [PMC10052035](https://pmc.ncbi.nlm.nih.gov/articles/PMC10052035/)

## Scan-like fixture for the image-table pipeline

`--image-tables` needs a page with no text layer. The corpus has none, so build
one from a paper you already have:

```bash
# Shende et al., Drug Stability Analysis by Raman Spectroscopy,
# Pharmaceutics 2014 (CC BY) — PMC4279130. Save it, then:
python scripts/make_scanned_table_fixture.py \
  --source <that paper>.pdf --page 7 --clip 60,335,540,590 \
  --out papers/D_raman_table1_scan.pdf
python build_index.py --image-tables
```

That rasterises its Table 1 (prepared versus calculated percentages of
p-aminophenol mixed with acetaminophen) into a one-page image-only PDF. It is
**scan-like, not an original scanner capture**: no text layer and one embedded
raster, but none of a real scan's noise, skew, or bleed-through. Report numbers
measured on it with that caveat attached.

## Why this set works

Search the corpus for `paracetamol` and you get paper 1.
Search for `acetaminophen` and you get papers 2 and 3.
Search for `tylenol` and you get nothing.

Draw the structure and you get all three — because at ingest every one of those
names was resolved to the same InChIKey skeleton, `RZVAJINKPMORJF`.

Phenacetin behaves the same way (`phenacetin` / `acetophenetidin` /
`N-(4-ethoxyphenyl)acetamide`), and caffeine appears as both `caffeine` and
`1,3,7-trimethylxanthine`.

## Before you download

Check each paper's licence on its landing page. These are open access, but the
PDFs stay out of git regardless — `papers/*.pdf` is in `.gitignore`. Ship the
loader, not the corpus.
