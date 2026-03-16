import time
import sys
sys.path.insert(0, ".")
from agents.genotype_parser import run_parser

patients = [
    "data/test_patients/patient1.csv",
    "data/test_patients/patient2.csv",
    "data/test_patients/patient3.csv",
]

for p in patients:
    result = run_parser(p)
    elapsed = result["elapsed_seconds"]
    status = "✅" if elapsed < 3.0 else "❌ TOO SLOW"
    print(f"{status} {p} → {elapsed}s")
    for gene, info in result["phenotypes"].items():
        print(f"   {gene}: {info['diplotype']} → {info['phenotype']}")
