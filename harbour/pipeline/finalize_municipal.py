"""Reconcile coverage and retrieval evidence against the imported local database."""
import json, sqlite3, time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];OUT=ROOT/'data/prepared'
def js(x):return json.dumps(x,ensure_ascii=False,separators=(',',':'))
def main():
 db=None
 for p in (ROOT/'harbour/.wrangler/state').rglob('*.sqlite'):
  candidate=sqlite3.connect(p)
  if candidate.execute("SELECT name FROM sqlite_master WHERE name='properties'").fetchone():db=candidate;break
  candidate.close()
 if db is None:raise RuntimeError('Import all municipal rows into local D1 first')
 def layer(id):
  row=db.execute('SELECT data FROM layers WHERE id=?',(id,)).fetchone();return json.loads(row[0]) if row else None
 counts={r[0]:{'records':r[1],'assessments':r[2],'mapped':r[3]} for r in db.execute('SELECT municipality,COUNT(*),COALESCE(SUM(assessed>0),0),SUM(lat IS NOT NULL AND lon IS NOT NULL) FROM properties GROUP BY municipality')}
 coverage=layer('coverage');stats=layer('regional-stats');now=time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime());docs=[]
 for c in coverage:
  city=c['municipality'];c.update(counts[city]);summary=layer('municipal-summary:'+city);c['on_demand_assessments']=city in ['North Vancouver City','North Vancouver District'];c['enriched']=bool(summary);c['updated']=now
  if summary:
   c['datasets']=len(summary['datasets']);c['detail']=f"{c['records']:,} property records; "+(f"{c['assessments']:,} published assessments; " if c['assessments'] else ('assessment lookup on property open; ' if c['on_demand_assessments'] else 'no bulk assessments; '))+f"{c['datasets']} additional municipal datasets."
   c['limitations']=summary.get('limits',[])
  docs.append({'id':'coverage:'+city,'title':city+' property data coverage','content':js(c)+'. Assessment values are not asking or sale prices. Burnaby and North Vancouver public assessment sources do not state an assessment year; do not invent one. Surrey assessment metadata explicitly identifies 2026. North Vancouver values are checked by exact PID when opening a property, where the municipal service is available. Monetary bulk values also exist for Vancouver and Langley Township. Municipal map geometry and nearby permit/development records need property-specific verification. No licensed live MLS feed is connected.','url':summary['datasets'][0]['url'] if summary and summary['datasets'] else 'https://open.canada.ca/data/en/dataset/4cf233c2-f020-4f7a-9b87-1923252fbc24','source':'regional-coverage','embedding':None,'updated':now})
 stats.update(properties=sum(c['records'] for c in counts.values()),assessments=sum(c['assessments'] or 0 for c in counts.values()),municipality_counts={city:c['records'] for city,c in counts.items()},permits=db.execute('SELECT COUNT(*) FROM permits').fetchone()[0],updated=now,municipal_expansion={city:c for city,c in counts.items() if city in ['Burnaby','Surrey','North Vancouver City','North Vancouver District','Coquitlam']})
 rows=[{'id':'coverage','data':js(coverage)},{'id':'regional-stats','data':js(stats)}]
 for name,records in [('layers',rows),('documents',docs)]:
  with (OUT/(name+'-municipal.jsonl')).open('w',encoding='utf8') as f:
   for r in records:f.write(js(r)+'\n')
 database_counts={table:db.execute('SELECT COUNT(*) FROM '+table).fetchone()[0] for table in ['properties','addresses','archive','permits','documents','sources','layers','listings']}
 (ROOT/'data/municipal-validation.json').write_text(json.dumps({'stats':stats,'coverage':coverage,'database_counts':database_counts},indent=2),encoding='utf8')
 print('MUNICIPAL_COUNTS',js(stats['municipal_expansion']),flush=True);print('REGION',stats['properties'],'records',stats['assessments'],'assessments',stats['permits'],'permits',flush=True)
 db.close()
if __name__=='__main__':main()
