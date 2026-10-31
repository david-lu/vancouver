"""Archive complete selected North Vancouver public GIS and OGL bulk datasets.

Only writes data/raw/northvan-extra. Retains exact raw responses, full service
metadata, source/licence notes and checksums. Property lookup samples use an
explicit allowlist; owner and contact information is never retained.
"""
from __future__ import annotations

import csv
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import time
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET
import zipfile

ROOT = Path(__file__).resolve().parents[2] / "data/raw/northvan-extra"
CNV = "https://gisext2.cnv.org/arcgis/rest/services/"
DNV = "https://geoweb.dnv.org/arcgis/rest/services/"
LICENSES = {
    "city": {"name": "CityMap public GIS terms", "url": "https://gisext2.cnv.org/citymap/", "status": "Public official GIS; no explicit Open Government Licence found. Do not describe as OGL licensed.", "attribution": "Source: City of North Vancouver. Data supplied without accuracy or completeness warranty."},
    "district": {"name": "Open Government Licence - North Vancouver", "url": "https://geoweb.dnv.org/data/", "status": "Official GEOweb open-data licence, permits commercial reuse with attribution; excludes personal information and third-party rights.", "attribution": "Contains information licensed under the Open Government Licence - North Vancouver."},
}

SOURCES = [
    ("cnv-zoning", "city", CNV + "BaseMapServices/query_layers/MapServer/16", "zoning", {"zoning": "ZONING", "density_limit_fsr": "FSR", "permitted_height_limit": "MAX_BLDG_HT", "principal_use": "PRINCIP_USE", "bylaw": "BYLAW_NO", "adopted": "ADOPTED"}, "Zoning limits are regulations, not measured building characteristics; FSR is a source string and may need interpretation."),
    ("cnv-neighbourhoods", "city", CNV + "BaseMapServices/query_layers/MapServer/8", "neighbourhoods", {"name": "NHOOD"}, "Spatial polygon join; keep City distinct from District."),
    ("cnv-building-permits", "city", CNV + "BaseMapServices/query_layers/MapServer/25", "building-permits", {"permit_number": "Title", "description_html": "Content"}, "Published layer is not established as complete permit history. Content contains permit, address and description as HTML; sanitize before display."),
    ("cnv-buildings", "city", CNV + "BaseMapServices/query_layers/MapServer/5", "building-footprints", {"status": "BUILDING_STATUS", "name": "BUILDING_NAME", "source_link": "WEBLINK"}, "Building footprint, not floor area or number of dwellings."),
    ("cnv-legal-parcels", "city", CNV + "BaseMapServices/query_layers/MapServer/2", "parcel-addresses", {"municipal_parcel_id": "PRC_PARCEL_NO", "roll": "PRC_ROLL_NO", "address": "ADDRESS", "area_m2": "AREA_M"}, "PRC_PARCEL_NO mixes provincial PIDs and municipal IDs such as CNV-000-866. Only strict nine-digit PID-format values permit exact provincial joins; retain mixed identifiers with roll for CityMap lookup."),
    ("cnv-common-addresses", "city", CNV + "BaseMapServices/query_layers/MapServer/6", "parcel-addresses", {"municipal_parcel_id": "PRC_PARCEL_NO", "roll": "PRC_ROLL_NO", "parent_roll": "PPRC_ROLL_NO", "address": "ADDRESS", "status": "GIS_STATUS"}, "PRC_PARCEL_NO mixes provincial PIDs and municipal IDs. Strict nine-digit PID-format values permit exact joins; addresses may be one-to-many and require uniqueness checks. Never strip CNV-prefixed IDs into numeric PIDs."),
    ("cnv-ssmuh", "city", CNV + "Applications/SSMUHMap/MapServer/0", "zoning-capacity", {"pid_current": "PID_CURRENT", "pid_original": "PID_ORIGINAL", "address": "ADDRESS", "zoning": "Zoning", "permitted_units_class": "MinimumUnits", "heritage_protected": "HeritageProtected", "bus_proximity_class": "Within400mBusStop"}, "MinimumUnits is the municipal permitted-capacity category, NEVER actual existing homes. PID_CURRENT may hold more than one PID; split and validate rather than deleting separators blindly."),
    ("cnv-development-applications", "city", CNV + "Applications/DevelopmentApplications/MapServer/0", "development-applications", {"pid": "SITE_PID", "application_number": "APNO", "status": "STATUS", "description": "DESCRIPTION", "site": "SITE", "received": "Date_received", "more_info": "More_info_url"}, "Public planning application geometry and status. Applicant and contact columns are omitted from the public app export; raw API columns are retained only when not contact fields."),
    ("cnv-parks", "city", CNV + "BaseMapServices/query_layers/MapServer/4", "parks", {}, "Park polygons for proximity and map overlays; source field schema retained."),
    ("dnv-zoning", "district", DNV + "Data_DynamicLayers_Zoning/MapServer/0", "zoning", {"zoning": "Zoning"}, "This dynamic query returned all records with null geometry. Attribute archive preserved; spatial joins and display use the complete official RegZoning KML archive instead. Zoning remains a planning provision, not development approval."),
    ("dnv-ocp", "district", DNV + "Data_DynamicLayers_OCP/MapServer/0", "land-use-plan", {"ocp_class": "OCPClass"}, "Official Community Plan designation is distinct from zoning. The dynamic query returned all records with null geometry; this attribute archive is not used for spatial joins or display."),
    ("dnv-buildings", "district", DNV + "Data_CommonQueryLayers/MapServer/1", "building-attributes", {"building_id": "Asset_Id", "type": "BldgType", "primary_use": "PrimaryUse", "storeys": "NumStories", "height": "Height", "structural_class": "StructuralClass"}, "Storeys and height are source building inventory fields, never zoning limits. Validate units and null coverage; AM_Date is not assumed to be year built."),
    ("dnv-parcels", "district", DNV + "Data_CommonQueryLayers/MapServer/0", "municipal-parcel-join", {"municipal_parcel_id": "DBLink", "corner_lot": "CornerLot", "double_fronting": "DoubleFronting"}, "Join DBLink to CadLegalDescription.gislink for exact PID and BldIntermediate.DBLINK for buildings/addresses."),
    ("dnv-parks", "district", DNV + "Data_CommonQueryLayers/MapServer/3", "parks", {}, "Park geometry for proximity. Full source fields retained."),
]

