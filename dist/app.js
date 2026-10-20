if (typeof URL.parse !== "function") { URL.parse = function (url, base) { try { return new URL(url, base); } catch { return null; } }; }
if (typeof Promise.try !== "function") { Promise.try = function (fn, ...args) { return new Promise((resolve) => resolve(fn(...args))); }; }
const $ = (selector) => document.querySelector(selector);
const STORAGE = "one-page-workspace-v2";
const PREFERENCES_STORAGE = "one-page-preferences-v1";
const NOTES_STORAGE = "one-page-notes-v1";
const STATS_STORAGE = "one-page-reading-stats-v1";
const BOOKMARKS_STORAGE = "one-page-bookmarks-v1";
const RECENT_STORAGE = "one-page-recent-v1";
const HIGHLIGHTS_STORAGE = "one-page-highlights-v1";
const ANNOTATION_TOOLS_HIDDEN_STORAGE = "one-page-annotation-tools-hidden-v1";
const DEVICE_ID_STORAGE = "bookbot-device-id-v1";
const APP_VERSION = "1.0";
const DEFAULT_VIEW_MODE = "continuous";
const VIEW_MODE_MIGRATION_KEY = "one-page-view-mode-default-v1";
const SPLIT_PANE_MIGRATION_KEY = "one-page-split-pane-v1";
const SYNC_SHADOW_STORAGE = "bookbot-sync-shadow-v1";
const HAD_LOCAL_PROFILE = [STORAGE, PREFERENCES_STORAGE, NOTES_STORAGE, STATS_STORAGE, BOOKMARKS_STORAGE, RECENT_STORAGE, HIGHLIGHTS_STORAGE].some((key) => localStorage.getItem(key) !== null);
const LEGACY_PROGRESS = Number(localStorage.getItem("one-page-reader-progress")) || 186;
let library = [], books = new Map(), tabs = [], activeTabId = null, tocPaneIndex = 0, chooserPaneIndex = 0, lastStatus = "", shelfQuery = "", activeShelfTag = "", shelfPage = 1;
let draggingBookId = "", readerToastTimer = null;
const SHELF_PAGE_SIZE = 10;
let preferences = { theme: "light", pageScale: 100, uiScale: 100 };
let notes = {}, bookmarks = {}, highlights = {}, recent = [], libraryInfo = new Map(), managedBookId = null;
let publishedMap = {}, cloudConfigured = false;
let annotationTool = "none", editingHighlight = null, locatedHighlight = null, installPrompt = null;
let annotationToolsHidden = localStorage.getItem(ANNOTATION_TOOLS_HIDDEN_STORAGE) === "true";
let desktopMode = false;
let pdfjsPromise = null;
const pdfDocuments = new Map();
let activePdfRenders = 0;
const pdfRenderWaiters = [];
async function acquirePdfRenderSlot() {
  if (activePdfRenders < 2) { activePdfRenders += 1; return; }
  await new Promise((resolve) => pdfRenderWaiters.push(resolve));
  activePdfRenders += 1;
}
function releasePdfRenderSlot() {
  activePdfRenders = Math.max(0, activePdfRenders - 1);
  const next = pdfRenderWaiters.shift();
  if (next) next();
}
let statistics = { activities: { reading: { days: {}, totalSeconds: 0 }, work: { days: {}, totalSeconds: 0, categories: {} } }, books: {}, workTimer: { running: false, category: "research", hidden: false }, hidden: false, expanded: false }, lastActivityAt = Date.now(), lastStatsTick = Date.now(), lastWorkTick = Date.now();
let profileSyncTimer = null, profileSyncRetryTimer = null, profileSyncReady = false, profileSyncBusy = false;
const syncDeviceId = localStorage.getItem(DEVICE_ID_STORAGE) || `device-${crypto.randomUUID?.() || `${Date.now()}-${Math.random().toString(36).slice(2)}`}`;
localStorage.setItem(DEVICE_ID_STORAGE, syncDeviceId);
let syncShadow = (() => { try { const value = JSON.parse(localStorage.getItem(SYNC_SHADOW_STORAGE) || "null"); return value && typeof value === "object" ? value : { revision: 0, records: {} }; } catch { return { revision: 0, records: {} }; } })();

const uid = () => `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 7)}`;
const clamp = (value, min, max) => Math.max(min, Math.min(max, Math.round(Number(value) || min)));
const bookFor = (id) => books.get(id) || books.values().next().value;
const activeTab = () => tabs.find((tab) => tab.id === activeTabId) || tabs[0];
const activePane = () => activeTab()?.panes[activeTab().activePane || 0];
const progressKey = (bookId) => `one-page-progress:${bookId}`;
const savedProgress = (bookId) => {
  const stored = Number(localStorage.getItem(progressKey(bookId)));
  if (stored > 0) return stored;
  return bookId === "cancer-biology-2" ? LEGACY_PROGRESS : 1;
};
const noteKey = (bookId, page) => `${bookId}::${page}`;
const highlightKey = (bookId, page) => `${bookId}::${page}`;

function restorePersonalData() {
  try { preferences = { ...preferences, ...JSON.parse(localStorage.getItem(PREFERENCES_STORAGE) || "{}") }; } catch {}
  try { notes = JSON.parse(localStorage.getItem(NOTES_STORAGE) || "{}") || {}; } catch { notes = {}; }
  try { bookmarks = JSON.parse(localStorage.getItem(BOOKMARKS_STORAGE) || "{}") || {}; } catch { bookmarks = {}; }
  try { highlights = JSON.parse(localStorage.getItem(HIGHLIGHTS_STORAGE) || "{}") || {}; } catch { highlights = {}; }
  try { recent = JSON.parse(localStorage.getItem(RECENT_STORAGE) || "[]") || []; } catch { recent = []; }
  try {
    const storedStats = JSON.parse(localStorage.getItem(STATS_STORAGE) || "{}"); statistics = { ...statistics, ...storedStats }; statistics.books ||= {}; statistics.activities ||= {};
    statistics.activities.reading ||= { days: storedStats.days || {}, totalSeconds: Object.values(storedStats.days || {}).reduce((sum, seconds) => sum + Number(seconds || 0), 0) };
    statistics.activities.reading.days ||= storedStats.days || {}; statistics.activities.reading.totalSeconds ||= Object.values(statistics.activities.reading.days).reduce((sum, seconds) => sum + Number(seconds || 0), 0);
    statistics.activities.work ||= { days: {}, totalSeconds: 0, categories: {} }; statistics.activities.work.days ||= {}; statistics.activities.work.categories ||= {};
    statistics.workTimer = { running: false, category: "research", hidden: false, ...(statistics.workTimer || {}) }; statistics.workTimer.running = false;
  } catch {}
  applyPreferences();
}
function applyPreferences() {
  const theme = ["light", "sepia", "dark"].includes(preferences.theme) ? preferences.theme : "light";
  preferences.theme = theme; preferences.pageScale = clamp(preferences.pageScale, 75, 150); preferences.uiScale = clamp(preferences.uiScale, 90, 125);
  document.documentElement.dataset.theme = theme;
  document.documentElement.style.setProperty("--page-scale", String(preferences.pageScale / 100));
  document.documentElement.style.setProperty("--page-width-percent", `${preferences.pageScale}%`);
  document.documentElement.style.setProperty("--page-max", `${Math.round(860 * preferences.pageScale / 100)}px`);
  document.documentElement.style.setProperty("--ui-scale", String(preferences.uiScale / 100));
  document.querySelector('meta[name="theme-color"]')?.setAttribute("content", theme === "dark" ? "#171c19" : theme === "sepia" ? "#e9dfc8" : "#f4f0e8");
  document.querySelectorAll("[data-theme-option]").forEach((button) => button.classList.toggle("active", button.dataset.themeOption === theme));
  if ($("#pageScale")) { $("#pageScale").value = preferences.pageScale; $("#pageScaleValue").textContent = `${preferences.pageScale}%`; }
  if ($("#uiScale")) { $("#uiScale").value = preferences.uiScale; $("#uiScaleValue").textContent = `${preferences.uiScale}%`; }
}
function syncTargetLabel() { return ["localhost", "127.0.0.1"].includes(location.hostname) ? "本机数据库" : "私人书库"; }
function setSyncStatus(state, text, detail = "") {
  const badge = $("#syncStatus"), label = $("#syncStatusText"); if (!badge || !label) return;
  badge.className = `sync-status ${state}`; label.textContent = text; badge.title = detail || "点击重新同步";
}
function scheduleProfileSync(delay = 1800) {
  if (!profileSyncReady) { setSyncStatus("local", "已保存在本设备", "服务器暂未连接；数据仍安全保存在浏览器中。"); return; }
  clearTimeout(profileSyncTimer); setSyncStatus("pending", "等待同步", `将保存到${syncTargetLabel()}`); profileSyncTimer = setTimeout(saveServerProfile, delay);
}
function savePreferences() { localStorage.setItem(PREFERENCES_STORAGE, JSON.stringify(preferences)); applyPreferences(); scheduleProfileSync(); }
function recordRecent(bookId, page = savedProgress(bookId)) {
  recent = [{ bookId, page, openedAt: new Date().toISOString() }, ...recent.filter((item) => item.bookId !== bookId)].slice(0, 20); localStorage.setItem(RECENT_STORAGE, JSON.stringify(recent)); scheduleProfileSync();
}
function recentLabel(bookId) {
  const item = recent.find((entry) => entry.bookId === bookId); if (!item) return "继续阅读"; const date = new Date(item.openedAt), today = localDay();
  if (localDay(date) === today) return `今日 ${date.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" })}`;
  return date.toLocaleDateString("zh-CN", { month: "numeric", day: "numeric" });
}

