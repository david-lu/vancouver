import assert from 'node:assert/strict';
const base=process.env.TEST_URL||'http://127.0.0.1:5188';
async function get(path){const r=await fetch(base+'/api/'+path,{signal:AbortSignal.timeout(120000)});assert.equal(r.status,200,path);return r.json();}
const cities=['Burnaby','Surrey','North Vancouver City','North Vancouver District','Coquitlam'];
for(const city of cities){
 const summary=await get('layers/'+encodeURIComponent('municipal-summary:'+city));
 assert.equal(summary.municipality,city);assert.ok(summary.properties>10000);assert.ok(summary.datasets.length>=3);
 const zones=await get('layers/zoning?municipality='+encodeURIComponent(city));
 assert.ok(zones.features.length>0,city+' has zoning geometry');assert.ok(zones.features.every(f=>f.geometry&&f.properties.municipality===city));
 const neighbourhoods=await get('layers/neighbourhoods?municipality='+encodeURIComponent(city));
 assert.ok(neighbourhoods.features.length>0,city+' has neighbourhoods');
 console.log('MUNICIPAL_DATA',city,summary.properties,'properties',summary.assessments,'bulk assessments',zones.features.length,'zoning shapes');
}
for(const city of ['Burnaby','Surrey']){
 const found=await get('properties?municipality='+city+'&min=1&limit=12');
 assert.ok(found.total>90000);assert.ok(found.properties.every(p=>p.assessed>0&&Math.abs(p.assessed-p.land-p.improvement)<1));
 assert.ok(found.properties.every(p=>city==='Surrey'?p.year===2026:p.year===null));
 assert.ok(found.properties.some(p=>p.zone&&p.features?.municipal_facts?.length));
}
const burnaby=await get('properties/pmbc:000437697');assert.equal(burnaby.address,'6910 SUSSEX AVE');assert.equal(burnaby.assessed,3589000);assert.equal(burnaby.year,null);assert.ok(burnaby.features.assessment_label);
for(const [id,city] of [['pmbc:012034258','North Vancouver District'],['pmbc:002399091','North Vancouver City']]){
 const p=await get('properties/'+id);assert.equal(p.municipality,city);assert.ok(p.assessed>0,'Live exact-PID assessment lookup');assert.equal(p.year,null);assert.ok(p.features.live_lookup?.checked);assert.equal(p.assessed,p.land+p.improvement);
}
const parks=await get('layers/parks?municipality=Surrey');assert.ok(parks.features.length>500);assert.ok(parks.features.every(f=>f.geometry.type==='Point'));
const coverage=await get('layers/coverage');assert.equal(coverage.length,23);assert.ok(coverage.find(c=>c.municipality==='Burnaby').assessments>90000);assert.ok(coverage.find(c=>c.municipality==='Surrey').assessments>90000);
assert.equal((await fetch(base+'/api/import',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'})).status,401);
console.log('MUNICIPAL_CHECKS_PASSED');
