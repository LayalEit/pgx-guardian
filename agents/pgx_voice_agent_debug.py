"""
pgx_voice_agent.py — with full debug logging + fixed output formatting.

Key fixes vs original:
1. Gene-drug (DGI) alerts reported SEPARATELY and FIRST, before drug-drug pairs
2. CRITICAL gene-drug interactions are not buried inside DDI pair framing
3. omeprazole+clopidogrel miss: now explicitly logs when a DDI pair has no DDInter entry
4. Full structured logging of every DB query and scoring step
"""
import os
import re
import sys
import json
import logging
sys.path.insert(0, ".")
from dotenv import load_dotenv
from google.adk.agents import Agent
from google.adk.agents.run_config import RunConfig, StreamingMode, ToolThreadPoolConfig
from google.adk.sessions import InMemorySessionService
from google.adk.runners import Runner
from google.genai import types

load_dotenv()

# ── Root PGx logger — prints to terminal so you can see everything ──────────
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S"
)
pgx_log = logging.getLogger("pgx.agent")

from agents.ddi_loader import load_ddinter
from agents.dgidb_loader import load_dgidb
from agents.drug_list_agent import normalize_drug_list
from agents.dgi_analyzer import analyze_dgi
from agents.ddi_checker import check_ddgi
from agents.dosing_advisor import get_dosing_recommendations
from agents.genotype_parser import PHENOTYPE_MAP

pgx_log.info("Loading PGx data...")
load_ddinter("data/ddinter")
load_dgidb("data/dgidb/interactions.tsv")
pgx_log.info("Data ready")

def analyze_medications(medications: str, genotypes: str = "") -> str:
    """
    Analyze medications against a patient pharmacogenomic profile.
    medications: comma-separated drug names
    genotypes: free-form e.g. "CYP2D6: *4/*4, TPMT: *3A/*3A"
    """
    pgx_log.info("▶▶▶ analyze_medications TOOL CALLED")
    pgx_log.info(f"    raw medications: {medications!r}")
    pgx_log.info(f"    raw genotypes:   {genotypes!r}")

    phenotypes = {}
    all_mentioned_genes = set()

    if genotypes:
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

        bare_genes = re.findall(r'\b([A-Z][A-Z0-9a-z-]{1,15})\b', genotypes.upper())
        for g in bare_genes:
            if (any(c.isdigit() for c in g) or g.startswith("HLA") or
                g in ("DPYD","TPMT","VKORC","NUDT","SLCO","BRCA","EGFR","BRAF","KRAS","MTHFR")):
                all_mentioned_genes.add(g)

    pgx_log.info(f"    parsed phenotypes: {json.dumps(phenotypes)}")
    pgx_log.info(f"    all_mentioned_genes: {all_mentioned_genes}")

    drug_list = [d.strip() for d in medications.split(",")]
    normalized = normalize_drug_list(drug_list)
    drug_names = [d["normalized"] for d in normalized]
    pgx_log.info(f"    normalized drugs: {drug_names}")

    # ── Run pipeline ──────────────────────────────────────────────────────────
    dgi_alerts = analyze_dgi(phenotypes, drug_names)
    ddgi_results = check_ddgi(drug_names, dgi_alerts, phenotypes)
    dosing_recs = get_dosing_recommendations(phenotypes, drug_names)

    # ── Sort ddgi by severity ─────────────────────────────────────────────────
    severity_order = {"CRITICAL": 0, "HIGH": 1, "MODERATE": 2, "LOW": 3}
    ddgi_results.sort(key=lambda x: severity_order.get(x["severity"], 4))

    # ── Also sort DGI alerts by severity score (descending) ──────────────────
    dgi_alerts_sorted = sorted(dgi_alerts, key=lambda x: x["severity"], reverse=True)

    pgx_log.info(f"    DGI alerts: {len(dgi_alerts_sorted)}, DDGI pairs: {len(ddgi_results)}, Dosing recs: {len(dosing_recs)}")

    # ── Build output — FIXED format ───────────────────────────────────────────
    lines = []
    lines.append(f"I have analyzed {len(drug_names)} medications.")

    # 1. Gene phenotype summary
    abnormal = [(g, i["phenotype"]) for g, i in phenotypes.items()
                if i["phenotype"] not in ("Normal Metabolizer", "Normal Function", "Unknown")]
    if abnormal:
        lines.append("Actionable genetic variants: " +
                     ", ".join([f"{g} {p}" for g, p in abnormal]))

    # 2. CRITICAL gene-drug alerts FIRST (these are more clinically actionable than DDI pairs)
    critical_dgi = [a for a in dgi_alerts_sorted
                    if a["severity"] >= 5 and a["phenotype"] not in ("Normal Metabolizer","Normal Function")]
    if critical_dgi:
        for a in critical_dgi:
            lines.append(f"CRITICAL GENE-DRUG: {a['drug']} cannot be used safely with {a['gene']} {a['phenotype']}. "
                         f"{a['recommendation']}")

    # 3. HIGH gene-drug alerts
    high_dgi = [a for a in dgi_alerts_sorted
                if a["severity"] == 4 and a["phenotype"] not in ("Normal Metabolizer","Normal Function")]
    if high_dgi:
        for a in high_dgi:
            lines.append(f"HIGH GENE-DRUG: {a['drug']} with {a['gene']} {a['phenotype']}. {a['recommendation']}")

    # 4. Drug-drug interaction pairs (DDGI)
    critical_ddgi = [r for r in ddgi_results if r["severity"] == "CRITICAL"]
    high_ddgi     = [r for r in ddgi_results if r["severity"] == "HIGH"]

    if critical_ddgi:
        lines.append(f"CRITICAL DRUG INTERACTIONS:")
        for r in critical_ddgi:
            lines.append(f"{r['drug_a']} combined with {r['drug_b']} — score {r['compound_score']}. "
                         f"Escalated by {r.get('gene','unknown')} {r.get('phenotype','')}. "
                         f"{r.get('recommendation','')}")

    if high_ddgi:
        lines.append(f"HIGH DRUG INTERACTIONS:")
        for r in high_ddgi:
            lines.append(f"{r['drug_a']} and {r['drug_b']}: {r.get('recommendation','Use with caution.')}")

    # 5. Dosing recommendations
    if dosing_recs:
        lines.append("Dosing recommendations: " +
                     ". ".join([f"{r['action']} {r['drug']}: {r['reason']}" for r in dosing_recs]))

    if not critical_dgi and not high_dgi and not critical_ddgi and not high_ddgi:
        lines.append("No critical or high severity interactions detected.")

    report_text = " ".join(lines)
    pgx_log.info(f"    REPORT TEXT:\n{report_text}\n")

    metadata = json.dumps({
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
4. If the clinician corrects you, acknowledge, repeat the updated list, and confirm again before calling the tool.
5. If the clinician adds more medications in a follow-up, confirm the addition before re-analyzing.

RESPONSE STYLE:
- Speak clearly and calmly.
- When reporting results, present CRITICAL GENE-DRUG findings first — these are the primary clinical concern.
- CRITICAL DRUG INTERACTIONS and HIGH findings follow after.
- Do NOT reframe gene-drug findings as drug-drug interactions.
- Always end analysis with: 'This report requires physician review before any changes are made.'
- Keep responses concise when spoken aloud.
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
    pgx_log.info("PGx-Guardian Voice Agent initialized")
    return runner, session, run_config

if __name__ == "__main__":
    import asyncio
    asyncio.run(run_voice_agent())
