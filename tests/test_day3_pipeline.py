import sys
sys.path.insert(0, ".")

from agents.drug_list_agent import normalize_drug_list
from agents.ddi_loader import load_ddinter, lookup_ddi
from agents.dgidb_loader import load_dgidb, lookup_dgi

print("=" * 50)
print("DAY 3 PIPELINE TEST")
print("=" * 50)

# Load data
print("\n📦 Loading data...")
load_ddinter("data/ddinter")
load_dgidb("data/dgidb/interactions.tsv")

# Patient medication list (as a clinician might type it)
raw_meds = ["Plavix", "omeprazole", "codeine", "Prozac"]

print("\n💊 Step 1: Normalize drug names")
normalized = normalize_drug_list(raw_meds)
drug_names = [d["normalized"] for d in normalized]

print("\n🔍 Step 2: Check all drug-drug pairs (DDI)")
pairs_checked = 0
for i in range(len(drug_names)):
    for j in range(i + 1, len(drug_names)):
        a, b = drug_names[i], drug_names[j]
        result = lookup_ddi(a, b)
        if result:
            print(f"  ⚠️  {a} + {b} → {result['severity']} interaction")
        else:
            print(f"  ✅ {a} + {b} → No known interaction")
        pairs_checked += 1

print(f"\n  Total pairs checked: {pairs_checked}")

print("\n🧬 Step 3: Check drug-gene interactions (DGIdb)")
target_genes = ["CYP2D6", "CYP2C19", "CYP2C9", "DPYD", "SLCO1B1"]
for drug in drug_names:
    for gene in target_genes:
        result = lookup_dgi(drug, gene)
        if result:
            print(f"  🔬 {drug} + {gene} → {result['interaction_type']}")

print("\n✅ Day 3 pipeline test complete")
