"""Index the actual municipality coverage summaries without extra AI calls."""
import json,time
from pathlib import Path
OUT=Path(__file__).resolve().parents[2]/'data/prepared'
layers={r['id']:json.loads(r['data']) for r in map(json.loads,(OUT/'layers-regional.jsonl').open(encoding='utf8'))}
with (OUT/'documents-regional.jsonl').open('w',encoding='utf8') as f:
 for c in layers['coverage']:
  row={'id':'coverage:'+c['municipality'],'title':c['municipality']+' property data coverage','content':json.dumps(c)+'. ParcelMap BC and Statistics Canada NAR provide parcel geometry and separate civic address locations. A civic point is not a confirmed strata PID match. Monetary values are available only from Vancouver and Langley Township assessment exports. There are no verified live MLS listings without licensed feed credentials.','url':'https://open.canada.ca/data/en/dataset/4cf233c2-f020-4f7a-9b87-1923252fbc24','source':'regional-coverage','embedding':None,'updated':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())}
  f.write(json.dumps(row)+'\n')
print('COVERAGE_DOCUMENTS',len(layers['coverage']))
