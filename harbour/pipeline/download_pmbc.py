"""Download and verify the complete Metro Vancouver ParcelMap BC WFS export.

Raw WGS84 GeoJSON pages and SHA-256 receipts are retained under
data/raw/pmbc-metro. Reruns validate existing files before resuming. No API key
is required. The final receipt is only marked complete after every record ID
has been checked for uniqueness and the page totals match a fresh WFS count.
"""

from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import random
import time
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET


WORKSPACE = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = WORKSPACE / "data" / "raw" / "pmbc-metro"
ENDPOINT = "https://openmaps.gov.bc.ca/geo/pub/ows"
DATASET = "WHSE_CADASTRE.PMBC_PARCEL_FABRIC_POLY_SVW"
FILTER = "REGIONAL_DISTRICT='Metro Vancouver Regional District'"
ID_FIELD = "PARCEL_FABRIC_POLY_ID"
SOURCE_PAGE = "https://open.canada.ca/data/en/dataset/4cf233c2-f020-4f7a-9b87-1923252fbc24"
LICENCE = "Open Government Licence - British Columbia"
LICENCE_URL = "https://www2.gov.bc.ca/gov/content/data/open-data/open-government-licence-bc"


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def parameters(**extras: object) -> dict[str, object]:
    result: dict[str, object] = {
        "service": "WFS",
        "version": "2.0.0",
        "request": "GetFeature",
        "typeNames": DATASET,
        "cql_filter": FILTER,
        "outputFormat": "application/json",
        "srsName": "EPSG:4326",
        "sortBy": ID_FIELD,
    }
    result.update(extras)
    return result


def make_url(**extras: object) -> str:
    return ENDPOINT + "?" + urlencode(parameters(**extras))


def fetch(url: str, timeout: int, retries: int) -> bytes:
    for attempt in range(retries + 1):
        try:
            request = Request(url, headers={"User-Agent": "Harbour-Housing-OpenData/1.0"})
            with urlopen(request, timeout=timeout) as response:
                return response.read()
        except (OSError, HTTPError) as error:
            if attempt == retries:
                raise
            delay = min(60, 2 ** (attempt + 1)) + random.random()
            if isinstance(error, HTTPError):
                retry_after = error.headers.get("Retry-After", "")
                if retry_after.isdigit():
                    delay = max(delay, min(120, int(retry_after)))
            print(f"Retry {attempt + 1}/{retries} in {delay:.1f}s: {error}", flush=True)
            time.sleep(delay)
    raise RuntimeError("Unreachable request state")


def atomic_bytes(path: Path, payload: bytes) -> None:
    temporary = path.with_suffix(path.suffix + ".part")
    temporary.write_bytes(payload)
    temporary.replace(path)


def write_json(path: Path, value: object) -> None:
    atomic_bytes(path, (json.dumps(value, indent=2, ensure_ascii=False) + "\n").encode("utf-8"))


def record_count(timeout: int, retries: int) -> int:
    body = fetch(make_url(resultType="hits"), timeout, retries)
    try:
        root = ET.fromstring(body)
        count = root.attrib.get("numberMatched")
    except ET.ParseError:
        result = json.loads(body)
        count = result.get("numberMatched", result.get("totalFeatures"))
    if count is None or str(count) == "unknown":
        raise ValueError("The WFS did not provide a complete record count")
    return int(count)


def validate_page(body: bytes, offset: int, expected: int, total: int) -> tuple[dict, list[int]]:
    page = json.loads(body)
    if page.get("type") != "FeatureCollection":
        raise ValueError(f"Page {offset}: expected GeoJSON FeatureCollection")
    features = page.get("features", [])
    if len(features) != expected:
        raise ValueError(f"Page {offset}: got {len(features)} records, expected {expected}; refusing a capped export")
    matched = page.get("numberMatched", page.get("totalFeatures"))
    if matched is not None and int(matched) != total:
        raise ValueError(f"Source changed during download: expected {total}, WFS reports {matched}")
    ids = [int(feature["properties"][ID_FIELD]) for feature in features]
    if any(left >= right for left, right in zip(ids, ids[1:])):
        raise ValueError(f"Page {offset}: IDs are not strictly increasing")
    if any(feature.get("geometry") is None for feature in features):
        raise ValueError(f"Page {offset}: missing parcel geometry")
    return page, ids


def page_paths(output: Path, offset: int) -> tuple[Path, Path]:
    stem = f"page-{offset:09d}"
    return output / f"{stem}.geojson", output / f"{stem}.receipt.json"


def download_page(output: Path, offset: int, page_size: int, total: int, timeout: int, retries: int) -> dict:
    raw_path, receipt_path = page_paths(output, offset)
    expected = min(page_size, total - offset)
    url = make_url(count=page_size, startIndex=offset)
    if raw_path.exists() and receipt_path.exists():
        try:
            body = raw_path.read_bytes()
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            if receipt.get("sha256") != hashlib.sha256(body).hexdigest():
                raise ValueError("checksum mismatch")
            if receipt.get("url") != url or receipt.get("total_records") != total:
                raise ValueError("query or source count changed")
            validate_page(body, offset, expected, total)
            return {**receipt, "resumed": True}
        except (OSError, ValueError, KeyError, TypeError) as error:
            print(f"Re-fetching invalid cached page {offset}: {error}", flush=True)
    body = fetch(url, timeout, retries)
    page, ids = validate_page(body, offset, expected, total)
    receipt = {
        "dataset": DATASET,
        "source_page": SOURCE_PAGE,
        "licence": LICENCE,
        "licence_url": LICENCE_URL,
        "url": url,
        "start_index": offset,
        "requested_count": page_size,
        "record_count": len(ids),
        "total_records": total,
        "first_id": ids[0],
        "last_id": ids[-1],
        "bytes": len(body),
        "sha256": hashlib.sha256(body).hexdigest(),
        "downloaded_at": now(),
        "source_timestamp": page.get("timeStamp"),
        "file": raw_path.name,
    }
    atomic_bytes(raw_path, body)
    write_json(receipt_path, receipt)
    return {**receipt, "resumed": False}


