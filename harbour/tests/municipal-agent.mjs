import assert from 'node:assert/strict';
const base=process.env.TEST_URL||'http://127.0.0.1:5188';
const cities=['Burnaby','Surrey'];
const summaries=await Promise.all(cities.map(async city=>(await fetch(base+'/api/layers/'+encodeURIComponent('municipal-summary:'+city))).json()));
const response=await fetch(base+'/api/agent',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({message:'Compare the imported assessed-property counts for Burnaby and Surrey, and the assessment years supplied. Cite the exact supporting municipal data. Distinguish imported counts from raw source rows.',history:[]}),signal:AbortSignal.timeout(120000)});
const answer=await response.json();assert.equal(response.status,200);assert.ok(answer.citations?.length);
for(const [i,city] of cities.entries()){
 assert.ok(answer.answer.includes(city));
 assert.ok(answer.answer.replaceAll(',','').includes(String(summaries[i].assessments)),city+' imported assessment count');
 const sourceUrls=new Set(summaries[i].datasets.filter(d=>['assessment','property-detail'].includes(d.kind)).map(d=>d.url));
 sourceUrls.add(base+'/api/layers/'+encodeURIComponent('municipal-summary:'+city));
 assert.ok(answer.citations.some(c=>sourceUrls.has(c.url)),city+' relevant citation');
}
const references=[...answer.answer.matchAll(/\[(\d+)\]/g)].map(m=>Number(m[1]));
assert.ok(references.length);assert.ok(references.every(id=>answer.citations.some(c=>c.id===id)));
assert.ok(answer.citations.every(c=>references.includes(c.id)),'Only referenced sources are displayed');
console.log('MUNICIPAL_AGENT_PASSED',answer.answer,JSON.stringify(answer.citations));
