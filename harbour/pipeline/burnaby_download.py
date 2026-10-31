"""Archive complete public Burnaby property/context layers with provenance.

Run from the workspace: python harbour/pipeline/burnaby_download.py
All writes are confined to data/raw/burnaby-extra/. All API requests are reads.
"""
from __future__ import annotations
import concurrent.futures
import datetime as dt
import hashlib
import html
import json
from pathlib import Path
import re
import time
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "data" / "raw" / "burnaby-extra"
BASE = "https://gis.burnaby.ca/arcgis/rest/services/"
LICENCE_URL = "https://data.burnaby.ca/pages/open-government-licence"
TERMS_URL = "https://gis.burnaby.ca/burnabymap/configs/Splash/config_Splash.json"
DATASETS = {
    "property-assessments": "BurnabyMap/BBY_PUBLIC_TBL/MapServer/1",
    "zoning": "OpenData/OpenData1/MapServer/8",
    "parks": "OpenData/OpenData1/MapServer/6",
    "community-plan-areas": "OpenData/OpenData1/MapServer/10",
    "ocp-land-use": "OpenData/OpenData1/MapServer/11",
    "legal-parcels": "OpenData/OpenData4/MapServer/7",
    "building-outlines": "OpenData/OpenData4/MapServer/18",
    "places-of-interest": "OpenData/OpenData1/MapServer/1",
    "heritage-landmarks": "OpenData/OpenData1/MapServer/2",
    "skytrain-stations": "OpenData/OpenData5/MapServer/10",
    "urban-trails": "OpenData/OpenData5/MapServer/14",
    "bike-trails": "OpenData/OpenData5/MapServer/15",
    "capital-works": "Other/CapitalWorks/MapServer/0",
}

def now():
    return dt.datetime.now(dt.timezone.utc).isoformat()

def fetch(url, params=None):
    target = url + ("?" + urllib.parse.urlencode(params) if params else "")
    last = None
    for attempt in range(5):
        try:
            req = urllib.request.Request(target, headers={"User-Agent": "Harbour/1.0 public municipal data archive"})
            with urllib.request.urlopen(req, timeout=120) as response:
                return response.read()
        except Exception as exc:
            last = exc
            if attempt < 4:
                time.sleep(2 ** attempt)
    raise RuntimeError(f"Request failed: {url}") from last

def get(url, **params):
    value = json.loads(fetch(url, params))
    if "error" in value:
        raise RuntimeError(f"ArcGIS error: {url}: {value['error']}")
    return value

def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")

def sha(path):
    h = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()

