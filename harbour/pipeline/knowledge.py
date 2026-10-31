"""Archive official guidance, chunk it, and embed the small text corpus once.
Structured property/permit records use indexed retrieval; no per-row LLM calls.
"""
import concurrent.futures, hashlib, html.parser, json, os, re, time, urllib.request
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];RAW=ROOT/'data'/'raw'/'guidance';OUT=ROOT/'data'/'prepared'
SOURCES=[
 ('bc-transfer-tax','BC property transfer tax','https://www2.gov.bc.ca/gov/content/taxes/property-taxes/property-transfer-tax'),
 ('bc-first-time','First-time home buyers exemption','https://www2.gov.bc.ca/gov/content/taxes/property-taxes/property-transfer-tax/exemptions/first-time-home-buyers/current-amount'),
 ('bc-strata','Strata housing ownership','https://www2.gov.bc.ca/gov/content/housing-tenancy/strata-housing'),
 ('bcfsa-form-b','Strata Form B information certificate','https://www.bcfsa.ca/industry-resources/real-estate-professional-resources/knowledge-base/practice-resources/form-b-information-certificate'),
 ('bcfsa-rescission','Home buyer rescission period','https://www.bcfsa.ca/public-resources/real-estate/home-buyer-rescission-period'),
 ('vancouver-permits','Vancouver building permits','https://vancouver.ca/home-property-development/building-permit.aspx'),
 ('vancouver-property-info','Vancouver property information','https://vancouver.ca/home-property-development/find-property-information.aspx'),
 ('vancouver-property-research','Property research and permit copies','https://vancouver.ca/home-property-development/request-property-research-and-copies-of-permits.aspx'),
 ('cmhc-down-payment','CMHC mortgage insurance explained','https://www.cmhc-schl.gc.ca/observer/2025/cmhc-mortgage-loan-insurance-explained'),
 ('cmhc-premiums','CMHC mortgage insurance premiums','https://www.cmhc-schl.gc.ca/professionals/project-funding-and-mortgage-financing/mortgage-loan-insurance/mortgage-loan-insurance-homeownership-programs/premium-information-for-homeowner-and-small-rental-loans'),
]
def keys():
 result=dict(os.environ)
 for p in [ROOT/'.env',ROOT/'harbour'/'.dev.vars']:
  if p.exists():
   for line in p.read_text().splitlines():
    if '=' in line and not line.strip().startswith('#'):
     k,v=line.split('=',1);result[k.strip()]=v.strip().strip('"').strip("'")
 return result
class Text(html.parser.HTMLParser):
 def __init__(self):super().__init__();self.parts=[];self.hidden=0;self.main=0;self.mainparts=[]
 def handle_starttag(self,tag,attrs):
  if tag in ['script','style','nav','header','footer']:self.hidden+=1
  if tag in ['main','article']:self.main+=1
 def handle_endtag(self,tag):
  if tag in ['script','style','nav','header','footer']:self.hidden=max(0,self.hidden-1)
  if tag in ['main','article']:self.main=max(0,self.main-1)
 def handle_data(self,data):
  s=re.sub(r'\s+',' ',data).strip()
  if s and not self.hidden:
   self.parts.append(s)
   if self.main:self.mainparts.append(s)
 def text(self):return '\n'.join(self.mainparts if len(''.join(self.mainparts))>500 else self.parts)
def get(source):
 id,title,url=source;p=RAW/(id+'.html')
 try:
  if not p.exists():p.write_bytes(urllib.request.urlopen(urllib.request.Request(url,headers={'User-Agent':'Mozilla/5.0 Harbour public property research'}),timeout=60).read())
  b=p.read_bytes();parser=Text();parser.feed(b.decode('utf8',errors='replace'));text=parser.text()
  if len(text)<300:raise ValueError('No useful page text')
  receipt={'id':id,'title':title,'url':url,'downloaded':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),'bytes':len(b),'sha256':hashlib.sha256(b).hexdigest(),'status':'verified','records':None,'detail':'Official guidance archived and indexed for source-grounded retrieval.'};(RAW/(id+'.receipt.json')).write_text(json.dumps(receipt,indent=2),encoding='utf8')
  chunks=[]
  for i,start in enumerate(range(0,len(text),3200)):
   chunks.append({'id':id+':'+str(i),'title':title,'content':text[start:start+3800],'url':url,'source':id,'embedding':None,'updated':receipt['downloaded']})
  print('GUIDANCE',id,len(chunks),'chunks',flush=True);return chunks,receipt
 except Exception as e:print('GUIDANCE_UNAVAILABLE',id,type(e).__name__,flush=True);return [],{'id':id,'title':title,'url':url,'status':'unavailable','detail':'Source could not be retrieved in this run; original link retained.'}
