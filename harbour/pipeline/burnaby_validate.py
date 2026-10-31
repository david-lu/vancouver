"""Validate the complete Burnaby archive and append join guidance to report.json."""
import collections
import hashlib
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "data" / "raw" / "burnaby-extra"

def main():
    report_path = OUT / "report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    checks = []
    rows = None
    for receipt in report["datasets"]:
        path = ROOT / receipt["path"]
        raw = path.read_bytes()
        assert hashlib.sha256(raw).hexdigest() == receipt["sha256"], path
        data = json.loads(raw)
        spatial = receipt["geometry_type"] is not None
        records = data["features"] if spatial else data["records"]
        attrs = [f["properties"] for f in records] if spatial else records
        assert len(records) == receipt["records"] == receipt["expected_records"], path
        ids = [a[receipt["object_id_field"]] for a in attrs]
        assert len(ids) == len(set(ids)), path
        expected_fields = set(receipt["source_fields"])
        assert all(expected_fields.issubset(a) for a in attrs), path
        extra_fields = sorted(set().union(*(set(a) - expected_fields for a in attrs)))
        geometry_counts = dict(collections.Counter((f.get("geometry") or {}).get("type") for f in records)) if spatial else None
        checks.append({"id": receipt["id"], "records": len(records), "checksum_verified": True,
                       "all_source_fields_verified": True, "unique_oids_verified": True,
                       "geometry_types": geometry_counts, "extra_fields_returned_by_source": extra_fields})
        if receipt["id"] == "property-assessments":
            rows = attrs
        print("VERIFIED", receipt["id"], len(records), flush=True)
    if rows is not None:
        pids = [str(r.get("PTS_LTO_PID") or "").strip() for r in rows]
        exact = [p for p in pids if re.fullmatch(r"\d{3}-\d{3}-\d{3}", p)]
        pid_counts = collections.Counter(exact)
        value_field = "PTS_GROSS_ASSD_VALUE_CURRENT"
        profile = {
            "rows": len(rows), "address_status_counts": dict(collections.Counter(str(r.get("ADDR_STATUS")) for r in rows)),
            "legal_type_counts": dict(collections.Counter(str(r.get("LEGAL_TYPE")) for r in rows)),
            "exact_single_pid_rows": len(exact), "distinct_exact_single_pids": len(pid_counts),
            "blank_pid_rows": sum(not p for p in pids),
            "other_pid_format_rows": sum(bool(p) and not re.fullmatch(r"\d{3}-\d{3}-\d{3}", p) for p in pids),
            "multirow_exact_pids": sum(c > 1 for c in pid_counts.values()),
            "maximum_rows_per_exact_pid": max(pid_counts.values(), default=0),
            "rows_with_unit": sum(bool(str(r.get("ADDR_ST_UNIT") or "").strip()) for r in rows),
            "distinct_property_numbers": len(set(r.get("PROPERTY_NUMBER") for r in rows)),
            "positive_current_assessment_rows": sum((r.get(value_field) or 0) > 0 for r in rows),
            "null_current_assessment_rows": sum(r.get(value_field) is None for r in rows),
            "zero_current_assessment_rows": sum(r.get(value_field) == 0 for r in rows),
            "assessment_year": None,
            "join_guidance": [
                "Use PTS_LTO_PID for an exact single-PID match, removing hyphens only after validating a nine-digit PID.",
                "Keep PROPERTY_NUMBER, ADDR_ST_UNIT, ADDR_FULL, ADDR_STATUS and source OBJECTID; a PID may have multiple source rows.",
                "Do not total multiple table rows for the same parcel or copy an aggregate parent valuation to every unit.",
                "A building-height field is not a construction-year field. Lot dimensions need source unit verification before conversion.",
                "Assessment values are CURRENT/PREV source periods with no published year field; do not infer years from fetch date.",
            ],
        }
        report["property_join_profile"] = profile
    report["validation"] = checks
    report["additional_research"] = {
        "building_permits": {
            "url": "https://www.burnaby.ca/services-and-payments/permits-and-applications/building-permits-issued-and-tabulation-reports",
            "finding": "Official daily permit PDFs for the past year and monthly tabulation reports are published. No equivalent complete machine-readable permit layer found in inspected official OpenData and BurnabyMap catalogs. This archive does not claim permit coverage.",
        },
        "planning_developments_map": {
            "url": "https://burnaby.maps.arcgis.com/apps/MapSeries/index.html?appid=3a4d0a698826452f9a94aa01d71704cf",
            "finding": "Inspected configuration describes 2017 major projects; excluded from current-development coverage.",
        },
        "zoning_reference": "https://www.burnaby.ca/our-city/bylaws/zoning-bylaw",
    }
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report.get("property_join_profile"), indent=2), flush=True)

if __name__ == "__main__":
    main()
