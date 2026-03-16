import os
import re
import sys
sys.path.insert(0, ".")
from dotenv import load_dotenv
from google.adk.agents import Agent
from google.adk.agents.run_config import RunConfig, StreamingMode, ToolThreadPoolConfig
from google.adk.sessions import InMemorySessionService
from google.adk.runners import Runner
from google.genai import types

load_dotenv()

from agents.ddi_loader import load_ddinter
from agents.dgidb_loader import load_dgidb
from agents.drug_list_agent import normalize_drug_list
from agents.dgi_analyzer import analyze_dgi
from agents.ddi_checker import check_ddgi
from agents.dosing_advisor import get_dosing_recommendations
from agents.genotype_parser import PHENOTYPE_MAP

print("🔄 Loading PGx data...")
load_ddinter("data/ddinter")
load_dgidb("data/dgidb/interactions.tsv")
print("✅ Data ready\n")

def analyze_medications(medications: str, genotypes: str = "") -> str:
    """
    Analyze medications against a patient pharmacogenomic profile.
    medications: comma-separated drug names
    genotypes: free-form e.g. "CYP2D6: *4/*4, TPMT: *3A/*3A" or just "TP53, BRCA1"
    """
    phenotypes = {}
    all_mentioned_genes = set()  # Track ALL gene names mentioned, even without diplotypes

    if genotypes:
        # First, try to match structured genotypes (GENE: diplotype)
        pat = r"([A-Z][A-Z0-9a-z-]+)\s*:?\s*(\*[\w:]+(?:/\*[\w:]+)?|[A-Z][A-Za-z-]+/[A-Z][A-Za-z-]+)"
        for m in re.finditer(pat, genotypes):
            gene, diplotype = m.group(1).upper(), m.group(2)
            phenotype = PHENOTYPE_MAP.get(gene, {}).get(diplotype, "Unknown")
            if phenotype == "Unknown":
                parts = diplotype.split("/")
                if len(parts) == 2:
                    phenotype = PHENOTYPE_MAP.get(gene, {}).get(parts[1] + "/" + parts[0], "Unknown")
            phenotypes[gene] = {"diplotype": diplotype, "phenotype": phenotype}
            all_mentioned_genes.add(gene)

        # Also capture any bare gene-like names (no diplotype required)
        # This catches cases where the agent passes "TP53" or "TP53, BRCA1" without alleles
        bare_genes = re.findall(r'\b([A-Z][A-Z0-9a-z-]{1,15})\b', genotypes.upper())
        for g in bare_genes:
            # Filter: must look like a gene name (has letters + numbers, or is a known pattern)
            if (any(c.isdigit() for c in g) or g.startswith("HLA") or
                g in ("DPYD", "TPMT", "VKORC", "NUDT", "SLCO", "BRCA", "EGFR", "BRAF", "KRAS", "MTHFR")):
                all_mentioned_genes.add(g)

    drug_list = [d.strip() for d in medications.split(",")]
    normalized = normalize_drug_list(drug_list)
    drug_names = [d["normalized"] for d in normalized]

    dgi_alerts = analyze_dgi(phenotypes, drug_names)
    ddgi_results = check_ddgi(drug_names, dgi_alerts, phenotypes)
    dosing_recs = get_dosing_recommendations(phenotypes, drug_names)

    severity_order = {"CRITICAL": 0, "HIGH": 1, "MODERATE": 2, "LOW": 3}
    ddgi_results.sort(key=lambda x: severity_order.get(x["severity"], 4))

    lines = []
    lines.append(f"I have analyzed {len(drug_names)} medications.")

    abnormal = [(g, i["phenotype"]) for g, i in phenotypes.items()
                if i["phenotype"] not in ("Normal Metabolizer", "Normal Function", "Unknown")]
    if abnormal:
        lines.append("Actionable genetic variants found: " +
                     ", ".join([f"{g} {p}" for g, p in abnormal]))

    critical = [r for r in ddgi_results if r["severity"] == "CRITICAL"]
    high     = [r for r in ddgi_results if r["severity"] == "HIGH"]

    if critical:
        lines.append(f"CRITICAL ALERT: {len(critical)} critical interactions detected.")
        for r in critical:
            lines.append(f"{r['drug_a']} combined with {r['drug_b']} is CRITICAL with score {r['compound_score']}. {r.get('recommendation', '')}")

    if high:
        lines.append(f"HIGH severity: {len(high)} interactions.")
        for r in high:
            lines.append(f"{r['drug_a']} and {r['drug_b']}: {r.get('recommendation', 'Use with caution.')}")

    if dosing_recs:
        lines.append("Dosing recommendations: " +
                     ". ".join([f"{r['action']} {r['drug']}: {r['reason']}" for r in dosing_recs]))

    if not critical and not high:
        lines.append("No critical or high severity interactions detected.")

    report_text = " ".join(lines)

    # Append structured metadata for the UI (server will strip this before display)
    import json as _json
    metadata = _json.dumps({
        "__pgx_meta__": True,
        "drugs": drug_names,
        "genes": list(all_mentioned_genes)
    })
    return f"{report_text} |||META|||{metadata}"

pgx_voice_agent = Agent(
    name="pgx_guardian_voice",
    model="gemini-2.5-flash-native-audio-latest",
    description="PGx-Guardian voice agent for pharmacogenomics safety alerts",
    instruction="""You are PGx-Guardian, a clinical pharmacogenomics voice assistant.
You help clinicians identify dangerous drug-gene and drug-drug interactions in real time.

WORKFLOW:
1. When a clinician describes a patient's medications and/or genetic variants, DO NOT immediately call the analyze_medications tool.
2. First, repeat back what you understood: list the specific medication names and any genetic variants or gene names you heard.
   Example: "I understood the patient is taking paracetamol and ibuprofen, with mutations in TP53 and BRCA1. Is that correct?"
3. Wait for the clinician to confirm or correct. If they say yes/correct/that's right, THEN call analyze_medications.
4. If the clinician corrects you (e.g. "no, not ibuprofen, it's omeprazole"), acknowledge the correction, repeat the updated list, and confirm again.
5. If the clinician adds more medications in a follow-up message, confirm the addition before re-analyzing.

RESPONSE STYLE:
- Speak clearly and calmly.
- Prioritize critical alerts first when presenting results.
- Always end analysis results with: 'This report requires physician review before any changes are made.'
- If interrupted, stop speaking and listen.
- Keep responses concise when spoken aloud.
- Always use the exact drug names (e.g. "paracetamol" not "the medication") when confirming.
""",
    tools=[analyze_medications],
)

APP_NAME = "pgx_guardian"
session_service = InMemorySessionService()

async def run_voice_agent():
    runner = Runner(
        app_name=APP_NAME,
        agent=pgx_voice_agent,
        session_service=session_service,
    )
    session = await session_service.create_session(
        app_name=APP_NAME,
        user_id="clinician_1"
    )
    run_config = RunConfig(
        streaming_mode=StreamingMode.BIDI,
        response_modalities=["AUDIO"],
        speech_config=types.SpeechConfig(
            voice_config=types.VoiceConfig(
                prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name="Aoede")
            )
        ),
        tool_thread_pool_config=ToolThreadPoolConfig(),
    )
    print("✅ PGx-Guardian Voice Agent initialized successfully")
    return runner, session, run_config

if __name__ == "__main__":
    import asyncio
    asyncio.run(run_voice_agent())
