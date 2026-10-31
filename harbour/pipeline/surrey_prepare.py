"""Prepare Surrey municipal assessment/context assets for the existing importer.

Only exact municipal identifiers and exact civic address joins are used. All
source rows remain in the raw files and the local integration SQLite archive.
Display geometries may be simplified; source geometries are never overwritten.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3

import numpy as np
import shapely
from shapely.geometry import Point, mapping, shape
from shapely.strtree import STRtree

from prepare_regional import FIELDS, iter_features

ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / "data/raw/surrey-extra"
OUT = ROOT / "data/prepared"
PROPERTY_URL = "https://services5.arcgis.com/YRpe0VKTJytZSSIB/arcgis/rest/services/property%20detail%20list/FeatureServer/0"
YEAR_URL = "https://www.arcgis.com/sharing/rest/content/items/14d610192b7b4cfb98fee31a608d8a1e/info/metadata/metadata.xml"
UPDATED = datetime.now(timezone.utc).isoformat()
METRES_PER_DEGREE = math.pi * 6371008.8 / 180
COS_LATITUDE = math.cos(math.radians(49.13))
LAYER_LIMIT = 1450000
ENTITY_KEY_VERSION = "pid-folio-valuation-v2"


def js(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def clean(value):
    if value is None or str(value).strip().upper() in ("", "NULL", "<NULL>", "NONE", "N/A"):
        return None
    return str(value).strip()


def key_pid(value):
    digits = re.sub(r"\D", "", str(value or ""))
    return digits if len(digits) == 9 and digits != "000000000" else None


def display_pid(value):
    return f"{value[:3]}-{value[3:6]}-{value[6:]}" if value else None


def norm(value):
    return re.sub(r"\s+", " ", str(value or "")).strip().upper()


def civic(props):
    house = props.get("HOUSE")
    if isinstance(house, (float, int)) and float(house).is_integer():
        house = str(int(house))
    base = " ".join(v for v in (clean(house), clean(props.get("STREET"))) if v)
    unit = clean(props.get("UNIT"))
    return (f"{unit} - {base}" if unit and base else base), base


def official_key(props):
    pid, folio = key_pid(props.get("PID")), clean(props.get("FOLIO"))
    address, _ = civic(props)
    identity = ["pid-folio", pid, norm(folio)] if pid and folio else [
        "incomplete-identity", pid, str(props.get("PROPERTY_NUMBER") or ""), norm(address), norm(folio)]
    values = identity + [props.get("GROSS_LAND"), props.get("GROSS_IMPROVEMENTS"), props.get("GROSS_ASSESSMENT")]
    return hashlib.sha256(js(values).encode()).hexdigest()


def point_from_feature(feature):
    geometry = feature.get("geometry")
    if geometry and geometry.get("type") == "Point":
        lon, lat = geometry["coordinates"][:2]
        if lon is not None and lat is not None and -124 < lon < -121 and 48 < lat < 51:
            return Point(lon, lat), "official municipal property point"
    props = feature["properties"]
    lon, lat = props.get("LONGITUDE"), props.get("LATITUDE")
    if isinstance(lon, (int, float)) and isinstance(lat, (int, float)) and -124 < lon < -121 and 48 < lat < 51:
        return Point(lon, lat), "official municipal longitude/latitude fields"
    return None, "not located"


def fix(geometry):
    if geometry is None:
        return None
    geom = shape(geometry)
    return geom if geom.is_valid else shapely.make_valid(geom)


def connect():
    db = sqlite3.connect(RAW / "integration.sqlite")
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA synchronous=NORMAL")
    db.execute("PRAGMA cache_size=-100000")
    db.executescript("""
        CREATE TABLE IF NOT EXISTS imported(source TEXT PRIMARY KEY,sha256 TEXT,records INTEGER);
        CREATE TABLE IF NOT EXISTS source_records(source TEXT,objectid INTEGER,pid TEXT,mslink INTEGER,entity_key TEXT,raw TEXT,PRIMARY KEY(source,objectid));
        CREATE INDEX IF NOT EXISTS source_entity ON source_records(source,entity_key);
        CREATE INDEX IF NOT EXISTS source_mslink ON source_records(source,mslink);
        CREATE INDEX IF NOT EXISTS source_mslink_ordered ON source_records(source,mslink,objectid);
        CREATE INDEX IF NOT EXISTS source_entity_ordered ON source_records(source,entity_key,objectid);
        CREATE TABLE IF NOT EXISTS base_properties(id TEXT PRIMARY KEY,pid TEXT,record TEXT);
        CREATE INDEX IF NOT EXISTS base_pid ON base_properties(pid);
        CREATE TABLE IF NOT EXISTS output_properties(id TEXT PRIMARY KEY,record TEXT,origin TEXT);
    """)
    return db


def archive_sources(db):
    receipts = {}
    for receipt_path in sorted(RAW.glob("*/receipt.json")):
        source = receipt_path.parent.name
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        receipts[source] = receipt
        fingerprint = receipt["sha256"] + (":" + ENTITY_KEY_VERSION if source == "property-detail" else "")
        prior = db.execute("SELECT sha256,records FROM imported WHERE source=?", (source,)).fetchone()
        if prior == (fingerprint, receipt["records"]):
            continue
        db.execute("DELETE FROM source_records WHERE source=?", (source,))
        path = ROOT / receipt["path"]
        field = receipt["object_id_field"]
        batch, count = [], 0
        for feature in iter_features(path):
            props = feature["properties"]
            entity = official_key(props) if source == "property-detail" else None
            batch.append((source, props[field], key_pid(props.get("PID")), props.get("MSLINK"), entity, js(feature)))
            count += 1
            if len(batch) == 2000:
                db.executemany("INSERT INTO source_records VALUES(?,?,?,?,?,?)", batch)
                db.commit()
                batch = []
        db.executemany("INSERT INTO source_records VALUES(?,?,?,?,?,?)", batch)
        if count != receipt["records"]:
            raise RuntimeError(f"Incomplete source archive {source}: {count}")
        db.execute("INSERT OR REPLACE INTO imported VALUES(?,?,?)", (source, fingerprint, count))
        db.commit()
        print("ARCHIVED", source, count, flush=True)
    for required in ["property-detail", "lots", "zoning", "communities", "parks", "issued-building-permits"]:
        if required not in receipts:
            raise RuntimeError(f"Required complete Surrey source missing: {required}")
    return receipts


def archive_base(db):
    original = sqlite3.connect(f"file:{(ROOT / 'data/regional.sqlite').as_posix()}?mode=ro", uri=True)
    expected = original.execute("SELECT COUNT(*) FROM properties WHERE municipality='Surrey'").fetchone()[0]
    db.execute("DELETE FROM base_properties")
    batch, count = [], 0
    for identity, pid, record in original.execute("SELECT id,pid,record FROM properties WHERE municipality='Surrey'"):
        batch.append((identity, key_pid(pid), record))
        count += 1
        if len(batch) == 2000:
            db.executemany("INSERT INTO base_properties VALUES(?,?,?)", batch)
            db.commit()
            batch = []
    db.executemany("INSERT INTO base_properties VALUES(?,?,?)", batch)
    db.commit()
    original.close()
    if count != expected:
        raise RuntimeError("Base Surrey parcel copy incomplete")
    db.executescript("""
        DROP TABLE IF EXISTS official_entities;
        CREATE TABLE official_entities AS SELECT entity_key,pid,MIN(objectid) objectid,COUNT(*) source_rows
          FROM source_records WHERE source='property-detail' GROUP BY entity_key;
        CREATE UNIQUE INDEX entity_key_unique ON official_entities(entity_key);
        CREATE INDEX entity_pid ON official_entities(pid);
        DROP TABLE IF EXISTS entity_pid_counts;
        CREATE TABLE entity_pid_counts AS SELECT pid,COUNT(*) n FROM official_entities WHERE pid IS NOT NULL GROUP BY pid;
        CREATE UNIQUE INDEX entity_pid_count_key ON entity_pid_counts(pid);
        DROP TABLE IF EXISTS lot_use_counts;
        CREATE TABLE lot_use_counts AS SELECT mslink,COUNT(DISTINCT entity_key) n FROM source_records
          WHERE source='property-detail' AND mslink IS NOT NULL GROUP BY mslink;
        CREATE UNIQUE INDEX lot_use_key ON lot_use_counts(mslink);
    """)
    db.commit()
    print("BASE_READY", count, "OFFICIAL_ENTITIES", db.execute("SELECT COUNT(*) FROM official_entities").fetchone()[0], flush=True)
    return count


def metric(geometries):
    def coordinates(values):
        output = values.copy()
        output[:, 0] *= METRES_PER_DEGREE * COS_LATITUDE
        output[:, 1] *= METRES_PER_DEGREE
        return output
    return shapely.transform(geometries, coordinates)


class Context:
    def __init__(self, db, receipts):
        self.receipts = receipts
        self.layers = {}
        for source in ["zoning", "communities", "parks", "schools", "elementary-catchments", "secondary-catchments", "official-community-plan", "development-applications", "floodplain-200-year", "hazard-lands"]:
            if source not in receipts:
                continue
            features, geometries = [], []
            for (raw,) in db.execute("SELECT raw FROM source_records WHERE source=? ORDER BY objectid", (source,)):
                feature = json.loads(raw)
                geom = fix(feature.get("geometry"))
                if geom is not None and not geom.is_empty:
                    features.append(feature)
                    geometries.append(geom)
            self.layers[source] = {"features": features, "geometries": geometries, "areas": [g.area for g in geometries], "tree": STRtree(geometries)}
            shapely.prepare(geometries)
            if source in ("parks", "schools"):
                self.layers[source]["metric_geometries"] = metric(np.array(geometries, dtype=object))
                self.layers[source]["metric_tree"] = STRtree(self.layers[source]["metric_geometries"])
            print("CONTEXT_READY", source, len(features), flush=True)

    def matches(self, source, point):
        layer = self.layers.get(source)
        if not layer or point is None:
            return []
        indexes = [i for i in layer["tree"].query(point) if layer["geometries"][i].covers(point)]
        indexes = sorted(indexes, key=lambda i: layer["areas"][i])
        return [layer["features"][i] for i in indexes]

    def enrich(self, row):
        features = json.loads(row["features"])
        facts = features.setdefault("municipal_facts", [])
        sources = {s["url"]: s for s in features.get("municipality_context_sources", [])}

        def source_url(source):
            receipt = self.receipts[source]
            sources[receipt["url"]] = {"title": receipt["title"], "url": receipt["url"]}
            return receipt["url"]

        point = Point(row["lon"], row["lat"]) if row["lat"] is not None and row["lon"] is not None else None
        if point is not None:
            for source, field, label in [("communities", "NAME", "Community"), ("zoning", "ZONING_TEXT", "Mapped zoning"),
                    ("official-community-plan", "LAND_USE", "Official community plan"),
                    ("elementary-catchments", "SCHOOL_NAME", "Mapped elementary catchment"),
                    ("secondary-catchments", "SCHOOL_NAME", "Mapped secondary catchment")]:
                matches = self.matches(source, point)
                if matches:
                    props = matches[0]["properties"]
                    value = clean(props.get(field))
                    url = source_url(source)
                    if source == "communities" and value:
                        value = value.title()
                        row["neighbourhood"] = value
                        features["neighbourhood_match"] = "municipal boundary covers reference point"
                    if source == "zoning":
                        if not row["zone"]:
                            row["zone"] = value
                        features["mapped_zone_description"] = props.get("ZONING")
                        url = clean(props.get("WEBLINK")) or url
                    if value:
                        facts.append({"label": label, "value": value, "url": url})
            projected = metric(point)
            for source, field, target in [("parks", "PARK_NAME", "park"), ("schools", "NAME", "school")]:
                layer = self.layers.get(source)
                if not layer or not layer["features"]:
                    continue
                index = int(layer["metric_tree"].nearest(projected))
                props = layer["features"][index]["properties"]
                name = clean(props.get(field)) or clean(props.get("DESCRIPTION")) or ("Mapped park" if target == "park" else "Mapped school")
                features[target] = {"name": name, "metres": round(projected.distance(layer["metric_geometries"][index])),
                    "url": source_url(source), "distance_method": "approximate straight-line distance to mapped boundary",
                    "status": props.get("STATUS")}
            projects = self.matches("development-applications", point)
            if projects:
                features["mapped_development_application_count"] = len(projects)
                facts.append({"label": "Development applications", "value": f"{len(projects)} mapped application records; these are not issued building permits", "url": source_url("development-applications")})
                features["development_projects"] = [{"number": f["properties"].get("PROJECT_NO"), "status": f["properties"].get("STATUS"),
                    "url": f["properties"].get("WEBLINK")} for f in projects[:5]]
            for source, label in [("floodplain-200-year", "Mapped 200-year floodplain"), ("hazard-lands", "Hazard lands permit area")]:
                matches = self.matches(source, point)
                if matches:
                    facts.append({"label": label, "value": "Reference point falls inside the published area; property-specific review required", "url": source_url(source)})
        features["municipality_context_sources"] = list(sources.values())
        features["municipal_context_match"] = "spatial reference-point screening; no building characteristics or development permissions inferred"
        row["features"] = js(features)
        return row


class LotMatcher:
    def __init__(self, db):
        self.db = db
        self.cache = {}

    def one(self, mslink):
        if mslink not in self.cache:
            rows = [json.loads(raw) for (raw,) in self.db.execute("SELECT raw FROM source_records WHERE source='lots' AND mslink=? ORDER BY objectid", (mslink,))]
            parts = {js(f["geometry"]): fix(f["geometry"]) for f in rows if f.get("geometry")}
            polygon = shapely.union_all(list(parts.values())) if parts else None
            if polygon is not None and not polygon.is_valid:
                polygon = shapely.make_valid(polygon)
            use = self.db.execute("SELECT n FROM lot_use_counts WHERE mslink=?", (mslink,)).fetchone()
            result = polygon, [f["properties"]["OBJECTID"] for f in rows], use[0] if use else 0
            if len(self.cache) >= 18000:
                self.cache.clear()
            self.cache[mslink] = result
        return self.cache[mslink]

    def match(self, records):
        mslinks = sorted({f["properties"].get("MSLINK") for f in records if f["properties"].get("MSLINK") is not None})
        parts, ids, shared = {}, [], False
        for mslink in mslinks:
            polygon, source_ids, uses = self.one(mslink)
            ids.extend(source_ids)
            shared = shared or uses > 1
            if polygon is not None:
                parts[polygon.wkb] = polygon
        polygon = shapely.union_all(list(parts.values())) if parts else None
        return polygon, ids, shared, mslinks


def parcel_text(polygon):
    if polygon is None or polygon.is_empty:
        return "null", False
    text = js(mapping(polygon))
    if len(text.encode()) < LAYER_LIMIT:
        return text, False
    for tolerance in (0.000001, 0.000005, 0.00001, 0.00005, 0.0001, 0.0005):
        text = js(mapping(polygon.simplify(tolerance, preserve_topology=True)))
        if len(text.encode()) < LAYER_LIMIT:
            return text, True
    raise RuntimeError("A display parcel still exceeds the importer string limit")


def store_rows(db, rows, origin):
    db.executemany("INSERT INTO output_properties VALUES(?,?,?)", [(r["id"], js(r), origin) for r in rows])
    db.commit()


def prepare_properties(db, receipts, context):
    db.execute("DELETE FROM output_properties")
    db.commit()
    matcher = LotMatcher(db)
    stats, batch = Counter(), []
    query = """SELECT e.entity_key,e.pid,e.objectid,e.source_rows,p.n
        FROM official_entities e LEFT JOIN entity_pid_counts p ON p.pid=e.pid ORDER BY e.objectid"""
    for entity, pid, first_id, source_count, pid_count in db.execute(query):
        records = [json.loads(raw) for (raw,) in db.execute("SELECT raw FROM source_records WHERE source='property-detail' AND entity_key=? ORDER BY objectid", (entity,))]
        first, props = records[0], records[0]["properties"]
        bases = list(db.execute("SELECT id,record FROM base_properties WHERE pid=?", (pid,))) if pid else []
        if pid_count == 1 and len(bases) == 1:
            row = json.loads(bases[0][1])
            stats["existing_ids_preserved"] += 1
        else:
            row = {field: None for field in FIELDS}
            row.update(id=f"sry:{first_id}", features="{}", parcel="null")
            stats["new_official_entities"] += 1
        polygon, lot_ids, shared, mslinks = matcher.match(records)
        point, method = point_from_feature(first)
        if point is None:
            for candidate in records[1:]:
                point, method = point_from_feature(candidate)
                if point is not None:
                    break
        if point is None and polygon is not None and not polygon.is_empty:
            point, method = polygon.representative_point(), "representative point of official MSLINK parcel join"
        if point is None and row.get("lat") is not None and row.get("lon") is not None:
            point, method = Point(row["lon"], row["lat"]), "existing exact-PID parcel reference point"
        if polygon is not None:
            row["parcel"], simplified = parcel_text(polygon)
            stats["official_entities_with_mslink_geometry"] += 1
        else:
            simplified = False
            stats["official_entities_without_mslink_geometry"] += 1
        address, _ = civic(props)
        alternate_addresses = sorted({civic(f["properties"])[0] for f in records if civic(f["properties"])[0]})
        base_addresses = sorted({civic(f["properties"])[1] for f in records if civic(f["properties"])[1]})
        multiple_addresses = len(alternate_addresses) > 1
        if multiple_addresses:
            address = base_addresses[0] if len(base_addresses) == 1 else "Parcel " + (display_pid(pid) or str(first_id))
        lot_type = clean(props.get("LOT_TYPE"))
        legal_type = "STRATA" if lot_type and "strata" in lot_type.lower() else "LAND" if lot_type == "Standard Lot" else lot_type
        features = json.loads(row["features"])
        features.update(source="surrey-property-detail", source_url=PROPERTY_URL,
            assessment_label="2026 City of Surrey assessed value", assessed_not_asking=True,
            assessment_year_source_url=YEAR_URL, source_record_ids=[f["properties"]["OBJECTID"] for f in records],
            source_record_count=source_count, official_entity_key=entity,
            source_property_numbers=sorted({str(f["properties"]["PROPERTY_NUMBER"]) for f in records if f["properties"].get("PROPERTY_NUMBER")}),
            folio=props.get("FOLIO"), alternate_addresses=alternate_addresses,
            assessment_scope="One PID/folio assessment covering multiple source addresses; not a separate value for each unit" if multiple_addresses else "One municipal PID/folio assessed entity",
            assessment_shared_across_addresses=multiple_addresses,
            address_match="official addresses grouped by identical PID, folio and valuation" if multiple_addresses else "official municipal assessment address and unit",
            coordinate_match=method, parcel_match="official MSLINK to municipal lot polygons" if lot_ids else "existing exact-PID geometry" if row["parcel"] != "null" else "unmatched",
            source_mslinks=mslinks, parcel_source_record_ids=lot_ids, parcel_source_url=receipts["lots"]["url"] if lot_ids else features.get("source_url"),
            parcel_geometry_shared=bool(shared or legal_type == "STRATA"), parcel_display_simplified=simplified,
            parcel_geometry_note="Municipal lot/site geometry may be shared by multiple strata units; not an individual unit boundary",
            official_lot_type=lot_type, postal_code=props.get("POSTAL_CODE"), zone_description=props.get("ZONE_DESCRIPTION"),
            plan_year=props.get("PLAN_YEAR"), lot_size_source_text=props.get("LOT_SIZE"),
            municipality_context_sources=[{"title": receipts["property-detail"]["title"], "url": PROPERTY_URL}], municipal_facts=[])
        if multiple_addresses:
            features["municipal_facts"].append({"label": "Assessment scope", "value": f"One PID/folio assessed value covers {len(alternate_addresses)} source addresses. This amount is not an individual value for each address or unit.", "url": PROPERTY_URL})
        if props.get("LOT_SIZE"):
            features["municipal_facts"].append({"label": "Approximate parcel size", "value": props["LOT_SIZE"], "url": PROPERTY_URL})
            area = re.match(r"([\d,]+(?:\.\d+)?)\s+sq\.\s+meters", props["LOT_SIZE"])
            if area:
                features["parcel_area_sqm"] = float(area[1].replace(",", ""))
                features["parcel_area_note"] = "Approximate source parcel/site area, not dwelling floor area"
        land, improvement, total = props["GROSS_LAND"], props["GROSS_IMPROVEMENTS"], props["GROSS_ASSESSMENT"]
        if total is not None and land is not None and improvement is not None and abs(total - land - improvement) > .01:
            raise RuntimeError(f"Assessment arithmetic mismatch {first_id}")
        row.update(pid=display_pid(pid), address=address or "Surrey property " + str(first_id), street=clean(props.get("STREET")),
            municipality="Surrey", lat=point.y if point else None, lon=point.x if point else None,
            assessed=total, land=land, improvement=improvement, year=2026, built=None, tax=None, change=None,
            zone=clean(props.get("ZONE")), type=legal_type, features=js(features),
            history=js([{"year": 2026, "land": land, "improvement": improvement, "value": total, "tax": None}]))
        batch.append(context.enrich(row))
        stats["official_entities"] += 1
        stats["official_mapped" if point is not None else "official_unmapped"] += 1
        stats["shared_lot_geometry"] += bool(shared or legal_type == "STRATA")
        if len(batch) == 1000:
            store_rows(db, batch, "official")
            batch = []
            if stats["official_entities"] % 10000 == 0:
                print("SURREY_OFFICIAL_PREPARED", stats["official_entities"], flush=True)
    store_rows(db, batch, "official")
    batch = []
    for (raw,) in db.execute("SELECT b.record FROM base_properties b LEFT JOIN output_properties o ON o.id=b.id WHERE o.id IS NULL ORDER BY b.id"):
        row = json.loads(raw)
        features = json.loads(row["features"])
        features.update(municipal_facts=[], municipality_context_sources=[], assessment_label="Assessment unavailable for this parcel record")
        pid = key_pid(row.get("pid"))
        separate = db.execute("SELECT n FROM entity_pid_counts WHERE pid=?", (pid,)).fetchone() if pid else None
        if separate:
            features["separate_official_assessment_entities"] = separate[0]
            features["municipal_facts"].append({"label": "Official assessed entities", "value": f"{separate[0]} municipal PID/folio assessed entities share this PID; no assessment assigned to this base parcel", "url": PROPERTY_URL})
        row["features"] = js(features)
        batch.append(context.enrich(row))
        stats["base_parcels_without_unique_assessment"] += 1
        if len(batch) == 1000:
            store_rows(db, batch, "base")
            batch = []
    store_rows(db, batch, "base")
    return dict(stats)


def address_candidates(db):
    points = defaultdict(dict)
    for (raw,) in db.execute("SELECT raw FROM source_records WHERE source='property-detail'"):
        feature = json.loads(raw)
        point, _ = point_from_feature(feature)
        address, _ = civic(feature["properties"])
        if point is not None and address:
            points[norm(address)][(point.x, point.y)] = PROPERTY_URL
    # Dedicated municipal address points are preferred for whole-site permits.
    dedicated = defaultdict(dict)
    for (raw,) in db.execute("SELECT raw FROM source_records WHERE source='address-points'"):
        feature = json.loads(raw)
        p = feature["properties"]
        geom = feature.get("geometry")
        if geom and geom.get("type") == "Point" and p.get("HOUSE_NO") is not None and p.get("GCROADS"):
            address = str(p["HOUSE_NO"]) + " " + p["GCROADS"]
            x, y = geom["coordinates"][:2]
            dedicated[norm(address)][(x, y)] = "https://gisservices.surrey.ca/arcgis/rest/services/OpenData/MapServer/138"
    for address, candidates in dedicated.items():
        if len(candidates) == 1:
            points[address] = candidates
    return points


def resolve_financial_scope(db):
    """Do not interpret unexplained value variants as whole-property values."""
    groups = list(db.execute("""SELECT e.pid,TRIM(UPPER(json_extract(s.raw,'$.properties.FOLIO'))) folio,
        json_group_array(e.objectid),COUNT(*) n FROM official_entities e
        JOIN source_records s ON s.source='property-detail' AND s.objectid=e.objectid
        WHERE e.pid IS NOT NULL AND TRIM(json_extract(s.raw,'$.properties.FOLIO'))!=''
        GROUP BY e.pid,folio HAVING n>1"""))
    stats = {"ambiguous_pid_folio_groups": len(groups), "original_value_entities_affected": sum(g[3] for g in groups),
             "whole_property_values_suppressed": True}
    for pid, folio, objectids, number in groups:
        records = []
        for oid in json.loads(objectids):
            match = db.execute("SELECT record FROM output_properties WHERE id=?", (f"sry:{oid}",)).fetchone()
            if match:
                records.append(json.loads(match[0]))
        if not records or any(json.loads(r["features"]).get("financial_scope_unresolved") for r in records):
            continue
        if len(records) != number:
            raise RuntimeError("Incomplete financial-variant group")
        records.sort(key=lambda r: min(json.loads(r["features"])["source_record_ids"]))
        row = records[0].copy()
        features = json.loads(row["features"])
        variants, source_ids, aliases, entity_keys, property_numbers = [], set(), set(), set(), set()
        for record in records:
            f = json.loads(record["features"])
            variants.append({"land": record["land"], "improvement": record["improvement"], "total": record["assessed"],
                "source_record_ids": f["source_record_ids"], "source_property_numbers": f.get("source_property_numbers", []),
                "addresses": f.get("alternate_addresses", [record["address"]]), "entity_key": f["official_entity_key"]})
            source_ids.update(f["source_record_ids"])
            aliases.update(f.get("alternate_addresses", [record["address"]]))
            entity_keys.add(f["official_entity_key"])
            property_numbers.update(f.get("source_property_numbers", []))
        bases = list(db.execute("SELECT id FROM base_properties WHERE pid=?", (pid,)))
        pid_count = db.execute("SELECT n FROM entity_pid_counts WHERE pid=?", (pid,)).fetchone()[0]
        if pid_count == number and len(bases) == 1:
            row["id"] = bases[0][0]
        explanation = f"The municipality publishes {number} different financial records for this PID and folio without identifying their scope. All source amounts are retained, but no whole-property assessed value is inferred or summed."
        features.update(financial_scope_unresolved=True, financial_variants=variants,
            assessment_label="Assessment scope unresolved — multiple published values",
            assessment_scope=explanation, source_record_ids=sorted(source_ids), source_record_count=len(source_ids),
            alternate_addresses=sorted(aliases), official_entity_keys=sorted(entity_keys),
            source_property_numbers=sorted(property_numbers))
        features["municipal_facts"] = [f for f in features.get("municipal_facts", []) if f.get("label") != "Assessment scope"]
        features["municipal_facts"].append({"label": "Assessment scope", "value": explanation, "url": PROPERTY_URL})
        row.update(assessed=None, land=None, improvement=None, history="[]", features=js(features))
        db.executemany("DELETE FROM output_properties WHERE id=?", [(r["id"],) for r in records])
        db.execute("INSERT OR REPLACE INTO output_properties VALUES(?,?,?)", (row["id"], js(row), "official"))
    reductions = Counter()
    for pid, folio, objectids, number in groups:
        reductions[pid] += number - 1
    for pid, reduction in reductions.items():
        remaining = db.execute("SELECT n FROM entity_pid_counts WHERE pid=?", (pid,)).fetchone()[0] - reduction
        for (identity,) in db.execute("SELECT id FROM base_properties WHERE pid=?", (pid,)):
            found = db.execute("SELECT record FROM output_properties WHERE id=? AND origin='base'", (identity,)).fetchone()
            if not found:
                continue
            row = json.loads(found[0])
            features = json.loads(row["features"])
            features["separate_official_assessment_entities"] = remaining
            for fact in features.get("municipal_facts", []):
                if fact.get("label") == "Official assessed entities":
                    fact["value"] = f"{remaining} municipal PID/folio entities share this PID, including unresolved financial records; no assessment assigned to this base parcel"
            row["features"] = js(features)
            db.execute("UPDATE output_properties SET record=? WHERE id=?", (js(row), identity))
    db.execute("CREATE TABLE IF NOT EXISTS financial_scope_summary(id INTEGER PRIMARY KEY,data TEXT)")
    db.execute("INSERT OR REPLACE INTO financial_scope_summary VALUES(1,?)", (js(stats),))
    db.commit()
    print("FINANCIAL_SCOPE_RESOLVED", js(stats), flush=True)
    return stats


def prepare_permits(db, receipts):
    points = address_candidates(db)
    records = [json.loads(raw) for (raw,) in db.execute("SELECT raw FROM source_records WHERE source='issued-building-permits' ORDER BY objectid")]
    numbers = Counter(f["properties"].get("PermitNumber") for f in records)
    rows, stats = [], Counter()
    for feature in records:
        props = feature["properties"]
        address = props.get("ProjectAddress")
        matches = points.get(norm(address), {})
        coordinate = next(iter(matches)) if len(matches) == 1 else (None, None)
        issued = clean(props.get("IssuedDate"))
        if issued and re.fullmatch(r"\d{8}", issued):
            issued = f"{issued[:4]}-{issued[4:6]}-{issued[6:8]}"
        number = props.get("PermitNumber") or str(props["ObjectId"])
        identity = "surrey-permit:" + number + (":" + str(props["ObjectId"]) if numbers.get(number, 0) > 1 else "")
        raw = {"municipality": "Surrey", "source_url": receipts["issued-building-permits"]["url"],
               "source": "surrey-issued-building-permits", "attributes": props,
               "location_match": "exact civic address at a municipal location; not a PID/unit assignment" if coordinate[0] is not None else "ambiguous or unmatched exact address",
               "location_source_url": matches[coordinate] if coordinate[0] is not None else None}
        row = {"id": identity, "address": address, "description": " · ".join(str(props[k]) for k in ("WorkDescription", "SubDescription") if props.get(k)),
               "issued": issued, "value": props.get("ValueOfConstruction"), "type": props.get("WorkDescription"),
               "lat": coordinate[1], "lon": coordinate[0], "raw": js(raw)}
        rows.append(row)
        stats["mapped" if coordinate[0] is not None else "unmapped"] += 1
    write_jsonl(OUT / "permits-surrey.jsonl", rows)
    return rows, dict(stats)


def write_jsonl(path, rows):
    temp = path.with_suffix(".jsonl.part")
    with temp.open("w", encoding="utf-8") as stream:
        for row in rows:
            stream.write(js(row) + "\n")
    temp.replace(path)


def display_layer(context, source, name_field):
    original = context.layers[source]
    base_features = []
    for feature, geometry in zip(original["features"], original["geometries"]):
        p = feature["properties"]
        name = clean(p.get(name_field)) or clean(p.get("DESCRIPTION")) or ("Park " + str(p["OBJECTID"]))
        if source == "communities":
            name = name.title()
        properties = {"name": name, "municipality": "Surrey", "source_url": clean(p.get("WEBLINK")) or context.receipts[source]["url"], "source_id": p["OBJECTID"]}
        if source == "zoning":
            properties["description"] = p.get("ZONING")
        if source == "parks":
            geometry = geometry.representative_point()
            properties["location_method"] = "representative point of the full municipal park polygon"
        base_features.append((properties, geometry))
    if source == "zoning":
        # Same-zone polygons have identical meaning; dissolve their boundaries
        # before simplifying to avoid thousands of repeated bylaw URL strings.
        groups = defaultdict(list)
        for properties, geometry in base_features:
            key = (properties["name"], properties.get("description"), properties["source_url"])
            groups[key].append(geometry)
        base_features = []
        for (name, description, url), geometries in groups.items():
            properties = {"name": name, "description": description, "municipality": "Surrey", "source_url": url,
                          "source_feature_count": len(geometries)}
            geometry = shapely.union_all(geometries)
            if not geometry.is_valid:
                geometry = shapely.make_valid(geometry)
            base_features.append((properties, geometry))
    for tolerance in (0, .00001, .00003, .00005, .0001, .0002, .0005, .001):
        features = []
        for props, geometry in base_features:
            simplified = geometry.simplify(tolerance, preserve_topology=True) if tolerance else geometry
            # Valid-output precision avoids invalid rings when shortening coordinates.
            if tolerance:
                simplified = shapely.set_precision(simplified, 0.000001, mode="valid_output")
            features.append({"type": "Feature", "properties": props, "geometry": mapping(simplified)})
        value = {"type": "FeatureCollection", "features": features, "display_only": True,
                 "simplification_tolerance_degrees": tolerance, "full_resolution_source": context.receipts[source]["url"]}
        data = js(value)
        if len(data.encode()) < LAYER_LIMIT:
            return data, {"features": len(features), "bytes": len(data.encode()), "tolerance_degrees": tolerance}
    raise RuntimeError(f"Display layer too large: {source}")


def prepare_documents(db, receipts, permits, summary):
    rows = []
    zones = set()
    for (raw,) in db.execute("SELECT raw FROM source_records WHERE source='zoning'"):
        p = json.loads(raw)["properties"]
        key = (p.get("ZONING_TEXT"), p.get("ZONING"), p.get("WEBLINK"))
        if key in zones:
            continue
        zones.add(key)
        rows.append({"id": "surrey-zone:" + hashlib.sha256(js(key).encode()).hexdigest()[:20],
            "title": f"Surrey zoning {key[0]} — {key[1]}",
            "content": f"City of Surrey mapped zoning code {key[0]}. Official zone description: {key[1]}. Bylaw references: " + ", ".join(str(p[k]) for k in ["BY_LAW_01", "BY_LAW_02", "BY_LAW_03", "BY_LAW_04"] if clean(p.get(k))) + ". The zoning designation does not establish current building characteristics or permission for a particular project. Consult the linked current bylaw and municipality.",
            "url": clean(key[2]) or receipts["zoning"]["url"], "source": "surrey-zoning", "embedding": None, "updated": UPDATED})
    for (raw,) in db.execute("SELECT raw FROM source_records WHERE source='development-applications' ORDER BY objectid"):
        p = json.loads(raw)["properties"]
        rows.append({"id": "surrey-development:" + str(p["OBJECTID"]), "title": f"Surrey development application {p.get('PROJECT_NO')}",
            "content": f"City of Surrey development application {p.get('PROJECT_NO')}. Status: {p.get('STATUS')}. Description: {p.get('DESCRIPTION')}. This is a development application record, not an issued building permit. Application documents: {p.get('APPLICATION_DOCUMENTS_WEBLINK') or 'Not supplied'}.",
            "url": clean(p.get("WEBLINK")) or receipts["development-applications"]["url"], "source": "surrey-development-applications", "embedding": None, "updated": UPDATED})
    for permit in permits:
        rows.append({"id": "doc:" + permit["id"], "title": f"Surrey building permit: {permit['address']}",
            "content": f"City of Surrey issued building permit {permit['id'].removeprefix('surrey-permit:')} at {permit['address']}. Issued {permit['issued']}. Work: {permit['description']}. Reported construction value: {permit['value']}. Construction value is not property assessment, asking price or sale price.",
            "url": receipts["issued-building-permits"]["url"], "source": "surrey-issued-building-permits", "embedding": None, "updated": UPDATED})
    rows.append({"id": "surrey-data-coverage", "title": "Surrey property assessments and municipal data coverage", "content": js(summary),
        "url": PROPERTY_URL, "source": "surrey-property-detail", "embedding": None, "updated": UPDATED})
    write_jsonl(OUT / "documents-surrey.jsonl", rows)
    return len(rows)


def output_all(db, receipts, context, stats, permits, permit_stats, base_count):
    property_path = OUT / "properties-surrey.jsonl"
    neighbourhoods, values = Counter(), defaultdict(list)
    count = assessed = mapped = 0
    with property_path.with_suffix(".jsonl.part").open("w", encoding="utf-8") as stream:
        for (record,) in db.execute("SELECT record FROM output_properties ORDER BY id"):
            row = json.loads(record)
            if set(row) != set(FIELDS):
                raise RuntimeError("Property field schema mismatch")
            if row["municipality"] != "Surrey":
                raise RuntimeError("Non-Surrey normalized municipality")
            if any(not isinstance(row[field], str) for field in ["history", "features", "parcel"]):
                raise RuntimeError("Expected serialized JSON property fields")
            stream.write(record + "\n")
            count += 1
            assessed += row["assessed"] is not None and row["assessed"] > 0
            mapped += row["lat"] is not None and row["lon"] is not None
            if row["neighbourhood"]:
                neighbourhoods[row["neighbourhood"]] += 1
                if row["assessed"] is not None and row["assessed"] > 0:
                    values[row["neighbourhood"]].append(row["assessed"])
    property_path.with_suffix(".jsonl.part").replace(property_path)
    neighbourhood_rows = []
    for name, n in sorted(neighbourhoods.items()):
        row = {"name": name, "count": n}
        if values[name]:
            row["median"] = float(np.median(values[name]))
        neighbourhood_rows.append(row)
    datasets = [{"title": r["title"], "url": r["url"], "records": r["records"], "kind": name} for name, r in sorted(receipts.items())]
    summary = {"municipality": "Surrey", "properties": count, "assessments": assessed, "neighbourhoods": neighbourhood_rows,
        "datasets": datasets, "limits": ["Assessments are 2026 municipal values, not asking prices or appraisals.",
            "No dwelling floor area, bedrooms, bathrooms, year built or annual property tax is supplied by these sources.",
            "Cadastral PLAN_YEAR is not building age; parcel area is not living area.",
            "Shared strata lot geometry does not identify an individual unit boundary.",
            "Repeated identical PID/folio values across addresses are one assessed entity. Differing values under the same PID/folio are preserved as unresolved source variants with no whole-property value assigned.",
            "School catchment polygons do not guarantee admission. Park/school distances are approximate straight-line distances.",
            "Development applications are distinct from issued building permits; the permit source is the complete current published table.",
            "Display polygons are simplified; complete source geometries and all source records are preserved locally."], "updated": UPDATED}
    layers, layer_stats = [], {}
    for source, identity, field in [("zoning", "zoning:Surrey", "ZONING_TEXT"), ("communities", "neighbourhoods:Surrey", "NAME"), ("parks", "parks:Surrey", "PARK_NAME")]:
        data, metrics = display_layer(context, source, field)
        layers.append({"id": identity, "data": data})
        layer_stats[identity] = metrics
    layers.append({"id": "municipal-summary:Surrey", "data": js(summary)})
    write_jsonl(OUT / "layers-surrey.jsonl", layers)
    sources = [{"id": r["id"], "title": r["title"], "url": r["url"], "downloaded": r["downloaded_at"],
        "records": r["records"], "bytes": r["bytes"], "sha256": r["sha256"], "status": "verified",
        "detail": "Complete raw source and metadata preserved. " + r["licence"] + ". " + r.get("limitations", "")}
        for r in receipts.values()]
    write_jsonl(OUT / "sources-surrey.jsonl", sources)
    document_count = prepare_documents(db, receipts, permits, summary)
    source_rows = receipts["property-detail"]["records"]
    report = {"status": "complete", "updated": UPDATED, "base_surrey_properties": base_count,
        "official_source_rows": source_rows, "identical_official_rows_deduplicated": source_rows - db.execute("SELECT COUNT(*) FROM official_entities").fetchone()[0],
        "properties": count, "assessments": assessed, "mapped_properties": mapped, "unmapped_properties": count - mapped,
        "property_processing": stats, "permits": len(permits), "permit_locations": permit_stats, "documents": document_count,
        "sources": len(sources), "display_layers": layer_stats, "summary": summary,
        "id_policy": "Existing ID retained for one base PID to one deduplicated official entity, including one consolidated unresolved PID/folio entity. All other original base IDs are retained. Otherwise sry:<minimum original OBJECTID>.",
        "deduplication_key": "Valid PID plus nonempty folio plus land/improvement/total valuation; alternate unit/address rows collapse into one assessed entity. Incomplete identifiers retain official property number and exact address.",
        "archive": "data/raw/surrey-extra/integration.sqlite", "financial_scope": json.loads(db.execute("SELECT data FROM financial_scope_summary WHERE id=1").fetchone()[0]), "output_files": {}}
    for path in OUT.glob("*-surrey.jsonl"):
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            while block := stream.read(1024 * 1024):
                digest.update(block)
        report["output_files"][path.name] = {"bytes": path.stat().st_size, "sha256": digest.hexdigest()}
    temp = RAW / "integration-report.json.part"
    temp.write_text(js(report), encoding="utf-8")
    temp.replace(RAW / "integration-report.json")
    print("SURREY_INTEGRATION_COMPLETE", js({k: v for k, v in report.items() if k not in ("summary", "output_files")}), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--export-only", action="store_true")
    parser.add_argument("--reuse-base", action="store_true", help="Reuse the already archived unchanged regional base and entity groups")
    args = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    db = connect()
    receipts = archive_sources(db)
    base_count = db.execute("SELECT COUNT(*) FROM base_properties").fetchone()[0] if args.export_only or args.reuse_base else archive_base(db)
    if args.reuse_base and not base_count:
        raise RuntimeError("No archived regional base available to reuse")
    context = Context(db, receipts)
    if not args.export_only:
        prepare_properties(db, receipts, context)
    resolve_financial_scope(db)
    if True:
        stats = Counter()
        for identity, raw, origin in db.execute("SELECT id,record,origin FROM output_properties"):
            row = json.loads(raw)
            if origin == "official":
                features = json.loads(row["features"])
                stats["official_entities"] += 1
                stats["existing_ids_preserved" if not identity.startswith("sry:") else "new_official_entities"] += 1
                stats["official_mapped" if row["lat"] is not None else "official_unmapped"] += 1
                stats["official_entities_with_mslink_geometry" if features.get("parcel_source_record_ids") else "official_entities_without_mslink_geometry"] += 1
                stats["shared_lot_geometry"] += bool(features.get("parcel_geometry_shared"))
            else:
                stats["base_parcels_without_unique_assessment"] += 1
        covered = sum(json.loads(json.loads(raw)["features"])["source_record_count"] for (raw,) in db.execute("SELECT record FROM output_properties WHERE origin='official'"))
        if covered != receipts["property-detail"]["records"]:
            raise RuntimeError("Cannot export incomplete official source coverage")
        stats = dict(stats)
    permits, permit_stats = prepare_permits(db, receipts)
    output_all(db, receipts, context, stats, permits, permit_stats, base_count)
    db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    db.close()


if __name__ == "__main__":
    main()