function chapterFor(book, page) {
  return (book.outline || []).filter((item) => item.pageNumber && item.pageNumber <= page).at(-1) || { title: book.title, pageNumber: 1 };
}
function chapterLabel(book, page) {
  return (book.outline || []).filter((item) => item.depth === 0 && item.pageNumber && item.pageNumber <= page).at(-1)?.title || chapterFor(book, page).title;
}
const pageUrl = (book, page) => `${book.assetBase}/pages/page-${page}.jpg`;
function preload(book, page) { if (!book.localDirect && page <= book.pageCount) { const image = new Image(); image.src = pageUrl(book, page); } }
async function pdfjsLibrary() {
  if (!pdfjsPromise) pdfjsPromise = import("./vendor/pdfjs/pdf.min.mjs").then((module) => { module.GlobalWorkerOptions.workerSrc = "vendor/pdfjs/pdf.worker.compat.mjs"; return module; });
  return pdfjsPromise;
}
async function pdfDocumentFor(book) {
  if (!pdfDocuments.has(book.id)) {
    const pdfjs = await pdfjsLibrary();
    const url = new URL(book.pdfUrl, location.href).href;
    const loading = pdfjs.getDocument({
      url,
      rangeChunkSize: 1024 * 1024,
      disableAutoFetch: true,
      disableStream: true,
      cMapUrl: "vendor/pdfjs/cmaps/",
      cMapPacked: true,
      standardFontDataUrl: "vendor/pdfjs/standard_fonts/",
    }).promise.catch((error) => {
      pdfDocuments.delete(book.id);
      throw error;
    });
    pdfDocuments.set(book.id, loading);
  }
  return pdfDocuments.get(book.id);
}
async function renderBookPage(container, book, page, markerSide = "right", missing = null) {
  const loader = container.querySelector(".page-loader"), image = container.querySelector("img");
  if (!book.localDirect) {
    image.src = pageUrl(book, page); image.alt = `${book.title}第 ${page} 页`;
    image.onload = () => { loader.hidden = true; renderAnnotationLayer(container, book, page, markerSide); };
    image.onerror = () => { loader.hidden = true; image.hidden = true; if (missing) missing.hidden = false; else container.classList.add("page-error"); };
    renderAnnotationLayer(container, book, page, markerSide);
    return;
  }
  image.hidden = true;
  await acquirePdfRenderSlot();
  try {
    for (let attempt = 0; attempt < 3; attempt += 1) {
      try {
        const documentProxy = await pdfDocumentFor(book), pdfPage = await documentProxy.getPage(page);
        if (!container.isConnected) return;
        const base = pdfPage.getViewport({ scale: 1 });
        const width = Math.min(1800, Math.max(760, (container.clientWidth || 900) * Math.min(window.devicePixelRatio || 1, 1.7)));
        const viewport = pdfPage.getViewport({ scale: width / base.width });
        let canvas = container.querySelector("canvas.pdf-page-canvas");
        if (!canvas) { canvas = document.createElement("canvas"); canvas.className = "pdf-page-canvas"; image.after(canvas); }
        canvas.width = Math.ceil(viewport.width); canvas.height = Math.ceil(viewport.height); canvas.setAttribute("aria-label", `${book.title}第 ${page} 页`);
        await pdfPage.render({ canvasContext: canvas.getContext("2d", { alpha: false }), viewport }).promise;
        if (!container.isConnected) return;
        loader.hidden = true; renderAnnotationLayer(container, book, page, markerSide);
        return;
      } catch (error) {
        if (attempt < 2) {
          container.querySelector("canvas.pdf-page-canvas")?.remove();
          await new Promise((resolve) => setTimeout(resolve, 350 * (attempt + 1)));
          continue;
        }
        throw error;
      }
    }
  } catch (error) {
    console.error("Direct PDF render failed", error?.name || "", error?.message || "", error); loader.hidden = true; if (missing) missing.hidden = false; else container.classList.add("page-error");
  } finally {
    releasePdfRenderSlot();
  }
}
const SVG_NS = "http://www.w3.org/2000/svg";
function pageHighlights(bookId, page) { return highlights[highlightKey(bookId, page)] || []; }
function persistHighlights() { localStorage.setItem(HIGHLIGHTS_STORAGE, JSON.stringify(highlights)); scheduleProfileSync(); }
function setAnnotationTool(tool = "none") {
  annotationTool = annotationTool === tool ? "none" : tool;
  document.documentElement.dataset.annotationTool = annotationTool;
  document.querySelectorAll("[data-annotation-tool]").forEach((button) => {
    const active = button.dataset.annotationTool === annotationTool; button.classList.toggle("active", active); button.setAttribute("aria-pressed", String(active));
  });
  const status = $("#annotationToolStatus"), labels = { none: "再次点击可取消", highlight: "拖动书页 · 点击取消", erase: "点击删除 · 点击取消", marker: "点击页边 · 点击取消" };
  if (status) status.textContent = labels[annotationTool] || labels.none;
  closeHighlightEditor(); hideHighlightTooltip(); refreshVisibleHighlightsForOpenPanes();
}
function setAnnotationToolsHidden(hidden) {
  annotationToolsHidden = Boolean(hidden); localStorage.setItem(ANNOTATION_TOOLS_HIDDEN_STORAGE, String(annotationToolsHidden));
  $("#annotationTools").hidden = annotationToolsHidden; $("#annotationToolsRestore").hidden = !annotationToolsHidden;
  if (annotationToolsHidden && annotationTool !== "none") setAnnotationTool(annotationTool);
}
function svgPoint(svg, event) {
  const rect = svg.getBoundingClientRect(); return { x: Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width)), y: Math.max(0, Math.min(1, (event.clientY - rect.top) / rect.height)) };
}
function polylinePoints(points) { return points.map((point) => `${Math.round(point.x * 1000)},${Math.round(point.y * 1000)}`).join(" "); }
function positionFloating(element, clientX, clientY) {
  element.hidden = false; const margin = 12, width = Math.min(330, window.innerWidth - margin * 2), left = Math.min(window.innerWidth - width - margin, Math.max(margin, clientX + 14));
  element.style.left = `${left}px`; element.style.top = `${Math.min(window.innerHeight - element.offsetHeight - margin, Math.max(margin, clientY + 12))}px`;
}
function hideHighlightTooltip() { $("#highlightTooltip").hidden = true; }
function showHighlightTooltip(text, event) {
  if (!text) return; const tooltip = $("#highlightTooltip"); tooltip.textContent = text; tooltip.hidden = false;
  const margin = 10, left = Math.min(window.innerWidth - tooltip.offsetWidth - margin, event.clientX + 13), top = Math.min(window.innerHeight - tooltip.offsetHeight - margin, event.clientY + 13); tooltip.style.left = `${Math.max(margin, left)}px`; tooltip.style.top = `${Math.max(margin, top)}px`;
}
function removeHighlight(bookId, page, id) {
  const key = highlightKey(bookId, page); highlights[key] = pageHighlights(bookId, page).filter((item) => item.id !== id); if (!highlights[key].length) delete highlights[key];
  persistHighlights(); closeHighlightEditor(); hideHighlightTooltip(); refreshVisibleHighlights(bookId, page);
}
function renderAnnotationLayer(container, book, page, markerSide = null) {
  let svg = container.querySelector(":scope > .annotation-layer");
  if (!svg) { svg = document.createElementNS(SVG_NS, "svg"); svg.classList.add("annotation-layer"); svg.setAttribute("viewBox", "0 0 1000 1000"); svg.setAttribute("preserveAspectRatio", "none"); container.append(svg); }
  const resolvedMarkerSide = markerSide || svg.dataset.markerSide || "right"; svg.dataset.bookId = book.id; svg.dataset.page = String(page); svg.dataset.markerSide = resolvedMarkerSide; svg.replaceChildren();
  for (const item of pageHighlights(book.id, page)) {
    const anchor = item.points?.at(-1) || { x: .5, y: .5 };
    if (item.type !== "marker" && (item.points || []).length > 1) {
      const line = document.createElementNS(SVG_NS, "polyline"); line.classList.add("annotation-stroke"); line.setAttribute("points", polylinePoints(item.points || [])); line.setAttribute("stroke", item.color || "#f4d35e"); line.setAttribute("stroke-width", "30");
      line.dataset.highlightId = item.id; if (locatedHighlight?.id === item.id && locatedHighlight.bookId === book.id && locatedHighlight.page === page) line.classList.add("located");
      line.addEventListener("pointerdown", (event) => { if (annotationTool === "erase") event.stopPropagation(); });
      line.addEventListener("click", (event) => { if (annotationTool !== "erase") return; event.stopPropagation(); removeHighlight(book.id, page, item.id); }); svg.append(line);
    }
    if (item.type === "marker" || item.note) {
      const markerX = resolvedMarkerSide === "left" ? -16 : 1016, marker = document.createElementNS(SVG_NS, "g"); marker.classList.add("annotation-note-marker", `marker-${resolvedMarkerSide}`); marker.setAttribute("transform", `translate(${markerX} ${anchor.y * 1000})`); marker.dataset.highlightId = item.id;
      if (locatedHighlight?.id === item.id && locatedHighlight.bookId === book.id && locatedHighlight.page === page) marker.classList.add("located");
      const dot = document.createElementNS(SVG_NS, "ellipse"), pageAspect = Math.max(.35, Math.min(1.4, (container.clientWidth || 1) / (container.clientHeight || 1))); dot.classList.add("annotation-dot"); dot.setAttribute("rx", "7"); dot.setAttribute("ry", String(7 * pageAspect)); dot.style.fill = item.color || "#f4d35e"; marker.append(dot);
      const title = document.createElementNS(SVG_NS, "title"); title.textContent = item.note ? "查看页边批注" : "添加页边批注"; marker.append(title);
      marker.addEventListener("pointerdown", (event) => event.stopPropagation()); marker.addEventListener("click", (event) => { event.stopPropagation(); if (annotationTool === "erase") removeHighlight(book.id, page, item.id); else openHighlightEditor(book.id, page, item.id, event.clientX, event.clientY); });
      marker.addEventListener("pointerenter", (event) => showHighlightTooltip(item.note, event)); marker.addEventListener("pointermove", (event) => showHighlightTooltip(item.note, event)); marker.addEventListener("pointerleave", hideHighlightTooltip); svg.append(marker);
    }
  }
  let drawing = null, draft = null;
  svg.onpointerdown = (event) => {
    if (event.button > 0 || event.target.closest?.(".annotation-note-marker,.annotation-stroke")) return;
    if (annotationTool === "marker") { event.preventDefault(); const point = svgPoint(svg, event), key = highlightKey(book.id, page), item = { id: uid(), type: "marker", bookId: book.id, page, color: "#f4d35e", points: [point], note: "", createdAt: new Date().toISOString(), updatedAt: new Date().toISOString() }; highlights[key] = [...(highlights[key] || []), item]; persistHighlights(); refreshVisibleHighlights(book.id, page); openHighlightEditor(book.id, page, item.id, event.clientX, event.clientY); return; }
    if (annotationTool !== "highlight") return; event.preventDefault(); hideHighlightTooltip(); svg.setPointerCapture(event.pointerId); drawing = [svgPoint(svg, event)];
    draft = document.createElementNS(SVG_NS, "polyline"); draft.classList.add("annotation-draft"); draft.setAttribute("stroke", "#f4d35e"); draft.setAttribute("stroke-width", "30"); svg.append(draft);
  };
  svg.onpointermove = (event) => { if (!drawing) return; event.preventDefault(); const point = svgPoint(svg, event), previous = drawing.at(-1); if (Math.hypot(point.x - previous.x, point.y - previous.y) > .003) drawing.push(point); draft.setAttribute("points", polylinePoints(drawing)); };
  const finish = (event) => {
    if (!drawing) return; event.preventDefault(); const points = drawing; drawing = null; draft?.remove(); draft = null; if (points.length < 2) return;
    const key = highlightKey(book.id, page), item = { id: uid(), type: "highlight", bookId: book.id, page, color: "#f4d35e", points, note: "", createdAt: new Date().toISOString(), updatedAt: new Date().toISOString() };
    highlights[key] = [...(highlights[key] || []), item]; persistHighlights(); refreshVisibleHighlights(book.id, page); openHighlightEditor(book.id, page, item.id, event.clientX, event.clientY);
  };
  svg.onpointerup = finish; svg.onpointercancel = finish;
}
function refreshVisibleHighlights(bookId, page) {
  document.querySelectorAll(`.annotation-layer[data-book-id="${CSS.escape(bookId)}"][data-page="${page}"]`).forEach((svg) => { const container = svg.parentElement, book = bookFor(bookId); if (container && book) renderAnnotationLayer(container, book, page); });
  if ($("#notesPanel").classList.contains("open")) renderNotesPanel();
}
function refreshVisibleHighlightsForOpenPanes() {
  document.querySelectorAll(".annotation-layer").forEach((svg) => { const book = bookFor(svg.dataset.bookId), page = Number(svg.dataset.page); if (book && page) renderAnnotationLayer(svg.parentElement, book, page); });
}
function openHighlightEditor(bookId, page, id, clientX = window.innerWidth / 2, clientY = window.innerHeight / 2) {
  const item = pageHighlights(bookId, page).find((candidate) => candidate.id === id); if (!item) return; hideHighlightTooltip(); editingHighlight = { bookId, page, id };
  $("#highlightNoteText").value = item.note || ""; document.querySelectorAll("[data-highlight-color]").forEach((button) => button.classList.toggle("active", button.dataset.highlightColor === item.color)); positionFloating($("#highlightEditor"), clientX, clientY); $("#highlightNoteText").focus();
}
function closeHighlightEditor() { $("#highlightEditor").hidden = true; editingHighlight = null; }
function locateHighlight(bookId, page, id = null) {
  const pane = activePane(); if (!pane || pane.bookId !== bookId) return; pane.returnPage = pane.mode === "reading" ? pane.page : (pane.returnPage || savedProgress(bookId)); pane.page = page; pane.mode = "peek"; pane.viewMode = "page";
  locatedHighlight = id ? { bookId, page, id } : null; closeNotes(); renderWorkspace();
  if (!id) return;
  const reveal = () => {
    const target = document.querySelector(`.annotation-layer[data-book-id="${CSS.escape(bookId)}"][data-page="${page}"] [data-highlight-id="${CSS.escape(id)}"]`); if (!target) return false;
    target.classList.add("located"); target.scrollIntoView({ block: "center", inline: "nearest", behavior: "smooth" }); const rect = target.getBoundingClientRect(); openHighlightEditor(bookId, page, id, rect.left + rect.width / 2, rect.top + rect.height / 2); return true;
  };
  requestAnimationFrame(() => { if (!reveal()) setTimeout(reveal, 350); }); setTimeout(() => { if (locatedHighlight?.id === id) { locatedHighlight = null; refreshVisibleHighlights(bookId, page); } }, 2600);
}
function updateEditingHighlight(mutator) {
  if (!editingHighlight) return null; const items = pageHighlights(editingHighlight.bookId, editingHighlight.page), item = items.find((candidate) => candidate.id === editingHighlight.id); if (!item) return null; mutator(item); item.updatedAt = new Date().toISOString(); highlights[highlightKey(editingHighlight.bookId, editingHighlight.page)] = items; persistHighlights(); refreshVisibleHighlights(editingHighlight.bookId, editingHighlight.page); return item;
}
function saveHighlightNote() { updateEditingHighlight((item) => { item.note = $("#highlightNoteText").value.trim(); }); closeHighlightEditor(); }
function deleteEditingHighlight() {
  if (!editingHighlight) return; const { bookId, page, id } = editingHighlight; removeHighlight(bookId, page, id);
}
function makePane(bookId, page = savedProgress(bookId)) { return { bookId, page, mode: "reading", returnPage: page, viewMode: DEFAULT_VIEW_MODE }; }
function pickCompanionBook(currentId) {
  const recentMatch = recent.find((item) => item.bookId !== currentId && books.has(item.bookId));
  if (recentMatch) return recentMatch.bookId;
  const localMatch = library.find((item) => item.id !== currentId && item.localDirect);
  return localMatch?.id || library.find((item) => item.id !== currentId)?.id || currentId;
}
function syncTabTitle(tab) {
  const first = books.get(tab.panes?.[0]?.bookId), second = books.get(tab.panes?.[1]?.bookId);
  tab.title = tab.split && first?.id !== second?.id ? "对照阅读" : (first?.title || "阅读");
}
function makeTab(bookId, page) {
  const book = bookFor(bookId), start = page || savedProgress(bookId), companion = bookFor(pickCompanionBook(book.id));
  return { id: uid(), title: book.title, split: false, activePane: 0, panes: [makePane(book.id, start), makePane(companion.id, savedProgress(companion.id))] };
}
function persist() { localStorage.setItem(STORAGE, JSON.stringify({ tabs, activeTabId })); scheduleProfileSync(); }
function restoreTabs() {
  try {
    const stored = JSON.parse(localStorage.getItem(STORAGE));
    tabs = (stored?.tabs || []).filter((tab) => tab.panes?.every((pane) => books.has(pane.bookId)));
    activeTabId = tabs.some((tab) => tab.id === stored?.activeTabId) ? stored.activeTabId : tabs[0]?.id;
  } catch { tabs = []; }
  if (!localStorage.getItem(VIEW_MODE_MIGRATION_KEY)) {
    tabs.forEach((tab) => (tab.panes || []).forEach((pane) => { if (pane.viewMode === "page") pane.viewMode = DEFAULT_VIEW_MODE; }));
    localStorage.setItem(VIEW_MODE_MIGRATION_KEY, "1");
  }
  if (!localStorage.getItem(SPLIT_PANE_MIGRATION_KEY)) {
    tabs.forEach((tab) => {
      if (!tab.split || !tab.panes?.[1] || tab.panes[0].bookId !== tab.panes[1].bookId) return;
      const companionId = pickCompanionBook(tab.panes[0].bookId);
      if (companionId === tab.panes[0].bookId) return;
      const companion = books.get(companionId), pane = tab.panes[1];
      if (!companion || !pane) return;
      pane.bookId = companion.id; pane.page = clamp(savedProgress(companion.id), 1, companion.pageCount); pane.returnPage = pane.page; pane.mode = "reading"; syncTabTitle(tab);
    });
    localStorage.setItem(SPLIT_PANE_MIGRATION_KEY, "1");
  }
  if (!tabs.length) { tabs = [makeTab(library[0].id)]; activeTabId = tabs[0].id; }
}

