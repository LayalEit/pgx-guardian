def validate_inputs(genotype_path: str, medications: list) -> dict:
    """Check inputs before running pipeline. Return warnings, not crashes."""
    warnings = []

    # Check genotype file exists
    import os
    if not os.path.exists(genotype_path):
        warnings.append({"type": "ERROR", "message": f"Genotype file not found: {genotype_path}"})
        return {"valid": False, "warnings": warnings}

    # Check medications list
    if not medications:
        warnings.append({"type": "ERROR", "message": "No medications provided."})
        return {"valid": False, "warnings": warnings}

    if len(medications) == 1:
        warnings.append({"type": "INFO", "message": "Only 1 medication — no DDI checks possible."})

    if len(medications) > 20:
        warnings.append({"type": "WARNING", "message": f"{len(medications)} medications is unusually high. Results may be verbose."})

    # Check for unknown drugs after normalization
    from agents.drug_list_agent import normalize_drug, SYNONYMS
    for med in medications:
        result = normalize_drug(med)
        if result["status"] == "passthrough" and med.lower() not in SYNONYMS:
            warnings.append({"type": "INFO", "message": f"'{med}' passed through as-is — not in synonym dictionary."})

    return {"valid": True, "warnings": warnings}

if __name__ == "__main__":
    tests = [
        ("data/test_patients/patient_demo.csv", ["Plavix", "omeprazole"]),
        ("data/test_patients/missing.csv", ["aspirin"]),
        ("data/test_patients/patient_demo.csv", []),
        ("data/test_patients/patient_demo.csv", ["codeine"]),
        ("data/test_patients/patient_demo.csv", ["Plavix", "omeprazole", "codeine", "Prozac", "warfarin", "atorvastatin"]),
    ]

    for path, meds in tests:
        result = validate_inputs(path, meds)
        status = "✅" if result["valid"] else "❌"
        print(f"{status} {path} + {meds}")
        for w in result["warnings"]:
            print(f"   [{w['type']}] {w['message']}")
