import sys
import time
sys.path.insert(0, ".")

from agents.drug_list_agent import normalize_drug_list
from agents.genotype_parser import run_parser
from agents.dgi_analyzer import analyze_dgi
from agents.ddi_checker import check_ddgi, SEVERITY_LABELS
from agents.ddi_loader import load_ddinter, lookup_ddi
from agents.dgidb_loader import load_dgidb, lookup_dgi

# Load data once at startup
print("🔄 Loading data at startup...")
load_ddinter("data/ddinter")
load_dgidb("data/dgidb/interactions.tsv")
print("✅ Data loaded\n")

def run_pipeline(genotype_input: str, raw_medications: list) -> dict:
    """
    Full PGx-Guardian pipeline.
    Input: genotype file path + raw medication list
    Output: complete safety report
    """
    start = time.time()
    report = {}

    # Phase 1 (can run in parallel in ADK):
    # Step 1: Parse genotypes
    t1 = time.time()
    genotype_result = run_parser(genotype_input)
    phenotypes = genotype_result["phenotypes"]
    report["genotype_elapsed"] = genotype_result["elapsed_seconds"]
    print(f"  ✅ Genotype parsed in {genotype_result['elapsed_seconds']}s")

    # Step 2: Normalize drugs
    t2 = time.time()
    normalized = normalize_drug_list(raw_medications)
    drug_names = [d["normalized"] for d in normalized]
    report["drug_normalization_elapsed"] = round(time.time() - t2, 3)
    print(f"  ✅ Drugs normalized in {report['drug_normalization_elapsed']}s")

    # Phase 2: Analysis
    # Step 3: DGI Analysis
    t3 = time.time()
    dgi_alerts = analyze_dgi(phenotypes, drug_names)
    report["dgi_elapsed"] = round(time.time() - t3, 3)
    print(f"  ✅ DGI analysis in {report['dgi_elapsed']}s — {len(dgi_alerts)} alerts")

    # Step 4: DDI + DDGI
    t4 = time.time()
    ddgi_results = check_ddgi(drug_names, dgi_alerts, phenotypes)
    report["ddi_elapsed"] = round(time.time() - t4, 3)
    print(f"  ✅ DDI/DDGI analysis in {report['ddi_elapsed']}s — {len(ddgi_results)} interactions")

    # Step 5: Compile report
    total = round(time.time() - start, 3)
    report["phenotypes"] = phenotypes
    report["drugs"] = drug_names
    report["dgi_alerts"] = dgi_alerts
    report["ddgi_results"] = ddgi_results
    report["total_elapsed"] = total

    # Sort DDGI results by severity
    severity_order = {"CRITICAL": 0, "HIGH": 1, "MODERATE": 2, "LOW": 3}
    report["ddgi_results"].sort(key=lambda x: severity_order.get(x["severity"], 4))

    return report

def print_report(report: dict):
    print("\n" + "=" * 55)
    print("PGx-GUARDIAN SAFETY REPORT")
    print("=" * 55)

    print("\n🧬 PATIENT PHENOTYPES:")
    for gene, info in report["phenotypes"].items():
        print(f"  {gene}: {info['diplotype']} → {info['phenotype']}")

    print(f"\n💊 MEDICATIONS: {', '.join(report['drugs'])}")

    print("\n⚠️  DRUG-GENE INTERACTIONS:")
    if report["dgi_alerts"]:
        for a in report["dgi_alerts"]:
            print(f"  🧬 {a['drug']} + {a['gene']} ({a['phenotype']}) — severity {a['severity']}")
            print(f"     → {a['recommendation']}")
    else:
        print("  None found.")

    print("\n🔴 DDGI COMPOUND INTERACTIONS (sorted by severity):")
    if report["ddgi_results"]:
        for r in report["ddgi_results"]:
            label = SEVERITY_LABELS.get(r["severity"], r["severity"])
            print(f"  {label}: {r['drug_a']} + {r['drug_b']} (score: {r['compound_score']})")
            print(f"     {r['escalation_note']}")
            print(f"     → {r.get('recommendation', 'Monitor closely.')}")
    else:
        print("  None found.")

    print(f"\n⏱️  LATENCY BREAKDOWN:")
    print(f"  Genotype parsing:   {report['genotype_elapsed']}s")
    print(f"  Drug normalization: {report['drug_normalization_elapsed']}s")
    print(f"  DGI analysis:       {report['dgi_elapsed']}s")
    print(f"  DDI/DDGI analysis:  {report['ddi_elapsed']}s")
    print(f"  ─────────────────────────")
    print(f"  TOTAL:              {report['total_elapsed']}s")
    target = "✅ UNDER TARGET" if report['total_elapsed'] < 9 else "❌ OVER TARGET"
    print(f"  {target} (target: <9s)")

if __name__ == "__main__":
    # Run the main demo scenario from the blueprint
    print("🏥 Running demo scenario: CYP2C19 IM + CYP2D6 PM patient\n")
    report = run_pipeline(
        genotype_input="data/test_patients/patient2.csv",
        raw_medications=["Plavix", "omeprazole", "codeine", "Prozac"]
    )
    print_report(report)
