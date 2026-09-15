import { initChat } from './chat.js?v=highlight6';

let pdfjsLib = null;

const state = {
  sections: [],
  align: {},
  view: 'contents',
  themeFilter: 'all',
  query: '',
  collapsed: new Set(),
  current: null,
  visiblePage: 11,
  pdf: null,
  pdfReady: false,
  pendingLocation: null,
  renderedPages: new Map(),
  pageRatio: 840.232 / 595.276,
  favorites: new Set(JSON.parse(localStorage.getItem('binas:favorites') || '[]')),
  recent: JSON.parse(localStorage.getItem('binas:recent') || '[]'),
  answerHighlights: null,
};

const el = id => document.getElementById(id);
const normalize = value => String(value || '').toLocaleLowerCase('nl').normalize('NFD').replace(/[\u0300-\u036f]/g, '');

function flattenData(sections) {
  return sections.flatMap((section, sectionIndex) => section.items.flatMap(item => {
    const parent = makeEntry(section, item, sectionIndex);
    const children = (item.children || []).map(child => makeEntry(section, child, sectionIndex, item));
    return [parent, ...children];
  }));
}

function makeEntry(section, item, sectionIndex, parent = null) {
  const displayLabel = parent ? `${parent.label}${String(item.label).replace(/^.*\./, '')}` : String(item.label);
  const key = `${sectionIndex}:${parent ? parent.label + '/' : ''}${item.label}:${item.title}`;
  return { ...item, key, section: section.section, theme: section.theme || normalize(section.section), sectionIndex, parent, displayLabel };
}

function filteredEntries() {
  let entries = flattenData(state.sections);
  if (state.view === 'favorites') entries = entries.filter(x => state.favorites.has(x.key));
  if (state.view === 'recent') {
    const order = new Map(state.recent.map((key, i) => [key, i]));
    entries = entries.filter(x => order.has(x.key)).sort((a, b) => order.get(a.key) - order.get(b.key));
  }
  if (state.themeFilter !== 'all') entries = entries.filter(x => x.theme === state.themeFilter);
  if (state.query) {
    const q = normalize(state.query);
    entries = entries.filter(x => normalize(`${x.displayLabel} ${x.label} ${x.title} ${x.section}`).includes(q));
  }
  return entries;
}

function render() {
  const entries = filteredEntries();
  const container = el('tableList');
  container.textContent = '';
  el('favoriteCount').textContent = state.favorites.size;
  el('resultSummary').textContent = `${entries.length} ${entries.length === 1 ? 'resultaat' : 'resultaten'}`;

  if (!entries.length) {
    container.innerHTML = `<div class="empty"><b>Niets gevonden</b><p>Probeer een titel, onderwerp of tabelnummer.</p></div>`;
    return;
  }

  const grouped = new Map();
  for (const entry of entries) {
    const groupKey = `${entry.sectionIndex}:${entry.section}`;
    if (!grouped.has(groupKey)) grouped.set(groupKey, { name: entry.section, key: groupKey, entries: [] });
    grouped.get(groupKey).entries.push(entry);
  }

  for (const group of grouped.values()) {
    const section = document.createElement('section');
    section.className = `section${state.collapsed.has(group.key) ? ' collapsed' : ''}`;
    section.innerHTML = `<button class="section-heading"><span>${escapeHtml(group.name)}</span><small>${group.entries.length}</small><span class="chevron">⌄</span></button><div class="section-items"></div>`;
    section.querySelector('.section-heading').addEventListener('click', () => {
      state.collapsed.has(group.key) ? state.collapsed.delete(group.key) : state.collapsed.add(group.key);
      render();
    });
    const items = section.querySelector('.section-items');
    group.entries.forEach(entry => items.appendChild(renderRow(entry)));
    container.appendChild(section);
  }
}

