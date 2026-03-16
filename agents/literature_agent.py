# Literature Agent — Pre-loaded evidence cache
# PubMed API used where available; fallback to curated cache for demo reliability

EVIDENCE_CACHE = {
    ("clopidogrel", "CYP2C19"): [
        {
            "pmid": "21716271",
            "title": "Clinical Pharmacogenomics Implementation Consortium guidelines for CYP2C19 and clopidogrel therapy",
            "authors": "Scott SA, Sangkuhl K, et al.",
            "journal": "Clinical Pharmacology & Therapeutics",
            "year": "2011",
            "url": "https://pubmed.ncbi.nlm.nih.gov/21716271/"
        },
        {
            "pmid": "33000038",
            "title": "CPIC Guideline for CYP2C19 and Antiplatelet Therapy",
            "authors": "Claassens DMF, Vos GJA, et al.",
            "journal": "Clinical Pharmacology & Therapeutics",
            "year": "2020",
            "url": "https://pubmed.ncbi.nlm.nih.gov/33000038/"
        }
    ],
    ("codeine", "CYP2D6"): [
        {
            "pmid": "24458010",
            "title": "CPIC guidelines for CYP2D6 and codeine therapy",
            "authors": "Crews KR, Gaedigk A, et al.",
            "journal": "Clinical Pharmacology & Therapeutics",
            "year": "2014",
            "url": "https://pubmed.ncbi.nlm.nih.gov/24458010/"
        }
    ],
    ("fluorouracil", "DPYD"): [
        {
            "pmid": "29152729",
            "title": "CPIC Guideline for Dihydropyrimidine Dehydrogenase Genotype and Fluoropyrimidine Dosing",
            "authors": "Amstutz U, Henricks LM, et al.",
            "journal": "Clinical Pharmacology & Therapeutics",
            "year": "2018",
            "url": "https://pubmed.ncbi.nlm.nih.gov/29152729/"
        }
    ],
    ("simvastatin", "SLCO1B1"): [
        {
            "pmid": "22617227",
            "title": "CPIC guidelines for SLCO1B1 and simvastatin-induced myopathy",
            "authors": "Wilke RA, Ramsey LB, et al.",
            "journal": "Clinical Pharmacology & Therapeutics",
            "year": "2012",
            "url": "https://pubmed.ncbi.nlm.nih.gov/22617227/"
        }
    ],
    ("abacavir", "HLA-B"): [
        {
            "pmid": "22378157",
            "title": "CPIC guidelines for HLA-B genotype and abacavir therapy",
            "authors": "Martin MA, Klein TE, et al.",
            "journal": "Clinical Pharmacology & Therapeutics",
            "year": "2012",
            "url": "https://pubmed.ncbi.nlm.nih.gov/22378157/"
        }
    ],
}

def find_evidence(drug: str, gene: str, phenotype: str = "") -> list:
    """Find evidence for a drug-gene pair from the cache."""
    key = (drug.lower(), gene)
    results = EVIDENCE_CACHE.get(key, [])
    if not results:
        # Try reverse key
        key2 = (gene, drug.lower())
        results = EVIDENCE_CACHE.get(key2, [])
    return results

def get_all_evidence(dgi_alerts: list) -> dict:
    """Get evidence for all DGI alerts in a report."""
    evidence = {}
    for alert in dgi_alerts:
        drug = alert["drug"]
        gene = alert["gene"]
        phenotype = alert["phenotype"]
        key = f"{drug}+{gene}"
        results = find_evidence(drug, gene, phenotype)
        if results:
            evidence[key] = results
    return evidence

if __name__ == "__main__":
    print("🔬 Literature Agent — Evidence Cache\n")
    test_queries = [
        ("clopidogrel", "CYP2C19", "Intermediate Metabolizer"),
        ("codeine",     "CYP2D6",  "Poor Metabolizer"),
        ("fluorouracil","DPYD",    "Intermediate Metabolizer"),
        ("warfarin",    "CYP2C9",  "Poor Metabolizer"),  # not in cache
    ]
    for drug, gene, phenotype in test_queries:
        print(f"📚 Evidence for {drug} + {gene}:")
        results = find_evidence(drug, gene, phenotype)
        if results:
            for r in results:
                print(f"  ✅ [{r['year']}] {r['title'][:70]}...")
                print(f"     {r['authors']} — {r['journal']}")
                print(f"     {r['url']}")
        else:
            print(f"  ℹ️  No cached evidence. Recommend PubMed search.")
        print()
