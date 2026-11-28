// Browser smoke test of the published page: loads it in headless Chromium the way a reader would and fails if the
// page throws errors, a data file does not load, a key section is empty, values like "undefined" or "NaN" show up,
// or the layout overflows a phone screen. Usage: node scripts/page_test.mjs [url]   (needs the playwright package)
import { chromium } from 'playwright';

const url = process.argv[2] || 'https://jhyunkn.github.io/market-analysis/';
const problems = [];
const browser = await chromium.launch();

async function load(width) {
  const page = await browser.newPage({ viewport: { width, height: 900 } });
  page.on('pageerror', (e) => problems.push(`${width}px: page error: ${e.message}`));
  page.on('console', (m) => { if (m.type() === 'error') problems.push(`${width}px: console error: ${m.text().slice(0, 200)}`); });
  page.on('response', (r) => { if (/\/data\/[^?]+\.json/.test(r.url()) && r.status() >= 400) problems.push(`${width}px: ${r.url().split('/').pop()} returned ${r.status()}`); });
  await page.goto(url + (url.includes('?') ? '&' : '?') + 't=' + Date.now(), { waitUntil: 'networkidle', timeout: 60000 });
  await page.waitForTimeout(3000);
  return page;
}

const page = await load(1280);
const text = async (sel) => (await page.locator(sel).first().innerText().catch(() => '')).trim();
const body = await page.locator('body').innerText();
for (const bad of ['undefined', 'NaN', '[object Object]', 'Infinity']) if (body.includes(bad)) problems.push(`page text contains "${bad}"`);
const raw = body.match(/\d\.\d{6,}/); if (raw) problems.push(`unrounded number on the page: ${raw[0]}`);
const negZero = body.match(/[−-]0(\.0+)?%/); if (negZero) problems.push(`"${negZero[0]}" on the page (should read 0%)`);
const failedParts = await page.locator('.drawfail').count(); if (failedParts) problems.push(`${failedParts} part(s) of the page could not be drawn (see console errors)`);
if (/failed to load/i.test(await text('[data-draw=freshness]'))) problems.push('freshness line says data failed to load');
for (const d of ['rtread', 'desksum', 'livestrip', 'stread', 'stbeat', 'valread', 'cyread']) {
  const t = await text(`[data-draw=${d}]`);
  if (!t || t === '—' || /^Loading/.test(t)) problems.push(`section "${d}" is empty`);
}
const items = await page.locator('[data-draw=rtlong] .rtitem, [data-draw=rtshort] .rtitem').count();
if (!items && !/No stock passes/.test(await text('[data-draw=rtlong]'))) problems.push('no stock ratings listed');
const rows = await page.locator('#oltable tbody tr').count();
if (rows < 100) problems.push(`stock table has only ${rows} rows`);
const ratedRows = await page.locator('[data-draw=rttable] tbody tr').count();
if (ratedRows < 100) problems.push(`ratings table has only ${ratedRows} rows`);
if (items) {
  await page.locator('[data-draw=rtlong] .rtitem, [data-draw=rtshort] .rtitem').first().click();
  await page.waitForTimeout(1500);
  if (!/Long-term rating/i.test(await text('[data-draw=olrate]'))) problems.push('clicking a rated stock did not show its rating card');
}

// open the folded background sections too, so their charts and lazily loaded data get exercised
await page.evaluate(() => document.querySelectorAll('.bggroup').forEach((g) => window.maOpenGroup && window.maOpenGroup(g.id)));
await page.waitForTimeout(5000);
// press every button and fold, and step through every dropdown: any error they raise is caught by the console listener above
const clicked = await page.evaluate(async () => {
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms)); let k = 0;
  for (const el of [...document.querySelectorAll('button, summary')]) { if (el.id === 'themebtn') continue; el.click(); k++; await sleep(15); }
  for (const s of [...document.querySelectorAll('select')]) for (const o of [...s.options].slice(0, 3)) { s.value = o.value; s.dispatchEvent(new Event('change', { bubbles: true })); await sleep(100); }
  document.querySelectorAll('.bggroup').forEach((g) => window.maOpenGroup && window.maOpenGroup(g.id)); return k; });
if (clicked < 50) problems.push(`only ${clicked} buttons found to press`);
await page.waitForTimeout(3000);
for (const d of ['mhgauges', 'strip']) if (!((await page.locator(`[data-draw=${d}]`).first().textContent().catch(() => '')) || '').trim() && !(await page.locator(`[data-draw=${d}] svg`).count())) problems.push(`background section "${d}" is empty after opening`);

// accessibility: serious or critical WCAG A/AA problems (axe-core, when installed next to playwright)
try {
  const { createRequire } = await import('node:module'); const fs = await import('node:fs');
  const axePath = createRequire(import.meta.url.startsWith('file:') ? process.cwd() + '/' : import.meta.url).resolve('axe-core/axe.min.js');
  await page.evaluate(() => document.querySelectorAll('details').forEach((d) => { d.open = true; }));
  await page.addScriptTag({ content: fs.readFileSync(axePath, 'utf8') });
  const bad = await page.evaluate(async () => (await axe.run(document, { runOnly: ['wcag2a', 'wcag2aa'] })).violations.filter((v) => v.impact === 'serious' || v.impact === 'critical').map((v) => `${v.id} (${v.nodes.length}): ${v.nodes[0].target.join(' ')}`));
  bad.forEach((b) => problems.push('accessibility: ' + b));
} catch (e) { if (!/Cannot find module/.test(String(e))) problems.push('accessibility check failed to run: ' + String(e).slice(0, 120)); }

const phone = await load(390);
const over = await phone.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
if (over > 1) problems.push(`page is ${over}px wider than a 390px phone screen`);

await browser.close();
if (problems.length) { console.log(`page test: ${problems.length} problem(s)\n- ` + [...new Set(problems)].join('\n- ')); process.exit(1); }
console.log(`page test: ok (${rows} stocks, ${ratedRows} rated, ${items} on the Strong lists)`);