BULK = [
    ("dnv-legal-descriptions", "CadLegalDescription", "CSV", "exact-pid-join", {"pid": "pid", "municipal_parcel_id": "gislink", "folio": "folio", "unit": "unit", "property_number": "propertynumber"}),
    ("dnv-addresses", "BldAddress", "CSV", "civic-addresses", {"building_id": "ASSET_ID", "address": "ADDRESS", "base_address": "BASE_ADDRESS", "unit": "UNIT", "status": "STATUS", "postal_code": "POST_CODE"}),
    ("dnv-building-parcel-links", "BldIntermediate", "CSV", "building-parcel-join", {"building_id": "ASSET_ID", "municipal_parcel_id": "DBLINK"}),
    ("dnv-neighbourhoods", "RegNeighbourhood", "KML", "neighbourhoods", {}),
    ("dnv-zoning-polygons", "RegZoning", "KML", "zoning-polygons", {}),
]


def stamp():
    return datetime.now(timezone.utc).isoformat()


def fetch(url, data=None):
    for attempt in range(5):
        try:
            with urlopen(Request(url, data=data, headers={"User-Agent": "Harbour-Housing-OpenData/1.0", **({"Content-Type": "application/x-www-form-urlencoded"} if data is not None else {})}), timeout=120) as r:
                return r.read()
        except OSError:
            if attempt == 4:
                raise
            time.sleep(min(20, 2 ** attempt))


