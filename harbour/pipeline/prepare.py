"""Create the complete local SQLite archive and compact records for the web database."""
import csv, hashlib, json, math, sqlite3, time, collections, sys
from shapely.geometry import shape, Point
from shapely.prepared import prep
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]; RAW=ROOT/'data'/'raw'; OUT=ROOT/'data'/'prepared'
def js(x): return json.dumps(x,ensure_ascii=False,separators=(',',':'))
def num(v):
 try: return float(str(v).replace(',','')) if v not in ('',None) else None
 except (ValueError,TypeError): return None
def integer(v):
 n=num(v); return int(n) if n is not None else None
def load(name): return json.loads((RAW/name).read_text(encoding='utf-8-sig'))
def point(row):
 p=row.get('geo_point_2d') or row.get('googlemapdest')
 if isinstance(p,dict): return p.get('lon'),p.get('lat')
 if isinstance(p,str) and p:
  try:
   lat,lon=p.split(','); return float(lon),float(lat)
  except ValueError: pass
 return None,None
def address(r):
 unit=r.get('from_civic_number',''); civic=r.get('to_civic_number',''); street=r.get('street_name','')
 return f'{unit+" – " if unit else ""}{civic} {street}'.strip()
def distance(a,b,c,d):
 return round(math.hypot((a-c)*math.cos(math.radians((b+d)/2)),b-d)*111195)
def nearest(lon,lat,items):
 if not items or lon is None: return None
 item=min(items,key=lambda x:(lon-x['lon'])**2*.43+(lat-x['lat'])**2)
 return {'name':item['name'],'metres':distance(lon,lat,item['lon'],item['lat'])}
def in_ring(x,y,ring):
 inside=False
 for i in range(len(ring)):
  x1,y1=ring[i]; x2,y2=ring[i-1]
  if (y1>y)!=(y2>y) and x<(x2-x1)*(y-y1)/(y2-y1)+x1:inside=not inside
 return inside
def inside(lon,lat,geom):
 if lon is None:return False
 polygons=[geom['coordinates']] if geom['type']=='Polygon' else geom.get('coordinates',[])
 return any(in_ring(lon,lat,p[0]) and not any(in_ring(lon,lat,r) for r in p[1:]) for p in polygons)
