import {chromium} from '@playwright/test';
const browser=await chromium.launch({executablePath:'C:/Program Files/Google/Chrome/Application/chrome.exe',headless:true});
const page=await browser.newPage({viewport:{width:1440,height:1000}});
page.on('request',r=>{if(r.url().includes('/api/'))console.log('API_REQUEST',r.url())});page.on('response',r=>{if(r.url().includes('/api/'))console.log('API_RESPONSE',r.status(),r.url())});
page.on('pageerror',e=>console.log('PAGE_ERROR',e.message));page.on('console',m=>{if(m.type()==='error')console.log('CONSOLE_ERROR',m.text().slice(0,1000));});page.on('requestfailed',r=>console.log('REQUEST_FAILED',r.url(),r.failure()?.errorText));
await page.goto(process.env.TEST_URL||'http://127.0.0.1:5188/',{waitUntil:'domcontentloaded'});await page.waitForTimeout(10000);
console.log('BODY',(await page.locator('body').innerText()).slice(0,4000));await page.screenshot({path:'outputs/inspect.png',fullPage:true,timeout:15000});await browser.close();