function renderRow(entry) {
  const row = document.createElement('div');
  row.className = `table-row${entry.parent ? ' subtable' : ''}${state.current?.key === entry.key ? ' active' : ''}`;
  row.tabIndex = 0;
  row.dataset.key = entry.key;
  row.innerHTML = `<span class="row-label">${escapeHtml(entry.parent ? String(entry.label).replace(/^.*\./, '') : entry.label)}</span><span class="row-copy"><span class="row-title">${escapeHtml(entry.title)}</span><span class="row-page">pagina ${entry.page}</span></span><button class="star${state.favorites.has(entry.key) ? ' saved' : ''}" aria-label="Favoriet">${state.favorites.has(entry.key) ? '★' : '☆'}</button>`;
  const open = () => selectEntry(entry);
  row.addEventListener('click', event => { if (!event.target.closest('.star')) open(); });
  row.addEventListener('keydown', event => { if (event.key === 'Enter' || event.key === ' ') open(); });
  row.querySelector('.star').addEventListener('click', () => toggleFavorite(entry));
  return row;
}

function selectEntry(entry, options = {}) {
  state.current = entry;
  if (options.addToRecent !== false) {
    state.recent = [entry.key, ...state.recent.filter(x => x !== entry.key)].slice(0, 30);
    localStorage.setItem('binas:recent', JSON.stringify(state.recent));
  }
  const align = findAlignment(entry);
  const page = align?.page || entry.page;
  state.visiblePage = page;
  el('currentLabel').textContent = entry.displayLabel;
  el('currentTitle').textContent = entry.title;
  el('currentMeta').textContent = `${entry.section} · pagina ${page}`;
  el('pageInput').value = page;
  jumpToLocation(page, align?.yFraction || 0);
  updateCurrentStar();
  render();
  revealActiveRow();
  closeSidebar();
}

function findAlignment(entry) {
  const candidates = Object.values(state.align).filter(x => x.title === entry.title && x.page === entry.page);
  return candidates.find(x => x.section === entry.section) || candidates[0];
}

function toggleFavorite(entry) {
  state.favorites.has(entry.key) ? state.favorites.delete(entry.key) : state.favorites.add(entry.key);
  localStorage.setItem('binas:favorites', JSON.stringify([...state.favorites]));
  updateCurrentStar();
  render();
}

function updateCurrentStar() {
  const saved = state.current && state.favorites.has(state.current.key);
  el('favoriteCurrent').textContent = saved ? '★' : '☆';
  el('favoriteCurrent').style.color = saved ? '#d8971c' : '';
}

