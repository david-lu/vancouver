"""Prepare auditable Burnaby property enrichment from complete public archives.

Run with the workspace .venv Python. Only this city's JSONL artifacts are written;
the regional SQLite source remains read-only. Assessment years are not inferred.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from contextlib import nullcontext
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3

import shapely
from shapely.geometry import Point, mapping, shape
from shapely.ops import transform
from shapely.strtree import STRtree

ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / "data/raw/burnaby-extra"
OUT = ROOT / "data/prepared"
FIELDS = "id,pid,address,street,neighbourhood,municipality,lat,lon,assessed,land,improvement,tax,year,built,zone,type,change,history,features,parcel".split(",")
NOW = datetime.now(timezone.utc).isoformat()
ASSESSMENT_LABEL = "Current reported assessment (year not supplied)"
X_SCALE = 111195 * math.cos(math.radians(49.25))
Y_SCALE = 111195


def js(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def read(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def unpack(value, default):
    return json.loads(value) if isinstance(value, str) else value if value is not None else default


def clean(value):
    text = str(value).strip() if value is not None else ""
    return text if text and text.upper() not in {"NULL", "N/A", "NO_DATA"} else None


def pid(value):
    text = clean(value)
    if text and re.fullmatch(r"(?:\d{3}-\d{3}-\d{3}|\d{9})", text):
        digits = text.replace("-", "")
        return digits if digits != "000000000" else None
    return None


def display_pid(value):
    return f"{value[:3]}-{value[3:6]}-{value[6:]}" if value else None


def number(value):
    try:
        n = float(value)
        return n if math.isfinite(n) else None
    except (ValueError, TypeError):
        return None


def iter_array(path, key):
    decoder = json.JSONDecoder()
    with path.open(encoding="utf-8-sig") as stream:
        buffer = ""
        while True:
            block = stream.read(1024 * 1024)
            if not block:
                raise ValueError(f"Missing {key} array: {path}")
            buffer += block
            match = re.search(r'"' + key + r'"\s*:\s*\[', buffer)
            if match:
                buffer = buffer[match.end():]
                break
        while True:
            buffer = buffer.lstrip(" \r\n\t,")
            if buffer.startswith("]"):
                return
            try:
                value, end = decoder.raw_decode(buffer)
                yield value
                buffer = buffer[end:]
            except json.JSONDecodeError:
                block = stream.read(1024 * 1024)
                if not block:
                    raise
                buffer += block


def sha(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            h.update(block)
    return h.hexdigest()


def project(geometry):
    return transform(lambda x, y, z=None: (x * X_SCALE, y * Y_SCALE), geometry)


class Spatial:
    def __init__(self, name):
        self.name = name
        self.features = list(iter_array(RAW / f"{name}.geojson", "features"))
        self.items = []
        self.geoms = []
        for feature in self.features:
            if not feature.get("geometry"):
                continue
            geom = shape(feature["geometry"])
            if not geom.is_valid:
                geom = shapely.make_valid(geom)
            if not geom.is_empty:
                self.items.append(feature["properties"])
                self.geoms.append(geom)
        self.tree = STRtree(self.geoms)
        self.url = read(RAW / f"{name}.receipt.json")["url"]

    def covers(self, point):
        indices = self.tree.query(point, predicate="intersects")
        return [self.items[int(i)] for i in sorted(indices, key=lambda i: self.geoms[int(i)].area)]


class Context:
    def __init__(self):
        self.zones = Spatial("zoning")
        self.areas = Spatial("community-plan-areas")
        self.parks = Spatial("parks")
        self.ocp = Spatial("ocp-land-use")
        self.park_geoms = [project(g) for g in self.parks.geoms]
        self.park_tree = STRtree(self.park_geoms)
        self.heritage = defaultdict(list)
        for f in iter_array(RAW / "heritage-landmarks.geojson", "features"):
            p = f["properties"]
            if key := pid(p.get("LTO_PID")):
                self.heritage[key].append(p)
        self.heritage_url = read(RAW / "heritage-landmarks.receipt.json")["url"]
        self.cache = {}

    def get(self, lon, lat):
        key = (round(lon, 8), round(lat, 8))
        if key in self.cache:
            return self.cache[key]
        point = Point(lon, lat)
        zones = self.zones.covers(point)
        areas = self.areas.covers(point)
        ocp = self.ocp.covers(point)
        projected = project(point)
        park_index = int(self.park_tree.nearest(projected))
        park = self.parks.items[park_index]
        result = {
            "zones": sorted({clean(z.get("ZONECODE")) for z in zones if clean(z.get("ZONECODE"))}),
            "zone_overlays": sorted({clean(z.get("OVERLAY_ZONE")) for z in zones if clean(z.get("OVERLAY_ZONE"))}),
            "zone_cd": sorted({clean(z.get("CD_ZONE")) for z in zones if clean(z.get("CD_ZONE"))}),
            "planning_areas": sorted({clean(a.get("NAME")) for a in areas if clean(a.get("NAME"))}),
            "ocp": sorted({clean(a.get("LUDESC1")) for a in ocp if clean(a.get("LUDESC1"))}),
            "park": {"name": clean(park.get("NAME")), "metres": round(projected.distance(self.park_geoms[park_index])),
                     "distance_note": "Approximate straight-line distance to mapped park boundary; not walking distance or an entrance.",
                     "source_url": self.parks.url, "classification": park.get("CLASSIFICATION"), "status": park.get("PARK_STATUS")},
        }
        self.cache[key] = result
        return result

    def enrich(self, row, features, official=None):
        sources = [{"title": "Burnaby legal parcels", "url": LEGAL_URL}]
        facts = list(features.get("municipal_facts") or [])
        if row.get("lon") is not None and row.get("lat") is not None:
            ctx = self.get(row["lon"], row["lat"])
            features["park"] = ctx["park"]
            features["municipal_spatial_match"] = "Parcel representative point intersects official municipal polygons; boundary overlaps retained."
            features["municipal_zoning"] = {"codes": ctx["zones"], "cd_zones": ctx["zone_cd"], "overlays": ctx["zone_overlays"], "source_url": self.zones.url}
            features["community_plan_areas"] = ctx["planning_areas"]
            features["ocp_land_use"] = ctx["ocp"]
            if not row.get("zone") and ctx["zones"]:
                row["zone"] = ", ".join(ctx["zones"])
            if not row.get("neighbourhood") and ctx["planning_areas"]:
                row["neighbourhood"] = " / ".join(ctx["planning_areas"])
                features["neighbourhood_match"] = "Community plan area containing parcel representative point; this layer is not a complete neighbourhood partition."
            sources += [{"title": title, "url": obj.url} for title, obj in [
                ("Burnaby zoning", self.zones), ("Burnaby community plan areas", self.areas),
                ("Burnaby parks", self.parks), ("Burnaby OCP land use", self.ocp)]]
        key = pid(row.get("pid"))
        if key and key in self.heritage:
            features["heritage_records"] = self.heritage[key]
            statuses = sorted({clean(h.get("STATUS")) for h in self.heritage[key] if clean(h.get("STATUS"))})
            facts.append({"label": "Heritage source record (exact PID)", "value": "; ".join(statuses) or "Listed in municipal heritage layer; see record for designation details.", "url": self.heritage_url})
            sources.append({"title": "Burnaby heritage landmarks", "url": self.heritage_url})
        if official:
            sources.insert(0, {"title": "Burnaby reported property assessments", "url": ASSESS_URL})
            for field, label in [("PER_LOT_WIDTH", "Reported lot width (source units unspecified)"),
                                 ("PER_LOT_DEPTH", "Reported lot depth (source units unspecified)"),
                                 ("PER_LOT_AREA", "Reported lot area (source units unspecified; may describe shared site)")]:
                if number(official.get(field)) is not None:
                    facts.append({"label": label, "value": str(official[field]), "url": ASSESS_URL})
            if clean(official.get("SSMUH_R1_MAX_UNITS")):
                facts.append({"label": "Reported SSMUH R1 maximum units (verify eligibility and approvals)", "value": str(official["SSMUH_R1_MAX_UNITS"]), "url": ASSESS_URL})
        features["municipality_context_sources"] = sources
        features["municipal_facts"] = facts
        return row, features


ASSESS_URL = "https://gis.burnaby.ca/arcgis/rest/services/BurnabyMap/BBY_PUBLIC_TBL/MapServer/1"
LEGAL_URL = "https://gis.burnaby.ca/arcgis/rest/services/OpenData/OpenData4/MapServer/7"


def load_staging(connection, source):
    connection.executescript("""
        CREATE TABLE base(id TEXT PRIMARY KEY,pid TEXT,record TEXT);
        CREATE TABLE official(property_number TEXT PRIMARY KEY,pid TEXT,record TEXT);
        CREATE TABLE emitted(id TEXT PRIMARY KEY,neighbourhood TEXT,assessed REAL,official INTEGER,mapped INTEGER);
    """)
    count = 0
    for row_id, raw_pid, record in source.execute("SELECT id,pid,record FROM properties WHERE municipality='Burnaby'"):
        connection.execute("INSERT INTO base VALUES(?,?,?)", (row_id, pid(raw_pid), record))
        count += 1
    connection.execute("CREATE INDEX base_pid ON base(pid)")
    aliases = 0
    for record in iter_array(RAW / "property-assessments.json", "records"):
        if record.get("ADDR_STATUS") != "L" or record.get("LEGAL_TYPE") == "ALIAS":
            aliases += 1
            continue
        connection.execute("INSERT INTO official VALUES(?,?,?)", (clean(record["PROPERTY_NUMBER"]), pid(record.get("PTS_LTO_PID")), js(record)))
    connection.executescript("""
        CREATE INDEX official_pid ON official(pid);
        CREATE TABLE base_count AS SELECT pid,count(*) AS n FROM base WHERE pid IS NOT NULL GROUP BY pid;
        CREATE UNIQUE INDEX base_count_pid ON base_count(pid);
        CREATE TABLE official_count AS SELECT pid,count(*) AS n FROM official WHERE pid IS NOT NULL GROUP BY pid;
        CREATE UNIQUE INDEX official_count_pid ON official_count(pid);
        CREATE TABLE stable AS SELECT b.id,o.property_number,o.pid FROM base b
          JOIN base_count bc ON bc.pid=b.pid AND bc.n=1
          JOIN official_count oc ON oc.pid=b.pid AND oc.n=1
          JOIN official o ON o.pid=b.pid;
        CREATE UNIQUE INDEX stable_id ON stable(id);
        CREATE UNIQUE INDEX stable_property ON stable(property_number);
    """)
    connection.commit()
    return {"base_rows": count, "primary_official_rows": connection.execute("SELECT count(*) FROM official").fetchone()[0],
            "aliases_archived_not_imported": aliases, "stable_exact_pid_replacements": connection.execute("SELECT count(*) FROM stable").fetchone()[0]}


class GeometryResolver:
    def __init__(self, connection):
        self.connection = connection
        self.parcels = []
        self.index = {field: defaultdict(list) for field in ("LTO_PID", "PROPERTY_NUMBER", "LOT_ID", "GIS_ID")}
        self.cache = {}
        for feature in iter_array(RAW / "legal-parcels.geojson", "features"):
            if not feature.get("geometry"):
                continue
            index = len(self.parcels)
            self.parcels.append(feature)
            for field, lookup in self.index.items():
                value = feature["properties"].get(field)
                key = pid(value) if field == "LTO_PID" else clean(value)
                if key:
                    lookup[key].append(index)

    def municipal(self, indices, method, shared):
        key = tuple(sorted(set(indices)))
        if key not in self.cache:
            geometries = [shape(self.parcels[i]["geometry"]) for i in key]
            geometries = [g if g.is_valid else shapely.make_valid(g) for g in geometries]
            geom = shapely.union_all(geometries)
            point = geom.representative_point()
            self.cache[key] = (mapping(geom), point.x, point.y)
        geom, lon, lat = self.cache[key]
        return geom, lon, lat, {"coordinate_match": method, "geometry_source_url": LEGAL_URL,
            "geometry_source_objectids": [self.parcels[i]["properties"].get("OBJECTID") for i in key],
            "shared_strata_geometry": bool(shared), "geometry_note": "Shared site/parent geometry; this does not locate an individual dwelling within a building." if shared else "Official parcel representative point; not an entrance or dwelling footprint."}

    def resolve(self, official, existing=None):
        key = pid(official.get("PTS_LTO_PID"))
        is_strata = official.get("LEGAL_TYPE") == "STRATA"
        if existing and existing.get("parcel") and existing.get("lat") is not None:
            old_features = unpack(existing.get("features"), {})
            return unpack(existing["parcel"], None), existing["lon"], existing["lat"], {
                "coordinate_match": "exact PID to unique regional parcel record", "geometry_source_url": old_features.get("source_url"),
                "shared_strata_geometry": is_strata, "geometry_note": "Parcel/site geometry can be shared by strata units; it does not identify a unit's position."}
        if key:
            count = self.connection.execute("SELECT n FROM base_count WHERE pid=?", (key,)).fetchone()
            if count and count[0] == 1:
                base = json.loads(self.connection.execute("SELECT record FROM base WHERE pid=?", (key,)).fetchone()[0])
                if base.get("parcel") and base.get("lat") is not None:
                    return self.resolve(official, base)
            if indices := self.index["LTO_PID"].get(key):
                return self.municipal(indices, "exact PID to municipal legal parcels", is_strata)
        candidates = [("PROPERTY_NUMBER", "PROPERTY_NUMBER"), ("LGLLOT_PROPNUM", "PROPERTY_NUMBER"),
                      ("PARENT_PROPNUM", "PROPERTY_NUMBER"), ("BPD_LOT_ID", "LOT_ID")]
        for source_field, parcel_field in candidates:
            value = clean(official.get(source_field))
            if value and (indices := self.index[parcel_field].get(value)):
                return self.municipal(indices, f"municipal {source_field} to legal parcel {parcel_field}",
                                      is_strata or source_field != "PROPERTY_NUMBER")
        return None, None, None, {"coordinate_match": "unmatched; no unambiguous exact PID or municipal parcel identifier join",
                                  "shared_strata_geometry": False, "geometry_source_url": None}


def official_row(official, resolver, existing=None):
    row = {field: None for field in FIELDS}
    features = unpack(existing.get("features"), {}) if existing else {}
    row.update(existing or {})
    geom, lon, lat, geom_note = resolver.resolve(official, existing)
    row.update(id=existing["id"] if existing else "bby:" + str(official["PROPERTY_NUMBER"]),
               pid=display_pid(pid(official.get("PTS_LTO_PID"))), address=clean(official.get("ADDR_FULL")) or "Burnaby property " + str(official["PROPERTY_NUMBER"]),
               street=" ".join(filter(None, [clean(official.get(f)) for f in ("ADDR_ST_NAME", "ADDR_ST_TYPE", "ADDR_ST_SFX")])),
               neighbourhood=clean(official.get("PER_PSA")), municipality="Burnaby", lat=lat, lon=lon,
               assessed=number(official.get("PTS_GROSS_ASSD_VALUE_CURRENT")), land=number(official.get("PTS_GROSS_LAND_VALUE_CURRENT")),
               improvement=number(official.get("PTS_GROSS_IMPR_VALUE_CURRENT")), tax=None, year=None, built=None,
               zone=clean(official.get("PER_ZONING")), type=clean(official.get("LEGAL_TYPE")), parcel=geom)
    previous = number(official.get("PTS_GROSS_ASSD_VALUE_PREV"))
    row["change"] = round(100 * (row["assessed"] - previous) / previous, 3) if row["assessed"] is not None and previous and previous > 0 else None
    history = []
    for period, label in [("PREV", "Previous reported assessment (year not supplied)"), ("CURRENT", ASSESSMENT_LABEL)]:
        value = number(official.get(f"PTS_GROSS_ASSD_VALUE_{period}"))
        if value is not None:
            history.append({"year": None, "label": label, "period": period.lower(), "assessed": value, "value": value, "tax": None,
                            "land": number(official.get(f"PTS_GROSS_LAND_VALUE_{period}")),
                            "improvement": number(official.get(f"PTS_GROSS_IMPR_VALUE_{period}")), "source_url": ASSESS_URL})
    row["history"] = history
    features.update(source="burnaby-property-assessments", source_url=ASSESS_URL, assessment_label=ASSESSMENT_LABEL,
                    assessment_status="reported municipal current/previous values; calendar years not supplied", assessed_not_asking=True,
                    source_property_number=official["PROPERTY_NUMBER"], source_objectid=official["OBJECTID"],
                    source_pid=official.get("PTS_LTO_PID"), address_match="official primary property table address (ADDR_STATUS L)",
                    neighbourhood_match="official property table PER_PSA" if row["neighbourhood"] else None,
                    assessment_join="exact valid PID; one base row and one primary municipal row" if existing else "source-specific municipal property number; no assessment attached to ambiguous base PID",
                    municipal_property=official, **geom_note)
    features["assessment_period_note"] = "CURRENT and PREV are the source's labels. No calendar year or valuation date was published in the source schema."
    return row, features


def serialize_property(row, features):
    result = {field: row.get(field) for field in FIELDS}
    result["features"] = js(features)
    result["history"] = js(unpack(result["history"], []))
    geometry = unpack(result.get("parcel"), None)
    if geometry and len(js(geometry).encode()) > 1200000:
        geom = shape(geometry)
        tolerance = 0.000005
        while len(js(geometry).encode()) > 1200000:
            geometry = mapping(geom.simplify(tolerance, preserve_topology=True))
            tolerance *= 2
        features["display_geometry_simplified"] = True
        result["features"] = js(features)
    result["parcel"] = js(geometry) if geometry else None
    if len(js(result).encode()) >= 1500000:
        raise ValueError(f"Property exceeds D1 string guard: {result['id']}")
    return result


def display_layer(spatial, name_field, point=False):
    def collection(tolerance):
        features = []
        for props, geom in zip(spatial.items, spatial.geoms):
            shown = geom.representative_point() if point else geom.simplify(tolerance, preserve_topology=True)
            attributes = {"name": props.get(name_field), "municipality": "Burnaby", "source_url": spatial.url}
            if spatial.name == "zoning":
                attributes.update(zone=props.get("ZONECODE"), cd_zone=props.get("CD_ZONE"), overlay=props.get("OVERLAY_ZONE"))
            if point:
                attributes.update(classification=props.get("CLASSIFICATION"), status=props.get("PARK_STATUS"))
            features.append({"type": "Feature", "id": props.get("OBJECTID"), "geometry": mapping(shown), "properties": attributes})
        return {"type": "FeatureCollection", "features": features, "municipality": "Burnaby", "source_url": spatial.url,
                "display_geometry_note": "Park representative points; full polygons retained in raw archive." if point else "Simplified for display; complete source geometry retained in raw archive.", "simplification_tolerance_degrees": tolerance}
    tolerance = 0.00001
    data = collection(tolerance)
    while len(js(data).encode()) >= 1450000:
        tolerance *= 2
        data = collection(tolerance)
    return data


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    source = sqlite3.connect((ROOT / "data/regional.sqlite").resolve().as_uri() + "?mode=ro", uri=True)
    # Use an explicitly scoped temporary file: Windows sandbox temp directories
    # can carry inaccessible ACLs. Never delete directories recursively.
    staging_path = RAW / ".burnaby-integration-staging.sqlite"
    if staging_path.exists():
        staging_path.unlink()
    with nullcontext(RAW):
        connection = sqlite3.connect(str(staging_path))
        connection.execute("PRAGMA journal_mode=OFF")
        connection.execute("PRAGMA synchronous=OFF")
        report = load_staging(connection, source)
        assert report["primary_official_rows"] == 102700, report
        print(js({"stage": "staged", **report}), flush=True)
        context = Context()
        resolver = GeometryResolver(connection)
        geometry_counts, value_checks = Counter(), Counter()
        totals = Counter()
        files = {}
        path = OUT / "properties-burnaby.jsonl"
        with path.open("w", encoding="utf-8", newline="\n") as stream:
            def emit(row, features, official=None):
                row, features = context.enrich(row, features, official)
                result = serialize_property(row, features)
                mapped = int(result.get("lat") is not None and result.get("lon") is not None)
                connection.execute("INSERT INTO emitted VALUES(?,?,?,?,?)", (result["id"], result.get("neighbourhood"), result.get("assessed"), int(official is not None), mapped))
                stream.write(js(result) + "\n")
                totals["output_rows"] += 1
                totals["mapped_rows"] += mapped
                if official:
                    totals["official_rows"] += 1
                    totals["mapped_official_rows"] += mapped
                    totals["official_with_assessment"] += int(result["assessed"] is not None)
                    totals["mapped_official_with_assessment"] += int(mapped and result["assessed"] is not None)
                    totals["explicit_property_number_address_fallbacks"] += int(not clean(official.get("ADDR_FULL")))
                    geometry_counts[features["coordinate_match"]] += 1
                    for period in ("CURRENT", "PREV"):
                        values = [number(official.get(f"PTS_GROSS_{part}_VALUE_{period}")) for part in ("LAND", "IMPR", "ASSD")]
                        if all(v is not None for v in values):
                            value_checks[f"{period.lower()}_components_checked"] += 1
                            if abs(values[0] + values[1] - values[2]) > 0.01:
                                value_checks[f"{period.lower()}_component_mismatches"] += 1
                    assert row["year"] is None
                if totals["output_rows"] % 25000 == 0:
                    connection.commit()
                    print(js({"stage": "properties", **totals}), flush=True)
            query = "SELECT b.record,o.record FROM base b LEFT JOIN stable s ON s.id=b.id LEFT JOIN official o ON o.property_number=s.property_number ORDER BY b.id"
            for base_json, official_json in connection.execute(query):
                base = json.loads(base_json)
                if official_json:
                    official = json.loads(official_json)
                    row, features = official_row(official, resolver, base)
                    emit(row, features, official)
                else:
                    features = unpack(base.get("features"), {})
                    emit(base, features)
            query = "SELECT o.record FROM official o LEFT JOIN stable s ON s.property_number=o.property_number WHERE s.property_number IS NULL ORDER BY o.property_number"
            for (official_json,) in connection.execute(query):
                official = json.loads(official_json)
                row, features = official_row(official, resolver)
                emit(row, features, official)
        connection.commit()
        assert totals["official_rows"] == report["primary_official_rows"]
        assert totals["output_rows"] == report["base_rows"] + report["primary_official_rows"] - report["stable_exact_pid_replacements"]
        assert connection.execute("SELECT count(*) FROM emitted").fetchone()[0] == totals["output_rows"]
        report.update(totals)
        report["unmapped_official_rows"] = totals["official_rows"] - totals["mapped_official_rows"]
        report["unmapped_official_with_assessment"] = totals["official_with_assessment"] - totals["mapped_official_with_assessment"]
        report["geometry_join_counts"] = dict(geometry_counts)
        value_checks.setdefault("current_component_mismatches", 0)
        value_checks.setdefault("prev_component_mismatches", 0)
        report["value_checks"] = dict(value_checks)
        report["unknown_assessment_year_preserved"] = True
        report["history_value_field_validated"] = True
        receipts = [read(p) for p in sorted(RAW.glob("*.receipt.json"))]
        datasets = [{"title": "Burnaby " + r["id"].replace("-", " "), "url": r["url"], "records": r["records"], "kind": "assessment" if r["id"] == "property-assessments" else "municipal context"} for r in receipts]
        neighbourhoods = []
        for name, count in connection.execute("SELECT neighbourhood,count(*) FROM emitted WHERE neighbourhood IS NOT NULL AND neighbourhood<>'' GROUP BY neighbourhood ORDER BY count(*) DESC"):
            values = [r[0] for r in connection.execute("SELECT assessed FROM emitted WHERE neighbourhood=? AND assessed>0 ORDER BY assessed", (name,))]
            item = {"name": name, "count": count}
            if values:
                mid = len(values) // 2
                item["median"] = values[mid] if len(values) % 2 else (values[mid - 1] + values[mid]) / 2
            neighbourhoods.append(item)
        limits = ["Reported assessment periods are CURRENT/PREV; no calendar year or valuation date is supplied.",
                  "Assessments are not asking prices, sale prices, appraisals, or verified for-sale listings.",
                  "Exact unique PID joins preserve existing IDs; ambiguous or missing PID rows retain municipal property IDs.",
                  "Strata and parent/site geometry may be shared; mapped points do not locate individual units within buildings.",
                  "Community plan areas cover selected areas, not a complete neighbourhood partition; PER_PSA is preferred when reported.",
                  "Park distances are approximate straight-line distances to mapped boundaries, not walking routes.",
                  "Property table and capital works are public municipal APIs with City copyright; no dataset-specific open licence was located. Other downloaded OpenData layers use the Open Government Licence - Burnaby.",
                  "No current machine-readable building permit or development application dataset was integrated; building outlines are not permit records."]
        summary = {"municipality": "Burnaby", "properties": totals["output_rows"], "assessments": totals["official_with_assessment"],
                   "neighbourhoods": neighbourhoods, "datasets": datasets, "limits": limits, "updated": NOW}
        path = OUT / "layers-burnaby.jsonl"
        with path.open("w", encoding="utf-8", newline="\n") as stream:
            for layer_id, spatial, field, points in [("zoning:Burnaby", context.zones, "ZONECODE", False),
                                                     ("neighbourhoods:Burnaby", context.areas, "NAME", False),
                                                     ("parks:Burnaby", context.parks, "NAME", True)]:
                data = display_layer(spatial, field, points)
                stream.write(js({"id": layer_id, "data": js(data)}) + "\n")
            stream.write(js({"id": "municipal-summary:Burnaby", "data": js(summary)}) + "\n")
        with (OUT / "sources-burnaby.jsonl").open("w", encoding="utf-8", newline="\n") as stream:
            for r in receipts:
                source_row = {"id": "burnaby-" + r["id"], "title": "Burnaby " + r["id"].replace("-", " "), "url": r["url"],
                              "downloaded": r["downloaded_at"], "records": r["records"], "bytes": r["bytes"], "sha256": r["sha256"],
                              "status": "verified", "detail": f"Complete original attributes and geometry archived at {r['path']}. {r['licence']}. {r['licence_scope']} Attribution: {r['attribution']}."}
                stream.write(js(source_row) + "\n")
        with (OUT / "documents-burnaby.jsonl").open("w", encoding="utf-8", newline="\n") as stream:
            def document(doc_id, title, content, url, source_id):
                stream.write(js({"id": doc_id, "title": title, "content": content, "url": url, "source": source_id, "embedding": None, "updated": NOW}) + "\n")
            document("coverage:Burnaby", "Burnaby municipal property and context coverage", js(summary), ASSESS_URL, "burnaby-property-assessments")
            for r in receipts:
                text = f"The City of Burnaby {r['id'].replace('-', ' ')} public dataset was completely archived with {r['records']:,} source records. Fields: {', '.join(r['source_fields'])}. Licence/terms: {r['licence']}. {r['licence_scope']}."
                if r["id"] == "property-assessments":
                    text += f" The raw table includes 102,700 primary (ADDR_STATUS L) property rows and 32,240 address aliases. Only primary rows are imported as properties. {totals['official_with_assessment']:,} primary rows have current reported assessments; {totals['mapped_official_with_assessment']:,} of those have verified parcel-identifier geometry joins. CURRENT and PREV values have no published calendar year. Legal types and unit addresses are retained. A source lot area can describe a shared strata site, not unit floor area."
                elif r["id"] == "community-plan-areas":
                    text += " These 20 planning boundaries do not define a complete municipal neighbourhood partition. Official property PER_PSA labels are used when available."
                elif r["id"] == "zoning":
                    text += " ZONECODE, CD_ZONE and OVERLAY_ZONE are reported spatial context. They alone do not establish permitted use, development yield, or approval. Consult current zoning bylaws and the City."
                document("dataset:Burnaby:" + r["id"], "Burnaby " + r["id"].replace("-", " "), text, r["url"], "burnaby-" + r["id"])
            zone_counts = Counter(clean(p.get("ZONECODE")) for p in context.zones.items if clean(p.get("ZONECODE")))
            for zone, count in sorted(zone_counts.items()):
                document("zone:Burnaby:" + zone, f"Burnaby zoning map code {zone}",
                         f"The City of Burnaby public zoning layer reports zone code {zone} on {count} mapped polygons. A property map match uses its parcel representative point. CD_ZONE and OVERLAY_ZONE may provide additional mapped context. This is not a determination of permitted use, unit entitlement, or redevelopment approval; consult the current City zoning bylaw and official map.", context.zones.url, "burnaby-zoning")
        for output in sorted(OUT.glob("*-burnaby.jsonl")):
            record_count = 0
            max_bytes = 0
            with output.open(encoding="utf-8") as stream:
                for line in stream:
                    row = json.loads(line)
                    record_count += 1
                    max_bytes = max(max_bytes, len(line.encode()))
                    if output.name.startswith("layers"):
                        assert len(row["data"].encode()) < 1500000
                    if output.name.startswith("properties"):
                        assert set(row) == set(FIELDS)
                        features = json.loads(row["features"])
                        history = json.loads(row["history"])
                        if features.get("source") == "burnaby-property-assessments":
                            assert row["year"] is None
                            assert all(h["value"] == h["assessed"] and h["year"] is None for h in history)
            files[output.name] = {"path": str(output.relative_to(ROOT)), "records": record_count,
                                  "bytes": output.stat().st_size, "sha256": sha(output), "max_row_bytes": max_bytes}
        report.update(updated=NOW, outputs=files, limits=limits, sources=len(receipts), validation="passed: output schema, unique IDs, primary-only counts, join counts, source-value arithmetic, bounded layer size")
        (RAW / "integration-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        connection.close()
        staging_path.unlink()
    source.close()
    print(js(report), flush=True)


if __name__ == "__main__":
    main()
