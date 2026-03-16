import os
import json
from supabase import create_client
from dotenv import load_dotenv

load_dotenv()
supabase = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])

def _get_cpic_recommendation(gene: str, drug: str, phenotype: str, diplotype: str) -> dict | None:
    """
    Look up CPIC recommendation with priority:
    1. Exact diplotype match (most specific)
    2. Phenotype match
    3. First row fallback (only if not Indeterminate)
    Returns the matching row dict or None.
    """
    result = supabase.table("cpic_cache") \
        .select("*") \
        .eq("gene", gene) \
        .eq("drug_name", drug.lower()) \
        .execute()

    if not result.data:
        return None

    rows = result.data

    # 1. Exact diplotype match
    if diplotype and diplotype != "direct":
        for row in rows:
            if row.get("diplotype") == diplotype:
                return row

    # 2. Phenotype match
    for row in rows:
        if row.get("phenotype") == phenotype:
            return row

    # 3. Fallback — skip Indeterminate rows
    non_indet = [r for r in rows if r.get("phenotype") != "Indeterminate"]
    return non_indet[0] if non_indet else rows[0]


# ── Hardcoded recommendations for HLA/immune genes where CPIC cache is empty ──
# CPIC guideline data for these is allele-based, not diplotype-based,
# so cpic_cache has no rows — recommendations are hardcoded from CPIC guidelines.
HLA_HARDCODED = {
    "HLA-B": {
        "Abacavir hypersensitivity — HIGH RISK": {
            "abacavir": (
                "Abacavir is contraindicated. Patients carrying HLA-B*57:01 have a high risk "
                "of a potentially fatal hypersensitivity reaction. Use an alternative antiretroviral agent."
            ),
        },
        "Carbamazepine SJS/TEN — HIGH RISK": {
            "carbamazepine": (
                "Carbamazepine is contraindicated. Patients carrying HLA-B*15:02 have a high risk "
                "of Stevens-Johnson Syndrome or Toxic Epidermal Necrolysis. Use an alternative antiepileptic."
            ),
        },
        "Allopurinol SJS/TEN — HIGH RISK": {
            "allopurinol": (
                "Allopurinol is contraindicated. Patients carrying HLA-B*58:01 have a high risk "
                "of Stevens-Johnson Syndrome or Toxic Epidermal Necrolysis. Use febuxostat as an alternative."
            ),
        },
        "Abacavir hypersensitivity — carrier risk": {
            "abacavir": (
                "Use abacavir with caution. Patient carries one copy of HLA-B*57:01 and has elevated "
                "risk of hypersensitivity reaction. Consider alternative antiretroviral if available."
            ),
        },
        "Carbamazepine SJS/TEN — carrier risk": {
            "carbamazepine": (
                "Use carbamazepine with caution. Patient carries one copy of HLA-B*15:02. "
                "Monitor closely for skin reactions and consider alternative antiepileptic."
            ),
        },
    },
    "HLA-A": {
        "Carbamazepine DRESS — HIGH RISK": {
            "carbamazepine": (
                "Carbamazepine is contraindicated. Patients carrying HLA-A*31:01 have a high risk "
                "of Drug Reaction with Eosinophilia and Systemic Symptoms (DRESS). Use an alternative antiepileptic."
            ),
        },
        "Carbamazepine DRESS — carrier risk": {
            "carbamazepine": (
                "Use carbamazepine with caution. Patient carries one copy of HLA-A*31:01. "
                "Monitor closely for systemic hypersensitivity reactions."
            ),
        },
    },
    "G6PD": {
        "Deficient": {
            "rasburicase":    "Rasburicase is contraindicated in G6PD deficiency. Risk of severe hemolytic anemia. Use allopurinol as alternative.",
            "dapsone":        "Avoid dapsone in G6PD deficiency. Risk of hemolytic anemia and methemoglobinemia.",
            "primaquine":     "Avoid primaquine in G6PD deficiency. Risk of severe hemolytic anemia.",
            "nitrofurantoin": "Avoid nitrofurantoin in G6PD deficiency. Risk of hemolytic anemia.",
            "methylene blue": "Methylene blue is contraindicated in G6PD deficiency. Paradoxically worsens methemoglobinemia.",
        },
        "Deficient — heterozygous": {
            "rasburicase":    "Use rasburicase with extreme caution. Heterozygous G6PD deficiency still carries hemolysis risk.",
            "dapsone":        "Use dapsone with caution in G6PD heterozygotes. Monitor for hemolytic anemia.",
            "primaquine":     "Use primaquine with caution in G6PD heterozygotes. Monitor hemoglobin closely.",
            "nitrofurantoin": "Use nitrofurantoin with caution. Monitor for hemolytic anemia.",
        },
    },
    "RYR1": {
        "Malignant Hyperthermia Susceptible": {
            "halothane":       "Halothane is contraindicated. RYR1 variant confers malignant hyperthermia susceptibility. Use total intravenous anesthesia (TIVA).",
            "sevoflurane":     "Sevoflurane is contraindicated. RYR1 variant confers malignant hyperthermia susceptibility. Use TIVA.",
            "desflurane":      "Desflurane is contraindicated. RYR1 variant confers malignant hyperthermia susceptibility. Use TIVA.",
            "isoflurane":      "Isoflurane is contraindicated. RYR1 variant confers malignant hyperthermia susceptibility. Use TIVA.",
            "succinylcholine": "Succinylcholine is contraindicated. RYR1 variant confers malignant hyperthermia susceptibility. Use non-depolarizing neuromuscular blocker.",
        },
    },
    "CACNA1S": {
        "Malignant Hyperthermia Susceptible": {
            "halothane":       "Halothane is contraindicated. CACNA1S variant confers malignant hyperthermia susceptibility. Use TIVA.",
            "sevoflurane":     "Sevoflurane is contraindicated. CACNA1S variant confers malignant hyperthermia susceptibility. Use TIVA.",
            "desflurane":      "Desflurane is contraindicated. CACNA1S variant confers malignant hyperthermia susceptibility. Use TIVA.",
            "isoflurane":      "Isoflurane is contraindicated. CACNA1S variant confers malignant hyperthermia susceptibility. Use TIVA.",
            "succinylcholine": "Succinylcholine is contraindicated. CACNA1S variant confers malignant hyperthermia susceptibility. Use non-depolarizing neuromuscular blocker.",
        },
    },
}

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
            cpic_row = _get_cpic_recommendation(gene, drug, patient_phenotype, diplotype)

            if cpic_row:
                recommendation = cpic_row["recommendation"]
                # Suppress useless "No recommendation" when we have a phenotype
                if recommendation.strip().lower() in ("no recommendation", "") \
                        and patient_phenotype not in ("Indeterminate", "Unknown"):
                    recommendation = "No CPIC guideline available for this phenotype. Use clinical judgment."
                alerts.append({
                    "drug": drug,
                    "gene": gene,
                    "diplotype": diplotype,
                    "phenotype": patient_phenotype,
                    "mechanism_type": row["mechanism_type"],
                    "relationship": row["relationship"],
                    "strength": row["strength"],
                    "recommendation": recommendation,
                    "cpic_includes_inhibitor_context": cpic_row["cpic_includes_inhibitor_context"],
                    "severity": _phenotype_to_severity(patient_phenotype, row["relationship"]),
                    "source": "CPIC"
                })
            else:
                # No CPIC entry — check hardcoded HLA table before falling back
                hardcoded = HLA_HARDCODED.get(gene, {}).get(patient_phenotype, {}).get(drug.lower())
                recommendation = hardcoded if hardcoded else "No CPIC guideline available. Use clinical judgment."
                source = "CPIC_hardcoded" if hardcoded else "mechanism_kb_only"
                alerts.append({
                    "drug": drug,
                    "gene": gene,
                    "diplotype": diplotype,
                    "phenotype": patient_phenotype,
                    "mechanism_type": row["mechanism_type"],
                    "relationship": row["relationship"],
                    "strength": row["strength"],
                    "recommendation": recommendation,
                    "cpic_includes_inhibitor_context": False,
                    "severity": _phenotype_to_severity(patient_phenotype, row["relationship"]),
                    "source": source
                })

    return alerts