def main():
 RAW.mkdir(parents=True,exist_ok=True);OUT.mkdir(parents=True,exist_ok=True)
 docs=[];sources=[]
 with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
  for chunks,receipt in pool.map(get,SOURCES):docs.extend(chunks);sources.append(receipt)
 if (OUT/'stats.json').exists():
  stats=json.loads((OUT/'stats.json').read_text())
  for area in stats['neighbourhoods']:docs.append({'id':'area:'+area['name'],'title':area['name']+' assessment profile','content':f"{area['name']} has {area['count']} current property records with an assessed value. The median assessed value across all legal property types in the downloaded {stats['year']} Vancouver dataset is CAD {area['median']}. This is not a median sale price. Properties include land and strata legal types; mixed property types limit cross-neighbourhood comparability. Snapshot: {stats['updated']}.",'url':'https://opendata.vancouver.ca/explore/dataset/property-tax-report/','source':'property-tax-report','embedding':None,'updated':stats['updated']})
 docs.append({'id':'methodology','title':'Harbour property data methodology','content':'Harbour archives full Vancouver assessment and tax exports for 2006 onward, issued building permits, parcel boundaries, addresses, local area boundaries, zoning districts, parks, schools, heritage, designated floodplain and rental standards issues. Vancouver assessment history is joined by folio. Current addresses match land coordinate to pcoord, then civic number and street. Parcels join by tax coordinate. Nearby parks and schools are geometric straight-line distances, not walking distances or catchment eligibility. Permit matches are approximate within a small coordinate box and must be checked by original address. Floodplain checks use point-in-polygon published designated areas. Heritage within 25m is only a nearby register flag. Provincial PMBC provides regional parcel geometry and PIDs; NAR provides independently collected civic addresses. Spatial address joins are approximate and never establish a strata unit. Langley Township 2026 values are land plus building assessment. Missing values remain null. Beds, baths, floor area, sale prices and active listings cannot be inferred from public assessment records. Licensed CREA DDF credentials are required for current MLS listing data. Saved searches are manual filters, not email alerts.','url':'https://opendata.vancouver.ca/pages/licence/','source':'methodology','embedding':None,'updated':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())})
 key=keys().get('OPENAI_API_KEY')
 cache_path=OUT/'embedding-cache.json';cache=json.loads(cache_path.read_text()) if cache_path.exists() else {}
 missing=[]
 for d in docs:
  h=hashlib.sha256(d['content'].encode()).hexdigest()
  if h in cache:d['embedding']=json.dumps(cache[h],separators=(',',':'))
  else:missing.append(d)
 if key:
  for start in range(0,len(missing),32):
   batch=missing[start:start+32]
   req=urllib.request.Request('https://api.openai.com/v1/embeddings',data=json.dumps({'model':'text-embedding-3-small','dimensions':256,'input':[d['content'] for d in batch]}).encode(),headers={'Authorization':'Bearer '+key,'Content-Type':'application/json'})
   try:
    data=json.load(urllib.request.urlopen(req,timeout=60))
    for item in data['data']:
     d=batch[item['index']];d['embedding']=json.dumps(item['embedding'],separators=(',',':'));cache[hashlib.sha256(d['content'].encode()).hexdigest()]=item['embedding']
    cache_path.write_text(json.dumps(cache),encoding='utf8');print('EMBEDDED',len(batch),flush=True)
   except Exception as e:print('EMBEDDING_FAILED',type(e).__name__,'lexical retrieval remains available',flush=True)
 for name,rows in [('documents',docs),('guidance-sources',sources)]:
  with (OUT/(name+'.jsonl')).open('w',encoding='utf8') as f:
   for d in rows:f.write(json.dumps(d,ensure_ascii=False,separators=(',',':'))+'\n')
 print('KNOWLEDGE_READY',len(docs),'chunks',sum(bool(d['embedding']) for d in docs),'embedded',flush=True)
if __name__=='__main__':main()
