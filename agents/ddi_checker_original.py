import sys
sys.path.insert(0, ".")
from agents.ddi_loader import load_ddinter, lookup_ddi

# Severity string → numeric score
DDI_SEVERITY_SCORES = {
    "Major": 4,
    "Moderate": 3,
    "Minor": 2,
    "Unknown": 1,
}

# Phenotype multipliers (conservative upper-bound ranges per blueprint)
PHENOTYPE_MULTIPLIERS = {
    "Poor Metabolizer":          4.0,
    "Intermediate Metabolizer":  2.2,
    "Ultra-Rapid Metabolizer":   3.0,
    "Rapid Metabolizer":         1.5,
    "Normal Metabolizer":        1.0,
    "Decreased Function":        2.2,
    "Poor Function":             4.0,
    "Normal Function":           1.0,
    "Unknown":                   1.0,
}

SEVERITY_LABELS = {
    "CRITICAL": "🔴 CRITICAL",
    "HIGH":     "🟠 HIGH",
    "MODERATE": "🟡 MODERATE",
    "LOW":      "🟢 LOW",
}

def score_to_label(score: float) -> str:
    if score >= 15: return "CRITICAL"
    if score >= 10: return "HIGH"
    if score >= 5:  return "MODERATE"
    return "LOW"

def check_ddgi(drug_names: list, dgi_alerts: list, phenotypes: dict) -> list:
    """
    For each drug pair, look up DDI severity.
    Then combine with DGI severity using DDGI formula.
    Applies cpic_includes_inhibitor_context cap to prevent double-counting.
    """
    results = []

    for i in range(len(drug_names)):
        for j in range(i + 1, len(drug_names)):
            drug_a = drug_names[i]
            drug_b = drug_names[j]

            ddi = lookup_ddi(drug_a, drug_b)
            if not ddi:
                continue

            ddi_severity = DDI_SEVERITY_SCORES.get(ddi["severity"], 1)

            # Find the worst DGI alert involving either drug
            relevant_dgi = [
                a for a in dgi_alerts
                if a["drug"] in (drug_a, drug_b)
            ]

            if not relevant_dgi:
                # Pure DDI, no genetic component
                compound_score = ddi_severity
                escalation_note = "No genetic component found."
                severity_label = score_to_label(compound_score)
                results.append({
                    "drug_a": drug_a,
                    "drug_b": drug_b,
                    "ddi_severity": ddi["severity"],
                    "ddi_score": ddi_severity,
                    "dgi_score": 0,
                    "compound_score": compound_score,
                    "severity": severity_label,
                    "escalation_note": escalation_note,
                })
                continue

            # Get worst DGI alert
            worst_dgi = max(relevant_dgi, key=lambda x: x["severity"])
            dgi_score = worst_dgi["severity"]
            gene = worst_dgi["gene"]

            # Get patient phenotype for this gene
            phenotype = phenotypes.get(gene, {}).get("phenotype", "Unknown")
            multiplier = PHENOTYPE_MULTIPLIERS.get(phenotype, 1.0)

            # Apply double-count cap if CPIC already includes inhibitor context
            if worst_dgi["cpic_includes_inhibitor_context"]:
                compound_score = max(dgi_score, ddi_severity)
                escalation_note = f"CPIC guideline already incorporates inhibitor context. No additional genetic escalation."
            else:
                compound_score = max(dgi_score, ddi_severity) * multiplier
                escalation_note = f"Genetic escalation applied: {phenotype} multiplier {multiplier}x"

            severity_label = score_to_label(compound_score)

            results.append({
                "drug_a": drug_a,
                "drug_b": drug_b,
                "ddi_severity": ddi["severity"],
                "ddi_score": ddi_severity,
                "dgi_score": dgi_score,
                "gene": gene,
                "phenotype": phenotype,
                "multiplier": multiplier,
                "compound_score": round(compound_score, 1),
                "severity": severity_label,
                "escalation_note": escalation_note,
                "recommendation": worst_dgi["recommendation"],
            })

    return results

if __name__ == "__main__":
    load_ddinter("data/ddinter")

    from agents.dgi_analyzer import analyze_dgi

    phenotypes = {
        "CYP2C19": {"diplotype": "*1/*2",  "phenotype": "Intermediate Metabolizer"},
        "CYP2D6":  {"diplotype": "*4/*4",  "phenotype": "Poor Metabolizer"},
    }
    drugs = ["clopidogrel", "codeine", "omeprazole", "fluoxetine"]

    dgi_alerts = analyze_dgi(phenotypes, drugs)
    results = check_ddgi(drugs, dgi_alerts, phenotypes)

    print(f"\nDDGI Results for patient:\n")
    for r in results:
        label = SEVERITY_LABELS.get(r["severity"], r["severity"])
        print(f"  {label}: {r['drug_a']} + {r['drug_b']}")
        print(f"     DDI: {r['ddi_severity']} | DGI: {r.get('dgi_score', 0)} | Compound score: {r['compound_score']}")
        print(f"     {r['escalation_note']}")
        print(f"     → {r.get('recommendation', 'Monitor closely.')}")
        print()
