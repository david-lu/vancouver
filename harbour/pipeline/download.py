"""Archive complete official exports, metadata and checksums. Safe to resume."""
import concurrent.futures, csv, hashlib, json, time, urllib.request
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / 'data' / 'raw'
BASE = 'https://opendata.vancouver.ca/api/explore/v2.1/catalog/datasets/'
DATASETS = {
 'property-tax-report': 'csv',
 'property-tax-report-2016-2019': 'csv',
 'property-tax-report-2011-2015': 'csv',
 'property-tax-report-2006-2010': 'csv',
 'issued-building-permits': 'csv',
 'property-parcel-polygons': 'geojson',
 'property-addresses': 'json',
 'local-area-boundary': 'geojson',
 'parks': 'json',
 'schools': 'json',
 'zoning-districts-and-labels': 'geojson',
 'heritage-sites': 'json',
 'designated-floodplain': 'geojson',
 'rental-standards-current-issues': 'json',
}
def request(url):
 return urllib.request.urlopen(urllib.request.Request(url,headers={'User-Agent':'HarbourVancouver/1.0 (public-data research)'}),timeout=240)
def download(item):
 name,fmt=item; path=RAW / f'{name}.{fmt}'; receipt=RAW / f'{name}.receipt.json'
 if path.exists() and receipt.exists():
  value=json.loads(receipt.read_text()); print('CACHED',name,value['bytes'],flush=True); return value
 url=BASE+name+'/exports/'+fmt+'?limit=-1'+('&delimiter=%2C' if fmt=='csv' else '')
 for attempt in range(4):
  try:
   with request(BASE+name) as r: metadata=json.load(r)
   (RAW/f'{name}.metadata.json').write_text(json.dumps(metadata,ensure_ascii=False),encoding='utf8')
   print('DOWNLOADING',name,flush=True)
   h=hashlib.sha256(); size=0
   with request(url) as response, path.with_suffix(path.suffix+'.part').open('wb') as out:
    while block:=response.read(1024*1024): out.write(block); h.update(block); size+=len(block)
   path.with_suffix(path.suffix+'.part').replace(path)
   value={'id':name,'url':url,'path':str(path.relative_to(ROOT)),'format':fmt,'bytes':size,'sha256':h.hexdigest(),'downloaded_at':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),'expected_records':metadata.get('metas',{}).get('default',{}).get('records_count'),'licence':'Open Government Licence – Vancouver','status':'downloaded'}
   receipt.write_text(json.dumps(value,indent=2),encoding='utf8'); print('COMPLETE',name,size,flush=True); return value
  except Exception as e:
   print('RETRY',name,attempt+1,str(e),flush=True); time.sleep(2*(attempt+1))
 return {'id':name,'url':url,'status':'error','error':str(e) if 'e' in locals() else 'Download failed'}
if __name__=='__main__':
 RAW.mkdir(parents=True,exist_ok=True)
 with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool: results=list(pool.map(download,DATASETS.items()))
 (ROOT/'data'/'manifest.json').write_text(json.dumps(results,indent=2),encoding='utf8')
 print('ARCHIVE',sum(x.get('bytes',0) for x in results),'bytes',sum(x['status']=='downloaded' for x in results),'datasets',flush=True)
 if any(x['status']=='error' for x in results): raise SystemExit(1)