function renderShelf() {
  const shelf = $("#continueShelf"); shelf.innerHTML = ""; renderTagFilters();
  const recentOrder = new Map(recent.map((item, index) => [item.bookId, index]));
  const filtered = [...library].sort((a, b) => (recentOrder.get(a.id) ?? 9999) - (recentOrder.get(b.id) ?? 9999)).filter((item) => {
    const book = bookFor(item.id), page = clamp(savedProgress(book.id), 1, book.pageCount);
    const tags = [...new Set(Object.values(notes).filter((note) => note.bookId === book.id).flatMap((note) => note.tags || []))].slice(0, 24);
    const searchable = `${book.title} ${chapterLabel(book, page)} ${tags.join(" ")}`.toLowerCase();
    return (!shelfQuery || searchable.includes(shelfQuery)) && (!activeShelfTag || tags.includes(activeShelfTag));
  });
  const visibleCount = filtered.length, totalPages = Math.max(1, Math.ceil(visibleCount / SHELF_PAGE_SIZE));
  shelfPage = clamp(shelfPage, 1, totalPages);
  const pageItems = filtered.slice((shelfPage - 1) * SHELF_PAGE_SIZE, shelfPage * SHELF_PAGE_SIZE);
  for (const item of pageItems) {
    const book = bookFor(item.id), page = clamp(savedProgress(book.id), 1, book.pageCount), card = document.createElement("article");
    const tags = [...new Set(Object.values(notes).filter((note) => note.bookId === book.id).flatMap((note) => note.tags || []))].slice(0, 24);
    card.className = "book-card";
    card.innerHTML = `<img class="book-cover" alt=""><div class="book-copy"><p class="overline recent-label"></p><div class="book-badges"></div><h2></h2><p class="chapter-label"></p><p class="book-size-label"></p><div class="book-tags"></div><div class="progress-track"><span></span></div><p class="progress-text"></p><div class="card-actions"><button class="primary-button continue">继续阅读</button><button class="secondary-button new-session">新建窗口</button><button class="secondary-button publish-stream" hidden>生成流式版本</button><button class="secondary-button cloud-publish" hidden>发布到云端</button><button class="secondary-button manage-book">管理</button></div></div>`;
    card.querySelector("img").src = item.cover; card.querySelector("img").alt = `${book.title}封面`; card.querySelector("h2").textContent = book.title;
    const flags = bookSourceFlags(item), badges = card.querySelector(".book-badges");
    if (flags.hasLocal) { const badge = document.createElement("span"); badge.className = `book-source-label${flags.hasStream ? "" : " local-only"}`; badge.textContent = "本地 PDF"; badges.append(badge); }
    if (flags.hasStream) { const badge = document.createElement("span"); badge.className = "book-source-label stream"; badge.textContent = "流式版"; badges.append(badge); }
    card.querySelector(".book-size-label").textContent = bookSizeSummary(item, book);
    card.querySelector(".recent-label").textContent = recentLabel(book.id);
    card.querySelector(".chapter-label").textContent = chapterLabel(book, page); card.querySelector(".progress-track span").style.width = `${page / book.pageCount * 100}%`;
    card.querySelector(".progress-text").textContent = `第 ${page} / ${book.pageCount} 页`;
    const tagBox = card.querySelector(".book-tags"); tags.slice(0, 6).forEach((tag) => { const chip = document.createElement("span"); chip.textContent = tag; tagBox.append(chip); }); tagBox.hidden = !tags.length;
    const publish = card.querySelector(".publish-stream"); publish.hidden = !item.localDirect || item.streamReady; publish.onclick = () => publishLocalBook(book.id, publish);
    const cloud = card.querySelector(".cloud-publish"), cloudInfo = libraryInfo.get(item.id), canCloud = Boolean(cloudInfo && cloudInfo.removable);
    cloud.hidden = !canCloud;
    if (canCloud) { cloud.textContent = publishedMap[item.id] ? "重新发布" : "发布到云端"; cloud.onclick = () => publishToCloud(book.id, cloud); }
    card.querySelector(".continue").onclick = () => openBook(book.id, false); card.querySelector(".new-session").onclick = () => openBook(book.id, true); card.querySelector(".manage-book").onclick = () => openManageBook(book.id); bindBookDrag(card, book.id); shelf.append(card);
  }
  $("#libraryCount").textContent = visibleCount === library.length ? `${library.length} 本书 · 每页 ${SHELF_PAGE_SIZE} 本` : `找到 ${visibleCount} / ${library.length} 本`;
  if (!visibleCount) shelf.innerHTML = `<div class="empty-library"><strong>没有找到符合条件的书</strong><span>试试清除搜索内容或标签筛选。</span><button class="secondary-button">清除筛选</button></div>`;
  shelf.querySelector(".empty-library button")?.addEventListener("click", () => { shelfQuery = ""; activeShelfTag = ""; shelfPage = 1; $("#librarySearch").value = ""; renderShelf(); });
  renderShelfPagination(visibleCount, totalPages);
}
function shelfPageNumbers(totalPages) {
  if (totalPages <= 7) return Array.from({ length: totalPages }, (_, index) => index + 1);
  const pages = new Set([1, totalPages, shelfPage - 1, shelfPage, shelfPage + 1].filter((page) => page >= 1 && page <= totalPages));
  const ordered = [...pages].sort((a, b) => a - b), result = [];
  ordered.forEach((page, index) => { if (index && page - ordered[index - 1] > 1) result.push(null); result.push(page); });
  return result;
}
function renderShelfPagination(visibleCount, totalPages) {
  const pagination = $("#libraryPagination"); pagination.innerHTML = "";
  if (!visibleCount || totalPages <= 1) { pagination.hidden = true; return; }
  pagination.hidden = false;
  const addButton = (label, page, options = {}) => {
    const button = document.createElement("button"); button.type = "button"; button.textContent = label; button.disabled = options.disabled; button.className = options.active ? "active" : "";
    if (options.active) button.setAttribute("aria-current", "page");
    button.onclick = () => { shelfPage = page; renderShelf(); document.querySelector(".section-heading")?.scrollIntoView({ behavior: "smooth", block: "start" }); };
    pagination.append(button);
  };
  addButton("上一页", shelfPage - 1, { disabled: shelfPage === 1 });
  shelfPageNumbers(totalPages).forEach((page) => { if (page === null) { const gap = document.createElement("span"); gap.className = "pagination-gap"; gap.textContent = "…"; pagination.append(gap); } else addButton(String(page), page, { active: page === shelfPage }); });
  const info = document.createElement("span"); info.className = "pagination-info"; info.textContent = `第 ${shelfPage} / ${totalPages} 页`; pagination.append(info);
  addButton("下一页", shelfPage + 1, { disabled: shelfPage === totalPages });
}
function renderTagFilters() {
  const box = $("#tagFilters"), tags = [...new Set(Object.values(notes).flatMap((note) => note.tags || []))].sort((a, b) => a.localeCompare(b, "zh-CN")); box.innerHTML = "";
  if (!tags.length) { box.hidden = true; return; } box.hidden = false;
  const all = document.createElement("button"); all.type = "button"; all.textContent = "全部"; all.className = !activeShelfTag ? "active" : ""; all.onclick = () => { activeShelfTag = ""; shelfPage = 1; renderShelf(); }; box.append(all);
  tags.forEach((tag) => { const button = document.createElement("button"); button.type = "button"; button.textContent = tag; button.className = activeShelfTag === tag ? "active" : ""; button.onclick = () => { activeShelfTag = activeShelfTag === tag ? "" : tag; shelfPage = 1; renderShelf(); }; box.append(button); });
}
function openBook(bookId, newSession) {
  if (statistics.workTimer?.running) { statistics.workTimer.running = false; persistStatistics(); renderWorkTimer(); }
  let tab = !newSession && tabs.find((candidate) => candidate.panes.some((pane) => pane.bookId === bookId));
  if (!tab) { tab = makeTab(bookId); tabs.push(tab); }
  else { const paneIndex = tab.panes.findIndex((pane) => pane.bookId === bookId); if (paneIndex >= 0) { tab.activePane = paneIndex; if (paneIndex > 0) tab.split = true; } }
  recordRecent(bookId); activeTabId = tab.id; $("#homeView").hidden = true; $("#readerView").hidden = false; renderWorkspace();
}
function closeWorkspace() { const pane = activePane(); if (pane) recordRecent(pane.bookId, pane.page); closeToc(); closeNotes(); closeHighlightEditor(); setReaderShelfOpen(false); if (annotationTool !== "none") setAnnotationTool(annotationTool); $("#readerView").hidden = true; $("#homeView").hidden = false; renderShelf(); }

