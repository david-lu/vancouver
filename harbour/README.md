# Harbour

Greater Vancouver property research: a custom map, simultaneous assessment labels, comparison of six properties, and a source-grounded assistant.

## Implemented

- More than **one million** property/parcel records across all **23** regional jurisdictions.
- **1,198,015** civic/unit address locations. Address suggestions locate nearby parcels without inventing unit-to-PID matches.
- **4,255,337** original Vancouver assessment rows since 2006; **228,999** latest Vancouver records and **53,100** Langley Township assessment records.
- **52,533** Vancouver permits, 22 neighbourhoods, regional boundaries, parks, schools, zoning, heritage and designated floodplain layers. Municipal expansions add Burnaby, Surrey, both North Vancouvers and Coquitlam zoning, neighbourhoods, parks and property context.
- Property details, assessment history charts/CSV, nearby permits, parcel outlines, aerial imagery, Street View links, saved homes, private notes and saved searches.
- Canadian mortgage and ownership-cost planner with editable assumptions and general BC transfer tax.
- Hybrid RAG: SQL property facts, FTS5 addresses/permits/documents, small cached embeddings for guidance, and exact original assessment records in R2. Answers include source citations and explicit map actions.
- Complete original downloads, provenance receipts, checksums, repeatable preparation and a hosted downloadable raw archive.

These are property records, **not homes for sale**. Parcels include strata, commercial, public and vacant land. Assessments are not asking prices or appraisals. Bulk monetary sources cover Vancouver, Langley Township, Burnaby and Surrey. Exact-PID public North Vancouver assessments can be checked when a property opens. Burnaby and North Vancouver sources do not state assessment years; missing years stay null. Regional parcel coverage does not imply equivalent assessment, permit or zoning publication by every municipality.

## Local setup

Requires Node 22.13+, Python 3.11+, Git, and a modern WebGL browser.

```powershell
npm ci
python -m pip install -r requirements.txt
npm run build
```

Apply every **pending** SQL migration in `drizzle/` once, in filename order. Example:

```powershell
node --import ./scripts/sites-env.mjs ./node_modules/wrangler/bin/wrangler.js d1 execute DB --local --config dist/server/wrangler.json --persist-to .wrangler/state --file drizzle/0000_married_sally_floyd.sql
```

Repeat for pending `0001` through `0004` files. Do not replay applied migrations. Hosted D1 is separate; Sites applies hosted migrations at publication.

Create ignored `.dev.vars` with `OPENAI_API_KEY`, `OPENAI_MODEL=gpt-4.1-mini`, and a long random `INGEST_TOKEN`. Optional licensed-feed keys: `DDF_CLIENT_ID`, `DDF_CLIENT_SECRET`. Keys stay server-side. No Gemini/FAL/Comfy calls are needed.

```powershell
python pipeline/load_local.py
npm run dev -- --port 5188
```

Open `http://127.0.0.1:5188`. No login is required. A signed, HttpOnly browser cookie scopes saved properties, notes and searches; existing Sites identity can preserve earlier research when already present. The assistant uses the server-side API key. The retained framework documentation is in `docs/STARTER.md`.

## Data pipeline

The data root is the checkout's sibling `../data/`. Bulk data and secrets never enter source control.

| Path | Contents |
| --- | --- |
| `data/raw/` | Complete original downloads, metadata, hashes and assessment shards |
| `data/harbour.sqlite` | Full Vancouver assessment archive and preparation tables |
| `data/regional.sqlite` | 4,797,849 preserved raw regional records and spatial joins |
| `data/prepared/*.jsonl` | Normalized import streams, sources, layers, addresses, documents |
| `data/hosted-import.json` | Database upload progress without credentials |
| `.wrangler/state/` | Local app database/object storage |

```powershell
python pipeline/download.py
python pipeline/download_nar.py
python pipeline/download_langley.py
python pipeline/download_pmbc.py
python pipeline/prepare.py
python pipeline/prepare_regional.py
python pipeline/prepare_regional.py --export-addresses-only
python pipeline/archive_history.py
python pipeline/knowledge.py
python pipeline/knowledge_records.py
python pipeline/coverage_knowledge.py
python pipeline/load_local.py
```

Downloaders retain complete snapshots. PMBC verifies all 212 pages, hashes, ordered unique IDs and the live source count. NAR retrieves and CRC-checks complete BC ZIP members using byte ranges. Completed downloads are cached. To refresh, archive the old snapshot under a dated directory and rerun; do not mix pages from different snapshots. Stop the local server before a large direct SQLite import.

