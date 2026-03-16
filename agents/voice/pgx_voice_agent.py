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

# ── All known CPIC genes — used to detect bare gene mentions ─────────────────
KNOWN_GENES = {
    "CYP2D6", "CYP2C19", "CYP2C9", "CYP3A5", "CYP3A4", "CYP2B6", "CYP1A2",
    "DPYD", "TPMT", "NUDT15", "SLCO1B1", "UGT1A1", "VKORC1",
    "HLA-B", "HLA-A", "G6PD", "RYR1", "CACNA1S", "IFNL3", "NAT2",
}

# ── Direct phenotype strings — bypass diplotype lookup entirely ───────────────
DIRECT_PHENOTYPES = {
    "poor metabolizer":                  "Poor Metabolizer",
    "intermediate metabolizer":          "Intermediate Metabolizer",
    "normal metabolizer":                "Normal Metabolizer",
    "ultrarapid metabolizer":            "Ultra-Rapid Metabolizer",
    "ultra-rapid metabolizer":           "Ultra-Rapid Metabolizer",
    "ultra rapid metabolizer":           "Ultra-Rapid Metabolizer",
    "rapid metabolizer":                 "Rapid Metabolizer",
    "normal function":                   "Normal Function",
    "decreased function":                "Decreased Function",
    "poor function":                     "Poor Function",
    "high warfarin sensitivity":         "High warfarin sensitivity",
    "intermediate warfarin sensitivity": "Intermediate warfarin sensitivity",
    "normal warfarin sensitivity":       "Normal warfarin sensitivity",
    "deficient":                         "Deficient",
    "malignant hyperthermia susceptible":"Malignant Hyperthermia Susceptible",
    "favorable response":                "Favorable Response (peginterferon)",
    "unfavorable response":              "Unfavorable Response",
    "intermediate response":             "Intermediate Response",
    # HLA-B: *57:01 abacavir, *15:02 carbamazepine, *58:01 allopurinol
    "abacavir hypersensitivity":              "Abacavir hypersensitivity — HIGH RISK",
    "abacavir risk":                          "Abacavir hypersensitivity — HIGH RISK",
    "abacavir positive":                      "Abacavir hypersensitivity — HIGH RISK",
    "hla-b positive":                         "Abacavir hypersensitivity — HIGH RISK",
    "hla b positive":                         "Abacavir hypersensitivity — HIGH RISK",
    "hla-b 57":                               "Abacavir hypersensitivity — HIGH RISK",
    "hla b 57":                               "Abacavir hypersensitivity — HIGH RISK",
    "carbamazepine hypersensitivity":          "Carbamazepine SJS/TEN — HIGH RISK",
    "carbamazepine skin risk":                 "Carbamazepine SJS/TEN — HIGH RISK",
    "hla-b 15":                               "Carbamazepine SJS/TEN — HIGH RISK",
    "hla b 15":                               "Carbamazepine SJS/TEN — HIGH RISK",
    "allopurinol hypersensitivity":            "Allopurinol SJS/TEN — HIGH RISK",
    "allopurinol risk":                        "Allopurinol SJS/TEN — HIGH RISK",
    "hla-b 58":                               "Allopurinol SJS/TEN — HIGH RISK",
    "hla b 58":                               "Allopurinol SJS/TEN — HIGH RISK",
    # HLA-A: *31:01 carbamazepine DRESS
    "hla-a positive":                         "Carbamazepine DRESS — HIGH RISK",
    "hla a positive":                         "Carbamazepine DRESS — HIGH RISK",
    "carbamazepine dress":                     "Carbamazepine DRESS — HIGH RISK",
    "dress risk":                              "Carbamazepine DRESS — HIGH RISK",
    # G6PD shortcuts — ASR often mangles "G6PD" to "G 6 P D"
    "g6pd deficient":                         "Deficient",
    "g6pd deficiency":                        "Deficient",
    "glucose 6 phosphate deficient":           "Deficient",
    "glucose-6-phosphate deficient":           "Deficient",
    # RYR1/CACNA1S malignant hyperthermia
    "malignant hyperthermia":                  "Malignant Hyperthermia Susceptible",
    "mh susceptible":                          "Malignant Hyperthermia Susceptible",
    "malignant hyperthermia susceptible":      "Malignant Hyperthermia Susceptible",
}