function renderTabs() {
  const nav = $("#readerTabs"); nav.innerHTML = "";
  for (const tab of tabs) {
    const button = document.createElement("button"); button.className = `reader-tab ${tab.id === activeTabId ? "active" : ""}`;
    button.innerHTML = `<span class="tab-title"></span><span class="tab-close" aria-label="关闭标签">×</span>`; button.querySelector(".tab-title").textContent = tab.title;
    button.onclick = () => { activeTabId = tab.id; renderWorkspace(); };
    button.title = "点击切换标签；也可把标签拖到窗格中打开";
    const tabBook = books.get(tab.panes?.[tab.activePane || 0]?.bookId); if (tabBook) bindBookDrag(button, tabBook.id);
    button.querySelector(".tab-close").onclick = (event) => { event.stopPropagation(); closeTab(tab.id); }; nav.append(button);
  }
}
function closeTab(id) {
  if (tabs.length === 1) return closeWorkspace();
  const index = tabs.findIndex((tab) => tab.id === id); tabs.splice(index, 1);
  if (activeTabId === id) activeTabId = tabs[Math.max(0, index - 1)].id; renderWorkspace();
}
function renderWorkspace() {
  const tab = activeTab(); if (!tab) return;
  if (!tab.split) tab.activePane = 0;
  syncTabTitle(tab); renderTabs();
  $("#workspace").className = `workspace ${tab.split ? "split" : "single"}`; $("#splitButton").textContent = tab.split ? "合并" : "分屏";
  $("#splitButton").setAttribute("aria-pressed", String(tab.split)); $("#mobilePaneSwitch").hidden = !tab.split;
  document.querySelector(".reader-header")?.classList.toggle("split-active", tab.split); $("#readerView").classList.toggle("split-workspace", tab.split);
  document.querySelectorAll("#mobilePaneSwitch button").forEach((button) => button.classList.toggle("active", Number(button.dataset.pane) === tab.activePane));
  const workspace = $("#workspace"); workspace.innerHTML = "";
  for (let index = 0; index < (tab.split ? 2 : 1); index += 1) workspace.append(renderPane(tab, index));
  recordPageView(activePane()); updateFooter(); updateStatsWidget(); persist();
  if (!$("#readerShelf").hidden) renderReaderShelf();
}
function continuousPage(book, page, markerSide = "right") {
  const article = document.createElement("article"); article.className = "continuous-page"; article.dataset.page = page;
  article.innerHTML = `<span class="continuous-page-number">${page}</span><div class="page-loader"><span></span></div><img loading="lazy" decoding="async" draggable="false" alt="">`;
  renderBookPage(article, book, page, markerSide); return article;
}
function setupContinuous(section, tab, index, book, pane) {
  const stream = section.querySelector(".continuous-pages"); let first = Math.max(1, pane.page - 2), last = Math.min(book.pageCount, pane.page + 6), scheduled = false, ignoreScrollUntil = Date.now() + 1400;
  const markerSide = tab.split && index === 1 ? "left" : "right"; for (let page = first; page <= last; page += 1) stream.append(continuousPage(book, page, markerSide));
  const requestedPage = pane.page;
  const alignTarget = () => {
    const target = stream.querySelector(`[data-page="${requestedPage}"]`), header = section.querySelector(".pane-header"); if (!target || !header || !section.isConnected) return;
    section.scrollTop += target.getBoundingClientRect().top - section.getBoundingClientRect().top - header.offsetHeight - 6;
  };
  requestAnimationFrame(alignTarget); [120, 420, 900].forEach((delay) => setTimeout(alignTarget, delay));
  const targetImage = stream.querySelector(`[data-page="${requestedPage}"] img`); targetImage?.addEventListener("load", () => { ignoreScrollUntil = Date.now() + 350; alignTarget(); }, { once: true });
  function updateFromScroll() {
    scheduled = false; if (!section.isConnected || Date.now() < ignoreScrollUntil) return; const pages = [...stream.children], anchor = section.getBoundingClientRect().top + section.querySelector(".pane-header").offsetHeight + 8;
    const closest = pages.reduce((best, item) => Math.abs(item.getBoundingClientRect().top - anchor) < Math.abs(best.getBoundingClientRect().top - anchor) ? item : best, pages[0]);
    const visiblePage = Number(closest.dataset.page);
    if (visiblePage !== pane.page) {
      pane.page = visiblePage; section.querySelector(".pane-page").textContent = `${pane.page} / ${book.pageCount}`; section.querySelector(".pane-chapter").textContent = chapterLabel(book, pane.page); section.querySelector(".pane-bookmark").textContent = isBookmarked(book.id, pane.page) ? "★" : "☆";
      recordPageView(pane);
      if (pane.mode === "reading") { pane.returnPage = pane.page; localStorage.setItem(progressKey(book.id), String(pane.page)); }
      if (tab.activePane === index) updateFooter(); persist();
    }
    if (section.scrollTop + section.clientHeight > section.scrollHeight - 900 && last < book.pageCount) {
      const end = Math.min(book.pageCount, last + 5); for (let page = last + 1; page <= end; page += 1) stream.append(continuousPage(book, page, markerSide)); last = end;
      while (stream.children.length > 18) { const removed = stream.firstElementChild, height = removed.offsetHeight; removed.remove(); first += 1; section.scrollTop -= height; }
    }
    if (section.scrollTop < 700 && first > 1) {
      const oldHeight = section.scrollHeight, start = Math.max(1, first - 5); for (let page = first - 1; page >= start; page -= 1) stream.prepend(continuousPage(book, page, markerSide)); first = start; section.scrollTop += section.scrollHeight - oldHeight;
      while (stream.children.length > 18) { stream.lastElementChild.remove(); last -= 1; }
    }
  }
  section.addEventListener("scroll", () => { if (!scheduled) { scheduled = true; requestAnimationFrame(updateFromScroll); } }, { passive: true });
}
function renderPane(tab, index) {
  const pane = tab.panes[index], book = bookFor(pane.bookId); pane.page = clamp(pane.page, 1, book.pageCount); pane.viewMode ||= DEFAULT_VIEW_MODE;
  const section = document.createElement("section"); section.className = `reading-pane ${index === tab.activePane ? "active" : ""} ${pane.viewMode !== "page" ? "continuous-mode" : ""} ${pane.viewMode === "snap" ? "snap-mode" : ""}`; section.dataset.pane = index; section.dataset.bookId = book.id;
  const body = pane.viewMode === "page" ? `<div class="page-shell"><div class="page-loader"><span></span></div><img draggable="false" alt=""><div class="missing-page" hidden><h2>页面暂时无法加载</h2><p>请检查阅读服务后重试。</p></div></div>` : `<div class="continuous-pages"></div>`;
  section.innerHTML = `<header class="pane-header"><button class="pane-book" title="点击更换本书；也可以把上方书架里的书拖到这个窗格"><span class="pane-dot"></span><span class="pane-title"></span><span class="pane-book-caret">▾</span></button><button class="pane-chapter"></button><select class="pane-mode" aria-label="阅读模式"><option value="page">单页</option><option value="continuous">连续</option><option value="snap">分页</option></select><button class="pane-bookmark" aria-label="收藏当前页"></button><span class="pane-page"></span></header><div class="peek-banner" hidden><span>临时翻阅，正式进度在第 <strong></strong> 页</span><button>返回进度</button></div>${body}`;
  section.querySelector(".pane-title").textContent = book.title; section.querySelector(".pane-chapter").textContent = chapterLabel(book, pane.page); section.querySelector(".pane-page").textContent = `${pane.page} / ${book.pageCount}`; section.querySelector(".pane-mode").value = pane.viewMode;
  section.querySelector(".pane-mode").onchange = (event) => { pane.viewMode = event.target.value; renderWorkspace(); };
  section.querySelector(".pane-bookmark").textContent = isBookmarked(book.id, pane.page) ? "★" : "☆"; section.querySelector(".pane-bookmark").onclick = () => toggleBookmark(pane);
  const banner = section.querySelector(".peek-banner"); banner.hidden = pane.mode !== "peek"; banner.querySelector("strong").textContent = pane.returnPage;
  banner.querySelector("button").onclick = () => { pane.page = pane.returnPage; pane.mode = "reading"; renderWorkspace(); };
  section.querySelector(".pane-book").onclick = () => openBookChooser(index); section.querySelector(".pane-book").setAttribute("aria-label", `当前《${book.title}》，点击更换书籍`); section.querySelector(".pane-chapter").onclick = () => openToc(index); section.onpointerdown = () => { tab.activePane = index; updateActivePane(); };
  bindPaneDrop(section, tab, index);
  if (pane.viewMode === "page") {
    const shell = section.querySelector(".page-shell"), missing = section.querySelector(".missing-page");
    renderBookPage(shell, book, pane.page, tab.split && index === 1 ? "left" : "right", missing); preload(book, pane.page + 1); preload(book, pane.page + 2);
  } else setupContinuous(section, tab, index, book, pane);
  return section;
}
function updateActivePane() {
  const tab = activeTab(); document.querySelectorAll(".reading-pane").forEach((pane) => pane.classList.toggle("active", Number(pane.dataset.pane) === tab.activePane));
  document.querySelectorAll("#mobilePaneSwitch button").forEach((button) => button.classList.toggle("active", Number(button.dataset.pane) === tab.activePane)); recordPageView(activePane()); updateFooter(); updateStatsWidget(); persist();
}
function updateFooter() {
  const pane = activePane(), book = bookFor(pane.bookId), hasNote = Boolean(notes[noteKey(book.id, pane.page)]);
  $("#pageSlider").max = book.pageCount; $("#pageSlider").value = pane.page; $("#currentPage").textContent = pane.page; $("#totalPages").textContent = book.pageCount;
  $("#notesButton").classList.toggle("has-note", hasNote); $("#notesButton").textContent = hasNote ? "笔记 ✓" : "笔记";
  if ($("#notesPanel").classList.contains("open")) renderNotesPanel();
}
function changePage(delta) {
  const pane = activePane(), book = bookFor(pane.bookId); pane.page = clamp(pane.page + delta, 1, book.pageCount);
  if (pane.mode === "reading") { pane.returnPage = pane.page; localStorage.setItem(progressKey(book.id), String(pane.page)); } renderWorkspace();
}
function jumpTo(page) { const pane = activePane(), book = bookFor(pane.bookId); pane.page = clamp(page, 1, book.pageCount); pane.mode = "peek"; renderWorkspace(); }
function commitProgress() { const pane = activePane(); pane.mode = "reading"; pane.returnPage = pane.page; localStorage.setItem(progressKey(pane.bookId), String(pane.page)); scheduleProfileSync(); renderWorkspace(); }
function isBookmarked(bookId, page) { return (bookmarks[bookId] || []).includes(Number(page)); }
function toggleBookmark(pane) {
  const pages = new Set(bookmarks[pane.bookId] || []), page = Number(pane.page); pages.has(page) ? pages.delete(page) : pages.add(page); bookmarks[pane.bookId] = [...pages].sort((a, b) => a - b);
  localStorage.setItem(BOOKMARKS_STORAGE, JSON.stringify(bookmarks)); scheduleProfileSync(); renderWorkspace(); if ($("#notesPanel").classList.contains("open")) renderNotesPanel();
}

