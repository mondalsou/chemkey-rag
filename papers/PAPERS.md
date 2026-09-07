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
