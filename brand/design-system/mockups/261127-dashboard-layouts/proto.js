// Draws the Documents dashboard in one of the trial's four layouts at a given screen width.
// Rules copied from the app: the tile grid's columns follow the viewport (utility-patterns.css),
// the table needs a results area of max(640, 240 + 112 x fields) px and shows the list below
// that (useDashboardLayout.ts), the sidebar (256px expanded) shows from 1024px. All documents are invented.
(() => {
  const KIND = { Invoice: 'var(--category-3)', Letter: 'var(--category-1)', Receipt: 'var(--category-2)',
                 Contract: 'var(--category-5)', Warranty: 'var(--category-4)' }
  const DOCS = [
    { t: 'Energy bill for October', k: 'Invoice', s: 'Northwind Energy', tags: ['House', 'Utilities'], d: '2026-11-03', l: 'Dutch', a: '€ 142.18',
      sum: 'Monthly bill for electricity and gas at the house, with a meter reading and the payment date.' },
    { t: 'Change to waste collection days', k: 'Letter', s: 'Gemeente Oostveld', tags: ['House'], d: '2026-10-28', l: 'Dutch',
      sum: 'From January the green bin is collected on Tuesdays instead of Fridays.' },
    { t: 'Car insurance renewal', k: 'Contract', s: 'Harbourline Insurance', tags: ['Car', 'Insurance', 'Renewals'], d: '2026-10-21', l: 'English', a: '€ 612.00',
      review: 'No amount found', sum: 'Renewal of the policy for another year, with the new premium and the cover summary.' },
    { t: 'Dishwasher warranty', k: 'Warranty', s: 'Keukenhuis', tags: ['House', 'Appliances'], d: '2026-10-14', l: 'Dutch',
      sum: 'Two-year warranty for the dishwasher bought in October, with the serial number.' },
    { t: 'Dentist appointment receipt', k: 'Receipt', s: 'Praktijk de Linde', tags: ['Health'], d: '2026-10-09', l: 'Dutch', a: '€ 68.50',
      sum: 'Receipt for a check-up and cleaning, paid by card.' },
    { t: 'Annual tax assessment 2025 with the explanation of the calculation', k: 'Letter', s: 'Tax Office', tags: ['Tax', '2025'], d: '2026-09-30', l: 'Dutch',
      sum: 'The final assessment for 2025 and how it was calculated. Nothing more to pay.' },
    { t: 'School trip consent form', k: null, s: 'Oak Lane Primary', tags: ['School', 'Kids'], d: '2026-09-22', l: 'English',
      sum: 'Consent form for the class trip to the science museum on 14 October.' },
    { t: 'Broadband contract', k: 'Contract', s: 'Fibrelink', tags: ['House', 'Utilities', 'Internet', 'Contracts', 'Renewals'], d: '2026-09-15', l: 'English', a: '€ 49.95',
      sum: 'Twelve-month contract for 1 Gbit broadband, starting 1 October.' },
    { t: 'Bike repair receipt', k: 'Receipt', s: 'Fietsmaker Jansen', tags: ['Bike'], d: '2026-09-02', l: 'Dutch', a: '€ 37.00', status: 'processing',
      sum: 'New chain and brake pads.' },
    { t: 'Library card renewal', k: 'Letter', s: 'City Library', tags: [], d: '2026-08-25', l: 'English',
      sum: 'Reminder that the library card expires at the end of September.' },
  ]
  // Default dashboard fields: kind, sender, tags, date, language, status (settings.ts).
  const FIELDS = ['kind', 'sender', 'tags', 'date', 'language', 'status']
  const HEAD = { kind: 'Type', sender: 'From', tags: 'Tags', date: 'Date', language: 'Language', status: 'Status' }
  const MON = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
  const LONG = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December']
  const date = (iso, short) => { const [y, m, d] = iso.split('-').map(Number); return `${d} ${(short ? MON : LONG)[m - 1]} ${y}` }
  const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c])
  const kindStyle = (doc) => (doc.k && KIND[doc.k] ? ` style="--kind: ${KIND[doc.k]}"` : '')

  const tableMinWidth = (n) => Math.max(640, 240 + 112 * n)
  const tileCols = (w) => (w < 769 ? 2 : w < 1400 ? 3 : 4)

  // One field in the chips look (tiles, rows) or the text look (list, table).
  function field(doc, f, look, short) {
    const chips = look === 'chips'
    switch (f) {
      case 'kind': return doc.k ? (chips ? `<span class="badge">${doc.k}</span>` : `<span class="f">${doc.k}</span>`) : ''
      case 'sender': return `<span class="f">${esc(doc.s)}</span>`
      case 'tags':
        if (!doc.tags.length) return ''
        if (chips) return doc.tags.slice(0, 4).map((x) => `<span class="badge">${x}</span>`).join('') + (doc.tags.length > 4 ? `<span class="f">+${doc.tags.length - 4}</span>` : '')
        return `<span class="f">${doc.tags.slice(0, 4).join(', ')}${doc.tags.length > 4 ? ` +${doc.tags.length - 4}` : ''}</span>`
      case 'date': return `<span class="f">${look === 'chips' ? '<span class="lab">Date</span> ' : ''}${date(doc.d, short)}</span>`
      case 'language': return chips ? `<span class="badge">${doc.l}</span>` : `<span class="f">${doc.l}</span>`
      case 'status': return doc.status === 'processing' ? '<span class="badge yellow">Processing</span>' : ''
    }
    return ''
  }
  const review = (doc, reason) => (doc.review ? `<span class="badge yellow">Needs review</span>${reason ? `<span class="reason">${doc.review}</span>` : ''}` : '')
  const meta = (doc, look, short, reason) => `<p class="meta">${review(doc, reason)}${FIELDS.map((f) => field(doc, f, look, short)).join('')}</p>`
  const sheet = '<div class="sheet"><i class="h"></i><i></i><i class="m"></i><i></i><i class="s"></i><i></i><i class="m"></i><i></i></div>'

  const LAYOUTS = {
    tiles: (phone, w) => `<ul class="tiles" style="--cols:${tileCols(w)}">${DOCS.map((doc) => `
      <li class="tile"${kindStyle(doc)}><div class="thumb">${sheet}</div><div class="tile-body">
        <h2><a href="#">${esc(doc.t)}</a></h2>${meta(doc, 'chips', phone, true)}<p class="sum">${esc(doc.sum)}</p></div></li>`).join('')}</ul>`,
    rows: (phone) => `<ul class="rows">${DOCS.map((doc) => `
      <li class="row"${kindStyle(doc)}><h2><a href="#">${esc(doc.t)}</a></h2>${meta(doc, 'chips', phone, true)}</li>`).join('')}</ul>`,
    table: () => `<div class="tbl-card"><table class="tbl"><thead><tr><th>Title</th>${FIELDS.map((f) => `<th>${HEAD[f]}</th>`).join('')}</tr></thead><tbody>${DOCS.map((doc) => `
      <tr><td class="t"><span><a href="#" title="${esc(doc.t)}">${esc(doc.t)}</a>${doc.review ? '<span class="badge yellow">Needs review</span>' : ''}</span></td>
      ${FIELDS.map((f) => `<td class="${f === 'sender' || f === 'tags' ? 'cut' : ''}">${f === 'date' ? date(doc.d, true) : field(doc, f, 'text', true)}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`,
    list: () => `<ul class="list">${DOCS.map((doc) => `
      <li class="li"><div class="li-t"><span class="dot"${kindStyle(doc)}></span><a href="#">${esc(doc.t)}</a></div>${meta(doc, 'text', true, false)}</li>`).join('')}</ul>`,
  }
  const LABEL = { tiles: 'Tiles', rows: 'Rows', table: 'Table', list: 'List' }

  const ICONS = ['i-docs', 'i-search', 'i-ask', 'i-up', 'i-note', 'i-chart', 'i-folder', 'i-gear']
  const NAV = ['Documents', 'Search', 'Ask', 'Upload', 'New note', 'Spending', 'Matters', 'Settings']

  /** Draw the dashboard for `layout` at screen width `w` into `el`. Returns what was rendered. */
  function draw(el, layout, w) {
    const side = w >= 1024 ? 256 : 0 // AppSidebar: w-64 expanded
    const phone = w < 641
    const pad = w < 640 ? 16 : w < 1024 ? 24 : 32 // #app-page px-4 sm:px-6 lg:px-8
    const results = w - side - 2 * pad
    const wantsTable = layout === 'table'
    const fits = results >= tableMinWidth(FIELDS.length)
    const shown = wantsTable && !fits ? 'list' : layout
    el.innerHTML = `
      <div class="scr${phone ? ' phone' : ''}" style="grid-template-columns:${side ? `${side}px minmax(0, 1fr)` : 'minmax(0, 1fr)'}; --pad:${pad}px; width:${w}px">
        <nav class="scr-side"${side ? '' : ' hidden'}>
          <div class="scr-logo"><svg width="28" height="28"><use href="#mark"/></svg>Library</div>
          ${NAV.map((n, i) => `<a class="scr-nav${i ? '' : ' on'}" href="#"><svg width="20" height="20"><use href="#${ICONS[i]}"/></svg>${n}</a>`).join('')}
        </nav>
        <main class="scr-main">
          <div class="scr-bar">${side ? '' : '<span class="scr-icon"><svg width="22" height="22"><use href="#i-menu"/></svg></span>'}<h1>Documents</h1>
            <span class="scr-icon"><svg width="20" height="20"><use href="#i-up"/></svg></span></div>
          <div class="scr-search"><svg width="18" height="18"><use href="#i-search"/></svg>Search documents</div>
          <div class="scr-controls">
            <label class="scr-ctl"><span>Sort</span><b>Newest first <small>▾</small></b></label>
            <label class="scr-ctl now"><span>Layout</span><b>${LABEL[layout]} <small>▾</small></b></label>
            <span class="scr-btn">Fields</span><span class="scr-btn">Save view</span>
          </div>
          <p class="scr-count">${DOCS.length} documents</p>
          ${wantsTable && !fits ? `<p class="fallback-note">Too narrow for the table here (${results}px of the ${tableMinWidth(FIELDS.length)}px it needs), so it shows as the list.</p>` : ''}
          ${LAYOUTS[shown](phone, w)}
        </main>
      </div>`
    el.querySelectorAll('a[href="#"]').forEach((a) => a.addEventListener('click', (e) => e.preventDefault()))
    return { shown, results }
  }

  window.Dashboard = { draw, tableMinWidth, FIELDS }
})()