function localDay(date = new Date()) { return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`; }
function bookStats(bookId) { return statistics.books[bookId] ||= { seconds: 0, viewed: [] }; }
function activityStats(type) { const activity = statistics.activities[type] ||= { days: {}, totalSeconds: 0 }; activity.days ||= {}; activity.totalSeconds ||= 0; return activity; }
function workCategoryStats(category) { const work = activityStats("work"); work.categories ||= {}; const item = work.categories[category] ||= { days: {}, totalSeconds: 0 }; item.days ||= {}; item.totalSeconds ||= 0; return item; }
function persistStatistics() { localStorage.setItem(STATS_STORAGE, JSON.stringify(statistics)); scheduleProfileSync(5000); }
function recordActivityTime(type, seconds) { const activity = activityStats(type), value = Math.max(0, Number(seconds || 0)), key = localDay(); activity.days[key] = Number(activity.days[key] || 0) + value; activity.totalSeconds += value; }
function recordPageView(pane) {
  if (!pane?.bookId || !Number.isFinite(Number(pane.page))) return; const stats = bookStats(pane.bookId), page = Number(pane.page);
  if (!stats.viewed.includes(page)) { stats.viewed.push(page); stats.viewed.sort((a, b) => a - b); persistStatistics(); }
}
function markReadingActivity() { lastActivityAt = Date.now(); }
function sumActivityTime(type, period) {
  const now = new Date(), monthPrefix = localDay(now).slice(0, 7); let weekStart = new Date(now.getFullYear(), now.getMonth(), now.getDate()); weekStart.setDate(weekStart.getDate() - ((weekStart.getDay() + 6) % 7));
  return Object.entries(activityStats(type).days).reduce((total, [key, seconds]) => {
    if (period === "month" && key.startsWith(monthPrefix)) return total + Number(seconds || 0);
    if (period === "week") { const date = new Date(`${key}T00:00:00`); if (date >= weekStart && date <= now) return total + Number(seconds || 0); }
    return total;
  }, 0);
}
function formatReadingTime(seconds) {
  const minutes = Math.floor(Number(seconds || 0) / 60); if (minutes < 1) return "不足 1 分钟"; if (minutes < 60) return `${minutes} 分钟`;
  const hours = Math.floor(minutes / 60), rest = minutes % 60; return rest ? `${hours} 小时 ${rest} 分` : `${hours} 小时`;
}
function updateStatsWidget() {
  const pane = activePane(); if (!pane || !books.size) return; const book = bookFor(pane.bookId), stats = bookStats(book.id), formalPage = clamp(savedProgress(book.id), 1, book.pageCount), percent = Math.round(formalPage / book.pageCount * 100);
  const reading = activityStats("reading"); $("#statsProgress").textContent = `${percent}%`; $("#statsBook").textContent = book.title; $("#statsToday").textContent = formatReadingTime(reading.days[localDay()] || 0);
  $("#statsWeek").textContent = formatReadingTime(sumActivityTime("reading", "week")); $("#statsMonth").textContent = formatReadingTime(sumActivityTime("reading", "month")); $("#statsPages").textContent = `${stats.viewed.length} 页`;
  $("#readingStatsWidget").hidden = Boolean(statistics.hidden); $("#statsRestoreButton").hidden = !statistics.hidden;
  $("#statsDetails").hidden = !statistics.expanded; $("#statsMainButton").setAttribute("aria-expanded", String(Boolean(statistics.expanded))); $("#readingStatsWidget").classList.toggle("expanded", Boolean(statistics.expanded));
}
function tickReadingTime() {
  const now = Date.now(), elapsed = Math.min(6, Math.max(0, (now - lastStatsTick) / 1000)); lastStatsTick = now;
  if (!statistics.workTimer?.running && !$("#readerView").hidden && !document.hidden && now - lastActivityAt < 65000) {
    const pane = activePane(); if (pane) { recordActivityTime("reading", elapsed); bookStats(pane.bookId).seconds += elapsed; persistStatistics(); updateStatsWidget(); }
  }
}
function renderWorkTimer() {
  const timer = statistics.workTimer ||= { running: false, category: "general", hidden: false }, work = activityStats("work"), today = localDay();
  $("#workTimerWidget").hidden = Boolean(timer.hidden); $("#workTimerRestoreButton").hidden = !timer.hidden;
  $("#workTimerValue").textContent = `今日 ${formatReadingTime(work.days[today] || 0)}`;
  $("#workWeekTotal").textContent = formatReadingTime(sumActivityTime("work", "week")); $("#workMonthTotal").textContent = formatReadingTime(sumActivityTime("work", "month"));
  $("#workTimerToggle").textContent = timer.running ? "暂停计时" : "开始专注"; $("#workTimerToggle").classList.toggle("running", Boolean(timer.running));
}
function tickWorkTimer() {
  const now = Date.now(), elapsed = Math.min(2, Math.max(0, (now - lastWorkTick) / 1000)); lastWorkTick = now; const timer = statistics.workTimer;
  if (timer?.running && !document.hidden && $("#readerView").hidden) {
    recordActivityTime("work", elapsed); persistStatistics(); renderWorkTimer();
  }
}
function toggleWorkTimer() { const timer = statistics.workTimer; timer.running = !timer.running; lastWorkTick = Date.now(); persistStatistics(); renderWorkTimer(); }

function parseTags(value) { return [...new Set(value.split(/[,，]/).map((tag) => tag.trim()).filter(Boolean))].slice(0, 12); }
function openNotes() { renderNotesPanel(); $("#notesBackdrop").hidden = false; $("#notesPanel").classList.add("open"); $("#notesPanel").setAttribute("aria-hidden", "false"); }
function closeNotes() { $("#notesPanel").classList.remove("open"); $("#notesPanel").setAttribute("aria-hidden", "true"); $("#notesBackdrop").hidden = true; }
function renderNotesPanel() {
  const pane = activePane(); if (!pane) return; const book = bookFor(pane.bookId), key = noteKey(book.id, pane.page), current = notes[key];
  $("#noteContext").textContent = `${book.title} · 第 ${pane.page} 页 · ${chapterLabel(book, pane.page)}`;
  $("#noteText").value = current?.text || ""; $("#noteTags").value = (current?.tags || []).join("，"); $("#deleteNoteButton").hidden = !current;
  const bookmarkList = $("#bookmarksList"); bookmarkList.innerHTML = ""; const markedPages = bookmarks[book.id] || [];
  if (!markedPages.length) bookmarkList.innerHTML = `<span class="empty-bookmarks">还没有书签，点击阅读页顶部的 ☆ 即可收藏。</span>`;
  markedPages.forEach((page) => { const button = document.createElement("button"); button.type = "button"; button.className = page === pane.page ? "current" : ""; button.textContent = `第 ${page} 页`; button.onclick = () => { pane.page = page; pane.mode = "peek"; pane.viewMode = "page"; renderWorkspace(); renderNotesPanel(); }; bookmarkList.append(button); });
  const highlightList = $("#highlightNotesList"), highlightQuery = ($("#noteSearch").value || "").trim().toLowerCase(); highlightList.innerHTML = "";
  const bookHighlights = Object.values(highlights).flat().filter((item) => item.bookId === book.id && (!highlightQuery || `${item.page} ${item.note || ""}`.toLowerCase().includes(highlightQuery))).sort((a, b) => a.page - b.page || String(a.createdAt).localeCompare(String(b.createdAt)));
  const highlightGroups = new Map(); bookHighlights.forEach((item) => { if (!highlightGroups.has(item.page)) highlightGroups.set(item.page, []); highlightGroups.get(item.page).push(item); });
  $("#highlightIndexSummary").textContent = bookHighlights.length ? `${highlightGroups.size} 页 · ${bookHighlights.length} 处` : "按页归纳";
  if (!bookHighlights.length) highlightList.innerHTML = `<span class="empty-highlights">这本书还没有高亮。打开左侧“高亮”，在书页上拖动即可。</span>`;
  for (const [page, items] of highlightGroups) {
    const group = document.createElement("details"); group.className = `highlight-page-group ${page === pane.page ? "current" : ""}`; group.open = page === pane.page;
    const summary = document.createElement("summary"); summary.innerHTML = `<span>第 ${page} 页</span><em>${items.length} 处</em>`; group.append(summary);
    const branches = document.createElement("div"); branches.className = "highlight-branches";
    items.forEach((item, index) => { const button = document.createElement("button"); button.type = "button"; button.innerHTML = `<i></i><span></span><small>定位</small>`; button.querySelector("i").style.setProperty("--highlight-color", item.color || "#f4d35e"); button.querySelector("span").textContent = item.note || `${item.type === "marker" ? "页边标记" : "高亮"} ${index + 1}（未写批注）`; button.onclick = () => locateHighlight(book.id, page, item.id); branches.append(button); });
    group.append(branches); highlightList.append(group);
  }
  const list = $("#notesList"); list.innerHTML = "";
  const query = ($("#noteSearch").value || "").trim().toLowerCase();
  const bookNotes = Object.values(notes).filter((note) => note.bookId === book.id && (!query || `${note.page} ${note.text || ""} ${(note.tags || []).join(" ")}`.toLowerCase().includes(query))).sort((a, b) => a.page - b.page);
  if (!bookNotes.length) { list.innerHTML = `<p class="empty-notes">这本书还没有笔记。读到有感觉的地方，就在这里留下一句。</p>`; return; }
  for (const note of bookNotes) {
    const button = document.createElement("button"); button.className = `note-card ${note.page === pane.page ? "current" : ""}`; button.type = "button";
    button.innerHTML = `<span class="note-page"></span><span class="note-preview"></span><span class="note-card-tags"></span>`;
    button.querySelector(".note-page").textContent = `第 ${note.page} 页`; button.querySelector(".note-preview").textContent = note.text || "仅标签";
    const tags = button.querySelector(".note-card-tags"); (note.tags || []).forEach((tag) => { const chip = document.createElement("i"); chip.textContent = tag; tags.append(chip); });
    button.onclick = () => { pane.page = note.page; pane.mode = "peek"; renderWorkspace(); renderNotesPanel(); }; list.append(button);
  }
}
function saveCurrentNote() {
  const pane = activePane(), book = bookFor(pane.bookId), key = noteKey(book.id, pane.page), text = $("#noteText").value.trim(), tags = parseTags($("#noteTags").value);
  if (!text && !tags.length) return;
  notes[key] = { bookId: book.id, page: pane.page, text, tags, updatedAt: new Date().toISOString() };
  localStorage.setItem(NOTES_STORAGE, JSON.stringify(notes)); scheduleProfileSync(); renderNotesPanel(); updateFooter(); renderShelf();
  $("#saveNoteButton").textContent = "已保存"; setTimeout(() => { $("#saveNoteButton").textContent = "保存笔记"; }, 1200);
}
function deleteCurrentNote() {
  const pane = activePane(), key = noteKey(pane.bookId, pane.page); if (!notes[key] || !confirm("删除这一页的笔记和标签？")) return;
  delete notes[key]; localStorage.setItem(NOTES_STORAGE, JSON.stringify(notes)); scheduleProfileSync(); renderNotesPanel(); updateFooter(); renderShelf();
}

function dateStamp() { return new Date().toLocaleDateString("zh-CN", { year: "numeric", month: "2-digit", day: "2-digit" }).replaceAll("/", "-"); }
function downloadText(filename, text, type) {
  const url = URL.createObjectURL(new Blob([text], { type })), link = document.createElement("a"); link.href = url; link.download = filename; document.body.append(link); link.click(); link.remove(); setTimeout(() => URL.revokeObjectURL(url), 1000);
}
function readingProgressSnapshot() { return Object.fromEntries(library.map((item) => [item.id, clamp(savedProgress(item.id), 1, bookFor(item.id).pageCount)])); }
function updateDataSummary() {
  const noteList = Object.values(notes), tags = new Set(noteList.flatMap((note) => note.tags || [])), highlightCount = Object.values(highlights).reduce((sum, items) => sum + items.length, 0);
  $("#dataSummary").textContent = `${library.length} 本书 · ${noteList.length} 页笔记 · ${highlightCount} 处高亮 · ${tags.size} 个标签`;
}
function exportBackup() {
  const backup = { format: "one-page-reading-backup", version: 5, exportedAt: new Date().toISOString(), preferences, notes, bookmarks, highlights, recent, statistics, progress: readingProgressSnapshot(), workspace: { tabs, activeTabId } };
  downloadText(`一页-阅读数据-${dateStamp()}.json`, JSON.stringify(backup, null, 2), "application/json;charset=utf-8");
}
function exportNotesMarkdown() {
  const lines = ["# 一页 · 阅读笔记", "", `导出时间：${new Date().toLocaleString("zh-CN")}`, ""];
  for (const book of books.values()) {
    const bookNotes = Object.values(notes).filter((note) => note.bookId === book.id).sort((a, b) => a.page - b.page); if (!bookNotes.length) continue;
    lines.push(`## ${book.title}`, "");
    bookNotes.forEach((note) => { lines.push(`### 第 ${note.page} 页 · ${chapterLabel(book, note.page)}`, "", note.text || "（仅标签）", ""); if (note.tags?.length) lines.push(`标签：${note.tags.map((tag) => `#${tag.replaceAll(" ", "-")}`).join(" ")}`, ""); });
  }
  const annotated = Object.values(highlights).flat().filter((item) => item.note).sort((a, b) => a.page - b.page);
  if (annotated.length) { lines.push("## 高亮批注", ""); annotated.forEach((item) => { const book = bookFor(item.bookId); lines.push(`### ${book?.title || item.bookId} · 第 ${item.page} 页`, "", item.note, ""); }); }
  if (lines.length === 4) lines.push("还没有保存任何笔记。", "");
  downloadText(`一页-阅读笔记-${dateStamp()}.md`, lines.join("\n"), "text/markdown;charset=utf-8");
}
async function importBackup(file) {
  if (!file) return; let backup;
  try { backup = JSON.parse(await file.text()); } catch { alert("无法读取这个备份文件，请确认它是“一页”导出的 JSON 文件。"); return; }
  if (backup?.format !== "one-page-reading-backup" || !backup.notes || !backup.preferences) { alert("这不是有效的“一页”阅读数据备份。"); return; }
  if (!confirm("恢复后将用备份中的笔记、书签、进度和外观覆盖当前数据。书籍文件不会改变。是否继续？")) return;
  notes = backup.notes || {}; bookmarks = backup.bookmarks || {}; highlights = backup.highlights || {}; recent = Array.isArray(backup.recent) ? backup.recent : []; preferences = { ...preferences, ...(backup.preferences || {}) };
  if (backup.statistics) { statistics = { ...statistics, ...backup.statistics }; statistics.books ||= {}; statistics.activities ||= {}; statistics.activities.reading ||= { days: backup.statistics.days || {}, totalSeconds: 0 }; statistics.activities.work ||= { days: {}, totalSeconds: 0 }; localStorage.setItem(STATS_STORAGE, JSON.stringify(statistics)); }
  Object.entries(backup.progress || {}).forEach(([bookId, page]) => { if (books.has(bookId)) localStorage.setItem(progressKey(bookId), String(page)); });
  if (backup.workspace?.tabs) { tabs = backup.workspace.tabs.filter((tab) => tab.panes?.every((pane) => books.has(pane.bookId))); activeTabId = tabs.some((tab) => tab.id === backup.workspace.activeTabId) ? backup.workspace.activeTabId : tabs[0]?.id; }
  if (!tabs.length) { tabs = [makeTab(library[0].id)]; activeTabId = tabs[0].id; }
  localStorage.setItem(NOTES_STORAGE, JSON.stringify(notes)); localStorage.setItem(BOOKMARKS_STORAGE, JSON.stringify(bookmarks)); localStorage.setItem(HIGHLIGHTS_STORAGE, JSON.stringify(highlights)); localStorage.setItem(RECENT_STORAGE, JSON.stringify(recent)); localStorage.setItem(PREFERENCES_STORAGE, JSON.stringify(preferences)); persist(); applyPreferences(); renderShelf(); updateDataSummary(); $("#dataDialog").close();
  alert("阅读数据已经恢复。");
}

function openToc(index = activeTab().activePane) { tocPaneIndex = index; renderOutline(); $("#tocBackdrop").hidden = false; $("#tocPanel").classList.add("open"); $("#tocPanel").setAttribute("aria-hidden", "false"); }
function closeToc() { $("#tocPanel").classList.remove("open"); $("#tocPanel").setAttribute("aria-hidden", "true"); $("#tocBackdrop").hidden = true; }
function renderOutline(query = $("#tocSearch").value || "") {
  const tab = activeTab(), pane = tab.panes[tocPaneIndex], book = bookFor(pane.bookId), normalized = query.trim().toLowerCase(), list = $("#tocList");
  $("#tocBookTitle").textContent = book.title; list.innerHTML = "";
  for (const item of (book.outline || []).filter((entry) => entry.pageNumber && (!normalized || entry.title.toLowerCase().includes(normalized)))) {
    const button = document.createElement("button"); button.className = `toc-item depth-${Math.min(item.depth, 1)} ${chapterFor(book, pane.page).pageNumber === item.pageNumber ? "current" : ""}`;
    button.innerHTML = `<span class="toc-title"></span><span class="toc-page"></span>`; button.querySelector(".toc-title").textContent = item.title; button.querySelector(".toc-page").textContent = item.pageNumber;
    button.onclick = () => { tab.activePane = tocPaneIndex; pane.page = item.pageNumber; pane.mode = "peek"; closeToc(); renderWorkspace(); }; list.append(button);
  }
}
function openBookChooser(index) {
  chooserPaneIndex = index; const pane = activeTab().panes[index], choices = $("#bookChoices"); choices.innerHTML = "";
  for (const item of library) {
    const book = bookFor(item.id), button = document.createElement("button"); button.type = "button"; button.className = `book-choice ${item.id === pane.bookId ? "current" : ""}`;
    button.innerHTML = `<img alt=""><span><strong></strong><small></small></span><span>›</span>`; button.querySelector("img").src = item.cover; button.querySelector("img").alt = `${book.title}封面`;
    button.querySelector("strong").textContent = book.title; button.querySelector("small").textContent = `${bookFormatSummary(item, book)} · 上次读到 ${savedProgress(book.id)} 页`;
    button.onclick = () => { const tab = activeTab(); $("#bookDialog").close(); assignBookToPane(tab, chooserPaneIndex, book.id); showReaderToast(`窗格已打开《${book.title}》。`); }; choices.append(button);
  }
  $("#bookDialog").showModal();
}

function bookSourceFlags(item) {
  const hasLocal = Boolean(item.localDirect);
  const hasStream = hasLocal ? Boolean(item.streamReady) : Boolean(item.metadata || item.assetBase);
  return { hasLocal, hasStream };
}
function bookSizeSummary(item, book) {
  const info = libraryInfo.get(item.id) || {}, flags = bookSourceFlags(item), parts = [`${book.pageCount} 页`];
  if (flags.hasLocal && Number(item.sourceBytes) > 0) parts.push(`PDF ${formatBytes(Number(item.sourceBytes))}`);
  if (flags.hasStream && Number(info.outputBytes) > 0) parts.push(`流式 ${formatBytes(Number(info.outputBytes))}`);
  return parts.join(" · ");
}
function bookFormatSummary(item, book) {
  const flags = bookSourceFlags(item), kind = flags.hasLocal && flags.hasStream ? "本地 + 流式" : flags.hasLocal ? "本地 PDF" : "流式";
  return `${bookSizeSummary(item, book)} · ${kind}`;
}
function showReaderToast(message) {
  let toast = $("#readerToast");
  if (!toast) { toast = document.createElement("div"); toast.id = "readerToast"; toast.className = "reader-toast"; document.body.append(toast); }
  toast.textContent = message; clearTimeout(readerToastTimer); requestAnimationFrame(() => toast.classList.add("show"));
  readerToastTimer = setTimeout(() => toast.classList.remove("show"), 2800);
}
function bindBookDrag(element, bookId) {
  element.draggable = true; element.dataset.bookId = bookId;
  element.addEventListener("dragstart", (event) => {
    draggingBookId = bookId;
    if (event.dataTransfer) { event.dataTransfer.effectAllowed = "copy"; event.dataTransfer.setData("text/bookbot-id", bookId); event.dataTransfer.setData("text/plain", bookId); }
    element.classList.add("dragging"); document.body.classList.add("dragging-book");
  });
  element.addEventListener("dragend", () => {
    draggingBookId = ""; element.classList.remove("dragging"); document.body.classList.remove("dragging-book");
    document.querySelectorAll(".reading-pane.drop-target, .reading-pane.drop-left, .reading-pane.drop-right").forEach((node) => node.classList.remove("drop-target", "drop-left", "drop-right"));
  });
}
function bookDragId(event) {
  const fromData = event.dataTransfer?.getData("text/bookbot-id") || event.dataTransfer?.getData("text/plain") || "";
  return books.has(fromData) ? fromData : (books.has(draggingBookId) ? draggingBookId : "");
}
function assignBookToPane(tab, index, bookId) {
  const book = books.get(bookId); if (!tab || !book) return;
  if (index === 1) tab.split = true;
  const pane = tab.panes[index]; if (!pane) return;
  pane.bookId = book.id; pane.page = clamp(savedProgress(book.id), 1, book.pageCount); pane.returnPage = pane.page; pane.mode = "reading"; pane.viewMode ||= DEFAULT_VIEW_MODE;
  tab.activePane = index; recordRecent(book.id, pane.page); syncTabTitle(tab); renderWorkspace();
}
function bindPaneDrop(section, tab, index) {
  const clearDrop = () => section.classList.remove("drop-target", "drop-left", "drop-right");
  section.addEventListener("dragenter", (event) => { if (!draggingBookId) return; event.preventDefault(); section.classList.add("drop-target"); });
  section.addEventListener("dragover", (event) => {
    if (!draggingBookId) return; event.preventDefault(); if (event.dataTransfer) event.dataTransfer.dropEffect = "copy";
    const rect = section.getBoundingClientRect(), rightHalf = !tab.split && event.clientX > rect.left + rect.width / 2;
    section.classList.toggle("drop-left", !rightHalf); section.classList.toggle("drop-right", rightHalf);
  });
  section.addEventListener("dragleave", (event) => { if (!section.contains(event.relatedTarget)) clearDrop(); });
  section.addEventListener("drop", (event) => {
    if (!draggingBookId) return; event.preventDefault();
    const book = books.get(bookDragId(event) || draggingBookId), rect = section.getBoundingClientRect(), rightHalf = !tab.split && event.clientX > rect.left + rect.width / 2, targetIndex = rightHalf ? 1 : index;
    clearDrop(); if (!book) return;
    assignBookToPane(tab, targetIndex, book.id);
    showReaderToast(rightHalf ? `右侧已打开《${book.title}》。` : `窗格已打开《${book.title}》。`);
    setReaderShelfOpen(false);
  });
}
function setReaderShelfOpen(open) {
  const panel = $("#readerShelf"), button = $("#readerLibraryButton"), view = $("#readerView"); if (!panel) return;
  panel.hidden = !open; button?.setAttribute("aria-expanded", String(open)); view.classList.toggle("shelf-open", open);
  if (open) renderReaderShelf();
}
function renderReaderShelf() {
  const list = $("#readerShelfList"); if (!list) return;
  const tab = activeTab(), currentIds = new Set((tab?.panes || []).map((pane) => pane.bookId)); list.innerHTML = "";
  for (const item of library) {
    const book = books.get(item.id); if (!book) continue;
    const card = document.createElement("article"); card.className = `reader-shelf-book${currentIds.has(item.id) ? " current" : ""}`; card.title = "按住拖到窗格，或点击放入当前窗格";
    card.innerHTML = `<img alt=""><div><strong></strong><small></small></div>`;
    card.querySelector("img").src = item.cover; card.querySelector("img").alt = `${book.title}封面`;
    const flags = bookSourceFlags(item); card.querySelector("strong").textContent = book.title; card.querySelector("small").textContent = `${flags.hasLocal ? "本地 PDF" : "流式"} · ${bookSizeSummary(item, book)}`;
    card.onclick = () => { const active = activeTab(); if (!active) return; setReaderShelfOpen(false); assignBookToPane(active, active.activePane || 0, book.id); showReaderToast(`窗格已打开《${book.title}》。`); };
    bindBookDrag(card, book.id); list.append(card);
  }
  if (!list.children.length) list.innerHTML = `<p class="reader-shelf-empty">书库还没有可打开的书。</p>`;
}
function formatBytes(bytes) {
  if (!Number.isFinite(bytes)) return "";
  const units = ["B", "KB", "MB", "GB", "TB"]; let value = bytes, index = 0;
  while (value >= 1024 && index < units.length - 1) { value /= 1024; index += 1; }
  return `${value.toFixed(index > 1 ? 1 : 0)} ${units[index]}`;
}
function renderStatus(status) {
  const state = status.status || "idle", activeStates = ["uploading", "queued", "inspecting", "converting", "waiting"];
  $("#statusDot").className = `status-dot ${activeStates.includes(state) ? "active" : state}`;
  $("#statusTitle").textContent = status.message || "书籍助手准备就绪";
  $("#statusDetail").textContent = status.book ? `当前书籍：${status.book}` : "处理窗口已经集成在个人图书馆中";
  const percent = Number(status.percent);
  $("#statusProgress").hidden = !activeStates.includes(state) || !Number.isFinite(percent);
  $("#statusProgress").value = Number.isFinite(percent) ? percent : 0;
  if (status.disk) $("#diskStatus").textContent = `可用 ${formatBytes(status.disk.free)}\n共 ${formatBytes(status.disk.total)}`;
  if (state === "complete" && lastStatus !== "complete") loadLibrary(false).catch(console.error);
  lastStatus = state;
}
async function pollStatus() {
  try {
    const response = await fetch("api/status", { cache: "no-store" });
    if (!response.ok) throw new Error("service unavailable");
    renderStatus(await response.json());
  } catch {
    renderStatus({ status: "failed", message: "处理助手未连接", book: "请通过“启动阅读工作台.cmd”打开" });
  }
}
async function openLocalPdf() {
  const button = $("#directPdfButton"), original = button.textContent; button.disabled = true; button.textContent = "正在打开…";
  try {
    const response = await fetch("api/open-local-pdf", { method: "POST" }), result = await response.json();
    if (result.cancelled) return;
    if (!response.ok || !result.ok) throw new Error(result.error || "无法打开 PDF");
    await loadLibrary(false); openBook(result.book.id, false);
  } catch (error) { renderStatus({ status: "failed", message: error.message || "无法打开 PDF" }); }
  finally { button.disabled = false; button.textContent = original; }
}
async function publishLocalBook(bookId, button) {
  const original = button.textContent; button.disabled = true; button.textContent = "已加入队列";
  try {
    const response = await fetch("api/publish-local", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ id: bookId }) }), result = await response.json();
    if (!response.ok || !result.ok) throw new Error(result.error || "无法生成流式版本");
    await pollStatus();
  } catch (error) { renderStatus({ status: "failed", message: error.message || "无法生成流式版本" }); button.disabled = false; button.textContent = original; }
}
async function publishToCloud(bookId, button) {
  const book = bookFor(bookId), info = libraryInfo.get(bookId) || {};
  const sizeText = formatBytes(info.outputBytes || 0);
  if (!cloudConfigured) { alert("尚未配置发布服务器和发布令牌，请先设置 data/publish-config.json。"); return; }
  if (!confirm(`发布《${book.title}》到云端？\n\n页数：${book.pageCount}\n大小：${sizeText}\n\n只会上传流式页面，不会上传原始 PDF。`)) return;
  button.disabled = true; button.textContent = "准备中…";
  try {
    const response = await fetch("api/cloud-publish", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ id: bookId }) });
    const result = await response.json();
    if (!response.ok || !result.ok) throw new Error(result.error || "无法开始发布");
    await pollCloudPublish(bookId, button);
  } catch (error) {
    alert(`发布失败：${error.message || error}`);
    button.disabled = false;
    button.textContent = publishedMap[bookId] ? "重新发布" : "发布到云端";
  }
}
async function pollCloudPublish(bookId, button) {
  for (;;) {
    const response = await fetch(`api/cloud-publish/status?t=${Date.now()}`, { cache: "no-store" });
    const status = await response.json();
    if (status.bookId && status.bookId !== bookId) { await new Promise((resolve) => setTimeout(resolve, 800)); continue; }
    if (status.state === "planning") button.textContent = "准备中…";
    else if (status.state === "uploading") button.textContent = `上传中 ${status.percent || 0}%`;
    else if (status.state === "committing") button.textContent = "校验中…";
    if (status.state === "done") {
      publishedMap[bookId] = { publishedAt: Date.now() };
      button.disabled = false;
      button.textContent = "已发布";
      alert(`发布成功。服务器剩余空间：${formatBytes(status.freeBytes || 0)}。刷新手机上的 BookBot 即可看到。`);
      return;
    }
    if (status.state === "failed") throw new Error(status.error || "发布失败");
    await new Promise((resolve) => setTimeout(resolve, 800));
  }
}
function uploadBook(file) {
  if (!file || !file.name.toLowerCase().endsWith(".pdf")) return renderStatus({ status: "failed", message: "请选择 PDF 文件" });
  const request = new XMLHttpRequest(); request.open("POST", "api/import"); request.setRequestHeader("Content-Type", "application/pdf"); request.setRequestHeader("X-Book-Name", encodeURIComponent(file.name));
  request.upload.onprogress = (event) => { if (event.lengthComputable) renderStatus({ status: "uploading", book: file.name.replace(/\.pdf$/i, ""), percent: Math.round(event.loaded / event.total * 100), message: `正在导入：${Math.round(event.loaded / event.total * 100)}%` }); };
  request.onload = () => { if (request.status >= 200 && request.status < 300) pollStatus(); else renderStatus({ status: "failed", message: "导入失败，请检查磁盘空间" }); };
  request.onerror = () => renderStatus({ status: "failed", message: "导入中断，请重新选择文件" }); request.send(file);
}
async function loadLibrary(initial = false) {
  let staticLibrary = [];
  try {
    const response = await fetch(`assets/library.json?t=${Date.now()}`, { cache: "no-store" });
    if (!response.ok) throw new Error(`library index ${response.status}`);
    staticLibrary = await response.json();
  } catch (error) { console.warn("Stream library index unavailable", error); }
  let localLibrary = [];
  try { const response = await fetch(`api/local-library?t=${Date.now()}`, { cache: "no-store" }); if (response.ok) { const payload = await response.json(); localLibrary = payload.books || []; desktopMode = Boolean(payload.desktopMode); } } catch {}
  $("#directPdfButton").hidden = !desktopMode;
  const staticIds = new Set(staticLibrary.map((item) => item.id)), merged = new Map(staticLibrary.map((item) => [item.id, item]));
  localLibrary.forEach((item) => merged.set(item.id, { ...item, streamReady: staticIds.has(item.id) })); library = [...merged.values()];
  const loaded = (await Promise.all(library.map(async (item) => {
    if (item.localDirect) return item;
    try {
      const response = await fetch(`${item.metadata}?t=${Date.now()}`, { cache: "no-store" });
      if (!response.ok) throw new Error(`book metadata ${response.status}`);
      return { ...item, ...(await response.json()) };
    } catch (error) { console.warn(`Skipping unavailable stream book ${item.id}`, error); return null; }
  }))).filter(Boolean);
  library = loaded;
  books = new Map(); loaded.forEach((book) => books.set(book.id, book));
  loaded.forEach((book) => { const key = progressKey(book.id), stored = Number(localStorage.getItem(key)); if (stored > book.pageCount) localStorage.setItem(key, "1"); });
  await loadLibraryInfo();
  await loadCloudStatus();
  if (initial) restoreTabs(); renderShelf(); if (!$("#readerShelf").hidden) renderReaderShelf();
}

