"""Join official Coquitlam zoning/plan polygons and unambiguous PID addresses."""
import collections, json, re, sqlite3, time
from pathlib import Path
from shapely.geometry import shape, Point, mapping
from shapely.strtree import STRtree
import shapely

ROOT=Path(__file__).resolve().parents[2]
RAW=ROOT/'data/raw/surrey-extra/coquitlam';OUT=ROOT/'data/prepared'
NOW=time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())
def js(x):return json.dumps(x,ensure_ascii=False,separators=(',',':'),allow_nan=False)
def read(p):return json.loads(p.read_text(encoding='utf-8-sig'))
def pid(v):
 s=re.sub(r'\D','',str(v or ''));return s if len(s)==9 and s!='000000000' else None
def dataset(name):return read(RAW/name/(name+'.geojson'))['features'],read(RAW/name/'receipt.json')
def index(features):
 keep=[f for f in features if f.get('geometry')];geoms=[shapely.make_valid(shape(f['geometry'])) for f in keep]
 return keep,geoms,STRtree(geoms)
def containing(idx,point):
 records,geoms,tree=idx;hits=tree.query(point,predicate='intersects')
 return records[min(hits,key=lambda i:geoms[i].area)] if len(hits) else None

def main():
 props,prop_receipt=dataset('property-information');zones,zone_receipt=dataset('zoning');plans,plan_receipt=dataset('neighbourhood-plans')
 address_map=collections.defaultdict(dict)
 for f in props:
  a=f['properties'];p=pid(a.get('PID'))
  if p and a.get('ADDRESS'):address_map[p][str(a['ADDRESS']).strip().upper()]=a
 zidx=index(zones);nidx=index(plans)
 base=sqlite3.connect(ROOT/'data/regional.sqlite');counts=collections.Counter();addresses=0;total=0;zone_count=0
 with (OUT/'properties-coquitlam.jsonl').open('w',encoding='utf-8') as target:
  for (record,) in base.execute("SELECT record FROM properties WHERE municipality='Coquitlam'"):
   row=json.loads(record);extras=json.loads(row['features']) if isinstance(row['features'],str) else row['features'];extras=extras or {};facts=[];sources=[]
   matches=address_map.get(pid(row.get('pid')),{});unique=list(matches.values())
   if len(unique)==1:
    a=unique[0];row['address']=a['ADDRESS'];row['street']=' '.join(str(a[k]) for k in ['PROPHOUSE','PROPSTREET','PROPSTREETTYPE'] if a.get(k));addresses+=1
    extras['municipal_address_match']='Exact provincial PID; one distinct official address';extras['postal_code']=a.get('PROPPOSTAL');extras['source_url']=prop_receipt['url'];extras['source_objectid']=a.get('OBJECTID')
   if row.get('lon') is not None and row.get('lat') is not None:
    point=Point(row['lon'],row['lat']);zone=containing(zidx,point);plan=containing(nidx,point)
    if zone:
     a=zone['properties'];row['zone']=a['ZONING_CODE'];zone_count+=1
     facts.append({'label':'Mapped zoning','value':a['ZONING_CODE']+(' — '+a['ZONING_DESCRIPTION'] if a.get('ZONING_DESCRIPTION') else ''),'url':zone_receipt['url']});sources.append({'title':'Coquitlam zoning','url':zone_receipt['url']})
    if plan:
     a=plan['properties'];row['neighbourhood']=a.get('NP_LABEL') or a['NP_NAME'];facts.append({'label':'Neighbourhood plan area','value':a['NP_NAME'],'url':plan_receipt['url']});sources.append({'title':'Coquitlam neighbourhood plans','url':plan_receipt['url']})
   if row.get('neighbourhood'):counts[row['neighbourhood']]+=1
   extras['municipal_facts']=facts;extras['municipality_context_sources']=sources;extras['municipal_checked']=NOW;row['features']=js(extras)
   target.write(js(row)+'\n');total+=1
 base.close();sources=[];documents=[];layer_rows=[]
 for name,features,receipt,field in [('zoning',zones,zone_receipt,'ZONING_CODE'),('neighbourhoods',plans,plan_receipt,'NP_LABEL')]:
  tolerance=0.000015
  while True:
   display={'type':'FeatureCollection','features':[{'type':'Feature','properties':{'name':f['properties'].get(field) or f['properties'].get('NP_NAME'),'municipality':'Coquitlam','source_url':receipt['url']},'geometry':mapping(shapely.make_valid(shape(f['geometry'])).simplify(tolerance,preserve_topology=True))} for f in features if f.get('geometry')]}
   if len(js(display).encode())<1400000:break
   tolerance*=2
  layer_rows.append({'id':name+':Coquitlam','data':js(display)})
 for receipt in [prop_receipt,zone_receipt,plan_receipt]:
  sources.append({'id':receipt['id'],'title':receipt['title'],'url':receipt['url'],'downloaded':receipt.get('downloaded_at') or NOW,'records':receipt['records'],'bytes':receipt.get('bytes'),'sha256':receipt.get('sha256'),'status':'verified','detail':'Complete official dataset. '+str(receipt.get('licence',''))+'. No monetary assessments in this source.'})
 summary={'municipality':'Coquitlam','properties':total,'assessments':0,'neighbourhoods':[{'name':n,'count':c} for n,c in sorted(counts.items())],'datasets':[{'title':r['title'],'url':r['url'],'records':r['records']} for r in sources],'limits':['No monetary assessment values are supplied in these datasets.','Address updates require one distinct address for an exact provincial PID.','Zoning and plan areas are point-in-polygon matches; verify parcel-specific bylaws.'],'updated':NOW}
 layer_rows.append({'id':'municipal-summary:Coquitlam','data':js(summary)})
 documents.append({'id':'municipal:Coquitlam','title':'Coquitlam municipal property data','content':js(summary),'url':prop_receipt['url'],'source':'municipal-coquitlam','embedding':None,'updated':NOW})
 seen=set()
 for f in zones:
  a=f['properties'];code=a['ZONING_CODE']
  if code in seen:continue
  seen.add(code);documents.append({'id':'coquitlam-zone:'+code,'title':'Coquitlam zoning '+code,'content':f"Official Coquitlam zoning map code {code}. Published description: {a.get('ZONING_DESCRIPTION') or 'not provided'}. Category: {a.get('CATEGORY') or 'not provided'}. This is mapped zoning, not a statement of development approval or existing building form.",'url':zone_receipt['url'],'source':'municipal-coquitlam','embedding':None,'updated':NOW})
 for name,rows in [('layers',layer_rows),('sources',sources),('documents',documents)]:
  with (OUT/(name+'-coquitlam.jsonl')).open('w',encoding='utf8') as target:
   for row in rows:target.write(js(row)+'\n')
 report={'properties':total,'addresses_exact_pid_unique':addresses,'zoning_matches':zone_count,'assessment_values_added':0,'neighbourhoods':dict(counts)}
 (RAW/'integration-report.json').write_text(json.dumps(report,indent=2),encoding='utf8');print('COQUITLAM_PREPARED',js(report),flush=True)

if __name__=='__main__':main()
