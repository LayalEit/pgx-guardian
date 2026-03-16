import sys
import os
sys.path.insert(0, ".")

import streamlit as st
from agents.drug_list_agent import normalize_drug_list
from agents.genotype_parser import run_parser
from agents.dgi_analyzer import analyze_dgi
from agents.ddi_checker import check_ddgi, SEVERITY_LABELS
from agents.ddi_loader import load_ddinter
from agents.dgidb_loader import load_dgidb
from agents.dosing_advisor import get_dosing_recommendations
from agents.explainer import explain_clinician, explain_patient

# ── Page config ──────────────────────────────────────────
st.set_page_config(
    page_title="PGx-Guardian",
    page_icon="🧬",
    layout="wide"
)

# ── Load data once at startup ─────────────────────────────
@st.cache_resource
def load_data():
    load_ddinter("data/ddinter")
    load_dgidb("data/dgidb/interactions.tsv")
    return True

load_data()

# ── Header ────────────────────────────────────────────────
st.title("🧬 PGx-Guardian")
st.caption("Pharmacogenomics Decision Support System for Polypharmacy Safety")
st.divider()

# ── Sidebar ───────────────────────────────────────────────
with st.sidebar:
    st.header("⚙️ Settings")
    mode = st.radio("Report mode", ["🩺 Clinician", "👤 Patient"])
    st.divider()
    st.caption("Data sources: CPIC · DDInter · DGIdb · PharmCAT")
    st.caption("v2.2 — DrugBank-free stack")

# ── Input section ─────────────────────────────────────────
col1, col2 = st.columns(2)

with col1:
    st.subheader("🧬 Genotype Input")
    genotype_file = st.file_uploader(
        "Upload genotype file (.csv or .json)",
        type=["csv", "json"]
    )
    st.caption("CSV format: columns `gene` and `diplotype`")

with col2:
    st.subheader("💊 Medications")
    meds_input = st.text_area(
        "Enter medications (one per line)",
        placeholder="Plavix\nomeprazole\ncodeine\nProzac",
        height=150
    )

# ── Run button ────────────────────────────────────────────
run = st.button("🔍 Analyze", type="primary", use_container_width=True)

if run:
    # Validate inputs
    if not genotype_file:
        st.error("Please upload a genotype file.")
        st.stop()
    if not meds_input.strip():
        st.error("Please enter at least one medication.")
        st.stop()

    # Save uploaded file temporarily
    tmp_path = f"/tmp/{genotype_file.name}"
    with open(tmp_path, "wb") as f:
        f.write(genotype_file.read())

    raw_meds = [m.strip() for m in meds_input.strip().split("\n") if m.strip()]

    with st.spinner("Analyzing..."):
        import time
        start = time.time()

        # Run pipeline
        genotype_result = run_parser(tmp_path)
        phenotypes = genotype_result["phenotypes"]
        normalized = normalize_drug_list(raw_meds)
        drug_names = [d["normalized"] for d in normalized]
        dgi_alerts = analyze_dgi(phenotypes, drug_names)
        ddgi_results = check_ddgi(drug_names, dgi_alerts, phenotypes)
        dosing_recs = get_dosing_recommendations(phenotypes, drug_names)

        # Sort by severity
        severity_order = {"CRITICAL": 0, "HIGH": 1, "MODERATE": 2, "LOW": 3}
        ddgi_results.sort(key=lambda x: severity_order.get(x["severity"], 4))

        elapsed = round(time.time() - start, 2)

    st.success(f"Analysis complete in {elapsed}s")
    st.divider()

    # ── Results ───────────────────────────────────────────
    # Phenotypes
    st.subheader("🧬 Patient Phenotypes")
    pcols = st.columns(len(phenotypes))
    for i, (gene, info) in enumerate(phenotypes.items()):
        with pcols[i]:
            phenotype = info["phenotype"]
            color = "🔴" if "Poor" in phenotype else "🟡" if "Intermediate" in phenotype else "🟢"
            st.metric(label=gene, value=phenotype, delta=info["diplotype"])

    st.divider()

    # DDGI Interactions
    st.subheader("⚠️ Drug Interactions (sorted by severity)")

    critical = [r for r in ddgi_results if r["severity"] == "CRITICAL"]
    high     = [r for r in ddgi_results if r["severity"] == "HIGH"]
    moderate = [r for r in ddgi_results if r["severity"] == "MODERATE"]
    low      = [r for r in ddgi_results if r["severity"] == "LOW"]

    for r in critical:
        with st.expander(f"🔴 CRITICAL: {r['drug_a'].upper()} + {r['drug_b'].upper()} (score: {r['compound_score']})", expanded=True):
            st.error(r.get("recommendation", "Avoid combination."))
            st.caption(r["escalation_note"])

    for r in high:
        with st.expander(f"🟠 HIGH: {r['drug_a'].upper()} + {r['drug_b'].upper()} (score: {r['compound_score']})", expanded=True):
            st.warning(r.get("recommendation", "Use with caution."))
            st.caption(r["escalation_note"])

    for r in moderate:
        with st.expander(f"🟡 MODERATE: {r['drug_a'].upper()} + {r['drug_b'].upper()} (score: {r['compound_score']})", expanded=False):
            st.warning(r.get("recommendation", "Monitor closely."))
            st.caption(r["escalation_note"])

    for r in low:
        with st.expander(f"🟢 LOW: {r['drug_a'].upper()} + {r['drug_b'].upper()} (score: {r['compound_score']})", expanded=False):
            st.info(r.get("recommendation", "No action required."))

    st.divider()

    # Dosing recommendations
    if dosing_recs:
        st.subheader("💊 Dosing Recommendations")
        for r in dosing_recs:
            action_color = "🔴" if r["action"] == "AVOID" else "🟡" if "REDUCE" in r["action"] else "🟠"
            with st.expander(f"{action_color} {r['action']}: {r['drug'].upper()} ({r['gene']} {r['phenotype']})", expanded=True):
                st.write(f"**Reason:** {r['reason']}")
                if r["alternatives"]:
                    st.write(f"**Alternatives:** {', '.join(r['alternatives'])}")
                st.caption(f"Source: {r['guideline']} [{r['evidence']}]")

    st.divider()

    # AI Explanation
    st.subheader("🤖 AI-Generated Explanation")
    with st.spinner("Generating explanation..."):
        if "Clinician" in mode:
            explanation = explain_clinician(ddgi_results, dosing_recs, phenotypes)
        else:
            explanation = explain_patient(ddgi_results, dosing_recs, phenotypes)

    st.info(explanation)

    st.divider()

    # Ethics footer
    st.caption("⚖️ This system does not prescribe. All outputs require clinician review. No PHI is stored. Data sources: CPIC, DDInter, DGIdb.")
    st.caption("CPIC guidelines version: 2024 | DDInter v2.0 | DGIdb 2024-Dec")
