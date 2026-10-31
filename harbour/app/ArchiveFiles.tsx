'use client';
import {useState,useEffect} from 'react';
import {Download,Search} from 'lucide-react';
export default function ArchiveFiles(){
 const [files,setFiles]=useState<any[]>([]),[q,setQ]=useState(''),[loading,setLoading]=useState(true);
 useEffect(()=>{fetch('/api/raw-manifest').then(r=>r.json()).then((d:any)=>setFiles(d.files||[])).catch(()=>setFiles([])).finally(()=>setLoading(false));},[]);
 const shown=files.filter(f=>f.file.toLowerCase().includes(q.toLowerCase()));
 return <><p className="muted">Complete downloaded source files, compressed for storage. Each entry retains its original size and SHA-256 fingerprint. Original source licences still apply.</p><label className="archive-search"><Search size={16}/><input value={q} onChange={e=>setQ(e.target.value)} placeholder="Find a source file…" aria-label="Search raw archive"/></label><p className="fineprint">{loading?'Loading archive…':`${shown.length} files · ${files.reduce((s,f)=>s+f.originalBytes,0).toLocaleString()} original bytes archived`}</p><div className="source-list">{shown.slice(0,100).map(f=><div key={f.key}><Download size={16}/><div><a href={'/api/raw/'+f.key} download>{f.file}</a><p>{(f.originalBytes/1e6).toFixed(2)} MB original · {(f.storedBytes/1e6).toFixed(2)} MB download</p><small className="file-hash">SHA-256 {f.originalSha256}</small></div></div>)}</div>{shown.length>100&&<p className="muted">Showing the first 100 files. Narrow your search to find another source.</p>}{!files.length&&!loading&&<p className="muted">The raw archive has not finished uploading yet. Official source downloads remain available under Data sources.</p>}</>;
}
