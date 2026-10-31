/** Resumable hosted D1 + R2 import. Runtime credentials are read in memory only. */
import fs from 'node:fs';
import path from 'node:path';
import readline from 'node:readline';
import { createHash } from 'node:crypto';
import { createGzip } from 'node:zlib';
import { pipeline } from 'node:stream/promises';
import { fileURLToPath } from 'node:url';
const ROOT=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'../..');
const DATA=path.join(ROOT,'data'),PREP=path.join(DATA,'prepared'),RAW=path.join(DATA,'raw');
const flags=new Set(process.argv.slice(2));
function readSecret(){return new Promise((resolve,reject)=>{let value='';const terminal=process.stdin.isTTY;function finish(){process.stdin.off('data',read);if(terminal)process.stdin.setRawMode(false);process.stdin.pause();try{resolve(JSON.parse(value.trim()));}catch{reject(Error('Expected configuration JSON on stdin'));}}function read(chunk){value+=chunk;if(value.includes('\n')||value.includes('\r'))finish();}if(terminal)process.stdin.setRawMode(true);process.stdin.setEncoding('utf8');process.stdin.on('data',read);process.stdin.resume();process.stderr.write('Ready for data import JSON on stdin (input is hidden).\n');});}
const config=await readSecret();
if(!/^https:\/\//.test(config.url)&&!config.url.startsWith('http://127.0.0.1'))throw Error('Use HTTPS for hosted import');
const localEnv=fs.readFileSync(path.join(ROOT,'harbour','.dev.vars'),'utf8');
const ingest=config.ingestToken||localEnv.match(/^INGEST_TOKEN=(.*)$/m)?.[1]?.trim().replace(/^['"]|['"]$/g,'');
if(!ingest)throw Error('INGEST_TOKEN is required');
const headers={Authorization:'Bearer '+ingest,...(config.bypass?{'OAI-Sites-Authorization':'Bearer '+config.bypass}:{})};
const checkpointPath=path.join(DATA,flags.has('--raw-only')?'hosted-raw-import.json':'hosted-import.json');
let checkpoint=fs.existsSync(checkpointPath)?JSON.parse(fs.readFileSync(checkpointPath,'utf8')):{};
if(checkpoint.url!==config.url)checkpoint={url:config.url,files:{},raw:{}};
function checkpointSave(){fs.writeFileSync(checkpointPath,JSON.stringify(checkpoint,null,2));}
async function request(endpoint,options={}){for(let attempt=0;attempt<8;attempt++){try{const response=await fetch(config.url+endpoint,{...options,headers:{...headers,...options.headers},signal:AbortSignal.timeout(180000)});if(response.ok)return await response.json();if(![408,429,500,502,503,504].includes(response.status))throw Object.assign(Error('Import HTTP '+response.status+' at '+endpoint),{permanent:true});}catch(e){if(e.permanent||attempt===7)throw e;}await new Promise(r=>setTimeout(r,Math.min(30000,1000*2**attempt)));}throw Error('Import retry budget exhausted');}
const baseFiles=[['addresses-regional','addresses'],['archive','archive'],['properties','properties'],['permits','permits'],['layers','layers'],['sources','sources'],['documents','documents'],['documents-data','documents'],['documents-regional','documents'],['guidance-sources','sources'],['properties-regional','properties'],['layers-regional','layers'],['sources-regional','sources']];
const selectedCity=[...flags].find(f=>f.startsWith('--city='))?.slice(7);
const municipalFiles=['burnaby','surrey','northvan','coquitlam','municipal'].filter(city=>!selectedCity||selectedCity.split(',').includes(city)).flatMap(city=>['properties','permits','layers','sources','documents'].map(table=>[table+'-'+city,table]));
const files=flags.has('--municipal')?municipalFiles:[...baseFiles,...municipalFiles];
async function uploadRows(name,table){
 const filename=path.join(PREP,name+'.jsonl');if(!fs.existsSync(filename)){console.log('MISSING_OPTIONAL',name);return;}
 const stat=fs.statSync(filename),signature=stat.size+':'+stat.mtimeMs,old=checkpoint.files[name];
 if(old?.signature===signature&&old.complete){console.log('HOSTED_CACHED',name);return;}
 const skip=old?.signature===signature?old.rows:0;let count=0,batch=[],last=Date.now();
 const input=readline.createInterface({input:fs.createReadStream(filename),crlfDelay:Infinity});
 async function flush(){const chunks=[];for(let i=0;i<batch.length;i+=500)chunks.push(batch.slice(i,i+500));await Promise.all(chunks.map(rows=>request('/api/import',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({table,rows})})));checkpoint.files[name]={signature,rows:count,complete:false};checkpointSave();batch=[];if(Date.now()-last>15000){console.log('HOSTED',name,count);last=Date.now();}}
 for await(const line of input){if(!line.trim())continue;count++;if(count<=skip)continue;batch.push(JSON.parse(line));if(batch.length===4000)await flush();}
 if(batch.length)await flush();checkpoint.files[name]={signature,rows:count,complete:true};checkpointSave();console.log('HOSTED_COMPLETE',name,count);
}
if(!flags.has('--raw-only')){for(const [name,table]of files)await uploadRows(name,table);console.log('HOSTED_COUNTS',JSON.stringify(await request('/api/import-status')));}
async function walk(dir){let result=[];for(const entry of await fs.promises.readdir(dir,{withFileTypes:true})){if(entry.name.startsWith('.')||/\.(sqlite|sqlite-wal|sqlite-shm|db)$/i.test(entry.name))continue;const p=path.join(dir,entry.name);if(entry.isDirectory())result.push(...await walk(p));else if(!/\.(part|tmp|zip)$/i.test(entry.name))result.push(p);}return result;}
async function hashFile(p){const h=createHash('sha256');for await(const chunk of fs.createReadStream(p))h.update(chunk);return h.digest('hex');}
async function uploadFile(p){const relative=path.relative(RAW,p).split(path.sep).join('/');const original=fs.statSync(p),signature=original.size+':'+original.mtimeMs;const old=checkpoint.raw[relative];if(old?.signature===signature)return old;let compressed=p,key=relative;if(!p.endsWith('.gz')){compressed=path.join(DATA,'upload-cache',relative+'.gz');await fs.promises.mkdir(path.dirname(compressed),{recursive:true});if(!fs.existsSync(compressed)||fs.statSync(compressed).mtimeMs<original.mtimeMs)await pipeline(fs.createReadStream(p),createGzip({level:3}),fs.createWriteStream(compressed));key+='.gz';}const content=await fs.promises.readFile(compressed),sha256=createHash('sha256').update(content).digest('hex');const result=await request('/api/raw/'+key,{method:'PUT',headers:{'Content-Type':'application/gzip','X-Content-SHA256':sha256},body:content});if(result.bytes!==content.length||result.sha256!==sha256)throw Error('Archive read-back metadata mismatch: '+relative);const record={file:relative,key,originalBytes:original.size,storedBytes:content.length,sha256,originalSha256:await hashFile(p),signature};checkpoint.raw[relative]=record;checkpointSave();console.log('RAW_COMPLETE',relative,content.length);return record;}
if(!flags.has('--rows-only')){const pending=await walk(RAW),records=[];let cursor=0;await Promise.all(Array.from({length:3},async()=>{while(cursor<pending.length){const p=pending[cursor++];records.push(await uploadFile(p));}}));const manifest={created:new Date().toISOString(),files:records.sort((a,b)=>a.file.localeCompare(b.file))};const body=JSON.stringify(manifest);await request('/api/raw/archive-manifest.json',{method:'PUT',headers:{'Content-Type':'application/json','X-Content-SHA256':createHash('sha256').update(body).digest('hex')},body});console.log('RAW_ARCHIVE_COMPLETE',records.length,'files',records.reduce((n,r)=>n+r.originalBytes,0),'original bytes');}
console.log('DATA_PUBLICATION_COMPLETE');