# ── Non-star-allele notation maps merged into PHENOTYPE_MAP at startup ────────

# VKORC1: SNP-based (-1639G>A) or nucleotide genotype (GG/GA/AA)
VKORC1_MAP = {
    "GG":       "Normal warfarin sensitivity",
    "GA":       "Intermediate warfarin sensitivity",
    "AG":       "Intermediate warfarin sensitivity",
    "AA":       "High warfarin sensitivity",
    "-1639GG":  "Normal warfarin sensitivity",
    "-1639GA":  "Intermediate warfarin sensitivity",
    "-1639AG":  "Intermediate warfarin sensitivity",
    "-1639AA":  "High warfarin sensitivity",
    "G/G":      "Normal warfarin sensitivity",
    "G/A":      "Intermediate warfarin sensitivity",
    "A/G":      "Intermediate warfarin sensitivity",
    "A/A":      "High warfarin sensitivity",
}

# IFNL3 (IL28B): rs12979860 genotype — peginterferon response
IFNL3_MAP = {
    "CC":  "Favorable Response (peginterferon)",
    "CT":  "Intermediate Response",
    "TC":  "Intermediate Response",
    "TT":  "Unfavorable Response",
    "C/C": "Favorable Response (peginterferon)",
    "C/T": "Intermediate Response",
    "T/C": "Intermediate Response",
    "T/T": "Unfavorable Response",
}

# CYP3A4: limited CPIC coverage, common alleles only
CYP3A4_MAP = {
    "*1/*1":   "Normal Metabolizer",
    "*1/*22":  "Intermediate Metabolizer",
    "*22/*22": "Poor Metabolizer",
    "*1/*20":  "Ultra-Rapid Metabolizer",
}

# CYP1A2: limited CPIC coverage
CYP1A2_MAP = {
    "*1A/*1A": "Normal Metabolizer",
    "*1F/*1F": "Ultra-Rapid Metabolizer",
    "*1A/*1F": "Rapid Metabolizer",
    "*1/*1":   "Normal Metabolizer",
}

# Merge all into PHENOTYPE_MAP at runtime
PHENOTYPE_MAP.setdefault("VKORC1", {}).update(VKORC1_MAP)
PHENOTYPE_MAP.setdefault("IFNL3", {}).update(IFNL3_MAP)
PHENOTYPE_MAP.setdefault("CYP3A4", {}).update(CYP3A4_MAP)
PHENOTYPE_MAP.setdefault("CYP1A2", {}).update(CYP1A2_MAP)

# ── Spoken number normalization ───────────────────────────────────────────────
SPOKEN_ORDINALS = {
    "first":"1","second":"2","third":"3","fourth":"4","fifth":"5",
    "sixth":"6","seventh":"7","eighth":"8","ninth":"9","tenth":"10",
    "one":"1","two":"2","three":"3","four":"4","five":"5",
    "six":"6","seven":"7","eight":"8","nine":"9","ten":"10",
}


