'use client';
import { useEffect, useRef, useState } from 'react';
import { Plus, Minus, LocateFixed, Layers, Map as MapIcon, Check, X, Expand, Trees, School, Droplets, Building2 } from 'lucide-react';
import type { Property } from '../lib/types';
import { money } from '../lib/types';
const empty={type:'FeatureCollection',features:[]};
export default function MapView({properties,selected,onSelect,onBounds,onArea,layerNames,setLayerNames,flyTo,showValues,setShowValues,municipality}:{properties:Property[];selected:Property|null;onSelect:(id:string)=>void;onBounds:(bbox:string)=>void;onArea:(name:string,municipality?:string)=>void;layerNames:string[];setLayerNames:(s:string[])=>void;flyTo:any;showValues:boolean;setShowValues:(v:boolean)=>void;municipality:string}){
 const boundLayers=useRef(new Set<string>()),el=useRef<HTMLDivElement>(null),addressPin=useRef<any>(null),map=useRef<any>(null),callbacks=useRef({onSelect,onBounds,onArea});callbacks.current={onSelect,onBounds,onArea};
 const [ready,setReady]=useState(false),[error,setError]=useState(''),[layersOpen,setLayersOpen]=useState(false),[satellite,setSatellite]=useState(false);
 useEffect(()=>{
  let disposed=false;
  function init(){if(disposed||!el.current)return;try{
   const lib=(window as any).maplibregl;
   const m=new lib.Map({container:el.current,center:[-122.98,49.23],zoom:10.8,minZoom:8,maxZoom:19,attributionControl:false,style:{version:8,glyphs:'https://fonts.openmaptiles.org/{fontstack}/{range}.pbf',sources:{streets:{type:'raster',tiles:['https://tile.openstreetmap.org/{z}/{x}/{y}.png'],tileSize:256,attribution:'&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap contributors</a>'},satellite:{type:'raster',tiles:['https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}'],tileSize:256,attribution:'Imagery © Esri, Maxar, Earthstar Geographics'}},layers:[{id:'streets',type:'raster',source:'streets',paint:{'raster-saturation':-.6,'raster-contrast':-.07,'raster-brightness-min':.05}},{id:'satellite',type:'raster',source:'satellite',layout:{visibility:'none'}}]}});map.current=m;const resize=new ResizeObserver(()=>m.resize());resize.observe(el.current!);(m as any)._harbourResize=resize;
   m.addControl(new lib.AttributionControl({compact:true}),'bottom-right');
   m.on('load',()=>{
    if(disposed)return;
    m.addSource('homes',{type:'geojson',data:empty,cluster:true,clusterRadius:38,clusterMaxZoom:8});
    m.addLayer({id:'clusters',type:'circle',source:'homes',filter:['has','point_count'],paint:{'circle-color':'#244d46','circle-radius':['step',['get','point_count'],19,20,23,100,28],'circle-stroke-width':3,'circle-stroke-color':'#fff'}});
    m.addLayer({id:'cluster-count',type:'symbol',source:'homes',filter:['has','point_count'],layout:{'text-field':['get','point_count_abbreviated'],'text-font':['Open Sans Semibold'],'text-size':12},paint:{'text-color':'#fff'}});
    m.addLayer({id:'home-dots',type:'circle',source:'homes',filter:['!',['has','point_count']],paint:{'circle-radius':5,'circle-color':'#c36b4e','circle-stroke-width':2,'circle-stroke-color':'#fff'}});
    m.addLayer({id:'home-values',type:'symbol',source:'homes',filter:['!',['has','point_count']],layout:{'text-field':['get','label'],'text-font':['Open Sans Semibold'],'text-size':12,'text-offset':[0,-1.1],'text-padding':8},paint:{'text-color':'#244d46','text-halo-color':'#fff','text-halo-width':6,'text-halo-blur':0}});
    m.addSource('selection',{type:'geojson',data:empty});m.addLayer({id:'selected-fill',type:'fill',source:'selection',paint:{'fill-color':'#ce7959','fill-opacity':.28}});m.addLayer({id:'selected-line',type:'line',source:'selection',paint:{'line-color':'#a74c30','line-width':3}});
    for(const id of ['home-values','home-dots']){m.on('click',id,(e:any)=>{if(e.features?.[0])callbacks.current.onSelect(e.features[0].properties.id);});m.on('mouseenter',id,()=>m.getCanvas().style.cursor='pointer');m.on('mouseleave',id,()=>m.getCanvas().style.cursor='');}
    m.on('click','clusters',async(e:any)=>{const f=e.features[0];const z=await m.getSource('homes').getClusterExpansionZoom(f.properties.cluster_id);m.easeTo({center:f.geometry.coordinates,zoom:z});});
    setReady(true);const b=m.getBounds();callbacks.current.onBounds([b.getWest(),b.getSouth(),b.getEast(),b.getNorth()].join(','));
   });m.on('moveend',()=>{const b=m.getBounds();callbacks.current.onBounds([b.getWest(),b.getSouth(),b.getEast(),b.getNorth()].join(','));});
   m.on('error',(e:any)=>{if(e.error?.message?.includes('WebGL'))setError('Your browser could not start the map. Property search and comparisons are still available.');});
  }catch{setError('The map could not load. Try refreshing; property search is still available.');}}
  if((window as any).maplibregl)init();else {const script=document.createElement('script');script.src='/maplibre-gl.js';script.onload=init;script.onerror=()=>setError('The map renderer could not load. Please refresh.');document.head.appendChild(script);}
  return()=>{disposed=true;map.current?._harbourResize?.disconnect();map.current?.remove();map.current=null;};
 },[]);
 useEffect(()=>{if(!ready)return;map.current.getSource('homes').setData({type:'FeatureCollection',features:properties.filter(p=>p.lon&&p.lat).map(p=>({type:'Feature',geometry:{type:'Point',coordinates:[p.lon,p.lat]},properties:{id:p.id,label:p.assessed==null?'':money(p.assessed,true)}}))});},[properties,ready]);
 useEffect(()=>{if(ready)map.current.setLayoutProperty('home-values','visibility',showValues?'visible':'none');},[ready,showValues]);
 useEffect(()=>{if(ready)map.current.getSource('selection').setData(selected?.parcel?{type:'FeatureCollection',features:[{type:'Feature',geometry:selected.parcel,properties:{}}]}:empty);},[selected,ready]);
 useEffect(()=>{if(!ready||!flyTo)return;addressPin.current?.remove();if(flyTo.label){addressPin.current=new (window as any).maplibregl.Marker({color:'#244d46'}).setLngLat(flyTo.center).setPopup(new (window as any).maplibregl.Popup().setText(flyTo.label+' - NAR address location')).addTo(map.current);}if(flyTo.bounds)map.current.fitBounds(flyTo.bounds,{padding:65,duration:900,maxZoom:16});else map.current.flyTo({center:flyTo.center,zoom:flyTo.zoom||15.6,duration:900});},[flyTo,ready]);
 useEffect(()=>{if(!ready)return;const m=map.current;let cancelled=false;
  for(const id of ['neighbourhoods','municipalities','parks','schools','floodplain','zoning','heritage']){
   const visible=layerNames.includes(id);
   if(m.getLayer(id)){m.setLayoutProperty(id,'visibility',visible?'visible':'none');if(m.getLayer(id+'-outline'))m.setLayoutProperty(id+'-outline','visibility',visible?'visible':'none');}
   if(!visible)continue;
   fetch('/api/layers/'+id+'?municipality='+encodeURIComponent(municipality||'')).then(r=>r.json()).then(data=>{
    if(cancelled||!map.current)return;
    if(m.getSource(id)){m.getSource(id).setData(data);return;}
    m.addSource(id,{type:'geojson',data});const points=['parks','schools','heritage'].includes(id);const colour:Record<string,string>={neighbourhoods:'#3c7165',municipalities:'#43685d',parks:'#6b8968',schools:'#bd8a50',floodplain:'#4b91bb',zoning:'#9b81ac',heritage:'#bd7357'};
    m.addLayer(points?{id,type:'circle',source:id,paint:{'circle-radius':5,'circle-color':colour[id],'circle-stroke-color':'#fff','circle-stroke-width':1.5}}:{id,type:'fill',source:id,paint:{'fill-color':colour[id],'fill-opacity':id==='neighbourhoods'||id==='municipalities'?.025:.22}},'clusters');
    if(!points)m.addLayer({id:id+'-outline',type:'line',source:id,paint:{'line-color':colour[id],'line-width':1.2,'line-opacity':.55,'line-dasharray':[3,3]}},'clusters');
    if(!boundLayers.current.has(id)){
     boundLayers.current.add(id);
     if(id==='neighbourhoods')m.on('click',id,(e:any)=>{if(m.queryRenderedFeatures(e.point,{layers:['home-dots','home-values','clusters']}).length)return;const p=e.features[0].properties;callbacks.current.onArea(p.name,p.municipality||'Vancouver');});
     else m.on('click',id,(e:any)=>{if(m.queryRenderedFeatures(e.point,{layers:['home-dots','home-values','clusters']}).length)return;new (window as any).maplibregl.Popup().setLngLat(e.lngLat).setText(e.features[0].properties.name||e.features[0].properties.zone||id).addTo(m);});
    }
   }).catch(()=>{if(!cancelled)setError('One map layer is temporarily unavailable.');});
  }return()=>{cancelled=true;};
 },[layerNames,ready,municipality]);
 const changeBase=()=>{setSatellite(v=>!v);if(ready){map.current.setLayoutProperty('satellite','visibility',satellite?'none':'visible');map.current.setLayoutProperty('streets','visibility',satellite?'visible':'none');}};
 return <div className="map-wrapper"><div className="map-canvas" ref={el} aria-label="Interactive map of Metro Vancouver properties"/>{!ready&&!error&&<div className="map-loading"><span className="loader"/> Getting your bearings…</div>}{error&&<div className="map-error">{error}<button onClick={()=>setError('')} aria-label="Dismiss map message"><X size={14}/></button></div>}
  <div className="map-control-left"><button className="map-button" onClick={()=>setLayersOpen(!layersOpen)}><Layers size={17}/> Layers</button>{layersOpen&&<div className="layer-menu"><div className="panel-title">Map layers <button onClick={()=>setLayersOpen(false)} aria-label="Close layers"><X size={15}/></button></div>{[['neighbourhoods','Neighbourhood boundaries',MapIcon],['municipalities','Municipal boundaries',MapIcon],['parks','Parks & green space',Trees],['schools','Schools',School],['floodplain','Designated floodplain',Droplets],['zoning','Zoning districts',Layers],['heritage','Heritage register',Building2]].map(([id,label,Icon]:any)=><button key={id} onClick={()=>setLayerNames(layerNames.includes(id)?layerNames.filter(x=>x!==id):[...layerNames,id])}><Icon size={16}/><span>{label}</span><span className={'checkbox '+(layerNames.includes(id)?'checked':'')}>{layerNames.includes(id)&&<Check size={12}/>}</span></button>)}<label className="layer-values"><input type="checkbox" checked={showValues} onChange={e=>setShowValues(e.target.checked)}/> Show assessed values</label><p>Coverage varies by municipality.</p></div>}</div>
  <div className="map-control-right"><div className="zoom-group"><button aria-label="Zoom in" onClick={()=>map.current?.zoomIn()}><Plus size={19}/></button><button aria-label="Zoom out" onClick={()=>map.current?.zoomOut()}><Minus size={19}/></button></div><button className="map-square" aria-label="Show Greater Vancouver" onClick={()=>map.current?.fitBounds([[-123.4,49.0],[-122.3,49.48]],{padding:30})}><Expand size={18}/></button></div>
  <button className={'base-toggle '+(satellite?'satellite':'')} onClick={changeBase}><span className="mini-map"/><span>{satellite?'Street map':'Satellite'}</span></button><div className="map-legend"><span className="legend-dot"/> {showValues?'Assessed values · CAD':'Property records'}</div>
 </div>;
}