function escapeHtml(value) {
  return String(value).replace(/[&<>'"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));
}

function closeSidebar() {
  el('sidebar').classList.remove('open');
  el('backdrop').classList.remove('show');
}

function revealActiveRow() {
  requestAnimationFrame(() => {
    const row = [...document.querySelectorAll('.table-row')].find(x => x.dataset.key === state.current?.key);
    row?.scrollIntoView({ block: 'nearest' });
  });
}

function bindEvents() {
  el('searchInput').addEventListener('input', event => { state.query = event.target.value; render(); });
  document.addEventListener('keydown', event => {
    const typing = ['searchInput', 'chatInput', 'pageInput'].includes(document.activeElement?.id);
    if (event.key === '/' && !typing) { event.preventDefault(); el('searchInput').focus(); }
    if (event.key === 'Escape') closeSidebar();
  });
  document.querySelectorAll('.quick-tab').forEach(button => button.addEventListener('click', () => {
    document.querySelectorAll('.quick-tab').forEach(x => x.classList.toggle('active', x === button));
    state.view = button.dataset.view;
    render();
  }));
  document.querySelectorAll('.subject').forEach(button => button.addEventListener('click', () => {
    document.querySelectorAll('.subject').forEach(x => x.classList.toggle('active', x === button));
    state.themeFilter = button.dataset.themeFilter;
    render();
  }));
  el('expandButton').addEventListener('click', () => {
    const allCollapsed = state.collapsed.size > 0;
    state.collapsed.clear();
    if (!allCollapsed) state.sections.forEach((s, i) => state.collapsed.add(`${i}:${s.section}`));
    el('expandButton').textContent = allCollapsed ? 'Alles inklappen' : 'Alles uitklappen';
    render();
  });
  el('pageInput').addEventListener('change', event => {
    const page = Math.max(1, Math.min(317, Number(event.target.value) || 1));
    event.target.value = page;
    jumpToLocation(page, 0);
  });
  el('openPdfButton').addEventListener('click', () => window.open(`assets/Binas.pdf#page=${state.visiblePage}`, '_blank', 'noopener'));
  el('favoriteCurrent').addEventListener('click', () => state.current && toggleFavorite(state.current));
  el('menuButton').addEventListener('click', () => { el('sidebar').classList.add('open'); el('backdrop').classList.add('show'); });
  el('backdrop').addEventListener('click', closeSidebar);
  el('themeButton').addEventListener('click', () => {
    const next = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
    document.documentElement.dataset.theme = next;
    localStorage.setItem('binas:theme', next);
  });
  el('infoButton').addEventListener('click', () => el('infoDialog').showModal());
  el('closeInfo').addEventListener('click', () => el('infoDialog').close());
}

async function initPdf() {
  try {
    pdfjsLib = await import('./vendor/pdfjs/pdf.min.mjs');
    pdfjsLib.GlobalWorkerOptions.workerSrc = './vendor/pdfjs/pdf.worker.min.mjs';
  } catch (error) {
    el('pdfLoading').textContent = 'De PDF-module kon niet laden. Controleer de webserver-MIME types.';
    el('pdfLoading').classList.add('error');
    console.error(error);
    return;
  }
  const loadingTask = pdfjsLib.getDocument({ url: 'assets/Binas.pdf' });
  state.pdf = await loadingTask.promise;
  const firstPage = await state.pdf.getPage(1);
  const viewport = firstPage.getViewport({ scale: 1 });
  state.pageRatio = viewport.height / viewport.width;
  buildPagePlaceholders();
  state.pdfReady = true;
  el('pdfLoading').classList.add('hidden');
  const location = state.pendingLocation || { page: state.visiblePage, yFraction: 0 };
  state.pendingLocation = null;
  jumpToLocation(location.page, location.yFraction);
  bindPdfScrollSync();
}

function buildPagePlaceholders() {
  const viewer = el('pdfViewer');
  viewer.querySelectorAll('.pdf-page').forEach(x => x.remove());
  const width = pageDisplayWidth();
  const fragment = document.createDocumentFragment();
  for (let page = 1; page <= state.pdf.numPages; page++) {
    const holder = document.createElement('section');
    holder.className = 'pdf-page';
    holder.dataset.page = page;
    holder.style.height = `${Math.round(width * state.pageRatio)}px`;
    holder.innerHTML = `<span class="pdf-page-number">${page}</span>`;
    fragment.appendChild(holder);
  }
  viewer.appendChild(fragment);
}

function pageDisplayWidth() {
  return Math.min(920, Math.max(260, el('pdfViewer').clientWidth - 48));
}

function clearPageSurface(holder) {
  holder?._textLayer?.cancel?.();
  holder._textLayer = null;
  holder?.querySelector('.pdf-page-surface')?.remove();
}

async function renderPage(pageNumber) {
  if (!state.pdfReady || state.renderedPages.has(pageNumber)) return;
  const holder = el('pdfViewer').querySelector(`[data-page="${pageNumber}"]`);
  if (!holder) return;
  state.renderedPages.set(pageNumber, true);
  try {
    const page = await state.pdf.getPage(pageNumber);
    const base = page.getViewport({ scale: 1 });
    const cssWidth = holder.clientWidth;
    const pixelRatio = Math.min(window.devicePixelRatio || 1, 2);
    const displayScale = cssWidth / base.width;
    const displayViewport = page.getViewport({ scale: displayScale });
    const renderViewport = page.getViewport({ scale: displayScale * pixelRatio });
    const displayHeight = Math.round(displayViewport.height);

    clearPageSurface(holder);
    holder.style.height = `${displayHeight}px`;

    const surface = document.createElement('div');
    surface.className = 'pdf-page-surface';
    surface.style.width = `${cssWidth}px`;
    surface.style.height = `${displayHeight}px`;

    const canvas = document.createElement('canvas');
    canvas.width = Math.floor(renderViewport.width);
    canvas.height = Math.floor(renderViewport.height);
    canvas.style.width = `${cssWidth}px`;
    canvas.style.height = `${displayHeight}px`;

    const highlightLayer = document.createElement('div');
    highlightLayer.className = 'pdf-highlight-layer';

    const textLayerDiv = document.createElement('div');
    textLayerDiv.className = 'textLayer';
    textLayerDiv.style.setProperty('--scale-factor', String(displayScale));

    surface.append(canvas, highlightLayer, textLayerDiv);
    holder.prepend(surface);

    await page.render({ canvasContext: canvas.getContext('2d'), viewport: renderViewport }).promise;

    const textLayer = new pdfjsLib.TextLayer({
      textContentSource: await page.getTextContent(),
      container: textLayerDiv,
      viewport: displayViewport,
    });
    holder._textLayer = textLayer;
    await textLayer.render();
    if (state.answerHighlights?.page === pageNumber) {
      paintAnswerHighlights(textLayerDiv, state.answerHighlights.terms, state.answerHighlights.skip);
    }
  } catch (error) {
    state.renderedPages.delete(pageNumber);
    clearPageSurface(holder);
    console.error(`Kon PDF-pagina ${pageNumber} niet renderen`, error);
  }
}

function renderAround(pageNumber) {
  for (let page = Math.max(1, pageNumber - 2); page <= Math.min(state.pdf?.numPages || 317, pageNumber + 2); page++) renderPage(page);
  for (const rendered of [...state.renderedPages.keys()]) {
    if (Math.abs(rendered - pageNumber) <= 8) continue;
    const holder = el('pdfViewer').querySelector(`[data-page="${rendered}"]`);
    clearPageSurface(holder);
    if (holder) holder.style.height = `${Math.round(pageDisplayWidth() * state.pageRatio)}px`;
    state.renderedPages.delete(rendered);
  }
}

function jumpToLocation(page, yFraction = 0) {
  state.visiblePage = page;
  if (!state.pdfReady) {
    state.pendingLocation = { page, yFraction };
    return;
  }
  const viewer = el('pdfViewer');
  const holder = viewer.querySelector(`[data-page="${page}"]`);
  if (!holder) return;
  viewer.scrollTop = holder.offsetTop + holder.offsetHeight * yFraction - 24;
  renderAround(page);
  syncSidebarFromViewport();
}

function bindPdfScrollSync() {
  let frame = 0;
  el('pdfViewer').addEventListener('scroll', () => {
    if (frame) return;
    frame = requestAnimationFrame(() => {
      frame = 0;
      syncSidebarFromViewport();
    });
  }, { passive: true });
  let resizeTimer;
  window.addEventListener('resize', () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => {
      const page = state.visiblePage;
      state.renderedPages.clear();
      buildPagePlaceholders();
      jumpToLocation(page, 0);
    }, 180);
  });
}

