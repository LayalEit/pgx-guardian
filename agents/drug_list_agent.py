import json

# Brand name → generic name mapping (covers common drugs)
SYNONYMS = {
    "tylenol": "acetaminophen",
    "advil": "ibuprofen",
    "motrin": "ibuprofen",
    "aleve": "naproxen",
    "plavix": "clopidogrel",
    "prilosec": "omeprazole",
    "prozac": "fluoxetine",
    "zoloft": "sertraline",
    "lipitor": "atorvastatin",
    "zocor": "simvastatin",
    "coumadin": "warfarin",
    "glucophage": "metformin",
    "norvasc": "amlodipine",
    "synthroid": "levothyroxine",
    "deltasone": "prednisone",
    "roxicodone": "oxycodone",
    "codcontin": "codeine",
}

def normalize_drug(drug_name: str) -> dict:
    """Normalize a drug name to its generic form."""
    lower = drug_name.strip().lower()
    generic = SYNONYMS.get(lower, lower)
    return {
        "original": drug_name,
        "normalized": generic,
        "status": "mapped" if generic != lower else "passthrough"
    }

def normalize_drug_list(drug_names: list) -> list:
    results = []
    for drug in drug_names:
        result = normalize_drug(drug)
        results.append(result)
        tag = "🔄" if result["status"] == "mapped" else "✅"
        print(f"  {tag} {result['original']} → {result['normalized']}")
    return results

if __name__ == "__main__":
    test_meds = ["clopidogrel", "omeprazole", "codeine", "fluoxetine", "Tylenol", "aspirin", "Plavix", "Prozac"]
    print("Normalizing medication list...")
    results = normalize_drug_list(test_meds)
    print(f"\n✅ Drug List Agent ready ({len(results)} drugs normalized)")
