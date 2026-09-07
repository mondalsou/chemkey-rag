"""Regression: coformer history must not hide Table 1's values."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pypdf import PdfReader
import chemkey as ck
idx = ck.load_index('data/index.json')
smi = 'CC(=O)Nc1ccc(O)cc1'
question = 'What are the measured solubility values in DMSO + water mixtures at different temperatures?'
history = [
    {'role':'user','content':'Which coformers were used to make cocrystals, and what holds the layers together?'},
    {'role':'assistant','content':'Paracetamol forms cocrystals with naphthalene, quinoline and acridine. Hydrogen bonds hold the layers together.'},
]
raw = PdfReader('papers/B_acetaminophen_solubility.pdf').pages[9].extract_text()
for count in [2, 4, 6, 8]:
    hits, block = ck.conversation_search(question, smi, idx, history, top_k=count)
    table = next(s for s in hits if s['source_doc'].startswith('B_') and s['page_number'] == 10)
    for value in ['0.20', '5.17', '13.66', '26.47', '51.08', '48.92']:
        assert value in raw and value in table['text']
    assert '25 ◦C 30 ◦C 35 ◦C 40 ◦C' in table['text']
    assert 'A + DMSO + water (xA × 102)' in table['text']
    assert 'P + 4FM + water' in table['text']  # separate compound labels retained
    assert all(block in s['blocks'] and s.get('anchor_text') for s in hits)
    assert len({(s['source_doc'], s['page_number']) for s in hits}) == len(hits)
    assert not any(s['source_doc'].startswith('A_') for s in hits[:2])
print('PASS: PDF-verified table values, units, temperatures, compound labels, history and source limits 2/4/6/8')
