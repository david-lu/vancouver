'use client';
import {useEffect,useState} from 'react';
import {MapPin} from 'lucide-react';
export default function AddressSearch({query,municipality,onSelect}:{query:string;municipality:string;onSelect:(a:any)=>void}){
 const [matches,setMatches]=useState<any[]>([]);
 useEffect(()=>{setMatches([]);if(query.trim().length<3)return;const controller=new AbortController();const timer=setTimeout(()=>{fetch('/api/addresses?'+new URLSearchParams({q:query,municipality}),{signal:controller.signal}).then(r=>r.json()).then(d=>{if(Array.isArray(d))setMatches(d);}).catch(()=>{});},500);return()=>{clearTimeout(timer);controller.abort();};},[query,municipality]);
 if(!matches.length)return null;
 return <div className="address-matches" aria-label="Civic address locations"><p>ADDRESS LOCATIONS <span>Show nearby property records</span></p>{matches.slice(0,5).map(a=><button key={a.id} onClick={()=>onSelect(a)}><MapPin size={16}/><span><strong>{a.address}</strong><small>{a.municipality} · NAR address point</small></span></button>)}<small>Address points do not establish a unit-to-parcel match.</small></div>;
}