def _normalize_genotype_input(raw: str) -> str:
    """
    Normalize spoken or free-form genotype strings into structured format.

    Handles:
      Star alleles (spoken):
        "star four star four"           -> "*4/*4"
        "CYP2D6 four four"              -> "CYP2D6: *4/*4"
        "CYP2D6 *4 *4"                  -> "CYP2D6: *4/*4"
        "third allele"                  -> "*3"

      Phenotype-direct:
        "poor metabolizer for CYP2D6"   -> "CYP2D6: Poor Metabolizer"
        "CYP2D6 poor metabolizer"       -> "CYP2D6: Poor Metabolizer"
        "TPMT deficient"                -> "TPMT: Poor Metabolizer"

      SNP/nucleotide notation:
        "VKORC1 AA"                     -> "VKORC1: AA"
        "VKORC1 -1639AA"                -> "VKORC1: -1639AA"
        "VKORC1 G/A"                    -> "VKORC1: GA"
        "IFNL3 CC" or "IL28B CC"        -> "IFNL3: CC"

      Already structured:
        "CYP2D6: *4/*4"                 -> unchanged
    """
    g = raw.strip()

    # IL28B is an alias for IFNL3 — normalize
    g = re.sub(r'\bIL28B\b', 'IFNL3', g, flags=re.IGNORECASE)

    # G6PD: ASR often produces "G 6 P D" or "G six PD" — normalize to G6PD
    g = re.sub(r'\bG\s*6\s*P\s*D\b', 'G6PD', g, flags=re.IGNORECASE)
    g = re.sub(r'\bglucose.?6.?phosphate.?dehydrogenase\b', 'G6PD', g, flags=re.IGNORECASE)

    # HLA: ASR sometimes produces "H L A B" or "H-L-A-B" — normalize
    g = re.sub(r'\bH\s*[\-\s]?L\s*[\-\s]?A\s*[\-\s]?B\b', 'HLA-B', g, flags=re.IGNORECASE)
    g = re.sub(r'\bH\s*[\-\s]?L\s*[\-\s]?A\s*[\-\s]?A\b', 'HLA-A', g, flags=re.IGNORECASE)

    # SLCO1B1: ASR might say "SLCO one B one" or "SLC O 1 B 1"
    g = re.sub(r'\bSLC\s*O\s*1\s*B\s*1\b', 'SLCO1B1', g, flags=re.IGNORECASE)
    g = re.sub(r'\bSLCO\s+1\s*B\s*1\b', 'SLCO1B1', g, flags=re.IGNORECASE)

    # NUDT15: ASR might say "newt 15" or "N U D T 15"
    g = re.sub(r'\b(?:nudt|n\s*u\s*d\s*t)\s*15\b', 'NUDT15', g, flags=re.IGNORECASE)

    # UGT1A1: "U G T one A one" or "UGT 1A1"
    g = re.sub(r'\bUGT\s*1\s*A\s*1\b', 'UGT1A1', g, flags=re.IGNORECASE)

    # CYP2C9 vs CYP2C19 — ASR sometimes confuses them; 
    # "CYP2C19" must be checked before "CYP2C9" to avoid partial match
    # (already handled by regex ordering in parsing, but normalize spacing)
    g = re.sub(r'\bCYP\s*2\s*C\s*19\b', 'CYP2C19', g, flags=re.IGNORECASE)
    g = re.sub(r'\bCYP\s*2\s*C\s*9\b',  'CYP2C9',  g, flags=re.IGNORECASE)
    g = re.sub(r'\bCYP\s*2\s*D\s*6\b',  'CYP2D6',  g, flags=re.IGNORECASE)
    g = re.sub(r'\bCYP\s*2\s*B\s*6\b',  'CYP2B6',  g, flags=re.IGNORECASE)
    g = re.sub(r'\bCYP\s*3\s*A\s*5\b',  'CYP3A5',  g, flags=re.IGNORECASE)
    g = re.sub(r'\bCYP\s*3\s*A\s*4\b',  'CYP3A4',  g, flags=re.IGNORECASE)

    # Convert ordinals FIRST: "third" -> "3", "four" -> "4"
    for word, num in SPOKEN_ORDINALS.items():
        g = re.sub(rf'\b{word}\b', num, g, flags=re.IGNORECASE)

    # "star N" -> "*N"  (after ordinals so "star four" -> "star 4" -> "*4")
    g = re.sub(r'\bstar\s+(\w+)', r'*\1', g, flags=re.IGNORECASE)

    # "N allele" or "allele N" -> "*N"  (so "third allele" -> "3 allele" -> "*3")
    g = re.sub(r'\b(\d+)\s+allele\b', r'*\1', g, flags=re.IGNORECASE)
    g = re.sub(r'\ballele\s+(\d+)\b', r'*\1', g, flags=re.IGNORECASE)

    # Standalone "*N *N" (no slash) -> "*N/*N"
    g = re.sub(r'(?<![A-Za-z:3])(\*\w+)\s+(\*\w+)', r'\1/\2', g)

    # Phenotype-first phrasing
    metabolizer_types = [
        (r'ultra.?rapid\s+metabolizer', 'Ultra-Rapid Metabolizer'),
        (r'rapid\s+metabolizer',         'Rapid Metabolizer'),
        (r'poor\s+metabolizer',          'Poor Metabolizer'),
        (r'intermediate\s+metabolizer',  'Intermediate Metabolizer'),
        (r'normal\s+metabolizer',        'Normal Metabolizer'),
        (r'poor\s+function',             'Poor Function'),
        (r'decreased\s+function',        'Decreased Function'),
        (r'normal\s+function',           'Normal Function'),
        (r'deficient',                   'Poor Metabolizer'),
    ]
    for pattern, label in metabolizer_types:
        # "phenotype for GENE" or "phenotype GENE"
        g = re.sub(
            rf'{pattern}\s+(?:for\s+)?([A-Z][A-Z0-9a-z-]+)',
            lambda m, l=label: f"{m.group(1).upper()}: {l}",
            g, flags=re.IGNORECASE
        )
        # "GENE phenotype"
        g = re.sub(
            rf'([A-Z][A-Z0-9a-z-]+)\s+{pattern}',
            lambda m, l=label: f"{m.group(1).upper()}: {l}",
            g, flags=re.IGNORECASE
        )

    # HLA genes: normalize bare allele numbers to star notation
    # "HLA-B 57:01" or "HLA-B 57" -> "HLA-B: *57:01/*57:01"
    # "HLA-B 57:01/57:01" -> "HLA-B: *57:01/*57:01"
    def _normalize_hla_allele(allele: str) -> str:
        """Ensure allele has star prefix and colon-padded sub-allele."""
        allele = allele.strip()
        if not allele.startswith("*"):
            allele = "*" + allele
        # If no colon after the star, add :01 (e.g. *57 -> *57:01)
        if re.match(r'^\*\d+$', allele):
            allele = allele + ":01"
        return allele

    for hla_gene in ("HLA-B", "HLA-A"):
        # Match "HLA-B: 57:01", "HLA-B 57", "HLA-B *57:01", "HLA-B 57:01/57:01"
        def _hla_repl(m, gene=hla_gene):
            raw = m.group(1).strip().replace(" ", "")
            parts = raw.split("/")
            if len(parts) == 2:
                a1 = _normalize_hla_allele(parts[0])
                a2 = _normalize_hla_allele(parts[1])
            else:
                a1 = _normalize_hla_allele(parts[0])
                a2 = a1
            return f"{gene}: {a1}/{a2}"
        # Skip if already fully structured (e.g. "HLA-B: *57:01/*57:01")
        if not re.search(rf'{hla_gene}\s*:\s*\*[\d]+:[\d]+/\*[\d]+:[\d]+', g, re.IGNORECASE):
            g = re.sub(
                rf'{hla_gene}\s*:?\s*(\*{{0,1}}[\d][\d:./\w]*(?:/\*{{0,1}}[\d][\d:./\w]*)?)',
                _hla_repl,
                g,
                flags=re.IGNORECASE
            )

    # VKORC1/IFNL3 SNP with slash: "G/A" -> "GA"
    g = re.sub(
        r'(VKORC1|IFNL3)\s*:?\s*([ACGT])/([ACGT])',
        lambda m: f"{m.group(1).upper()}: {m.group(2)}{m.group(3)}",
        g, flags=re.IGNORECASE
    )

    # VKORC1 with position prefix: "-1639 G>A" or "-1639GA" -> "-1639GA"
    g = re.sub(
        r'(VKORC1)\s*:?\s*-1639\s*([ACGT])[>/]?\s*([ACGT])',
        lambda m: f"VKORC1: -1639{m.group(2)}{m.group(3)}",
        g, flags=re.IGNORECASE
    )

    # VKORC1/IFNL3 bare nucleotide: "VKORC1 AA" or "IFNL3 CC"
    g = re.sub(
        r'(VKORC1|IFNL3)\s+([ACGT]{2})\b',
        lambda m: f"{m.group(1).upper()}: {m.group(2).upper()}",
        g, flags=re.IGNORECASE
    )

    # "GENE *N *N" (space-separated, no slash) -> "GENE: *N/*N"
    g = re.sub(
        r'([A-Z][A-Z0-9a-z-]+)\s*:?\s*(\*\w+)\s+(\*\w+)',
        lambda m: f"{m.group(1).upper()}: {m.group(2)}/{m.group(3)}",
        g
    )

    # "GENE N N" (bare numbers) -> "GENE: *N/*N"
    g = re.sub(
        r'([A-Z][A-Z0-9]{2,}\w*)\s+(\d+\w*)\s+(\d+\w*)',
        lambda m: f"{m.group(1).upper()}: *{m.group(2)}/*{m.group(3)}",
        g
    )

    return g


