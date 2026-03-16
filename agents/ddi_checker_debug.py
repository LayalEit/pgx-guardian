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
    "Poor Metabolizer": 4.0, "Intermediate Metabolizer": 2.2,
    "Ultra-Rapid Metabolizer": 3.0, "Rapid Metabolizer": 1.5,
    "Normal Metabolizer": 1.0, "Decreased Function": 2.2,
    "Poor Function": 4.0, "Normal Function": 1.0, "Unknown": 1.0,
}

def score_to_label(score: float) -> str:
    if score >= 15: return "CRITICAL"
    if score >= 10: return "HIGH"
    if score >= 5:  return "MODERATE"
    return "LOW"

def check_ddgi(drug_names: list, dgi_alerts: list, phenotypes: dict) -> list:
    pgx_log.info("=" * 60)
    pgx_log.info(f"CHECK_DDGI called | drugs={drug_names}")
    pgx_log.info(f"DGI alerts received: {len(dgi_alerts)}")
    for a in dgi_alerts:
        pgx_log.info(f"  DGI: {a['drug']}+{a['gene']} | {a['phenotype']} | sev={a['severity']} | cpic_inhibitor_ctx={a['cpic_includes_inhibitor_context']}")
    pgx_log.info("=" * 60)

    results = []
    pairs_checked = 0
    pairs_found = 0

    for i in range(len(drug_names)):
        for j in range(i + 1, len(drug_names)):
            drug_a = drug_names[i]
            drug_b = drug_names[j]
            pairs_checked += 1

            ddi = lookup_ddi(drug_a, drug_b)
            if not ddi:
                pgx_log.debug(f"[{drug_a} x {drug_b}] NO DDI entry in DDInter — pair skipped entirely")
                continue

            pairs_found += 1
            ddi_severity = DDI_SEVERITY_SCORES.get(ddi["severity"], 1)
            pgx_log.info(f"[{drug_a} x {drug_b}] DDI found: severity={ddi['severity']} (score={ddi_severity})")

            relevant_dgi = [a for a in dgi_alerts if a["drug"] in (drug_a, drug_b)]
            pgx_log.debug(f"[{drug_a} x {drug_b}] Relevant DGI alerts: {len(relevant_dgi)}")
            for a in relevant_dgi:
                pgx_log.debug(f"         {a['drug']}+{a['gene']} | {a['phenotype']} | sev={a['severity']}")

            if not relevant_dgi:
                compound_score = ddi_severity
                severity_label = score_to_label(compound_score)
                pgx_log.info(f"[{drug_a} x {drug_b}] Pure DDI (no gene component) | score={compound_score} | {severity_label}")
                results.append({
                    "drug_a": drug_a, "drug_b": drug_b,
                    "ddi_severity": ddi["severity"], "ddi_score": ddi_severity,
                    "dgi_score": 0, "compound_score": compound_score,
                    "severity": severity_label,
                    "escalation_note": "No genetic component found.",
                })
                continue

            worst_dgi = max(relevant_dgi, key=lambda x: x["severity"])
            dgi_score = worst_dgi["severity"]
            gene = worst_dgi["gene"]
            phenotype = phenotypes.get(gene, {}).get("phenotype", "Unknown")
            multiplier = PHENOTYPE_MULTIPLIERS.get(phenotype, 1.0)

            pgx_log.info(f"[{drug_a} x {drug_b}] Worst DGI: {worst_dgi['drug']}+{gene} | {phenotype} | dgi_score={dgi_score} | multiplier={multiplier}x")
            pgx_log.info(f"           cpic_includes_inhibitor_context={worst_dgi['cpic_includes_inhibitor_context']}")

            if worst_dgi["cpic_includes_inhibitor_context"]:
                compound_score = max(dgi_score, ddi_severity)
                note = "CPIC already incorporates inhibitor context — no additional escalation"
            else:
                compound_score = max(dgi_score, ddi_severity) * multiplier
                note = f"Genetic escalation: {phenotype} x{multiplier}"

            severity_label = score_to_label(compound_score)
            pgx_log.info(f"[{drug_a} x {drug_b}] FINAL: max({dgi_score},{ddi_severity}) * {multiplier} = {round(compound_score,1)} => {severity_label}")
            pgx_log.info(f"           Recommendation: {worst_dgi.get('recommendation','')}")

            results.append({
                "drug_a": drug_a, "drug_b": drug_b,
                "ddi_severity": ddi["severity"], "ddi_score": ddi_severity,
                "dgi_score": dgi_score, "gene": gene, "phenotype": phenotype,
                "multiplier": multiplier,
                "compound_score": round(compound_score, 1),
                "severity": severity_label,
                "escalation_note": note,
                "recommendation": worst_dgi["recommendation"],
            })

    pgx_log.info(f"CHECK_DDGI done — checked {pairs_checked} pairs, {pairs_found} had DDI data, {len(results)} results")
    pgx_log.info("FINAL RESULTS (sorted by severity):")
    sorted_r = sorted(results, key=lambda x: {"CRITICAL":0,"HIGH":1,"MODERATE":2,"LOW":3}.get(x["severity"],4))
    for r in sorted_r:
        pgx_log.info(f"  [{r['severity']}] {r['drug_a']} x {r['drug_b']} | score={r['compound_score']}")

    return results
