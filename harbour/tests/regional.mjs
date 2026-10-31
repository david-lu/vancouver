import assert from 'node:assert/strict';
const base=process.env.TEST_URL||'http://127.0.0.1:5188';
async function get(path){const r=await fetch(base+'/api/'+path);assert.equal(r.status,200,path);return r.json();}
const coverage=await get('layers/coverage');const stats=await get('layers/regional-stats');assert.equal(coverage.length,23);assert.equal(coverage.reduce((s,c)=>s+c.records,0),stats.properties);assert.ok(stats.properties>=1064512);
for(const c of coverage){const result=await get('properties?limit=1&municipality='+encodeURIComponent(c.municipality));assert.equal(result.total,c.mapped,c.municipality);assert.equal(result.properties[0].municipality,c.municipality);if(!c.assessments&&!c.on_demand_assessments)assert.equal(result.properties[0].assessed,null);}
const addresses=await get('addresses?q=Kingsway&municipality=Burnaby');assert.ok(addresses.length>0);assert.ok(addresses.every(a=>a.municipality==='Burnaby'&&a.address.toUpperCase().includes('KINGSWAY')));
const langley=await get('properties?municipality=Langley%20Township&min=1&limit=10');assert.ok(langley.properties.every(p=>p.year===2026&&p.assessed===p.land+p.improvement));
const map=await get('properties?bbox=-123.18,49.23,-123.04,49.29&sample=map&sort=recommended&limit=300');assert.ok(map.properties.length>40);assert.ok(map.properties.every(p=>p.lat>=49.23&&p.lat<=49.29&&p.lon>=-123.18&&p.lon<=-123.04));
for(const route of ['agent','import']){const r=await fetch(base+'/api/'+route,{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});assert.equal(r.status,route==='import'?401:400,route+' authorization/validation');}
assert.equal((await fetch(base+'/api/saved')).status,200);
const rejected=await fetch(base+'/api/saved',{method:'POST',headers:{Origin:'https://unrelated.example','Content-Type':'application/json'},body:'{}'});assert.equal(rejected.status,403);
console.log('REGIONAL_CHECKS_PASSED',coverage.length,'jurisdictions',addresses.length,'address matches',map.properties.length,'distributed map markers');
