"""
Patch mechanism_knowledge_base with missing gene-drug entries for:
- G6PD: rasburicase, dapsone, primaquine, nitrofurantoin, methylene blue
- RYR1: halothane, sevoflurane, desflurane, isoflurane, succinylcholine
- CACNA1S: halothane, sevoflurane, desflurane, isoflurane, succinylcholine
"""
import os
from supabase import create_client

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_KEY"]
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

ENTRIES = [
    # G6PD — oxidative stress drugs trigger hemolytic anemia in deficient patients
    {"drug_name": "rasburicase",    "gene": "G6PD", "mechanism_type": "immune",    "relationship": "hypersensitivity_marker", "strength": "binary", "evidence_level": "CPIC-A", "source": "CPIC"},
    {"drug_name": "dapsone",        "gene": "G6PD", "mechanism_type": "immune",    "relationship": "hypersensitivity_marker", "strength": "binary", "evidence_level": "CPIC-A", "source": "CPIC"},
    {"drug_name": "primaquine",     "gene": "G6PD", "mechanism_type": "immune",    "relationship": "hypersensitivity_marker", "strength": "binary", "evidence_level": "CPIC-A", "source": "CPIC"},
    {"drug_name": "nitrofurantoin", "gene": "G6PD", "mechanism_type": "immune",    "relationship": "hypersensitivity_marker", "strength": "binary", "evidence_level": "CPIC-B", "source": "CPIC"},
    {"drug_name": "methylene blue", "gene": "G6PD", "mechanism_type": "immune",    "relationship": "hypersensitivity_marker", "strength": "binary", "evidence_level": "CPIC-B", "source": "CPIC"},
    # RYR1 — malignant hyperthermia triggering agents
    {"drug_name": "halothane",      "gene": "RYR1", "mechanism_type": "immune",    "relationship": "hypersensitivity_marker", "strength": "binary", "evidence_level": "CPIC-A", "source": "CPIC"},
    {"drug_name": "sevoflurane",    "gene": "RYR1", "mechanism_type": "immune",    "relationship": "hypersensitivity_marker", "strength": "binary", "evidence_level": "CPIC-A", "source": "CPIC"},
    {"drug_name": "desflurane",     "gene": "RYR1", "mechanism_type": "immune",    "relationship": "hypersensitivity_marker", "strength": "binary", "evidence_level": "CPIC-A", "source": "CPIC"},
    {"drug_name": "isoflurane",     "gene": "RYR1", "mechanism_type": "immune",    "relationship": "hypersensitivity_marker", "strength": "binary", "evidence_level": "CPIC-A", "source": "CPIC"},
    {"drug_name": "succinylcholine","gene": "RYR1", "mechanism_type": "immune",    "relationship": "hypersensitivity_marker", "strength": "binary", "evidence_level": "CPIC-A", "source": "CPIC"},
    # CACNA1S — malignant hyperthermia (second MH gene)
    {"drug_name": "halothane",      "gene": "CACNA1S", "mechanism_type": "immune", "relationship": "hypersensitivity_marker", "strength": "binary", "evidence_level": "CPIC-A", "source": "CPIC"},
    {"drug_name": "sevoflurane",    "gene": "CACNA1S", "mechanism_type": "immune", "relationship": "hypersensitivity_marker", "strength": "binary", "evidence_level": "CPIC-A", "source": "CPIC"},
    {"drug_name": "desflurane",     "gene": "CACNA1S", "mechanism_type": "immune", "relationship": "hypersensitivity_marker", "strength": "binary", "evidence_level": "CPIC-A", "source": "CPIC"},
    {"drug_name": "isoflurane",     "gene": "CACNA1S", "mechanism_type": "immune", "relationship": "hypersensitivity_marker", "strength": "binary", "evidence_level": "CPIC-A", "source": "CPIC"},
    {"drug_name": "succinylcholine","gene": "CACNA1S", "mechanism_type": "immune", "relationship": "hypersensitivity_marker", "strength": "binary", "evidence_level": "CPIC-A", "source": "CPIC"},
]

# Upsert — safe to run multiple times
result = supabase.table("mechanism_knowledge_base").upsert(
    ENTRIES,
    on_conflict="drug_name,gene"
).execute()
print(f"Upserted {len(result.data)} rows")
for row in result.data:
    print(f"  {row['gene']} + {row['drug_name']}")
