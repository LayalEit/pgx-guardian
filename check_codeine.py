from supabase import create_client
import os
from dotenv import load_dotenv

load_dotenv()
sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])
r = sb.table("cpic_cache").select("*").eq("gene", "CYP2D6").eq("drug_name", "codeine").execute()
print(r.data)
