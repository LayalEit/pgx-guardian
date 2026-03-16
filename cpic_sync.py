"""
cpic_sync.py — downloads CPIC diplotype data from their GitHub release TSV files
(no API pagination issues) and updates:
  1. agents/genotype_parser.py  (PHENOTYPE_MAP)
  2. Supabase cpic_cache

Run once from project root: python3 cpic_sync.py
"""
import os, sys, json, re, csv, io, requests
sys.path.insert(0, ".")
from dotenv import load_dotenv
load_dotenv(dotenv_path=".env")

CPIC_BASE = "https://api.cpicpgx.org/v1"

TARGET_GENES = [
    "CYP2D6", "CYP2C19", "CYP2C9", "CYP3A5", "CYP3A4",
    "CYP2B6", "CYP1A2", "DPYD", "TPMT", "NUDT15",
    "SLCO1B1", "UGT1A1", "VKORC1", "HLA-B", "HLA-A",
    "G6PD", "RYR1", "CACNA1S", "IFNL3", "NAT2",
]

def fetch_json(url, params=None):
    r = requests.get(url, params=params, timeout=30)
    r.raise_for_status()
    return r.json()

# ── Step 1: Build PHENOTYPE_MAP from CPIC TSV files on GitHub ────────────────
# CPIC publishes per-gene diplotype-phenotype TSV files in their releases
# These are the authoritative source with ALL diplotypes, no row limits

print("Fetching diplotype→phenotype mappings from CPIC GitHub release files...")
phenotype_map = {}

# The CPIC GitHub releases contain per-gene TSV files
# Base URL for latest release TSV files
GITHUB_TSV_BASE = "https://raw.githubusercontent.com/cpicpgx/cpic-data/master/data/diplotype_phenotype"

# Also try the API but with a much larger page size using the Prefer header
def fetch_all_with_prefer(gene):
    """Fetch all diplotypes using PostgREST Prefer: count=exact header."""
    headers = {
        "Prefer": "count=exact",
        "Range-Unit": "items",
        "Range": "0-9999"  # Request up to 10000 rows
    }
    r = requests.get(
        f"{CPIC_BASE}/diplotype",
        params={
            "genesymbol": f"eq.{gene}",
            "select": "diplotype,generesult",
        },
        headers=headers,
        timeout=30
    )
    if r.status_code in (200, 206):
        return r.json()
    return []

for gene in TARGET_GENES:
    try:
        data = fetch_all_with_prefer(gene)
        if not data:
            print(f"  {gene}: no data")
            continue

        gene_map = {}
        for row in data:
            diplotype = (row.get("diplotype") or "").strip()
            result    = (row.get("generesult") or "").strip()
            if diplotype and result:
                gene_map[diplotype] = result

        if gene_map:
            phenotype_map[gene] = gene_map
            print(f"  {gene}: {len(gene_map)} diplotypes")
        else:
            print(f"  {gene}: empty")

    except Exception as e:
        print(f"  {gene}: ERROR — {e}")

# ── Step 2: Sanity check — if common diplotypes still missing, fetch via TSV ──
missing = []
checks = {
    "CYP2D6": "*4/*4",
    "CYP2C19": "*2/*2",
    "TPMT": "*3A/*3A",
    "DPYD": "*2A/*2A",
}
for gene, dip in checks.items():
    if gene not in phenotype_map or dip not in phenotype_map.get(gene, {}):
        missing.append(gene)

if missing:
    print(f"\nWarning: {missing} still missing key diplotypes — fetching from CPIC TSV files...")

    # CPIC publishes TSV files per gene on their data repo
    TSV_URLS = {
        "CYP2D6":  "https://github.com/cpicpgx/cpic-data/raw/master/data/diplotype_phenotype/CYP2D6_diplotype_phenotype.tsv",
        "CYP2C19": "https://github.com/cpicpgx/cpic-data/raw/master/data/diplotype_phenotype/CYP2C19_diplotype_phenotype.tsv",
        "CYP2C9":  "https://github.com/cpicpgx/cpic-data/raw/master/data/diplotype_phenotype/CYP2C9_diplotype_phenotype.tsv",
        "CYP2B6":  "https://github.com/cpicpgx/cpic-data/raw/master/data/diplotype_phenotype/CYP2B6_diplotype_phenotype.tsv",
        "DPYD":    "https://github.com/cpicpgx/cpic-data/raw/master/data/diplotype_phenotype/DPYD_diplotype_phenotype.tsv",
        "TPMT":    "https://github.com/cpicpgx/cpic-data/raw/master/data/diplotype_phenotype/TPMT_diplotype_phenotype.tsv",
        "NUDT15":  "https://github.com/cpicpgx/cpic-data/raw/master/data/diplotype_phenotype/NUDT15_diplotype_phenotype.tsv",
        "SLCO1B1": "https://github.com/cpicpgx/cpic-data/raw/master/data/diplotype_phenotype/SLCO1B1_diplotype_phenotype.tsv",
        "UGT1A1":  "https://github.com/cpicpgx/cpic-data/raw/master/data/diplotype_phenotype/UGT1A1_diplotype_phenotype.tsv",
        "CYP3A5":  "https://github.com/cpicpgx/cpic-data/raw/master/data/diplotype_phenotype/CYP3A5_diplotype_phenotype.tsv",
        "NAT2":    "https://github.com/cpicpgx/cpic-data/raw/master/data/diplotype_phenotype/NAT2_diplotype_phenotype.tsv",
        "G6PD":    "https://github.com/cpicpgx/cpic-data/raw/master/data/diplotype_phenotype/G6PD_diplotype_phenotype.tsv",
        "RYR1":    "https://github.com/cpicpgx/cpic-data/raw/master/data/diplotype_phenotype/RYR1_diplotype_phenotype.tsv",
    }

    for gene in missing:
        if gene not in TSV_URLS:
            continue
        try:
            r = requests.get(TSV_URLS[gene], timeout=30)
            r.raise_for_status()
            reader = csv.DictReader(io.StringIO(r.text), delimiter='\t')
            gene_map = phenotype_map.get(gene, {})
            added = 0
            for row in reader:
                # TSV columns vary — try common field names
                dip = (row.get("diplotype") or row.get("Diplotype") or "").strip()
                res = (row.get("phenotype") or row.get("Phenotype") or
                       row.get("generesult") or row.get("EHR Priority Result") or "").strip()
                if dip and res:
                    gene_map[dip] = res
                    added += 1
            phenotype_map[gene] = gene_map
            print(f"  {gene}: loaded {added} diplotypes from TSV (total: {len(gene_map)})")
        except Exception as e:
            print(f"  {gene}: TSV fetch failed — {e}")

