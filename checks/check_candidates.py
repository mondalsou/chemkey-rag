"""Run: python checks/check_candidates.py. No network or index mutation."""
import inspect
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import chemkey as ck
vocab=ck.NameResolver(online=False).lexicon
assert inspect.signature(ck.build_index).parameters['max_candidates'].default == 500
for text in ['Paracetamol-Oxalic Acid', 'Acid-Paracetamol', 'paracetamol-based']:
    assert 'paracetamol' in ck.candidate_names(text,vocab=vocab)
assert 'caffeine' in ck.candidate_names('caffeine-containing and ca ffeine',vocab=vocab)
for name in ['103-90-2','n-phenylethanamide','4-hydroxyacetanilide']:
    assert name in ck.candidate_names(name,vocab=vocab)
assert 'paracetamol' not in ck.candidate_names('notparacetamol-unknown',vocab=vocab)
assert not ck.candidate_names('a well-known ordinary phrase',vocab=vocab)
print('PASS: aligned candidate limit; known-name hyphen recovery; intact CAS/systematic names; no substring invention')
