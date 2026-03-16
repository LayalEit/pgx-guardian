import os
from supabase import create_client
from dotenv import load_dotenv

load_dotenv()
supabase = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])

PRIORITY_DRUGS = {
    "fluoxetine":  ("CYP2D6",  "metabolism", "inhibitor",               "strong"),
    "omeprazole":  ("CYP2C19", "metabolism", "inhibitor",               "moderate"),
    "codeine":     ("CYP2D6",  "metabolism", "substrate",               "strong"),
    "clopidogrel": ("CYP2C19", "metabolism", "substrate",               "strong"),
    "abacavir":    ("HLA-B",   "immune",     "hypersensitivity_marker", "binary"),
}

rows = []
for drug, (gene, mech_type, rel, strength) in PRIORITY_DRUGS.items():
    rows.append({
        "drug_name": drug,
        "gene": gene,
        "mechanism_type": mech_type,
        "relationship": rel,
        "strength": strength,
        "evidence_level": "CPIC-A",
        "source": "CPIC"
    })

supabase.table("mechanism_knowledge_base").insert(rows).execute()
print("✅ Mechanism KB seeded with 5 priority drugs")
