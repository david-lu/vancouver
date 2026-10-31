"""Optional neighbouring Coquitlam public property/context data asset download."""
from __future__ import annotations

import surrey_download as download


def main():
    download.OUT = download.OUT / "coquitlam"
    download.OUT.mkdir(parents=True, exist_ok=True)
    download.PREFIX = "coquitlam"
    download.PORTAL = "https://data.coquitlam.ca"
    download.LICENCE = "Open Government Licence - British Columbia (version 2.0), adopted by Coquitlam"
    download.LICENCE_URL = "https://www.coquitlam.ca/894/Open-Government-Licence"
    download.LICENCE_EVIDENCE = "https://data.coquitlam.ca/api/feed/dcat-us/1.1.json"
    download.ATTRIBUTION = "Contains information licensed under the Open Government Licence – British Columbia."
    for name, url in [("licence.html", download.LICENCE_URL), ("catalogue.json", download.LICENCE_EVIDENCE)]:
        data, _ = download.fetch(url)
        (download.OUT / name).write_bytes(data)
    base = "https://services2.arcgis.com/Q6Lq3evZUGfPrN7o/arcgis/rest/services"
    sources = [
        ("property-information", "Coquitlam Property Information", base + "/Cadastral/FeatureServer/16", "c72d39364d8b4e3796dc90b193a2f9b6"),
        ("zoning", "Coquitlam Zoning", base + "/Planning_and_Development/FeatureServer/10", "5715436c37b346ef8bf75cc9116aefd2"),
        ("neighbourhood-plans", "Coquitlam Neighbourhood Plans", base + "/Planning_and_Development/FeatureServer/0", "5715436c37b346ef8bf75cc9116aefd2"),
    ]
    receipts = []
    for source in sources:
        receipts.append(download.download(source))
        download.save_json(download.OUT / "report.json", {"status": "downloading", "datasets": receipts})
    download.save_json(download.OUT / "report.json", {"status": "complete", "updated_at": download.now(),
        "datasets": receipts, "records": sum(r["records"] for r in receipts),
        "notes": ["Official property-to-unit/address/PID polygon associations; no valuation fields.",
                  "Neighbourhood plans are planning areas, not comprehensive city-wide neighbourhood coverage."]})
    print("COQUITLAM_COMPLETE", sum(r["records"] for r in receipts), len(receipts), flush=True)


if __name__ == "__main__":
    main()
