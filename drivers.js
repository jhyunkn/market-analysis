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

  dash.draw('drvread', (el) => {
    const p = D(); if (!p) return pending(el, 'Loading the market read…');
    const label = p.regime.label, cls = /off|selloff/i.test(label) ? 'bad' : /rally/i.test(label) ? 'ok' : 'warn';
    const top = mk('div', 'drvregime'); top.append(mk('span', 'drvpill ' + cls, label), mk('span', '', p.regime.detail + (p.regime.flags.length ? ' Also, ' + p.regime.flags.join(', ') + '.' : '')));
    const read = mk('div', 'drvread'); p.read.forEach((t) => read.appendChild(mk('p', '', t)));
    const m = META(), when = new Date(m.generated_at).toLocaleString('en-US', { timeZone: 'America/New_York', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' });
    const note = mk('p', 'drvnote', 'These show what moved together, which is not proof of cause. News from the last ' + m.news_window_hours + ' hours, ' + m.feeds_ok + ' feeds, updated ' + when + ' ET.' + (m.news_stale ? ' News feeds were unavailable, so this list is from the previous update.' : ''));
    el.replaceChildren(top, read, note);
  });

  let theme = '';
  function ranked(p) {
    if (theme) return p.news.filter((n) => n.themes.includes(theme)).slice(0, 20);
    const order = ['geopolitics', 'fed', 'ai', 'energy', 'trade', 'inflation', 'credit', 'markets'], seen = new Set(), out = [];
    for (let pass = 0; pass < 2; pass++) order.forEach((t) => { const n = p.news.find((x) => x.themes.includes(t) && !seen.has(x)); if (n) { seen.add(n); out.push(n); } });
    p.news.forEach((n) => { if (!seen.has(n) && out.length < 24) { seen.add(n); out.push(n); } });
    return out;
  }
  dash.draw('drvnews', (el) => {
    const p = D(); if (!p) return pending(el);
    const btns = mk('div', 'drvbtns'); const add = (id, label) => { const b = mk('button', 'drvbtn', label); b.type = 'button'; b.setAttribute('aria-pressed', String(theme === id)); b.addEventListener('click', () => { theme = id; dash.draw('drvnews'); }); btns.appendChild(b); };
    add('', 'Top stories'); p.themes.filter((t) => t.count).forEach((t) => add(t.id, t.name));
    const nodes = [btns];
    if (theme) { const t = p.themes.find((x) => x.id === theme), r = mk('div', 'drvreact'); r.appendChild(mk('span', '', 'Market reaction this week:')); t.proxies.forEach((x) => { const s = mk('span'); s.append(x.name + ' ', mk('b', tone(x.chg_1w, x.up_good), chg(x.chg_1w, x.unit))); r.appendChild(s); }); nodes.push(r); }
    const ul = mk('ul', 'drvnews');
    ranked(p).forEach((n) => { const li = mk('li'), href = safe(n.link), a = mk(href ? 'a' : 'span', 't', n.title); if (href) { a.href = href; a.target = '_blank'; a.rel = 'noopener noreferrer'; }
      const m = mk('div', 'drvmeta'); m.append(mk('span', '', n.source + (n.sources > 1 ? ' +' + (n.sources - 1) + ' more' : '')), mk('span', '', ago(n.published))); n.themes.forEach((t) => m.appendChild(mk('span', 'drvtag', themeName(p, t)))); (n.tickers || []).forEach((s) => { if (p.stocks[s]) m.appendChild(symBtn(s)); });
      li.append(a, m); ul.appendChild(li); });
    if (!ul.children.length) ul.appendChild(mk('li', 'sub', 'No important stories in this category right now.'));
    nodes.push(ul); el.replaceChildren(...nodes);
  });

  let factor = 'oil';
  dash.draw('drvwho', (el) => {
    const p = D(); if (!p) return pending(el);
    const btns = mk('div', 'drvbtns'); p.factors.filter((f) => f.id !== 'market' && f.id !== 'semis').forEach((f) => { const b = mk('button', 'drvbtn', f.name); b.type = 'button'; b.setAttribute('aria-pressed', String(factor === f.id)); b.addEventListener('click', () => { factor = f.id; dash.draw('drvwho'); }); btns.appendChild(b); });
    const f = p.factors.find((x) => x.id === factor);
    const lev = new Set((dash.data('outlook_summary').data || []).filter((r) => r.group === 'Leveraged ETFs').map((r) => r.symbol));
    const rows = Object.entries(p.stocks).filter(([s]) => !lev.has(s) && s !== 'SPY').map(([s, x]) => ({ s, e: x.exposures.find((y) => y.id === factor), week: x.week.total })).filter((r) => r.e && Math.abs(r.e.t) >= 3);
    const list = (title, items) => { const box = mk('div'); box.appendChild(mk('h4', '', title)); if (!items.length) box.appendChild(mk('p', 'sub', 'None clear.')); items.forEach((r) => { const row = mk('div', 'whorow'); row.append(symBtn(r.s), mk('span', '', sign(r.e.beta) + num(r.e.beta, 2) + '%'), mk('span', tone(r.week, true), chg(r.week, 'pct'))); box.appendChild(row); }); return box; };
    const up = rows.filter((r) => r.e.beta > 0).sort((a, b) => b.e.beta - a.e.beta).slice(0, 8), down = rows.filter((r) => r.e.beta < 0).sort((a, b) => a.e.beta - b.e.beta).slice(0, 8);
    const grid = mk('div', 'drvwho'); grid.append(list('Tends to rise with it', up), list('Tends to fall with it', down));
    el.replaceChildren(btns, mk('p', 'sub', 'Typical stock move when ' + f.name.toLowerCase() + ' moves ' + f.per + ', with the S&P held constant (2 years of daily data), then the stock’s move this week. Click a stock to open it.'), grid);
  });

  dash.draw('drvtiles', (el) => {
    const p = D(); if (!p) return pending(el);
    const groups = mk('div', 'drvgroups');
    [...new Set(p.drivers.map((d) => d.group))].forEach((g) => { const box = mk('div', 'drvgroup'); box.appendChild(mk('h4', '', g)); const head = mk('div', 'drvrow head'); head.append(mk('span', '', ''), mk('span', '', 'Last'), mk('span', '', '1 day'), mk('span', '', '1 week'), mk('span', 'm', '1 month')); box.appendChild(head);
      p.drivers.filter((d) => d.group === g).forEach((d) => { const r = mk('div', 'drvrow'); r.append(mk('span', '', d.name), mk('span', '', level(d)), mk('span', tone(d.chg_1d, d.up_good), chg(d.chg_1d, d.unit)), mk('span', tone(d.chg_1w, d.up_good), chg(d.chg_1w, d.unit)), mk('span', 'm ' + tone(d.chg_1m, d.up_good), chg(d.chg_1m, d.unit))); box.appendChild(r); }); groups.appendChild(box); });
    el.replaceChildren(groups);
  });

  const macroDate = (m) => { const d = new Date(m.date + 'T00:00:00Z'), monthly = (d - new Date(m.prior_date + 'T00:00:00Z')) / 864e5 > 20; return d.toLocaleDateString('en-US', monthly ? { month: 'short', year: '2-digit', timeZone: 'UTC' } : { month: 'short', day: 'numeric', timeZone: 'UTC' }); };
  const macroVal = (v, u) => (u === '%' ? v.toFixed(2) + '%' : u === 'k' ? Math.round(v).toLocaleString('en-US') + 'k' : u === 'T' ? '$' + v.toFixed(2) + 'T' : v.toFixed(2));
  const macroChg = (c, u) => (!c ? '0' : sign(c) + (u === 'k' ? Math.round(Math.abs(c)) + 'k' : Math.abs(c).toFixed(2)));
  dash.draw('drvmacro', (el) => {
    const p = D(); if (!p) return pending(el);
    const groups = mk('div', 'drvgroups');
    [...new Set(p.macro.map((m) => m.group))].forEach((g) => { const box = mk('div', 'drvgroup'); box.appendChild(mk('h4', '', g)); const head = mk('div', 'macrow head'); head.append(mk('span', '', ''), mk('span', '', 'Latest'), mk('span', '', 'Change'), mk('span', '', 'As of'), mk('span', 'rng', '5-yr')); box.appendChild(head);
      p.macro.filter((m) => m.group === g).forEach((m) => { const r = mk('div', 'macrow'), range = mk('span', 'macrange rng'), tick = mk('i'); tick.style.left = m.pct_5y + '%'; range.title = m.pct_5y + 'th percentile of the last 5 years'; range.appendChild(tick);
        r.append(mk('span', '', m.name + (m.stale ? ' (stale)' : '')), mk('span', '', macroVal(m.value, m.unit)), mk('span', tone(m.change, m.up_good), macroChg(m.change, m.unit)), mk('span', '', macroDate(m)), range); box.appendChild(r); }); groups.appendChild(box); });
    el.replaceChildren(groups);
  });

  let span = 'week';
  const pick = (p) => { const v = document.getElementById('olsel')?.value; if (v && p.stocks[v]) return v; const h = decodeURIComponent(location.hash.slice(1)).toUpperCase(); return p.stocks[h] ? h : 'SPY'; };
  dash.draw('stockdrivers', (el) => {
    const p = D(); if (!p) { el.replaceChildren(); return; }
    const s = pick(p), x = p.stocks[s]; if (!x) { el.replaceChildren(); return; }
    const w = x[span], fname = (id) => (p.factors.find((f) => f.id === id) || {}).name || id;
    const parts = Object.entries(w.parts).map(([id, v]) => ({ label: fname(id), v })).concat([{ label: s === 'SPY' ? 'Other market forces' : 'Company or sector', v: w.other }]).sort((a, b) => Math.abs(b.v) - Math.abs(a.v));
    const max = Math.max(Math.abs(w.total), ...parts.map((r) => Math.abs(r.v)), 0.5);
    const bar = (label, v, cls) => { const r = mk('div', 'sdbar' + (cls ? ' ' + cls : '')), t = mk('span', 'sdtrack'), i = mk('i', v >= 0 ? 'pos' : 'neg'), width = Math.abs(v) / max * 50; i.style.width = width + '%'; i.style.left = (v >= 0 ? 50 : 50 - width) + '%'; t.appendChild(i); r.append(mk('span', '', label), t, mk('span', tone(v, true), chg(v, 'pct'))); return r; };
    const head = mk('div', 'drvregime'); head.appendChild(mk('h3', '', 'What moved ' + s)); const btns = mk('span', 'drvbtns'); btns.style.margin = '0'; [['day', 'Last day'], ['week', 'Last 5 days']].forEach(([id, label]) => { const b = mk('button', 'drvbtn', label); b.type = 'button'; b.setAttribute('aria-pressed', String(span === id)); b.addEventListener('click', () => { span = id; dash.draw('stockdrivers'); }); btns.appendChild(b); }); head.appendChild(btns);
    const lead = parts[0], verb = w.total > 0 ? 'rose' : w.total < 0 ? 'fell' : 'was flat'; const line = mk('p', 'sub', s + ' ' + verb + (w.total ? ' ' + num(w.total, 1) + '%' : '') + (span === 'week' ? ' over the last 5 trading days' : ' on the last trading day') + '. The largest piece lines up with ' + lead.label.toLowerCase().replace('s&p 500', 'the S&P 500') + ' (' + chg(lead.v, 'pct') + ').');
    const left = mk('div'); left.appendChild(bar('Total move', w.total, 'total')); parts.forEach((r) => left.appendChild(bar(r.label, r.v)));
    const strong = x.exposures.filter((e) => Math.abs(e.t) >= 3).sort((a, b) => Math.abs(b.t) - Math.abs(a.t));
    left.appendChild(mk('p', 'drvnote', (strong.length ? 'Reliable sensitivities: ' + strong.map((e) => sign(e.beta) + num(e.beta, 2) + '% per ' + (p.factors.find((f) => f.id === e.id) || {}).per + ' ' + fname(e.id).toLowerCase().replace('s&p 500', 'S&P 500')).join(', ') + '. ' : 'No reliable macro sensitivity. ') + 'These factors explain ' + Math.round(x.r2 * 100) + '% of its daily moves.'));
    const right = mk('div'); right.appendChild(mk('h4', '', 'News that may matter for ' + s)); right.firstChild.style.margin = '0 0 3px'; const ul = mk('ul', 'sdlist');
    const items = (x.company_news || []).concat((x.news || []).map((i) => p.news[i]).filter(Boolean)); const seen = new Set();
    items.forEach((n) => { if (seen.has(n.title) || seen.size >= 6) return; seen.add(n.title); const li = mk('li'), href = safe(n.link), a = mk(href ? 'a' : 'span', '', n.title); if (href) { a.href = href; a.target = '_blank'; a.rel = 'noopener noreferrer'; } li.appendChild(a); if (n.published) li.append(' ', mk('small', 'sub', ago(n.published))); ul.appendChild(li); });
    if (!ul.children.length) ul.appendChild(mk('li', 'sub', 'No important stories matched this stock.')); right.appendChild(ul);
    const cols = mk('div', 'sdcols'); cols.append(left, right); el.replaceChildren(head, line, cols);
  });
})();