def verify_all(output: Path, total: int, page_size: int) -> dict:
    unique_ids: set[int] = set()
    municipalities: Counter[str] = Counter()
    parcel_classes: Counter[str] = Counter()
    pages: list[dict] = []
    previous_id: int | None = None
    total_bytes = 0
    for offset in range(0, total, page_size):
        raw_path, receipt_path = page_paths(output, offset)
        body = raw_path.read_bytes()
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        digest = hashlib.sha256(body).hexdigest()
        if receipt.get("sha256") != digest:
            raise ValueError(f"Final checksum mismatch: {raw_path.name}")
        page, ids = validate_page(body, offset, min(page_size, total - offset), total)
        if previous_id is not None and ids[0] <= previous_id:
            raise ValueError(f"Cross-page order or duplication error at offset {offset}")
        for feature, parcel_id in zip(page["features"], ids):
            if parcel_id in unique_ids:
                raise ValueError(f"Duplicate {ID_FIELD}: {parcel_id}")
            unique_ids.add(parcel_id)
            properties = feature["properties"]
            municipalities[str(properties.get("MUNICIPALITY") or "Unspecified")] += 1
            parcel_classes[str(properties.get("PARCEL_CLASS") or "Unspecified")] += 1
        previous_id = ids[-1]
        total_bytes += len(body)
        pages.append({"file": raw_path.name, "record_count": len(ids), "sha256": digest,
                      "first_id": ids[0], "last_id": ids[-1], "bytes": len(body)})
    if len(unique_ids) != total:
        raise ValueError(f"Unique count mismatch: {len(unique_ids)} != {total}")
    return {
        "record_count": len(unique_ids),
        "unique_ids": len(unique_ids),
        "page_count": len(pages),
        "bytes": total_bytes,
        "municipalities": dict(sorted(municipalities.items())),
        "parcel_classes": dict(sorted(parcel_classes.items())),
        "pages": pages,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--page-size", type=int, default=5000)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--retries", type=int, default=6)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    if not 1 <= args.workers <= 4:
        parser.error("--workers must be between 1 and 4")
    if not 1 <= args.page_size <= 5000:
        parser.error("--page-size must be between 1 and 5000")
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    started = now()
    total = record_count(args.timeout, args.retries)
    if total < 1:
        raise ValueError("Refusing an empty Metro Vancouver result")
    manifest_path = output / "manifest.json"
    manifest = {
        "status": "verifying" if args.verify_only else "downloading",
        "dataset": DATASET,
        "source": ENDPOINT,
        "source_page": SOURCE_PAGE,
        "filter": FILTER,
        "licence": LICENCE,
        "licence_url": LICENCE_URL,
        "attribution": "Contains information licensed under the Open Government Licence - British Columbia.",
        "started_at": started,
        "expected_records": total,
        "page_size": args.page_size,
        "expected_pages": math.ceil(total / args.page_size),
        "coordinate_system": "EPSG:4326",
        "sort_field": ID_FIELD,
    }
    write_json(manifest_path, manifest)
    print(f"Source count {total:,}; {manifest['expected_pages']} pages; {args.workers} workers; output {output}", flush=True)
    try:
        if not args.verify_only:
            complete = 0
            records = 0
            with ThreadPoolExecutor(max_workers=args.workers) as executor:
                futures = [executor.submit(download_page, output, offset, args.page_size,
                                           total, args.timeout, args.retries)
                           for offset in range(0, total, args.page_size)]
                for future in as_completed(futures):
                    receipt = future.result()
                    complete += 1
                    records += receipt["record_count"]
                    kind = "verified cache" if receipt["resumed"] else "downloaded"
                    print(f"{complete}/{manifest['expected_pages']} pages, {records:,}/{total:,} records; {kind} {receipt['file']}", flush=True)
                    write_json(manifest_path, {**manifest, "completed_pages": complete,
                                             "completed_records": records, "updated_at": now()})
        print("Verifying all SHA-256 checksums, ID order, uniqueness, and totals...", flush=True)
        verified = verify_all(output, total, args.page_size)
        final_count = record_count(args.timeout, args.retries)
        if final_count != total:
            raise ValueError(f"Source changed during download: initial {total}, final {final_count}; rerun to refresh")
        final = {**manifest, **verified, "status": "complete", "completed_at": now(),
                 "final_source_count": final_count, "verification": "All raw page checksums, geometry presence, sorted IDs, global unique IDs, and fresh source count match."}
        write_json(manifest_path, final)
        print(json.dumps({k: final[k] for k in ["status", "record_count", "unique_ids", "page_count", "bytes", "municipalities"]}), flush=True)
    except BaseException as error:
        write_json(manifest_path, {**manifest, "status": "incomplete", "error": str(error), "updated_at": now()})
        raise


if __name__ == "__main__":
    main()
