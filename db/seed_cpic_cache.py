import os
from supabase import create_client
from dotenv import load_dotenv

load_dotenv()
supabase = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])

supabase.table("cpic_cache").insert([
    {
        "gene": "CYP2C19",
        "diplotype": "*1/*2",
        "drug_name": "clopidogrel",
        "phenotype": "Intermediate Metabolizer",
        "recommendation": "Consider prasugrel or ticagrelor",
        "cpic_includes_inhibitor_context": True
    },
    {
        "gene": "CYP2D6",
        "diplotype": "*4/*4",
        "drug_name": "codeine",
        "phenotype": "Poor Metabolizer",
        "recommendation": "AVOID codeine. Use non-CYP2D6 analgesic.",
        "cpic_includes_inhibitor_context": False
    }
]).execute()
print("✅ CPIC cache seeded with double-count flags")