def atomic(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".part")
    temp.write_bytes(content)
    temp.replace(path)


def save_json(path, value):
    atomic(path, (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode())


def json_get(url, **params):
    result = json.loads(fetch(url + "?" + urlencode(params)))
    if "error" in result:
        raise ValueError(f"{url}: {result['error']}")
    return result


def sha(body):
    return hashlib.sha256(body).hexdigest()


def arcgis(source):
    key, municipality, url, role, mapping, note = source
    folder = ROOT / key
    cached = folder / "receipt.json"
    if cached.exists():
        receipt = json.loads(cached.read_text(encoding="utf-8"))
        combined = ROOT / receipt["file"]
        if receipt.get("status") == "complete" and combined.exists() and sha(combined.read_bytes()) == receipt["sha256"]:
            print(f"VERIFIED CACHE {key}: {receipt['record_count']:,} records", flush=True)
            return receipt
    metadata = json_get(url, f="json")
    save_json(folder / "metadata.json", metadata)
    count = json_get(url + "/query", f="json", where="1=1", returnCountOnly="true")["count"]
    ids_result = json_get(url + "/query", f="json", where="1=1", returnIdsOnly="true")
    oid = ids_result["objectIdFieldName"]
    ids = sorted(ids_result.get("objectIds") or [])
    if count != len(ids) or count != len(set(ids)):
        raise ValueError(f"{key}: source count / unique ID mismatch")
    save_json(folder / "object-ids.json", ids_result)
    excluded = {"Applicant", "CITY_CONTACT", "MAILTO"} if key == "cnv-development-applications" else set()
    fields = [f["name"] for f in metadata["fields"] if f["type"] not in ("esriFieldTypeGeometry", "esriFieldTypeBlob") and f["name"] not in excluded]
    features = []
    pages = []
    seen = set()
    for start in range(0, count, 250):
        batch = ids[start:start + 250]
        params = {"f": "geojson", "objectIds": ",".join(map(str, batch)), "outFields": ",".join(fields), "returnGeometry": "true", "outSR": "4326"}
        query = url + "/query?" + urlencode(params)
        # ArcGIS query POST is read-only and avoids IIS URL-length limits.
        body = fetch(url + "/query", urlencode(params).encode())
        page = json.loads(body)
        if page.get("type") != "FeatureCollection":
            raise ValueError(f"{key}: not a GeoJSON feature collection: {page.get('error')}")
        found = page.get("features", [])
        got_ids = [int(f["properties"][oid]) for f in found]
        if len(found) != len(batch) or set(got_ids) != set(batch) or seen.intersection(got_ids):
            raise ValueError(f"{key}: truncated, duplicate, or mismatched query page at {start}")
        seen.update(got_ids)
        filename = f"page-{start:06d}.geojson"
        atomic(folder / filename, body)
        pages.append({"file": filename, "count": len(found), "sha256": sha(body), "url": query, "request_method": "POST"})
        features.extend(found)
    after = json_get(url + "/query", f="json", where="1=1", returnCountOnly="true")["count"]
    if after != count or len(features) != count or len(seen) != count:
        raise ValueError(f"{key}: final source count mismatch")
    combined = {"type": "FeatureCollection", "features": features}
    combined_bytes = json.dumps(combined, ensure_ascii=False, separators=(",", ":")).encode()
    atomic(folder / "data.geojson", combined_bytes)
    record = {"id": key, "status": "complete", "municipality": "North Vancouver City" if municipality == "city" else "North Vancouver District", "role": role, "source_url": url, "downloaded_at": stamp(), "record_count": count, "unique_ids": len(seen), "file": str((folder / "data.geojson").relative_to(ROOT)), "sha256": sha(combined_bytes), "bytes": len(combined_bytes), "source_fields": metadata["fields"], "field_mapping": mapping, "excluded_fields": sorted(excluded), "note": note, "license": LICENSES[municipality], "pages": pages, "null_geometry_count": sum(f.get("geometry") is None for f in features)}
    # Population of fields matters: do not present mostly-null building attributes as complete.
    record["field_non_null_counts"] = {field: sum(f["properties"].get(field) not in (None, "", " ") for f in features) for field in mapping.values()}
    save_json(folder / "receipt.json", record)
    print(f"COMPLETE {key}: {count:,} records", flush=True)
    return record


def kml_features(payload):
    ns = {"k": "http://www.opengis.net/kml/2.2"}
    root = ET.fromstring(payload)
    result = []
    for placemark in root.findall(".//k:Placemark", ns):
        props = {"name": placemark.findtext("k:name", default="", namespaces=ns), "description_html": placemark.findtext("k:description", default="", namespaces=ns)}
        for data in placemark.findall(".//k:SimpleData", ns):
            props[data.attrib.get("name", "")] = data.text
        for data in placemark.findall(".//k:Data", ns):
            props[data.attrib.get("name", "")] = data.findtext("k:value", namespaces=ns)
        polygons = []
        for polygon in placemark.findall(".//k:Polygon", ns):
            rings = []
            for ring in polygon.findall("k:outerBoundaryIs/k:LinearRing/k:coordinates", ns) + polygon.findall("k:innerBoundaryIs/k:LinearRing/k:coordinates", ns):
                rings.append([[float(n) for n in point.split(",")[:2]] for point in (ring.text or "").split()])
            if rings:
                polygons.append(rings)
        if polygons:
            geometry = {"type": "Polygon", "coordinates": polygons[0]} if len(polygons) == 1 else {"type": "MultiPolygon", "coordinates": polygons}
            result.append({"type": "Feature", "properties": props, "geometry": geometry})
    return result


def bulk(source):
    key, dataset, fmt, role, mapping = source
    folder = ROOT / key
    url = f"https://geoweb.dnv.org/Products/Data/{fmt}/{dataset}_{fmt.lower()}.zip"
    body = fetch(url)
    atomic(folder / "source.zip", body)
    z = zipfile.ZipFile(io.BytesIO(body))
    archive_entries = z.namelist()
    fields = []
    if fmt == "CSV":
        entry = next(n for n in archive_entries if n.lower().endswith(".csv"))
        raw = z.read(entry)
        atomic(folder / "data.csv", raw)
        rows = list(csv.DictReader(io.StringIO(raw.decode("utf-8-sig", errors="replace"))))
        count = len(rows)
        fields = list(rows[0]) if rows else []
        filename = "data.csv"
    else:
        features = []
        for name in archive_entries:
            if name.lower().endswith(".kml"):
                features.extend(kml_features(z.read(name)))
            elif name.lower().endswith(".kmz"):
                nested = zipfile.ZipFile(io.BytesIO(z.read(name)))
                for child in nested.namelist():
                    if child.lower().endswith(".kml"):
                        features.extend(kml_features(nested.read(child)))
        if not features:
            raise ValueError(f"{key}: KML archive contained no polygon features")
        save_json(folder / "data.geojson", {"type": "FeatureCollection", "features": features})
        count = len(features)
        fields = list(features[0]["properties"])
        filename = "data.geojson"
    metadata_url = f"https://geoweb.dnv.org/data/metadata.php?dataset={dataset}"
    atomic(folder / "metadata.html", fetch(metadata_url))
    for name in archive_entries:
        if "terms" in name.lower() and name.lower().endswith(".txt"):
            atomic(folder / "licence.txt", z.read(name))
    record = {"id": key, "status": "complete", "municipality": "North Vancouver District", "role": role, "source_url": url, "metadata_url": metadata_url, "downloaded_at": stamp(), "record_count": count, "file": str((folder / filename).relative_to(ROOT)), "source_archive": str((folder / "source.zip").relative_to(ROOT)), "archive_sha256": sha(body), "archive_bytes": len(body), "source_fields": fields, "field_mapping": mapping, "license": LICENSES["district"], "archive_entries": archive_entries, "note": "Complete publisher-provided ZIP retained; extracted data row count verified. Address STATUS may include inactive addresses." if fmt == "CSV" else "Complete publisher KML archive retained; polygons converted to WGS84 GeoJSON without reprojection."}
    save_json(folder / "receipt.json", record)
    print(f"COMPLETE {key}: {count:,} records", flush=True)
    return record


def property_samples():
    url = "https://geoweb.dnv.org/properties/api/properties/propertyservice/?dblink=N-3933-4"
    raw = json.loads(fetch(url))
    sample = raw[0]["properties"][0]
    details = sample["details"]
    selected = {
        "ltopid": sample.get("ltopid"), "dbLink": sample.get("dbLink"),
        "addresses": [{k: address.get(k) for k in ["fullAddress", "house", "street", "unit", "postCode"]} for address in sample.get("addresses", [])],
        "assessment": {k: details.get("assessment", {}).get(k) for k in ["buildingValue", "landValue", "totalValue", "taxConsolidation"]},
        "building": {k: details.get("building", {}).get(k) for k in ["yearBuilt", "type", "subtype", "heritageRegistry", "heritageLegallyProtected"]},
        "geography": {k: details.get("geography", {}).get(k) for k in ["area", "latitude", "longitude", "elevation"]},
        "spatialRelations": {k: details.get("spatialRelations", {}).get(k) for k in ["neighbourhood", "zonings", "elementarySchool", "secondarySchool"]},
    }
    pid_url = "https://geoweb.dnv.org/properties/api/properties/propertyservice/?" + urlencode({"dblink": "null", "ltopid": selected["ltopid"]})
    pid_result = json.loads(fetch(pid_url))
    matching_pid = any(p.get("ltopid") == selected["ltopid"] for group in pid_result for p in group.get("properties", []))
    if not matching_pid:
        raise ValueError("DNV exact-PID lookup did not return the requested PID")
    result = {"district": {"verified_url": url, "per_pid_verified_url": pid_url, "per_pid_match_verified": matching_pid, "per_pid_url_template": "https://geoweb.dnv.org/properties/api/properties/propertyservice/?dblink=null&ltopid={PID}", "downloaded_at": stamp(), "sample": selected, "assessment_year": None, "notes": ["Official public Property Viewer API; one property sample verified by DBLink and formatted PID, not a bulk assessment download.", "No explicit assessment year in the tested assessment object; display source retrieval date separately.", "Tax-consolidated values may cover more than one legal property; never duplicate as separate unit values.", "GEOweb OGL excludes third-party rights. The public app's assessment display does not establish a separate bulk BC Assessment redistribution licence.", "API geography.area has not been validated as lot or floor area; do not substitute it for either without source definition.", "Allowlisted property fields only; owners and contacts were not retained."]}}
    # This endpoint is called by CityMap itself and accepts its municipal parcel ID.
    city_url = "https://gisext2.cnv.org/cnvrest/propertyReport/roll/981979.000/pid/CNV-000-866"
    try:
        city = json.loads(fetch(city_url))
        allowed = ["pid", "roll", "address", "lotAreaFt", "lotAreaM", "valuationLand", "valuationImprovement", "valuationYear", "yearBuilt", "zone", "zoning", "neighbourhood"]
        city_sample = {k: city[k] for k in allowed if k in city}
        # Inspect field names only for documentation; no owner/contact values retained.
        safe_fields = [k for k in city if not any(s in k.lower() for s in ["owner", "occup", "phone", "email", "contact", "mail", "business"])]
        addresses = [{k: a.get(k) for k in ["address", "addressPid", "addressRoll"]} for a in city.get("addresses", [])]
        city_sample["addresses"] = addresses
        detail_url = None
        detail = {}
        if addresses:
            first = addresses[0]
            detail_url = "https://gisext2.cnv.org/cnvrest/property/roll/" + quote(str(first["addressRoll"])) + "/pid/" + quote(str(first["addressPid"]))
            detail_raw = json.loads(fetch(detail_url))
            detail = {k: detail_raw.get(k) for k in ["pid", "roll", "valuationLand", "valuationImprovement", "zoning", "neighbourhood"]}
            if detail["pid"] != first["addressPid"]:
                raise ValueError("CNV unit assessment lookup did not return the requested PID")
        result["city"] = {"verified_url": city_url, "url_template": "https://gisext2.cnv.org/cnvrest/propertyReport/roll/{PRC_ROLL_NO}/pid/{PRC_PARCEL_NO}", "assessment_url_template": "https://gisext2.cnv.org/cnvrest/property/roll/{addressRoll}/pid/{addressPid}", "assessment_verified_url": detail_url, "assessment_year": None, "source_fields_without_contacts": safe_fields, "sample": city_sample, "unit_assessment_sample": detail, "notes": ["First resolve municipal parcel/roll to addresses[].addressPid and addresses[].addressRoll; then request the selected exact PID's property endpoint.", "PRC_PARCEL_NO is the municipal parcel identifier, not necessarily a nine-digit provincial PID.", "valuationLand and valuationImprovement are monetary assessment fields. No assessment year was present in the verified response.", "Keep each unit's valuation attached only to the selected exact PID.", "Public CityMap terms do not provide an explicit bulk assessment reuse licence.", "Sample stores only allowlisted property fields; no owners or contacts."]}
    except Exception as error:
        result["city"] = {"url_template": city_url, "status": "lookup_failed", "error": str(error)}
    save_json(ROOT / "property-lookup-evidence.json", result)
    return result


def main():
    ROOT.mkdir(parents=True, exist_ok=True)
    report = {"status": "downloading", "started_at": stamp(), "datasets": [], "errors": [], "join_instructions": ["DNV exact PID → dnv-legal-descriptions.pid; gislink → dnv-building-parcel-links.DBLINK; ASSET_ID → dnv-addresses.ASSET_ID and dnv-buildings.Asset_Id.", "DNV legal and building joins are one-to-many. Do not attach every unit to every PID solely because the building is shared; use unit and address evidence.", "CNV PRC_PARCEL_NO mixes exact provincial PIDs and municipal CNV-prefixed IDs. Validate nine-digit PID format before exact joining; preserve PRC_ROLL_NO for unit assessment lookup. Address matches can remain one-to-many.", "Zoning maximum height, FSR and permitted unit capacity must remain separate from observed building height, floor area and existing dwellings."]}
    save_json(ROOT / "report.json", report)
    jobs = [(arcgis, source) for source in SOURCES] + [(bulk, source) for source in BULK]
    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = {executor.submit(fn, source): source[0] for fn, source in jobs}
        for future in as_completed(futures):
            try:
                receipt = future.result()
                report["datasets"].append({k: v for k, v in receipt.items() if k not in ["pages", "source_fields"]})
            except Exception as error:
                record = {"id": futures[future], "error": str(error)}
                report["errors"].append(record)
                print("FAILED " + json.dumps(record), flush=True)
            save_json(ROOT / "report.json", report)
    report["property_lookup"] = property_samples()
    report["completed_at"] = stamp()
    report["status"] = "complete" if not report["errors"] else "partial"
    report["datasets"].sort(key=lambda x: x["id"])
    report["totals"] = {"datasets": len(report["datasets"]), "source_records": sum(x["record_count"] for x in report["datasets"])}
    report["geometry_note"] = "DNV dynamic zoning and OCP queries returned null geometry when verified. Use the complete official RegZoning KML archive for zoning display and spatial joins. Original attribute archives remain intact."
    save_json(ROOT / "report.json", report)
    print(json.dumps({"status": report["status"], "datasets": len(report["datasets"]), "errors": report["errors"]}), flush=True)
    if report["errors"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