Spatial extraction uses Shapely. Source values remain unchanged and missing amounts remain null. Regional civic labels require one unambiguous address site inside a parcel. Other parcels display PID. Assessment PIDs suppress duplicate unvalued PMBC entities. NAR unit lookup is independent of parcel valuation. Municipalities use official spatial boundaries; 48 out-of-boundary PMBC entities remain in the raw archive.

Only the small guidance corpus is embedded once, at 256 dimensions. Bulk facts use indexes. The assistant has bounded tool rounds and a 50-question daily allowance per browser workspace, a 200-question network allowance, a 10-request network minute limit and a 500-question shared daily cap. Network quota keys are HMAC hashes; raw IP addresses are not stored. Direct public knowledge searches use free lexical retrieval; semantic embedding calls run only within the bounded assistant.

## Hosting and import

Preserve the registered Site ID in `.openai/hosting.json`. Use the Sites skill's source/build/package workflow to push the exact source commit and deploy its matching artifact. Runtime secrets and bulk datasets are excluded from that artifact.

The site is public and does not require ChatGPT sign-in. Administrative imports still require the ingest token. The bypass field below is optional for a public site. After publication, run `node pipeline/publish_data.mjs`. It reads one **hidden** JSON line from stdin:

```json
{"url":"https://YOUR-SITE.chatgpt.site","bypass":"SITE-DISPATCH-BEARER"}
```

The separate ingest token comes from `.dev.vars`, or `ingestToken` in that hidden JSON. Never pass credentials in shell arguments, history, client code or committed files. The script resumes idempotent database imports and raw uploads, checks object size/hash metadata, and publishes an archive manifest. Optional flags: `--rows-only`, `--raw-only`. Changed source files are reimported.

## Live listings

No licensed DDF credentials were supplied. The site accurately reports the disconnected feed and links to current listings on REW. It never fabricates listings or scrapes REW/REALTOR pages.

`pipeline/ddf_sync.py` implements CREA OAuth and complete OData pagination, archives returned pages, and creates normalized listing records. Brokerage-authorized credentials and feed-specific display/media compliance are needed before enabling in-app listing display. This adapter is not an active connection; public assessments cannot replace an MLS licence.

## Verification

```powershell
node node_modules/typescript/bin/tsc --noEmit
node tests/browser.mjs
node tests/regional.mjs
node tests/no-login.mjs
node tests/municipal.mjs
```

Playwright uses installed Chrome. Checks cover map rendering, comparison, assessment history, value filters, persistence, a real cited assistant answer, mobile layout, municipal coverage, address search and API authorization. Preparation verifies complete source counts, schema, coordinates, unique identifiers, valuation arithmetic and SQLite integrity. Screenshots are in ignored `outputs/`.

## Sources and limits

Sources: Vancouver Open Data, Township of Langley, ParcelMap BC, Statistics Canada NAR, Metro Vancouver municipal boundaries, BC government, BCFSA and CMHC. Receipts retain exact URLs, retrieval times and hashes. Each source's licence applies. MapLibre's BSD licence is bundled in `public/maplibre-LICENSE.txt`; street tiles credit OpenStreetMap, imagery credits Esri and contributors.

School distances are straight-line, not catchments. Nearby permits/heritage can belong to adjacent parcels or other units. Floodplain flags are geographic screening only. The calculator excludes exemptions, GST, additional foreign-buyer tax, utilities and closing fees; its rate is an assumption, never a live quote. Consult current official records before decisions.

## Municipal expansion

The default screen keeps the map and assistant visible, with property results and secondary tools opened on demand. Municipal preparation scripts preserve complete raw downloads and source metadata, validate PID joins, and retain existing property IDs. Surrey repeated shared assessments are consolidated rather than represented as separate unit values. Coquitlam adds official address, zoning and neighbourhood-plan context without inventing assessments.

Use `pipeline/load_local.py --only` for the prepared municipal files; then `pipeline/finalize_municipal.py` reconciles coverage from actual database counts. The publisher supports `--rows-only --municipal --city=burnaby,surrey,northvan,coquitlam,municipal`; omit the city filter for all prepared municipal assets. Per-source integration reports record counts, match limitations and checksums. The complete raw archive stays unsimplified; map display polygons may be simplified.
