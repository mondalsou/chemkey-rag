"""Conservative chart adapter for the verified Table 1 in paper B.

Parse original retrieved text, never LLM prose or executable model output.
Unknown layouts fail closed. Extend with a new verified parser for other tables.
"""
import csv
import io
import re

# Verified identities and table abbreviations, matching the table caption.
SUBJECTS = {'RZVAJINKPMORJF': ('A', 'Paracetamol')}
HEADER = re.compile(r'\b([AP])\s*\+\s*(DMSO|DMF|4FM)\s*\+\s*water\s*\(x[AapP]\s*×\s*102\)')
NUMBER = r'(\d+\.\d+)'
PAIR = NUMBER + r'\s*±\s*' + NUMBER
ROW = re.compile(r'(?<![\d.])' + NUMBER + r'\s+' + r'\s+'.join([PAIR]*4) + r'(?![\d.])')


def solubility_tables(sources, block):
    """Return complete verified-layout datasets for this molecule, or []."""
    # Compute phenacetin identity with the same canonicalizer used at ingest.
    from chemkey import to_inchikey
    subjects = {**SUBJECTS, to_inchikey('CCOc1ccc(NC(C)=O)cc1')[1]: ('P', 'Phenacetin')}
    if not block or block not in subjects:
        return []
    symbol, label = subjects[block]
    tables = []
    seen = set()
    for number, source in enumerate(sources, 1):
        if (source.get('source_doc') != 'B_acetaminophen_solubility.pdf'
                or source.get('page_number') != 10 or block not in source.get('blocks', [])):
            continue
        text = source['text']
        if not all(token in text for token in ['Table 1.', 'acetaminophen (A)', 'phenacetin (P)']):
            continue
        if not re.search(r'25\s*◦C\s*30\s*◦C\s*35\s*◦C\s*40\s*◦C', text):
            continue
        headers = list(HEADER.finditer(text))
        for i, header in enumerate(headers):
            compound, solvent = header.groups()
            if compound != symbol or solvent in seen:
                continue
            section = text[header.end():headers[i+1].start() if i+1 < len(headers) else len(text)]
            rows = []
            for match in ROW.finditer(section):
                values = list(map(float, match.groups()))
                composition = values[0]
                for offset, temperature in enumerate([25, 30, 35, 40]):
                    value, uncertainty = values[1+2*offset:3+2*offset]
                    rows.append({'organic_fraction': composition, 'temperature_C': temperature,
                                 'solubility_x100': value, 'uncertainty_x100': uncertainty,
                                 'lower': value-uncertainty, 'upper': value+uncertainty})
            expected = [0.0, .2, .4, .6, .8, 1.0] if solvent == 'DMSO' else [.2, .4, .6, .8, 1.0]
            if [r['organic_fraction'] for r in rows[::4]] != expected:
                continue  # partial/duplicate/malformed table: don't draw it
            if any(r['uncertainty_x100'] < 0 or r['solubility_x100'] < 0 for r in rows):
                continue
            seen.add(solvent)
            tables.append({'title': f'{label} · {solvent} + water', 'solvent': solvent,
                           'source': number, 'source_doc': source['source_doc'], 'page': 10,
                           'rows': rows})
    return tables


def table_csv(table):
    stream = io.StringIO()
    fields = ['organic_fraction','temperature_C','solubility_x100','uncertainty_x100']
    writer = csv.DictWriter(stream, fieldnames=fields + ['source_doc','page'], extrasaction='ignore')
    writer.writeheader()
    for row in table['rows']:
        writer.writerow({**row, 'source_doc':table['source_doc'], 'page':table['page']})
    return stream.getvalue()


def render_svg(table, axis="Composition"):
    """Render in an isolated process; native library issues cannot hang the UI."""
    import json
    import subprocess
    import sys
    from pathlib import Path
    result = subprocess.run([sys.executable, str(Path(__file__).resolve())],
                            input=json.dumps({"table": table, "axis": axis}),
                            text=True, capture_output=True, timeout=20, check=True)
    return result.stdout


if __name__ == "__main__":
    import json
    import sys
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    payload = json.load(sys.stdin)
    table, axis = payload["table"], payload["axis"]
    composition = axis == "Composition"
    x = "organic_fraction" if composition else "temperature_C"
    group = "temperature_C" if composition else "organic_fraction"
    fig, ax = plt.subplots(figsize=(9, 4.5), layout="constrained")
    fig.patch.set_facecolor("#fffdf7")
    ax.set_facecolor("#fffdf7")
    colors = ["#c2317a", "#1d3a45", "#4e8f9c", "#c98a3c", "#7a6aa8", "#3f8f6b"]
    for color, value in zip(colors, sorted({r[group] for r in table["rows"]})):
        points = sorted([r for r in table["rows"] if r[group] == value], key=lambda r: r[x])
        ax.errorbar([r[x] for r in points], [r["solubility_x100"] for r in points],
                    yerr=[r["uncertainty_x100"] for r in points],
                    label=f"{value:g} °C" if composition else f"x = {value:g}",
                    color=color, marker="o", markersize=4, linewidth=1.4, capsize=3)
    ax.set_xlabel("Organic solvent mole fraction" if composition else "Temperature (°C)", color="#41606b")
    ax.set_ylabel("Solubility mole fraction × 100", color="#41606b")
    ax.set_title(table["title"], color="#1d3a45", loc="left", pad=16)
    ax.tick_params(colors="#7f959d")
    ax.grid(alpha=.12)
    for spine in ax.spines.values():
        spine.set_color("#e7dac0")
    ax.legend(frameon=False, labelcolor="#41606b", fontsize=9, ncol=2)
    ax.set_ylim(bottom=0)
    fig.savefig(sys.stdout, format="svg")
    plt.close(fig)
