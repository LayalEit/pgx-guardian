import sys
sys.path.insert(0, ".")

# CPIC dosing recommendations by gene + phenotype + drug
# These are real CPIC guideline summaries
DOSING_RULES = {
    ("CYP2D6", "Poor Metabolizer", "codeine"): {
        "action": "AVOID",
        "reason": "Codeine cannot be converted to morphine. Risk of inefficacy.",
        "alternatives": ["morphine", "hydromorphone", "non-opioid analgesics"],
        "guideline": "CPIC codeine/CYP2D6 guideline",
        "evidence": "Level A"
    },
    ("CYP2D6", "Ultra-Rapid Metabolizer", "codeine"): {
        "action": "AVOID",
        "reason": "Ultra-rapid conversion to morphine. Risk of toxicity and death.",
        "alternatives": ["morphine (with caution)", "non-opioid analgesics"],
        "guideline": "CPIC codeine/CYP2D6 guideline",
        "evidence": "Level A"
    },
    ("CYP2D6", "Intermediate Metabolizer", "codeine"): {
        "action": "USE WITH CAUTION",
        "reason": "Reduced conversion to morphine. May have reduced efficacy.",
        "alternatives": ["consider non-CYP2D6 analgesic"],
        "guideline": "CPIC codeine/CYP2D6 guideline",
        "evidence": "Level A"
    },
    ("CYP2C19", "Poor Metabolizer", "clopidogrel"): {
        "action": "AVOID",
        "reason": "Markedly reduced platelet inhibition. High risk of cardiovascular events.",
        "alternatives": ["prasugrel", "ticagrelor"],
        "guideline": "CPIC clopidogrel/CYP2C19 guideline",
        "evidence": "Level A"
    },
    ("CYP2C19", "Intermediate Metabolizer", "clopidogrel"): {
        "action": "CONSIDER ALTERNATIVE",
        "reason": "Reduced platelet inhibition. Increased risk especially with PPI co-medication.",
        "alternatives": ["prasugrel", "ticagrelor"],
        "guideline": "CPIC clopidogrel/CYP2C19 guideline",
        "evidence": "Level A"
    },
    ("CYP2C19", "Rapid Metabolizer", "clopidogrel"): {
        "action": "USE AS DIRECTED",
        "reason": "Normal to increased platelet inhibition.",
        "alternatives": [],
        "guideline": "CPIC clopidogrel/CYP2C19 guideline",
        "evidence": "Level A"
    },
    ("DPYD", "Intermediate Metabolizer", "fluorouracil"): {
        "action": "REDUCE DOSE 50%",
        "reason": "Reduced DPYD activity. Risk of severe fluoropyrimidine toxicity.",
        "alternatives": ["start at 50% dose", "consider capecitabine with dose reduction"],
        "guideline": "CPIC fluoropyrimidine/DPYD guideline",
        "evidence": "Level A"
    },
    ("DPYD", "Poor Metabolizer", "fluorouracil"): {
        "action": "AVOID",
        "reason": "Complete or near-complete DPYD deficiency. Life-threatening toxicity risk.",
        "alternatives": ["alternative non-fluoropyrimidine chemotherapy"],
        "guideline": "CPIC fluoropyrimidine/DPYD guideline",
        "evidence": "Level A"
    },
    ("SLCO1B1", "Decreased Function", "simvastatin"): {
        "action": "REDUCE DOSE or SWITCH",
        "reason": "Increased simvastatin exposure. Risk of myopathy.",
        "alternatives": ["pravastatin", "rosuvastatin", "lower simvastatin dose"],
        "guideline": "CPIC simvastatin/SLCO1B1 guideline",
        "evidence": "Level A"
    },
    ("SLCO1B1", "Poor Function", "simvastatin"): {
        "action": "AVOID",
        "reason": "Markedly increased simvastatin exposure. High myopathy risk.",
        "alternatives": ["pravastatin", "rosuvastatin"],
        "guideline": "CPIC simvastatin/SLCO1B1 guideline",
        "evidence": "Level A"
    },
}

def get_dosing_recommendations(phenotypes: dict, drug_names: list) -> list:
    """Return CPIC-based dosing recommendations for each drug-gene pair."""
    recommendations = []
    for drug in drug_names:
        for gene, info in phenotypes.items():
            phenotype = info["phenotype"]
            key = (gene, phenotype, drug.lower())
            rule = DOSING_RULES.get(key)
            if rule:
                recommendations.append({
                    "drug": drug,
                    "gene": gene,
                    "phenotype": phenotype,
                    "action": rule["action"],
                    "reason": rule["reason"],
                    "alternatives": rule["alternatives"],
                    "guideline": rule["guideline"],
                    "evidence": rule["evidence"],
                })
    return recommendations

if __name__ == "__main__":
    phenotypes = {
        "CYP2C19": {"diplotype": "*1/*2", "phenotype": "Intermediate Metabolizer"},
        "CYP2D6":  {"diplotype": "*4/*4", "phenotype": "Poor Metabolizer"},
        "DPYD":    {"diplotype": "*1/*2A", "phenotype": "Intermediate Metabolizer"},
    }
    drugs = ["clopidogrel", "codeine", "fluorouracil", "omeprazole"]

    print("💊 DOSING RECOMMENDATIONS:\n")
    recs = get_dosing_recommendations(phenotypes, drugs)
    if recs:
        for r in recs:
            print(f"  {r['action']}: {r['drug']} ({r['gene']} {r['phenotype']})")
            print(f"  Reason: {r['reason']}")
            if r['alternatives']:
                print(f"  Alternatives: {', '.join(r['alternatives'])}")
            print(f"  Source: {r['guideline']} [{r['evidence']}]")
            print()
    else:
        print("  No specific dosing recommendations found.")
