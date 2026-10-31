type RecordData=Record<string,any>;
const pidKey=(value:unknown)=>String(value||'').replace(/\D/g,'');
const positive=(value:unknown)=>typeof value==='number'&&Number.isFinite(value)&&value>=0?value:null;

/** Public municipal property lookups; retain only housing fields, never owner details. */
export async function northVanAssessment(property:RecordData):Promise<RecordData|null>{
 const pid=pidKey(property.pid);if(pid.length!==9)return null;
 if(property.features?.live_lookup?.checked&&Date.now()-Date.parse(property.features.live_lookup.checked)<86400000)return null;
 const formatted=pid.slice(0,3)+'-'+pid.slice(3,6)+'-'+pid.slice(6);
 let url='',land:number|null=null,improvement:number|null=null,total:number|null=null,built:number|null=null;
 if(property.municipality==='North Vancouver District')url='https://geoweb.dnv.org/properties/api/properties/propertyservice/?dblink=null&ltopid='+formatted;
 else if(property.municipality==='North Vancouver City'&&property.features?.cnv_roll)url='https://gisext2.cnv.org/cnvrest/property/roll/'+encodeURIComponent(property.features.cnv_roll)+'/pid/'+formatted;
 if(!url)return null;
 try{
  const response=await fetch(url,{headers:{Accept:'application/json'},signal:AbortSignal.timeout(7000)});if(!response.ok)return null;
  const data:any=await response.json();
  if(property.municipality==='North Vancouver District'){
   const records=(Array.isArray(data)?data:[data]).flatMap((group:any)=>group.properties||[]);
   const record=records.find((p:any)=>pidKey(p.ltopid)===pid);if(!record)return null;
   const a=record.details?.assessment;if(!a||a.taxConsolidation)return null;
   land=positive(a.landValue);improvement=positive(a.buildingValue);total=positive(a.totalValue);
   const year=positive(record.details?.building?.yearBuilt);if(year&&year>1700&&year<=new Date().getFullYear())built=year;
  }else{
   if(pidKey(data.pid)!==pid)return null;
   land=positive(data.valuationLand);improvement=positive(data.valuationImprovement);
   if(land!=null&&improvement!=null)total=land+improvement;
  }
  if(total==null||total<=0||land==null||improvement==null||Math.abs(total-land-improvement)>1)return null;
  const checked=new Date().toISOString();
  return {...property,assessed:total,land,improvement,built:built||property.built,year:null,features:{...property.features,assessment_label:'Reported assessment (year not supplied)',assessment_source_url:url,live_lookup:{checked,url,match:'Exact provincial PID',year:null},municipality_context_sources:[...(property.features?.municipality_context_sources||[]),{title:property.municipality+' property assessment',url}]}};
 }catch{return null;}
}
