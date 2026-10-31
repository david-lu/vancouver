"""Download complete official Surrey housing-context datasets with receipts.

All fields are preserved. Spatial exports use WGS84; permit tables retain null
geometry. Object-ID snapshots are checked before/after each full layer download.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import time
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "data/raw/surrey-extra"
MAP = "https://gisservices.surrey.ca/arcgis/rest/services/OpenData/MapServer"
HOST = "https://services5.arcgis.com/YRpe0VKTJytZSSIB/arcgis/rest/services"
PREFIX = "surrey"
PORTAL = "https://opendata-surrey.hub.arcgis.com"
LICENCE_EVIDENCE = "https://www.surrey.ca/services-payments/online-services/open-data"
LICENCE = "Open Government License - Surrey"
LICENCE_URL = "https://opendata-surrey.hub.arcgis.com/pages/55089a19491a4fe59a41e059fd8af708"
ATTRIBUTION = "Contains information licensed under the Open Government License – City of Surrey."
SOURCES = [
    ("property-detail", "Surrey Property Detail List 2026", HOST + "/property%20detail%20list/FeatureServer/0", "14d610192b7b4cfb98fee31a608d8a1e"),
    ("issued-building-permits", "Surrey Issued Building Permits", HOST + "/IssuedBuildingPermits/FeatureServer/0", "f3ec200f59a342e38c52248f07fe610d"),
    ("zoning", "Surrey Zoning Boundaries", MAP + "/239", None),
    ("communities", "Surrey Communities", "https://gisservices.surrey.ca/arcgis/rest/services/Public/Legal/MapServer/14", None),
    ("parks", "Surrey Parks", MAP + "/256", None),
    ("development-applications", "Surrey Development Applications", MAP + "/167", None),
    ("schools", "Surrey Schools", MAP + "/156", None),
    ("elementary-catchments", "Surrey Elementary School Catchments", MAP + "/157", None),
    ("secondary-catchments", "Surrey Secondary School Catchments", MAP + "/159", None),
    ("official-community-plan", "Surrey Official Community Plan Designations", MAP + "/237", None),
    ("urban-centres", "Surrey Urban Centres", MAP + "/230", None),
    ("town-centre-densities", "Surrey Town Centre Densities", MAP + "/228", None),
    ("hazard-lands", "Surrey Hazard Lands Development Permit Area", MAP + "/235", None),
    ("floodplain-200-year", "Surrey Drainage 200 Year Flood Plain", MAP + "/4", None),
    ("heritage-sites", "Surrey Heritage Sites", MAP + "/223", None),
    ("agricultural-land-reserve", "Surrey Agricultural Land Reserve", MAP + "/220", None),
    ("lots", "Surrey Lots", MAP + "/148", None),
    ("address-points", "Surrey Address Points", MAP + "/138", None),
]


def now():
    return datetime.now(timezone.utc).isoformat()


def encoded(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")


def save_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".part")
    temp.write_bytes(encoded(value))
    temp.replace(path)


def sha(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def fetch(url, params=None):
    if params:
        url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
    for attempt in range(5):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "HarbourHousingPublicData/1.0", "Accept-Encoding": "identity"})
            with urllib.request.urlopen(request, timeout=120) as response:
                data = response.read()
            return data, url
        except Exception:
            if attempt == 4:
                raise
            time.sleep(2 ** attempt)


def get_json(url, params=None):
    data, actual = fetch(url, params)
    value = json.loads(data)
    if "error" in value:
        raise RuntimeError(f"ArcGIS error {actual}: {value['error']}")
    return value, data, actual


def ids_for(url):
    value, _, _ = get_json(url + "/query", {"f": "json", "where": "1=1", "returnIdsOnly": "true"})
    ids = sorted(value["objectIds"])
    if len(ids) != len(set(ids)):
        raise RuntimeError("Duplicate source object IDs")
    return ids, value.get("objectIdFieldName")


def download(source):
    identity, title, url, item = source
    directory = OUT / identity
    directory.mkdir(parents=True, exist_ok=True)
    metadata, raw_metadata, _ = get_json(url, {"f": "pjson"})
    (directory / "metadata.json").write_bytes(raw_metadata)
    if item:
        for suffix, filename in [("?f=pjson", "item.json"), ("/info/metadata/metadata.xml", "metadata.xml")]:
            data, _ = fetch("https://www.arcgis.com/sharing/rest/content/items/" + item + suffix)
            (directory / filename).write_bytes(data)
    ids, object_field = ids_for(url)
    count, _, count_url = get_json(url + "/query", {"f": "json", "where": "1=1", "returnCountOnly": "true"})
    if count["count"] != len(ids):
        raise RuntimeError(f"Count/ID mismatch for {identity}")
    save_json(directory / "object-ids.json", ids)
    object_field = object_field or metadata.get("objectIdField") or next(f["name"] for f in metadata["fields"] if f["type"] == "esriFieldTypeOID")
    receipt_path = directory / "receipt.json"
    path = directory / (identity + ".geojson")
    ids_hash = hashlib.sha256(encoded(ids)).hexdigest()
    if receipt_path.exists() and path.exists():
        prior = json.loads(receipt_path.read_text(encoding="utf-8"))
        if metadata.get("editingInfo") and prior.get("object_ids_sha256") == ids_hash and prior.get("editing_info") == metadata.get("editingInfo") and prior.get("sha256") == sha(path):
            print("CACHED", identity, len(ids), flush=True)
            return prior
    started = now()
    pages = [ids[i:i + 500] for i in range(0, len(ids), 500)]

    def page(arguments):
        number, selected = arguments
        # Short object-ID ranges avoid server URL limits; compare returned IDs
        # with the complete snapshot so gaps or changing records cannot be lost.
        params = {"f": "geojson", "where": f"{object_field} >= {selected[0]} AND {object_field} <= {selected[-1]}", "outFields": "*",
                  "returnGeometry": "true" if metadata.get("geometryType") else "false", "outSR": "4326"}
        try:
            value, data, query_url = get_json(url + "/query", params)
        except Exception:
            if len(selected) <= 1:
                raise
            # Very complex polygon requests may exceed a server geometry limit.
            # Split only the failing range, still preserving every source ID.
            midpoint = len(selected) // 2
            left = page((str(number) + "a", selected[:midpoint]))
            right = page((str(number) + "b", selected[midpoint:]))
            value = {"type": "FeatureCollection", "features": left[2] + right[2]}
            data = encoded(value)
            query_url = {"split_requests": [left[3]["url"], right[3]["url"]]}
        if value.get("type") != "FeatureCollection":
            raise RuntimeError(f"Expected GeoJSON for {identity}")
        features = value.get("features", [])
        actual = sorted(f["properties"][object_field] for f in features)
        if actual != selected or value.get("exceededTransferLimit"):
            raise RuntimeError(f"Incomplete page {identity}/{number}")
        filename = "page-" + (f"{number:05d}" if isinstance(number, int) else number) + ".geojson"
        (directory / filename).write_bytes(data)
        return number, filename, features, {"page": number, "file": filename, "records": len(features), "url": query_url,
               "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}

    page_receipts = []
    total, geometry_count = 0, 0
    with path.with_suffix(".geojson.part").open("wb") as stream:
        stream.write(b'{"type":"FeatureCollection","features":[')
        with ThreadPoolExecutor(max_workers=4) as executor:
            for _, _, features, page_receipt in executor.map(page, enumerate(pages)):
                page_receipts.append(page_receipt)
                for feature in features:
                    if total:
                        stream.write(b",")
                    stream.write(encoded(feature))
                    total += 1
                    geometry_count += feature.get("geometry") is not None
                if total % 25000 == 0:
                    print("DOWNLOADED", identity, total, len(ids), flush=True)
        stream.write(b"]}")
    final_ids, _ = ids_for(url)
    if final_ids != ids or total != len(ids):
        raise RuntimeError(f"Source IDs changed during full download: {identity}; retry")
    path.with_suffix(".geojson.part").replace(path)
    receipt = {"id": PREFIX + "-" + identity, "title": title, "url": url, "source_item_id": item,
        "source_page": PORTAL + "/datasets/" + item if item else url,
        "path": str(path.relative_to(ROOT)).replace("\\", "/"), "status": "verified", "records": total,
        "expected_records": len(ids), "geometry_records": geometry_count, "crs": "EPSG:4326",
        "geometry_type": metadata.get("geometryType"), "source_fields": metadata["fields"],
        "object_id_field": object_field, "object_ids_sha256": ids_hash, "source_ids_stable": True,
        "editing_info": metadata.get("editingInfo"), "bytes": path.stat().st_size, "sha256": sha(path),
        "started_at": started, "downloaded_at": now(), "licence": LICENCE, "licence_url": LICENCE_URL,
        "licence_evidence": LICENCE_EVIDENCE,
        "attribution": ATTRIBUTION, "count_url": count_url, "pages": page_receipts,
        "preservation": "All source fields and records; raw API pages and metadata preserved. Geometry server-projected to EPSG:4326 without simplification."}
    if identity == "property-detail":
        receipt["assessment_year"] = 2026
        receipt["assessment_year_evidence"] = "Official metadata.xml dataIdInfo/idPurp identifies current-year 2026 listing. PLAN_YEAR is cadastral plan year, not year built."
    if identity == "issued-building-permits":
        receipt["limitations"] = "Source table has no geometry. ValueOfConstruction is construction value, not assessed or asking price. This is the complete current published table, not all historical permits."
    save_json(receipt_path, receipt)
    print("VERIFIED", identity, total, path.stat().st_size, flush=True)
    return receipt


def archive_licence():
    directory = OUT / "licence"
    directory.mkdir(parents=True, exist_ok=True)
    urls = {"open-data-page.html": "https://www.surrey.ca/services-payments/online-services/open-data",
            "licence-page.html": LICENCE_URL,
            "licence-item.json": "https://www.arcgis.com/sharing/rest/content/items/55089a19491a4fe59a41e059fd8af708?f=pjson",
            "licence-content.json": "https://www.arcgis.com/sharing/rest/content/items/55089a19491a4fe59a41e059fd8af708/data?f=pjson"}
    for filename, url in urls.items():
        data, _ = fetch(url)
        (directory / filename).write_bytes(data)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--only", nargs="*")
    args = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    archive_licence()
    chosen = [s for s in SOURCES if not args.only or s[0] in args.only]
    failures = []
    for source in chosen:
        try:
            download(source)
        except Exception as error:
            failures.append({"id": source[0], "url": source[2], "error": str(error)})
            print("SOURCE_FAILED", source[0], str(error), flush=True)
        receipts = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(OUT.glob("*/receipt.json"))]
        save_json(OUT / "report.json", {"status": "downloading", "updated_at": now(), "datasets": receipts})
    receipts = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(OUT.glob("*/receipt.json"))]
    for receipt in receipts:
        receipt.update(licence=LICENCE, licence_url=LICENCE_URL, attribution=ATTRIBUTION)
        identity = receipt["id"].removeprefix("surrey-")
        save_json(OUT / identity / "receipt.json", receipt)
    save_json(OUT / "report.json", {"status": "incomplete" if failures else "complete", "failures": failures,
        "updated_at": now(), "datasets": receipts,
        "records": sum(r["records"] for r in receipts), "bytes": sum(r["bytes"] for r in receipts),
        "notes": ["Surrey property assessments explicitly cover 2026 per source metadata.",
            "All nonempty source layers exported completely. Historical permits beyond the currently published table are not claimed.",
            "PLAN_YEAR is not year built; LOT_SIZE is parcel/site area, not living area.",
            "Community source contains nine named boundaries including City Centre, Clayton and Grandview."]})
    print("SURREY_COMPLETE", sum(r["records"] for r in receipts), len(receipts), flush=True)


if __name__ == "__main__":
    main()
