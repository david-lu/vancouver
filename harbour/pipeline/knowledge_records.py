"""Index all small city thematic records without spending AI tokens per record."""
import json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];RAW=ROOT/'data/raw';OUT=ROOT/'data/prepared';rows=[]
for name in ['parks','schools','heritage-sites','rental-standards-current-issues','zoning-districts-and-labels','local-area-boundary','designated-floodplain']:
 fmt='geojson' if name in ['zoning-districts-and-labels','local-area-boundary','designated-floodplain'] else 'json'
 data=json.loads((RAW/(name+'.'+fmt)).read_text(encoding='utf8'));records=[f['properties'] for f in data['features']] if fmt=='geojson' else data
 receipt=json.loads((RAW/(name+'.receipt.json')).read_text(encoding='utf8'))
 for i,r in enumerate(records):
  r={k:v for k,v in r.items() if k not in ['geom','geo_shape']}
  title=str(r.get('name') or r.get('school_name') or r.get('buildingnamespecifics') or r.get('address') or r.get('district') or r.get('zoning_district') or i)
  rows.append({'id':name+':'+str(i),'title':name.replace('-',' ').title()+' - '+title,'content':json.dumps(r,ensure_ascii=False),'url':'https://opendata.vancouver.ca/explore/dataset/'+name+'/','source':name,'embedding':None,'updated':receipt['downloaded_at']})
with (OUT/'documents-data.jsonl').open('w',encoding='utf8') as f:
 for r in rows:f.write(json.dumps(r,ensure_ascii=False,separators=(',',':'))+'\n')
print('THEMATIC_RECORDS_INDEXED',len(rows))
