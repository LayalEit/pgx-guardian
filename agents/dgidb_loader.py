import csv
import time

DGIDB_MAP = {}

TARGET_GENES = {"CYP2D6", "CYP2C19", "CYP2C9", "DPYD", "SLCO1B1", "HLA-B", "VKORC1"}

def load_dgidb(tsv_path: str):
    """Load DGIdb interactions for our target genes into a hash map."""
    start = time.time()
    count = 0
    with open(tsv_path) as f:
        reader = csv.DictReader(f, delimiter="\t")
        for row in reader:
            gene = row["gene_name"].strip()
            if gene not in TARGET_GENES:
                continue
            drug = row["drug_name"].strip().lower()
            interaction_type = row["interaction_type"].strip()
            if drug and gene:
                key = (drug, gene)
                DGIDB_MAP[key] = {
                    "gene": gene,
                    "drug": drug,
                    "interaction_type": interaction_type,
                    "source": "DGIdb"
                }
                count += 1
    elapsed = time.time() - start
    print(f"✅ DGIdb loaded: {count} interactions for target genes in {round(elapsed, 2)}s")
    return DGIDB_MAP

def lookup_dgi(drug: str, gene: str) -> dict:
    """O(1) lookup for a drug-gene pair."""
    key = (drug.lower(), gene)
    return DGIDB_MAP.get(key, None)

if __name__ == "__main__":
    load_dgidb("data/dgidb/interactions.tsv")
    test_pairs = [
        ("codeine", "CYP2D6"),
        ("fluoxetine", "CYP2D6"),
        ("clopidogrel", "CYP2C19"),
        ("omeprazole", "CYP2C19"),
    ]
    for drug, gene in test_pairs:
        result = lookup_dgi(drug, gene)
        print(f"  {drug} + {gene} → {result}")
