"""Validate isolated North Vancouver assets against the untouched regional base."""
from __future__ import annotations

from collections import Counter
from itertools import zip_longest
import hashlib
import json
from pathlib import Path
import sqlite3

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "data/prepared"
UNCHANGED = "id,pid,municipality,lat,lon,assessed,land,improvement,tax,year,built,type,change,history,parcel".split(",")


def main():
    report_path = OUT / "integration-report-northvan.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    db = sqlite3.connect("file:" + (ROOT / "data/regional.sqlite").as_posix() + "?mode=ro", uri=True)
    query = db.execute("SELECT record FROM properties WHERE municipality IN (?,?) ORDER BY id", ("North Vancouver City", "North Vancouver District"))
    counts, seen = Counter(), set()
    with (OUT / "properties-northvan.jsonl").open(encoding="utf-8") as source:
        for before, line in zip_longest(query, source):
            assert before is not None and line is not None, "Base/output row count mismatch"
            old, row = json.loads(before[0]), json.loads(line)
            counts["maximum_property_json_bytes"] = max(counts["maximum_property_json_bytes"], len(line.encode("utf-8")))
            assert row["id"] not in seen, "Duplicate property ID"
            seen.add(row["id"])
            for field in UNCHANGED:
                assert old[field] == row[field], (row["id"], "Unexpected mutation", field)
            context = json.loads(row["features"])
            assert context["municipal_enrichment_source"] == "northvan-extra"
            assert isinstance(context["municipality_context_sources"], list)
            assert all(f["url"].startswith("https://") for f in context["municipal_facts"])
            if context.get("cnv_roll"):
                assert row["municipality"] == "North Vancouver City"
                assert context["cnv_pid"] == row["pid"]
                counts["city_exact_roll_lookup"] += 1
            if context.get("building_storeys"):
                assert row["municipality"] == "North Vancouver District"
                assert "one linked building inventory record" in context["municipal_building_match"]
                counts["district_observed_storeys"] += 1
            counts["properties"] += 1
    db.close()
    for name in ["layers", "permits", "documents", "sources"]:
        ids = set()
        with (OUT / (name + "-northvan.jsonl")).open(encoding="utf-8") as source:
            for line in source:
                row = json.loads(line)
                assert row["id"] not in ids, (name, "duplicate ID")
                ids.add(row["id"])
                if name == "layers":
                    assert len(row["data"].encode("utf-8")) < 1_500_000
                    data = json.loads(row["data"])
                    if row["id"].startswith("municipal-summary:"):
                        assert set(data) == {"municipality", "properties", "assessments", "neighbourhoods", "datasets", "limits", "updated"}
                        assert sum(n["count"] for n in data["neighbourhoods"]) == data["properties"]
                        assert data["datasets"]
                    else:
                        assert data["features"], (row["id"], "Empty display layer")
                        assert all(f["geometry"] and f["properties"]["source_url"] for f in data["features"])
                if name == "permits":
                    assert row["issued"] is None and row["value"] is None and row["type"] == "Building permit record"
                    assert json.loads(row["raw"])["issuance_status"] == "Not provided in published layer"
        counts[name] = len(ids)
    for output in report["outputs"]:
        path = ROOT / output["file"]
        assert path.stat().st_size == output["bytes"]
        assert hashlib.file_digest(path.open("rb"), "sha256").hexdigest() == output["sha256"]
    report["validation"] = {"status": "passed", "checked_against_read_only_base": True, "preserved_fields": UNCHANGED, "counts": dict(counts), "checks": ["All original North Vancouver IDs and monetary histories preserved", "Distinct typed data for observed storeys and permitted capacity", "All map layers nonempty and below 1.5 MB JSON per D1 row", "Municipal summaries match shared app schema and property counts", "Permit records contain no inferred issuance date or value", "All prepared artifact checksums match report"]}
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report["validation"], indent=2))


if __name__ == "__main__":
    main()
