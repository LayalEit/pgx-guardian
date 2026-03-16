import sys
sys.path.insert(0, ".")
from agents.genotype_parser import run_parser
from agents.drug_list_agent import normalize_drug_list
from agents.ddi_loader import load_ddinter
from agents.dgi_analyzer import analyze_dgi
from agents.ddi_checker import check_ddgi
from agents.graceful_degradation import validate_inputs
from agents.literature_agent import find_evidence

load_ddinter("data/ddinter")

print("=" * 55)
print("DAY 8 EDGE CASE TESTS")
print("=" * 55)

# Edge case 1: Unknown drug
print("\n📋 Test 1: Unknown/rare drug")
result = normalize_drug_list(["xyzunknowndrug123"])
print(f"  {'✅' if result[0]['status'] == 'passthrough' else '❌'} Unknown drug handled: {result[0]}")

# Edge case 2: Single medication (no DDI possible)
print("\n📋 Test 2: Single medication")
validation = validate_inputs("data/test_patients/patient_demo.csv", ["aspirin"])
print(f"  {'✅' if validation['valid'] else '❌'} Single med handled gracefully")
for w in validation["warnings"]:
    print(f"     [{w['type']}] {w['message']}")

# Edge case 3: Missing genotype file
print("\n📋 Test 3: Missing genotype file")
validation = validate_inputs("data/test_patients/nonexistent.csv", ["aspirin"])
print(f"  {'✅' if not validation['valid'] else '❌'} Missing file caught")
for w in validation["warnings"]:
    print(f"     [{w['type']}] {w['message']}")

# Edge case 4: Empty medication list
print("\n📋 Test 4: Empty medication list")
validation = validate_inputs("data/test_patients/patient_demo.csv", [])
print(f"  {'✅' if not validation['valid'] else '❌'} Empty meds caught")
for w in validation["warnings"]:
    print(f"     [{w['type']}] {w['message']}")

# Edge case 5: Unknown diplotype → phenotype falls back to Unknown
print("\n📋 Test 5: Unknown diplotype")
from agents.genotype_parser import PHENOTYPE_MAP
result = PHENOTYPE_MAP.get("CYP2D6", {}).get("*99/*99", "Unknown")
print(f"  {'✅' if result == 'Unknown' else '❌'} Unknown diplotype → '{result}'")

# Edge case 6: Drug with no interactions in DDInter
print("\n📋 Test 6: Drug pair with no known interaction")
from agents.ddi_loader import lookup_ddi
result = lookup_ddi("water", "air")
print(f"  {'✅' if result is None else '❌'} Unknown pair returns None: {result}")

# Edge case 7: Literature agent with no cached evidence
print("\n📋 Test 7: Literature agent — uncached drug")
results = find_evidence("warfarin", "CYP2C9", "Poor Metabolizer")
print(f"  {'✅' if results == [] else '❌'} No cache → empty list (graceful)")

# Edge case 8: Brand name normalization
print("\n📋 Test 8: Brand name chain")
meds = normalize_drug_list(["Plavix", "Prozac", "Tylenol", "Zocor"])
all_mapped = all(m["status"] == "mapped" for m in meds)
print(f"  {'✅' if all_mapped else '❌'} All brand names mapped to generics")
for m in meds:
    print(f"     {m['original']} → {m['normalized']}")

print("\n✅ Day 8 edge case tests complete")
