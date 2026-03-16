import os
import csv
import json
import time

DDI_HASH_MAP = {}

def load_ddinter(ddinter_dir: str):
    """Load all DDInter CSV files into a hash map."""
    start = time.time()
    count = 0
    for filename in os.listdir(ddinter_dir):
        if not filename.endswith(".csv"):
            continue
        with open(os.path.join(ddinter_dir, filename)) as f:
            reader = csv.DictReader(f)
            for row in reader:
                drug_a = row["Drug_A"].strip().lower()
                drug_b = row["Drug_B"].strip().lower()
                level = row["Level"].strip()
                key = tuple(sorted([drug_a, drug_b]))
                DDI_HASH_MAP[key] = {"severity": level, "source": "DDInter"}
                count += 1
    elapsed = time.time() - start
    print(f"✅ DDInter loaded: {count} pairs in {round(elapsed, 2)}s")
    return DDI_HASH_MAP

def lookup_ddi(drug_a: str, drug_b: str) -> dict:
    """O(1) lookup for a drug pair."""
    key = tuple(sorted([drug_a.lower(), drug_b.lower()]))
    return DDI_HASH_MAP.get(key, None)

if __name__ == "__main__":
    load_ddinter("data/ddinter")
    # Test our key drugs
    test_pairs = [
        ("clopidogrel", "omeprazole"),
        ("codeine", "fluoxetine"),
        ("naltrexone", "abacavir"),
    ]
    for a, b in test_pairs:
        result = lookup_ddi(a, b)
        print(f"  {a} + {b} → {result}")
