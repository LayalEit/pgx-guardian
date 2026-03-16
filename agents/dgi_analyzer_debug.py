import os
import json
import logging
from supabase import create_client
from dotenv import load_dotenv

load_dotenv()
supabase = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])

# ── Debug logger ──────────────────────────────────────────────────────────────
pgx_log = logging.getLogger("pgx.dgi")
if not pgx_log.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s [DGI] %(message)s", "%H:%M:%S"))
    pgx_log.addHandler(handler)
    pgx_log.setLevel(logging.DEBUG)

def analyze_dgi(phenotypes: dict, drug_names: list) -> list:
    pgx_log.info("=" * 60)
    pgx_log.info(f"ANALYZE_DGI called")
    pgx_log.info(f"  Drugs:      {drug_names}")
    pgx_log.info(f"  Phenotypes: {json.dumps(phenotypes)}")
    pgx_log.info("=" * 60)

    alerts = []

    for drug in drug_names:
        pgx_log.debug(f"[{drug}] Querying mechanism_knowledge_base...")
        mech_result = supabase.table("mechanism_knowledge_base") \
            .select("*").eq("drug_name", drug.lower()).execute()

        if not mech_result.data:
            pgx_log.warning(f"[{drug}] NO ROWS in mechanism_knowledge_base — not indexed or wrong name")
            continue

        pgx_log.debug(f"[{drug}] {len(mech_result.data)} mechanism rows:")
        for row in mech_result.data:
            pgx_log.debug(f"         gene={row['gene']} relationship={row['relationship']} strength={row['strength']}")

        for row in mech_result.data:
            gene = row["gene"]
            if gene not in phenotypes:
                pgx_log.debug(f"[{drug}] {gene} — no patient phenotype, skipping")
                continue

            patient_phenotype = phenotypes[gene]["phenotype"]
            diplotype = phenotypes[gene]["diplotype"]

            cpic_result = supabase.table("cpic_cache") \
                .select("*").eq("gene", gene).eq("drug_name", drug.lower()).execute()

            sev = _phenotype_to_severity(patient_phenotype, row["relationship"])

            if cpic_result.data:
                cpic_row = cpic_result.data[0]
                pgx_log.info(f"[{drug}+{gene}] CPIC HIT | {patient_phenotype} | rel={row['relationship']} | sev={sev} | inhibitor_ctx={cpic_row['cpic_includes_inhibitor_context']}")
                pgx_log.info(f"           rec: {cpic_row['recommendation']}")
                alerts.append({
                    "drug": drug, "gene": gene, "diplotype": diplotype,
                    "phenotype": patient_phenotype,
                    "mechanism_type": row["mechanism_type"],
                    "relationship": row["relationship"],
                    "strength": row["strength"],
                    "recommendation": cpic_row["recommendation"],
                    "cpic_includes_inhibitor_context": cpic_row["cpic_includes_inhibitor_context"],
                    "severity": sev, "source": "CPIC"
                })
            else:
                pgx_log.warning(f"[{drug}+{gene}] NO CPIC ENTRY | {patient_phenotype} | rel={row['relationship']} | sev={sev}")
                alerts.append({
                    "drug": drug, "gene": gene, "diplotype": diplotype,
                    "phenotype": patient_phenotype,
                    "mechanism_type": row["mechanism_type"],
                    "relationship": row["relationship"],
                    "strength": row["strength"],
                    "recommendation": "No CPIC guideline available. Use clinical judgment.",
                    "cpic_includes_inhibitor_context": False,
                    "severity": sev, "source": "mechanism_kb_only"
                })

    pgx_log.info(f"ANALYZE_DGI done — {len(alerts)} alerts:")
    for a in alerts:
        pgx_log.info(f"  => {a['drug']}+{a['gene']} | {a['phenotype']} | sev={a['severity']} | {a['source']}")
    return alerts


def _phenotype_to_severity(phenotype: str, relationship: str) -> int:
    if relationship == "substrate":
        return {"Poor Metabolizer": 5, "Intermediate Metabolizer": 4,
                "Ultra-Rapid Metabolizer": 4, "Normal Metabolizer": 1,
                "Rapid Metabolizer": 2, "Unknown": 2}.get(phenotype, 2)
    elif relationship in ("inhibitor", "inducer"):
        return {"strong": 4, "moderate": 3, "weak": 2}.get("moderate", 3)
    elif relationship == "hypersensitivity_marker":
        return 5
    return 2
