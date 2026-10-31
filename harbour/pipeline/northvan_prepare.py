"""Build isolated North Vancouver importer assets; shared SQLite stays read-only."""
from __future__ import annotations

from collections import Counter, defaultdict
import csv
from datetime import datetime, timezone
import hashlib
import html
import json
import math
from pathlib import Path
import re
import sqlite3

import numpy as np
import shapely
from shapely.geometry import Point, mapping, shape
from shapely.strtree import STRtree

ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / "data/raw/northvan-extra"
OUT = ROOT / "data/prepared"
CITY = "North Vancouver City"
DISTRICT = "North Vancouver District"
FIELDS = "id,pid,address,street,neighbourhood,municipality,lat,lon,assessed,land,improvement,tax,year,built,zone,type,change,history,features,parcel".split(",")
SCALE = np.array([111320 * math.cos(math.radians(49.33)), 111320])


def js(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def write(path, value):
    temp = path.with_suffix(path.suffix + ".part")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


def jsonl(name, rows):
    path = OUT / (name + "-northvan.jsonl")
    temp = path.with_suffix(".jsonl.part")
    digest, count = hashlib.sha256(), 0
    with temp.open("wb") as output:
        for row in rows:
            line = (js(row) + "\n").encode("utf-8")
            output.write(line)
            digest.update(line)
            count += 1
    temp.replace(path)
    return {"file": str(path.relative_to(ROOT)), "records": count, "bytes": path.stat().st_size, "sha256": digest.hexdigest()}


def pid(value):
    value = str(value or "").strip()
    if not re.fullmatch(r"\d{3}-?\d{3}-?\d{3}", value):
        return None
    digits = value.replace("-", "")
    return digits if digits != "000000000" else None


def tidy(value):
    return re.sub(r"\s+", " ", str(value or "")).strip()


def unit(value):
    value = tidy(value).upper()
    return str(int(value)) if value.isdigit() else value


def rows_csv(key):
    with (RAW / key / "data.csv").open(encoding="utf-8-sig", errors="replace", newline="") as source:
        return list(csv.DictReader(source))


def features(key):
    return read(RAW / key / "data.geojson")["features"]


def receipt(key):
    return read(RAW / key / "receipt.json")


def source_url(key):
    return receipt(key)["source_url"]


class Layer:
    def __init__(self, key, name_field, municipality):
        self.key, self.name_field, self.municipality = key, name_field, municipality
        self.url = source_url(key)
        self.features, self.shapes = [], []
        for feature in features(key):
            if not feature.get("geometry"):
                continue
            geom = shape(feature["geometry"])
            if not geom.is_valid:
                geom = shapely.make_valid(geom)
            if geom.is_empty:
                continue
            self.features.append(feature)
            self.shapes.append(geom)
        if not self.shapes:
            raise ValueError(f"{key}: no usable geometry for map or spatial enrichment")
        self.tree = STRtree(self.shapes)
        self.metric_shapes = None
        self.metric_tree = None

    def at(self, point):
        found = self.tree.query(point, predicate="intersects")
        if len(found) == 0:
            return None
        # Select the smallest polygon where public layers overlap; record the
        # spatial membership method and keep all full geometries in raw archive.
        index = min(map(int, found), key=lambda i: self.shapes[i].area)
        return self.features[index]["properties"]

    def nearest(self, point):
        if self.metric_tree is None:
            self.metric_shapes = shapely.transform(self.shapes, lambda coords: coords * SCALE)
            self.metric_tree = STRtree(self.metric_shapes)
        metric = Point(point.x * SCALE[0], point.y * SCALE[1])
        index = int(self.metric_tree.nearest(metric))
        name = tidy(self.features[index]["properties"].get(self.name_field))
        return {"name": name, "metres": int(round(metric.distance(self.metric_shapes[index]))), "source_url": self.url, "method": "Approximate straight-line distance to mapped park boundary; not walking distance"}

    def display(self):
        tolerance = 0.000025
        for _ in range(12):
            out = []
            for feature, geom in zip(self.features, self.shapes):
                props = feature["properties"]
                name = tidy(props.get(self.name_field)) or "Unnamed mapped area"
                simplified = geom.simplify(tolerance, preserve_topology=True)
                out.append({"type": "Feature", "properties": {"name": name, "municipality": self.municipality, "source_url": self.url}, "geometry": mapping(simplified)})
            data = {"type": "FeatureCollection", "features": out, "source_url": self.url, "municipality": self.municipality, "display_simplification_degrees": tolerance}
            if len(js(data).encode("utf-8")) < 1_400_000:
                return data
            tolerance *= 2
        raise ValueError(f"{self.key}: display layer still exceeds safe D1 row size")


def city_indexes():
    by_pid = defaultdict(list)
    for feature in features("cnv-common-addresses"):
        props = feature["properties"]
        key = pid(props.get("PRC_PARCEL_NO"))
        if key and str(props.get("GIS_STATUS") or "").lower() != "inactive":
            by_pid[key].append(props)
    # Add legal parcel addresses only when the detailed address layer has no PID.
    for feature in features("cnv-legal-parcels"):
        props = feature["properties"]
        key = pid(props.get("PRC_PARCEL_NO"))
        if key and key not in by_pid:
            by_pid[key].append(props)
    capacity = defaultdict(list)
    for feature in features("cnv-ssmuh"):
        props = feature["properties"]
        key = pid(props.get("PID_CURRENT"))
        if key:
            capacity[key].append(props)
    return by_pid, capacity


def district_indexes():
    legal = defaultdict(list)
    for row in rows_csv("dnv-legal-descriptions"):
        key = pid(row.get("pid"))
        if key:
            legal[key].append(row)
    addresses = defaultdict(list)
    for row in rows_csv("dnv-addresses"):
        if row.get("STATUS") == "ACTIVE" and row.get("ASSET_ID"):
            addresses[row["ASSET_ID"].strip()].append(row)
    buildings_for_parcel = defaultdict(set)
    for row in rows_csv("dnv-building-parcel-links"):
        if row.get("DBLINK") and row.get("ASSET_ID"):
            buildings_for_parcel[row["DBLINK"].strip()].add(row["ASSET_ID"].strip())
    buildings = defaultdict(list)
    for feature in features("dnv-buildings"):
        props = feature["properties"]
        if props.get("Asset_Id"):
            buildings[props["Asset_Id"].strip()].append(props)
    return legal, addresses, buildings_for_parcel, buildings


def make_properties(layers, stats):
    city_addresses, city_capacity = city_indexes()
    district_legal, district_addresses, district_links, district_buildings = district_indexes()
    city_address_url = source_url("cnv-common-addresses")
    district_address_url = source_url("dnv-addresses")
    district_legal_url = source_url("dnv-legal-descriptions")
    building_url = source_url("dnv-buildings")
    capacity_url = source_url("cnv-ssmuh")
    db = sqlite3.connect("file:" + (ROOT / "data/regional.sqlite").as_posix() + "?mode=ro", uri=True)
    stats["base_counts"] = dict(db.execute("SELECT municipality,COUNT(*) FROM properties WHERE municipality IN (?,?) GROUP BY municipality", (CITY, DISTRICT)))
    stats["matching"] = {CITY: Counter(), DISTRICT: Counter()}
    stats["neighbourhood_counts"] = {CITY: Counter(), DISTRICT: Counter()}
    stats["assessment_counts"] = {CITY: 0, DISTRICT: 0}
    ids = set()
    total = 0
    for (raw,) in db.execute("SELECT record FROM properties WHERE municipality IN (?,?) ORDER BY id", (CITY, DISTRICT)):
        row = json.loads(raw)
        original_id = row["id"]
        if original_id in ids:
            raise ValueError("Duplicate base property ID")
        ids.add(original_id)
        municipality = row["municipality"]
        counters = stats["matching"][municipality]
        key = pid(row.get("pid"))
        context = json.loads(row["features"]) if isinstance(row["features"], str) else (row.get("features") or {})
        context_sources = []
        facts = []

        def add_source(title, url):
            if not any(x["url"] == url for x in context_sources):
                context_sources.append({"title": title, "url": url})

        if municipality == CITY and key:
            matches = city_addresses.get(key, [])
            addresses = sorted({tidy(x.get("ADDRESS")) for x in matches if tidy(x.get("ADDRESS"))})
            rolls = sorted({tidy(x.get("PRC_ROLL_NO")) for x in matches if tidy(x.get("PRC_ROLL_NO"))})
            if matches:
                counters["exact_pid_municipal_match"] += 1
                add_source("City of North Vancouver municipal addresses", city_address_url)
                context["municipal_address_candidates"] = addresses[:12]
                context["municipal_address_candidate_count"] = len(addresses)
                context["address_source_url"] = city_address_url
            if len(addresses) == 1:
                row["address"] = addresses[0]
                context["address_match"] = "Exact provincial PID in City municipal parcel/address data; one unique civic address"
                counters["exact_unique_address"] += 1
            elif len(addresses) > 1:
                context["municipal_address_note"] = "Several official civic addresses share this PID; candidate addresses are parcel-level, not a selected dwelling."
                counters["ambiguous_address_preserved"] += 1
            if len(rolls) == 1:
                context["cnv_roll"] = rolls[0]
                context["cnv_pid"] = row["pid"]
                counters["exact_roll_for_live_lookup"] += 1
            capacity = city_capacity.get(key, [])
            classes = sorted({tidy(x.get("MinimumUnits")) for x in capacity if tidy(x.get("MinimumUnits"))})
            if len(classes) == 1:
                facts.append({"label": "Municipal permitted housing capacity", "value": classes[0] + " (planning category; not existing homes)", "url": capacity_url})
                add_source("City small-scale multi-unit housing map", capacity_url)
                counters["permitted_capacity_fact"] += 1

        if municipality == DISTRICT and key:
            context["dnv_pid"] = row["pid"]
            records = district_legal.get(key, [])
            if records:
                counters["exact_pid_municipal_match"] += 1
                add_source("District legal parcel identifiers", district_legal_url)
            address_candidates, building_ids = {}, set()
            for legal in records:
                linked = district_links.get(tidy(legal.get("gislink")), set())
                building_ids.update(linked)
                wanted_unit = unit(legal.get("unit"))
                for building_id in linked:
                    for address in district_addresses.get(building_id, []):
                        # Unit identity must agree. A blank legal unit never
                        # selects an arbitrary apartment in a shared building.
                        if unit(address.get("UNIT")) != wanted_unit:
                            continue
                        label = tidy(address.get("ADDRESS"))
                        if label:
                            address_candidates[label] = address
            addresses = sorted(address_candidates)
            if addresses:
                add_source("District active civic addresses", district_address_url)
                context["municipal_address_candidates"] = addresses[:12]
                context["municipal_address_candidate_count"] = len(addresses)
                context["address_source_url"] = district_address_url
            if len(addresses) == 1:
                chosen = address_candidates[addresses[0]]
                row["address"] = addresses[0]
                row["street"] = tidy(chosen.get("STREET")) or row.get("street")
                context["address_match"] = "Exact PID → municipal parcel/building join → matching unit → one active civic address"
                counters["exact_unique_address"] += 1
            elif len(addresses) > 1:
                context["municipal_address_note"] = "Multiple active addresses remain after exact PID, building and unit matching; these are parcel address candidates."
                counters["ambiguous_address_preserved"] += 1
            if len(building_ids) == 1:
                inventory = district_buildings.get(next(iter(building_ids)), [])
                if len(inventory) == 1:
                    building = inventory[0]
                    add_source("District building inventory", building_url)
                    context["municipal_building_id"] = building.get("Asset_Id")
                    context["municipal_building_match"] = "Exact PID → municipal parcel → one linked building inventory record; attributes describe the building, not an individual unit"
                    stories = building.get("NumStories")
                    if isinstance(stories, (int, float)) and 0 < stories < 150:
                        context["building_storeys"] = stories
                        facts.append({"label": "Building storeys", "value": str(stories) + " (municipal inventory)", "url": building_url})
                        counters["building_storeys"] += 1
                    if tidy(building.get("BldgType")):
                        context["municipal_building_type"] = tidy(building["BldgType"])
                        facts.append({"label": "Building type", "value": tidy(building["BldgType"]), "url": building_url})
                    # Height was null in the entire verified source. AM_Date is
                    # not a documented year-built field and is never promoted.

        if row.get("lon") is not None and row.get("lat") is not None:
            point = Point(row["lon"], row["lat"])
            zoning, neighbourhood, parks = layers[municipality]
            zone_props = zoning.at(point)
            if zone_props:
                code = tidy(zone_props.get(zoning.name_field))
                if code:
                    row["zone"] = code
                    counters["zoning_spatial_match"] += 1
                    context["zoning_match"] = "Official zoning polygon at parcel representative point; not a zoning approval"
                    add_source(municipality + " zoning", zoning.url)
                    facts.append({"label": "Zoning", "value": code, "url": zoning.url})
                    if municipality == CITY and tidy(zone_props.get("FSR")):
                        facts.append({"label": "Zoning FSR provision", "value": tidy(zone_props["FSR"]) + " (planning limit, not existing floor area)", "url": zoning.url})
            neighbourhood_props = neighbourhood.at(point)
            if neighbourhood_props:
                name = tidy(neighbourhood_props.get(neighbourhood.name_field))
                if name:
                    row["neighbourhood"] = name.title() if municipality == DISTRICT else name
                    counters["neighbourhood_spatial_match"] += 1
                    context["neighbourhood_match"] = "Official neighbourhood polygon at parcel representative point"
                    add_source(municipality + " neighbourhood boundaries", neighbourhood.url)
            nearest = parks.nearest(point)
            if nearest["name"]:
                context["park"] = nearest
                counters["nearest_park"] += 1
                add_source(municipality + " parks", parks.url)

        # Monetary values and histories are preserved exactly; neither parcel
        # attributes nor a one-property live API sample create bulk assessments.
        context["municipality_context_sources"] = context_sources
        context["municipal_facts"] = facts
        context["municipal_enrichment_source"] = "northvan-extra"
        context["municipal_enrichment_date"] = datetime.now(timezone.utc).date().isoformat()
        row["features"] = js(context)
        if row["id"] != original_id or set(row) != set(FIELDS):
            raise ValueError("Property ID/schema changed unexpectedly")
        counters["rows"] += 1
        stats["neighbourhood_counts"][municipality][row.get("neighbourhood") or "Unassigned"] += 1
        if isinstance(row.get("assessed"), (int, float)) and row["assessed"] > 0:
            stats["assessment_counts"][municipality] += 1
        total += 1
        yield row
    if total != sum(stats["base_counts"].values()):
        raise ValueError("Output does not cover every North Vancouver base property")
    stats["property_ids_unique"] = len(ids)
    stats["property_ids_preserved"] = True
    db.close()


def permits():
    url = source_url("cnv-building-permits")
    for feature in features("cnv-building-permits"):
        props = feature["properties"]
        content = re.sub(r"<br\s*/?>", "\n", props.get("Content") or "", flags=re.I)
        content = html.unescape(re.sub(r"<[^>]*>", "", content))
        address_match = re.search(r"Address:\s*([^\n]+)", content, re.I)
        description_match = re.search(r"Description:\s*(.*)", content, re.I | re.S)
        point = shape(feature["geometry"]).representative_point() if feature.get("geometry") else None
        yield {"id": "cnv-building-permit:" + str(props["OBJECTID"]), "address": tidy(address_match.group(1)) if address_match else None, "description": tidy(description_match.group(1)) if description_match else content.strip(), "issued": None, "value": None, "type": "Building permit record", "lat": point.y if point else None, "lon": point.x if point else None, "raw": js({"municipality": CITY, "source_url": url, "original_data": props, "issuance_status": "Not provided in published layer", "match_note": "Public mapped permit record; location is a site, not a verified strata unit"})}


def make_sources():
    for folder in sorted(RAW.iterdir()):
        path = folder / "receipt.json"
        if not path.exists():
            continue
        r = read(path)
        yield {"id": "northvan:" + r["id"], "title": r["municipality"] + " · " + r["role"].replace("-", " "), "url": r["source_url"], "downloaded": r["downloaded_at"], "records": r["record_count"], "bytes": r.get("bytes", r.get("archive_bytes", 0)), "sha256": r.get("sha256", r.get("archive_sha256", "")), "status": "Complete public source download", "detail": r.get("note", "") + " " + r["license"]["status"] + " " + r["license"]["attribution"]}


def documents(sources):
    now = datetime.now(timezone.utc).isoformat()
    for source in sources:
        yield {"id": "doc:" + source["id"], "title": source["title"], "content": source["title"] + ". Complete downloaded source has " + str(source["records"]) + " records. " + source["detail"] + " Downloaded " + source["downloaded"] + ". Monetary assessment values are not provided by these parcel, address, zoning, neighbourhood or building layers. A missing value means unavailable, not zero.", "url": source["url"], "source": source["id"], "embedding": None, "updated": now}
    guidance = [
        ("cnv-zoning-guidance", "North Vancouver City: zoning and permitted capacity", "City zoning codes, FSR provisions and maximum heights describe planning regulations. Small-scale multi-unit housing MinimumUnits categories describe permitted capacity, not the number of homes presently on a parcel. The published map is a research aid and does not grant development approval. Confirm the current zoning bylaw, development permit rules and parcel circumstances with the City.", "https://www.cnv.org/Business-Development/Building/Land-Use-Approvals/Zoning"),
        ("dnv-property-guidance", "North Vancouver District: property records and assessment lookup", "District legal descriptions join provincial PIDs to GIS parcel identifiers. Building-to-parcel records connect the parcel to building inventory and active civic addresses. Unit matching and unique address checks prevent assigning one apartment address to another PID. The public Property Viewer provides land and improvement assessment values on demand, but the verified response contains no explicit assessment year. Tax-consolidated values may cover more than one legal property. Assessment is not asking price or market value.", "https://geoweb.dnv.org/properties/"),
        ("cnv-property-guidance", "North Vancouver City: exact PID and assessment lookup", "City common-address data contains both nine-digit provincial PIDs and municipal parcel identifiers beginning CNV. Only validated provincial PIDs are used for exact joins. The CityMap report resolves parcel and roll to specific address PIDs; its unit property endpoint returns land and improvement valuation. The verified response gives no explicit assessment year. Several addresses can share a PID; these remain parcel address candidates unless there is one unique civic address.", "https://gisext2.cnv.org/citymap/"),
        ("northvan-distance-guidance", "North Vancouver neighbourhoods, parks and buildings", "Neighbourhood and zoning enrichments use official polygons at the parcel representative point, so boundary cases require reviewing the full map. Nearest park distances are approximate straight-line distances to mapped park boundaries, not walking routes or entrances. District storeys are linked building inventory observations; shared building attributes are not individual-unit measurements. District building Height is entirely unpopulated in the downloaded source and remains unknown. AM_Date is not assumed to mean year built.", "https://geoweb.dnv.org/data/"),
    ]
    for key, title, content, url in guidance:
        yield {"id": "doc:northvan:" + key, "title": title, "content": content, "url": url, "source": "northvan-guidance", "embedding": None, "updated": now}


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    if read(RAW / "report.json")["status"] != "complete":
        raise ValueError("Full source downloads must complete before preparation")
    layers = {
        CITY: (Layer("cnv-zoning", "ZONING", CITY), Layer("cnv-neighbourhoods", "NHOOD", CITY), Layer("cnv-parks", "PARK_NAME", CITY)),
        DISTRICT: (Layer("dnv-zoning-polygons", "Zoning", DISTRICT), Layer("dnv-neighbourhoods", "Nbdy_Name_resolved", DISTRICT), Layer("dnv-parks", "Park_Name", DISTRICT)),
    }
    stats = {"status": "preparing", "started_at": datetime.now(timezone.utc).isoformat(), "outputs": [], "monetary_values": "Preserved from base; no new bulk North Vancouver values or years inferred", "height": "No values supplied: District Height field has zero non-null records", "match_policy": "Exact validated provincial PID, unique address or matching unit only; ambiguous address candidates labelled at parcel level"}
    stats["outputs"].append(jsonl("properties", make_properties(layers, stats)))
    sources = list(make_sources())
    display_layers = []
    for municipality, municipal_layers in layers.items():
        for role, layer in zip(["zoning", "neighbourhoods", "parks"], municipal_layers):
            data = layer.display()
            display_layers.append({"id": role + ":" + municipality, "data": js(data)})
        summary = {
            "municipality": municipality,
            "properties": stats["base_counts"][municipality],
            "assessments": stats["assessment_counts"][municipality],
            "neighbourhoods": [{"name": name, "count": count} for name, count in stats["neighbourhood_counts"][municipality].most_common()],
            "datasets": [{"title": source["title"], "url": source["url"], "records": source["records"], "kind": "municipal public data"} for source in sources if source["title"].startswith(municipality + " · ")],
            "limits": [
                "Bulk monetary assessments are unavailable in these downloaded layers; official property values are available through an on-demand lookup with no explicit assessment year.",
                "Ambiguous address candidates remain at parcel level; a parcel may include several dwellings.",
                "Zoning and housing capacity describe planning provisions, not existing homes or guaranteed development approval.",
                "Park distances are approximate straight-line distances to mapped boundaries, not walking routes.",
            ],
            "updated": datetime.now(timezone.utc).isoformat(),
        }
        if municipality == DISTRICT:
            summary["limits"].append("Building storeys are linked inventory attributes. Building height is unpopulated and no year-built value was inferred.")
        display_layers.append({"id": "municipal-summary:" + municipality, "data": js(summary)})
    stats["layer_json_bytes"] = {row["id"]: len(row["data"].encode("utf-8")) for row in display_layers}
    if max(stats["layer_json_bytes"].values()) >= 1_500_000:
        raise ValueError("A display layer exceeds the safe D1 row limit")
    stats["outputs"].append(jsonl("layers", display_layers))
    stats["outputs"].append(jsonl("permits", permits()))
    stats["outputs"].append(jsonl("sources", sources))
    stats["outputs"].append(jsonl("documents", documents(sources)))
    stats["matching"] = {k: dict(v) for k, v in stats["matching"].items()}
    stats["status"] = "complete"
    stats["completed_at"] = datetime.now(timezone.utc).isoformat()
    write(OUT / "integration-report-northvan.json", stats)
    print(json.dumps(stats, ensure_ascii=True, indent=2), flush=True)


if __name__ == "__main__":
    main()
