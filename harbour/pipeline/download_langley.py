"""Archive complete Township of Langley open data with count/checksum receipts.

Only public, read-only ArcGIS queries are used. The original source properties
are preserved, including nulls; geometry is requested as WGS84 GeoJSON.
Run: python harbour/pipeline/download_langley.py
"""
from __future__ import annotations

import concurrent.futures
import datetime as dt
import hashlib
import json
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / "data" / "raw"
BASE = "https://services5.arcgis.com/frpHL0Fv8koQRVWY/ArcGIS/rest/services/"
LICENCE_URL = "https://www.tol.ca/en/connect/open-data-licence.aspx"
LICENCE = "Open Government License - Township of Langley (version 2)"
ATTRIBUTION = "Contains information licensed under the Open Government License - Township of Langley."
DATASETS = {
    "langley-township-assessments-2026": "Assessments_2026/FeatureServer/0",
    "langley-township-parcel-attributes": "Parcel_Attributes/FeatureServer/0",
}
PAGE_SIZE = 1000


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def request_bytes(url, params=None):
    if params:
        url += "?" + urllib.parse.urlencode(params)
    last_error = None
    for attempt in range(5):
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": "HarbourVancouver/1.0 (public-data archive)"}
            )
            with urllib.request.urlopen(req, timeout=120) as response:
                return response.read()
        except Exception as exc:
            last_error = exc
            if attempt < 4:
                time.sleep(2 ** attempt)
    raise RuntimeError(f"Public data request failed: {url}") from last_error