async function loadLibraryInfo() {
  try {
    const response = await fetch(`api/library-info?t=${Date.now()}`, { cache: "no-store" });
    if (!response.ok) throw new Error("library info unavailable");
    const payload = await response.json(); libraryInfo = new Map((payload.books || []).map((item) => [item.id, item]));
  } catch { libraryInfo = new Map(); }
}
async function loadAppVersion() {
  let serviceVersion = "", mode = desktopMode ? "本地" : "线上";
  try {
    const response = await fetch(`api/version?t=${Date.now()}`, { cache: "no-store" });
    if (response.ok) { const payload = await response.json(); serviceVersion = String(payload.version || ""); mode = payload.mode === "remote" ? "线上" : "本地"; }
  } catch {}
  const line = $("#appVersionLine");
  if (line) line.textContent = serviceVersion ? `界面 ${APP_VERSION} · 服务 ${serviceVersion} · ${mode}` : `界面 ${APP_VERSION} · 服务版本未知（线上可能仍是旧版）`;
}
async function loadCloudStatus() {
  if (!desktopMode) return;
  try {
    const response = await fetch(`api/cloud-publish/library?t=${Date.now()}`, { cache: "no-store" });
    if (!response.ok) return;
    const payload = await response.json();
    publishedMap = payload.published || {};
    cloudConfigured = Boolean(payload.configured);
  } catch {}
}
function profileSnapshot() {
  return { version: 2, preferences, notes, bookmarks, highlights, recent, statistics, progress: readingProgressSnapshot(), workspace: { tabs, activeTabId } };
}
const syncRecordKey = (kind, id) => `${kind}\u001f${id}`;
const stableValue = (value) => JSON.stringify(value ?? null);
function localSyncRecords() {
  const records = new Map(), add = (kind, id, payload) => records.set(syncRecordKey(kind, id), { kind, id: String(id), payload });
  add("settings", "default", preferences);
  Object.entries(notes).forEach(([id, payload]) => add("note", id, payload));
  Object.entries(highlights).forEach(([pageKey, items]) => (items || []).forEach((item) => add("highlight", item.id, { ...item, pageKey })));
  Object.entries(bookmarks).forEach(([bookId, pages]) => (pages || []).forEach((page) => add("bookmark", `${bookId}::${page}`, { bookId, page: Number(page) })));
  Object.entries(readingProgressSnapshot()).forEach(([bookId, page]) => add("progress", bookId, { bookId, page }));
  recent.forEach((item) => { if (item?.bookId) add("recent", item.bookId, item); });
  add("statistics", "default", statistics);
  add("workspace", "default", { tabs, activeTabId });
  return records;
}
function normalizeStatistics() {
  statistics.books ||= {}; statistics.activities ||= {};
  statistics.activities.reading ||= { days: {}, totalSeconds: 0 }; statistics.activities.reading.days ||= {};
  statistics.activities.work ||= { days: {}, totalSeconds: 0, categories: {} }; statistics.activities.work.days ||= {}; statistics.activities.work.categories ||= {};
  statistics.workTimer = { running: false, category: "research", hidden: false, ...(statistics.workTimer || {}) }; statistics.workTimer.running = false;
}
function applySyncRecords(records, allowWorkspace = false) {
  let needsRender = false;
  [...records].sort((a, b) => Number(a.revision || 0) - Number(b.revision || 0)).forEach((record) => {
    const payload = record.payload, deleted = Boolean(record.deleted);
    const remote = String(record.deviceId || "") !== syncDeviceId;
    if (record.kind === "settings" && !deleted && payload) { preferences = { ...preferences, ...payload }; if (remote) needsRender = true; }
    if (record.kind === "note") { if (deleted) delete notes[record.id]; else if (payload) notes[record.id] = payload; if (remote) needsRender = true; }
    if (record.kind === "highlight") {
      Object.keys(highlights).forEach((key) => { highlights[key] = (highlights[key] || []).filter((item) => item.id !== record.id); if (!highlights[key].length) delete highlights[key]; });
      if (!deleted && payload) { const key = payload.pageKey || highlightKey(payload.bookId, payload.page); const { pageKey, ...item } = payload; highlights[key] = [...(highlights[key] || []), item]; }
      if (remote) needsRender = true;
    }
    if (record.kind === "bookmark") {
      const marker = payload || {}; const splitAt = record.id.lastIndexOf("::"), bookId = marker.bookId || record.id.slice(0, splitAt), page = Number(marker.page || record.id.slice(splitAt + 2));
      const pages = new Set(bookmarks[bookId] || []); pages.delete(page); if (!deleted) pages.add(page); if (pages.size) bookmarks[bookId] = [...pages].sort((a, b) => a - b); else delete bookmarks[bookId];
      if (remote) needsRender = true;
    }
    if (record.kind === "progress") { const book = books.get(record.id); if (deleted) localStorage.removeItem(progressKey(record.id)); else if (book && payload) localStorage.setItem(progressKey(record.id), String(clamp(payload.page, 1, book.pageCount))); }
    if (record.kind === "recent") { recent = recent.filter((item) => item.bookId !== record.id); if (!deleted && payload) recent.push(payload); recent.sort((a, b) => String(b.openedAt || "").localeCompare(String(a.openedAt || ""))); recent = recent.slice(0, 20); }
    if (record.kind === "statistics" && !deleted && payload) { statistics = { ...statistics, ...payload }; normalizeStatistics(); }
    if (record.kind === "workspace" && allowWorkspace && !deleted && payload?.tabs) {
      const restored = payload.tabs.filter((tab) => tab.panes?.every((pane) => books.has(pane.bookId)));
      if (restored.length) { tabs = restored; activeTabId = restored.some((tab) => tab.id === payload.activeTabId) ? payload.activeTabId : restored[0].id; needsRender = true; }
    }
  });
  localStorage.setItem(PREFERENCES_STORAGE, JSON.stringify(preferences)); localStorage.setItem(NOTES_STORAGE, JSON.stringify(notes)); localStorage.setItem(BOOKMARKS_STORAGE, JSON.stringify(bookmarks));
  localStorage.setItem(HIGHLIGHTS_STORAGE, JSON.stringify(highlights)); localStorage.setItem(RECENT_STORAGE, JSON.stringify(recent)); localStorage.setItem(STATS_STORAGE, JSON.stringify(statistics));
  localStorage.setItem(STORAGE, JSON.stringify({ tabs, activeTabId })); applyPreferences(); renderShelf();
  if (needsRender && !$("#readerView").hidden && activeTab()) renderWorkspace();
}
function updateSyncShadow(records, revision) {
  syncShadow.records ||= {};
  records.forEach((record) => { syncShadow.records[syncRecordKey(record.kind, record.id)] = record; });
  syncShadow.revision = Math.max(Number(syncShadow.revision || 0), Number(revision || 0));
  localStorage.setItem(SYNC_SHADOW_STORAGE, JSON.stringify(syncShadow));
}
function pendingSyncChanges() {
  const local = localSyncRecords(), changes = [], updatedAt = new Date().toISOString(), shadowRecords = syncShadow.records || {};
  local.forEach((record, key) => { const prior = shadowRecords[key]; if (!prior || prior.deleted || stableValue(prior.payload) !== stableValue(record.payload)) changes.push({ ...record, updatedAt, deleted: false }); });
  Object.entries(shadowRecords).forEach(([key, prior]) => { if (!prior.deleted && !local.has(key)) changes.push({ kind: prior.kind, id: prior.id, updatedAt, deleted: true }); });
  return changes;
}
async function saveServerProfile() {
  if (!profileSyncReady || profileSyncBusy) return;
  profileSyncBusy = true; clearTimeout(profileSyncRetryTimer); setSyncStatus("syncing", "正在同步…", `正在保存到${syncTargetLabel()}`);
  try {
    const response = await fetch("api/sync", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ deviceId: syncDeviceId, since: Number(syncShadow.revision || 0), changes: pendingSyncChanges() }), keepalive: true });
    if (!response.ok) throw new Error(`profile save failed: ${response.status}`);
    const payload = await response.json(); applySyncRecords(payload.records || [], false); updateSyncShadow(payload.records || [], payload.serverRevision);
    const now = new Date(); setSyncStatus("synced", `${syncTargetLabel()}已保存`, `最近保存：${now.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" })}`);
  } catch {
    setSyncStatus("offline", "离线保存", "网页数据已保留在本设备；连接恢复后会自动重试。");
    clearTimeout(profileSyncRetryTimer); profileSyncRetryTimer = setTimeout(() => { if (profileSyncReady) saveServerProfile(); else initProfileSync(); }, 15000);
  } finally { profileSyncBusy = false; }
}
async function initProfileSync() {
  setSyncStatus("syncing", "正在连接书库…");
  try {
    const firstUpgrade = Number(syncShadow.revision || 0) === 0, preservedLocal = firstUpgrade && HAD_LOCAL_PROFILE ? [...localSyncRecords().values()] : null;
    const response = await fetch(`api/sync?since=${Number(syncShadow.revision || 0)}&t=${Date.now()}`, { cache: "no-store" }); if (!response.ok) throw new Error("profile unavailable"); const payload = await response.json();
    applySyncRecords(payload.records || [], firstUpgrade); updateSyncShadow(payload.records || [], payload.serverRevision);
    if (preservedLocal) applySyncRecords(preservedLocal, true);
    profileSyncReady = true; await saveServerProfile();
  } catch { profileSyncReady = false; setSyncStatus("local", "已保存在本设备", "服务器暂未连接；点击可重试。"); }
}
function openManageBook(bookId) {
  const book = bookFor(bookId), info = libraryInfo.get(bookId); if (!book) return; managedBookId = bookId;
  $("#manageBookTitle").textContent = book.title;
  const sizeParts = [`${book.pageCount} 页`]; if (Number(book.sourceBytes) > 0) sizeParts.push(`原始 PDF ${formatBytes(Number(book.sourceBytes))}`); if (Number(info?.outputBytes) > 0) sizeParts.push(`阅读版本 ${formatBytes(Number(info.outputBytes))}`);
  $("#manageBookSummary").textContent = sizeParts.join(" · ");
  const localOnly = !info && Boolean(book.localDirect), localBook = Boolean(book.localDirect), removable = Boolean(info?.removable) || localOnly, legacy = Boolean(info?.legacy), shared = Boolean(info?.shared), button = $("#removeBookButton");
  button.disabled = !removable; button.textContent = localOnly ? "从书架移除" : "从书架移除并释放空间";
  $("#manageBookHint").textContent = localOnly
    ? "这本书只登记了本机 PDF 路径，没有生成流式页面。移除只会从书架移除登记，不会删除原始 PDF；笔记、书签和阅读记录都会保留。"
    : removable
      ? (legacy ? "这是早期兼容目录。移除只删除这本书自己的 book.json、conversion.json 和 pages/，不会删除书架索引、封面或其他应用资源；原始 PDF 会保留。" : localBook ? "这本书同时有本机 PDF 和流式版。移除会删除本地流式页面并移除本机 PDF 登记；不会删除原始 PDF 文件。笔记、书签和阅读记录会保留。" : "移除后，笔记、书签和阅读记录仍会保存在浏览器中；以后用原 PDF 重新生成即可继续阅读。")
      : (shared ? "这本书的目录与其他书共用，暂不提供自动移除。" : legacy ? "这是早期兼容目录；当前连接的是只读书库，请在电脑本地安全移除。" : "当前连接的是只读书库；删除请在电脑本地完成。");
  $("#manageBookDialog").showModal();
}
async function removeManagedBook() {
  const book = books.get(managedBookId), info = libraryInfo.get(managedBookId), button = $("#removeBookButton"); if (!book) return;
  const localOnly = !info && Boolean(book.localDirect), localBook = Boolean(book.localDirect), legacy = Boolean(info?.legacy);
  const confirmMessage = localOnly
    ? `确定从书架移除《${book.title}》吗？\n\n这本书只登记了本机 PDF 路径，没有生成流式页面。移除只会删除书架登记，不会删除原始 PDF；笔记、书签和阅读记录都会保留。`
    : legacy
      ? `确定从书架移除《${book.title}》吗？\n\n这是早期兼容目录，只会删除这本书自己的 book.json、conversion.json 和 pages/；不会删除 assets/library.json、封面或其他应用资源。原始 PDF、笔记、书签和阅读记录都会保留。`
      : localBook
        ? `确定从书架移除《${book.title}》吗？\n\n这本书同时有本机 PDF 和流式版。移除会删除本地流式页面并移除本机登记，但不会删除原始 PDF 文件；笔记、书签和阅读记录都会保留。`
        : `确定从书架移除《${book.title}》吗？\n\n只会删除生成的阅读页面，原始 PDF、笔记、书签和阅读记录都会保留。`;
  if (!confirm(confirmMessage)) return;
  button.disabled = true; button.textContent = "正在安全移除…";
  try {
    const response = await fetch("api/remove-book", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ id: managedBookId }) });
    const result = await response.json(); if (!response.ok) throw new Error(result.error || "移除失败");
    tabs = tabs.filter((tab) => tab.panes.every((pane) => pane.bookId !== managedBookId)); $("#manageBookDialog").close(); await loadLibrary(false);
    if (!tabs.length && library.length) { tabs = [makeTab(library[0].id)]; activeTabId = tabs[0].id; }
    else if (!tabs.some((tab) => tab.id === activeTabId)) activeTabId = tabs[0]?.id || null;
    persist(); renderShelf(); await pollStatus();
    alert(result.localOnly ? "已从书架移除登记。原始 PDF、笔记、书签和阅读记录均已保留。" : result.localRemoved ? `已从书架移除并释放 ${formatBytes(result.freedBytes)} 空间。原始 PDF 文件、笔记、书签和阅读记录均已保留。` : `已释放 ${formatBytes(result.freedBytes)} 空间。原始 PDF 和个人阅读数据均已保留。`);
  } catch (error) { alert(error.message || "移除失败，请稍后重试。"); }
  finally { button.disabled = !libraryInfo.get(managedBookId)?.removable; button.textContent = localOnly ? "从书架移除" : "从书架移除并释放空间"; }
}