function syncSidebarFromViewport() {
  if (!state.pdfReady) return;
  const viewer = el('pdfViewer');
  const probe = viewer.scrollTop + Math.min(110, viewer.clientHeight * 0.18);
  let holder = viewer.querySelector('.pdf-page');
  for (const candidate of viewer.querySelectorAll('.pdf-page')) {
    if (candidate.offsetTop <= probe) holder = candidate;
    else break;
  }
  if (!holder) return;
  const page = Number(holder.dataset.page);
  const fraction = Math.max(0, Math.min(.999, (probe - holder.offsetTop) / holder.offsetHeight));
  state.visiblePage = page;
  el('pageInput').value = page;
  renderAround(page);
  const entry = entryForPosition(page, fraction);
  if (!entry) return;
  el('currentMeta').textContent = `${entry.section} · pagina ${page}`;
  if (entry.key === state.current?.key) return;
  state.current = entry;
  el('currentLabel').textContent = entry.displayLabel;
  el('currentTitle').textContent = entry.title;
  updateCurrentStar();
  render();
  revealActiveRow();
}

function entryForPosition(page, fraction) {
  const entries = flattenData(state.sections);
  const aligned = Object.values(state.align)
    .filter(x => x.page < page || (x.page === page && (x.yFraction || 0) <= fraction))
    .sort((a, b) => a.page - b.page || (a.yFraction || 0) - (b.yFraction || 0));
  const location = aligned.at(-1);
  if (location) {
    const exact = entries.find(x => x.page === location.page && x.title === location.title && x.section === location.section);
    if (exact) return exact;
  }
  return entries.filter(x => x.page <= page).sort((a, b) => a.page - b.page).at(-1) || entries[0];
}

