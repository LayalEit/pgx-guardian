import os
import sys
sys.path.insert(0, ".")
from google import genai
from dotenv import load_dotenv

load_dotenv()
client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])

CLINICIAN_PROMPT = """You are a clinical pharmacogenomics assistant.
Given the following patient safety report, write a concise clinical summary (3-5 sentences).
Focus on the most critical interactions, the genetic reason, and the recommended action.
Always cite the guideline source. Never use relative risk without a baseline.
Add this disclaimer at the end: 'This report is for clinical decision support only. All recommendations require physician review.'

Report:
{report}
"""

PATIENT_PROMPT = """You are explaining a medication safety report to a patient in plain English.
Use simple words. No medical jargon. Focus on what this means for them practically.
Never say '3x higher risk' without saying 'compared to what'.
Use absolute terms: 'This medication may not work well for you' not 'reduced efficacy'.
Keep it under 100 words. Be reassuring but honest.
Add: 'Please discuss these results with your doctor before making any changes.'

Report:
{report}
"""

def format_report_for_prompt(ddgi_results: list, dosing_recs: list, phenotypes: dict) -> str:
    lines = []
    lines.append("PATIENT PHENOTYPES:")
    for gene, info in phenotypes.items():
        lines.append(f"  {gene}: {info['phenotype']}")
    lines.append("\nCRITICAL INTERACTIONS:")
    for r in ddgi_results:
        if r["severity"] in ("CRITICAL", "HIGH"):
            lines.append(f"  {r['drug_a']} + {r['drug_b']}: {r['severity']} (score {r['compound_score']})")
            lines.append(f"  Note: {r['escalation_note']}")
    lines.append("\nDOSING RECOMMENDATIONS:")
    for r in dosing_recs:
        lines.append(f"  {r['action']}: {r['drug']} — {r['reason']}")
        if r["alternatives"]:
            lines.append(f"  Alternatives: {', '.join(r['alternatives'])}")
    return "\n".join(lines)

def explain_clinician(ddgi_results: list, dosing_recs: list, phenotypes: dict) -> str:
    report_text = format_report_for_prompt(ddgi_results, dosing_recs, phenotypes)
    prompt = CLINICIAN_PROMPT.format(report=report_text)
    response = client.models.generate_content(model="gemini-2.5-flash", contents=prompt)
    return response.text

def explain_patient(ddgi_results: list, dosing_recs: list, phenotypes: dict) -> str:
    report_text = format_report_for_prompt(ddgi_results, dosing_recs, phenotypes)
    prompt = PATIENT_PROMPT.format(report=report_text)
    response = client.models.generate_content(model="gemini-2.5-flash", contents=prompt)
    return response.text

if __name__ == "__main__":
    from agents.ddi_loader import load_ddinter
    from agents.dgi_analyzer import analyze_dgi
    from agents.ddi_checker import check_ddgi
    from agents.dosing_advisor import get_dosing_recommendations

    load_ddinter("data/ddinter")

    phenotypes = {
        "CYP2C19": {"diplotype": "*1/*2",  "phenotype": "Intermediate Metabolizer"},
        "CYP2D6":  {"diplotype": "*4/*4",  "phenotype": "Poor Metabolizer"},
    }
    drugs = ["clopidogrel", "codeine", "omeprazole", "fluoxetine"]

    dgi_alerts = analyze_dgi(phenotypes, drugs)
    ddgi_results = check_ddgi(drugs, dgi_alerts, phenotypes)
    dosing_recs = get_dosing_recommendations(phenotypes, drugs)

    print("=" * 55)
    print("🩺 CLINICIAN EXPLANATION:")
    print("=" * 55)
    print(explain_clinician(ddgi_results, dosing_recs, phenotypes))

    print("=" * 55)
    print("👤 PATIENT EXPLANATION:")
    print("=" * 55)
    print(explain_patient(ddgi_results, dosing_recs, phenotypes))
