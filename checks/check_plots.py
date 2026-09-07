"""Verify source-derived charts and safe chat rendering; no API calls."""
import sys
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import chemkey as ck
import evidence_plots as plots
from streamlit.testing.v1 import AppTest
idx = ck.load_index('data/index.json')
hits, block = ck.conversation_search('solubility DMSO water', 'CC(=O)Nc1ccc(O)cc1', idx, top_k=6)
tables = plots.solubility_tables(hits, block)
assert [len(t['rows']) for t in tables] == [24,20,20]
assert '<svg' in plots.render_svg(tables[0])
assert tables[0]['rows'][0]['solubility_x100'] == .20
assert tables[0]['rows'][-1]['solubility_x100'] == 48.92
assert tables[0]['rows'][-1]['uncertainty_x100'] == .62
assert plots.solubility_tables(hits, 'unknown') == []
assert plots.solubility_tables([{**h,'text':h['text'][:250]} for h in hits], block) == []
assert plots.solubility_tables([{**h,'text':h['text'].replace('25 ◦C','26 ◦C')} for h in hits], block) == []
p_hits, p_block = ck.conversation_search('solubility 4FM water','CCOc1ccc(NC(C)=O)cc1', idx, top_k=6)
p_tables = plots.solubility_tables(p_hits,p_block)
assert len(p_tables) == 1 and p_tables[0]['rows'][0]['solubility_x100'] == .59
assert 'source_doc,page' in plots.table_csv(tables[0])
at = AppTest.from_file(str(Path(__file__).resolve().parents[1]/'streamlit_app.py')).run(timeout=30)
next(t for t in at.text_input if t.label == 'OpenRouter API key').set_value('test').run(timeout=30)
with patch.object(ck,'ask_openrouter',return_value='Measurements [Source 1]'):
    at.chat_input[0].set_value('Plot solubility in DMSO and water.').run(timeout=30)
assert not at.exception
assert len(at.session_state['chat'][-1]['plots']) == 3
assert any(e.label == 'Plot source data' for e in at.expander)
next(r for r in at.radio if r.label == 'Horizontal axis').set_value('Temperature').run(timeout=30)
assert not at.exception and len(at.session_state['chat']) == 2
import streamlit as st
st.cache_data.clear()
with patch.object(plots, 'render_svg', side_effect=RuntimeError('test rendering failure')):
    at.run(timeout=30)
    assert not at.exception
    assert any('plot could not' in i.value for i in at.info)
assert at.session_state['chat'][-1]['content'] == 'Measurements [Source 1]'
print('PASS: verified values, subject isolation, incomplete-table rejection, chart controls, CSV, graceful rendering failure')