function focusBinasLocation({ page, yFraction = 0, title = '', table = '', highlights = [] } = {}) {
  const targetPage = Math.max(1, Math.min(state.pdf?.numPages || 317, Number(page) || state.visiblePage || 1));
  const entries = flattenData(state.sections);
  const compact = value => normalize(value).replace(/\s+/g, '');
  let match = null;
  if (table) {
    const want = compact(table);
    match = entries.find(x => compact(x.displayLabel) === want)
      || entries.find(x => compact(x.label) === want && x.page === targetPage)
      || entries.find(x => compact(x.label) === want);
  }
  if (!match && title) {
    const want = normalize(title);
    match = entries.find(x => normalize(x.title) === want && x.page === targetPage)
      || entries.find(x => normalize(x.title) === want);
  }
  const focusPage = match?.page || targetPage;
  if (match) {
    selectEntry(match);
    const aligned = findAlignment(match)?.yFraction || 0;
    const frac = Number(yFraction) || aligned;
    if (frac > 0.02) jumpToLocation(focusPage, frac);
  } else {
    jumpToLocation(targetPage, Number(yFraction) || 0);
  }
  highlightPdfPage(focusPage);
  applyAnswerHighlights(focusPage, highlights, [
    title,
    table,
    match?.title,
    match?.section,
    ...flattenData(state.sections).filter(entry => entry.page === focusPage).map(entry => entry.title),
  ]);
}

function highlightPdfPage(page) {
  requestAnimationFrame(() => {
    const holder = el('pdfViewer')?.querySelector(`[data-page="${page}"]`);
    if (!holder) return;
    holder.classList.remove('flash-focus');
    void holder.offsetWidth;
    holder.classList.add('flash-focus');
    window.setTimeout(() => holder.classList.remove('flash-focus'), 1800);
  });
}

function compactText(value) {
  return normalize(String(value || '')).replace(/[^\p{L}\p{N}]+/gu, '');
}

function highlightQueries(searchTexts, skipTexts = []) {
  const skip = [...new Set((skipTexts || []).map(compactText).filter(value => value.length >= 8))];
  const skipped = value => skip.some(item => value === item || (value.length >= 8 && (item.includes(value) || value.includes(item)) && Math.min(value.length, item.length) / Math.max(value.length, item.length) >= 0.55));
  const queries = [];
  for (const raw of searchTexts || []) {
    const compact = compactText(raw);
    if (compact.length >= 8 && !skipped(compact)) queries.push(compact);
    if (compact.length >= 16) {
      for (const token of String(raw).split(/[^\p{L}\p{N}]+/u)) {
        const piece = compactText(token);
        if (piece.length >= 8 && !skipped(piece)) queries.push(piece);
      }
    }
  }
  return [...new Set(queries)].sort((a, b) => b.length - a.length);
}

