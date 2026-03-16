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
logging.basicConfig(level=logging.DEBUG,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s", datefmt="%H:%M:%S")
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
    genotypes: free-form e.g. "CYP2D6: *4/*4, CYP2C19: *2/*2"
    """
    pgx_log.info(f"TOOL CALLED | meds={medications!r} | geno={genotypes!r}")

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

    drug_list = [d.strip() for d in medications.split(",")]
    normalized = normalize_drug_list(drug_list)
    drug_names = [d["normalized"] for d in normalized]
    pgx_log.info(f"  drugs={drug_names} | phenotypes={json.dumps(phenotypes)}")

    dgi_alerts  = analyze_dgi(phenotypes, drug_names)
    ddgi_results = check_ddgi(drug_names, dgi_alerts, phenotypes)
    dosing_recs  = get_dosing_recommendations(phenotypes, drug_names)

    severity_order = {"CRITICAL": 0, "HIGH": 1, "MODERATE": 2, "LOW": 3}
    ddgi_results.sort(key=lambda x: severity_order.get(x["severity"], 4))
    dgi_sorted = sorted(dgi_alerts, key=lambda x: x["severity"], reverse=True)

    lines = []
    lines.append(f"I have analyzed {len(drug_names)} medications.")

    # Phenotype summary
    abnormal = [(g, i["phenotype"]) for g, i in phenotypes.items()
                if i["phenotype"] not in ("Normal Metabolizer", "Normal Function", "Unknown")]
    if abnormal:
        lines.append("Actionable genetic variants: " +
                     ", ".join([f"{g} {p}" for g, p in abnormal]))

    # ── SECTION 1: Gene-drug findings (primary — these drive clinical decisions)
    # Severity 5 = CRITICAL gene-drug (PM/URM substrate or hypersensitivity)
    # Severity 4 = HIGH gene-drug (IM substrate)
    critical_dgi = [a for a in dgi_sorted if a["severity"] >= 5
                    and a["phenotype"] not in ("Normal Metabolizer","Normal Function","Unknown")]
    high_dgi     = [a for a in dgi_sorted if a["severity"] == 4
                    and a["phenotype"] not in ("Normal Metabolizer","Normal Function","Unknown")]

    if critical_dgi:
        for a in critical_dgi:
            lines.append(
                f"CRITICAL GENE-DRUG INTERACTION: {a['drug'].capitalize()} with {a['gene']} "
                f"{a['phenotype']}. {a['recommendation']}"
            )
    if high_dgi:
        for a in high_dgi:
            lines.append(
                f"HIGH GENE-DRUG INTERACTION: {a['drug'].capitalize()} with {a['gene']} "
                f"{a['phenotype']}. {a['recommendation']}"
            )

    # ── SECTION 2: Drug-drug pairs that are CRITICAL or HIGH after genetic escalation
    # Only report a pair if its PRIMARY driver is the drug-drug interaction,
    # not just the gene-drug finding we already reported above.
    # Deduplicate: skip pairs whose recommendation duplicates a gene-drug alert already shown.
    already_reported_drugs = {a["drug"] for a in critical_dgi + high_dgi}

    critical_ddgi = [r for r in ddgi_results if r["severity"] == "CRITICAL"
                     and not (r["drug_a"] in already_reported_drugs
                              and r["drug_b"] in already_reported_drugs)]
    high_ddgi     = [r for r in ddgi_results if r["severity"] == "HIGH"
                     and not (r["drug_a"] in already_reported_drugs
                              and r["drug_b"] in already_reported_drugs)]

    if critical_ddgi:
        lines.append("CRITICAL DRUG-DRUG INTERACTIONS:")
        for r in critical_ddgi:
            lines.append(
                f"{r['drug_a'].capitalize()} combined with {r['drug_b']} — "
                f"genetically escalated by {r.get('gene','')} {r.get('phenotype','')}. "
                f"{r.get('recommendation','')}"
            )
    if high_ddgi:
        lines.append("HIGH DRUG-DRUG INTERACTIONS:")
        for r in high_ddgi:
            lines.append(
                f"{r['drug_a'].capitalize()} and {r['drug_b']}: "
                f"{r.get('recommendation','Use with caution.')}"
            )

    # ── SECTION 3: Dosing recommendations
    if dosing_recs:
        lines.append("Dosing recommendations: " +
                     ". ".join([f"{r['action']} {r['drug']}: {r['reason']}" for r in dosing_recs]))

    if not critical_dgi and not high_dgi and not critical_ddgi and not high_ddgi:
        lines.append("No critical or high severity interactions detected.")

    report_text = " ".join(lines)
    pgx_log.info(f"REPORT:\n{report_text}")

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
1. When a clinician describes a patient's medications and/or genetic variants, DO NOT immediately call analyze_medications.
2. First repeat back what you understood — list the specific medication names and genetic variants.
   Example: "I understood the patient is taking clopidogrel, codeine, omeprazole and fluoxetine, with CYP2C19 *2/*2 and CYP2D6 *4/*4. Is that correct?"
3. Wait for confirmation. Only call analyze_medications after the clinician confirms.
4. If corrected, acknowledge, repeat the updated list, and confirm again before calling.

REPORTING RESULTS — STRICT ORDER:
1. Report CRITICAL GENE-DRUG INTERACTIONS first — these are the primary clinical concern.
2. Then HIGH GENE-DRUG INTERACTIONS.
3. Then any CRITICAL or HIGH DRUG-DRUG INTERACTIONS.
4. Then dosing recommendations.
- Do NOT reframe a gene-drug finding as a drug-drug interaction.
- Do NOT say "codeine combined with fluoxetine is critical" when the primary cause is CYP2D6 Poor Metabolizer status.
- Always end with: 'This report requires physician review before any changes are made.'
- Keep responses concise for spoken delivery.
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
