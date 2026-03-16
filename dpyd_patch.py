import re, importlib.util

DPYD_ADDITIONS = {
    "*2A/*2A": "Poor Metabolizer",
    "*2A/*13": "Poor Metabolizer",
    "*13/*13": "Poor Metabolizer",
    "*2A/*1":  "Intermediate Metabolizer",
    "*13/*1":  "Intermediate Metabolizer",
    "*1/*2A":  "Intermediate Metabolizer",
    "*1/*13":  "Intermediate Metabolizer",
    "*2A/*HapB3":   "Poor Metabolizer",
    "*HapB3/*HapB3":"Intermediate Metabolizer",
    "*1/*HapB3":    "Intermediate Metabolizer",
    "*1/*1":   "Normal Metabolizer",
}

parser_path = "agents/genotype_parser.py"
with open(parser_path) as f:
    src = f.read()

pattern = r'("DPYD"\s*:\s*\{)(.*?)(\s*\},)'

def inject(m):
    existing = m.group(2)
    new_entries = "\n".join(
        f'        "{dip}": "{phen}",'
        for dip, phen in DPYD_ADDITIONS.items()
        if f'"{dip}"' not in existing
    )
    if not new_entries:
        return m.group(0)
    return m.group(1) + existing + "\n" + new_entries + m.group(3)

new_src, n = re.subn(pattern, inject, src, flags=re.DOTALL)
if n == 0:
    dpyd_entries = "\n".join(f'        "{d}": "{p}",' for d, p in DPYD_ADDITIONS.items())
    insert = f'\n    "DPYD": {{\n{dpyd_entries}\n    }},\n'
    new_src = src.replace("PHENOTYPE_MAP = {", "PHENOTYPE_MAP = {" + insert)
    print("DPYD block not found — added new block")
else:
    print(f"Injected into existing DPYD block")

with open(parser_path, "w") as f:
    f.write(new_src)

# Verify without triggering __main__
spec = importlib.util.spec_from_file_location("gp", parser_path)
gp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gp)
PM = gp.PHENOTYPE_MAP
print("CYP2D6  *4/*4  :", PM.get("CYP2D6",{}).get("*4/*4","MISSING"))
print("CYP2C19 *2/*2  :", PM.get("CYP2C19",{}).get("*2/*2","MISSING"))
print("TPMT    *3A/*3A:", PM.get("TPMT",{}).get("*3A/*3A","MISSING"))
print("DPYD    *2A/*2A:", PM.get("DPYD",{}).get("*2A/*2A","MISSING"))
print("DPYD    *1/*1  :", PM.get("DPYD",{}).get("*1/*1","MISSING"))
print("DPYD    *1/*2A :", PM.get("DPYD",{}).get("*1/*2A","MISSING"))
