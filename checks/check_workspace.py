"""Run from the project root: python checks/check_workspace.py."""
import sys
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import chemkey as ck
from streamlit.testing.v1 import AppTest

at = AppTest.from_file(str(Path(__file__).resolve().parents[1] / 'streamlit_app.py')).run(timeout=30)
assert not at.exception
assert at.metric[0].value == '133'
at.text_input[0].set_value('caffeine').run()
assert at.metric[0].value == '10'
assert next(m for m in at.metric if m.label == 'Only structure finds').value == '3'  # structure-only coverage vs all indexed spellings
at.text_input[0].set_value('unknown_not_a_molecule').run()
assert not at.exception and not at.metric
at.selectbox[0].select('Phenacetin').run()
assert at.metric[0].value == '48'
at.radio[0].set_value('SMILES').run()
assert not at.exception
smiles = next(w for w in at.text_input if w.label == 'SMILES')
smiles.set_value('invalid').run()
assert at.error and not at.metric
at.radio[0].set_value('Chemical name').run()
at.multiselect[0].set_value(['B_acetaminophen_solubility.pdf']).run()
assert not at.exception
# Exercise chat composition without a paid external API request.
next(w for w in at.text_input if w.label == 'OpenRouter API key').set_value('test').run()
with patch.object(ck, 'ask_openrouter', return_value='Test answer [Source 1]') as answer:
    at.chat_input[0].set_value('What solubility values are measured?').run()
    assert not at.exception
    assert answer.call_args.kwargs['subject']['label'] == 'phenacetin'
    assert answer.call_args.args[1]
at.selectbox[0].select('Caffeine').run()
assert at.session_state['chat'] == []
print('PASS: coverage, resolved identity, unknown/invalid input, filtering, chat context, conversation reset')
