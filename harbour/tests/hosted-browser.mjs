import {chromium} from '@playwright/test';
import assert from 'node:assert/strict';
const cfg=await new Promise((resolve,reject)=>{let s='';const tty=process.stdin.isTTY;const read=x=>{s+=x;if(/[\r\n]/.test(s)){process.stdin.off('data',read);if(tty)process.stdin.setRawMode(false);process.stdin.pause();try{resolve(JSON.parse(s.trim()));}catch{reject(Error('Invalid configuration'));}}};if(tty)process.stdin.setRawMode(true);process.stdin.setEncoding('utf8');process.stdin.on('data',read);process.stdin.resume();process.stderr.write('Ready for hosted browser JSON on stdin (input is hidden).\n');});
const browser=await chromium.launch({executablePath:'C:/Program Files/Google/Chrome/Application/chrome.exe',headless:true});
const context=await browser.newContext({viewport:{width:1440,height:1000}});
// Scope the private-site credential strictly to this origin, never imagery or tiles.
if(cfg.bypass)await context.route(url=>url.origin===new URL(cfg.url).origin,route=>route.continue({headers:{...route.request().headers(),'OAI-Sites-Authorization':'Bearer '+cfg.bypass}}));
const page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
try{
 const map=page.waitForResponse(r=>r.url().includes('/api/properties?')&&r.url().includes('bbox=')&&r.status()===200,{timeout:60000});await page.goto(cfg.url);await map;await page.waitForTimeout(1500);
 await page.locator('.results-pill').click();await page.locator('.property-grid .property-card').first().waitFor();assert.ok(await page.locator('.property-grid .property-card').count()>1);await page.screenshot({path:'outputs/harbour-hosted-desktop.png'});
 await page.locator('.card-foot button').nth(0).click();await page.locator('.card-foot button').nth(1).click();await page.getByRole('button',{name:'Compare side by side'}).click();assert.equal(await page.locator('.comparison-table thead th').count(),3);await page.getByRole('button',{name:'Close dialog'}).click();
 await page.getByRole('button',{name:'More tools',exact:true}).click();await page.getByRole('button',{name:'Data & coverage',exact:true}).click();assert.equal(await page.locator('.coverage-grid button').count(),23);await page.getByRole('button',{name:'Browse the complete raw-data archive'}).click();await page.getByLabel('Search raw archive').fill('property-tax-report.csv');await page.getByRole('link',{name:'property-tax-report.csv',exact:true}).waitFor();await page.screenshot({path:'outputs/harbour-hosted-archive.png'});await page.getByRole('button',{name:'Close dialog'}).click();
 await page.getByLabel('Municipality').selectOption('Burnaby');await page.getByLabel('Search properties').fill('Kingsway');await page.locator('.address-matches button').first().waitFor({timeout:20000});await page.locator('.address-matches button').first().click();await page.waitForTimeout(1500);
 await page.setViewportSize({width:390,height:844});await page.reload();await page.locator('.maplibregl-canvas').waitFor();await page.waitForTimeout(1500);await page.screenshot({path:'outputs/harbour-hosted-mobile.png'});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
 assert.deepEqual(errors,[]);console.log('HOSTED_BROWSER_CHECKS_PASSED');
}finally{await browser.close();}
