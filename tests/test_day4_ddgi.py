import sys
sys.path.insert(0, ".")
from agents.ddi_loader import load_ddinter
from agents.dgi_analyzer import analyze_dgi
from agents.ddi_checker import check_ddgi, SEVERITY_LABELS

load_ddinter("data/ddinter")

print("=" * 55)
print("DAY 4 VALIDATION — DDGI Formula Tests")
print("=" * 55)

# Test 1: codeine + fluoxetine + CYP2D6 PM → CRITICAL (score 20)
print("\n📋 Test 1: CYP2D6 Poor Metabolizer + codeine + fluoxetine")
phenotypes1 = {"CYP2D6": {"diplotype": "*4/*4", "phenotype": "Poor Metabolizer"}}
drugs1 = ["codeine", "fluoxetine"]
dgi1 = analyze_dgi(phenotypes1, drugs1)
ddgi1 = check_ddgi(drugs1, dgi1, phenotypes1)
for r in ddgi1:
    label = SEVERITY_LABELS.get(r["severity"], r["severity"])
    expected = r["compound_score"] == 20.0 and r["severity"] == "CRITICAL"
    status = "✅ PASS" if expected else "❌ FAIL"
    print(f"  {status} {label} — score {r['compound_score']} (expected 20.0 CRITICAL)")

# Test 2: clopidogrel + omeprazole + CYP2C19 IM → no double-count
print("\n📋 Test 2: CYP2C19 Intermediate Metabolizer + clopidogrel + omeprazole")
phenotypes2 = {"CYP2C19": {"diplotype": "*1/*2", "phenotype": "Intermediate Metabolizer"}}
drugs2 = ["clopidogrel", "omeprazole"]
dgi2 = analyze_dgi(phenotypes2, drugs2)
ddgi2 = check_ddgi(drugs2, dgi2, phenotypes2)
for r in ddgi2:
    label = SEVERITY_LABELS.get(r["severity"], r["severity"])
    expected = r["compound_score"] <= 5 and r["severity"] in ("LOW", "MODERATE")
    status = "✅ PASS" if expected else "❌ FAIL"
    print(f"  {status} {label} — score {r['compound_score']} (expected ≤5, no escalation)")
    print(f"     {r['escalation_note']}")

print("\n✅ Day 4 validation complete")
