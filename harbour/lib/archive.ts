import { env } from 'cloudflare:workers';
export async function historical(query:string){
 if(!query.trim())return {matches:0,properties:[]};
 const result=await env.DB!.prepare("SELECT * FROM archive WHERE id=? OR pid=? OR address LIKE ? ESCAPE '\\' LIMIT 6").bind(query,query,'%'+query.toUpperCase().replace(/[\\%_]/g,'\\$&')+'%').all<Record<string,any>>();
 const rows=result.results||[];const properties=[];
 for(const row of rows.slice(0,2)){
  const object=await env.BUCKET?.get('assessment-history/'+row.shard+'.jsonl.gz');let records:Record<string,any>[]=[];
  if(object){
   const reader=object.body.pipeThrough(new DecompressionStream('gzip')).pipeThrough(new TextDecoderStream()).getReader();let pending='';
   const accept=(line:string)=>{if(!line)return;const record=JSON.parse(line);if(String(record.folio)===String(row.id))records.push(record);};
   while(true){const {value,done}=await reader.read();if(done)break;pending+=value;let end;while((end=pending.indexOf('\n'))>=0){accept(pending.slice(0,end));pending=pending.slice(end+1);}}
   accept(pending);
  }
  properties.push({...row,years:JSON.parse(row.years),records,archiveAvailable:!!object});
 }
 return {matches:rows.length,properties,source:'Original complete City of Vancouver assessment exports, 2006 onward. Includes historic-only folios.'};
}
