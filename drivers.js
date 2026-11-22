/* News, market drivers, macro, and what moved each stock. All feed text is inserted as text, never as HTML. */
(function () {
  const D = () => { const d = dash.data('drivers'); return d.status === 'ok' && d.data && d.data[0] ? d.data[0] : null; };
  const META = () => dash.data('drivers').meta || {};
  const failed = () => dash.data('drivers').status === 'error';
  const mk = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };
  const safe = (u) => { try { const x = new URL(u); return x.protocol === 'https:' ? x.href : null; } catch (e) { return null; } };
  const sign = (v) => (v > 0 ? '+' : v < 0 ? '−' : '');
  const num = (v, d) => Math.abs(v).toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d });
  const chg = (v, unit) => { if (v == null) return '—'; const r = Number(v.toFixed(unit === 'bp' ? 0 : 1)); return unit === 'bp' ? sign(r) + num(r, 0) + ' bp' : unit === 'pts' ? sign(r) + num(r, 1) : sign(r) + num(r, 1) + '%'; };
  const tone = (v, good) => (v == null || Math.abs(v) < 0.05 || good == null ? '' : (v > 0) === good ? 'drvup' : 'drvdown');
  const DOLLAR = new Set(['wti', 'brent', 'natgas', 'gold', 'copper', 'btc', 'ndx', 'small', 'defense', 'china', 'taiwan', 'japan', 'europe', 'em', 'semis', 'banks', 'junk']);
  const level = (r) => { if (r.unit === 'bp') return r.last.toFixed(2) + '%'; const d = r.last >= 1000 ? 0 : 2; return (DOLLAR.has(r.id) ? '$' : '') + r.last.toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d }); };
  const ago = (iso) => { const h = (Date.now() - new Date(iso)) / 36e5; return h < 1 ? Math.max(1, Math.round(h * 60)) + 'm ago' : h < 48 ? Math.round(h) + 'h ago' : Math.round(h / 24) + 'd ago'; };
  const themeName = (p, id) => (p.themes.find((t) => t.id === id) || {}).name || id;
  const goStock = (s) => { if (location.hash === '#' + s) dash.draw('stockdrivers'); else location.hash = s; document.getElementById('fan')?.scrollIntoView({ block: 'start', behavior: 'smooth' }); };
  const symBtn = (s) => { const b = mk('button', 'drvsym', s); b.type = 'button'; b.addEventListener('click', () => goStock(s)); return b; };
  const pending = (el, msg) => { el.replaceChildren(mk('p', 'sub', failed() ? 'News and macro data failed to load.' : msg || 'Loading…')); };

  const staleReasons = (p) => {
    const m = META(), out = [], hours = (Date.now() - new Date(m.generated_at)) / 36e5;
    if (!(hours <= 8)) out.push('this section was last updated ' + (Number.isFinite(hours) ? Math.round(hours) + ' hours ago' : 'at an unknown time'));
    if ((Date.now() - new Date(p.asof + 'T21:00:00Z')) / 864e5 > 5) out.push('its latest market close is ' + p.asof);
    if (m.news_stale) out.push('news feeds were unavailable, so stories are from an earlier update');
    const old = p.macro.filter((x) => x.stale).length; if (old) out.push(old + ' macro readings could not be refreshed');
    return out;
  };
  const quickLine = (p) => {
    const g = (id) => p.drivers.find((d) => d.id === id), spx = g('spx');
    return 'S&P ' + chg(spx.chg_1d, 'pct') + ' day, ' + chg(spx.chg_1w, 'pct') + ' week · VIX ' + g('vix').last.toFixed(1) + ' · 10-yr ' + g('y10').last.toFixed(2) + '% · Oil ' + chg(g('wti').chg_1w, 'pct') + ' · Chips ' + chg(g('semis').chg_1w, 'pct') + ' this week';
  };
  let readOpen = false;
  dash.draw('drvread', (el) => {
    const p = D(); if (!p) return pending(el, 'Loading the market read…');
    const label = p.regime.label, cls = /off|selloff/i.test(label) ? 'bad' : /rally/i.test(label) ? 'ok' : 'warn';
    const box = mk('div', 'drvbrief' + (readOpen ? ' open' : '')), stale = staleReasons(p);
    if (stale.length) { const b = mk('div', 'drvstale', 'Out of date: ' + stale.join('; ') + '.'); b.setAttribute('role', 'alert'); box.appendChild(b); }
    const top = mk('div', 'drvregime'), text = mk('span'); text.append(p.regime.detail); if (p.regime.flags.length) text.appendChild(mk('span', 'drvflags', ' Also, ' + p.regime.flags.join(', ') + '.')); top.append(mk('span', 'drvpill ' + cls, label), text);
    const read = mk('div', 'drvread drvfull'); p.read.forEach((t) => read.appendChild(mk('p', '', t)));
    const m = META(), when = new Date(m.generated_at).toLocaleString('en-US', { timeZone: 'America/New_York', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' });
    const more = mk('button', 'drvbtn drvmore', readOpen ? 'Show less' : 'Show full read'); more.type = 'button'; more.setAttribute('aria-expanded', String(readOpen)); more.addEventListener('click', () => { readOpen = !readOpen; dash.draw('drvread'); });
    box.append(top, mk('p', 'drvquick', quickLine(p)), read, more, mk('p', 'drvnote', 'Shows what moved together, not proof of cause. Updated ' + when + ' ET from ' + m.feeds_ok + ' news feeds (last ' + m.news_window_hours + ' hours).'));
    el.replaceChildren(box);
  });

  let theme = '', newsLimit = 8;
  function ranked(p) {
    if (theme) return p.news.filter((n) => n.themes.includes(theme)).slice(0, 20);
    const order = ['geopolitics', 'fed', 'ai', 'energy', 'trade', 'inflation', 'credit', 'markets'], seen = new Set(), out = [];
    for (let pass = 0; pass < 2; pass++) order.forEach((t) => { const n = p.news.find((x) => x.themes.includes(t) && !seen.has(x)); if (n) { seen.add(n); out.push(n); } });
    p.news.forEach((n) => { if (!seen.has(n) && out.length < 24) { seen.add(n); out.push(n); } });
    return out;
  }
  dash.draw('drvnews', (el) => {
    const p = D(); if (!p) return pending(el);
    const btns = mk('div', 'drvbtns'); const add = (id, label) => { const b = mk('button', 'drvbtn', label); b.type = 'button'; b.setAttribute('aria-pressed', String(theme === id)); b.addEventListener('click', () => { theme = id; newsLimit = 8; dash.draw('drvnews'); }); btns.appendChild(b); };
    add('', 'Top stories'); p.themes.filter((t) => t.count).forEach((t) => add(t.id, t.name));
    const nodes = [btns];
    if (theme) { const t = p.themes.find((x) => x.id === theme), r = mk('div', 'drvreact'); r.appendChild(mk('span', '', 'Market reaction this week:')); t.proxies.forEach((x) => { const s = mk('span'); s.append(x.name + ' ', mk('b', tone(x.chg_1w, x.up_good), chg(x.chg_1w, x.unit))); r.appendChild(s); }); nodes.push(r); }
    const ul = mk('ul', 'drvnews');
    const stories = ranked(p);
    stories.slice(0, newsLimit).forEach((n) => { const li = mk('li'), href = safe(n.link), a = mk(href ? 'a' : 'span', 't', n.title); if (href) { a.href = href; a.target = '_blank'; a.rel = 'noopener noreferrer'; }
      const m = mk('div', 'drvmeta'); m.append(mk('span', '', n.source + (n.sources > 1 ? ' +' + (n.sources - 1) + ' more' : '')), mk('span', '', ago(n.published))); n.themes.forEach((t) => m.appendChild(mk('span', 'drvtag', themeName(p, t)))); (n.tickers || []).forEach((s) => { if (p.stocks[s]) m.appendChild(symBtn(s)); });
      li.append(a, m); ul.appendChild(li); });
    if (!ul.children.length) ul.appendChild(mk('li', 'sub', 'No important stories in this category right now.'));
    nodes.push(ul);
    if (stories.length > 8) { const open = newsLimit >= stories.length, b = mk('button', 'drvbtn drvshow', open ? 'Show fewer' : 'Show ' + (stories.length - newsLimit) + ' more'); b.type = 'button'; b.addEventListener('click', () => { newsLimit = open ? 8 : stories.length; dash.draw('drvnews'); }); nodes.push(b); }
    el.replaceChildren(...nodes);
  });

  let factor = 'chips';
  dash.draw('drvwho', (el) => {
    const p = D(); if (!p) return pending(el);
    const btns = mk('div', 'drvbtns'); p.factors.filter((f) => f.id !== 'market' && f.id !== 'broad').forEach((f) => { const b = mk('button', 'drvbtn', f.name); b.type = 'button'; b.setAttribute('aria-pressed', String(factor === f.id)); b.addEventListener('click', () => { factor = f.id; dash.draw('drvwho'); }); btns.appendChild(b); });
    const f = p.factors.find((x) => x.id === factor);
    const lev = new Set((dash.data('outlook_summary').data || []).filter((r) => r.group === 'Leveraged ETFs').map((r) => r.symbol));
    const rows = Object.entries(p.stocks).filter(([s]) => !lev.has(s) && s !== 'SPY' && !(factor === 'chips' && s === 'SMH')).map(([s, x]) => ({ s, e: x.exposures.find((y) => y.id === factor), week: x.week.total })).filter((r) => r.e && Math.abs(r.e.t) >= 3);
    const list = (title, items) => { const box = mk('div'); box.appendChild(mk('h4', '', title)); if (!items.length) box.appendChild(mk('p', 'sub', 'None clear.')); items.forEach((r) => { const row = mk('div', 'whorow'); row.append(symBtn(r.s), mk('span', '', sign(r.e.beta) + num(r.e.beta, 2) + '%'), mk('span', tone(r.week, true), chg(r.week, 'pct'))); box.appendChild(row); }); return box; };
    const up = rows.filter((r) => r.e.beta > 0).sort((a, b) => b.e.beta - a.e.beta).slice(0, 8), down = rows.filter((r) => r.e.beta < 0).sort((a, b) => a.e.beta - b.e.beta).slice(0, 8);
    const grid = mk('div', 'drvwho'); grid.append(list('Tends to rise with it', up), list('Tends to fall with it', down));
    el.replaceChildren(btns, mk('p', 'sub', 'Typical stock move when ' + f.phrase + ', with the S&P held constant (2 years of daily data), then the stock’s move this week. Click a stock to open it.'), grid);
  });

  const spk = (arr) => (arr || []).map((x) => [new Date(x[0] + 'T00:00:00Z'), x[1]]);
  const fmtDay = (d) => d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric', timeZone: 'UTC' });
  let picked = ['spx', 'semis', 'gold', 'wti'];
  const slot = { spx: 0, semis: 1, gold: 2, wti: 3 };
  dash.draw('drvcompare', (el) => {
    const p = D(); if (!p) return pending(el); el.replaceChildren();
    const pool = p.drivers.filter((d) => d.unit === 'pct' && d.spark && d.spark.length > 2);
    picked = picked.filter((id) => pool.some((d) => d.id === id));
    const chips = mk('div', 'drvbtns'); chips.style.flexWrap = 'wrap';
    pool.forEach((d) => { const b = mk('button', 'drvbtn', d.name); b.type = 'button'; b.setAttribute('aria-pressed', String(picked.includes(d.id)));
      b.addEventListener('click', () => { if (picked.includes(d.id)) { picked = picked.filter((x) => x !== d.id); delete slot[d.id]; } else { if (picked.length >= 4) { delete slot[picked[0]]; picked = picked.slice(1); } const used = new Set(picked.map((x) => slot[x])); slot[d.id] = [0, 1, 2, 3].find((k) => !used.has(k)); picked = picked.concat(d.id); } dash.draw('drvcompare'); }); chips.appendChild(b); });
    const box = mk('div', 'lchart'); el.append(chips, box);
    if (!picked.length) { box.appendChild(mk('p', 'sub', 'Pick a market above.')); return; }
    const colors = ['var(--chart-1)', 'var(--chart-2)', 'var(--chart-3)', 'var(--chart-4)'];
    const start = new Date(Math.max(...picked.map((id) => new Date(pool.find((d) => d.id === id).spark[0][0] + 'T00:00:00Z'))));
    const series = picked.map((id, i) => { const d = pool.find((x) => x.id === id), pts = spk(d.spark).filter((q) => q[0] >= start), b = pts[0][1];
      return { name: d.name, short: d.name, color: colors[slot[id] ?? i], width: 2, points: pts.map((q) => [q[0], (q[1] / b - 1) * 100]) }; });
    window.pwr.lineChart(box, { series, time: true, aria: 'Percent change of the chosen markets over three months', xfmt: d3.utcFormat('%b %-d'), xtip: (v) => fmtDay(new Date(v)), labelW: 150, height: 250 });
  });
  dash.draw('drvranked', (el) => {
    const p = D(); if (!p) return pending(el); el.replaceChildren();
    const rows = p.drivers.filter((d) => d.unit === 'pct').sort((a, b) => b.chg_1m - a.chg_1m), other = p.drivers.filter((d) => d.unit !== 'pct');
    const lg = mk('div', 'clegend'); lg.innerHTML = '<span><i style="border-top:8px solid var(--chart-1);width:12px"></i>Past month</span><span><i style="border:0;width:8px;height:8px;border-radius:50%;background:var(--color-fg)"></i>Past week</span>'; el.appendChild(lg);
    const W = el.clientWidth || 700, rh = 19, mg = { t: 4, r: 64, b: 22, l: 128 }, H = mg.t + mg.b + rows.length * rh;
    const xs = d3.scaleLinear(d3.extent([0, ...rows.flatMap((d) => [d.chg_1m, d.chg_1w])]), [mg.l, W - mg.r]).nice(), ys = d3.scaleBand(rows.map((d) => d.id), [mg.t, H - mg.b]).padding(0.28);
    const svg = d3.select(el).append('svg').attr('width', '100%').attr('viewBox', `0 0 ${W} ${H}`).attr('role', 'img').attr('aria-label', 'Every market ranked by its one-month change, with the one-week change as a dot');
    svg.append('g').attr('class', 'gridline').attr('transform', `translate(0,${H - mg.b})`).call(d3.axisBottom(xs).ticks(6).tickSize(-(H - mg.t - mg.b)).tickFormat((v) => (v > 0 ? '+' : '') + v + '%'));
    svg.append('line').attr('x1', xs(0)).attr('x2', xs(0)).attr('y1', mg.t).attr('y2', H - mg.b).style('stroke', 'var(--cds-chart-axis)');
    const tip = window.pwr.tip(el);
    rows.forEach((d) => { const y = ys(d.id), bh = ys.bandwidth();
      svg.append('text').attr('x', mg.l - 8).attr('y', y + bh / 2).attr('dy', '0.35em').attr('text-anchor', 'end').style('font-size', '11px').style('fill', 'var(--color-fg)').text(d.name);
      svg.append('rect').attr('x', Math.min(xs(0), xs(d.chg_1m))).attr('width', Math.max(1, Math.abs(xs(d.chg_1m) - xs(0)))).attr('y', y).attr('height', bh).attr('rx', 2).style('fill', 'var(--chart-1)').style('opacity', 0.85);
      svg.append('circle').attr('cx', xs(d.chg_1w)).attr('cy', y + bh / 2).attr('r', 4).style('fill', 'var(--color-fg)').style('stroke', 'var(--color-panel)').style('stroke-width', 1.5);
      svg.append('text').attr('x', W - mg.r + 6).attr('y', y + bh / 2).attr('dy', '0.35em').style('font-size', '11px').style('fill', 'var(--color-fg-muted)').text(chg(d.chg_1m, 'pct'));
      svg.append('rect').attr('x', 0).attr('width', W).attr('y', y - 2).attr('height', bh + 4).style('fill', 'transparent')
        .on('pointermove', (ev) => { const [px, py] = d3.pointer(ev, el); tip.show([[d.name + ' (' + d.group + ')', ''], ['Last:', level(d)], ['Past day:', chg(d.chg_1d, 'pct')], ['Past week:', chg(d.chg_1w, 'pct')], ['Past month:', chg(d.chg_1m, 'pct')]], px, py); }).on('pointerleave', () => tip.hide()); });
    el.appendChild(mk('p', 'sub', 'Not on the % scale: ' + other.map((d) => d.name + ' ' + level(d) + ' (week ' + chg(d.chg_1w, d.unit) + ', month ' + chg(d.chg_1m, d.unit) + ')').join(' · ') + '.'));
  });

  const macroDate = (m) => { const d = new Date(m.date + 'T00:00:00Z'), monthly = (d - new Date(m.prior_date + 'T00:00:00Z')) / 864e5 > 20; return d.toLocaleDateString('en-US', monthly ? { month: 'short', year: '2-digit', timeZone: 'UTC' } : { month: 'short', day: 'numeric', timeZone: 'UTC' }); };
  const macroVal = (v, u) => (u === '%' ? v.toFixed(2) + '%' : u === 'k' ? Math.round(v).toLocaleString('en-US') + 'k' : u === 'T' ? '$' + v.toFixed(2) + 'T' : v.toFixed(2));
  const macroChg = (c, u) => (!c ? '0' : sign(c) + (u === 'k' ? Math.round(Math.abs(c)) + 'k' : Math.abs(c).toFixed(2)));
  dash.draw('drvmacro', (el) => {
    const p = D(); if (!p) return pending(el); el.replaceChildren(); el.className = 'lchart';
    const groups = [...new Set(p.macro.map((m) => m.group))], rows = [];
    groups.forEach((g) => { rows.push({ head: g }); p.macro.filter((m) => m.group === g).forEach((m) => rows.push(m)); });
    const lg = mk('div', 'clegend'); lg.innerHTML = '<span><i style="border:0;width:9px;height:9px;border-radius:50%;background:var(--color-ok)"></i>Latest change usually helps stocks</span><span><i style="border:0;width:9px;height:9px;border-radius:50%;background:var(--color-bad)"></i>Usually hurts</span><span><i style="border:0;width:9px;height:9px;border-radius:50%;background:var(--cds-chart-muted)"></i>Neutral or no change</span><span><i style="width:16px;border-top:1.5px solid var(--color-fg-muted)"></i>From the prior reading</span>'; el.appendChild(lg);
    const W = el.clientWidth || 700, narrow = W < 640, rh = 20, mg = { t: 18, r: narrow ? 8 : 150, b: 6, l: narrow ? 8 : 190 };
    const H = mg.t + mg.b + rows.reduce((a, r) => a + (r.head ? 22 : narrow ? rh + 14 : rh), 0);
    const xs = d3.scaleLinear([0, 100], [mg.l, W - mg.r]);
    const svg = d3.select(el).append('svg').attr('width', '100%').attr('viewBox', `0 0 ${W} ${H}`).attr('role', 'img').attr('aria-label', 'Where each macro series sits in its five-year range, and how it moved from the prior reading');
    [0, 25, 50, 75, 100].forEach((v) => { svg.append('line').attr('x1', xs(v)).attr('x2', xs(v)).attr('y1', mg.t - 4).attr('y2', H - mg.b).style('stroke', 'var(--cds-chart-grid)');
      svg.append('text').attr('x', xs(v)).attr('y', 10).attr('text-anchor', 'middle').style('font-size', '10.5px').style('fill', 'var(--color-fg-muted)').text(v === 0 ? '5-yr low' : v === 100 ? '5-yr high' : v + 'th pct'); });
    const tip = window.pwr.tip(el); let y = mg.t;
    rows.forEach((m) => {
      if (m.head) { svg.append('text').attr('x', narrow ? mg.l : 8).attr('y', y + 15).style('font-size', '10.5px').style('font-weight', 600).style('letter-spacing', '.04em').style('fill', 'var(--color-fg-muted)').text(m.head.toUpperCase()); y += 22; return; }
      const top = narrow ? y + 13 : y, cy = top + rh / 2, t = tone(m.change, m.up_good), col = t === 'drvup' ? 'var(--color-ok)' : t === 'drvdown' ? 'var(--color-bad)' : 'var(--cds-chart-muted)';
      const lab = svg.append('text').style('font-size', '11.5px').style('fill', 'var(--color-fg)').text(m.name + (m.stale ? ' (stale)' : '') + (narrow ? '  ' + macroVal(m.value, m.unit) + ' (' + macroChg(m.change, m.unit) + ')' : ''));
      if (narrow) lab.attr('x', mg.l).attr('y', y + 10); else lab.attr('x', mg.l - 10).attr('y', cy).attr('dy', '0.35em').attr('text-anchor', 'end');
      svg.append('line').attr('x1', xs(0)).attr('x2', xs(100)).attr('y1', cy).attr('y2', cy).style('stroke', 'var(--cds-chart-grid)').style('stroke-width', 5).style('stroke-linecap', 'round');
      if (m.pct_5y_prior != null && m.pct_5y_prior !== m.pct_5y) svg.append('line').attr('x1', xs(m.pct_5y_prior)).attr('x2', xs(m.pct_5y)).attr('y1', cy).attr('y2', cy).style('stroke', 'var(--color-fg-muted)').style('stroke-width', 1.5);
      svg.append('circle').attr('cx', xs(m.pct_5y)).attr('cy', cy).attr('r', 5.5).style('fill', col).style('stroke', 'var(--color-panel)').style('stroke-width', 2);
      if (!narrow) svg.append('text').attr('x', W - mg.r + 10).attr('y', cy).attr('dy', '0.35em').style('font-size', '11px').style('fill', 'var(--color-fg-muted)').text(macroVal(m.value, m.unit) + '  (' + macroChg(m.change, m.unit) + ', ' + macroDate(m) + ')');
      svg.append('rect').attr('x', 0).attr('width', W).attr('y', y).attr('height', narrow ? rh + 14 : rh).style('fill', 'transparent')
        .on('pointermove', (ev) => { const [px, py] = d3.pointer(ev, el); tip.show([[m.name, ''], ['Latest:', macroVal(m.value, m.unit) + ' (' + macroDate(m) + ')'], ['Change:', macroChg(m.change, m.unit)], ['5-year percentile:', (m.pct_5y_prior != null ? m.pct_5y_prior + ' → ' : '') + m.pct_5y]], px, py); }).on('pointerleave', () => tip.hide());
      y += narrow ? rh + 14 : rh; });
    el.appendChild(mk('p', 'sub', 'Each dot is where the latest official reading sits in its own 5-year range (left = lowest in 5 years, right = highest); the line shows where the prior reading sat. Every series on one 0–100 scale, so you can compare them directly.'));
  });

  let span = 'week';
  const pick = (p) => { const v = document.getElementById('olsel')?.value; if (v && p.stocks[v]) return v; const h = decodeURIComponent(location.hash.slice(1)).toUpperCase(); return p.stocks[h] ? h : 'SPY'; };
  dash.draw('stockdrivers', (el) => {
    const p = D(); if (!p) { el.replaceChildren(); return; }
    const s = pick(p), x = p.stocks[s]; if (!x) { el.replaceChildren(); return; }
    const w = x[span], fname = (id) => (p.factors.find((f) => f.id === id) || {}).name || id, fshort = (id) => (p.factors.find((f) => f.id === id) || {}).short || id;
    const parts = Object.entries(w.parts).map(([id, v]) => ({ label: fname(id), short: fshort(id), v })).concat([{ label: s === 'SPY' ? 'Unexplained' : 'Unexplained / company-specific', short: null, v: w.other }]).sort((a, b) => Math.abs(b.v) - Math.abs(a.v));
    const max = Math.max(Math.abs(w.total), ...parts.map((r) => Math.abs(r.v)), 0.5);
    const bar = (label, v, cls) => { const r = mk('div', 'sdbar' + (cls ? ' ' + cls : '')), t = mk('span', 'sdtrack'), i = mk('i', v >= 0 ? 'pos' : 'neg'), width = Math.abs(v) / max * 50; i.style.width = width + '%'; i.style.left = (v >= 0 ? 50 : 50 - width) + '%'; t.appendChild(i); r.append(mk('span', '', label), t, mk('span', tone(v, true), chg(v, 'pct'))); return r; };
    const head = mk('div', 'drvregime'); head.appendChild(mk('h3', '', 'What moved ' + s)); const btns = mk('span', 'drvbtns'); btns.style.margin = '0'; [['day', 'Last day'], ['week', 'Last 5 days']].forEach(([id, label]) => { const b = mk('button', 'drvbtn', label); b.type = 'button'; b.setAttribute('aria-pressed', String(span === id)); b.addEventListener('click', () => { span = id; dash.draw('stockdrivers'); }); btns.appendChild(b); }); head.appendChild(btns);
    const lead = parts[0], verb = w.total > 0 ? 'rose' : w.total < 0 ? 'fell' : 'was flat'; const line = mk('p', 'sub', s + ' ' + verb + (w.total ? ' ' + num(w.total, 1) + '%' : '') + (span === 'week' ? ' over the last 5 trading days' : ' on the last trading day') + '. ' + (lead.short ? 'The largest piece lines up with ' + lead.short : 'The largest piece is not explained by these factors') + ' (' + chg(lead.v, 'pct') + ').');
    const left = mk('div'); left.appendChild(bar('Total move', w.total, 'total')); parts.forEach((r) => left.appendChild(bar(r.label, r.v)));
    const strong = x.exposures.filter((e) => Math.abs(e.t) >= 3).sort((a, b) => Math.abs(b.t) - Math.abs(a.t));
    left.appendChild(mk('p', 'drvnote', (strong.length ? 'Reliable sensitivities: ' + strong.map((e) => sign(e.beta) + num(e.beta, 2) + '% per ' + (p.factors.find((f) => f.id === e.id) || {}).per + ' ' + fname(e.id)).join(', ') + '. ' : 'No reliable macro sensitivity. ') + 'These factors explain ' + Math.round(x.r2 * 100) + '% of its daily moves.'));
    const right = mk('div'); right.appendChild(mk('h4', '', 'News that may matter for ' + s)); right.firstChild.style.margin = '0 0 3px'; const ul = mk('ul', 'sdlist');
    const items = (x.company_news || []).concat((x.news || []).map((i) => p.news[i]).filter(Boolean)); const seen = new Set();
    items.forEach((n) => { if (seen.has(n.title) || seen.size >= 6) return; seen.add(n.title); const li = mk('li'), href = safe(n.link), a = mk(href ? 'a' : 'span', '', n.title); if (href) { a.href = href; a.target = '_blank'; a.rel = 'noopener noreferrer'; } li.appendChild(a); if (n.published) li.append(' ', mk('small', 'sub', ago(n.published))); ul.appendChild(li); });
    if (!ul.children.length) ul.appendChild(mk('li', 'sub', 'No important stories matched this stock.')); right.appendChild(ul);
    const cols = mk('div', 'sdcols'); cols.append(left, right); el.replaceChildren(head, line, cols);
  });
})();