$("#backButton").onclick = closeWorkspace; $("#newTabButton").onclick = () => { const pane = activePane(), tab = makeTab(pane.bookId, pane.page); tabs.push(tab); activeTabId = tab.id; renderWorkspace(); };
$("#splitButton").onclick = () => {
  const tab = activeTab(); if (!tab) return;
  tab.split = !tab.split;
  if (tab.split) {
    const first = tab.panes[0], second = tab.panes[1];
    if (first.bookId === second.bookId) {
      const companionId = pickCompanionBook(first.bookId);
      if (companionId !== first.bookId) {
        const companion = books.get(companionId);
        second.bookId = companion.id; second.page = clamp(savedProgress(companion.id), 1, companion.pageCount); second.returnPage = second.page; second.mode = "reading";
        showReaderToast(`右侧已打开《${companion.title}》；也可以从上方书架拖书进来。`);
      } else showReaderToast("书库里只有一本书，点击窗格标题可以再次选择。");
    }
  }
  tab.activePane = 0; renderWorkspace();
};
$("#readerLibraryButton").onclick = () => setReaderShelfOpen($("#readerShelf").hidden);
$("#closeReaderShelf").onclick = () => setReaderShelfOpen(false);
document.querySelectorAll("[data-annotation-tool]").forEach((button) => button.onclick = () => setAnnotationTool(button.dataset.annotationTool));
$("#closeAnnotationTools").onclick = () => setAnnotationToolsHidden(true); $("#annotationToolsRestore").onclick = () => setAnnotationToolsHidden(false); setAnnotationToolsHidden(annotationToolsHidden);
$("#closeHighlightEditor").onclick = closeHighlightEditor; $("#saveHighlightNote").onclick = saveHighlightNote; $("#deleteHighlight").onclick = deleteEditingHighlight;
document.querySelectorAll("[data-highlight-color]").forEach((button) => button.onclick = () => { updateEditingHighlight((item) => { item.color = button.dataset.highlightColor; }); document.querySelectorAll("[data-highlight-color]").forEach((item) => item.classList.toggle("active", item === button)); });
$("#tocButton").onclick = () => openToc(); $("#footerTocButton").onclick = () => openToc(); $("#closeTocButton").onclick = closeToc; $("#tocBackdrop").onclick = closeToc;
$("#notesButton").onclick = openNotes; $("#closeNotesButton").onclick = closeNotes; $("#notesBackdrop").onclick = closeNotes; $("#saveNoteButton").onclick = saveCurrentNote; $("#deleteNoteButton").onclick = deleteCurrentNote;
$("#statsMainButton").onclick = () => { statistics.expanded = !statistics.expanded; persistStatistics(); updateStatsWidget(); };
$("#closeStatsButton").onclick = () => { statistics.hidden = true; persistStatistics(); updateStatsWidget(); };
$("#statsRestoreButton").onclick = () => { statistics.hidden = false; persistStatistics(); updateStatsWidget(); };
$("#workTimerToggle").onclick = toggleWorkTimer;
$("#closeWorkTimerButton").onclick = () => { statistics.workTimer.hidden = true; statistics.workTimer.running = false; persistStatistics(); renderWorkTimer(); };
$("#workTimerRestoreButton").onclick = () => { statistics.workTimer.hidden = false; persistStatistics(); renderWorkTimer(); };
$("#noteSearch").oninput = renderNotesPanel; $("#librarySearch").oninput = (event) => { shelfQuery = event.target.value.trim().toLowerCase(); shelfPage = 1; renderShelf(); };
$("#settingsButton").onclick = () => { applyPreferences(); $("#settingsDialog").showModal(); };
$("#dataButton").onclick = () => { updateDataSummary(); $("#dataDialog").showModal(); }; $("#exportBackupButton").onclick = exportBackup; $("#exportNotesButton").onclick = exportNotesMarkdown; $("#importBackupButton").onclick = () => $("#backupFileInput").click();
$("#backupFileInput").onchange = (event) => { importBackup(event.target.files[0]).finally(() => { event.target.value = ""; }); };
$("#removeBookButton").onclick = removeManagedBook;
$("#syncStatus").onclick = () => { clearTimeout(profileSyncRetryTimer); profileSyncReady ? saveServerProfile() : initProfileSync(); };
document.querySelectorAll("[data-theme-option]").forEach((button) => button.onclick = () => { preferences.theme = button.dataset.themeOption; savePreferences(); });
$("#pageScale").oninput = (event) => { preferences.pageScale = Number(event.target.value); savePreferences(); };
$("#uiScale").oninput = (event) => { preferences.uiScale = Number(event.target.value); savePreferences(); };
$("#tocSearch").oninput = (event) => renderOutline(event.target.value); $("#previousPage").onclick = () => changePage(-1); $("#nextPage").onclick = () => changePage(1); $("#setProgressButton").onclick = commitProgress;
$("#pageSlider").onchange = (event) => jumpTo(event.target.value); $("#pageNumberButton").onclick = () => { const pane = activePane(), book = bookFor(pane.bookId); $("#pageInput").max = book.pageCount; $("#pageInput").value = pane.page; $("#pageDialog").showModal(); $("#pageInput").select(); };
$("#jumpButton").onclick = (event) => { event.preventDefault(); $("#pageDialog").close(); jumpTo($("#pageInput").value); }; $("#aboutButton").onclick = () => $("#aboutDialog").showModal();
window.addEventListener("beforeinstallprompt", (event) => { event.preventDefault(); installPrompt = event; });
window.addEventListener("appinstalled", () => { const hint = $("#installAppHint"); if (hint) hint.textContent = "已安装。可以从 Windows 开始菜单或桌面启动。"; });
$("#installAppButton").onclick = async () => {
  if (!installPrompt) { alert("当前窗口没有提供安装入口。\n\n请在 Edge 右上角菜单 → 应用 → 将此站点安装为应用。"); return; }
  installPrompt.prompt();
  const choice = await installPrompt.userChoice;
  installPrompt = null;
  const hint = $("#installAppHint");
  if (hint) hint.textContent = choice.outcome === "accepted" ? "正在安装；完成后可从开始菜单或桌面启动。" : "已取消安装。";
};
$("#directPdfButton").onclick = openLocalPdf; $("#importBookButton").onclick = () => $("#bookFileInput").click(); $("#dropZone").onclick = () => $("#bookFileInput").click(); $("#bookFileInput").onchange = (event) => uploadBook(event.target.files[0]);
for (const eventName of ["dragenter", "dragover"]) $("#dropZone").addEventListener(eventName, (event) => { event.preventDefault(); $("#dropZone").classList.add("dragging"); });
for (const eventName of ["dragleave", "drop"]) $("#dropZone").addEventListener(eventName, (event) => { event.preventDefault(); $("#dropZone").classList.remove("dragging"); });
$("#dropZone").addEventListener("drop", (event) => uploadBook(event.dataTransfer.files[0]));
document.querySelectorAll("#mobilePaneSwitch button").forEach((button) => button.onclick = () => { activeTab().activePane = Number(button.dataset.pane); updateActivePane(); });
for (const eventName of ["pointerdown", "keydown", "wheel", "touchstart"]) document.addEventListener(eventName, markReadingActivity, { passive: true });
document.addEventListener("visibilitychange", () => { lastStatsTick = Date.now(); if (!document.hidden) markReadingActivity(); });
window.addEventListener("online", () => initProfileSync());
window.addEventListener("offline", () => setSyncStatus("offline", "离线保存", "数据已保留在本设备；联网后会自动同步。"));
document.addEventListener("keydown", (event) => { if (event.key === "Escape" && !$("#highlightEditor").hidden) return closeHighlightEditor(); if (event.key === "Escape" && !$("#readerShelf").hidden) return setReaderShelfOpen(false); if ($("#readerView").hidden || $("#tocPanel").classList.contains("open") || $("#notesPanel").classList.contains("open") || document.querySelector("dialog[open]") || !$("#highlightEditor").hidden) return; if (event.key === "ArrowLeft") changePage(-1); if (event.key === "ArrowRight") changePage(1); });

async function init() {
  restorePersonalData();
  await loadLibrary(true); await loadAppVersion(); await initProfileSync(); await pollStatus(); updateStatsWidget(); renderWorkTimer(); setInterval(pollStatus, 2500); setInterval(tickReadingTime, 5000); setInterval(tickWorkTimer, 1000);
  if ("serviceWorker" in navigator) navigator.serviceWorker.register("service-worker.js").catch(() => {});
}
init().catch((error) => { console.error(error); $("#continueShelf").innerHTML = `<p class="load-error">书架加载失败，请确认本地阅读服务正在运行。</p>`; });