def download(entry):
    name, relative_url = entry
    url = BASE + relative_url
    receipt_path = OUT / f"{name}.receipt.json"
    if receipt_path.exists():
        old = json.loads(receipt_path.read_text(encoding="utf-8"))
        path = ROOT / old["path"]
        if path.exists() and sha(path) == old["sha256"]:
            print(f"CACHED {name}: {old['records']}", flush=True)
            return old
    started = now()
    metadata = get(url, f="json")
    service_metadata = get(url.rsplit("/", 1)[0], f="json")
    save(OUT / f"{name}.metadata.json", {"url": url, "retrieved_at": started, "layer": metadata, "service": service_metadata})
    spatial = bool(metadata.get("geometryType"))
    path = OUT / (name + (".geojson" if spatial else ".json"))
    partial = path.with_suffix(path.suffix + ".part")
    oid_response = get(url + "/query", f="json", where="1=1", returnIdsOnly="true")
    oid = oid_response["objectIdFieldName"]
    expected_ids = sorted(oid_response.get("objectIds") or [])
    count = get(url + "/query", f="json", where="1=1", returnCountOnly="true")["count"]
    if len(expected_ids) != count or len(set(expected_ids)) != count:
        raise RuntimeError(f"Source changed while counting {name}")
    fields = [f["name"] for f in metadata["fields"] if f["type"] != "esriFieldTypeGeometry"]
    seen = []
    pages = []
    page_size = min(1000, metadata.get("maxRecordCount", 1000))
    print(f"DOWNLOADING {name}: {count}", flush=True)
    with partial.open("w", encoding="utf-8", newline="\n") as output:
        output.write('{"type":"FeatureCollection","features":[\n' if spatial else '{"records":[\n')
        first = True
        for offset in range(0, count, page_size):
            batch = expected_ids[offset:offset + page_size]
            params = {"f": "geojson" if spatial else "json", "where": f"{oid}>={batch[0]} AND {oid}<={batch[-1]}",
                      "outFields": "*", "returnGeometry": "true" if spatial else "false", "outSR": 4326,
                      "orderByFields": oid + " ASC", "resultRecordCount": page_size}
            raw = fetch(url + "/query", params)
            data = json.loads(raw)
            if "error" in data:
                raise RuntimeError(str(data["error"]))
            features = data.get("features", [])
            attrs = [f["properties" if spatial else "attributes"] for f in features]
            actual = [a[oid] for a in attrs]
            if actual != batch:
                raise RuntimeError(f"{name}: source page IDs differ at {offset}")
            for feature, properties in zip(features, attrs):
                if set(fields) - set(properties):
                    raise RuntimeError(f"{name}: source fields missing")
                if not first:
                    output.write(",\n")
                output.write(json.dumps(feature if spatial else properties, ensure_ascii=False, separators=(",", ":")))
                first = False
            seen.extend(actual)
            pages.append({"first_id": batch[0], "last_id": batch[-1], "records": len(features),
                          "response_sha256": hashlib.sha256(raw).hexdigest()})
            if offset % 20000 == 0 or len(seen) == count:
                print(f"PROGRESS {name}: {len(seen)}/{count}", flush=True)
        output.write("\n]}\n")
    final_ids = sorted(get(url + "/query", f="json", where="1=1", returnIdsOnly="true").get("objectIds") or [])
    if seen != expected_ids or final_ids != expected_ids:
        raise RuntimeError(f"{name}: source ID set changed during download")
    partial.replace(path)
    is_open = relative_url.startswith("OpenData/")
    result = {"id": name, "name": metadata["name"], "url": url, "query_url": url + "/query",
              "path": str(path.relative_to(ROOT)), "records": count, "expected_records": count,
              "source_ids_stable": True, "object_id_field": oid, "source_fields": fields,
              "geometry_type": metadata.get("geometryType"), "crs": "EPSG:4326" if spatial else None,
              "source_crs": (metadata.get("extent") or {}).get("spatialReference"),
              "bytes": path.stat().st_size, "sha256": sha(path), "pages": pages,
              "started_at": started, "downloaded_at": now(), "status": "downloaded",
              "licence": "Open Government Licence - Burnaby" if is_open else "BurnabyMap public terms; no dataset-specific open licence located",
              "licence_url": LICENCE_URL if is_open else TERMS_URL,
              "licence_scope": "Official OpenData catalog service" if is_open else "Public municipal API; as-is disclaimer and City copyright. No explicit redistribution prohibition found in inspected terms; public access alone is not an open licence.",
              "attribution": "City of Burnaby; contains information licensed under the Open Government Licence - British Columbia." if is_open else "City of Burnaby",
              "assessment_year": None,
              "assessment_period_note": "Source uses CURRENT and PREV field labels with no explicit assessment year in table or metadata; do not assign a calendar year." if name == "property-assessments" else None,
              "snapshot_note": "All source fields retained, nulls unchanged, complete unsimplified WGS84 geometry. IDs checked before/after; attributes not transactionally locked."}
    save(receipt_path, result)
    print(f"COMPLETE {name}: {count}, {result['bytes']} bytes", flush=True)
    return result

def main():
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "open-government-licence.html").write_bytes(fetch(LICENCE_URL))
    licence_data = get("https://www.arcgis.com/sharing/rest/content/items/5651cd6a185c42288dfc19d102f40bc5/data", f="json")
    save(OUT / "open-government-licence.content.json", licence_data)
    terms = get(TERMS_URL)
    save(OUT / "burnabymap-terms.json", terms)
    terms_text = html.unescape(re.sub("<[^>]+>", " ", terms["splash"]["splashContent"]))
    (OUT / "burnabymap-terms.txt").write_text(re.sub(r"\s+", " ", terms_text), encoding="utf-8")
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(download, DATASETS.items()))
    save(OUT / "report.json", {"publisher": "City of Burnaby", "created_at": now(),
        "catalogue": "https://data.burnaby.ca/", "licence_url": LICENCE_URL,
        "public_map_terms_url": TERMS_URL, "datasets": results,
        "total_records": sum(r["records"] for r in results), "total_bytes": sum(r["bytes"] for r in results),
        "important_notes": ["Assessment year is not explicitly published; CURRENT/PREV preserved without guessed years.",
            "Property-assessments is a full nonspatial public table. Keep distinct unit rows; join normalized PTS_LTO_PID to parcel PID.",
            "Community plan areas are planning boundaries, not a comprehensive neighbourhood partition.",
            "Property-assessments and capital-works are public APIs outside the OpenData catalog; their licence scope differs and is recorded per dataset."]})
    print("COMPLETE Burnaby archive", flush=True)

if __name__ == "__main__":
    main()
