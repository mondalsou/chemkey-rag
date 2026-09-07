"""Run from project root: python checks/check_chat.py (no API calls)."""
import sys
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import chemkey as ck
from streamlit.testing.v1 import AppTest
idx = ck.load_index('data/index.json')
smiles = 'CC(=O)Nc1ccc(O)cc1'
_, block = ck.to_inchikey(smiles)
history = [
    {'role': 'user', 'content': 'Which coformers make cocrystals?'},
    {'role': 'assistant', 'content': 'Trimethylglycine is discussed [Source 1].'},
    {'role': 'user', 'content': 'What holds the layers together?'},
    {'role': 'assistant', 'content': 'Hydrogen bonding connects the layers [Source 2].'},
]
query = ck.retrieval_query('Tell me more about those.', history)
assert 'coformers' in query and 'Hydrogen bonding' in query
for question in ['Which coformers?', 'Tell me more about those.', 'phenacetin solubility']:
    hits, found = ck.conversation_search(question, smiles, idx, history, top_k=8)
    assert hits and found == block
    assert all(block in row['blocks'] for row in hits)
    assert all(not ck.looks_like_bibliography(row['text']) for row in hits)
assert ck.conversation_search('acetaminophen solubility', 'NC(=O)N1c2ccccc2C=Cc2ccccc21', idx)[0] == []
assert ck.split_answer_followups('Answer <followups>["Next?", "Next?"]</followups>') == ('Answer', ['Next?'])
assert ck.split_answer_followups('Answer <followups>invalid</followups>') == ('Answer', [])
at = AppTest.from_file(str(Path(__file__).resolve().parents[1] / 'streamlit_app.py')).run()
next(t for t in at.text_input if t.label == 'OpenRouter API key').set_value('test').run()
reply = 'Cocrystal evidence [Source 1].<followups>["How do hydrogen bonds connect the cocrystal layers?"]</followups>'
with patch.object(ck, 'ask_openrouter', return_value=reply) as model:
    at.chat_input[0].set_value('Which coformers are used?').run()
    assert not at.exception
    assert len(at.session_state['chat']) == 2
    next(b for b in at.button if b.label == 'How do hydrogen bonds connect the cocrystal layers?').click().run()
    assert not at.exception and len(at.session_state['chat']) == 4
    assert model.call_args.args[0] == 'How do hydrogen bonds connect the cocrystal layers?'
    assert len(model.call_args.kwargs['history']) == 2
    assert all(block in row['blocks'] for row in model.call_args.args[1])
    assert '<followups>' not in at.session_state['chat'][1]['content']
at.selectbox[0].select('Caffeine').run()
assert at.session_state['chat'] == []
print('PASS: structure-only sources, empty structure, multi-turn context, follow-up parsing, clickable continuation, reset')
