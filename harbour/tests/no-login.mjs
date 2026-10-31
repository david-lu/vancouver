import assert from 'node:assert/strict';
import { chromium } from '@playwright/test';

// Deliberately no Sites bypass token, authorization headers, or sign-in navigation.
const base=process.env.TEST_URL||'http://127.0.0.1:5188';
const browser=await chromium.launch({executablePath:'C:/Program Files/Google/Chrome/Application/chrome.exe',headless:true});
const first=await browser.newContext({viewport:{width:1440,height:1000}});
const second=await browser.newContext();
const forged=await browser.newContext();
const property='750097550000';
let searchId;
try{
 const page=await first.newPage();
 page.on('pageerror',e=>console.error('BROWSER_ERROR',e.message));
 const response=await page.goto(base+'/',{waitUntil:'domcontentloaded'});
 assert.equal(response.status(),200);assert.equal(new URL(page.url()).origin,new URL(base).origin);
 await page.locator('.map-canvas').waitFor({timeout:60000});
 assert.equal(await page.locator('a[href*="signin-with-chatgpt"]').count(),0);
 const me=await first.request.get(base+'/api/me');
 assert.equal(me.status(),200);assert.equal((await me.json()).loginRequired,false);
 const cookie=(await first.cookies()).find(c=>c.name==='harbour_workspace');
 assert.ok(cookie?.httpOnly);assert.equal(cookie.sameSite,'Lax');
 if(base.startsWith('https:'))assert.ok(cookie.secure);
 const save=await first.request.post(base+'/api/saved',{data:{id:property,note:'Anonymous workspace privacy test'}});
 assert.equal(save.status(),200);
 const saved=await (await first.request.get(base+'/api/saved')).json();
 assert.ok(saved.some(s=>s.id===property&&s.note==='Anonymous workspace privacy test'));
 await page.reload({waitUntil:'domcontentloaded'});
 assert.ok((await (await first.request.get(base+'/api/saved')).json()).some(s=>s.id===property));
 assert.deepEqual(await (await second.request.get(base+'/api/saved')).json(),[]);
 await second.request.delete(base+'/api/saved/'+property);
 assert.ok((await (await first.request.get(base+'/api/saved')).json()).some(s=>s.id===property));
 const otherCookie=(await second.cookies()).find(c=>c.name==='harbour_workspace');
 // Use the target's ID with another visitor's signature: must never expose target data.
 await forged.addCookies([{...cookie,value:cookie.value.split('.')[0]+'.'+otherCookie.value.split('.')[1]}]);
 assert.deepEqual(await (await forged.request.get(base+'/api/saved')).json(),[]);
 const search=await first.request.post(base+'/api/searches',{data:{name:'Anonymous test search',filters:{municipality:'Vancouver'}}});
 assert.equal(search.status(),200);searchId=(await search.json()).id;
 assert.ok((await (await first.request.get(base+'/api/searches')).json()).some(s=>s.id===searchId));
 assert.deepEqual(await (await second.request.get(base+'/api/searches')).json(),[]);
 assert.equal((await first.request.post(base+'/api/saved',{headers:{Origin:'https://unrelated.example'},data:{id:property}})).status(),403);
 assert.equal((await second.request.post(base+'/api/import',{data:{}})).status(),401);
 await page.getByRole('button',{name:'More tools',exact:true}).click();
 await page.getByRole('button',{name:'Your research workspace',exact:true}).click();
 await page.getByText('No account needed.',{exact:false}).waitFor();
 await page.getByRole('button',{name:'Close dialog'}).click();
 await page.getByLabel('Ask Harbour assistant').fill('What is the median assessed value in Kitsilano in the imported data? Cite the source.');
 const answerPromise=page.waitForResponse(r=>r.url().endsWith('/api/agent')&&r.request().method()==='POST',{timeout:120000});
 await page.getByRole('button',{name:'Send message',exact:true}).click();
 const answerResponse=await answerPromise;const answer=await answerResponse.json();
 assert.equal(answerResponse.status(),200,answer.error);
 assert.ok(answer.answer?.length>40);assert.ok(answer.citations?.length>0);
 await page.locator('.message.assistant .citations a').first().waitFor({timeout:10000});
 console.log('NO_LOGIN_CHECKS_PASSED',new URL(base).host,'public page; isolated, persistent saved homes, notes and searches; forged-cookie rejection; CSRF/import protection; real sourced AI response');
 console.log('AI_RESULT',answer.answer.slice(0,250));
}finally{
 await first.request.delete(base+'/api/saved/'+property).catch(()=>{});
 if(searchId)await first.request.delete(base+'/api/searches/'+searchId).catch(()=>{});
 await browser.close();
}