function paintAnswerHighlights(textLayer, searchTexts, skipTexts = []) {
  const surface = textLayer.closest('.pdf-page-surface') || textLayer.parentElement;
  const overlay = surface?.querySelector('.pdf-highlight-layer');
  overlay?.replaceChildren();
  textLayer.querySelectorAll('.answer-highlight').forEach(node => node.classList.remove('answer-highlight'));

  const spans = [...textLayer.querySelectorAll('span')].filter(span => {
    if (span.classList.contains('markedContent')) return false;
    return compactText(span.textContent).length > 0;
  });
  if (!spans.length) return false;

  let compact = '';
  const map = [];
  spans.forEach((span, index) => {
    const text = compactText(span.textContent);
    for (let i = 0; i < text.length; i++) map.push({ index, offset: i, spanLen: text.length });
    compact += text;
  });

  const ranges = [];
  for (const query of highlightQueries(searchTexts, skipTexts)) {
    let from = 0;
    let hits = 0;
    while (from < compact.length && ranges.length < 4) {
      const start = compact.indexOf(query, from);
      if (start < 0) break;
      const end = start + query.length;
      const overlaps = ranges.some(range => start < range.end && end > range.start);
      if (!overlaps) {
        ranges.push({ start, end });
        hits += 1;
      }
      from = end;
      if (hits >= 2) break;
    }
    if (ranges.length >= 4) break;
  }
  if (!ranges.length) return false;

  const surfaceRect = surface.getBoundingClientRect();
  for (const range of ranges) {
    const bySpan = new Map();
    for (let i = range.start; i < range.end && i < map.length; i++) {
      const loc = map[i];
      let rec = bySpan.get(loc.index);
      if (!rec) {
        rec = { min: loc.offset, max: loc.offset + 1, spanLen: loc.spanLen };
        bySpan.set(loc.index, rec);
      } else {
        rec.min = Math.min(rec.min, loc.offset);
        rec.max = Math.max(rec.max, loc.offset + 1);
      }
    }
    for (const [index, rec] of bySpan) {
      const span = spans[index];
      const rect = span.getBoundingClientRect();
      if (!overlay || rect.width < 0.5 || rect.height < 0.5 || surfaceRect.width < 1 || !rec.spanLen) continue;
      const covered = rec.spanLen ? (rec.max - rec.min) / rec.spanLen : 1;
      if (covered >= 0.72) span.classList.add('answer-highlight');
      const left = ((rect.left - surfaceRect.left + rect.width * (rec.min / rec.spanLen)) / surfaceRect.width) * 100;
      const width = ((rect.width * covered) / surfaceRect.width) * 100;
      const box = document.createElement('div');
      box.className = 'pdf-answer-box';
      box.style.left = `${left}%`;
      box.style.top = `${((rect.top - surfaceRect.top) / surfaceRect.height) * 100}%`;
      box.style.width = `${Math.max(width, 0.4)}%`;
      box.style.height = `${(Math.max(rect.height, 8) / surfaceRect.height) * 100}%`;
      overlay.append(box);
    }
  }
  return true;
}

async function waitForTextLayer(page, attempts = 50) {
  for (let i = 0; i < attempts; i++) {
    const layer = el('pdfViewer')?.querySelector(`[data-page="${page}"] .textLayer`);
    if (layer?.querySelector('span')) return layer;
    renderAround(page);
    await new Promise(resolve => window.setTimeout(resolve, 80));
  }
  return null;
}

async function applyAnswerHighlights(page, highlights, skipTexts = []) {
  el('pdfViewer')?.querySelectorAll('.pdf-highlight-layer').forEach(node => node.replaceChildren());
  el('pdfViewer')?.querySelectorAll('.answer-highlight').forEach(node => node.classList.remove('answer-highlight'));
  const terms = [...new Set((highlights || []).map(x => String(x || '').trim()).filter(x => x.length >= 4))];
  const skip = [...new Set((skipTexts || []).map(x => String(x || '').trim()).filter(Boolean))];
  if (!terms.length) {
    state.answerHighlights = null;
    return;
  }

  state.answerHighlights = { page, terms, skip };
  const textLayer = await waitForTextLayer(page);
  if (!textLayer || state.answerHighlights?.page !== page) return;

  paintAnswerHighlights(textLayer, terms, skip);
  const first = textLayer.parentElement?.querySelector('.pdf-answer-box, .answer-highlight');
  first?.scrollIntoView({ block: 'center', behavior: 'smooth' });
}

async function init() {
  document.documentElement.dataset.theme = localStorage.getItem('binas:theme') || 'light';
  try {
    const auth = await fetch('/api/auth', { credentials: 'same-origin' });
    if (auth.status === 401) {
      location.replace('/login.html');
      return;
    }
  } catch {
    /* De bestaande data-foutmelding dekt een ontbrekende server. */
  }
  bindEvents();
  initChat({ focus: focusBinasLocation });
  try {
    const [navigation, alignment] = await Promise.all([
      fetch('data/navigation-data.json').then(r => r.json()),
      fetch('data/binas-align.json').then(r => r.json()),
    ]);
    state.sections = navigation;
    state.align = alignment.items || {};
    state.current = flattenData(navigation)[0];
    selectEntry(state.current);
    await initPdf();
  } catch (error) {
    el('tableList').innerHTML = `<div class="empty"><b>Start via een lokale webserver</b><p>Open de README voor het startcommando.</p></div>`;
    el('resultSummary').textContent = 'Data kon niet laden';
    console.error(error);
  }
}

init();
