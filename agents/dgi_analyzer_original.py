import os
import json
from supabase import create_client
from dotenv import load_dotenv

load_dotenv()
supabase = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])

def analyze_dgi(phenotypes: dict, drug_names: list) -> list:
    """
    For each drug, check if the patient's phenotype creates a risk.
    Queries mechanism_knowledge_base + cpic_cache.
    Returns list of DGI alerts.
    """
    alerts = []

    for drug in drug_names:
        # Step 1: Find all genes this drug interacts with
        mech_result = supabase.table("mechanism_knowledge_base") \
            .select("*") \
            .eq("drug_name", drug.lower()) \
            .execute()

        for row in mech_result.data:
            gene = row["gene"]

            # Step 2: Does this patient have a phenotype for this gene?
            if gene not in phenotypes:
                continue

            patient_phenotype = phenotypes[gene]["phenotype"]
            diplotype = phenotypes[gene]["diplotype"]

            # Step 3: Look up CPIC recommendation
            cpic_result = supabase.table("cpic_cache") \
                .select("*") \
                .eq("gene", gene) \
                .eq("drug_name", drug.lower()) \
                .execute()

            if cpic_result.data:
                cpic_row = cpic_result.data[0]
                alerts.append({
                    "drug": drug,
                    "gene": gene,
                    "diplotype": diplotype,
                    "phenotype": patient_phenotype,
                    "mechanism_type": row["mechanism_type"],
                    "relationship": row["relationship"],
                    "strength": row["strength"],
                    "recommendation": cpic_row["recommendation"],
                    "cpic_includes_inhibitor_context": cpic_row["cpic_includes_inhibitor_context"],
                    "severity": _phenotype_to_severity(patient_phenotype, row["relationship"]),
                    "source": "CPIC"
                })
            else:
                # No CPIC entry but mechanism exists — still flag it
                alerts.append({
                    "drug": drug,
                    "gene": gene,
                    "diplotype": diplotype,
                    "phenotype": patient_phenotype,
                    "mechanism_type": row["mechanism_type"],
                    "relationship": row["relationship"],
                    "strength": row["strength"],
                    "recommendation": "No CPIC guideline available. Use clinical judgment.",
                    "cpic_includes_inhibitor_context": False,
                    "severity": _phenotype_to_severity(patient_phenotype, row["relationship"]),
                    "source": "mechanism_kb_only"
                })

    return alerts

def _phenotype_to_severity(phenotype: str, relationship: str) -> int:
    """
    Convert phenotype + relationship to a DGI severity score (1-5).
    Substrates are at risk when they are poor/ultra-rapid metabolizers.
    Inhibitors/inducers carry risk regardless of patient phenotype.
    """
    if relationship == "substrate":
        return {
            "Poor Metabolizer": 5,
            "Intermediate Metabolizer": 4,
            "Ultra-Rapid Metabolizer": 4,
            "Normal Metabolizer": 1,
            "Rapid Metabolizer": 2,
            "Unknown": 2,
        }.get(phenotype, 2)
    elif relationship in ("inhibitor", "inducer"):
        return {"strong": 4, "moderate": 3, "weak": 2}.get("moderate", 3)
    elif relationship == "hypersensitivity_marker":
        return 5
    return 2

if __name__ == "__main__":
    # Test with Patient 1: CYP2C19 Intermediate Metabolizer
    phenotypes = {
        "CYP2C19": {"diplotype": "*1/*2", "phenotype": "Intermediate Metabolizer"},
        "CYP2D6":  {"diplotype": "*4/*4", "phenotype": "Poor Metabolizer"},
    }
    drugs = ["clopidogrel", "codeine", "omeprazole", "fluoxetine"]

    print("Running DGI Analysis...")
    alerts = analyze_dgi(phenotypes, drugs)
    print(f"\nFound {len(alerts)} DGI alerts:\n")
    for a in alerts:
        print(f"  🧬 {a['drug']} + {a['gene']} ({a['phenotype']})")
        print(f"     Severity: {a['severity']} | {a['recommendation']}")
        print(f"     CPIC includes inhibitor context: {a['cpic_includes_inhibitor_context']}")
        print()
