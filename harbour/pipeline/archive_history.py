"""Shard every original assessment row for inexpensive exact-record RAG retrieval."""
import sqlite3,json,gzip,hashlib
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];OUT=ROOT/'data'/'prepared';ARCH=ROOT/'data'/'raw'/'assessment-history'
def main():
 ARCH.mkdir(exist_ok=True);db=sqlite3.connect(ROOT/'data'/'harbour.sqlite')
 streams={i:gzip.open(ARCH/(str(i)+'.jsonl.gz'),'wt',encoding='utf8',compresslevel=3) for i in range(256)}
 count=0
 for folio,raw in db.execute('SELECT folio,raw FROM assessments'):
  shard=int(hashlib.sha256(str(folio).encode()).hexdigest()[:4],16)%256
  streams[shard].write(raw+'\n');count+=1
  if count%500000==0:print('HISTORY_ARCHIVE',count,flush=True)
 for f in streams.values():f.close()
 with (OUT/'archive.jsonl').open('w',encoding='utf8') as f:
  for folio in db.execute('SELECT DISTINCT folio FROM assessments'):
   id=folio[0]
   if not id:continue
   row=db.execute('SELECT raw FROM assessments WHERE folio=? ORDER BY year DESC LIMIT 1',(id,)).fetchone();r=json.loads(row[0]);years=[x[0] for x in db.execute('SELECT DISTINCT year FROM assessments WHERE folio=? ORDER BY year',(id,))]
   unit=r.get('from_civic_number');address=((unit+' – ') if unit else '')+(r.get('to_civic_number') or '')+' '+(r.get('street_name') or '')
   item={'id':id,'pid':r.get('pid'),'address':address,'years':json.dumps(years),'shard':str(int(hashlib.sha256(id.encode()).hexdigest()[:4],16)%256)}
   f.write(json.dumps(item,ensure_ascii=False,separators=(',',':'))+'\n')
 db.close();print('ALL_HISTORY_ARCHIVED',count,flush=True)
if __name__=='__main__':main()