def _phenotype_to_severity(phenotype: str, relationship: str) -> int:
    """
    Convert phenotype + relationship to a DGI severity score (1-5).
    Handles standard metabolizer phenotypes plus non-standard strings
    used by VKORC1, HLA, G6PD, SLCO1B1, RYR1 etc.
    """
    if relationship == "substrate":
        return {
            # Standard metabolizer phenotypes
            "Poor Metabolizer":          5,
            "Intermediate Metabolizer":  4,
            "Ultra-Rapid Metabolizer":   4,
            "Ultrarapid Metabolizer":    4,
            "Rapid Metabolizer":         2,
            "Normal Metabolizer":        1,
            "Normal Function":           1,
            # VKORC1 warfarin sensitivity
            "High warfarin sensitivity":          5,
            "Intermediate warfarin sensitivity":  4,
            "Normal warfarin sensitivity":        1,
            # SLCO1B1 transporter function
            "Poor Function":      5,
            "Decreased Function": 4,
            # HLA hypersensitivity alleles
            "Abacavir hypersensitivity — HIGH RISK":   5,
            "Abacavir hypersensitivity — carrier risk": 4,
            "Carbamazepine SJS/TEN — HIGH RISK":       5,
            "Carbamazepine SJS/TEN — carrier risk":    4,
            "Carbamazepine DRESS — HIGH RISK":         5,
            "Carbamazepine DRESS — carrier risk":      4,
            "Allopurinol SJS/TEN — HIGH RISK":         5,
            "Allopurinol SJS/TEN — carrier risk":      4,
            # G6PD
            "Deficient":                5,
            "Deficient — heterozygous": 4,
            "Normal Activity":          1,
            # RYR1/CACNA1S malignant hyperthermia
            "Malignant Hyperthermia Susceptible": 5,
            # IFNL3
            "Unfavorable Response":   4,
            "Intermediate Response":  3,
            "Favorable Response (peginterferon)": 1,
            "Unknown": 2,
        }.get(phenotype, 2)
    elif relationship in ("inhibitor", "inducer"):
        return {"strong": 4, "moderate": 3, "weak": 2}.get("moderate", 3)
    elif relationship == "hypersensitivity_marker":
        return 5
    return 2

if __name__ == "__main__":
    phenotypes = {
        "CYP2C19": {"diplotype": "*2/*2", "phenotype": "Poor Metabolizer"},
        "CYP2D6":  {"diplotype": "*4/*4", "phenotype": "Poor Metabolizer"},
    }
    drugs = ["clopidogrel", "codeine", "omeprazole", "fluoxetine"]

    print("Running DGI Analysis...")
    alerts = analyze_dgi(phenotypes, drugs)
    print(f"\nFound {len(alerts)} DGI alerts:\n")
    for a in alerts:
        print(f"  {a['drug']} + {a['gene']} ({a['phenotype']})")
        print(f"     Severity: {a['severity']} | {a['recommendation'][:80]}")
        print()