def analyze_medications(medications: str, genotypes: str = "") -> str:
    """
    Analyze medications against a patient pharmacogenomic profile.

    medications: comma-separated drug names
    genotypes:   flexible input — star alleles, spoken, SNP notation (VKORC1: AA),
                 nucleotide (IFNL3: CC), or phenotype-direct (CYP2D6: Poor Metabolizer)
    """
    pgx_log.info(f"TOOL CALLED | meds={medications!r} | geno={genotypes!r}")

    phenotypes = {}
    all_mentioned_genes = set()
    unresolved_genes = []   # (gene, value_or_None)

    if genotypes:
        # Step 1: normalize
        normalized = _normalize_genotype_input(genotypes)
        pgx_log.info(f"  normalized geno: {normalized!r}")

        # Step 2: parse GENE: value
        pat = (
            r"([A-Z][A-Z0-9a-z-]+)\s*:\s*"
            r"("
                r"\*[\w]+(?:/\*[\w]+)?"                                              # *4/*4
                r"|(?:-\d+)?[ACGT]{2,}"                                              # AA, -1639AA
                r"|[A-Za-z][A-Za-z\s\-\u2014]+(?:Metabolizer|Function|sensitivity"  # phenotype
                r"|Susceptible|Deficient|Response|RISK|risk|positive|negative"        # + HLA shortcuts
                r"|hypersensitivity|toxicity)"
            r")"
        )
        matched_genes = set()
        for m in re.finditer(pat, normalized):
            gene  = m.group(1).upper()
            value = m.group(2).strip()
            matched_genes.add(gene)
            all_mentioned_genes.add(gene)

            # 1) Direct phenotype?
            direct = DIRECT_PHENOTYPES.get(value.lower())
            if direct:
                pgx_log.info(f"  {gene}: direct phenotype = {direct}")
                phenotypes[gene] = {"diplotype": "direct", "phenotype": direct}
                continue

            # 2) PHENOTYPE_MAP lookup — flexible matching
            gene_map = PHENOTYPE_MAP.get(gene, {})

            def _flexible_lookup(g_map, v):
                """Try progressively looser matching strategies."""
                # a) Exact
                if v in g_map: return g_map[v]
                # b) Reversed allele order
                parts = v.split("/")
                if len(parts) == 2:
                    rev = parts[1] + "/" + parts[0]
                    if rev in g_map: return g_map[rev]
                # c) Uppercase
                if v.upper() in g_map: return g_map[v.upper()]
                # d) HLA single allele — try homozygous then carrier
                if "/" not in v:
                    for candidate in (v + "/" + v, v + "/*other"):
                        if candidate in g_map: return g_map[candidate]
                # e) Strip sub-allele precision: *57:01 -> *57, *3A -> *3
                #    Useful when ASR drops the colon-suffix or letter suffix
                stripped = re.sub(r'([*]\d+)[:\.][0-9A-Za-z]+', r'', v)
                if stripped != v:
                    for candidate in (stripped, stripped + "/" + stripped, stripped + "/*other"):
                        if candidate in g_map: return g_map[candidate]
                    parts2 = stripped.split("/")
                    if len(parts2) == 2:
                        rev2 = parts2[1] + "/" + parts2[0]
                        if rev2 in g_map: return g_map[rev2]
                # f) Prefix match — find any key that starts with the same allele numbers
                #    e.g. value="*4" matches "*4/*4", "*4/*5" etc. — pick most severe
                #    Only for single allele inputs to avoid false positives
                if "/" not in v and v.startswith("*"):
                    candidates = [k for k in g_map if k.startswith(v + "/") or k.endswith("/" + v)]
                    if candidates:
                        # Prefer homozygous, then pick first
                        homo = [k for k in candidates if k == v + "/" + v]
                        return g_map[homo[0]] if homo else g_map[candidates[0]]
                return "Unknown"

            phenotype = _flexible_lookup(gene_map, value)
            if phenotype != "Unknown":
                pgx_log.info(f"  {gene}: {value!r} -> {phenotype} (flexible match)")

            if phenotype == "Unknown":
                pgx_log.warning(f"  {gene}: value {value!r} not recognized")
                unresolved_genes.append((gene, value))
            else:
                pgx_log.info(f"  {gene}: {value} -> {phenotype}")

            phenotypes[gene] = {"diplotype": value, "phenotype": phenotype}

        # Step 3: detect gene names mentioned with NO value at all
        tokens = re.findall(r'\b([A-Z][A-Z0-9]{1,10}(?:-[A-Z0-9]+)?)\b', normalized.upper())
        for g in tokens:
            if g in KNOWN_GENES and g not in matched_genes:
                pgx_log.warning(f"  {g}: mentioned but no genotype provided")
                unresolved_genes.append((g, None))
                all_mentioned_genes.add(g)

    # ── Drug analysis ──────────────────────────────────────────────────────────
    drug_list        = [d.strip() for d in medications.split(",")]
    normalized_drugs = normalize_drug_list(drug_list)
    drug_names       = [d["normalized"] for d in normalized_drugs]
    pgx_log.info(f"  drugs={drug_names} | phenotypes={json.dumps(phenotypes)}")

    dgi_alerts   = analyze_dgi(phenotypes, drug_names)
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

    # ── SECTION 1: Gene-drug interactions ─────────────────────────────────────
    critical_dgi = [a for a in dgi_sorted if a["severity"] >= 5
                    and a["phenotype"] not in ("Normal Metabolizer", "Normal Function", "Unknown")]
    high_dgi     = [a for a in dgi_sorted if a["severity"] == 4
                    and a["phenotype"] not in ("Normal Metabolizer", "Normal Function", "Unknown")]

    for a in critical_dgi:
        lines.append(
            f"CRITICAL GENE-DRUG INTERACTION: {a['drug'].capitalize()} with {a['gene']} "
            f"{a['phenotype']}. {a['recommendation']}"
        )
    for a in high_dgi:
        lines.append(
            f"HIGH GENE-DRUG INTERACTION: {a['drug'].capitalize()} with {a['gene']} "
            f"{a['phenotype']}. {a['recommendation']}"
        )

    # ── SECTION 2: Drug-drug interactions ─────────────────────────────────────
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

    # ── SECTION 3: Dosing recommendations ─────────────────────────────────────
    if dosing_recs:
        lines.append("Dosing recommendations: " +
                     ". ".join([f"{r['action']} {r['drug']}: {r['reason']}" for r in dosing_recs]))

    if not critical_dgi and not high_dgi and not critical_ddgi and not high_ddgi:
        lines.append("No critical or high severity interactions detected.")

    # ── Warnings for unresolved genes ─────────────────────────────────────────
    if unresolved_genes:
        for gene, value in unresolved_genes:
            if value is None:
                lines.append(
                    f"WARNING: {gene} was mentioned but no genotype or metabolizer status "
                    f"was provided. Please specify the diplotype (e.g. {gene}: *1/*4) or "
                    f"phenotype (e.g. Poor Metabolizer) to include {gene} in the analysis."
                )
            else:
                lines.append(
                    f"WARNING: {gene} value '{value}' was not recognized. "
                    f"Please verify the notation or provide the phenotype directly "
                    f"(e.g. Poor Metabolizer, Intermediate Metabolizer)."
                )

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
    instruction="""You are PGx-Guardian, a clinical pharmacogenomics voice assistant and knowledgeable colleague.
You help clinicians identify dangerous drug-gene and drug-drug interactions in real time.

PERSONALITY & TONE:
- You are a warm, confident clinical colleague — not a report printer.
- Speak naturally and conversationally, as if talking to a physician at the bedside.
- Use plain spoken language: say "this patient can't metabolize codeine" not "CYP2D6 Poor Metabolizer status results in diminished prodrug activation".
- After delivering findings, invite follow-up: "Would you like me to explain any of these interactions further, or suggest alternatives?"
- Answer follow-up questions naturally — explain mechanisms, suggest alternatives, clarify dosing — without re-running the analysis.
- If asked something outside your scope, say so briefly and stay helpful.

BEFORE CALLING analyze_medications:
- Genotype data is OPTIONAL. You can always analyze medications for drug-drug interactions even without any genetic data.
- If the user provides medications only (no genotypes), call analyze_medications immediately with an empty genotypes string — the tool will check drug-drug interactions from its database.
- Only ask for genotype clarification if the user has ALREADY mentioned a gene name but not provided its value. Example: "You mentioned CYP2D6 but no genotype — could you give me the diplotype or metabolizer status?"
- Never refuse to analyze medications just because no genetic data was provided.

WHEN INPUT IS COMPLETE:
1. Call analyze_medications immediately — do not speak first, do not wait.
2. Once the tool returns, deliver the findings conversationally.
3. End with a brief invitation for follow-up questions.
Never ask the user to confirm before calling the tool when input is complete.

ACCEPTED GENOTYPE FORMATS — normalize to structured form before passing to the tool:
  Star alleles:      CYP2D6: *4/*4
  Spoken alleles:    "star four star four" -> *4/*4
  Bare numbers:      "CYP2D6 four four" -> CYP2D6: *4/*4
  Ordinals:          "third allele" -> *3
  Phenotype-direct:  "CYP2D6 poor metabolizer" -> CYP2D6: Poor Metabolizer
  Deficient:         "TPMT deficient" -> TPMT: Poor Metabolizer

SPECIAL GENES — non-standard notation, pass exactly as spoken/written:
  VKORC1: nucleotide genotype GG / GA / AA (or with prefix: -1639GG / -1639GA / -1639AA)
          warfarin sensitivity — always include alongside warfarin
  IFNL3:  nucleotide genotype CC / CT / TT — peginterferon response
          IL28B is an older alias for IFNL3 — convert to IFNL3 before passing
  CYP3A4 / CYP1A2: limited CPIC coverage — accept star alleles or phenotype-direct
  HLA-B:  pronounced "H-L-A-B". Common alleles: *57:01 (abacavir risk), *15:02 (carbamazepine risk), *58:01 (allopurinol risk)
          If the user says "HLA-B abacavir risk" or "HLA-B positive" treat as abacavir hypersensitivity.
          If allele notation is unclear, ask: "For HLA-B, did you mean star 57 colon 01 for abacavir, star 15 colon 02 for carbamazepine, or star 58 colon 01 for allopurinol?"
  HLA-A:  pronounced "H-L-A-A". Common allele: *31:01 (carbamazepine DRESS risk)

REPORTING — STRICT ORDER:
1. CRITICAL GENE-DRUG INTERACTIONS first.
2. HIGH GENE-DRUG INTERACTIONS.
3. CRITICAL or HIGH DRUG-DRUG INTERACTIONS.
4. Dosing recommendations.
5. Warnings for unrecognized or missing genotypes.
- Never reframe a gene-drug finding as a drug-drug interaction.
- Deliver findings as natural spoken sentences, not bullet points or markdown.
- Always end with: 'This report requires physician review before any changes are made.'
- Then add one sentence inviting follow-up, e.g. "Do you have any questions about these findings, or would you like alternative options?"

AFTER DELIVERING A REPORT:
- Do NOT call analyze_medications again unless the user explicitly provides new medications or genotypes, or explicitly requests a new analysis.
- If the user asks follow-up questions, answer conversationally from the existing results — explain mechanisms, suggest alternatives, clarify risks — without calling the tool again.
- Ignore background noise, silence, or unclear audio — do not treat it as a new request.
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
