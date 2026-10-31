"""Load complete prepared data into local app SQLite (or Wrangler D1 storage)."""
import argparse,json,sqlite3,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]; APP=ROOT/'harbour';PREP=ROOT/'data'/'prepared'
def main():
 p=argparse.ArgumentParser();p.add_argument('--db');p.add_argument('--only',nargs='*');args=p.parse_args()
 if args.db:path=Path(args.db)
 else:
  candidates=list((APP/'.wrangler/state').rglob('*.sqlite'))
  path=None
  for candidate in candidates:
   c=sqlite3.connect(candidate)
   if c.execute("SELECT name FROM sqlite_master WHERE name='properties'").fetchone():path=candidate;c.close();break
   c.close()
  if path is None:raise SystemExit('Apply local D1 migrations first, or pass --db data/app.sqlite')
 db=sqlite3.connect(path);db.execute('PRAGMA journal_mode=WAL');db.execute('PRAGMA synchronous=NORMAL')
 db.execute('CREATE TABLE IF NOT EXISTS pipeline_imports(file TEXT PRIMARY KEY,bytes INTEGER,rows INTEGER)')
 files=[('addresses-regional','addresses'),('archive','archive'),('properties','properties'),('permits','permits'),('layers','layers'),('sources','sources'),('documents','documents'),('documents-data','documents'),('documents-regional','documents'),('listings','listings'),('guidance-sources','sources'),('properties-regional','properties'),('layers-regional','layers'),('sources-regional','sources')]
 files += [(table+'-'+city,table) for city in ['burnaby','surrey','northvan','coquitlam','municipal'] for table in ['properties','permits','layers','sources','documents']]
 for name,table in files:
  source=PREP/(name+'.jsonl')
  if args.only and name not in args.only:continue
  if not source.exists():continue
  old=db.execute('SELECT bytes FROM pipeline_imports WHERE file=?',(name,)).fetchone()
  if old and old[0]==source.stat().st_size:print('LOCAL_CACHED',name,flush=True);continue
  count=0;batch=[];sql=None
  with source.open(encoding='utf8') as f:
   for line in f:
    r=json.loads(line)
    if table=='properties':r.setdefault('municipality','Vancouver')
    columns=[c[1] for c in db.execute('PRAGMA table_info('+table+')')] if sql is None else columns
    if sql is None:sql='INSERT INTO '+table+' ('+','.join(columns)+') VALUES ('+','.join('?' for _ in columns)+') ON CONFLICT(id) DO UPDATE SET '+','.join(c+'=excluded.'+c for c in columns if c!='id')
    batch.append(tuple(r.get(c) for c in columns));count+=1
    if len(batch)==1000:db.executemany(sql,batch);db.commit();batch=[]
    if count%50000==0:print('LOCAL',name,count,flush=True)
   if batch:db.executemany(sql,batch);db.commit()
  db.execute('INSERT OR REPLACE INTO pipeline_imports VALUES(?,?,?)',(name,source.stat().st_size,count));db.commit();print('LOCAL_COMPLETE',name,count,flush=True)
 db.execute('ANALYZE');db.commit();print('LOCAL_DB',str(path),flush=True);db.close()
if __name__=='__main__':main()