def request_json(url, params=None):
    value = json.loads(request_bytes(url, params))
    if "error" in value:
        raise RuntimeError(f"ArcGIS error at {url}: {value['error']}")
    return value


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def checksum(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def archive_dataset(item):
    name, service_path = item
    layer_url = BASE + service_path
    path = RAW / f"{name}.geojson"
    receipt_path = RAW / f"{name}.receipt.json"
    if path.exists() and receipt_path.exists():
        cached = json.loads(receipt_path.read_text(encoding="utf-8"))
        if cached.get("sha256") == checksum(path) and cached.get("status") == "downloaded":
            print(f"CACHED {name}: {cached['records']} records", flush=True)
            return cached

    started_at = now()
    metadata = request_json(layer_url, {"f": "json"})
    service = request_json(layer_url.rsplit("/", 1)[0], {"f": "json"})
    item_id = service.get("serviceItemId")
    item_metadata = request_json(
        f"https://www.arcgis.com/sharing/rest/content/items/{item_id}", {"f": "json"}
    ) if item_id else None
    write_json(RAW / f"{name}.metadata.json", {
        "retrieved_at": started_at, "layer_url": layer_url,
        "layer": metadata, "service": service, "item": item_metadata,
    })

    query_url = layer_url + "/query"
    ids_response = request_json(query_url, {"f": "json", "where": "1=1", "returnIdsOnly": "true"})
    oid_field = ids_response["objectIdFieldName"]
    object_ids = sorted(ids_response["objectIds"])
    expected_count = request_json(query_url, {
        "f": "json", "where": "1=1", "returnCountOnly": "true",
    })["count"]
    if len(object_ids) != expected_count or len(set(object_ids)) != expected_count:
        raise RuntimeError(f"{name}: source changed while reading initial record IDs/count")
    expected_fields = {field["name"] for field in metadata["fields"]}
    observed_ids = []
    page_receipts = []
    partial = path.with_suffix(".geojson.part")
    print(f"DOWNLOADING {name}: {expected_count} records", flush=True)

    # Capture stable sorted IDs first, then query bounded ID ranges. This avoids
    # offset paging omissions when an upstream publisher inserts/deletes rows.
    with partial.open("w", encoding="utf-8", newline="\n") as output:
        output.write('{"type":"FeatureCollection","features":[\n')
        first = True
        for offset in range(0, len(object_ids), PAGE_SIZE):
            batch = object_ids[offset:offset + PAGE_SIZE]
            params = {
                "f": "geojson", "where": f"{oid_field} >= {batch[0]} AND {oid_field} <= {batch[-1]}",
                "outFields": "*", "outSR": "4326", "returnGeometry": "true",
                "orderByFields": oid_field + " ASC", "resultRecordCount": PAGE_SIZE,
            }
            raw_page = request_bytes(query_url, params)
            page = json.loads(raw_page)
            if "error" in page:
                raise RuntimeError(f"{name}: {page['error']}")
            features = page.get("features", [])
            actual_ids = [feature["properties"][oid_field] for feature in features]
            if actual_ids != batch:
                raise RuntimeError(f"{name}: missing, reordered or duplicate source records at offset {offset}")
            for feature in features:
                missing_fields = expected_fields - set(feature["properties"])
                if missing_fields:
                    raise RuntimeError(f"{name}: source fields omitted: {missing_fields}")
                if not first:
                    output.write(",\n")
                output.write(json.dumps(feature, ensure_ascii=False, separators=(",", ":")))
                first = False
            observed_ids.extend(actual_ids)
            page_receipts.append({
                "first_object_id": batch[0], "last_object_id": batch[-1],
                "records": len(features), "response_bytes": len(raw_page),
                "response_sha256": hashlib.sha256(raw_page).hexdigest(),
            })
            if (offset // PAGE_SIZE) % 10 == 0 or offset + PAGE_SIZE >= expected_count:
                print(f"PROGRESS {name}: {len(observed_ids)}/{expected_count}", flush=True)
        output.write("\n]}\n")

    final_ids = sorted(request_json(query_url, {
        "f": "json", "where": "1=1", "returnIdsOnly": "true",
    })["objectIds"])
    if observed_ids != object_ids or final_ids != object_ids:
        raise RuntimeError(f"{name}: source IDs changed during download; rerun to obtain a consistent archive")
    partial.replace(path)
    receipt = {
        "id": name, "status": "downloaded", "url": layer_url,
        "query_url": query_url, "path": str(path.relative_to(ROOT)),
        "format": "GeoJSON", "geometry_crs": "EPSG:4326",
        "original_geometry_crs": metadata.get("extent", {}).get("spatialReference"),
        "source_fields": [field["name"] for field in metadata["fields"]],
        "source_geometry_type": metadata.get("geometryType"),
        "records": len(observed_ids), "expected_records": expected_count,
        "unique_object_ids": len(set(observed_ids)), "source_ids_stable": True,
        "object_id_field": oid_field, "page_size": PAGE_SIZE, "pages": page_receipts,
        "bytes": path.stat().st_size, "sha256": checksum(path),
        "started_at": started_at, "downloaded_at": now(),
        "licence": LICENCE, "licence_url": LICENCE_URL,
        "licence_archive": "data/raw/langley-township-licence.html",
        "attribution": ATTRIBUTION,
        "source_item_id": item_id,
        "source_item_url": f"https://www.arcgis.com/home/item.html?id={item_id}",
        "preservation": "All source attributes and complete geometry returned by the official GeoJSON export; no simplification, filtering or null replacement.",
        "snapshot_note": "Stable record ID set checked before/after export; attribute edits within the export window are not transactionally locked by the public API.",
    }
    write_json(receipt_path, receipt)
    print(f"COMPLETE {name}: {receipt['records']} records, {receipt['bytes']} bytes", flush=True)
    return receipt


def main():
    RAW.mkdir(parents=True, exist_ok=True)
    licence_path = RAW / "langley-township-licence.html"
    licence_path.write_bytes(request_bytes(LICENCE_URL))
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        receipts = list(pool.map(archive_dataset, DATASETS.items()))
    write_json(RAW / "langley-township-manifest.json", {
        "created_at": now(), "publisher": "Township of Langley",
        "catalogue": "https://data-tol.opendata.arcgis.com/",
        "licence": LICENCE, "licence_url": LICENCE_URL, "attribution": ATTRIBUTION,
        "licence_file": str(licence_path.relative_to(ROOT)),
        "licence_sha256": checksum(licence_path), "datasets": receipts,
        "records": sum(row["records"] for row in receipts),
        "bytes": sum(row["bytes"] for row in receipts),
    })
    print("COMPLETE Township of Langley raw archive", flush=True)


if __name__ == "__main__":
    main()
