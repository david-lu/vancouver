import {env} from 'cloudflare:workers';

const COOKIE='harbour_workspace';
const YEAR=60*60*24*365;
const encoder=new TextEncoder();
const encode=(bytes:Uint8Array)=>btoa(String.fromCharCode(...bytes)).replaceAll('+','-').replaceAll('/','_').replace(/=+$/,'');
function decode(s:string){return Uint8Array.from(atob(s.replaceAll('-','+').replaceAll('_','/')),c=>c.charCodeAt(0));}
async function key(){
 if(!env.INGEST_TOKEN)throw new Error('Workspace signing secret is not configured');
 return crypto.subtle.importKey('raw',encoder.encode(env.INGEST_TOKEN),{name:'HMAC',hash:'SHA-256'},false,['sign','verify']);
}

/** A signed browser capability scopes saved research; no login or API key in the browser. */
export async function visitor(req:Request):Promise<{id:string;cookie?:string}>{
 const signingKey=await key();
 const token=req.headers.get('cookie')?.split(';').map(s=>s.trim()).find(s=>s.startsWith(COOKIE+'='))?.slice(COOKIE.length+1);
 if(token&&token.length<1024){try{
  const [payload,signature,...extra]=token.split('.');
  if(!extra.length&&signature&&await crypto.subtle.verify('HMAC',signingKey,decode(signature),encoder.encode('harbour-workspace-v1:'+payload))){
   const data=JSON.parse(new TextDecoder().decode(decode(payload)));
   if(typeof data.id==='string'&&data.id.length<256&&Number.isFinite(data.expires)&&data.expires>Date.now())return {id:data.id};
  }
 }catch{/* Invalid or expired capabilities receive a new, isolated workspace. */}}
 // Preserve access to earlier account-owned research when Sites already supplies identity.
 const id=req.headers.get('oai-authenticated-user-id')||'guest:'+crypto.randomUUID();
 const payload=encode(encoder.encode(JSON.stringify({id,expires:Date.now()+YEAR*1000})));
 const signature=encode(new Uint8Array(await crypto.subtle.sign('HMAC',signingKey,encoder.encode('harbour-workspace-v1:'+payload))));
 return {id,cookie:`${COOKIE}=${payload}.${signature}; Path=/; Max-Age=${YEAR}; HttpOnly; SameSite=Lax${new URL(req.url).protocol==='https:'?'; Secure':''}`};
}

/** Salt network quota keys so the database does not store raw client IP addresses. */
export async function networkQuotaKey(req:Request,fallback:string){
 const ip=req.headers.get('cf-connecting-ip');if(!ip)return fallback;
 return encode(new Uint8Array(await crypto.subtle.sign('HMAC',await key(),encoder.encode('harbour-network-v1:'+ip))));
}