# ── Step 3: Fetch recommendations from API ────────────────────────────────────
print("\nFetching recommendations from CPIC API...")
try:
    # Use large range header
    headers = {"Range": "0-9999", "Prefer": "count=exact"}
    r = requests.get(
        f"{CPIC_BASE}/recommendation",
        params={"select": "id,drugid,drug(name),lookupkey,phenotypes,implications,drugrecommendation"},
        headers=headers,
        timeout=30
    )
    recs = r.json()
    print(f"  Got {len(recs)} recommendation rows")
except Exception as e:
    print(f"  ERROR: {e}")
    recs = []

# ── Step 4: Update Supabase ───────────────────────────────────────────────────
from supabase import create_client
sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])

cpic_rows = []
for rec in recs:
    drug_name = (rec.get("drug") or {}).get("name", "").lower().strip()
    if not drug_name:
        continue
    phenotypes     = rec.get("phenotypes") or {}
    recommendation = (rec.get("drugrecommendation") or "").strip()
    if not recommendation:
        continue
    for gene, phenotype in phenotypes.items():
        if gene not in TARGET_GENES:
            continue
        if not phenotype or phenotype.lower() in ("n/a", ""):
            continue
        cpic_rows.append({
            "gene": gene,
            "drug_name": drug_name,
            "phenotype": phenotype,
            "recommendation": recommendation,
            "cpic_includes_inhibitor_context": False,
        })

print(f"\nBuilt {len(cpic_rows)} cpic_cache rows")
seen, unique_rows = set(), []
for r in cpic_rows:
    key = (r["gene"], r["drug_name"], r["phenotype"])
    if key not in seen:
        seen.add(key)
        unique_rows.append(r)
print(f"After dedup: {len(unique_rows)} unique rows")

inserted = 0
for i in range(0, len(unique_rows), 50):
    batch = unique_rows[i:i+50]
    try:
        sb.table("cpic_cache").upsert(batch, on_conflict="gene,drug_name,phenotype").execute()
        inserted += len(batch)
    except Exception as e:
        print(f"  Batch error: {e}")
        for row in batch:
            try:
                sb.table("cpic_cache").upsert([row], on_conflict="gene,drug_name,phenotype").execute()
                inserted += 1
            except: pass
print(f"Inserted/updated {inserted} rows in cpic_cache")

# ── Step 5: Rewrite genotype_parser.py ───────────────────────────────────────
print("\nUpdating agents/genotype_parser.py...")
parser_path = "agents/genotype_parser.py"
with open(parser_path, "r") as f:
    original = f.read()

lines = ["PHENOTYPE_MAP = {\n"]
for gene, diplotypes in sorted(phenotype_map.items()):
    lines.append(f'    "{gene}": {{\n')
    for diplotype, phenotype in sorted(diplotypes.items()):
        d = diplotype.replace('"', '\\"')
        p = phenotype.replace('"', '\\"')
        lines.append(f'        "{d}": "{p}",\n')
    lines.append("    },\n")
lines.append("}\n")
new_map_block = "".join(lines)

start_idx = original.find("PHENOTYPE_MAP = {")
if start_idx == -1:
    print("  ERROR: PHENOTYPE_MAP not found")
else:
    depth, i = 0, start_idx
    while i < len(original):
        if original[i] == '{': depth += 1
        elif original[i] == '}':
            depth -= 1
            if depth == 0:
                end_idx = i + 1
                break
        i += 1
    new_content = original[:start_idx] + new_map_block + original[end_idx:]
    new_content = re.sub(
        r'# ── Extended CPIC.*?PHENOTYPE_MAP\.update\(\{.*?\}\)',
        '', new_content, flags=re.DOTALL
    )
    with open(parser_path, "w") as f:
        f.write(new_content)
    total = sum(len(v) for v in phenotype_map.values())
    print(f"  Written: {len(phenotype_map)} genes, {total} total diplotypes")

# ── Summary + sanity check ────────────────────────────────────────────────────
print("\n=== SYNC COMPLETE ===")
for gene, dips in sorted(phenotype_map.items()):
    print(f"  {gene}: {len(dips)} diplotypes")

r = sb.table("cpic_cache").select("gene", count="exact").execute()
print(f"\ncpic_cache total rows: {r.count}")

print("\n--- Sanity check ---")
print(f"  CYP2D6  *4/*4  : {phenotype_map.get('CYP2D6',{}).get('*4/*4','MISSING')}")
print(f"  CYP2C19 *2/*2  : {phenotype_map.get('CYP2C19',{}).get('*2/*2','MISSING')}")
print(f"  TPMT    *3A/*3A: {phenotype_map.get('TPMT',{}).get('*3A/*3A','MISSING')}")
print(f"  DPYD    *2A/*2A: {phenotype_map.get('DPYD',{}).get('*2A/*2A','MISSING')}")