def main():
 OUT.mkdir(parents=True,exist_ok=True)
 db=sqlite3.connect(ROOT/'data'/'harbour.sqlite');db.execute('PRAGMA journal_mode=WAL');db.execute('PRAGMA synchronous=NORMAL')
 db.execute('CREATE TABLE IF NOT EXISTS archive_sources(id TEXT PRIMARY KEY,receipt TEXT)')
 db.execute('CREATE TABLE IF NOT EXISTS assessments(source TEXT,folio TEXT,year INTEGER,pid TEXT,land REAL,improvement REAL,tax REAL,raw TEXT)')
 db.execute('CREATE TABLE IF NOT EXISTS completed(source TEXT PRIMARY KEY,records INTEGER)')
 for name in ['property-tax-report-2006-2010','property-tax-report-2011-2015','property-tax-report-2016-2019','property-tax-report']:
  if db.execute('SELECT 1 FROM completed WHERE source=?',(name,)).fetchone():continue
  print('IMPORT',name,flush=True);db.execute('DELETE FROM assessments WHERE source=?',(name,));count=0;batch=[]
  with (RAW/(name+'.csv')).open(encoding='utf-8-sig',newline='') as f:
   for r in csv.DictReader(f):
    batch.append((name,r.get('folio'),integer(r.get('report_year') or r.get('tax_assessment_year')),r.get('pid'),num(r.get('current_land_value')),num(r.get('current_improvement_value')),num(r.get('tax_levy')),js(r)));count+=1
    if len(batch)>=10000:db.executemany('INSERT INTO assessments VALUES(?,?,?,?,?,?,?,?)',batch);db.commit();batch=[]
  if batch:db.executemany('INSERT INTO assessments VALUES(?,?,?,?,?,?,?,?)',batch)
  db.execute('INSERT INTO completed VALUES(?,?)',(name,count));db.commit(); print('IMPORTED',name,count,flush=True)
 db.execute('CREATE INDEX IF NOT EXISTS assessment_folio_year ON assessments(folio,year)');db.commit()
 addresses=load('property-addresses.json');coords={};addressmap={}
 for r in addresses:
  coords.setdefault(str(r.get('pcoord')),r);addressmap[(str(r.get('civic_number')),r.get('std_street'))]=r
 parcels={}
 for p in load('property-parcel-polygons.geojson')['features']:parcels[str(p['properties'].get('tax_coord'))]=p['geometry']
 areas=load('local-area-boundary.geojson'); flood=load('designated-floodplain.geojson')
 flood_shapes=[prep(shape(a['geometry'])) for a in flood['features']]
 area_shapes=[(a['properties']['name'],prep(shape(a['geometry']))) for a in areas['features']]
 feature_cache={}
 parks=[];schools=[]
 for name,target,key in [('parks',parks,'name'),('schools',schools,'school_name')]:
  for r in load(name+'.json'):
   lon,lat=point(r)
   if lon is not None:target.append({'name':r[key],'lon':lon,'lat':lat})
 heritage=[]
 for r in load('heritage-sites.json'):
  lon,lat=point(r)
  if lon is not None:heritage.append({'name':r.get('buildingnamespecifics') or 'Heritage register site','lon':lon,'lat':lat})
 permits=[];permitgrid=collections.defaultdict(list)
 with (RAW/'issued-building-permits.csv').open(encoding='utf-8-sig',newline='') as f:
  for r in csv.DictReader(f):
   lon,lat=point(r);p={'id':r['permitnumber'],'address':r.get('address'),'description':r.get('projectdescription'),'issued':r.get('issuedate'),'value':num(r.get('projectvalue')),'type':r.get('typeofwork'),'lon':lon,'lat':lat,'raw':js(r)};permits.append(p)
   if lon is not None:permitgrid[(round(lon,3),round(lat,3))].append(p)
 with (OUT/'permits.jsonl').open('w',encoding='utf8') as f:
  for p in permits:f.write(js(p)+'\n')
 latest_year=db.execute('SELECT MAX(year) FROM assessments').fetchone()[0]; print('LATEST YEAR',latest_year,flush=True)
 neighbourhoods=collections.defaultdict(list); count=0;matched=0
 with (OUT/'properties.jsonl').open('w',encoding='utf8') as f:
  # Select latest available record per folio; retained historic-only properties are archived but not mapped as current.
  for (raw,) in db.execute('SELECT raw FROM assessments WHERE year=?',(latest_year,)):
   r=json.loads(raw);folio=r['folio'];c=coords.get(str(r.get('land_coordinate'))) or addressmap.get((str(r.get('to_civic_number')),r.get('street_name'))) or {};lon,lat=point(c)
   neighbourhood=c.get('geo_local_area')
   if not neighbourhood and lon is not None:
    neighbourhood=next((name for name,polygon in area_shapes if polygon.covers(Point(lon,lat))),None)
   history=[{'year':y,'land':l,'improvement':i,'value':(l or 0)+(i or 0),'tax':t} for y,l,i,t in db.execute('SELECT year,land,improvement,tax FROM assessments WHERE folio=? GROUP BY year ORDER BY year',(folio,))]
   land=num(r.get('current_land_value'));improve=num(r.get('current_improvement_value'));value=(land or 0)+(improve or 0)
   previous=next((h['value'] for h in reversed(history) if h['year']<latest_year and h['value']),None)
   cache_key=(lon,lat)
   if cache_key in feature_cache:features=feature_cache[cache_key]
   else:features={'park':nearest(lon,lat,parks),'school':nearest(lon,lat,schools),'coordinate_match':'land_coordinate/address' if c else 'unmatched','source':'property-tax-report','assessed_not_asking':True}
   if lon is not None:matched+=1
   if lon is not None and cache_key not in feature_cache:
    features['designated_floodplain']=any(polygon.covers(Point(lon,lat)) for polygon in flood_shapes);h=nearest(lon,lat,heritage)
    features['heritage_nearby']=h if h and h['metres']<25 else None
    near=[]
    for dx in [-.001,0,.001]:
     for dy in [-.001,0,.001]:near.extend(permitgrid.get((round(lon+dx,3),round(lat+dy,3)),[]))
    features['nearby_permit_count']=sum(distance(lon,lat,p['lon'],p['lat'])<35 for p in near)
    feature_cache[cache_key]=features
   p={'id':folio,'pid':r.get('pid'),'address':address(r),'street':r.get('street_name'),'neighbourhood':neighbourhood,'lat':lat,'lon':lon,'assessed':value if land is not None or improve is not None else None,'land':land,'improvement':improve,'tax':num(r.get('tax_levy')),'year':latest_year,'built':integer(r.get('year_built')),'zone':r.get('zoning_district'),'type':r.get('legal_type'),'change':round((value/previous-1)*100,2) if previous else None,'history':js(history),'features':js(features),'parcel':js(parcels[str(r.get('land_coordinate'))]) if str(r.get('land_coordinate')) in parcels else None}
   f.write(js(p)+'\n');count+=1
   if neighbourhood and value:neighbourhoods[neighbourhood].append(value)
   if count%25000==0:print('FEATURES',count,flush=True)
 layers={'neighbourhoods':areas,'zoning':load('zoning-districts-and-labels.geojson'),'floodplain':flood}
 for name,items in [('parks',parks),('schools',schools),('heritage',heritage)]:layers[name]={'type':'FeatureCollection','features':[{'type':'Feature','geometry':{'type':'Point','coordinates':[p['lon'],p['lat']]},'properties':{'name':p['name']}} for p in items]}
 stats=[]
 for name,values in sorted(neighbourhoods.items()):
  values.sort();stats.append({'name':name,'count':len(values),'median':values[len(values)//2]})
 layers['stats']={'properties':count,'geocoded':matched,'assessment_records':db.execute('SELECT count(*) FROM assessments').fetchone()[0],'permits':len(permits),'year':latest_year,'neighbourhoods':stats,'updated':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())}
 with (OUT/'layers.jsonl').open('w',encoding='utf8') as f:
  for key,value in layers.items():f.write(js({'id':key,'data':js(value)})+'\n')
 sources=[]
 for path in RAW.glob('*.receipt.json'):
  r=json.loads(path.read_text());n=r['id'];actual=db.execute('SELECT records FROM completed WHERE source=?',(n,)).fetchone()
  sources.append({'id':n,'title':n.replace('-',' ').title(),'url':r['url'],'downloaded':r['downloaded_at'],'records':actual[0] if actual else r.get('expected_records'),'bytes':r['bytes'],'sha256':r['sha256'],'status':'verified','detail':'Full export archived locally. Contains information licensed under the Open Government Licence – Vancouver.'})
 with (OUT/'sources.jsonl').open('w',encoding='utf8') as f:
  for s in sources:f.write(js(s)+'\n');db.execute('INSERT OR REPLACE INTO archive_sources VALUES(?,?)',(s['id'],js(s)))
 # Full raw records remain in the SQLite archive and immutable downloaded files.
 db.execute('CREATE TABLE IF NOT EXISTS raw_datasets(id TEXT PRIMARY KEY,format TEXT,path TEXT)')
 for r in sources:db.execute('INSERT OR REPLACE INTO raw_datasets VALUES(?,?,?)',(r['id'],'source-export',str(RAW)))
 db.commit();db.close();(OUT/'stats.json').write_text(js(layers['stats']),encoding='utf8');print('PREPARED',js(layers['stats']),flush=True)
if __name__=='__main__': main()
