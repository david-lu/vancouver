"""Validate all normalized Surrey assets against the complete raw archive."""
from pathlib import Path
import json,sqlite3,hashlib
from collections import Counter
from shapely.geometry import shape
root=Path(__file__).resolve().parents[2]; raw=root/'data/raw/surrey-extra'; out=root/'data/prepared'
db=sqlite3.connect(f"file:{(raw/'integration.sqlite').as_posix()}?mode=ro",uri=True)
report=json.loads((raw/'integration-report.json').read_text(encoding='utf-8'))
fields=set('id pid address street neighbourhood municipality lat lon assessed land improvement tax year built zone type change history features parcel'.split())
ids=set(); source_ids=set(); keys=set(); financial=set(); counts=Counter(); max_string=0
for line in (out/'properties-surrey.jsonl').open(encoding='utf-8'):
 r=json.loads(line); assert set(r)==fields; assert r['id'] not in ids; ids.add(r['id']); assert r['municipality']=='Surrey'
 for k in ('history','features','parcel'): assert isinstance(r[k],str); json.loads(r[k])
 max_string=max(max_string,*(len(v.encode('utf-8')) for v in r.values() if isinstance(v,str)))
 f=json.loads(r['features']); counts['properties']+=1
 assert (r['lat'] is None)==(r['lon'] is None)
 if r['lat'] is not None: assert 48<r['lat']<51 and -124<r['lon']<-121; counts['mapped_properties']+=1
 if f.get('source')=='surrey-property-detail':
  counts['official_entities']+=1; assert r['year']==2026 and r['built'] is None and r['tax'] is None
  entity_keys=set(f.get('official_entity_keys',[f['official_entity_key']])); assert not keys.intersection(entity_keys); keys.update(entity_keys)
  if f.get('financial_scope_unresolved'):
   counts['unresolved_entities']+=1; assert r['assessed'] is None and r['land'] is None and r['improvement'] is None and json.loads(r['history'])==[]
   assert len(f['financial_variants'])>1 and any(x['label']=='Assessment scope' for x in f['municipal_facts'])
   assert {x for v in f['financial_variants'] for x in v['source_record_ids']}==set(f['source_record_ids'])
  else:
   assert r['assessed'] is not None and abs(r['assessed']-r['land']-r['improvement'])<0.01
   counts['assessments']+=r['assessed']>0
  s=f['source_record_ids']; assert len(s)==f['source_record_count']; assert not source_ids.intersection(s); source_ids.update(s)
  if r['pid'] and str(f.get('folio') or '').strip():
   key=(r['pid'],str(f['folio']).strip().upper(),r['land'],r['improvement'],r['assessed']); assert key not in financial; financial.add(key)
  if f.get('assessment_shared_across_addresses'):
   counts['assessments_with_alternate_addresses']+=1; assert len(f['alternate_addresses'])>1
  counts['official_mapped' if r['lat'] is not None else 'official_unmapped']+=1
base_ids={r[0] for r in db.execute('SELECT id FROM base_properties')}; assert base_ids<=ids
expected_source_ids={r[0] for r in db.execute("SELECT objectid FROM source_records WHERE source='property-detail'")}; assert source_ids==expected_source_ids
assert len(keys)==db.execute('SELECT COUNT(*) FROM official_entities').fetchone()[0]
assert counts['properties']==report['properties'] and counts['assessments']==report['assessments'] and counts['mapped_properties']==report['mapped_properties']
assert max_string<1450000
schemas={'permits':set('id address description issued value type lat lon raw'.split()),'layers':{'id','data'},'documents':set('id title content url source embedding updated'.split()),'sources':set('id title url downloaded records bytes sha256 status detail'.split())}
other={}; layer_sizes={}
for name,fields in schemas.items():
 seen=set(); n=0
 for line in (out/f'{name}-surrey.jsonl').open(encoding='utf-8'):
  r=json.loads(line); assert set(r)==fields; assert r['id'] not in seen; seen.add(r['id']); n+=1
  if name=='permits':
   p=json.loads(r['raw']); assert p['municipality']=='Surrey' and p['source']=='surrey-issued-building-permits'; assert (r['lat'] is None)==(r['lon'] is None)
  if name=='layers':
   d=json.loads(r['data']); layer_sizes[r['id']]=len(r['data'].encode()); assert layer_sizes[r['id']]<1450000
   if r['id'].startswith('municipal-summary:'): assert set(d)==set('municipality properties assessments neighbourhoods datasets limits updated'.split())
   else:
    for f in d['features']:
     assert f['properties']['municipality']=='Surrey'; assert shape(f['geometry']).is_valid
     if r['id']=='parks:Surrey': assert f['geometry']['type']=='Point'
  if name=='documents' and r['source']=='surrey-development-applications': assert 'not an issued building permit' in r['content']
 other[name]=n
assert other['permits']==2072 and other['sources']==18 and other['layers']==4 and other['documents']==report['documents']
for name,expected in report['output_files'].items():
 h=hashlib.sha256()
 with (out/name).open('rb') as f:
  while block:=f.read(4*1024*1024): h.update(block)
 assert h.hexdigest()==expected['sha256']; assert (out/name).stat().st_size==expected['bytes']
result={'status':'passed','counts':dict(counts),'source_property_rows_retained':len(source_ids),'base_ids_preserved':len(base_ids),'other_rows':other,'layer_string_bytes':layer_sizes,'max_property_string_bytes':max_string}
(raw/'validation-report.json').write_text(json.dumps(result,indent=2),encoding='utf-8');print(json.dumps(result),flush=True)
