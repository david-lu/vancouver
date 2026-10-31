"""Preserve regional source records in SQLite and prepare honest map properties.

NAR site-to-parcel matches are spatial approximations, never strata-unit joins.
Only the official Township of Langley assessment source supplies values here.
Run --addresses-only while the complete provincial parcel archive is downloading.
"""
from __future__ import annotations

import argparse
import collections
import csv
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3
import time

import numpy as np
import shapely
from shapely.geometry import shape, mapping
from shapely.strtree import STRtree

ROOT = Path(__file__).resolve().parents[2]
RAW, OUT = ROOT / "data/raw", ROOT / "data/prepared"
DB_PATH = ROOT / "data/regional.sqlite"
CANONICAL = ["Vancouver", "Burnaby", "Richmond", "Surrey", "New Westminster",
             "Coquitlam", "Port Coquitlam", "Port Moody", "Delta", "North Vancouver City",
             "North Vancouver District", "West Vancouver", "Langley City", "Langley Township",
             "Maple Ridge", "Pitt Meadows", "White Rock", "Bowen Island", "Lions Bay", "Anmore",
             "Belcarra", "Electoral Area A", "Tsawwassen First Nation"]
PMBC_URL = "https://open.canada.ca/data/en/dataset/4cf233c2-f020-4f7a-9b87-1923252fbc24"
NAR_URL = "https://www150.statcan.gc.ca/n1/pub/46-26-0002/462600022022001-eng.htm"
FIELDS = "id,pid,address,street,neighbourhood,municipality,lat,lon,assessed,land,improvement,tax,year,built,zone,type,change,history,features,parcel".split(",")
BATCH = 10000


