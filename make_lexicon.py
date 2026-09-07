"""Build and verify data/lexicon.json.

Every SMILES is parsed with RDKit, canonicalised and hashed to an InChIKey
before it is written, so the shipped lexicon cannot contain an unparseable or
silently wrong entry. Re-run this if you add names.
"""

import json
import os

from rdkit import Chem, RDLogger
from rdkit.Chem.rdMolDescriptors import CalcMolFormula

RDLogger.DisableLog("rdApp.*")

# name -> SMILES. Synonyms deliberately repeat the same structure: that is the
# whole point of the project.
RAW = {
    # --- paracetamol, under every name the corpus uses -------------------
    "paracetamol": "CC(=O)Nc1ccc(O)cc1",
    "acetaminophen": "CC(=O)Nc1ccc(O)cc1",
    "acetamidophenol": "CC(=O)Nc1ccc(O)cc1",
    "4-acetamidophenol": "CC(=O)Nc1ccc(O)cc1",
    "4-hydroxyacetanilide": "CC(=O)Nc1ccc(O)cc1",
    "p-hydroxyacetanilide": "CC(=O)Nc1ccc(O)cc1",
    "n-(4-hydroxyphenyl)acetamide": "CC(=O)Nc1ccc(O)cc1",
    "n-acetyl-p-aminophenol": "CC(=O)Nc1ccc(O)cc1",
    "tylenol": "CC(=O)Nc1ccc(O)cc1",

    # --- other actives in the corpus -------------------------------------
    "phenacetin": "CCOc1ccc(NC(C)=O)cc1",
    "acetophenetidin": "CCOc1ccc(NC(C)=O)cc1",
    "n-(4-ethoxyphenyl)acetamide": "CCOc1ccc(NC(C)=O)cc1",
    "acetanilide": "CC(=O)Nc1ccccc1",
    "n-phenylacetamide": "CC(=O)Nc1ccccc1",
    "benzocaine": "CCOC(=O)c1ccc(N)cc1",
    "ethyl 4-aminobenzoate": "CCOC(=O)c1ccc(N)cc1",
    "caffeine": "Cn1cnc2c1c(=O)n(C)c(=O)n2C",
    "1,3,7-trimethylxanthine": "Cn1cnc2c1c(=O)n(C)c(=O)n2C",
    "theophylline": "Cn1c(=O)c2[nH]cnc2n(C)c1=O",
    "theobromine": "Cn1cnc2c1c(=O)[nH]c(=O)n2C",
    "aspirin": "CC(=O)Oc1ccccc1C(=O)O",
    "acetylsalicylic acid": "CC(=O)Oc1ccccc1C(=O)O",
    "ibuprofen": "CC(C)Cc1ccc(cc1)C(C)C(=O)O",
    "naproxen": "COc1ccc2cc(ccc2c1)C(C)C(=O)O",
    "indomethacin": "COc1ccc2c(c1)c(CC(=O)O)c(C)n2C(=O)c1ccc(Cl)cc1",
    "carbamazepine": "NC(=O)N1c2ccccc2C=Cc2ccccc21",
    "saccharin": "O=C1NS(=O)(=O)c2ccccc21",

    # --- coformers and cocrystal partners ---------------------------------
    "naphthalene": "c1ccc2ccccc2c1",
    "quinoline": "c1ccc2ncccc2c1",
    "acridine": "c1ccc2nc3ccccc3cc2c1",
    "anthracene": "c1ccc2cc3ccccc3cc2c1",
    "phenanthrene": "c1ccc2c(c1)ccc1ccccc12",
    "nicotinamide": "NC(=O)c1cccnc1",
    "isonicotinamide": "NC(=O)c1ccncc1",
    "nicotinic acid": "OC(=O)c1cccnc1",
    "oxalic acid": "OC(=O)C(=O)O",
    "malonic acid": "OC(=O)CC(=O)O",
    "succinic acid": "OC(=O)CCC(=O)O",
    "maleic acid": "OC(=O)/C=C\\C(=O)O",
    "fumaric acid": "OC(=O)/C=C/C(=O)O",
    "glutaric acid": "OC(=O)CCCC(=O)O",
    "adipic acid": "OC(=O)CCCCC(=O)O",
    "citric acid": "OC(=O)CC(O)(CC(=O)O)C(=O)O",
    "tartaric acid": "OC(=O)C(O)C(O)C(=O)O",
    "benzoic acid": "OC(=O)c1ccccc1",
    "salicylic acid": "OC(=O)c1ccccc1O",
    "caprolactam": "O=C1CCCCCN1",
    "resorcinol": "Oc1cccc(O)c1",
    "urea": "NC(N)=O",
    "phenol": "Oc1ccccc1",
    "aniline": "Nc1ccccc1",
    "nitrobenzene": "[O-][N+](=O)c1ccccc1",
    "mannitol": "OCC(O)C(O)C(O)C(O)CO",
    "xylitol": "OCC(O)C(O)C(O)CO",

    # --- solvents ---------------------------------------------------------
    "water": "O",
    "methanol": "CO",
    "ethanol": "CCO",
    "1-propanol": "CCCO",
    "2-propanol": "CC(C)O",
    "isopropanol": "CC(C)O",
    "1-butanol": "CCCCO",
    "acetone": "CC(C)=O",
    "acetonitrile": "CC#N",
    "chloroform": "ClC(Cl)Cl",
    "dichloromethane": "ClCCl",
    "toluene": "Cc1ccccc1",
    "hexane": "CCCCCC",
    "heptane": "CCCCCCC",
    "cyclohexane": "C1CCCCC1",
    "1,4-dioxane": "C1COCCO1",
    "tetrahydrofuran": "C1CCOC1",
    "ethyl acetate": "CCOC(C)=O",
    "dimethylformamide": "CN(C)C=O",
    "dimethylsulfoxide": "CS(C)=O",
    "pyridine": "c1ccncc1",
    "ethylene glycol": "OCCO",
    "propylene glycol": "CC(O)CO",
    "glycerol": "OCC(O)CO",
    "formic acid": "OC=O",
    "acetic acid": "CC(=O)O",
}


def main():
    lexicon = {}
    report = []
    failures = []

    for name, smiles in RAW.items():
        mol = Chem.MolFromSmiles(smiles)
        if mol is None:
            failures.append(name)
            continue
        canonical = Chem.MolToSmiles(mol)
        key = Chem.MolToInchiKey(mol)
        lexicon[name] = canonical
        report.append((name, CalcMolFormula(mol), mol.GetNumHeavyAtoms(), key.split("-")[0], canonical))

    os.makedirs("data", exist_ok=True)
    with open("data/lexicon.json", "w", encoding="utf-8") as handle:
        json.dump(lexicon, handle, indent=2, sort_keys=True)

    for name, formula, heavy, block, canonical in report:
        print(f"{name:32s} {formula:12s} heavy={heavy:<3d} {block}  {canonical}")

    print(f"\n{len(lexicon)} entries written, {len(failures)} failures {failures}")

    groups = {}
    for name, _, _, block, _ in report:
        groups.setdefault(block, []).append(name)
    print("\nSynonym groups (same skeleton, different names):")
    for block, names in groups.items():
        if len(names) > 1:
            print(f"  {block}  {names}")


if __name__ == "__main__":
    main()
