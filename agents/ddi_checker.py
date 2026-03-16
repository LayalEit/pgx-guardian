import sys
import logging
sys.path.insert(0, ".")
from agents.ddi_loader import load_ddinter, lookup_ddi

pgx_log = logging.getLogger("pgx.ddi")
if not pgx_log.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s [DDI] %(message)s", "%H:%M:%S"))
    pgx_log.addHandler(handler)
    pgx_log.setLevel(logging.DEBUG)

DDI_SEVERITY_SCORES = {"Major": 4, "Moderate": 3, "Minor": 2, "Unknown": 1}

PHENOTYPE_MULTIPLIERS = {
    "Poor Metabolizer":         4.0,
    "Intermediate Metabolizer": 2.2,
    "Ultra-Rapid Metabolizer":  3.0,
    "Rapid Metabolizer":        1.5,
    "Normal Metabolizer":       1.0,
    "Decreased Function":       2.2,
    "Poor Function":            4.0,
    "Normal Function":          1.0,
    "Unknown":                  1.0,
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

    KEY FIXES vs original:
    1. Multiplier is taken from the GENE of the worst DGI alert, not the patient's
       worst phenotype globally — prevents wrong-gene multiplier being applied.
    2. cpic_includes_inhibitor_context cap is only applied when the DDI pair
       involves an inhibitor drug that CPIC already modelled. For a PM patient
       with an additional inhibitor, we still escalate because the inhibitor
       phenoconverts further on top of already-PM status.
    3. Pure gene-drug findings (no DDI pair) are now surfaced directly via
       the dgi_alerts list in pgx_voice_agent — ddi_checker only handles pairs.
    """
    results = []

    for i in range(len(drug_names)):
        for j in range(i + 1, len(drug_names)):
            drug_a = drug_names[i]
            drug_b = drug_names[j]

            ddi = lookup_ddi(drug_a, drug_b)
            if not ddi:
                pgx_log.debug(f"[{drug_a} x {drug_b}] No DDInter entry — skipped")
                continue

            ddi_severity = DDI_SEVERITY_SCORES.get(ddi["severity"], 1)

            # Find DGI alerts for each drug separately
            dgi_a = [a for a in dgi_alerts if a["drug"] == drug_a]
            dgi_b = [a for a in dgi_alerts if a["drug"] == drug_b]
            relevant_dgi = dgi_a + dgi_b

            if not relevant_dgi:
                compound_score = ddi_severity
                severity_label = score_to_label(compound_score)
                pgx_log.info(f"[{drug_a} x {drug_b}] Pure DDI | score={compound_score} | {severity_label}")
                results.append({
                    "drug_a": drug_a, "drug_b": drug_b,
                    "ddi_severity": ddi["severity"], "ddi_score": ddi_severity,
                    "dgi_score": 0, "compound_score": compound_score,
                    "severity": severity_label,
                    "escalation_note": "No genetic component found.",
                })
                continue

            # FIX 1: Get worst DGI alert for each drug independently,
            # then pick the one with the highest severity
            worst_dgi = max(relevant_dgi, key=lambda x: x["severity"])
            dgi_score = worst_dgi["severity"]
            gene = worst_dgi["gene"]

            # FIX 2: Use the multiplier for the GENE in this specific alert,
            # not the patient's globally worst phenotype
            gene_phenotype = phenotypes.get(gene, {}).get("phenotype", "Unknown")
            multiplier = PHENOTYPE_MULTIPLIERS.get(gene_phenotype, 1.0)

            pgx_log.info(f"[{drug_a} x {drug_b}] worst DGI={worst_dgi['drug']}+{gene} | "
                         f"{gene_phenotype} | dgi={dgi_score} | ddi={ddi_severity} | mult={multiplier}x")

            # FIX 3: cpic_includes_inhibitor_context cap only applies when
            # the DDI pair itself is the inhibitor scenario CPIC modelled.
            # Check: is one of the two drugs the inhibitor drug in the worst DGI alert?
            inhibitor_drug_in_pair = worst_dgi["drug"] in (drug_a, drug_b) and \
                                     worst_dgi.get("relationship") == "inhibitor"

            if worst_dgi["cpic_includes_inhibitor_context"] and not inhibitor_drug_in_pair:
                # CPIC already modelled this inhibitor — cap at max, no escalation
                compound_score = max(dgi_score, ddi_severity)
                note = "CPIC already incorporates inhibitor context — no additional escalation"
            else:
                # Either CPIC didn't model this, or the inhibitor is the OTHER drug
                # in this pair → full escalation applies
                compound_score = max(dgi_score, ddi_severity) * multiplier
                note = f"Genetic escalation: {gene} {gene_phenotype} x{multiplier}"

            severity_label = score_to_label(compound_score)
            pgx_log.info(f"[{drug_a} x {drug_b}] FINAL: {round(compound_score,1)} => {severity_label} | {note}")

            results.append({
                "drug_a": drug_a, "drug_b": drug_b,
                "ddi_severity": ddi["severity"], "ddi_score": ddi_severity,
                "dgi_score": dgi_score, "gene": gene,
                "phenotype": gene_phenotype,
                "multiplier": multiplier,
                "compound_score": round(compound_score, 1),
                "severity": severity_label,
                "escalation_note": note,
                "recommendation": worst_dgi["recommendation"],
            })

    return results


if __name__ == "__main__":
    load_ddinter("data/ddinter")
    from agents.dgi_analyzer import analyze_dgi

    phenotypes = {
        "CYP2C19": {"diplotype": "*2/*2", "phenotype": "Poor Metabolizer"},
        "CYP2D6":  {"diplotype": "*4/*4", "phenotype": "Poor Metabolizer"},
    }
    drugs = ["clopidogrel", "codeine", "omeprazole", "fluoxetine"]
    dgi_alerts = analyze_dgi(phenotypes, drugs)
    results = check_ddgi(drugs, dgi_alerts, phenotypes)

    print(f"\nDDGI Results:\n")
    for r in sorted(results, key=lambda x: {"CRITICAL":0,"HIGH":1,"MODERATE":2,"LOW":3}.get(x["severity"],4)):
        print(f"  [{r['severity']}] {r['drug_a']} x {r['drug_b']} | score={r['compound_score']}")
        print(f"     {r['escalation_note']}")
        print(f"     → {r.get('recommendation','')}")