def js(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def sha_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def number(value):
    try:
        parsed = float(str(value).replace(",", ""))
        return parsed if math.isfinite(parsed) else None
    except (ValueError, TypeError):
        return None


def clean(value):
    if value is None or str(value).strip().upper() in ("", "NO_DATA", "N/A", "NULL"):
        return None
    return str(value).strip()


def pid(value):
    digits = re.sub(r"\D", "", str(value or ""))
    return digits if len(digits) == 9 and digits != "000000000" else None


def display_pid(value):
    return f"{value[:3]}-{value[3:6]}-{value[6:]}" if value else None


def iter_features(path):
    """Incremental GeoJSON FeatureCollection reader, retaining one feature at a time."""
    decoder = json.JSONDecoder()
    with path.open(encoding="utf-8-sig") as stream:
        buffer = ""
        while True:
            block = stream.read(1024 * 1024)
            if not block:
                raise ValueError(f"Missing features array in {path}")
            buffer += block
            match = re.search(r'"features"\s*:\s*\[', buffer)
            if match:
                buffer = buffer[match.end():]
                break
        while True:
            buffer = buffer.lstrip(" \r\n\t,")
            if buffer.startswith("]"):
                return
            try:
                feature, end = decoder.raw_decode(buffer)
                yield feature
                buffer = buffer[end:]
            except json.JSONDecodeError:
                block = stream.read(1024 * 1024)
                if not block:
                    raise ValueError(f"Truncated GeoJSON feature in {path}")
                buffer += block


def connect():
    db = sqlite3.connect(DB_PATH)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA synchronous=NORMAL")
    db.execute("PRAGMA cache_size=-100000")
    db.execute("PRAGMA temp_store=FILE")
    db.executescript("""
        CREATE TABLE IF NOT EXISTS completed(source TEXT PRIMARY KEY,sha256 TEXT,records INTEGER);
        CREATE TABLE IF NOT EXISTS source_receipts(source TEXT PRIMARY KEY,receipt TEXT);
        CREATE TABLE IF NOT EXISTS raw_boundaries(objectid INTEGER PRIMARY KEY,raw TEXT);
        CREATE TABLE IF NOT EXISTS raw_nar_locations(
          source TEXT,line INTEGER,loc_guid TEXT,lon REAL,lat REAL,municipality TEXT,
          coordinate_method TEXT,raw TEXT,PRIMARY KEY(source,line));
        CREATE TABLE IF NOT EXISTS raw_nar_addresses(
          source TEXT,line INTEGER,addr_guid TEXT,loc_guid TEXT,base_address TEXT,street TEXT,
          unit TEXT,raw TEXT,PRIMARY KEY(source,line));
        CREATE TABLE IF NOT EXISTS raw_langley_attributes(
          objectid INTEGER PRIMARY KEY,property_number TEXT,pid TEXT,raw TEXT);
        CREATE TABLE IF NOT EXISTS raw_langley_assessments(
          objectid INTEGER PRIMARY KEY,property_number TEXT,pid TEXT,raw TEXT);
        CREATE TABLE IF NOT EXISTS raw_pmbc(
          fabric_id INTEGER PRIMARY KEY,pid TEXT,entity_key TEXT,source TEXT,raw TEXT);
        CREATE TABLE IF NOT EXISTS existing_pids(pid TEXT,source TEXT,PRIMARY KEY(pid,source));
        CREATE TABLE IF NOT EXISTS properties(
          id TEXT PRIMARY KEY,pid TEXT,municipality TEXT,assessed REAL,record TEXT);
    """)
    return db


class Municipalities:
    def __init__(self, db):
        self.geojson = read_json(RAW / "metro-municipalities.geojson")
        self.names, self.shapes = [], []
        for feature in self.geojson["features"]:
            original = js(feature)
            props = feature["properties"]
            name = props["ShortName"].replace("Tsawassen", "Tsawwassen")
            if name not in CANONICAL:
                raise ValueError(f"Unexpected municipality {name}")
            props["name"] = name
            polygon = shape(feature["geometry"])
            if not polygon.is_valid:
                polygon = shapely.make_valid(polygon)
            self.names.append(name)
            self.shapes.append(polygon)
            db.execute("INSERT OR REPLACE INTO raw_boundaries VALUES(?,?)", (props["OBJECTID"], original))
        self.tree = STRtree(self.shapes)
        self.areas = np.array([g.area for g in self.shapes])
        self.bounds = shapely.total_bounds(self.shapes)
        db.commit()

    def classify(self, points):
        result = [None] * len(points)
        best = np.full(len(points), np.inf)
        pairs = self.tree.query(points, predicate="covered_by")
        for point_index, polygon_index in zip(*pairs):
            if self.areas[polygon_index] < best[point_index]:
                result[point_index] = self.names[polygon_index]
                best[point_index] = self.areas[polygon_index]
        return result

    def one(self, geom):
        return self.classify(np.array([geom], dtype=object))[0]


def completed(db, source, digest):
    return db.execute("SELECT records FROM completed WHERE source=? AND sha256=?", (source, digest)).fetchone()


def finish_source(db, source, receipt, count):
    expected = receipt.get("records", receipt.get("expected_records"))
    if expected is not None and count != expected:
        raise ValueError(f"{source}: imported {count}, expected {expected}")
    db.execute("INSERT OR REPLACE INTO completed VALUES(?,?,?)", (source, receipt["sha256"], count))
    db.execute("INSERT OR REPLACE INTO source_receipts VALUES(?,?)", (source, js(receipt)))
    db.commit()
    print("ARCHIVED", source, count, flush=True)


def import_locations(db, municipalities):
    for path in sorted((RAW / "nar-bc").glob("Location_*.csv")):
        receipt = read_json(path.with_name(path.name + ".receipt.json"))
        source = "nar-" + path.stem
        if completed(db, source, receipt["sha256"]):
            continue
        count = 0
        batch = []
        with path.open(encoding="utf-8-sig", newline="") as stream:
            for line, row in enumerate(csv.DictReader(stream), 2):
                lon, lat = number(row.get("BF_REPPOINT_LONGITUDE")), number(row.get("BF_REPPOINT_LATITUDE"))
                method = "NAR building footprint representative point"
                if lon is None or lat is None or not (-140 < lon < -110 and 48 < lat < 61):
                    lon, lat = number(row.get("BG_LONGITUDE")), number(row.get("BG_LATITUDE"))
                    method = "NAR best geocode"
                batch.append([source, line, row["LOC_GUID"], lon, lat, None, method, js(row)])
                count += 1
                if len(batch) == BATCH:
                    insert_locations(db, municipalities, batch)
                    batch = []
                    if count % 100000 == 0:
                        print("LOCATIONS", source, count, flush=True)
            if batch:
                insert_locations(db, municipalities, batch)
        finish_source(db, source, receipt, count)
    db.execute("CREATE INDEX IF NOT EXISTS nar_location_guid ON raw_nar_locations(loc_guid)")
    db.execute("CREATE INDEX IF NOT EXISTS nar_location_municipality ON raw_nar_locations(municipality)")
    db.commit()


def insert_locations(db, municipalities, batch):
    minx, miny, maxx, maxy = municipalities.bounds
    eligible = [i for i, row in enumerate(batch) if row[3] is not None and row[4] is not None
                and minx <= row[3] <= maxx and miny <= row[4] <= maxy]
    points = shapely.points([(batch[i][3], batch[i][4]) for i in eligible]) if eligible else []
    for i, name in zip(eligible, municipalities.classify(points) if eligible else []):
        batch[i][5] = name
    db.executemany("INSERT OR REPLACE INTO raw_nar_locations VALUES(?,?,?,?,?,?,?,?)", batch)
    db.commit()


def import_addresses(db):
    for path in sorted((RAW / "nar-bc").glob("Address_*.csv")):
        receipt = read_json(path.with_name(path.name + ".receipt.json"))
        source = "nar-" + path.stem
        if completed(db, source, receipt["sha256"]):
            continue
        batch, count = [], 0
        with path.open(encoding="utf-8-sig", newline="") as stream:
            for line, row in enumerate(csv.DictReader(stream), 2):
                street = " ".join(filter(None, [clean(row.get("OFFICIAL_STREET_NAME")),
                    clean(row.get("OFFICIAL_STREET_TYPE")), clean(row.get("OFFICIAL_STREET_DIR"))]))
                civic = (row.get("CIVIC_NO", "") + row.get("CIVIC_NO_SUFFIX", "")).strip()
                base = f"{civic} {street}".strip() if civic and street else None
                batch.append((source, line, row["ADDR_GUID"], row["LOC_GUID"], base, street or None,
                              clean(row.get("APT_NO_LABEL")), js(row)))
                count += 1
                if len(batch) == BATCH:
                    db.executemany("INSERT OR REPLACE INTO raw_nar_addresses VALUES(?,?,?,?,?,?,?,?)", batch)
                    db.commit()
                    batch = []
                    if count % 100000 == 0:
                        print("ADDRESSES", source, count, flush=True)
            if batch:
                db.executemany("INSERT OR REPLACE INTO raw_nar_addresses VALUES(?,?,?,?,?,?,?,?)", batch)
        finish_source(db, source, receipt, count)
    db.execute("CREATE INDEX IF NOT EXISTS nar_address_location ON raw_nar_addresses(loc_guid)")
    db.execute("CREATE INDEX IF NOT EXISTS nar_address_guid ON raw_nar_addresses(addr_guid)")
    db.commit()


def create_sites(db):
    if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='nar_sites'").fetchone():
        return
    print("BUILDING_NAR_SITES", flush=True)
    db.executescript("""
      CREATE TABLE nar_sites AS
      SELECT l.loc_guid, MIN(l.lon) lon, MIN(l.lat) lat, MIN(l.municipality) municipality,
             MIN(a.base_address) base_address, MIN(a.street) street,
             COUNT(DISTINCT a.base_address) base_count, COUNT(*) address_count
      FROM raw_nar_locations l JOIN raw_nar_addresses a ON l.loc_guid=a.loc_guid
      WHERE l.municipality IS NOT NULL GROUP BY l.loc_guid;
      CREATE UNIQUE INDEX nar_sites_guid ON nar_sites(loc_guid);
    """)
    db.commit()
    print("NAR_SITES_READY", db.execute("SELECT COUNT(*) FROM nar_sites").fetchone()[0], flush=True)


def import_langley(db):
    for suffix, table in [("parcel-attributes", "raw_langley_attributes"),
                          ("assessments-2026", "raw_langley_assessments")]:
        source = "langley-township-" + suffix
        receipt = read_json(RAW / (source + ".receipt.json"))
        if completed(db, source, receipt["sha256"]):
            db.execute(f"CREATE INDEX IF NOT EXISTS {table}_property ON {table}(property_number)")
            db.execute(f"CREATE INDEX IF NOT EXISTS {table}_pid ON {table}(pid)")
            continue
        batch, count = [], 0
        for feature in iter_features(RAW / (source + ".geojson")):
            props = feature["properties"]
            batch.append((props["OBJECTID"], clean(props.get("Property_Number")), pid(props.get("PID")), js(feature)))
            count += 1
            if len(batch) == BATCH:
                db.executemany(f"INSERT OR REPLACE INTO {table} VALUES(?,?,?,?)", batch)
                db.commit()
                batch = []
        if batch:
            db.executemany(f"INSERT OR REPLACE INTO {table} VALUES(?,?,?,?)", batch)
        finish_source(db, source, receipt, count)
        db.execute(f"CREATE INDEX IF NOT EXISTS {table}_property ON {table}(property_number)")
        db.execute(f"CREATE INDEX IF NOT EXISTS {table}_pid ON {table}(pid)")
    db.commit()


def import_vancouver_pids(db):
    source = "existing-vancouver-properties"
    path = OUT / "properties.jsonl"
    fingerprint = f"{path.stat().st_size}:{path.stat().st_mtime_ns}"
    prior = completed(db, source, fingerprint)
    if prior:
        db.execute("INSERT OR IGNORE INTO existing_pids SELECT pid,'langley' FROM raw_langley_assessments WHERE pid IS NOT NULL")
        db.commit()
        return prior[0]
    db.execute("DELETE FROM existing_pids WHERE source='vancouver'")
    count, batch = 0, []
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            row = json.loads(line)
            key = pid(row.get("pid"))
            if key:
                batch.append((key, "vancouver"))
            count += 1
            if len(batch) >= BATCH:
                db.executemany("INSERT OR IGNORE INTO existing_pids VALUES(?,?)", batch)
                batch = []
    db.executemany("INSERT OR IGNORE INTO existing_pids VALUES(?,?)", batch)
    db.execute("INSERT OR REPLACE INTO completed VALUES(?,?,?)", (source, fingerprint, count))
    db.execute("INSERT OR IGNORE INTO existing_pids SELECT pid,'langley' FROM raw_langley_assessments WHERE pid IS NOT NULL")
    db.commit()
    return count


def import_pmbc(db):
    directory = RAW / "pmbc-metro"
    manifest = read_json(directory / "manifest.json")
    if manifest.get("status") not in ("complete", "downloaded", "verified"):
        raise RuntimeError("PMBC archive is incomplete; rerun after its manifest is complete")
    count = 0
    for receipt_path in sorted(directory.glob("page-*.receipt.json")):
        receipt = read_json(receipt_path)
        source = "pmbc-" + receipt_path.name.removesuffix(".receipt.json")
        expected = receipt["record_count"]
        count += expected
        if completed(db, source, receipt["sha256"]):
            continue
        path = directory / receipt["file"]
        if sha_file(path) != receipt["sha256"]:
            raise RuntimeError(f"PMBC page checksum mismatch: {path.name}")
        batch = []
        for feature in iter_features(path):
            props = feature["properties"]
            fabric_id = props["PARCEL_FABRIC_POLY_ID"]
            key = pid(props.get("PID"))
            entity_key = "pid:" + key if key else "fabric:" + str(fabric_id)
            batch.append((fabric_id, key, entity_key, source, js(feature)))
        if len(batch) != expected:
            raise RuntimeError("PMBC page record count mismatch")
        db.executemany("INSERT OR REPLACE INTO raw_pmbc VALUES(?,?,?,?,?)", batch)
        receipt["records"] = expected
        finish_source(db, source, receipt, expected)
    if count != manifest["expected_records"]:
        raise RuntimeError(f"PMBC archive count {count} differs from {manifest['expected_records']}")
    actual = db.execute("SELECT COUNT(*) FROM raw_pmbc").fetchone()[0]
    if actual != count:
        raise RuntimeError(f"PMBC duplicate/missing IDs: expected {count}, got {actual}")
    db.execute("CREATE INDEX IF NOT EXISTS pmbc_entity ON raw_pmbc(entity_key)")
    db.execute("CREATE INDEX IF NOT EXISTS pmbc_pid ON raw_pmbc(pid)")
    db.commit()
    return manifest


def base_property(identity, key, municipality, point, parcel, features):
    row = {field: None for field in FIELDS}
    row.update(id=identity, pid=display_pid(key), municipality=municipality,
               lon=point.x if point is not None else None, lat=point.y if point is not None else None,
               address="Parcel " + (display_pid(key) or identity), history="[]", features=js(features),
               parcel=js(parcel))
    return row


def store_properties(db, rows):
    db.executemany("INSERT OR REPLACE INTO properties VALUES(?,?,?,?,?)",
                   [(p["id"], pid(p["pid"]), p["municipality"], p["assessed"], js(p)) for p in rows])
    db.commit()


def prepare_langley(db, municipalities):
    assessment_receipt = read_json(RAW / "langley-township-assessments-2026.receipt.json")
    attributes_receipt = read_json(RAW / "langley-township-parcel-attributes.receipt.json")
    batch, count = [], 0
    for objectid, property_number, key, raw in db.execute("SELECT objectid,property_number,pid,raw FROM raw_langley_assessments ORDER BY objectid"):
        feature = json.loads(raw)
        props = feature["properties"]
        candidates = list(db.execute("SELECT raw FROM raw_langley_attributes WHERE property_number=? ORDER BY objectid", (property_number,)))
        method = "official Property_Number"
        if not candidates and key:
            candidates = list(db.execute("SELECT raw FROM raw_langley_attributes WHERE pid=? ORDER BY objectid", (key,)))
            method = "unique official PID"
            if len(candidates) != 1:
                candidates = []
        attributes = [json.loads(value[0]) for value in candidates]
        geometries = [shape(a["geometry"]) for a in attributes if a.get("geometry")]
        geometries = [g if g.is_valid else shapely.make_valid(g) for g in geometries]
        polygon = shapely.union_all(geometries) if geometries else None
        if polygon is not None and not polygon.is_valid:
            polygon = shapely.make_valid(polygon)
        location = polygon.representative_point() if polygon is not None else shape(feature["geometry"])
        municipality = municipalities.one(location)
        attribute_props = attributes[0]["properties"] if attributes else {}
        land, improvement = number(props.get("Total_Gross_Land_Assessment")), number(props.get("Total_Gross_Building_Assessment"))
        value = land + improvement if land is not None and improvement is not None else None
        year = number(props.get("AssessmentYear"))
        features = {
            "source": "langley-township-assessments-2026", "source_url": assessment_receipt["url"],
            "assessment_record_id": objectid, "property_number": property_number,
            "assessment_folio": props.get("Folio"), "address_match": "official assessment address",
            "parcel_match": method if attributes else "unmatched",
            "parcel_attribute_record_ids": [a["properties"]["OBJECTID"] for a in attributes],
            "parcel_source_url": attributes_receipt["url"] if attributes else None,
            "coordinate_match": "parcel representative point" if polygon is not None else "official assessment point",
            "municipality_match": "official boundary spatial membership" if municipality else "outside boundary; retained official assessment",
            "parcel_area_sqm": number(attribute_props.get("Shape__Area")) if len(attributes) == 1 else None,
            "parcel_area_note": "Source parcel geometry area, not dwelling floor area",
            "assessed_not_asking": True, "postal_code": props.get("Postal_Code"),
            "alr": clean(attribute_props.get("ALR")), "heritage": clean(attribute_props.get("Heritage")),
            "development_permit_area": clean(attribute_props.get("Development_Permit_Area")),
            "secondary_suite_details": clean(attribute_props.get("Secondary_Suite_Details")),
        }
        identity = f"langley-assessment:{property_number or 'unknown'}:{objectid}"
        row = base_property(identity, key, municipality, location, mapping(polygon) if polygon is not None else None, features)
        base = " ".join(filter(None, [clean(props.get("House")), clean(props.get("Street"))]))
        unit = clean(props.get("Unit"))
        row.update(address=f"{unit} - {base}" if unit and base else base or row["address"],
                   street=clean(props.get("Street")), neighbourhood=clean(props.get("Neighbourhood")) or clean(props.get("Community")),
                   assessed=value, land=land, improvement=improvement, year=int(year) if year is not None else None,
                   zone=clean(props.get("Zoning")) or clean(attribute_props.get("Zoning_Code")), type=clean(props.get("Legal_Type")),
                   history=js([{"year": int(year) if year is not None else None, "land": land,
                                "improvement": improvement, "value": value, "tax": None}]))
        batch.append(row)
        count += 1
        if len(batch) == 2000:
            store_properties(db, batch)
            batch = []
    store_properties(db, batch)
    if count != 53100:
        raise RuntimeError(f"Lost Langley assessment rows: {count}")
    print("LANGLEY_PREPARED", count, flush=True)


class SiteMatcher:
    def __init__(self, db):
        self.sites = list(db.execute("SELECT loc_guid,lon,lat,base_address,street,base_count,address_count FROM nar_sites"))
        points = shapely.points([(row[1], row[2]) for row in self.sites])
        self.tree = STRtree(points)

    def match(self, polygon):
        indexes = self.tree.query(polygon, predicate="covers")
        if len(indexes) == 1:
            site = self.sites[indexes[0]]
            if site[5] == 1 and site[3]:
                return {"address": site[3], "street": site[4], "loc_guid": site[0],
                        "site_count": 1, "address_count": site[6],
                        "address_match": "approximate spatial site match; base civic address only; no unit-to-PID match"}
        return {"site_count": len(indexes), "address_match": "ambiguous multiple NAR sites" if len(indexes) > 1 else "no unambiguous NAR site"}


def prepare_pmbc(db, municipalities):
    matcher = SiteMatcher(db)
    existing = {row[0] for row in db.execute("SELECT DISTINCT pid FROM existing_pids")}
    batch = []
    stats = collections.Counter()
    geometry_cache = {}
    cursor = db.execute("SELECT entity_key,MIN(fabric_id),COUNT(*) FROM raw_pmbc GROUP BY entity_key ORDER BY MIN(fabric_id)")
    for entity_key, first_id, feature_count in cursor:
        key = entity_key[4:] if entity_key.startswith("pid:") else None
        if key in existing:
            stats["covered_by_existing_assessment"] += 1
            continue
        records = [json.loads(row[0]) for row in db.execute("SELECT raw FROM raw_pmbc WHERE entity_key=? ORDER BY fabric_id", (entity_key,))]
        first = records[0]
        props = first["properties"]
        geometry_parts = {js(r["geometry"]): r["geometry"] for r in records if r.get("geometry")}
        if not geometry_parts:
            stats["missing_geometry"] += 1
            continue
        cache_key = hashlib.sha256("|".join(sorted(geometry_parts)).encode()).digest()
        if cache_key in geometry_cache:
            polygon, point, municipality, match = geometry_cache[cache_key]
        else:
            parts = [shape(g) for g in geometry_parts.values()]
            parts = [g if g.is_valid else shapely.make_valid(g) for g in parts]
            polygon = parts[0] if len(parts) == 1 else shapely.union_all(parts)
            if not polygon.is_valid:
                polygon = shapely.make_valid(polygon)
            if polygon.is_empty:
                stats["empty_geometry"] += 1
                continue
            point = polygon.representative_point()
            municipality = municipalities.one(point)
            match = matcher.match(polygon) if municipality else {}
            if len(geometry_cache) >= 15000:
                geometry_cache.clear()
            geometry_cache[cache_key] = polygon, point, municipality, match
        if municipality is None:
            stats["outside_municipal_boundaries"] += 1
            continue
        features = {
            "source": "pmbc-metro", "source_url": PMBC_URL,
            "source_geometry_ids": [r["properties"]["PARCEL_FABRIC_POLY_ID"] for r in records],
            "source_geometry_count": feature_count, "unique_geometry_parts": len(geometry_parts),
            "parcel_area_sqm": number(props.get("FEATURE_AREA_SQM")) if len(geometry_parts) == 1 else None,
            "parcel_area_note": "Parcel/site area, not dwelling floor area; shared strata geometry may repeat across PIDs",
            "parcel_class": props.get("PARCEL_CLASS"), "parcel_status": props.get("PARCEL_STATUS"),
            "owner_type": props.get("OWNER_TYPE"), "source_updated": props.get("WHEN_UPDATED"),
            "source_municipality": props.get("MUNICIPALITY"),
            "municipality_match": "official boundary spatial membership",
            "coordinate_match": "parcel representative point", "address_match": match.get("address_match"),
            "nar_site_count": match.get("site_count", 0), "nar_location_id": match.get("loc_guid"),
            "nar_address_count": match.get("address_count"), "address_source_url": NAR_URL,
            "assessment_status": "not available in this public parcel source",
        }
        row = base_property("pmbc:" + (key or "fabric-" + str(first_id)), key, municipality,
                            point, mapping(polygon), features)
        row["type"] = clean(props.get("PARCEL_CLASS"))
        if match.get("address"):
            row["address"], row["street"] = match["address"], match["street"]
            stats["approximate_site_address"] += 1
        batch.append(row)
        stats["prepared"] += 1
        if len(batch) == 2000:
            store_properties(db, batch)
            batch = []
            if stats["prepared"] % 20000 == 0:
                print("PMBC_PREPARED", stats["prepared"], flush=True)
    store_properties(db, batch)
    return dict(stats)


def output_sources(db, pmbc_manifest):
    sources = []
    receipt_paths = list((RAW / "nar-bc").glob("*.csv.receipt.json")) + [
        RAW / "metro-municipalities.receipt.json", RAW / "langley-township-assessments-2026.receipt.json",
        RAW / "langley-township-parcel-attributes.receipt.json"]
    for path in receipt_paths:
        receipt = read_json(path)
        identity = receipt["id"]
        if path.parent.name == "nar-bc":
            identity = "nar-" + identity
        sources.append({"id": identity, "title": identity.replace("-", " ").replace("_", " ").title(),
            "url": receipt["url"], "downloaded": receipt["downloaded_at"],
            "records": receipt.get("records", receipt.get("expected_records")), "bytes": receipt["bytes"],
            "sha256": receipt["sha256"], "status": "verified",
            "detail": f"Complete raw source archived in regional.sqlite and original files. {receipt.get('licence', '')}."})
    receipts = [json.loads(row[0]) for row in db.execute("SELECT receipt FROM source_receipts WHERE source LIKE 'pmbc-%' ORDER BY source")]
    digest = hashlib.sha256("\n".join(r["sha256"] for r in receipts).encode()).hexdigest()
    sources.append({"id": "pmbc-metro", "title": "ParcelMap BC - Metro Vancouver parcel fabric",
        "url": PMBC_URL, "downloaded": pmbc_manifest.get("completed_at", pmbc_manifest.get("updated_at")),
        "records": pmbc_manifest["expected_records"], "bytes": sum(r["bytes"] for r in receipts),
        "sha256": digest, "status": "verified",
        "detail": "All regional source geometries and attributes retained in regional.sqlite. Open Government Licence - British Columbia. SHA256 covers ordered newline-separated page SHA256 digests. No assessment values."})
    path = OUT / "sources-regional.jsonl"
    with path.with_suffix(".jsonl.part").open("w", encoding="utf-8") as stream:
        for row in sources:
            stream.write(js(row) + "\n")
    path.with_suffix(".jsonl.part").replace(path)


def output_addresses(db):
    """Export source civic/unit locations separately from parcel identity."""
    path = OUT / "addresses-regional.jsonl"
    counts = collections.Counter()
    expected, unique = db.execute("""
        SELECT COUNT(*), COUNT(DISTINCT a.addr_guid)
        FROM raw_nar_addresses a JOIN nar_sites s ON s.loc_guid=a.loc_guid
    """).fetchone()
    if expected != unique:
        raise RuntimeError("NAR address GUIDs are not unique; resolve source duplicates before export")
    query = """
        SELECT a.addr_guid,a.base_address,a.unit,s.municipality,s.lat,s.lon
        FROM raw_nar_addresses a JOIN nar_sites s ON s.loc_guid=a.loc_guid
        ORDER BY a.source,a.line
    """
    with path.with_suffix(".jsonl.part").open("w", encoding="utf-8") as stream:
        for guid, base, unit, municipality, lat, lon in db.execute(query):
            if not base or municipality not in CANONICAL or lat is None or lon is None:
                raise RuntimeError(f"Incomplete NAR source address location {guid}")
            address = f"{unit} - {base}" if unit else base
            row = {"id": "nar-address:" + guid, "address": address,
                   "municipality": municipality, "lat": lat, "lon": lon, "source": NAR_URL}
            stream.write(js(row) + "\n")
            counts[municipality] += 1
            if sum(counts.values()) % 200000 == 0:
                print("NAR_ADDRESSES_EXPORTED", sum(counts.values()), flush=True)
    if sum(counts.values()) != expected or len(counts) != 23:
        raise RuntimeError("Regional NAR address export count or coverage mismatch")
    path.with_suffix(".jsonl.part").replace(path)
    print("REGIONAL_ADDRESSES_COMPLETE", js({"records": expected, "municipality_counts": dict(counts),
          "bytes": path.stat().st_size, "sha256": sha_file(path)}), flush=True)


def output_assets(db, municipalities, pmbc_manifest, vancouver_count, pmbc_stats):
    counts = dict(db.execute("SELECT municipality,COUNT(*) FROM properties WHERE municipality IS NOT NULL GROUP BY municipality"))
    counts["Vancouver"] = counts.get("Vancouver", 0) + vancouver_count
    if any(counts.get(name, 0) == 0 for name in CANONICAL):
        raise RuntimeError("At least one regional jurisdiction has no mapped records")
    stats = {
        "properties": db.execute("SELECT COUNT(*) FROM properties").fetchone()[0],
        "existing_vancouver_properties": vancouver_count,
        "nar_locations_bc": db.execute("SELECT COUNT(*) FROM raw_nar_locations").fetchone()[0],
        "nar_addresses_bc": db.execute("SELECT COUNT(*) FROM raw_nar_addresses").fetchone()[0],
        "nar_sites_region": db.execute("SELECT COUNT(*) FROM nar_sites").fetchone()[0],
        "pmbc_raw_records": db.execute("SELECT COUNT(*) FROM raw_pmbc").fetchone()[0],
        "pmbc_unique_entities": db.execute("SELECT COUNT(DISTINCT entity_key) FROM raw_pmbc").fetchone()[0],
        "langley_assessment_records": db.execute("SELECT COUNT(*) FROM raw_langley_assessments").fetchone()[0],
        "langley_attribute_records": db.execute("SELECT COUNT(*) FROM raw_langley_attributes").fetchone()[0],
        "municipalities": 23, "boundary_parts": len(municipalities.geojson["features"]),
        "municipality_counts": counts, "pmbc_processing": pmbc_stats,
        "updated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    coverage = [{"municipality": name, "status": "imported", "assessments": name in ("Vancouver", "Langley Township"),
        "records": counts[name], "count": counts[name],
        "detail": "Official assessment values plus parcel coverage." if name in ("Vancouver", "Langley Township")
        else "Official parcel coverage; values unavailable. Civic labels are approximate spatial site matches where unambiguous."} for name in CANONICAL]
    properties_path = OUT / "properties-regional.jsonl"
    with properties_path.with_suffix(".jsonl.part").open("w", encoding="utf-8") as stream:
        for (record,) in db.execute("SELECT record FROM properties ORDER BY id"):
            stream.write(record + "\n")
    properties_path.with_suffix(".jsonl.part").replace(properties_path)
    layers_path = OUT / "layers-regional.jsonl"
    with layers_path.with_suffix(".jsonl.part").open("w", encoding="utf-8") as stream:
        for key, value in [("municipalities", municipalities.geojson), ("coverage", coverage), ("regional-stats", stats)]:
            stream.write(js({"id": key, "data": js(value)}) + "\n")
    layers_path.with_suffix(".jsonl.part").replace(layers_path)
    output_sources(db, pmbc_manifest)
    print("REGIONAL_COMPLETE", js(stats), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--addresses-only", action="store_true")
    parser.add_argument("--export-addresses-only", action="store_true")
    args = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    db = connect()
    if args.export_addresses_only:
        output_addresses(db)
        db.close()
        return
    municipalities = Municipalities(db)
    import_locations(db, municipalities)
    import_addresses(db)
    create_sites(db)
    import_langley(db)
    vancouver_count = import_vancouver_pids(db)
    if args.addresses_only:
        print("ADDRESS_PREPARATION_COMPLETE", flush=True)
        db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        db.close()
        return
    manifest = import_pmbc(db)
    # Rebuild only this script's normalized table; all source rows remain intact.
    db.execute("DELETE FROM properties")
    db.commit()
    prepare_langley(db, municipalities)
    stats = prepare_pmbc(db, municipalities)
    output_assets(db, municipalities, manifest, vancouver_count, stats)
    output_addresses(db)
    db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    db.close()


if __name__ == "__main__":
    main()
