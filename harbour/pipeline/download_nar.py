"""Archive complete BC National Address Register CSVs and Metro boundaries.

Reads the official ZIP directory using HTTP Range, then downloads only the five
British Columbia members. Compressed payloads resume safely within an ETag-scoped
directory. Every CSV is checked against ZIP CRC32 and uncompressed size, counted
with the CSV parser, and receipted with SHA256. No third-party credentials needed.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import csv
import hashlib
import json
import os
from pathlib import Path
import re
import struct
import time
import urllib.parse
import urllib.request
import zlib


ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / "data" / "raw"
NAR = RAW / "nar-bc"
ARCHIVE_URL = "https://www150.statcan.gc.ca/n1/pub/46-26-0002/2022001/202606.zip"
NAR_PAGE = "https://www150.statcan.gc.ca/n1/pub/46-26-0002/462600022022001-eng.htm"
NAR_LICENCE = "https://www.statcan.gc.ca/en/reference/licence"
BOUNDARY_LAYER = (
    "https://services6.arcgis.com/56eqCzQ5SZhBaDST/arcgis/rest/services/"
    "Administrative_Boundaries/FeatureServer/10"
)
BOUNDARY_ITEM = "https://www.arcgis.com/sharing/rest/content/items/1c86f57d9fcc4fc3a8134328b07f07e6"
USER_AGENT = "HarbourMetroHousing/1.0 (official public-data archive)"
BLOCK = 1024 * 1024


def utc_now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def request(url, *, method="GET", headers=None, timeout=90):
    values = {"User-Agent": USER_AGENT, "Accept-Encoding": "identity"}
    values.update(headers or {})
    return urllib.request.urlopen(
        urllib.request.Request(url, method=method, headers=values), timeout=timeout
    )


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".part")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


def get_json(url):
    with request(url) as response:
        return json.load(response)


def require_range(response, start, end, total, etag):
    expected = f"bytes {start}-{end}/{total}"
    if response.status != 206 or response.headers.get("Content-Range") != expected:
        raise RuntimeError(f"Expected HTTP 206 {expected}; received {response.status}, "
                           f"{response.headers.get('Content-Range')}")
    returned_etag = response.headers.get("ETag")
    if returned_etag and etag and returned_etag != etag:
        raise RuntimeError("Source ZIP changed during this download")


def read_range(start, end, total, etag):
    headers = {"Range": f"bytes={start}-{end}", "If-Range": etag}
    with request(ARCHIVE_URL, headers=headers) as response:
        require_range(response, start, end, total, etag)
        data = response.read(end - start + 2)
    if len(data) != end - start + 1:
        raise RuntimeError("Incomplete ZIP metadata range")
    return data


def archive_directory():
    with request(ARCHIVE_URL, method="HEAD") as response:
        total = int(response.headers["Content-Length"])
        etag = response.headers.get("ETag", "")
        modified = response.headers.get("Last-Modified")
    if not etag:
        raise RuntimeError("Archive has no ETag; cannot safely resume across revisions")
    tail = read_range(max(0, total - 65557), total - 1, total, etag)
    pos = tail.rfind(b"PK\x05\x06")
    if pos < 0:
        raise RuntimeError("ZIP end-of-central-directory record not found")
    end = struct.unpack_from("<4s4H2LH", tail, pos)
    disk, directory_disk, disk_entries, entries, directory_size, directory_offset, _ = end[1:]
    if disk or directory_disk or disk_entries != entries or entries == 65535:
        raise RuntimeError("Unsupported split or ZIP64 archive; inspect updated source")
    directory = read_range(directory_offset, directory_offset + directory_size - 1, total, etag)
    members = []
    pos = 0
    for _ in range(entries):
        values = struct.unpack_from("<4s6H3L5H2L", directory, pos)
        if values[0] != b"PK\x01\x02":
            raise RuntimeError("Invalid ZIP central directory signature")
        name_length, extra_length, comment_length = values[10:13]
        name = directory[pos + 46:pos + 46 + name_length].decode("utf-8")
        if re.fullmatch(r"(?:Locations/Location|Addresses/Address)_59_part_\d+\.csv", name):
            members.append({
                "name": name,
                "flags": values[3],
                "compression_method": values[4],
                "crc32": f"{values[7]:08x}",
                "compressed_bytes": values[8],
                "uncompressed_bytes": values[9],
                "local_header_offset": values[16],
            })
        pos += 46 + name_length + extra_length + comment_length
    if len(members) != 5:
        raise RuntimeError(f"Expected five BC members, found {len(members)}")
    for member in members:
        offset = member["local_header_offset"]
        local = read_range(offset, offset + 29, total, etag)
        values = struct.unpack("<4s5H3L2H", local)
        if values[0] != b"PK\x03\x04" or values[3] != member["compression_method"]:
            raise RuntimeError("ZIP member local header does not match directory")
        if member["flags"] & 1 or member["compression_method"] != 8:
            raise RuntimeError("Only unencrypted deflate ZIP members are supported")
        start = offset + 30 + values[-2] + values[-1]
        member.update(payload_start=start, payload_end=start + member["compressed_bytes"] - 1)
    metadata = {
        "url": ARCHIVE_URL, "source_page": NAR_PAGE, "edition": "June 2026",
        "etag": etag, "last_modified": modified, "archive_bytes": total,
        "archive_entries": entries, "retrieved_at": utc_now(), "members": members,
        "licence": "Statistics Canada Open Licence Agreement", "licence_url": NAR_LICENCE,
    }
    atomic_json(NAR / "archive.metadata.json", metadata)
    return metadata


def file_digest(path):
    sha = hashlib.sha256()
    crc = 0
    size = 0
    with path.open("rb") as stream:
        while block := stream.read(BLOCK):
            sha.update(block)
            crc = zlib.crc32(block, crc)
            size += len(block)
    return size, sha.hexdigest(), f"{crc & 0xffffffff:08x}"


def count_csv(path):
    csv.field_size_limit(16 * 1024 * 1024)
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        rows = csv.reader(stream)
        header = next(rows)
        count = 0
        for row in rows:
            if len(row) != len(header):
                raise RuntimeError(f"Malformed CSV row {count + 2} in {path.name}")
            count += 1
    return header, count


def archive_member(member, metadata):
    name = Path(member["name"]).name
    destination = NAR / name
    receipt_path = NAR / (name + ".receipt.json")
    if destination.exists() and receipt_path.exists():
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        if receipt.get("archive_etag") == metadata["etag"]:
            size, sha, crc = file_digest(destination)
            if (size == member["uncompressed_bytes"] and sha == receipt.get("sha256")
                    and crc == member["crc32"]):
                print("CACHED", name, receipt["records"], "records", flush=True)
                return receipt
    tag = hashlib.sha256(metadata["etag"].encode()).hexdigest()[:16]
    cache_dir = NAR / ".downloads" / tag
    cache_dir.mkdir(parents=True, exist_ok=True)
    compressed = cache_dir / (name + ".deflate.part")
    expected = member["compressed_bytes"]
    for attempt in range(6):
        current = compressed.stat().st_size if compressed.exists() else 0
        if current == expected:
            break
        if current > expected:
            raise RuntimeError(f"Oversized resumable payload: {compressed}")
        start = member["payload_start"] + current
        end = member["payload_end"]
        print("DOWNLOADING", name, f"{current}/{expected}", flush=True)
        try:
            headers = {"Range": f"bytes={start}-{end}", "If-Range": metadata["etag"]}
            with request(ARCHIVE_URL, headers=headers) as response, compressed.open("ab") as output:
                require_range(response, start, end, metadata["archive_bytes"], metadata["etag"])
                while block := response.read(min(BLOCK, expected - current)):
                    output.write(block)
                    current += len(block)
                    if current == expected:
                        break
                output.flush()
                os.fsync(output.fileno())
            if current != expected:
                raise RuntimeError(f"Incomplete compressed payload {current}/{expected}")
            break
        except Exception as exc:
            if attempt == 5:
                raise
            print("RETRY", name, attempt + 1, str(exc), flush=True)
            time.sleep(min(2 * (attempt + 1), 10))
    else:
        raise RuntimeError(f"Unable to download {name}")
    part = destination.with_name(destination.name + ".part")
    decompressor = zlib.decompressobj(-15)
    sha = hashlib.sha256()
    compressed_sha = hashlib.sha256()
    crc = 0
    size = 0
    with compressed.open("rb") as source, part.open("wb") as output:
        while block := source.read(BLOCK):
            compressed_sha.update(block)
            data = decompressor.decompress(block)
            output.write(data)
            sha.update(data)
            crc = zlib.crc32(data, crc)
            size += len(data)
        data = decompressor.flush()
        output.write(data)
        sha.update(data)
        crc = zlib.crc32(data, crc)
        size += len(data)
    actual_crc = f"{crc & 0xffffffff:08x}"
    if not decompressor.eof or decompressor.unused_data:
        raise RuntimeError(f"Incomplete or overlong deflate stream for {name}")
    if size != member["uncompressed_bytes"] or actual_crc != member["crc32"]:
        raise RuntimeError(f"ZIP CRC/size verification failed for {name}")
    header, count = count_csv(part)
    part.replace(destination)
    receipt = {
        "id": name.removesuffix(".csv"), "url": ARCHIVE_URL,
        "source_page": NAR_PAGE, "edition": "June 2026", "archive_member": member["name"],
        "archive_etag": metadata["etag"], "archive_last_modified": metadata["last_modified"],
        "path": str(destination.relative_to(ROOT)), "format": "csv", "bytes": size,
        "sha256": sha.hexdigest(), "crc32": actual_crc, "zip_crc32_verified": True,
        "records": count, "columns": header, "compressed_bytes": expected,
        "compressed_sha256": compressed_sha.hexdigest(),
        "compressed_payload_path": str(compressed.relative_to(ROOT)),
        "byte_range": [member["payload_start"], member["payload_end"]],
        "downloaded_at": utc_now(), "licence": metadata["licence"],
        "licence_url": NAR_LICENCE, "status": "downloaded",
    }
    atomic_json(receipt_path, receipt)
    print("COMPLETE", name, count, "records", size, "bytes", flush=True)
    return receipt


def download_boundaries():
    path = RAW / "metro-municipalities.geojson"
    count_url = BOUNDARY_LAYER + "/query?" + urllib.parse.urlencode({
        "where": "1=1", "returnCountOnly": "true", "f": "json",
    })
    expected = get_json(count_url)["count"]
    url = BOUNDARY_LAYER + "/query?" + urllib.parse.urlencode({
        "where": "1=1", "outFields": "*", "outSR": "4326", "f": "geojson",
        "orderByFields": "OBJECTID", "resultRecordCount": "2000", "resultOffset": "0",
    })
    with request(url) as response:
        raw = response.read()
    data = json.loads(raw)
    features = data.get("features", [])
    if len(features) != expected or data.get("exceededTransferLimit"):
        raise RuntimeError(f"Incomplete boundaries: {len(features)}/{expected}")
    if data.get("type") != "FeatureCollection" or any(not f.get("geometry") for f in features):
        raise RuntimeError("Invalid boundary geometry collection")
    part = path.with_name(path.name + ".part")
    part.write_bytes(raw)
    part.replace(path)
    item = get_json(BOUNDARY_ITEM + "?f=json")
    atomic_json(RAW / "metro-municipalities.metadata.json", item)
    receipt = {
        "id": "metro-municipalities", "url": url, "count_url": count_url,
        "source_layer": BOUNDARY_LAYER, "source_item": BOUNDARY_ITEM,
        "path": str(path.relative_to(ROOT)), "format": "geojson", "bytes": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(), "records": len(features),
        "expected_records": expected, "jurisdiction_names": sorted({
            f["properties"]["ShortName"] for f in features
        }), "downloaded_at": utc_now(), "licence": "Open Government Licence - Metro Vancouver",
        "licence_url": "https://open-data-portal-metrovancouver.hub.arcgis.com/pages/Open%20Government%20Licence",
        "licence_metadata": item.get("licenseInfo"), "status": "downloaded",
        "notes": "All polygon parts preserved. Normalize Tsawassen spelling; do not key by MunNum.",
    }
    atomic_json(RAW / "metro-municipalities.receipt.json", receipt)
    print("BOUNDARIES_READY", path, len(features), "parts", flush=True)
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--boundaries-only", action="store_true")
    parser.add_argument("--nar-only", action="store_true")
    parser.add_argument("--workers", type=int, default=3)
    args = parser.parse_args()
    RAW.mkdir(parents=True, exist_ok=True)
    NAR.mkdir(parents=True, exist_ok=True)
    if not args.nar_only:
        download_boundaries()
    if args.boundaries_only:
        return
    metadata = archive_directory()
    receipts = []
    failures = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, min(args.workers, 5))) as pool:
        futures = {pool.submit(archive_member, member, metadata): member for member in metadata["members"]}
        for future in concurrent.futures.as_completed(futures):
            try:
                receipts.append(future.result())
            except Exception as exc:
                failure = {"archive_member": futures[future]["name"], "error": str(exc)}
                failures.append(failure)
                print("FAILED", json.dumps(failure), flush=True)
            atomic_json(NAR / "manifest.json", {
                "url": ARCHIVE_URL, "source_page": NAR_PAGE, "edition": "June 2026",
                "archive_etag": metadata["etag"], "updated_at": utc_now(),
                "status": "complete" if len(receipts) == 5 else "in_progress" if not failures else "incomplete",
                "expected_members": 5, "downloaded_members": len(receipts),
                "bytes": sum(r["bytes"] for r in receipts),
                "records": sum(r["records"] for r in receipts),
                "licence": metadata["licence"], "licence_url": NAR_LICENCE,
                "files": sorted(receipts, key=lambda r: r["archive_member"]), "failures": failures,
            })
    if failures:
        raise SystemExit(1)
    print("NAR_COMPLETE", len(receipts), "files", sum(r["bytes"] for r in receipts), "bytes", flush=True)


if __name__ == "__main__":
    main()
