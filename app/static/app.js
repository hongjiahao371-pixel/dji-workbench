/* DJI 素材工作台 前端（原生 JS，无依赖） */
"use strict";

const $ = (s, el) => (el || document).querySelector(s);
const $$ = (s, el) => Array.from((el || document).querySelectorAll(s));

function fmtSize(n) {
  if (!n && n !== 0) return "";
  const u = ["B", "KB", "MB", "GB", "TB"];
  let i = 0; let v = n;
  while (v >= 1024 && i < u.length - 1) { v /= 1024; i++; }
  return (i === 0 ? v : v.toFixed(1)) + " " + u[i];
}
function fmtDur(s) {
  if (!s) return "";
  s = Math.round(s);
  const m = Math.floor(s / 60);
  return m > 0 ? `${m}:${String(s % 60).padStart(2, "0")}` : `0:${String(s).padStart(2, "0")}`;
}
function fmtTime(ts) {
  if (!ts) return "—";
  const d = new Date(ts * 1000);
  const diff = (Date.now() - d.getTime()) / 1000;
  if (diff < 60) return "刚刚";
  if (diff < 3600) return Math.floor(diff / 60) + " 分钟前";
  if (diff < 86400) return Math.floor(diff / 3600) + " 小时前";
  return d.toLocaleDateString("zh-CN");
}
let toastTimer = null;
function toast(msg) {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.remove("hidden");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.add("hidden"), 2600);
}
async function api(path, opts) {
  const r = await fetch(path, opts ? { headers: { "Content-Type": "application/json" }, ...opts } : undefined);
  let j = null;
  try { j = await r.json(); } catch (e) { /* empty */ }
  if (r.status === 401 && j && j.error === "unauthorized") { showLogin(); throw new Error("请先登录"); }
  if (!r.ok) {
    const msg = (j && j.error) || `请求失败 (${r.status})`;
    throw new Error(msg);
  }
  return j;
}

let loginShown = false;
function showLogin() {
  if (loginShown) return;
  loginShown = true;
  $("#loginMask").classList.remove("hidden");
  setTimeout(() => { const el = $("#loginPwd"); if (el) el.focus(); }, 100);
}
async function tryLogin() {
  const pwd = $("#loginPwd").value;
  if (!pwd) { toast("请输入密码"); return; }
  try {
    const r = await fetch("/api/login", { method: "POST",
      headers: { "Content-Type": "application/json" }, body: JSON.stringify({ password: pwd }) });
    const j = await r.json();
    if (!r.ok) throw new Error(j.error || "登录失败");
    loginShown = false;
    $("#loginMask").classList.add("hidden");
    toast("已登录");
    location.reload();
  } catch (e) { toast(e.message); }
}
document.addEventListener("DOMContentLoaded", () => {
  const go = $("#loginGo"), pwd = $("#loginPwd");
  if (go) go.addEventListener("click", tryLogin);
  if (pwd) pwd.addEventListener("keydown", e => { if (e.key === "Enter") tryLogin(); });
});

/* ================= Tabs ================= */
$$(".tab").forEach(b => b.addEventListener("click", () => {
  $$(".tab").forEach(x => x.classList.toggle("active", x === b));
  $$(".tabpane").forEach(p => p.classList.toggle("active", p.id === "tab-" + b.dataset.tab));
  if (b.dataset.tab === "settings") { loadSettings(); loadStorage(); }
  if (b.dataset.tab === "import") loadHealth();
  if (b.dataset.tab === "albums") renderAlbums();
  if (b.dataset.tab === "lut") loadLutWs();
  if (b.dataset.tab === "edit") { renderConcatPool(); edCheck(); }
}));

/* ================= 素材库 ================= */
const state = {
  page: 1, perPage: 60, total: 0, items: [],
  stats: null, health: null,
  selectMode: false, selected: new Set(),
  view: "grid", tracksJobs: new Set(),
  stacked: false, stacks: [], expanded: new Set(),
  semantic: null,   // {query, items} 语义搜索结果（激活时网格显示它）
};

function filterQuery() {
  const p = new URLSearchParams();
  if ($("#fKind").value) p.set("kind", $("#fKind").value);
  if ($("#fDevice").value) p.set("device", $("#fDevice").value === "__none__" ? "__empty__" : $("#fDevice").value);
  if ($("#fDate").value) p.set("date", $("#fDate").value);
  if ($("#fAlbum").value) p.set("album", $("#fAlbum").value);
  if ($("#fQuality").value && $("#fQuality").value !== "0") p.set("min_quality", $("#fQuality").value);
  if ($("#fRating").value && $("#fRating").value !== "0") p.set("min_rating", $("#fRating").value);
  p.set("rejected", $("#fRejected").value);
  if ($("#fSearch").value.trim()) p.set("q", $("#fSearch").value.trim());
  p.set("sort", $("#fSort").value);
  if ($("#fColor").value) p.set("color", $("#fColor").value);
  if ($("#fDup").checked) p.set("duplicates", "show");
  p.set("page", state.page);
  p.set("per_page", state.perPage);
  return p;
}

async function loadStats() {
  state.stats = await api("/api/stats");
  const devSel = $("#fDevice");
  const cur = devSel.value;
  devSel.innerHTML = '<option value="">全部设备</option>' +
    state.stats.devices.map(d => {
      const val = d.name === "(未分设备)" ? "__none__" : d.name;   // 空设备用哨兵值筛选
      return `<option value="${escAttr(val)}">${escHtml(d.name)} (${d.count})</option>`;
    }).join("");
  devSel.value = cur;
  const dateSel = $("#fDate");
  const curD = dateSel.value;
  dateSel.innerHTML = '<option value="">全部日期</option>' +
    state.stats.dates.map(d => `<option value="${d}">${d}</option>`).join("");
  dateSel.value = curD;
  fillColorSelect((state.stats || {}).color_counts || {});
  refreshAlbumSelects();
}

async function refreshAlbumSelects() {
  try {
    const j = await api("/api/albums");
    const albums = j.albums || [];
    const albumSel = $("#fAlbum");
    const cur = albumSel.value;
    albumSel.innerHTML = '<option value="">全部相册</option>' +
      albums.map(a => `<option value="${a.id}">${escHtml(a.name)} (${a.count})</option>`).join("");
    albumSel.value = cur;
    const bulkSel = $("#bulkAlbum");
    bulkSel.innerHTML = '<option value="">加入相册…</option>' +
      albums.map(a => `<option value="${a.id}">${escHtml(a.name)}</option>`).join("");
    const apSel = $("#apDate");
    if (apSel.options.length === 0 || apSel.dataset.stale === "1") {
      const curD = apSel.value;
      apSel.innerHTML = (state.stats ? state.stats.dates : []).map(d => `<option value="${d}">${d}</option>`).join("");
      apSel.value = curD;
    }
  } catch (e) { /* 静默 */ }
}

const KIND_LABEL = { video: "视频", photo: "照片", pano: "全景", raw: "RAW", sidecar: "字幕", audio: "音频" };

function cardHtml(it) {
  let thumb;
  if (it.thumb_url) {
    thumb = `<img class="thumb" data-src="${it.thumb_url}" alt="">`;
  } else if (it.kind === "video") {
    thumb = `<div class="nothumb">🎬</div>`;
  } else if (it.kind === "sidecar") {
    thumb = `<div class="nothumb">📄</div>`;
  } else if (it.kind === "audio") {
    thumb = `<div class="nothumb">🎵</div>`;
  } else {
    thumb = `<div class="nothumb">🖼️</div>`;
  }
  const sprite = (it.kind === "video" && it.sprite_url)
    ? `<img class="sprite-pre" data-sprite="${it.sprite_url}" alt="">` : "";
  const badges = [];
  badges.push(`<span class="badge kind-${it.kind}">${KIND_LABEL[it.kind] || it.kind}</span>`);
  if (it.duplicate_of) badges.push(`<span class="badge dup">重复</span>`);
  if (it.source === "import") badges.push(`<span class="badge">导入</span>`);
  const stars = it.rating > 0 ? `<span class="stars-chip">${"★".repeat(it.rating)}</span>` : "";
  const rater = it.kind !== "sidecar" ? `<div class="rater">${[1,2,3,4,5].map(n =>
    `<button data-rate="${n}" class="${n <= it.rating ? "on" : ""}" title="${n} 星">★</button>`).join("")}</div>` : "";
  const quality = (it.kind === "photo" && it.quality >= 40)
    ? `<span class="quality-chip" title="清晰度+曝光质量分">Q ${Math.round(it.quality)}</span>` : "";
  const tags = (it.tags && it.tags.length)
    ? `<span class="tags-chip" title="${escAttr(it.tags.join(" · "))}"># ${escHtml(it.tags.slice(0, 3).join(" "))}</span>` : "";
  const pick = state.selectMode ? `<span class="pick" data-pick="${it.id}">${state.selected.has(it.id) ? "✓" : ""}</span>` : "";
  const strip = it.color ? `<span class="color-strip strip-${it.color}"></span>` : "";
  let stackBadge = "";
  if (state.stacked) {
    const st = state.stacks.find(x => x.representative_id === it.id);
    if (st) stackBadge = `<span class="stack-badge" data-stack="${st.ids.join(",")}" title="点击展开/折叠连拍组">◉ ${st.ids.length} 张</span>`;
    else {
      const owner = state.stacks.find(x => x.ids.includes(it.id));
      if (owner && !state.expanded.has(owner.representative_id)) return "";  // 非代表张且组未展开
      if (owner) stackBadge = `<span class="stack-badge" title="连拍组内">◉ 组内</span>`;
    }
  }
  const busy = it.thumb_status === "generating" ? "处理中"
             : it.proxy_status === "generating" ? "转码中" : "";
  if (busy) badges.push(`<span class="badge busy">${busy}</span>`);
  const foot = [`<span class="name">${escHtml(it.rel_path.split("/").pop())}</span>`];
  if (it.thumb_status === "failed") foot.push(`<span title="${escAttr(it.thumb_note)}">缩略图失败</span>`);
  return `<div class="card ${it.rejected ? "rejected" : ""} ${state.selected.has(it.id) ? "selected" : ""}" data-id="${it.id}">
    ${strip}${thumb}${sprite}${pick}${stackBadge}
    <div class="badges">${badges.join("")}</div>
    ${stars}
    ${quality}${tags}
    ${it.duration ? `<span class="badge dur">${fmtDur(it.duration)}</span>` : ""}
    ${rater}
    <div class="status-foot">${foot.join("")}</div>
  </div>`;
}

function renderGrid() {
  const grid = $("#grid");
  const sort = $("#fSort").value;
  let html = "";
  if (sort === "date_desc" || sort === "date_asc") {
    let lastDate = null;
    for (const it of state.items) {
      const d = (it.taken_at || "").slice(0, 10) || "未知日期";
      if (d !== lastDate) {
        if (lastDate !== null) html += "</div>";
        const cnt = state.items.filter(x => (x.taken_at || "").slice(0, 10) === d).length;
        html += `<div class="group-head">${d}<span class="cnt">${cnt} 项</span></div><div class="grid">`;
        lastDate = d;
      }
      html += cardHtml(it);
    }
    if (lastDate !== null) html += "</div>";
    if (lastDate === null) html = "";
  } else {
    html = `<div class="grid">${state.items.map(cardHtml).join("")}</div>`;
  }
  grid.innerHTML = html;
  $("#emptyHint").classList.toggle("hidden", state.items.length > 0);
  $("#resultCount").textContent = `共 ${state.total} 项 · 第 ${state.page} 页`;
  bindCards(grid);
  lazyObserve(grid);
  bindSpriteHover(grid);
  renderPagerSafe();
}

function renderPagerSafe() {
  const pages = Math.max(1, Math.ceil(state.total / state.perPage));
  const el = $("#pager");
  if (pages <= 1) { el.innerHTML = ""; return; }
  let html = "";
  for (let p = 1; p <= pages; p++) {
    if (pages > 9 && p > 2 && p < pages - 1 && Math.abs(p - state.page) > 1) {
      if (!html.endsWith("…</span>")) html += "<span>…</span>";
      continue;
    }
    html += `<button class="btn ${p === state.page ? "btn-primary" : ""}" data-p="${p}">${p}</button>`;
  }
  el.innerHTML = html;
  $$("#pager button").forEach(b => b.addEventListener("click", () => {
    state.page = parseInt(b.dataset.p, 10);
    loadItems();
  }));
}

function renderTimeline() {
  const tl = $("#timeline");
  const sorted = [...state.items].sort((a, b) => (a.taken_at || "").localeCompare(b.taken_at || ""));
  const byDay = {};
  for (const it of sorted) {
    const day = (it.taken_at || "").slice(0, 10) || "未知";
    (byDay[day] = byDay[day] || []).push(it);
  }
  let html = "";
  for (const day of Object.keys(byDay).sort().reverse()) {
    const arr = byDay[day];
    // 天内按 ≤10 分钟间隔聚组
    const groups = [];
    let cur = [arr[0]];
    for (let k = 1; k < arr.length; k++) {
      const prev = cur[cur.length - 1].taken_at || "";
      const now = arr[k].taken_at || "";
      const gap = (Date.parse(now) - Date.parse(prev)) / 60000;
      if (gap <= 10) cur.push(arr[k]);
      else { groups.push(cur); cur = [arr[k]]; }
    }
    groups.push(cur);
    html += `<div class="tl-day"><div class="tl-day-head"><span class="d">${day}</span>
      <span class="c">${arr.length} 项 · ${groups.length} 组</span></div><div class="tl-body">`;
    for (const g of groups.reverse()) {   // 晚的在上
      const t = (g[0].taken_at || "").slice(11, 16) || "--:--";
      const end = (g[g.length - 1].taken_at || "").slice(11, 16);
      const span = t === end ? t : `${t} – ${end}`;
      const thumbs = g.slice(0, 5).map(x => x.thumb_url
        ? `<img src="${x.thumb_url}" data-open="${x.id}" loading="lazy" title="${escAttr(x.rel_path.split("/").pop())}">`
        : `<span class="tl-noimg">${x.kind === "video" ? "🎬" : x.kind === "raw" ? "RAW" : "🖼️"}</span>`).join("");
      const more = g.length > 5 ? `<span class="tl-more">+${g.length - 5}</span>` : "";
      html += `<div class="tl-group" data-day="${day}" data-start="${(g[0].taken_at || "").slice(0, 16)}">
        <div class="tl-time">${span}<span class="cnt">${g.length} 项</span></div>
        <div class="tl-axis"><span class="dot"></span></div>
        <div class="tl-thumbs">${thumbs}${more}
          <button class="btn" data-filter="${day} ${t}" title="筛选该时段">筛</button></div>
      </div>`;
    }
    html += `</div></div>`;
  }
  tl.innerHTML = html || '<div class="empty">当前筛选没有素材</div>';
  $$("#timeline .tl-thumbs img", tl).forEach(im => im.addEventListener("click", () => {
    const id = parseInt(im.dataset.open, 10);
    const idx = state.items.findIndex(x => x.id === id);
    if (idx >= 0) openLightbox(idx);
  }));
  $$("#timeline [data-filter]", tl).forEach(b => b.addEventListener("click", () => {
    const day = b.dataset.filter.split(" ")[0];
    $("#fDate").value = /^\d{4}-\d{2}-\d{2}$/.test(day) ? day : "";
    if (state.view !== "grid") $("#btnViewTimeline").click();
    toast(`已筛选 ${$("#fDate").value || "全部"} 的素材`);
  }));
  $("#emptyHint").classList.toggle("hidden", state.items.length > 0);
  $("#resultCount").textContent = `共 ${state.total} 项 · 第 ${state.page} 页（时间轴视图）`;
  $("#pager").innerHTML = "";
}

async function loadItems() {
  try {
    const q = $("#fSearch").value.trim();
    // 语义模式：搜索词以 ? 开头显式触发
    if (q.startsWith("?") && q.length > 1) {
      return await semanticSearch(q.slice(1));
    }
    const j = await api("/api/items?" + filterQuery().toString());
    state.total = j.total;
    // 自动语义兜底：普通搜索无结果且词长足够
    if (j.total === 0 && q.length >= 2 && !Object.keys(Object.fromEntries(new URLSearchParams(filterQuery().toString()))).some(k => !["q", "page", "per_page", "sort", "rejected", "duplicates"].includes(k))) {
      const st = await api("/api/semantic/status");
      if (st.enabled && st.indexed > 4) {
        toast(`没有文件名匹配，尝试语义搜索「${q}」…`);
        return await semanticSearch(q);
      }
    }
    state.semantic = null;
    state.items = j.items;
    if (state.view === "timeline") renderTimeline();
    else renderGrid();
  } catch (e) { toast(e.message); }
}

async function semanticSearch(query) {
  try {
    const j = await api("/api/search/semantic?q=" + encodeURIComponent(query) + "&limit=24");
    state.semantic = { query, items: j.results };
    state.items = j.results.map(r => ({ ...r, color: r.color || "", score: r.score }));
    state.total = j.results.length;
    $("#fDate").value = ""; $("#fAlbum").value = ""; $("#fKind").value = "";
    if (state.view === "timeline") { state.view = "grid"; $("#btnViewTimeline").click(); }
    renderSemanticGrid();
    const banner = $("#semanticBanner");
    if (j.results.length) {
      banner.classList.remove("hidden");
      $("#semanticText").textContent = `语义搜索「${query}」· 按相似度排序的 ${j.results.length} 个结果`;
    } else {
      banner.classList.add("hidden");
      toast(`语义搜索「${query}」无结果（素材已全部索引）`);
    }
  } catch (e) { toast(e.message); }
}

/* 语义结果专用渲染：按相似度平铺 + 分数徽章 + 日期分组不再打断排序 */
function renderSemanticGrid() {
  const grid = $("#grid");
  grid.innerHTML = `<div class="grid">${state.items.map(it => {
    const it2 = { ...it };
    const html = cardHtml(it2);
    return html;
  }).join("")}</div>`;
  // 注入相似度徽章
  $$("#grid .card").forEach(c => {
    const it = state.items.find(x => x.id === parseInt(c.dataset.id, 10));
    if (it && it.score !== undefined) {
      const b = document.createElement("span");
      b.className = "badge score-badge";
      b.textContent = (it.score * 100).toFixed(0) + "%";
      c.querySelector(".badges").prepend(b);
    }
  });
  bindCards(grid);
  lazyObserve(grid);
  $("#emptyHint").classList.toggle("hidden", state.items.length > 0);
  $("#pager").innerHTML = "";
}

function loadThumb(img) {
  if (!img.dataset.src) return;
  const src = img.dataset.src;
  delete img.dataset.src;
  const done = () => img.classList.add("loaded");
  img.addEventListener("load", done, { once: true });
  img.addEventListener("error", () => { img.style.display = "none"; }, { once: true });
  img.src = src;
  if (img.complete && img.naturalWidth > 0) done();  // 本地小图可能在事件挂上前就已解码完成
  if (typeof lazyIO !== "undefined") lazyIO.unobserve(img);
}

const lazyIO = new IntersectionObserver(entries => {
  for (const en of entries) {
    if (en.isIntersecting) loadThumb(en.target);
  }
}, { rootMargin: "300px" });

function loadVisibleThumbs() {
  const vh = window.innerHeight || 900;
  $$("img.thumb[data-src]").forEach(img => {
    const r = img.getBoundingClientRect();
    if (r.top < vh + 300 && r.bottom > -300) loadThumb(img);
  });
}
let scrollTimer = null;
window.addEventListener("scroll", () => {
  clearTimeout(scrollTimer);
  scrollTimer = setTimeout(loadVisibleThumbs, 120);
}, { passive: true });
window.addEventListener("resize", loadVisibleThumbs);

function lazyObserve(root) {
  $$("img.thumb[data-src]", root).forEach(i => lazyIO.observe(i));
  loadVisibleThumbs();  // 几何兜底：个别嵌入环境 IntersectionObserver 不回调
}

function bindCards(root) {
  $$(".card", root).forEach(card => {
    card.addEventListener("click", ev => {
      const stackBadge = ev.target.closest("[data-stack]");
      if (stackBadge) {
        ev.stopPropagation();
        const ids = stackBadge.dataset.stack.split(",").map(Number);
        const st = state.stacks.find(x => x.ids.some(id => ids.includes(id)));
        const rep = st ? st.representative_id : ids[0];   // 必须用 representative_id，否则展开 key 打不中
        if (state.expanded.has(rep)) state.expanded.delete(rep);
        else state.expanded.add(rep);
        renderGrid();
        return;
      }
      const rateBtn = ev.target.closest("[data-rate]");
      const pick = ev.target.closest("[data-pick]");
      const id = parseInt(card.dataset.id, 10);
      if (rateBtn) {
        ev.stopPropagation();
        setRate(id, parseInt(rateBtn.dataset.rate, 10));
        return;
      }
      if (pick) {
        ev.stopPropagation();
        toggleSelect(id);
        return;
      }
      if (state.selectMode) {
        toggleSelect(id);
        return;
      }
      const idx = state.items.findIndex(x => x.id === id);
      openLightbox(idx);
    });
  });
}

function toggleSelect(id) {
  if (state.selected.has(id)) state.selected.delete(id);
  else state.selected.add(id);
  updateSelectUi();
}

function updateSelectUi() {
  $("#bulkN").textContent = state.selected.size;
  const bar = $("#bulkbar");
  bar.classList.toggle("hidden", !state.selectMode);
  $("#btnSelect").classList.toggle("btn-primary", state.selectMode);
  $$(".card").forEach(c => {
    const id = parseInt(c.dataset.id, 10);
    c.classList.toggle("selected", state.selected.has(id));
    const pick = c.querySelector(".pick");
    if (pick) pick.textContent = state.selected.has(id) ? "✓" : "";
  });
}

function bindSpriteHover(root) {
  $$(".sprite-pre[data-sprite]", root).forEach(img => {
    if (img.dataset.bound) return;
    img.dataset.bound = "1";
    const card = img.closest(".card");
    const setFrame = frac => {
      const k = Math.max(0, Math.min(11, Math.round(frac * 11)));
      img.style.backgroundImage = `url(${img.dataset.sprite})`;
      img.style.backgroundSize = "1200% 100%";
      img.style.backgroundPosition = `${(k / 11) * 100}% 50%`;
    };
    card.addEventListener("mousemove", e => {
      const r = card.getBoundingClientRect();
      setFrame(Math.max(0, Math.min(1, (e.clientX - r.left) / r.width)));
    });
    card.addEventListener("mouseenter", () => setFrame(0.5));
  });
}

async function setRate(id, rating, color) {
  try {
    const body = {};
    if (rating !== undefined) body.rating = rating;
    if (color !== undefined) body.color = color;
    await api(`/api/items/${id}/rate`, { method: "POST", body: JSON.stringify(body) });
    const it = state.items.find(x => x.id === id);
    if (it) {
      if (rating !== undefined) it.rating = rating;
      if (color !== undefined) it.color = color;
    }
    renderGrid();
  } catch (e) { toast(e.message); }
}
async function setRejected(id, rejected) {
  try {
    await api(`/api/items/${id}/rate`, { method: "POST", body: JSON.stringify({ rejected }) });
    const it = state.items.find(x => x.id === id);
    if (it) it.rejected = rejected ? 1 : 0;
    renderGrid();
  } catch (e) { toast(e.message); }
}

/* ================= 灯箱 ================= */
const lb = { open: false, idx: -1, pano: null };
function escHtml(s) { return String(s == null ? "" : s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])); }
function escAttr(s) { return escHtml(s).replace(/`/g, "&#96;"); }

function openLightbox(idx) {
  if (idx < 0 || idx >= state.items.length) return;
  lb.open = true; lb.idx = idx; lb.single = null;
  $("#lightbox").classList.remove("hidden");
  renderLightbox();
}
async function openSingleItem(id) {
  try {
    const it = await api("/api/items/" + id);
    lb.open = true; lb.idx = -1; lb.single = it;
    $("#lightbox").classList.remove("hidden");
    renderLightbox();
  } catch (e) { toast(e.message); }
}
function closeLightbox() {
  lb.open = false;
  lb.single = null;
  $("#lightbox").classList.add("hidden");
  destroyPano();
  $("#lbStage").innerHTML = "";
}
function navLightbox(delta) { openLightbox(lb.idx + delta); }

function renderLightbox() {
  const it = lb.single || state.items[lb.idx];
  if (!it) return;
  $("#lbPrev").disabled = !!lb.single || lb.idx <= 0;
  $("#lbNext").disabled = !!lb.single || lb.idx >= state.items.length - 1;
  $("#lbTitle").textContent = it.rel_path;
  $("#lbDownload").href = `/api/items/${it.id}/file`;
  $("#lbReject").classList.toggle("on", !!it.rejected);
  $("#lbReject").textContent = it.rejected ? "已淘汰（点此恢复）" : "淘汰";
  const COLORS = [["red", "红"], ["yellow", "黄"], ["green", "绿"], ["blue", "蓝"]];
  $("#lbColors").innerHTML = COLORS.map(([c, cn]) =>
    `<button class="c-${c} ${it.color === c ? "on" : ""}" title="${cn}标"></button>`).join("")
    + `<button class="c-clear" title="清除色标">✕</button>`;
  $$("#lbColors button").forEach(b => b.addEventListener("click", () => {
    const cls = b.className.replace(" on", "").replace("c-", "").trim();
    const val = ["red", "yellow", "green", "blue"].includes(cls) ? cls : "";
    setRate(it.id, it.rating || 0, val).then(() => { renderLightbox(); loadStats(); });
  }));
  $("#lbStars").innerHTML = [1, 2, 3, 4, 5].map(n =>
    `<button data-n="${n}" class="${n <= it.rating ? "on" : ""}">${n <= it.rating ? "★" : "☆"}</button>`).join("");
  $$("#lbStars button").forEach(b => b.addEventListener("click", () => {
    if (lb.single) it.rating = parseInt(b.dataset.n, 10);   // 单条模式本地同步
    setRate(it.id, parseInt(b.dataset.n, 10) === it.rating ? 0 : parseInt(b.dataset.n, 10))
      .then(renderLightbox);
  }));

  const meta = [];
  const add = (k, v, wide) => meta.push(`<div class="k">${k}</div><div class="${wide ? "wide" : ""}">${v}</div>`);
  add("设备", escHtml(it.device || "—"));
  add("类型", KIND_LABEL[it.kind] || it.kind);
  add("日期", escHtml(it.taken_at || "—"));
  if (it.width) add("分辨率", `${it.width}×${it.height}`);
  if (it.duration) add("时长", fmtDur(it.duration));
  if (it.codec) add("编码", escHtml(it.codec.toUpperCase()));
  add("体积", fmtSize(it.size));
  if (it.kind === "photo" && it.quality > 0) add("质量分", Math.round(it.quality) + " / 100");
  if (it.tags && it.tags.length) add("标签", it.tags.map(t => "#" + escHtml(t)).join(" "), true);
  if (it.caption && !["__failed__", "__pending__"].includes(it.caption)) add("AI 描述", escHtml(it.caption), true);
  const ex = it.exif || {};
  if (ex.f_number) {
    const shutter = ex.exposure
      ? (ex.exposure >= 1 ? ex.exposure.toFixed(1) + "s"
                          : "1/" + Math.round(1 / ex.exposure) + "s") : "—";
    add("参数", `ƒ/${ex.f_number} · ${shutter} · ISO ${ex.iso || "—"} · ${ex.focal ? Math.round(ex.focal) + "mm" : "—"}`);
  }
  if (ex.fps) {
    add("帧率", ex.fps + " fps" + (ex.profile ? " · " + escHtml(ex.profile) : ""));
    if (ex.bitrate) add("码率", fmtSize(ex.bitrate / 8) + "/s");
  }
  if (it.sha256) add("SHA-256", escHtml(it.sha256.slice(0, 16)) + "…", true);
  if (it.duplicate_of) add("重复", `与 #${it.duplicate_of} 内容相同`, true);
  if (it.gps_lat != null) add("GPS", `${it.gps_lat.toFixed(5)}, ${it.gps_lon.toFixed(5)}`);
  if (it.source === "import") add("来源", "手动导入");
  const notes = [];
  if (it.thumb_status === "generating") notes.push("缩略图生成中…");
  if (it.proxy_status === "generating") notes.push("代理转码中，完成后自动可用…");
  if (it.kind === "video" && it.codec && /hevc|h265/i.test(it.codec) && !it.proxy_url) {
    notes.push("原片是 H.265/10-bit，浏览器大概率无法直接播放；等 H.264 代理生成完成后再看。");
  }
  if (it.proxy_status === "failed") notes.push("代理转码失败：" + escHtml(it.proxy_note));
  if (notes.length) meta.push(`<div class="k wide lb-note">${notes.join("<br>")}</div>`);
  $("#lbMeta").innerHTML = meta.join("");

  const stage = $("#lbStage");
  destroyPano();
  stage.innerHTML = "";
  const isVideo = it.kind === "video";
  const isPano = !!it.pano && !isVideo;
  if (isPano) {
    const loading = document.createElement("div");
    loading.textContent = "全景加载中…";
    loading.className = "muted";
    stage.appendChild(loading);
    const img = new Image();
    img.onload = () => {
      if (!lb.open) return;
      stage.innerHTML = "";
      mountPano(stage, `/api/items/${it.id}/file`);
    };
    img.onerror = () => { loading.textContent = "全景图加载失败"; };
    img.src = `/api/items/${it.id}/file`;
  } else if (isVideo) {
    const src = it.proxy_url || `/api/items/${it.id}/file`;
    stage.innerHTML = `<video controls preload="metadata" src="${src}"></video>`;
  } else {
    stage.innerHTML = `<img src="/api/items/${it.id}/file" alt="">`;
  }
}
$("#lbClose").addEventListener("click", closeLightbox);
$("#lbPrev").addEventListener("click", () => navLightbox(-1));
$("#lbNext").addEventListener("click", () => navLightbox(1));
$("#lbSimilar").addEventListener("click", () => {
  const it = lb.single || state.items[lb.idx];
  if (!it) return;
  openSimilar(it);
});
async function openSimilar(it) {
  try {
    const j = await api(`/api/items/${it.id}/similar?threshold=10`);
    $("#simTitle").textContent = `与「${it.rel_path.split("/").pop()}」相似的素材`;
    $("#simNote").textContent = j.items.length
      ? `感知哈希距离 ≤ ${j.threshold}（64bit，越小越像）。点击任意一张放大查看。`
      : (j.note || "没有找到足够相似的素材（阈值 10/64bit）。连拍或同场景图通常距离 <10。");
    const grid = $("#simGrid");
    grid.innerHTML = j.items.map(x => `
      <div class="sim-card" data-id="${x.id}">
        ${x.thumb_url ? `<img src="${x.thumb_url}" loading="lazy">` : `<div class="nothumb">🖼️</div>`}
        <span class="dist">距离 ${x.distance}</span>
        <span class="nm">${escHtml(x.rel_path.split("/").pop())}${x.rejected ? " · 已淘汰" : ""}</span>
      </div>`).join("");
    $$("#simGrid .sim-card").forEach(c => c.addEventListener("click", () => {
      const otherId = parseInt(c.dataset.id, 10);
      const meta = j.items.find(x => x.id === otherId);
      window.__cmpDist = meta ? meta.distance : null;
      $("#simModal").classList.add("hidden");
      openCompare(it.id, otherId);
    }));
    $("#simModal").classList.remove("hidden");
  } catch (e) { toast(e.message); }
}
$("#simClose").addEventListener("click", () => $("#simModal").classList.add("hidden"));
$("#simModal").addEventListener("click", e => {
  if (e.target === $("#simModal")) $("#simModal").classList.add("hidden");
});

$("#lbReject").addEventListener("click", () => {
  const it = state.items[lb.idx];
  if (it) setRejected(it.id, !it.rejected).then(renderLightbox);
});
document.addEventListener("keydown", e => {
  if (!lb.open) return;
  if (!$("#simModal").classList.contains("hidden") || !$("#cmpModal").classList.contains("hidden")) return;
  const it = lb.single || state.items[lb.idx];
  if (!it) return;
  if (e.key === "Escape") closeLightbox();
  else if (e.key === "ArrowLeft") navLightbox(-1);
  else if (e.key === "ArrowRight") navLightbox(1);
  else if (e.key >= "1" && e.key <= "5") {
    setRate(it.id, parseInt(e.key, 10)).then(renderLightbox);
  } else if (e.key === "0") {
    setRate(it.id, 0).then(renderLightbox);
  } else if (e.key === "x" || e.key === "X") {
    setRejected(it.id, !it.rejected).then(renderLightbox);
  } else if (["6", "7", "8", "9"].includes(e.key)) {
    const c = { 6: "red", 7: "yellow", 8: "green", 9: "blue" }[e.key];
    setRate(it.id, it.rating || 0, it.color === c ? "" : c).then(() => { renderLightbox(); loadStats(); });
  }
});

/* ---- WebGL 全景（等距柱状球面查看） ---- */
function destroyPano() {
  if (lb.pano) { lb.pano.destroy(); lb.pano = null; }
}
function mountPano(container, url) {
  const canvas = document.createElement("canvas");
  container.appendChild(canvas);
  const gl = canvas.getContext("webgl", { antialias: false });
  if (!gl) {
    const img = document.createElement("img");
    img.src = url;
    container.innerHTML = "";
    container.appendChild(img);
    return;
  }
  const VS = "attribute vec2 p;void main(){gl_Position=vec4(p,0.,1.);}";
  const FS = `precision mediump float;
uniform vec2 uRes;uniform float uYaw;uniform float uPitch;uniform float uFov;uniform sampler2D uTex;
void main(){
  vec2 uv=(gl_FragCoord.xy-.5*uRes)/uRes.y;
  float f=1.0/tan(radians(uFov)*.5);
  vec3 dir=normalize(vec3(uv.x,uv.y,-f));
  float cp=cos(uPitch),sp=sin(uPitch);
  dir.yz=mat2(cp,sp,-sp,cp)*dir.yz;
  float cy=cos(uYaw),sy=sin(uYaw);
  dir.xz=mat2(cy,sy,-sy,cy)*dir.xz;
  float u=.5+atan(dir.x,-dir.z)/6.28318530718;
  float v=.5-asin(clamp(dir.y,-1.,1.))/3.14159265359;
  gl_FragColor=texture2D(uTex,vec2(u,v));
}`;
  function sh(type, src) {
    const s = gl.createShader(type);
    gl.shaderSource(s, src); gl.compileShader(s);
    return s;
  }
  const prog = gl.createProgram();
  gl.attachShader(prog, sh(gl.VERTEX_SHADER, VS));
  gl.attachShader(prog, sh(gl.FRAGMENT_SHADER, FS));
  gl.linkProgram(prog);
  gl.useProgram(prog);
  const buf = gl.createBuffer();
  gl.bindBuffer(gl.ARRAY_BUFFER, buf);
  gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 1, -1, -1, 1, 1, 1]), gl.STATIC_DRAW);
  const loc = gl.getAttribLocation(prog, "p");
  gl.enableVertexAttribArray(loc);
  gl.vertexAttribPointer(loc, 2, gl.FLOAT, false, 0, 0);
  const uRes = gl.getUniformLocation(prog, "uRes");
  const uYaw = gl.getUniformLocation(prog, "uYaw");
  const uPitch = gl.getUniformLocation(prog, "uPitch");
  const uFov = gl.getUniformLocation(prog, "uFov");
  const tex = gl.createTexture();
  const img = new Image();
  let ready = false;
  img.onload = () => {
    gl.bindTexture(gl.TEXTURE_2D, tex);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, img);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
    ready = true;
    draw();
  };
  img.src = url;
  let yaw = 0, pitch = 0, fov = 75;
  let dragging = false, lx = 0, ly = 0;
  canvas.addEventListener("mousedown", e => { dragging = true; lx = e.clientX; ly = e.clientY; });
  window.addEventListener("mousemove", onMove);
  window.addEventListener("mouseup", () => { dragging = false; });
  canvas.addEventListener("touchstart", e => {
    if (e.touches.length) { dragging = true; lx = e.touches[0].clientX; ly = e.touches[0].clientY; }
  }, { passive: true });
  canvas.addEventListener("touchmove", e => {
    if (!dragging || !e.touches.length) return;
    step(e.touches[0].clientX, e.touches[0].clientY);
  }, { passive: true });
  canvas.addEventListener("touchend", () => { dragging = false; }, { passive: true });
  canvas.addEventListener("wheel", e => {
    e.preventDefault();
    fov = Math.min(110, Math.max(35, fov + e.deltaY * 0.05));
    draw();
  }, { passive: false });
  function step(x, y) {
    yaw -= (x - lx) * 0.0032 * (fov / 75);
    pitch += (y - ly) * 0.0032 * (fov / 75);
    pitch = Math.max(-1.35, Math.min(1.35, pitch));
    lx = x; ly = y;
    draw();
  }
  function onMove(e) { if (dragging) step(e.clientX, e.clientY); }
  function resize() {
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const w = canvas.clientWidth * dpr, h = canvas.clientHeight * dpr;
    if (w && h && (canvas.width !== w || canvas.height !== h)) {
      canvas.width = w; canvas.height = h;
      gl.viewport(0, 0, w, h);
      draw();
    }
  }
  function draw() {
    if (!ready) return;
    gl.uniform2f(uRes, canvas.width, canvas.height);
    gl.uniform1f(uYaw, yaw);
    gl.uniform1f(uPitch, pitch);
    gl.uniform1f(uFov, fov);
    gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
  }
  let alive = true;
  function loop() { if (!alive) return; resize(); requestAnimationFrame(loop); }
  requestAnimationFrame(loop);
  lb.pano = { destroy() { alive = false; window.removeEventListener("mousemove", onMove); } };
}

/* ================= 导入 ================= */
$("#btnBrowse").addEventListener("click", () => browseTo($("#importPath").value || "/"));
function browseTo(path) {
  api("/api/browse?path=" + encodeURIComponent(path)).then(j => {
    const box = $("#browseBox");
    box.classList.remove("hidden");
    box.innerHTML = `<div class="crumb"><span>${escHtml(j.path)}</span>
      <button id="bkUp">↑ 上一级</button></div>
      <ul>${j.dirs.map(d => `<li data-p="${escAttr(d.path)}"><span>${escHtml(d.name)}</span><span class="use">进入 ›</span></li>`).join("") || '<li class="muted">没有子目录</li>'}</ul>`;
    $("#bkUp").addEventListener("click", () => browseTo(j.parent));
    $$("li[data-p]", box).forEach(li => li.addEventListener("click", () => browseTo(li.dataset.p)));
  }).catch(e => toast(e.message));
}

$("#btnImportScan").addEventListener("click", async () => {
  const p = $("#importPath").value.trim();
  if (!p) { toast("请填写要扫描的目录"); return; }
  const btn = $("#btnImportScan");
  btn.disabled = true;
  try {
    const j = await api("/api/import/scan", { method: "POST", body: JSON.stringify({ path: p }) });
    const box = $("#importResult");
    box.classList.remove("hidden");
    if (!j.candidates.length) {
      box.innerHTML = `<div class="muted">没有新文件（已在库中 ${j.already_known} 个）</div>`;
      return;
    }
    const totalSize = j.candidates.reduce((a, c) => a + c.size, 0);
    box.innerHTML = `<div class="head">
        <span>发现 ${j.candidates.length} 个新文件（${fmtSize(totalSize)}），已在库中 ${j.already_known} 个</span>
        <button class="btn btn-primary" id="btnCommit">全部导入</button></div>
      <table><thead><tr><th>类型</th><th>文件</th><th>体积</th></tr></thead><tbody>
      ${j.candidates.slice(0, 300).map(c => `<tr><td>${KIND_LABEL[c.kind] || c.kind}</td>
        <td class="path" title="${escAttr(c.path)}">${escHtml(c.path)}</td><td>${fmtSize(c.size)}</td></tr>`).join("")}
      </tbody></table>
      ${j.candidates.length > 300 ? `<div class="muted">仅显示前 300 个…</div>` : ""}`;
    $("#btnCommit").addEventListener("click", async () => {
      try {
        const r = await api("/api/import/commit", {
          method: "POST", body: JSON.stringify({ paths: j.candidates.map(c => c.path) }),
        });
        toast(`已导入 ${r.added} 个，后台生成缩略图/哈希中`);
        loadStats();
        loadItems();
      } catch (e) { toast(e.message); }
    });
  } catch (e) {
    toast(e.message);
  } finally {
    btn.disabled = false;
  }
});

/* ================= 设置 ================= */
async function loadSettings() {
  try {
    const [cfg, health] = await Promise.all([api("/api/config"), api("/api/health")]);
    $("#cfgRoots").value = (cfg.watch_roots || []).join("\n");
    $("#cfgPoll").value = cfg.poll_interval;
    $("#cfgStableAge").value = cfg.stable_age;
    $("#cfgStableScans").value = cfg.stable_scans;
    $("#cfgProxyOn").checked = !!cfg.proxy_enabled;
    $("#cfgProxyH").value = String(cfg.proxy_height || 1080);
    $("#cfgCrf").value = cfg.proxy_crf;
    $("#cfgLut").value = cfg.lut_path || "";
    $("#cfgBackup").value = cfg.backup_dir || "";
    $("#cfgAiUrl").value = cfg.ai_base_url || "";
    $("#cfgAiModel").value = cfg.ai_model || "";
    $("#cfgAiKey").value = cfg.ai_api_key || "";
    $("#cfgAiKey").placeholder = cfg.ai_api_key ? "（已配置，留空则清除）" : "sk-…";
    $("#cfgFont").value = cfg.export_font_path || "";
    $("#cfgAccessPwd").value = "";
    $("#cfgAccessPwd").placeholder = cfg.access_password ? "（已设置，输入新值可修改，留空保持不变）" : "（未设置）";
    $("#cfgConcatUrl").value = cfg.concat_url || "";
    $("#envInfo").innerHTML = `运行环境：ffmpeg ${health.ffmpeg ? "✅" : "❌（视频缩略图/代理不可用）"} ·
      ffprobe ${health.ffprobe ? "✅" : "❌"} · 已入库 ${health.items} 项 · 版本 v${escHtml(health.version)}`;
  } catch (e) { toast(e.message); }
}
$("#btnSaveCfg").addEventListener("click", async () => {
  const body = {
    watch_roots: $("#cfgRoots").value.split("\n").map(s => s.trim()).filter(Boolean),
    poll_interval: parseInt($("#cfgPoll").value, 10),
    stable_age: parseInt($("#cfgStableAge").value, 10),
    stable_scans: parseInt($("#cfgStableScans").value, 10),
    proxy_enabled: $("#cfgProxyOn").checked,
    proxy_height: parseInt($("#cfgProxyH").value, 10),
    proxy_crf: parseInt($("#cfgCrf").value, 10),
    lut_path: $("#cfgLut").value.trim(),
    backup_dir: $("#cfgBackup").value.trim(),
    ai_base_url: $("#cfgAiUrl").value.trim(),
    ai_model: $("#cfgAiModel").value.trim(),
    ai_api_key: $("#cfgAiKey").value.trim(),
    export_font_path: $("#cfgFont").value.trim(),
    access_password: $("#cfgAccessPwd").value.trim(),
    concat_url: $("#cfgConcatUrl").value.trim(),
  };
  try {
    await api("/api/config", { method: "POST", body: JSON.stringify(body) });
    toast("已保存，开始按新配置扫描");
  } catch (e) { toast(e.message); }
});

/* ================= 顶栏状态 ================= */
async function loadHealth() {
  try {
    const h = await api("/api/health");
    if (state.health && typeof h.items === "number" && h.items > state.health.items) {
      const diff = h.items - state.health.items;
      toast(`📥 新入库 ${diff} 项素材`);
      if (!document.hidden) { loadStats(); loadItems(); }
    }
    state.health = h;
    updateTopmeta();
  } catch (e) { /* 静默 */ }
}
function updateTopmeta() {
  const h = state.health;
  if (!h) return;
  const q = h.queues || {};
  const queueN = (q.hash || 0) + (q.thumb || 0) + (q.proxy || 0) + (q.sprite || 0);
  const jobN = state.tracksJobs.size;
  $("#topMeta").innerHTML =
    `<span>已入库 <b>${h.items}</b></span>` +
    `<span>队列 <b>${queueN}</b></span>` +
    `<span>上次扫描 <b>${fmtTime(h.last_scan)}</b></span>` +
    `<button class="btn" data-jobs="1" title="后台任务">任务 ${jobN ? "(" + jobN + ")" : ""}</button>`;
}

$("#btnRescan").addEventListener("click", async () => {
  $("#btnRescan").disabled = true;
  try {
    const r = await api("/api/scan", { method: "POST", body: JSON.stringify({}) });
    toast(`扫描完成：新增 ${r.new}，判稳定 ${r.stable}`);
    await Promise.all([loadStats(), loadItems()]);
  } catch (e) { toast(e.message); } finally {
    $("#btnRescan").disabled = false;
  }
});

/* ================= 启动 ================= */
["fKind", "fDevice", "fDate", "fAlbum", "fQuality", "fRating", "fColor", "fRejected", "fSort", "fDup"].forEach(id =>
  $("#" + id).addEventListener("change", () => { state.page = 1; loadItems(); }));
$("#fStack").addEventListener("change", async ev => {
  state.stacked = ev.target.checked;
  if (state.stacked) {
    try {
      const date = $("#fDate").value;
      const j = await api("/api/stacks?gap=2" + (date ? `&date=${date}` : ""));
      state.stacks = j.stacks;
    } catch (e) { toast(e.message); state.stacked = false; }
  } else state.stacks = [];
  state.expanded.clear();
  renderGrid();
});

$("#btnViewTimeline").addEventListener("click", () => {
  state.view = state.view === "grid" ? "timeline" : "grid";
  $("#btnViewTimeline").textContent = state.view === "grid" ? "📅 时间轴" : "🔲 网格";
  $("#btnViewTimeline").classList.toggle("btn-primary", state.view === "timeline");
  $("#grid").classList.toggle("hidden", state.view !== "grid");
  $("#timeline").classList.toggle("hidden", state.view !== "timeline");
  loadItems();
});
let searchTimer = null;
$("#fSearch").addEventListener("input", () => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => {
    state.page = 1;
    if (!$("#fSearch").value.trim()) { $("#semanticBanner").classList.add("hidden"); state.semantic = null; }
    loadItems();
  }, 350);
});
setInterval(() => { if (!document.hidden) loadHealth(); }, 5000);
loadHealth();
loadStats().then(loadItems).catch(() => loadItems());

/* ================= 选择模式 / 批量操作 ================= */
$("#btnSelect").addEventListener("click", () => {
  state.selectMode = !state.selectMode;
  if (!state.selectMode) state.selected.clear();
  renderGrid();
  updateSelectUi();
});
$("#bulkDone").addEventListener("click", () => {
  state.selectMode = false;
  state.selected.clear();
  renderGrid();
  updateSelectUi();
});
$("#bulkAll").addEventListener("click", () => {
  state.items.forEach(i => state.selected.add(i.id));
  updateSelectUi();
});
$("#bulkClear").addEventListener("click", () => { state.selected.clear(); updateSelectUi(); });

$("#bulkStars").innerHTML = "打星：" + [1,2,3,4,5].map(n =>
  `<button data-n="${n}" title="${n} 星">★</button>`).join("");
$$("#bulkStars button").forEach(b => b.addEventListener("click", async () => {
  if (!state.selected.size) { toast("先选择素材"); return; }
  await api("/api/items/batch", { method: "POST",
    body: JSON.stringify({ ids: [...state.selected], rating: parseInt(b.dataset.n, 10) }) });
  toast(`已给 ${state.selected.size} 项打 ${b.dataset.n} 星`);
  loadItems();
}));

async function bulkApply(body, msg) {
  if (!state.selected.size) { toast("先选择素材"); return; }
  try {
    const r = await api("/api/items/batch", { method: "POST",
      body: JSON.stringify({ ids: [...state.selected], ...body }) });
    toast(`${msg}（${r.updated} 项）`);
    await loadStats();
    await loadItems();
  } catch (e) { toast(e.message); }
}
$("#bulkReject").addEventListener("click", () => bulkApply({ rejected: true }, "已淘汰"));
$("#bulkRestore").addEventListener("click", () => bulkApply({ rejected: false }, "已恢复"));
$("#bulkAlbum").addEventListener("change", async e => {
  const id = e.target.value;
  if (!id) return;
  await bulkApply({ album_add: parseInt(id, 10) }, "已加入相册");
  e.target.value = "";
  renderAlbums();
});

/* ================= 相册页 ================= */
async function renderAlbums() {
  try {
    const j = await api("/api/albums");
    const box = $("#albumList");
    if (!j.albums.length) { box.innerHTML = '<div class="muted">还没有相册。选择模式批量加入，或在这里先建一个。</div>'; return; }
    box.innerHTML = j.albums.map(a => `
      <div class="album-row" data-id="${a.id}">
        <span class="name">📁 ${escHtml(a.name)}</span>
        <span class="cnt">${a.count} 项 · 创建于 ${new Date(a.created_at * 1000).toLocaleDateString("zh-CN")}</span>
        <span class="ops">
          <button class="btn" data-open="${a.id}">在素材库查看</button>
          <button class="btn" data-del="${a.id}">删除</button>
        </span>
      </div>`).join("");
    $$("#albumList [data-open]").forEach(b => b.addEventListener("click", () => {
      $("#fAlbum").value = b.dataset.open;
      state.page = 1;
      document.querySelector('.tab[data-tab="library"]').click();
      loadItems();
    }));
    $$("#albumList [data-del]").forEach(b => b.addEventListener("click", async () => {
      if (!confirm("删除该相册？（只删引用，不动素材文件）")) return;
      await api("/api/albums/" + b.dataset.del, { method: "DELETE" });
      renderAlbums();
      refreshAlbumSelects();
    }));
  } catch (e) { toast(e.message); }
}
$("#btnAlbumCreate").addEventListener("click", async () => {
  const name = $("#albumName").value.trim();
  if (!name) { toast("请填相册名称"); return; }
  try {
    await api("/api/albums", { method: "POST", body: JSON.stringify({ name }) });
    $("#albumName").value = "";
    toast("相册已创建");
    renderAlbums();
    refreshAlbumSelects();
  } catch (e) { toast(e.message); }
});

/* ================= 自动选片 ================= */
$("#btnAutopick").addEventListener("click", () => {
  $("#apModal").classList.remove("hidden");
  refreshAlbumSelects();
});
$("#apClose").addEventListener("click", () => $("#apModal").classList.add("hidden"));
$("#apLoad").addEventListener("click", async () => {
  const d = $("#apDate").value;
  if (!d) { toast("先选日期"); return; }
  try {
    const j = await api("/api/autopick?date=" + d);
    const box = $("#apGroups");
    if (!j.groups.length) { box.innerHTML = '<div class="muted">该日期没有检测到连拍分组（需要同目录 2 秒内的多张照片）。</div>'; return; }
    box.innerHTML = j.groups.map((g, gi) => `
      <div class="ap-group" data-g="${gi}">
        ${g.ids.map(id => `<img data-id="${id}" class="${id === g.suggest_id ? "suggest" : ""}"
          src="/api/items/${id}/thumb" title="#${id}">`).join("")}
        <span class="ap-meta">建议保留 #${g.suggest_id}（质量 ${g.quality}）· 共 ${g.ids.length} 张</span>
      </div>`).join("") +
      `<div class="actions"><button class="btn btn-primary" id="apApply">应用建议（建议项★ + 其余淘汰）</button></div>`;
    $$("#apGroups img").forEach(im => im.addEventListener("click", () => {
      im.classList.toggle("suggest");
    }));
    $("#apApply").addEventListener("click", async () => {
      const groups = j.groups;
      const starIds = [], rejectIds = [];
      $$("#apGroups .ap-group").forEach((el, gi) => {
        const g = groups[gi];
        const suggests = new Set(Array.from(el.querySelectorAll("img.suggest")).map(i => parseInt(i.dataset.id, 10)));
        g.ids.forEach(id => (suggests.has(id) ? starIds : rejectIds).push(id));
      });
      if (starIds.length) await api("/api/items/batch", { method: "POST", body: JSON.stringify({ ids: starIds, rating: 3 }) });
      if (rejectIds.length) await api("/api/items/batch", { method: "POST", body: JSON.stringify({ ids: rejectIds, rejected: true }) });
      toast(`已应用：${starIds.length} 项打星，${rejectIds.length} 项淘汰`);
      $("#apModal").classList.add("hidden");
      loadStats(); loadItems();
    });
  } catch (e) { toast(e.message); }
});

/* ================= 导出 / 幻灯片 ================= */
function selectedPhotoIds() {
  return state.items.filter(i => state.selected.has(i.id) &&
    ["photo", "pano", "raw"].includes(i.kind)).map(i => i.id);
}
$("#bulkExport").addEventListener("click", () => {
  const ids = state.items.filter(i => state.selected.has(i.id) &&
    ["photo", "pano", "raw", "video"].includes(i.kind)).map(i => i.id);
  if (!ids.length) { toast("选择模式里先选中素材（支持照片/全景/RAW，视频可勾选一并导出）"); return; }
  refreshLutSelect();
  $("#exModal").classList.remove("hidden");
  $("#exGo").onclick = () => submitJob("export", ids, {
    opts: {
      brightness: +$("#exBright").value || 0, contrast: +$("#exContrast").value || 0,
      saturation: +$("#exSat").value || 0, temp: +$("#exTemp").value || 0,
      vignette: +$("#exVig").value || 0, resize_w: +$("#exResize").value || 0,
      format: $("#exFormat").value, watermark_text: $("#exWm").value.trim(),
      watermark_pos: $("#exWmPos").value,
      include_video: $("#exVideo").checked, video_crf: parseInt($("#exVcrf").value, 10) || 22,
      lut: $("#exLut").value,
    },
  });
});
$("#exClose").addEventListener("click", () => $("#exModal").classList.add("hidden"));

$("#bulkSlide").addEventListener("click", () => {
  const ids = selectedPhotoIds();
  if (!ids.length) { toast("选择模式里先选中照片"); return; }
  $("#slModal").classList.remove("hidden");
  $("#slGo").onclick = () => submitJob("slideshow", ids, {
    duration_per: +$("#slDur").value || 3, music_path: $("#slMusic").value.trim(),
  });
});
$("#slClose").addEventListener("click", () => $("#slModal").classList.add("hidden"));

$("#bulkAi").addEventListener("click", async () => {
  const ids = state.items.filter(i => state.selected.has(i.id) &&
    ["photo", "pano"].includes(i.kind)).map(i => i.id);
  if (!ids.length) { toast("选择模式里先选中照片"); return; }
  submitJob("ai_tag", ids, {});
});

/* ================= 任务跟踪 ================= */
async function submitJob(kind, ids, extra) {
  try {
    const r = await api("/api/jobs", { method: "POST",
      body: JSON.stringify({ kind, ids, ...extra }) });
    toast("任务已提交，后台处理中…");
    state.tracksJobs.add(r.job_id);
    $("#exModal").classList.add("hidden");
    $("#slModal").classList.add("hidden");
    pollJobs();
  } catch (e) { toast(e.message); }
}

let jobTimer = null;
async function pollJobs() {
  clearTimeout(jobTimer);
  let anyRunning = false;
  for (const id of state.tracksJobs) {
    try {
      const j = await api("/api/jobs/" + id);
      if (j.status === "running" || j.status === "queued") anyRunning = true;
      else {
        state.tracksJobs.delete(id);
        if (j.status === "done") {
          const r = j.result || {};
          toast(r.zip ? `导出完成（${r.count} 张），点击顶栏「任务」下载`
                     : r.mp4 && r.mode ? `拼接完成（${r.count} 段 · ${r.seconds}s · ${r.mode}），点击顶栏「任务」下载`
                     : r.mp4 ? `小片已生成（${r.count} 张 · ${r.seconds}s），点击顶栏「任务」下载`
                             : `AI 打标完成（${r.done} 成功 / ${r.failed} 失败）`);
        } else {
          toast("任务失败：" + (j.note || "未知错误"));
        }
      }
    } catch (e) { state.tracksJobs.delete(id); }
  }
  updateTopmeta();
  if (anyRunning) jobTimer = setTimeout(pollJobs, 1500);
}
$("#topMeta").addEventListener("click", e => {
  if (e.target.closest("[data-jobs]")) openJobModal();
});
async function openJobModal() {
  $("#jobModal").classList.remove("hidden");
  await renderJobs();
}
$("#jobClose").addEventListener("click", () => $("#jobModal").classList.add("hidden"));
async function renderJobs() {
  const box = $("#jobList");
  let jobs = [];
  try {
    jobs = (await api("/api/jobs")).jobs || [];
  } catch (e) { /* skip */ }
  if (!jobs.length) { box.innerHTML = '<div class="muted">还没有任务记录。</div>'; return; }
  box.innerHTML = jobs.map(j => {
    const st = { queued: "排队中", running: "处理中", done: "已完成", failed: "失败" }[j.status] || j.status;
    const r = j.result || {};
    const dl = j.status === "done" && (r.zip || r.mp4)
      ? `<a class="btn btn-primary" href="/api/jobs/${j.id}/download">下载</a>` : "";
    const meta = r.zip ? `${r.count} 张照片`
      : r.mp4 ? `${r.count} 张 · ${r.seconds}s` : r.notes ? r.notes.join("；").slice(0, 80) : "";
    const kindLabel = j.kind === "export" ? "导出" : j.kind === "slideshow" ? "小片" : j.kind === "concat" ? "拼接" : "AI打标";
    const n = (j.payload && j.payload.ids && j.payload.ids.length) || "";
    return `<div class="job-item"><span class="st ${j.status}">${st}</span>
      <span>${kindLabel}${n ? " ×" + n : ""}</span>
      <span class="meta">${escHtml(meta || (j.note || ""))}</span>${dl}</div>`;
  }).join("");
}


/* ================= 相似对比 ================= */
async function openCompare(baseId, otherId) {
  try {
    const [a, b] = await Promise.all([api("/api/items/" + baseId), api("/api/items/" + otherId)]);
    renderCompare(a, Object.assign(b, { _dist: window.__cmpDist }));
    window.__cmpDist = null;
    $("#cmpModal").classList.remove("hidden");
  } catch (e) { toast(e.message); }
}
function renderCompare(a, b) {
  const row = $("#cmpRow");
  const src = it => (it.kind === "raw" || !it.kind) ? (it.thumb_url || `/api/items/${it.id}/thumb`)
                                                    : `/api/items/${it.id}/file`;
  row.innerHTML = [a, b].map((it, i) => `
    <div class="cmp-pane" data-id="${it.id}">
      <div class="head"><span class="nm" title="${escAttr(it.rel_path)}">${escHtml(it.rel_path.split("/").pop())}</span>
        <span class="dist">${i === 1 ? "距离 " + (it._dist != null ? it._dist : "—") : "基准"}</span></div>
      <div class="imgbox"><img src="${src(it)}" loading="lazy"></div>
      <div class="head">
        <span>${it.width ? it.width + "×" + it.height : ""}</span>
        <span>${fmtSize(it.size)}</span>
        <span style="flex:1"></span>
        <button class="btn" data-cmp-rate="${it.id}">${"★".repeat(it.rating) || "打星"}</button>
        <button class="btn ${it.rejected ? "btn-reject on" : ""}" data-cmp-rej="${it.id}">${it.rejected ? "已淘汰" : "淘汰"}</button>
      </div>
    </div>`).join("");
  const cycle = id => {
    const cur = (id === a.id ? a : b).rating || 0;
    return cur >= 5 ? 0 : cur + 1;
  };
  $$("[data-cmp-rate]", row).forEach(btn => btn.addEventListener("click", async () => {
    const id = parseInt(btn.dataset.cmpRate, 10);
    await setRate(id, cycle(id));
    const [na, nb] = await Promise.all([api("/api/items/" + a.id), api("/api/items/" + b.id)]);
    renderCompare(na, Object.assign(nb, { _dist: b._dist }));
  }));
  $$("[data-cmp-rej]", row).forEach(btn => btn.addEventListener("click", async () => {
    const id = parseInt(btn.dataset.cmpRej, 10);
    const cur = (id === a.id ? a : b);
    await setRejected(id, !cur.rejected);
    const [na, nb] = await Promise.all([api("/api/items/" + a.id), api("/api/items/" + b.id)]);
    renderCompare(na, Object.assign(nb, { _dist: b._dist }));
  }));
}
$("#cmpClose").addEventListener("click", () => $("#cmpModal").classList.add("hidden"));
$("#cmpModal").addEventListener("click", e => {
  if (e.target === $("#cmpModal")) $("#cmpModal").classList.add("hidden");
});

/* ================= 重复清理 ================= */
$("#btnDups").addEventListener("click", loadDups);
$("#dupClose").addEventListener("click", () => $("#dupModal").classList.add("hidden"));
$("#dupModal").addEventListener("click", e => {
  if (e.target === $("#dupModal")) $("#dupModal").classList.add("hidden");
});
async function loadDups() {
  try {
    const j = await api("/api/duplicates");
    $("#dupNote").textContent = j.groups.length
      ? `共 ${j.groups.length} 组重复，可回收 ${fmtSize(j.total_wasted)}（文件移入同目录 .djiwb-trash/，不真删，可随时捞回）。每组默认保留最早入库的一张，点卡片可切换保留项。`
      : "没有发现重复文件（哈希完全相同的才计为重复）。";
    const box = $("#dupGroups");
    box.innerHTML = j.groups.map((g, gi) => `
      <div class="ap-group" data-g="${gi}" data-keep="${g.keep_id}">
        ${g.items.map((m, mi) => `
          <div class="dup-item" data-id="${m.id}" title="${escAttr(m.rel_path)}">
            ${m.thumb_url ? `<img src="${m.thumb_url}">` : `<div class="nothumb" style="width:72px;height:54px">🖼️</div>`}
            <span class="${m.id === g.keep_id ? "keep-chip" : "trash-chip"}">${m.id === g.keep_id ? "保留" : "回收"}</span>
            <span class="meta">${m.width || ""} ${fmtSize(m.size)}${m.rejected ? " · 已淘汰" : ""}</span>
          </div>`).join("")}
        <span style="flex:1"></span>
        <button class="btn" data-trash-g="${gi}">回收其余（${fmtSize(g.wasted)}）</button>
      </div>`).join("");
    $$("#dupGroups .dup-item").forEach(el => el.addEventListener("click", () => {
      const grp = el.closest(".ap-group");
      grp.dataset.keep = el.dataset.id;
      $$(".dup-item", grp).forEach(x => {
        const chip = x.querySelector("span:nth-child(2)");
        const isKeep = x.dataset.id === grp.dataset.keep;
        chip.className = isKeep ? "keep-chip" : "trash-chip";
        chip.textContent = isKeep ? "保留" : "回收";
      });
    }));
    $$("[data-trash-g]", box).forEach(btn => btn.addEventListener("click", async () => {
      const grp = btn.closest(".ap-group");
      const ids = $$(".dup-item", grp).map(x => parseInt(x.dataset.id, 10))
        .filter(id => id !== parseInt(grp.dataset.keep, 10));
      if (!ids.length) { toast("该组只剩保留项了"); return; }
      try {
        const r = await api("/api/items/trash", { method: "POST", body: JSON.stringify({ ids }) });
        toast(`已回收 ${r.moved} 个文件到 .djiwb-trash/` + (r.errors.length ? `，失败 ${r.errors.length}` : ""));
        await loadStats();
        loadItems();
        loadDups();
      } catch (e) { toast(e.message); }
    }));
    $("#dupModal").classList.remove("hidden");
  } catch (e) { toast(e.message); }
}

/* ================= 存储面板 ================= */
async function loadStorage() {
  try {
    const j = await api("/api/storage");
    const total = Math.max(1, j.total_size);
    const box = $("#storageBox");
    const rows = j.by_device.map(d => `
      <div class="st-row"><span style="min-width:130px">${escHtml(d.name)}</span>
        <div class="bar"><i style="width:${Math.max(2, Math.round(d.size / total * 100))}%"></i></div>
        <span class="val">${fmtSize(d.size)} · ${d.count} 项</span></div>`).join("");
    const cacheChips = Object.entries(j.cache).map(([k, v]) => {
      const label = { thumbs: "缩略图", posters: "海报", proxies: "代理", sprites: "雪碧图", exports: "导出产物" }[k] || k;
      const cleanable = ["posters", "sprites", "exports"].includes(k) && v > 0;
      return `<span class="chip">${label} ${fmtSize(v)}
        ${cleanable ? `<button class="btn" data-clean="${k}" style="padding:1px 8px;font-size:11px">清</button>` : ""}</span>`;
    }).join("");
    box.innerHTML = `
      <div class="st-row"><span style="min-width:130px">素材总量</span>
        <div class="bar"><i style="width:100%"></i></div>
        <span class="val">${fmtSize(j.total_size)}</span></div>
      ${rows}
      ${j.trashed ? `<div class="st-row"><span style="min-width:130px">回收站</span>
        <span class="val">${j.trashed} 个文件在 .djiwb-trash/</span></div>` : ""}
      <div class="st-cache">${cacheChips}</div>`;
    $$("[data-clean]", box).forEach(b => b.addEventListener("click", async () => {
      const t = b.dataset.clean;
      const label = { posters: "海报缓存（可即时重建）", sprites: "雪碧图缓存（可即时重建）", exports: "导出产物（下载后可删）" }[t];
      if (!confirm(`清理${label}？`)) return;
      try {
        const r = await api("/api/storage/cleanup", { method: "POST", body: JSON.stringify({ targets: [t] }) });
        toast(`已清理 ${fmtSize(r.freed)}`);
        loadStorage();
      } catch (e) { toast(e.message); }
    }));
  } catch (e) { /* 设置页静默 */ }
}


/* ================= LUT 工作台 ================= */
const lutWs = { luts: [], current: "", selected: new Set(), orig: false, photos: [] };

async function refreshLutSelect() {
  try {
    const j = await api("/api/luts");
    lutWs.luts = j.luts || [];
    const sel = $("#exLut");
    const cur = sel.value;
    sel.innerHTML = '<option value="">不套用</option>' +
      lutWs.luts.map(l => `<option value="${escAttr(l.name)}">${escHtml(l.name.replace(/\.cube$/i, ""))}</option>`).join("");
    sel.value = cur;
  } catch (e) { /* 静默 */ }
}

async function loadLutWs() {
  await refreshLutSelect();
  renderLutList();
  try {
    const j = await api("/api/items?kinds=photo,pano,raw&per_page=200&sort=date_desc");
    lutWs.photos = j.items;
    renderLutGrid();
  } catch (e) { toast(e.message); }
}

function renderLutList() {
  const box = $("#lutList");
  if (!lutWs.luts.length) {
    box.innerHTML = '<div class="muted">还没有 LUT。上传 .cube 文件开始（官方还原 LUT 见 dji.com/cn/lut）。</div>';
    return;
  }
  box.innerHTML = lutWs.luts.map(l => `
    <div class="lut-item ${lutWs.current === l.name ? "on" : ""}" data-name="${escAttr(l.name)}">
      <span class="nm" title="${escAttr(l.name)}">${escHtml(l.name.replace(/\.cube$/i, ""))}</span>
      <span class="sz">${fmtSize(l.size)}</span>
      <button class="btn" data-del="${escAttr(l.name)}" title="删除">✕</button>
    </div>`).join("");
  $$("#lutList .lut-item").forEach(el => el.addEventListener("click", ev => {
    if (ev.target.closest("[data-del]")) return;
    lutWs.current = el.dataset.name;
    renderLutList();
    renderLutGrid();
  }));
  $$("#lutList [data-del]").forEach(b => b.addEventListener("click", async ev => {
    ev.stopPropagation();
    if (!confirm(`删除 LUT「${b.dataset.del}」？`)) return;
    try {
      await api("/api/luts/" + encodeURIComponent(b.dataset.del), { method: "DELETE" });
      if (lutWs.current === b.dataset.del) lutWs.current = "";
      loadLutWs();
      refreshLutSelect();
    } catch (e) { toast(e.message); }
  }));
}

function renderLutGrid() {
  const grid = $("#lutGrid");
  const lutParam = lutWs.current && !lutWs.orig ? `?lut=${encodeURIComponent(lutWs.current)}` : "";
  $("#lutPhotoN").textContent = `${lutWs.photos.length} 张照片`;
  $("#lutSelN").textContent = lutWs.selected.size;
  $("#lutExport").disabled = !lutWs.current || !lutWs.selected.size;
  grid.innerHTML = lutWs.photos.map(it => `
    <div class="card lut-card ${lutWs.selected.has(it.id) ? "selected" : ""}" data-id="${it.id}" title="${escAttr(it.rel_path)}">
      ${it.thumb_url
        ? `<img class="thumb loaded lut-prev" src="${lutParam ? `/api/items/${it.id}/preview${lutParam}` : it.thumb_url}">`
        : `<div class="nothumb">🖼️</div>`}
      <span class="pick" data-pick="${it.id}" title="勾选（点击图片可放大查看）">${lutWs.selected.has(it.id) ? "✓" : ""}</span>
      <div class="status-foot"><span class="name">${escHtml(it.rel_path.split("/").pop())}</span></div>
    </div>`).join("") || '<div class="empty">没有照片素材</div>';
  $$("#lutGrid img.lut-prev").forEach(im => {
    let retried = false;
    im.addEventListener("error", () => {
      if (retried) { im.style.background = "repeating-linear-gradient(45deg,#141a22,#141a22 12px,#171e27 12px,#171e27 24px)"; return; }
      retried = true;
      setTimeout(() => { im.src = im.src; }, 4000);   // 预览在服务端串行生成，稍后重试
    });
  });
  $$("#lutGrid .card").forEach(c => c.addEventListener("click", ev => {
    const id = parseInt(c.dataset.id, 10);
    if (ev.target.closest("[data-pick]")) {           // 勾选区：只切勾选
      lutWs.selected.has(id) ? lutWs.selected.delete(id) : lutWs.selected.add(id);
      $("#lutSelN").textContent = lutWs.selected.size;
      $("#lutExport").disabled = !lutWs.current || !lutWs.selected.size;
      c.classList.toggle("selected", lutWs.selected.has(id));
      c.querySelector(".pick").textContent = lutWs.selected.has(id) ? "✓" : "";
      return;
    }
    openLutViewer(id);                                 // 点图片：同屏对比查看
  }));
}

$("#lutFile").addEventListener("change", async ev => {
  const f = ev.target.files[0];
  if (!f) return;
  const fd = new FormData();
  fd.append("file", f);
  try {
    const r = await fetch("/api/luts", { method: "POST", body: fd });
    const j = await r.json();
    if (!r.ok) throw new Error(j.error || "上传失败");
    toast(`LUT「${j.name}」已上传`);
    lutWs.current = j.name;
    loadLutWs();
    refreshLutSelect();
  } catch (e) { toast(e.message); }
  ev.target.value = "";
});

$("#lutOrigToggle").addEventListener("change", ev => {
  lutWs.orig = ev.target.checked;
  renderLutGrid();
});
$("#lutAll").addEventListener("click", () => {
  lutWs.photos.forEach(p => lutWs.selected.add(p.id));
  renderLutGrid();
});
$("#lutClear").addEventListener("click", () => { lutWs.selected.clear(); renderLutGrid(); });
$("#lutExport").addEventListener("click", () => {
  if (!lutWs.current) { toast("先选择一款 LUT"); return; }
  const ids = [...lutWs.selected];
  submitJob("export", ids, { opts: { lut: lutWs.current, format: "jpg" } });
});


/* ================= 编辑台（Concat 引擎 · 全功能） ================= */
const ed = { project: null, clips: [], dur: 0, timelineId: "TL1", exporting: false, exportJob: null, projInfo: null, linkage: true,
  tlSel: null, tlPlayhead: 0, tlPps: 60, tracks: [],
  projectsDir: "", effects: null, expanded: new Set() };

async function edApi(tool, args, timeout) {
  const r = await fetch("/api/editor/call", { method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ tool, args, timeout }) });
  const d = await r.json();
  if (d.error && !d.text) throw new Error(d.error);
  if (d.isError) throw new Error((d.text || "工具错误").slice(0, 220));
  return d;
}
async function edCheck() {
  try {
    const h = await (await fetch("/api/editor/health")).json();
    const ok = !!h.ok;
    $("#edStatus").textContent = ok ? "引擎在线 · " + (h.concat || "0.2.8-headless").replace("-headless", " ") + "· 32 工具已接入（多轨/撤销/标题字幕/特效层/关键帧/127特效/遮罩/防抖/画幅/导出）" : "引擎未启用（设置页填 Concat 地址）";
    $("#edConsole").classList.toggle("hidden", false);
    $("#edMain").classList.toggle("hidden", !ok);
    if (ok) {
      const info = await (await fetch("/api/editor/info")).json();
      ed.projectsDir = info.projects || "";
      const saved = localStorage.getItem("edProject");
      if (saved && !ed.project) {
        ed.project = saved; $("#edProject").textContent = saved.split("/").pop();
        try { const gd = await edApi("project_get", { path: saved }); const gp = JSON.parse(gd.text || "{}").project || {}; if (gp.width) ed.projInfo = { w: gp.width, h: gp.height, fps: gp.fps }; } catch (e) {}
        await edTimeline();
      }
      if (!ed.effects) edLoadEffects();
      edLoadAudioSel();
      edLoadProjSel();
    }
  } catch (e) {
    $("#edStatus").textContent = "引擎未启用";
    $("#edMain").classList.add("hidden");
  }
}
$("#edVncToggle").addEventListener("click", () => {
  const v = $("#edVnc");
  const open = v.classList.toggle("hidden") === false;
  $("#edVncToggle").textContent = open ? "▼ 收起原版界面" : "▶ 原版界面（可选）";
  if (open && $("#edVncFrame").src === "about:blank") {
    $("#edVncFrame").src = "http://" + location.hostname + ":18790/vnc.html?host=" + location.hostname + "&port=18790&autoconnect=true&resize=remote";
  }
});
async function edLoadProjSel() {
  const sel = $("#edProjSel");
  if (!sel) return;
  try {
    const d = await edApi("project_list", {});
    const list = JSON.parse(d.text) || [];
    sel.innerHTML = list.map(p => `<option value="${escAttr(p.path)}">${escHtml(p.name || p.path.split("/").pop())}</option>`).join("")
      || "<option value=''>（无历史工程）</option>";
  } catch (e) { sel.innerHTML = "<option value=''>（加载失败）</option>"; }
}
$("#edProjOpen").addEventListener("click", async () => {
  const path = $("#edProjSel").value;
  if (!path) { toast("没有可打开的工程"); return; }
  try {
    const d = await edApi("project_open", { path });
    const doc = JSON.parse(d.text || "{}");
    const pj = doc.project || {};
    if (pj.width) { ed.projInfo = { w: pj.width, h: pj.height, fps: pj.fps }; }
    ed.project = path;
    localStorage.setItem("edProject", path);
    $("#edProject").textContent = path.split("/").pop();
    await edTimeline();
    edLoadProjSel();
    toast("已打开工程" + (pj.width ? `（${pj.width}×${pj.height}@${pj.fps}）` : ""));
  } catch (err) { toast(err.message.slice(0, 160)); }
});
$("#edProjClose").addEventListener("click", async () => {
  if (!ed.project) { toast("当前没有打开的工程"); return; }
  const path = ed.project;
  try {
    await edApi("project_save", { path });
    await edApi("project_close", { path, save: true });
    ed.project = null;
    ed.clips = [];
    ed.dur = 0;
    localStorage.removeItem("edProject");
    $("#edProject").textContent = "（未创建）";
    edRender();
    $("#edPreview").classList.add("hidden");
    $("#edPreviewEmpty").classList.remove("hidden");
    edLoadProjSel();
    toast("工程已关闭并保存");
  } catch (err) { toast(err.message.slice(0, 160)); }
});
async function edLoadEffects() {
  try {
    const d = await edApi("effects_list", { path: ed.project || "/opt/concat-home/projects" });
    ed.effects = JSON.parse(d.text);
  } catch (e) { ed.effects = []; }
  edFillFxSel();
}
async function edTimeline() {
  const d = await edApi("timeline_read", { path: ed.project });
  const t = JSON.parse(d.text);
  ed.clips = t.timeline.clips || [];
  ed.dur = t.timeline.duration || 0;
  ed.timelineId = t.timeline.id || "TL1";
  ed.tracks = (t.timeline.tracks || []).map(x => x.id);
  let dimTxt = "";
  if (ed.projInfo) dimTxt = ` · ${ed.projInfo.w}×${ed.projInfo.h}@${ed.projInfo.fps}`;
  $("#edDur").textContent = "总长 " + ed.dur.toFixed(1) + "s" + dimTxt;
  edRender();
  await edApi("project_save", { path: ed.project });
}
function edRender() {
  edTlRender();
  const box = $("#edClips");
  if (!ed.clips.length) {
    box.innerHTML = '<div class="muted empty-tip">时间轴为空。从素材库勾选视频后点「从勾选创建项目」。</div>';
    return;
  }
  box.innerHTML = ed.clips.map((c, i) => {
    const open = ed.expanded.has(c.id);
    const kindTag = c.kind === "text" ? '<span class="concat-order" style="background:#3a5a3a">字</span>'
      : c.kind === "layer" ? '<span class="concat-order" style="background:#5a4a2a">效</span>' : "";
    const reorder = c.kind === "video" ? `
        <button class="btn btn-sm" data-edact="up" data-idx="${i}" title="与前一片段交换位置">↑</button>
        <button class="btn btn-sm" data-edact="down" data-idx="${i}" title="与后一片段交换位置">↓</button>` : "";
    return `
    <div class="concat-item">
      <span class="concat-order">${i + 1}</span>
      ${kindTag}
      <span class="concat-name" title="${escHtml(c.name || c.id)}">${escHtml(c.name || c.id)}</span>
      <span class="concat-dur">${(+c.start).toFixed(2)}–${(+c.start + +c.duration).toFixed(2)}s · ${c.track}</span>
      <span class="ed-clipops">
        ${reorder}
        ${c.kind === "video" ? `<button class="btn btn-sm" data-edact="split" data-idx="${i}" title="在时间 X 秒处分割">✂ 分割</button>` : ""}
        <button class="btn btn-sm" data-edact="detail" data-idx="${i}">${open ? "收起" : "详情"}</button>
        <button class="btn btn-sm" data-edact="del" data-idx="${i}">删</button>
      </span>
    </div>${open ? edDetailHtml(c, i) : ""}`;
  }).join("") + `<div class="ed-tlrow">
    <label class="chk" title="字幕/标题/音效随主轨移动（0.2.9 新能力）">联动<input type="checkbox" id="edLinkage" ${ed.linkage ? "checked" : ""}></label>
    <label class="muted">分割时间点 <input id="edSplitTime" class="ed-num" type="number" step="0.1" min="0" value="${ed.dur ? (ed.dur / 2).toFixed(1) : 0}"> s</label>
    <button class="btn btn-sm" id="edAddTrackTop">+轨道（上）</button>
    <button class="btn btn-sm" id="edAddTrackBottom">+轨道（下）</button>
    <select id="edBgKind" class="ed-res">
      <option value="none">背景：黑</option>
      <option value="blur">背景：模糊</option>
      <option value="color">背景：纯色</option>
    </select>
    <input id="edBgVal" class="ed-num" type="text" placeholder="#000000 或 0.5" style="display:none">
    <button class="btn btn-sm" id="edBgApply">应用背景</button>
  </div>`;
}
function edDetailHtml(c, i) {
  const fx = (c.videoEffects || []);
  if (c.kind === "text") {
    const tx = c.text || {};
    return `<div class="ed-detail" data-detail="${c.id}">
    <div class="ed-sec"><b>文字</b>
      <label style="flex:1 1 100%">内容<input class="ed-num" type="text" style="width:100%" data-edtx="content" value="${escAttr(tx.content || "")}"></label>
      <label>字号<input class="ed-num" type="number" step="0.01" min="0.02" max="0.5" data-edtx="fontSize" value="${tx.fontSize != null ? +tx.fontSize : 0.09}"></label>
      <label>颜色<input class="ed-num" type="text" data-edtx="color" value="${tx.color || "#ffffff"}"></label>
      <label>对齐<select class="ed-res" data-edtx="align">${["left","center","right"].map(a => `<option value="${a}" ${tx.align === a ? "selected" : ""}>${a}</option>`).join("")}</select></label>
      <label>描边宽<input class="ed-num" type="number" step="0.005" min="0" max="0.1" data-edtx="strokeWidth" value="${tx.strokeWidth != null ? +tx.strokeWidth : 0}"></label>
      <label class="chk">阴影<input type="checkbox" data-edtx="shadow" ${tx.shadow ? "checked" : ""}></label>
      <button class="btn btn-sm" data-edapply="text" data-idx="${i}">应用文字</button>
    </div>
    <div class="ed-sec"><b>时间与位置</b>
      <label>开始(s)<input class="ed-num" type="number" step="0.1" min="0" data-edmv="start" value="${(+c.start).toFixed(2)}"></label>
      <label>时长(s)<input class="ed-num" type="number" step="0.1" min="0.2" data-edmv="dur" value="${(+c.duration).toFixed(2)}"></label>
      <label>垂直位置<input class="ed-num" type="number" step="0.05" min="-0.9" max="0.9" data-edmv="offsetY" value="${c.offsetY != null ? +c.offsetY : 0}"></label>
      <button class="btn btn-sm" data-edapply="movetext" data-idx="${i}">应用时间/位置</button>
    </div>
    <div class="ed-sec"><b>词样式（字符范围，0.2.9）</b>
      <label>起<input class="ed-num" type="number" step="1" min="0" data-edws="start" value="0"></label>
      <label>止<input class="ed-num" type="number" step="1" min="0" data-edws="end" value="1"></label>
      <label>颜色<input type="text" data-edws="color" value="#ff5533" style="width:72px"></label>
      <label>字号倍数<input class="ed-num" type="number" step="0.1" min="0.1" max="10" data-edws="fontScale" value="1.5"></label>
      <label>字重<input class="ed-num" type="number" step="100" min="100" max="900" data-edws="fontWeight" value="700"></label>
      <button class="btn btn-sm" data-edapply="wordstyle" data-idx="${i}">设词样式</button>
      <button class="btn btn-sm" data-edapply="wordclear" data-idx="${i}">清除词样式</button>
    </div>
  </div>`;
  }
  if (c.kind === "layer") {
    const f0 = fx[0] || { id: c.name, params: {} };
    const cat = (ed.effects || []).find(x => x.id === f0.id) || { params: [] };
    const params = (cat.params || []).filter(pp => pp.type === "float" || pp.type === "int" || pp.type === "color");
    return `<div class="ed-detail" data-detail="${c.id}">
    <div class="ed-sec"><b>特效层参数（${escHtml(f0.id)}）</b>
      ${params.map(pp => `<label>${pp.key}<input class="ed-num" type="${pp.type === "color" ? "text" : "number"}" step="${pp.step || 0.1}" data-edlp="${pp.key}" data-lptype="${pp.type}" value="${(f0.params && f0.params[pp.key] != null) ? f0.params[pp.key] : pp.default}"></label>`).join("") || '<span class="muted">该特效无可调参数</span>'}
      <button class="btn btn-sm" data-edapply="layerparam" data-idx="${i}" ${params.length ? "" : "disabled"}>应用参数</button>
    </div>
    <div class="ed-sec"><b>区间</b>
      <label>开始(s)<input class="ed-num" type="number" step="0.1" min="0" data-edmv="start" value="${(+c.start).toFixed(2)}"></label>
      <label>时长(s)<input class="ed-num" type="number" step="0.1" min="0.2" data-edmv="dur" value="${(+c.duration).toFixed(2)}"></label>
      <button class="btn btn-sm" data-edapply="movetext" data-idx="${i}">应用区间</button>
    </div>
  </div>`;
  }
  return `<div class="ed-detail" data-detail="${c.id}">
    <div class="ed-sec"><b>基础</b>
      <label>掐头(源内s)<input class="ed-num" type="number" step="0.1" min="0" data-edp="sourceStart" value="${(+c.sourceStart || 0).toFixed(2)}"></label>
      <label>时长(s)<input class="ed-num" type="number" step="0.1" min="0.1" data-edp="duration" value="${(+c.duration).toFixed(2)}"></label>
      <label>速度<input class="ed-num" type="number" step="0.1" min="0.1" data-edp="speed" value="${c.speed || 1}"></label>
      <label>音量<input class="ed-num" type="number" step="0.1" min="0" data-edp="volume" value="${c.volume != null ? +c.volume : 1}"></label>
      <label>淡入(s)<input class="ed-num" type="number" step="0.1" min="0" data-edp="fadeIn" value="${c.fadeIn != null ? +c.fadeIn : 0}"></label>
      <label>淡出(s)<input class="ed-num" type="number" step="0.1" min="0" data-edp="fadeOut" value="${c.fadeOut != null ? +c.fadeOut : 0}"></label>
      <label class="chk">静音<input type="checkbox" data-edp="muted" ${c.muted ? "checked" : ""}></label>
      <button class="btn btn-sm" data-edapply="base" data-idx="${i}">应用</button>
    </div>
    <div class="ed-sec"><b>画面</b>
      <button class="btn btn-sm" data-edact="fit" data-idx="${i}">适配完整</button>
      <button class="btn btn-sm" data-edact="fill" data-idx="${i}">铺满裁边</button>
      <label>缩放<input class="ed-num" type="number" step="0.05" min="0.05" data-edt="scale" value="${c.scale != null ? +c.scale : 1}"></label>
      <label>X<input class="ed-num" type="number" step="0.02" data-edt="offsetX" value="${c.offsetX != null ? +c.offsetX : 0}"></label>
      <label>Y<input class="ed-num" type="number" step="0.02" data-edt="offsetY" value="${c.offsetY != null ? +c.offsetY : 0}"></label>
      <label>旋转°<input class="ed-num" type="number" step="1" data-edt="rotation" value="${c.rotation != null ? +c.rotation : 0}"></label>
      <label>不透明%<input class="ed-num" type="number" step="1" min="0" max="100" data-edt="opacity" value="${c.opacity != null ? Math.round(+c.opacity * 100) : 100}"></label>
      <label class="chk">水平镜像<input type="checkbox" data-edt="flipH" ${c.flipH ? "checked" : ""}></label>
      <label class="chk">垂直镜像<input type="checkbox" data-edt="flipV" ${c.flipV ? "checked" : ""}></label>
      <button class="btn btn-sm" data-edapply="transform" data-idx="${i}">应用变换</button>
    </div>
    <div class="ed-sec"><b>裁剪（源占比 0..1）</b>
      <label>左<input class="ed-num" type="number" step="0.02" min="0" max="0.9" data-edc="left" value="${c.crop ? +c.crop.left || 0 : 0}"></label>
      <label>上<input class="ed-num" type="number" step="0.02" min="0" max="0.9" data-edc="top" value="${c.crop ? +c.crop.top || 0 : 0}"></label>
      <label>右<input class="ed-num" type="number" step="0.02" min="0" max="0.9" data-edc="right" value="${c.crop ? +c.crop.right || 0 : 0}"></label>
      <label>下<input class="ed-num" type="number" step="0.02" min="0" max="0.9" data-edc="bottom" value="${c.crop ? +c.crop.bottom || 0 : 0}"></label>
      <button class="btn btn-sm" data-edapply="crop" data-idx="${i}">应用裁剪</button>
      <button class="btn btn-sm" data-edapply="cropnull" data-idx="${i}">取消裁剪</button>
    </div>
    <div class="ed-sec"><b>遮罩</b>
      <select class="ed-res" data-edm="shape">
        ${["circle","rectangle","linear","mirror","heart","star"].map(sh => `<option value="${sh}" ${c.mask && c.mask.shape === sh ? "selected" : ""}>${sh}</option>`).join("")}
      </select>
      <label>X<input class="ed-num" type="number" step="0.05" data-edm="x" value="${c.mask ? +c.mask.x || 0 : 0}"></label>
      <label>Y<input class="ed-num" type="number" step="0.05" data-edm="y" value="${c.mask ? +c.mask.y || 0 : 0}"></label>
      <label>宽<input class="ed-num" type="number" step="0.05" min="0.05" max="2" data-edm="width" value="${c.mask ? +c.mask.width || 0.66 : 0.66}"></label>
      <label>高<input class="ed-num" type="number" step="0.05" min="0.05" max="2" data-edm="height" value="${c.mask ? +c.mask.height || 0.66 : 0.66}"></label>
      <label>旋转°<input class="ed-num" type="number" step="5" data-edm="rotation" value="${c.mask ? +c.mask.rotation || 0 : 0}"></label>
      <label>圆角<input class="ed-num" type="number" step="0.05" min="0" max="1" data-edm="roundness" value="${c.mask ? +c.mask.roundness || 0 : 0}"></label>
      <label>羽化<input class="ed-num" type="number" step="0.05" min="0" max="1" data-edm="feather" value="${c.mask ? +c.mask.feather || 0 : 0}"></label>
      <label class="chk">反选<input type="checkbox" data-edm="invert" ${c.mask && c.mask.invert ? "checked" : ""}></label>
      <button class="btn btn-sm" data-edapply="mask" data-idx="${i}">应用遮罩</button>
      <button class="btn btn-sm" data-edapply="masknull" data-idx="${i}">移除遮罩</button>
    </div>
    <div class="ed-sec"><b>防抖</b>
      <label>平滑度<input class="ed-num" type="number" step="0.1" min="0" max="1" data-edsb="smoothness" value="0.5"></label>
      <label class="chk">保持旋转<input type="checkbox" data-edsb="keepRotation" checked></label>
      <button class="btn btn-sm" data-edapply="stab" data-idx="${i}">开启防抖</button>
      <button class="btn btn-sm" data-edapply="stabnull" data-idx="${i}">关闭防抖</button>
    </div>
    <div class="ed-sec"><b>关键帧（属性/位置0..1/数值）</b>
      <select class="ed-res" data-edkf="prop">
        ${["scale","offsetX","offsetY","rotation","opacity","volume","maskX","maskY","maskWidth","maskHeight","maskRotation","maskRoundness","maskFeather"].map(pp => `<option value="${pp}">${pp}</option>`).join("")}
      </select>
      <label>位置<input class="ed-num" type="number" step="0.05" min="0" max="1" data-edkf="at" value="0"></label>
      <label>数值<input class="ed-num" type="number" step="0.1" data-edkf="value" value="1"></label>
      <button class="btn btn-sm" data-edact="keyset" data-idx="${i}">设关键帧</button>
      <button class="btn btn-sm" data-edact="keyclear" data-idx="${i}">清关键帧</button>
    </div>
    <div class="ed-sec"><b>特效/滤镜链（${fx.length} 个）</b>
      <select class="ed-res" data-edfx="pick">
        ${["effect","filter","audio","transition"].map(kind => {
          const list = (ed.effects || []).filter(x => x.kind === kind);
          if (!list.length) return "";
          return `<optgroup label="${kind}">` + list.map(x => `<option value="${x.id}">${x.name}（${kind}）</option>`).join("") + "</optgroup>";
        }).join("")}
      </select>
      <button class="btn btn-sm" data-edapply="fxadd" data-idx="${i}">+添加特效</button>
      ${fx.map((f, fi) => {
        const cat = (ed.effects || []).find(x => x.id === f.id);
        const params = (cat && cat.params || []).filter(pp => pp.type === "float" || pp.type === "int");
        return `<div class="ed-fxrow" data-fxrow="${fi}">
          <span class="ed-fxname">${escHtml(f.id)}${f.enabled === false ? "（停用）" : ""}</span>
          ${params.map(pp => `<label>${pp.key}<input class="ed-num" type="number" step="${pp.step || 0.1}" data-edfxp="${fi}|${pp.key}" value="${(f.params && f.params[pp.key] != null) ? f.params[pp.key] : pp.default}"></label>`).join("")}
          <label class="chk">启用<input type="checkbox" data-edfxe="${fi}" ${f.enabled !== false ? "checked" : ""}></label>
          <button class="btn btn-sm" data-edapply="fxdel" data-idx="${i}" data-fi="${fi}">移除</button>
        </div>`;
      }).join("")}
    </div>
    <div class="ed-sec"><b>混合模式 / 片段背景 / 入场转场</b>
      <label>混合<input class="ed-num" type="text" data-edb="blend" value="${c.blend || ""}" placeholder="留空=正常"></label>
      <select class="ed-res" data-edb="bgkind">
        <option value="none" ${c.background && c.background.kind === "none" ? "selected" : ""}>黑</option>
        <option value="blur" ${c.background && c.background.kind === "blur" ? "selected" : ""}>模糊放大</option>
        <option value="color" ${c.background && c.background.kind === "color" ? "selected" : ""}>纯色</option>
      </select>
      <input class="ed-num" type="text" data-edb="bgcolor" placeholder="#000000" value="${c.background && c.background.color || "#000000"}">
      <label>模糊度<input class="ed-num" type="number" step="0.1" min="0" max="1" data-edb="bgamount" value="${c.background && c.background.amount != null ? +c.background.amount : 0.5}"></label>
      <button class="btn btn-sm" data-edapply="bg" data-idx="${i}">应用背景</button>
      <label>入场转场id<input class="ed-num" type="text" data-edb="transid" value="${c.transitionIn && c.transitionIn.id || ""}" placeholder="如 cross-fade"></label>
      <label>转场s<input class="ed-num" type="number" step="0.2" min="0.2" data-edb="transdur" value="${c.transitionIn && c.transitionIn.duration || 1}"></label>
      <button class="btn btn-sm" data-edapply="trans" data-idx="${i}">应用转场</button>
    </div>
  </div>`;
}
/* —— 可视化时间轴（画布渲染+拖拽交互） —— */
const TL_KIND_COLOR = { video: "#2f6fd8", text: "#d8a72f", layer: "#8a5fd8", audio: "#3fa66a", transition: "#d8712f", image: "#2fa8b8" };
function edTlLayout() {
  const cv = $("#edTlCanvas");
  if (!cv) return null;
  const wrap = cv.parentElement;
  const avail = Math.max(wrap.clientWidth - 2, 200);
  const contentW = Math.max(avail, Math.ceil((ed.dur + 2) * ed.tlPps) + 24);
  const dpr = window.devicePixelRatio || 1;
  if (cv.width !== Math.round(contentW * dpr)) {
    cv.width = Math.round(contentW * dpr); cv.height = Math.round(180 * dpr);
    cv.style.width = contentW + "px"; cv.style.height = "180px";
  }
  return { cv, ctx: cv.getContext("2d"), dpr, w: contentW, rulerH: 26, pad: 6 };
}
function edTlRender() {
  const L = edTlLayout();
  if (!L) return;
  const { cv, ctx, dpr, w, rulerH } = L;
  ctx.save(); ctx.scale(dpr, dpr);
  ctx.clearRect(0, 0, w, 180);
  const lanes = ed.tracks.length || 1;
  const laneH = Math.min(36, Math.floor((180 - rulerH - 8) / lanes));
  const laneTop = rulerH;
  const pps = ed.tlPps;
  // 泳道背景（主轨在下方：数组倒序画）
  for (let i = 0; i < lanes; i++) {
    const y = laneTop + (lanes - 1 - i) * laneH;
    ctx.fillStyle = i === 0 ? "rgba(47,111,216,.07)" : "#131a26";
    ctx.fillRect(0, y + 1, w, laneH - 2);
    ctx.fillStyle = "#5a6475"; ctx.font = "10px sans-serif";
    ctx.fillText(ed.tracks[i] || ("L" + i), 4, y + 12);
  }
  // 标尺
  ctx.fillStyle = "#0d1118"; ctx.fillRect(0, 0, w, rulerH);
  const step = pps >= 100 ? 1 : pps >= 40 ? 5 : 10;
  ctx.strokeStyle = "#3a4557"; ctx.fillStyle = "#8a94a5"; ctx.font = "10px sans-serif";
  for (let t = 0; t * pps < w; t += step) {
    const x = Math.round(t * pps) + .5;
    ctx.beginPath(); ctx.moveTo(x, rulerH - 8); ctx.lineTo(x, rulerH); ctx.stroke();
    if (t % (step * 2) === 0) ctx.fillText(t + "s", x + 3, rulerH - 11);
  }
  // 片段
  for (const c of ed.clips) {
    const li = ed.tracks.indexOf(c.track);
    if (li < 0) continue;
    const y = laneTop + (lanes - 1 - li) * laneH;
    const x = Math.round(+c.start * pps) + .5, cw = Math.max(3, Math.round(+c.duration * pps));
    const col = TL_KIND_COLOR[c.kind] || "#5f6d82";
    const sel = ed.tlSel === c.id;
    ctx.fillStyle = col; ctx.globalAlpha = sel ? 1 : .78;
    ctx.beginPath(); ctx.roundRect(x, y + 3, cw, laneH - 6, 5); ctx.fill();
    ctx.globalAlpha = 1;
    if (sel) { ctx.strokeStyle = "#fff"; ctx.lineWidth = 2; ctx.stroke(); }
    ctx.strokeStyle = "rgba(255,255,255,.35)"; ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(x + 4, y + 6); ctx.lineTo(x + 4, y + laneH - 6);
    ctx.moveTo(x + cw - 4, y + 6); ctx.lineTo(x + cw - 4, y + laneH - 6); ctx.stroke();
    const label = (c.name || c.id).slice(0, Math.max(2, Math.floor(cw / 7)));
    if (cw > 24) { ctx.fillStyle = "#fff"; ctx.font = "10px sans-serif"; ctx.fillText(label, x + 7, y + laneH / 2 + 3); }
  }
  // 播放头
  const px = Math.round(ed.tlPlayhead * pps) + .5;
  ctx.strokeStyle = "#ff5330"; ctx.lineWidth = 1.5;
  ctx.beginPath(); ctx.moveTo(px, 4); ctx.lineTo(px, 180); ctx.stroke();
  ctx.fillStyle = "#ff5330"; ctx.beginPath();
  ctx.moveTo(px - 5, 4); ctx.lineTo(px + 5, 4); ctx.lineTo(px, 12); ctx.closePath(); ctx.fill();
  ctx.restore();
}
(function edTlInit() {
  document.addEventListener("DOMContentLoaded", () => {
    const cv = $("#edTlCanvas");
    if (!cv) return;
    const ppsOf = () => ed.tlPps;
    const laneGeo = () => {
      const lanes = ed.tracks.length || 1;
      const laneH = Math.min(36, Math.floor((180 - 26 - 8) / lanes));
      return { lanes, laneH, laneTop: 26 };
    };
    const hit = (mx, my) => {
      const { lanes, laneH, laneTop } = laneGeo();
      if (my < laneTop) return { zone: "ruler" };
      const liFromY = lanes - 1 - Math.floor((my - laneTop) / laneH);
      if (liFromY < 0 || liFromY >= lanes) return {};
      const lane = ed.tracks[liFromY];
      for (const c of ed.clips) {
        if (c.track !== lane) continue;
        const x = +c.start * ppsOf(), cw = Math.max(3, +c.duration * ppsOf());
        if (mx >= x - 3 && mx <= x + cw + 3) {
          const edge = 6;
          if (mx < x + edge) return { zone: "trim-l", c };
          if (mx > x + cw - edge) return { zone: "trim-r", c };
          return { zone: "move", c, laneY: liFromY };
        }
      }
      return { zone: "lane", lane };
    };
    let drag = null;
    cv.addEventListener("pointerdown", e => {
      const r = cv.getBoundingClientRect();
      const mx = e.clientX - r.left, my = e.clientY - r.top;
      const h = hit(mx, my);
      if (h.zone === "ruler") { drag = { kind: "playhead" }; cv.setPointerCapture(e.pointerId); return; }
      if (h.zone === "move" || h.zone === "trim-l" || h.zone === "trim-r") {
        ed.tlSel = h.c.id; edRender();
        drag = { kind: h.zone, c: h.c, x0: mx, my0: my,
                 start0: +h.c.start, dur0: +h.c.duration, ss0: +h.c.sourceStart || 0,
                 moved: false };
        cv.setPointerCapture(e.pointerId); cv.classList.add("grabbing");
      } else if (h.zone === "lane") {
        ed.tlPlayhead = Math.max(0, mx / ppsOf());
        const t = $("#edPreviewTime"); if (t) t.value = ed.tlPlayhead.toFixed(1);
        edTlRender(); drag = { kind: "playhead" }; cv.setPointerCapture(e.pointerId);
      }
    });
    cv.addEventListener("pointermove", e => {
      const r = cv.getBoundingClientRect();
      const mx = e.clientX - r.left, my = e.clientY - r.top;
      if (!drag) {  // 悬停光标
        const h = hit(mx, my);
        cv.classList.toggle("trim-l", h.zone === "trim-l");
        cv.classList.toggle("trim-r", h.zone === "trim-r");
        return;
      }
      const dx = (mx - drag.x0) / ppsOf();
      if (Math.abs(mx - drag.x0) > 3 || Math.abs(my - drag.my0) > 3) drag.moved = true;
      if (drag.kind === "playhead") {
        ed.tlPlayhead = Math.max(0, Math.min(ed.dur, mx / ppsOf()));
        const t = $("#edPreviewTime"); if (t) t.value = ed.tlPlayhead.toFixed(1);
        edTlRender(); edTlScrub();
      } else if (drag.kind === "move") {
        drag.newStart = Math.max(0, drag.start0 + dx);
        const { lanes, laneH, laneTop } = laneGeo();
        const li = lanes - 1 - Math.max(0, Math.min(lanes - 1, Math.floor((my - laneTop) / laneH)));
        drag.newLane = ed.tracks[li];
        drag.c.start = drag.newStart;               // 本地预览
        if (drag.newLane) drag.c.track = drag.newLane;
        edTlRender();
      } else if (drag.kind === "trim-l") {
        const ns = Math.max(0, drag.start0 + dx);
        const minDur = 0.2;
        const lim = drag.start0 + drag.dur0 - minDur;
        drag.newStart = Math.min(ns, lim);
        drag.newDeltaSS = drag.newStart - drag.start0;
        drag.c.start = drag.newStart; drag.c.duration = drag.dur0 - drag.newDeltaSS;
        edTlRender();
      } else if (drag.kind === "trim-r") {
        const nd = Math.max(0.2, drag.dur0 + dx);
        drag.newDur = nd; drag.c.duration = nd;
        edTlRender();
      }
    });
    const finish = async () => {
      if (!drag) return;
      const d = drag; drag = null;
      cv.classList.remove("grabbing");
      if (d.kind === "playhead" || !d.moved) { if (d.kind !== "playhead" && d.c) { edTlScrub(); } return; }
      try {
        if (d.kind === "move") {
          const mv = { clipId: d.c.id, trackId: d.c.track, start: Math.round(d.newStart * 100) / 100 };
          await edApi("clips_move", { path: ed.project, moves: [mv] });
        } else if (d.kind === "trim-l") {
          await edApi("clip_trim", { path: ed.project, clipId: d.c.id, edge: "start", delta: Math.round(d.newDeltaSS * 100) / 100, ripple: false });
        } else if (d.kind === "trim-r") {
          const dd = d.newDur - d.dur0;
          await edApi("clip_trim", { path: ed.project, clipId: d.c.id, edge: "end", delta: Math.round(dd * 100) / 100, ripple: false });
        }
        await edTimeline();
        toast("时间轴已更新");
      } catch (err) { toast(err.message.slice(0, 160)); await edTimeline(); }
    };
    cv.addEventListener("pointerup", finish);
    cv.addEventListener("pointercancel", finish);
    // 缩放
    const z = $("#edTlZoom");
    if (z) z.addEventListener("input", () => { ed.tlPps = +z.value; edTlRender(); });
    const zi = $("#edTlZoomIn"), zo = $("#edTlZoomOut");
    if (zi) zi.addEventListener("click", () => { z.value = Math.min(240, +z.value + 30); z.dispatchEvent(new Event("input")); });
    if (zo) zo.addEventListener("click", () => { z.value = Math.max(20, +z.value - 30); z.dispatchEvent(new Event("input")); });
    window.addEventListener("resize", () => edTlRender());
  });
})();
let edTlScrubTimer = null;
function edTlScrub() {   // 节流预览帧
  if (edTlScrubTimer) return;
  edTlScrubTimer = setTimeout(async () => {
    edTlScrubTimer = null;
    if (!ed.project) return;
    try {
      const d = await edApi("preview_frame", { path: ed.project, time: Math.max(0, +ed.tlPlayhead.toFixed(2)) }, 30000);
      const img = (d.images || [])[0];
      if (img) {
        $("#edPreview").src = "data:" + img.mime + ";base64," + img.data;
        $("#edPreview").classList.remove("hidden");
        $("#edPreviewEmpty").classList.add("hidden");
      }
    } catch (e) {}
  }, 450);
}
function edClipByIdx(i) { return ed.clips[i]; }
async function edSaveAndRefresh() { await edTimeline(); }

async function edCreateFromSelection() {
  if (!state.selected.size) { toast("先到素材库点「选择」勾选视频"); return; }
  const items = [];
  for (const id of state.selected) {
    try { const it = await api("/api/items/" + id); if (it.kind === "video") items.push(it); } catch (e) {}
  }
  if (!items.length) { toast("勾选中没有视频"); return; }
  toast("创建 Concat 项目…");
  const name = "wb-" + Date.now();
  const cd = await edApi("project_create", { name });
  const cp = JSON.parse(cd.text || "{}").project || {};
  if (cp.width) ed.projInfo = { w: cp.width, h: cp.height, fps: cp.fps };
  ed.project = ed.projectsDir + "/" + name;
  localStorage.setItem("edProject", ed.project);
  $("#edProject").textContent = name;
  const mids = [];
  for (const it of items) {
    const d = await edApi("media_import", { path: ed.project, file: it.path });
    mids.push(JSON.parse(d.text).createdId);
  }
  await edApi("clips_add_sequence", { path: ed.project, mediaIds: mids, start: 0 });
  await edTimeline();
  edLoadProjSel();
  toast(`已创建：${items.length} 段已上时间轴`);
}
async function edPreview() {
  if (!ed.project) return;
  const time = parseFloat($("#edPreviewTime").value || 0);
  const d = await edApi("preview_frame", { path: ed.project, time: Math.max(0, time) });
  const img = (d.images || [])[0];
  if (img) {
    $("#edPreview").src = "data:" + img.mime + ";base64," + img.data;
    $("#edPreview").classList.remove("hidden");
    $("#edPreviewEmpty").classList.add("hidden");
  }
}
$("#edPreviewBtn").addEventListener("click", edPreview);
$("#edNew").addEventListener("click", edCreateFromSelection);

/* —— 标题 / 字幕 / 撤销重做 / 特效层 / 导出（v2 补齐 v1 丢失的处理器）—— */
$("#edTitleAdd").addEventListener("click", async () => {
  if (!ed.project) { toast("先创建项目"); return; }
  const txt = $("#edTitleText").value.trim();
  if (!txt) { toast("先填标题文字"); return; }
  const start = Math.max(0, parseFloat($("#edTitleStart").value || 0));
  const dur = Math.max(0.5, parseFloat($("#edTitleDur").value || 3));
  try {
    await edApi("title_add", { path: ed.project, start, duration: dur, style: { content: txt } });
    $("#edTitleText").value = "";
    await edTimeline(); toast("标题已加（详情里可改字号颜色位置）");
  } catch (err) { toast(err.message.slice(0, 160)); }
});
$("#edCapAdd").addEventListener("click", async () => {
  if (!ed.project) { toast("先创建项目"); return; }
  const caps = [];
  $("#edCapText").value.trim().split("\n").forEach(line => {
    const m = line.trim().match(/^([\d.]+)\s*[-~到]\s*([\d.]+)\s+(.+)$/);
    if (m) caps.push({ start: parseFloat(m[1]), end: parseFloat(m[2]), text: m[3].trim() });
  });
  if (!caps.length) { toast("没有可解析的字幕行（格式：1-3.5 你好）"); return; }
  try {
    await edApi("captions_add", { path: ed.project, captions: caps });
    $("#edCapText").value = "";
    await edTimeline(); toast(`已加 ${caps.length} 条字幕`);
  } catch (err) { toast(err.message.slice(0, 160)); }
});
$("#edUndo").addEventListener("click", async () => {
  if (!ed.project) return;
  try { await edApi("edit_undo", { path: ed.project }); await edTimeline(); } catch (err) { toast(err.message.slice(0, 140)); }
});
$("#edRedo").addEventListener("click", async () => {
  if (!ed.project) return;
  try { await edApi("edit_redo", { path: ed.project }); await edTimeline(); } catch (err) { toast(err.message.slice(0, 140)); }
});
function edFillFxSel() {
  const sel = $("#edFxSel");
  if (!sel || !ed.effects) return;
  const list = (ed.effects || []).filter(x => x.kind === "effect");
  sel.innerHTML = list.map(x => `<option value="${x.id}">${x.name}</option>`).join("") || "<option value=''>（无可用特效）</option>";
}
$("#edFxLayerAdd").addEventListener("click", async () => {
  if (!ed.project) { toast("先创建项目"); return; }
  const eid = $("#edFxSel").value;
  if (!eid) { toast("无可用特效"); return; }
  const start = Math.max(0, parseFloat($("#edFxStart").value || 0));
  const dur = Math.max(0.5, parseFloat($("#edFxDur").value || 4));
  try {
    await edApi("effect_layer_add", { path: ed.project, start, effectId: eid, name: eid, duration: dur });
    await edTimeline(); toast(`特效层已加（${eid}，详情里可调参数）`);
  } catch (err) { toast(err.message.slice(0, 160)); }
});
async function edLoadAudioSel() {
  const sel = $("#edAudioSel");
  if (!sel) return;
  try {
    const d = await (await fetch("/api/items?kind=audio&per_page=200")).json();
    const items = d.items || d || [];
    sel.innerHTML = items.map(it => `<option value="${escAttr(it.path)}">${escHtml(it.rel_path.split("/").pop())}</option>`).join("")
      || "<option value=''>（库中无音频，可先导入）</option>";
  } catch (e) { sel.innerHTML = "<option value=''>（加载失败）</option>"; }
}
$("#edAudioAdd").addEventListener("click", async () => {
  if (!ed.project) { toast("先创建项目"); return; }
  const path = $("#edAudioSel").value;
  if (!path) { toast("库中没有音频素材"); return; }
  const start = Math.max(0, parseFloat($("#edAudioStart").value || 0));
  const vol = Math.min(2, Math.max(0, parseFloat($("#edAudioVol").value || 0.6)));
  try {
    const d = await edApi("media_import", { path: ed.project, file: path });
    const m = JSON.parse(d.text);
    const mid = (m.media && m.media[0] && m.media[0].id) || m.createdId;
    // 音轨：用最下方的轨道；只有一个轨时先加一条
    const t = await edApi("timeline_read", { path: ed.project });
    const tl = JSON.parse(t.text).timeline;
    const tracks = (tl.tracks || []).map(x => x.id);
    let target = tracks[tracks.length - 1];
    if (tracks.length <= 1) {
      await edApi("track_add", { path: ed.project, bottom: true });
      const t2 = await edApi("timeline_read", { path: ed.project });
      const tl2 = JSON.parse(t2.text).timeline;
      target = (tl2.tracks || []).map(x => x.id).pop();
    }
    const ca = await edApi("clip_add", { path: ed.project, mediaId: mid, trackId: target, start });
    const ac = JSON.parse(ca.text);
    const aclips = (ac.timeline && ac.timeline.clips || []).filter(x => x.kind === "audio");
    if (aclips.length && vol !== 1) {
      await edApi("clip_update", { path: ed.project, clipId: aclips[aclips.length - 1].id, patch: { volume: vol } });
    }
    await edTimeline(); toast(`BGM 已加到 ${target}（0.2.9 引擎已支持音频持久化）`);
  } catch (err) { toast(err.message.slice(0, 160)); }
});
$("#edVideoSet").addEventListener("click", async () => {
  if (!ed.project) { toast("先创建项目"); return; }
  const v = $("#edVideoPreset").value;               // WxHxFPSxDEN
  const [w, h, num, den] = v.split("x").map(Number);
  try {
    await edApi("project_set_video", { path: ed.project, video: { width: w, height: h, rateNum: num, rateDen: den } });
    await edTimeline(); toast(`画幅已设 ${w}×${h} @${num}fps（已有片段自动适配）`);
  } catch (err) { toast(err.message.slice(0, 160)); }
});
$("#edExportCancel").addEventListener("click", async () => {
  if (!ed.exportJob) { toast("没有进行中的导出"); return; }
  try {
    await edApi("export_cancel", { job: ed.exportJob });
    toast("已请求取消");
  } catch (err) { toast(err.message.slice(0, 160)); }
});
$("#edExport").addEventListener("click", async () => {
  if (!ed.project) { toast("先创建项目"); return; }
  if (ed.exporting) { toast("已在导出中"); return; }
  const w = $("#edRes").value;
  const dims = { "1920": [1920, 1080], "1280": [1280, 720], "3840": [3840, 2160] }[w] || [1920, 1080];
  const name = "wb-" + Date.now() + ".mp4";
  ed.exporting = true;
  ed.exportJob = null;
  $("#edExportRow").classList.remove("hidden");
  $("#edDl").classList.add("hidden");
  $("#edExportCancel").style.display = "";
  $("#edExportState").textContent = "导出中…";
  try {
    await edApi("project_save", { path: ed.project });
    const d = await edApi("export_start", { path: ed.project, output: name, width: dims[0], height: dims[1] });
    const job = JSON.parse(d.text).job;
    ed.exportJob = job;
    const t0 = Date.now();
    while (Date.now() - t0 < 15 * 60 * 1000) {
      await new Promise(r => setTimeout(r, 2000));
      const s = await edApi("export_status", { job });
      const st = JSON.parse(s.text);
      $("#edExportState").textContent = `导出中 ${(st.fraction * 100).toFixed(0)}%`;
      if (st.state === "done") {
        $("#edExportState").textContent = "导出完成";
        $("#edDl").href = "/api/editor/export_file?name=" + encodeURIComponent(name);
        $("#edDl").classList.remove("hidden");
        ed.exporting = false;
        ed.exportJob = null;
        $("#edExportCancel").style.display = "none";
        toast("成片已就绪，点「下载成片」");
        return;
      }
      if (st.state === "failed" || st.state === "cancelled") {
        ed.exportJob = null;
        $("#edExportCancel").style.display = "none";
        throw new Error("导出" + (st.state === "failed" ? "失败" : "已取消"));
      }
    }
    throw new Error("导出超时（15 分钟）");
  } catch (err) {
    $("#edExportState").textContent = err.message.slice(0, 120);
    ed.exporting = false;
    ed.exportJob = null;
    $("#edExportCancel").style.display = "none";
  }
});

$("#edClips").addEventListener("click", async e => {
  const act = e.target.closest("[data-edact]");
  const apply = e.target.closest("[data-edapply]");
  const box = e.target.closest(".concat-item") || (apply && apply.closest(".ed-detail").previousElementSibling);
  let idx = act ? +act.dataset.idx : (apply ? +apply.dataset.idx : -1);
  let c = ed.clips[idx];
  const row = () => { const det = apply && apply.closest(".ed-detail"); if (det) { const cid = det.dataset.detail; return ed.clips.findIndex(x => x.id === cid); } return idx; };
  const detailBox = apply ? apply.closest(".ed-detail") : null;
  const readDetail = (sel) => { const el = detailBox.querySelector(sel); return el; };
  try {
    if (act && act.dataset.edact === "detail") {
      const cid = c.id;
      if (ed.expanded.has(cid)) ed.expanded.delete(cid); else ed.expanded.add(cid);
      edRender(); return;
    }
    if (act && act.dataset.edact === "del") {
      await edApi("clips_remove", { path: ed.project, clipIds: [c.id], ripple: true });
      await edTimeline(); toast("已删除"); return;
    }
    if (act && act.dataset.edact === "split") {
      const t = parseFloat(($("#edSplitTime") || {}).value || 0);
      await edApi("clips_split", { path: ed.project, clipIds: [c.id], time: Math.max(0.1, t) });
      await edTimeline(); toast("已分割"); return;
    }
    if (act && (act.dataset.edact === "up" || act.dataset.edact === "down")) {
      const same = ed.clips.filter(x => x.track === c.track).sort((a, b) => +a.start - +b.start);
      const si = same.findIndex(x => x.id === c.id);
      const other = act.dataset.edact === "up" ? same[si - 1] : same[si + 1];
      if (!other) { toast("已到边缘"); return; }
      await edApi("clips_move", { path: ed.project, moves: [
        { clipId: c.id, trackId: c.track, start: +other.start },
        { clipId: other.id, trackId: other.track, start: +c.start } ] });
      await edTimeline(); toast("已交换位置"); return;
    }
    if (act && act.dataset.edact === "fit" || act && act.dataset.edact === "fill") {
      await edApi("clip_frame", { path: ed.project, clipId: c.id, framing: act.dataset.edact });
      await edTimeline(); toast("已应用画幅"); return;
    }
    if (act && act.dataset.edact === "keyset") {
      const det = act.closest(".ed-detail");
      const prop = det.querySelector("[data-edkf=prop]").value;
      const at = parseFloat(det.querySelector("[data-edkf=at]").value || 0);
      const value = parseFloat(det.querySelector("[data-edkf=value]").value || 0);
      await edApi("keyframe_set", { path: ed.project, clipId: c.id, property: prop, at, value });
      await edTimeline(); toast("关键帧已设"); return;
    }
    if (act && act.dataset.edact === "keyclear") {
      const det = act.closest(".ed-detail");
      const prop = det.querySelector("[data-edkf=prop]").value;
      const at = parseFloat(det.querySelector("[data-edkf=at]").value || 0);
      await edApi("keyframe_clear", { path: ed.project, clipId: c.id, property: prop, at });
      await edTimeline(); toast("关键帧已清"); return;
    }
    if (!apply) return;
    const kindApply = apply.dataset.edapply;
    const det = apply.closest(".ed-detail");
    const num = sel => { const el = det.querySelector(sel); return el ? parseFloat(el.value) || 0 : 0; };
    if (kindApply === "base") {
      const patch = {};
      const sv = (sel) => { const el = det.querySelector(sel); return el ? parseFloat(el.value) : null; };
      // 掐头（sourceStart）与时长：通过 clip_trim 相对当前值
      const newSS = sv("[data-edp=sourceStart]");
      const newDur = sv("[data-edp=duration]");
      if (newSS != null && Math.abs(newSS - (+c.sourceStart || 0)) > 0.001)
        await edApi("clip_trim", { path: ed.project, clipId: c.id, edge: "start", delta: newSS - (+c.sourceStart || 0), ripple: false });
      if (newDur != null && Math.abs(newDur - (+c.duration)) > 0.001)
        await edApi("clip_trim", { path: ed.project, clipId: c.id, edge: "end", delta: newDur - (+c.duration), ripple: false });
      const vol = sv("[data-edp=volume]"); if (vol != null) patch.volume = Math.max(0, vol);
      const fi = sv("[data-edp=fadeIn]"); if (fi != null) patch.fadeIn = Math.max(0, fi);
      const fo = sv("[data-edp=fadeOut]"); if (fo != null) patch.fadeOut = Math.max(0, fo);
      const mu = det.querySelector("[data-edp=muted]"); if (mu) patch.muted = mu.checked;
      const sp = sv("[data-edp=speed]"); if (sp != null && Math.abs(sp - (c.speed || 1)) > 0.001)
        await edApi("clip_speed", { path: ed.project, clipId: c.id, speed: Math.max(0.0625, Math.min(16, sp)) });
      if (Object.keys(patch).length) await edApi("clip_update", { path: ed.project, clipId: c.id, patch });
      await edTimeline(); toast("基础属性已应用"); return;
    }
    if (kindApply === "transform") {
      const g = sel => { const el = det.querySelector(sel); return el ? parseFloat(el.value) : null; };
      const patch = {};
      const sc = g("[data-edt=scale]"); if (sc != null) patch.scale = sc;
      const ox = g("[data-edt=offsetX]"); if (ox != null) patch.offsetX = ox;
      const oy = g("[data-edt=offsetY]"); if (oy != null) patch.offsetY = oy;
      const ro = g("[data-edt=rotation]"); if (ro != null) patch.rotation = ro;
      const op = g("[data-edt=opacity]"); if (op != null) patch.opacity = op / 100;
      const fh = det.querySelector("[data-edt=flipH]"); if (fh) patch.flipH = fh.checked;
      const fv = det.querySelector("[data-edt=flipV]"); if (fv) patch.flipV = fv.checked;
      await edApi("clip_update", { path: ed.project, clipId: c.id, patch });
      await edTimeline(); toast("变换已应用"); return;
    }
    if (kindApply === "crop") {
      const g = sel => parseFloat((det.querySelector(sel) || {}).value || 0);
      await edApi("clip_update", { path: ed.project, clipId: c.id,
        patch: { crop: { left: g("[data-edc=left]"), top: g("[data-edc=top]"), right: g("[data-edc=right]"), bottom: g("[data-edc=bottom]") } } });
      await edTimeline(); toast("裁剪已应用"); return;
    }
    if (kindApply === "cropnull") {
      await edApi("clip_update", { path: ed.project, clipId: c.id, patch: { crop: null } });
      await edTimeline(); toast("裁剪已取消"); return;
    }
    if (kindApply === "mask") {
      const g = sel => { const el = det.querySelector(sel); return el ? parseFloat(el.value) : 0; };
      const shape = det.querySelector("[data-edm=shape]").value;
      const inv = det.querySelector("[data-edm=invert]");
      await edApi("clip_mask", { path: ed.project, clipId: c.id, mask: {
        shape, x: g("[data-edm=x]"), y: g("[data-edm=y]"),
        width: g("[data-edm=width]"), height: g("[data-edm=height]"),
        rotation: g("[data-edm=rotation]"), roundness: g("[data-edm=roundness]"),
        feather: g("[data-edm=feather]"), invert: inv ? inv.checked : false } });
      await edTimeline(); toast("遮罩已应用"); return;
    }
    if (kindApply === "masknull") {
      await edApi("clip_mask", { path: ed.project, clipId: c.id, mask: null });
      await edTimeline(); toast("遮罩已移除"); return;
    }
    if (kindApply === "stab") {
      const sm = parseFloat((det.querySelector("[data-edsb=smoothness]") || {}).value || 0.5);
      const kr = det.querySelector("[data-edsb=keepRotation]");
      await edApi("clip_stabilize", { path: ed.project, clipId: c.id, stabilize: { smoothness: sm, keepRotation: kr ? kr.checked : true } });
      await edTimeline(); toast("防抖已开启（首次分析需等待）"); return;
    }
    if (kindApply === "stabnull") {
      await edApi("clip_stabilize", { path: ed.project, clipId: c.id, stabilize: null });
      await edTimeline(); toast("防抖已关闭"); return;
    }
    if (kindApply === "fxadd") {
      const pick = det.querySelector("[data-edfx=pick]").value;
      const cat = (ed.effects || []).find(x => x.id === pick) || { params: [] };
      const params = {};
      (cat.params || []).forEach(pp => { if (pp.type === "float" || pp.type === "int" || pp.type === "color") params[pp.key] = pp.default; });
      const chain = (c.videoEffects || []).slice();
      chain.push({ id: pick, params, enabled: true });
      await edApi("clip_update", { path: ed.project, clipId: c.id, patch: { videoEffects: chain } });
      await edTimeline(); toast(`已加特效 ${pick}`); return;
    }
    if (kindApply === "fxdel") {
      const fi = +apply.dataset.fi;
      const chain = (c.videoEffects || []).slice();
      chain.splice(fi, 1);
      await edApi("clip_update", { path: ed.project, clipId: c.id, patch: { videoEffects: chain } });
      await edTimeline(); toast("特效已移除"); return;
    }
    if (kindApply === "bg") {
      const kind = det.querySelector("[data-edb=bgkind]").value;
      let bg = { kind: "none" };
      if (kind === "blur") bg = { kind: "blur", amount: parseFloat((det.querySelector("[data-edb=bgamount]") || {}).value || 0.5) };
      if (kind === "color") bg = { kind: "color", color: (det.querySelector("[data-edb=bgcolor]") || {}).value || "#000000" };
      await edApi("clip_update", { path: ed.project, clipId: c.id, patch: { background: bg } });
      await edTimeline(); toast("片段背景已应用"); return;
    }
    if (kindApply === "trans") {
      const tid = (det.querySelector("[data-edb=transid]") || {}).value.trim();
      const td = parseFloat((det.querySelector("[data-edb=transdur]") || {}).value || 1);
      if (!tid) { await edApi("clip_update", { path: ed.project, clipId: c.id, patch: { transitionIn: null } }); }
      else await edApi("clip_update", { path: ed.project, clipId: c.id, patch: { transitionIn: { id: tid, duration: td } } });
      await edTimeline(); toast("入场转场已应用"); return;
    }
    if (kindApply === "text") {
      const g = sel => { const el = det.querySelector(sel); return el ? el.value : null; };
      const patchText = {};
      const content = g("[data-edtx=content]");
      if (content != null && content.trim()) patchText.content = content.trim();
      const fs = parseFloat((det.querySelector("[data-edtx=fontSize]") || {}).value || 0);
      if (fs > 0) patchText.fontSize = fs;
      const col = g("[data-edtx=color]");
      if (col && /^#[0-9a-fA-F]{3,8}$/.test(col.trim())) patchText.color = col.trim();
      const al = g("[data-edtx=align]"); if (al) patchText.align = al;
      const sw = parseFloat((det.querySelector("[data-edtx=strokeWidth]") || {}).value || 0);
      if (sw >= 0) patchText.strokeWidth = sw;
      const sh = det.querySelector("[data-edtx=shadow]"); if (sh) patchText.shadow = sh.checked;
      await edApi("clip_update", { path: ed.project, clipId: c.id, patch: { text: patchText } });
      await edTimeline(); toast("文字已应用"); return;
    }
    if (kindApply === "movetext") {
      const start = parseFloat((det.querySelector("[data-edmv=start]") || {}).value || 0);
      const dur = parseFloat((det.querySelector("[data-edmv=dur]") || {}).value || 0);
      const oyEl = det.querySelector("[data-edmv=offsetY]");
      if (Math.abs(start - (+c.start)) > 0.001)
        await edApi("clips_move", { path: ed.project, moves: [{ clipId: c.id, trackId: c.track, start: Math.max(0, start) }] });
      if (dur > 0 && Math.abs(dur - (+c.duration)) > 0.001)
        await edApi("clip_trim", { path: ed.project, clipId: c.id, edge: "end", delta: dur - (+c.duration), ripple: false });
      if (oyEl) {
        const oy = parseFloat(oyEl.value || 0);
        if (Math.abs(oy - (c.offsetY || 0)) > 0.001)
          await edApi("clip_update", { path: ed.project, clipId: c.id, patch: { offsetY: oy } });
      }
      await edTimeline(); toast("时间/位置已应用"); return;
    }
    if (kindApply === "wordstyle") {
      const g = sel => { const el = det.querySelector(sel); return el ? el.value : null; };
      const patch = {};
      const col = g("[data-edws=color]");
      if (col && /^#[0-9a-fA-F]{6}$/.test(col.trim())) patch.color = col.trim();
      const fs = parseFloat(g("[data-edws=fontScale]"));
      if (fs >= 0.1 && fs <= 10 && fs !== 1) patch.fontScale = fs;
      const fw = parseFloat(g("[data-edws=fontWeight]"));
      if (fw >= 100 && fw <= 900) patch.fontWeight = fw;
      const st = Math.max(0, parseInt(g("[data-edws=start]") || 0, 10));
      const en = Math.max(st + 1, parseInt(g("[data-edws=end]") || 1, 10));
      if (!Object.keys(patch).length) { toast("没有可应用的词样式（颜色/字号/字重至少一项有效）"); return; }
      await edApi("title_style_words", { path: ed.project, clipId: c.id, start: st, end: en, patch });
      await edTimeline(); toast(`词样式已设（字符 ${st}..${en - 1}）`);
      return;
    }
    if (kindApply === "wordclear") {
      const st = Math.max(0, parseInt((det.querySelector("[data-edws=start]") || {}).value || 0, 10));
      const en = Math.max(st + 1, parseInt((det.querySelector("[data-edws=end]") || {}).value || 1, 10));
      await edApi("title_clear_word_styles", { path: ed.project, clipId: c.id, start: st, end: en });
      await edTimeline(); toast(`词样式已清除（字符 ${st}..${en - 1}）`);
      return;
    }
    if (kindApply === "layerparam") {
      const inputs = [...det.querySelectorAll("[data-edlp]")];
      for (const inp of inputs) {
        const key = inp.dataset.edlp, type = inp.dataset.lptype;
        const val = type === "color" ? inp.value.trim() : parseFloat(inp.value || 0);
        await edApi("effect_key_set", { path: ed.project, clipId: c.id, entry: 0, key, at: 0, value: val });
      }
      await edTimeline(); toast(`已设 ${inputs.length} 个参数`); return;
    }
  } catch (err) { toast(err.message.slice(0, 160)); await edTimeline(); }
});
$("#edClips").addEventListener("change", async e => {
  if (e.target.id === "edLinkage" && ed.project) {
    try {
      await edApi("timeline_linkage", { path: ed.project, timelineId: ed.timelineId, on: e.target.checked });
      ed.linkage = e.target.checked;
      toast("联动已" + (e.target.checked ? "开启" : "关闭"));
    } catch (err) { toast(err.message.slice(0, 160)); }
    return;
  }
  const fxEnable = e.target.closest("[data-edfxe]");
  if (fxEnable) {
    const row = fxEnable.closest("[data-fxrow]");
    const det = fxEnable.closest(".ed-detail");
    const cid = det.dataset.detail;
    const fi = +row.dataset.fxrow;
    const idx = [...det.parentElement.querySelectorAll(".concat-item")].length; // unused
    // 找到对应 clip：详情面板属于某个 clip
    const c = ed.clips.find(x => x.id === cid);
    if (!c) return;
    const chain = (c.videoEffects || []).slice();
    if (chain[fi]) chain[fi].enabled = fxEnable.checked;
    try {
      await edApi("clip_update", { path: ed.project, clipId: c.id, patch: { videoEffects: chain } });
      await edTimeline(); toast("已更新");
    } catch (err) { toast(err.message); }
  }
});
$("#edClips").addEventListener("click", async e => {
  const bgKind = e.target.closest("#edBgKind");
  const bgVal = e.target.closest("#edBgVal");
  if (bgKind || bgVal) {
    const val = document.getElementById("edBgVal");
    if (val) val.style.display = bgKind && bgKind.value === "color" ? "" : (bgVal ? "none" : "");
  }
  const applyBg = e.target.closest("#edBgApply");
  if (applyBg && ed.project) {
    const kind = (document.getElementById("edBgKind") || {}).value || "none";
    let bg = { kind: "none" };
    if (kind === "blur") bg = { kind: "blur", amount: parseFloat((document.getElementById("edBgVal") || {}).value || 0.5) };
    if (kind === "color") bg = { kind: "color", color: (document.getElementById("edBgVal") || {}).value || "#000000" };
    try {
      await edApi("timeline_background", { path: ed.project, timelineId: ed.timelineId, background: bg });
      await edTimeline(); toast("时间轴背景已应用");
    } catch (err) { toast(err.message.slice(0, 140)); }
  }
  const addTrack = e.target.closest("#edAddTrackTop,#edAddTrackBottom");
  if (addTrack) {
    try {
      await edApi("track_add", { path: ed.project, bottom: addTrack.id.endsWith("Bottom") });
      await edTimeline(); toast("轨道已加");
    } catch (err) { toast(err.message.slice(0, 140)); }
  }
});

/* ================= 剪辑（拼接台） ================= *//* ================= 剪辑（拼接台） ================= */
// 拼接列表：[{id, name, duration, thumb, start, end}]，顺序即拼接顺序
const concat = { segs: [], dragFrom: -1 };

function loadEditTab() {
  renderConcatPool();
}

function concatSeg(item) {
  return {
    id: item.id,
    name: item.rel_path ? item.rel_path.split("/").pop() : (item.file_name || ("片段 " + item.id)),
    duration: item.duration || 0,
    thumb: item.thumb_url || `/api/items/${item.id}/thumb`,
    start: 0, end: 0,
  };
}

function concatSummary() {
  const total = concat.segs.reduce((acc, s) =>
    acc + Math.max(0, (s.end || s.duration) - (s.start || 0)), 0);
  const modes = new Set(concat.segs.map(s => (s.start > 0 || (s.end && s.end < s.duration)) ? "trim" : "raw"));
  const mode = concat.segs.length > 1 && (modes.has("trim") || concat.segs.length > 1)
    ? "" : "";
  $("#concatSummary").textContent = concat.segs.length
    ? `${concat.segs.length} 段 · 约 ${total.toFixed(1)}s${concat.segs.length > 1 ? "" : "（单段可掐头去尾）"}`
    : "";
  $("#concatGo").disabled = !concat.segs.length;
}

function renderConcat() {
  const box = $("#concatList");
  if (!concat.segs.length) {
    box.innerHTML = '<div class="muted empty-tip">还没有片段。到素材库点「选择」勾选视频后点上方「加入勾选片段」，或在下方素材池点 + 加入。</div>';
    concatSummary();
    return;
  }
  box.innerHTML = concat.segs.map((s, i) => {
    const dur = s.duration || 0;
    const eff = Math.max(0, (s.end || dur) - (s.start || 0));
    return `<div class="concat-item" draggable="true" data-idx="${i}">
      <span class="concat-order">${i + 1}</span>
      <img class="concat-thumb" src="${s.thumb}" alt="">
      <span class="concat-name" title="${escHtml(s.name)}">${escHtml(s.name)}</span>
      <span class="concat-dur">原始 ${dur.toFixed(1)}s · 有效 ${eff.toFixed(1)}s</span>
      <span class="concat-trim">
        <label>掐头<input type="number" min="0" step="0.1" value="${s.start || ""}" data-trim="start" data-idx="${i}" placeholder="0"></label>
        <label>去尾<input type="number" min="0" step="0.1" value="${s.end || ""}" data-trim="end" data-idx="${i}" placeholder="${dur ? dur.toFixed(1) : "末尾"}"></label>
      </span>
      <span class="concat-ops">
        <button class="btn btn-sm" data-cmove="-1" data-idx="${i}" title="上移" ${i === 0 ? "disabled" : ""}>↑</button>
        <button class="btn btn-sm" data-cmove="1" data-idx="${i}" title="下移" ${i === concat.segs.length - 1 ? "disabled" : ""}>↓</button>
        <button class="btn btn-sm" data-cdel="${i}" title="移除">✕</button>
      </span>
    </div>`;
  }).join("");
  concatSummary();
}

function concatAdd(item) {
  if (concat.segs.some(s => s.id === item.id)) { toast("该片段已在拼接列表"); return; }
  if (item.kind !== "video") { toast("拼接只支持视频素材"); return; }
  concat.segs.push(concatSeg(item));
  renderConcat();
}

async function concatAddSelected() {
  if (!state.selected.size) { toast("先到素材库点「选择」勾选视频"); return; }
  let added = 0;
  for (const id of state.selected) {
    if (concat.segs.some(s => s.id === id)) continue;
    try {
      const it = await api("/api/items/" + id);
      if (it.kind === "video") { concat.segs.push(concatSeg(it)); added++; }
    } catch (e) { /* skip */ }
  }
  renderConcat();
  toast(added ? `已加入 ${added} 段（顺序=勾选顺序，可拖动调整）` : "勾选中没有可加入的视频");
}

function renderConcatPool() {
  const box = $("#concatPool");
  if (!box) return;
  const q = ($("#concatSearch").value || "").toLowerCase();
  const vids = state.items.filter(it => it.kind === "video" &&
    (!q || (it.rel_path || "").toLowerCase().includes(q) || (it.file_name || "").toLowerCase().includes(q)));
  if (!vids.length) { box.innerHTML = '<div class="muted">没有视频素材（或被筛选条件过滤）。</div>'; return; }
  box.innerHTML = vids.map(it => {
    const name = it.rel_path ? it.rel_path.split("/").pop() : (it.file_name || it.id);
    const inList = concat.segs.some(s => s.id === it.id);
    return `<div class="pool-item ${inList ? "inlist" : ""}">
      <img src="${it.thumb_url || `/api/items/${it.id}/thumb`}" alt="" loading="lazy">
      <span class="pool-name" title="${escHtml(name)}">${escHtml(name)}</span>
      <span class="pool-dur">${(it.duration || 0).toFixed(0)}s</span>
      <button class="btn btn-sm" data-cadd="${it.id}" ${inList ? "disabled" : ""}>${inList ? "已加" : "+"}</button>
    </div>`;
  }).join("");
}

$("#concatAddSel").addEventListener("click", concatAddSelected);
$("#concatSearch").addEventListener("input", renderConcatPool);
$("#concatGo").addEventListener("click", () => {
  if (!concat.segs.length) return;
  const trims = concat.segs
    .filter(s => s.start > 0 || (s.end && s.end < s.duration))
    .map(s => ({ id: s.id, start: s.start, end: s.end }));
  submitJob("concat", concat.segs.map(s => s.id), trims.length ? { trims } : {});
});

$("#concatList").addEventListener("click", e => {
  const del = e.target.closest("[data-cdel]");
  if (del) { concat.segs.splice(+del.dataset.cdel, 1); renderConcat(); return; }
  const mv = e.target.closest("[data-cmove]");
  if (mv) {
    const i = +mv.dataset.idx, d = +mv.dataset.cmove;
    const j = i + d;
    if (j >= 0 && j < concat.segs.length) {
      [concat.segs[i], concat.segs[j]] = [concat.segs[j], concat.segs[i]];
      renderConcat();
    }
  }
});
$("#concatList").addEventListener("input", e => {
  const t = e.target.closest("[data-trim]");
  if (!t) return;
  const seg = concat.segs[+t.dataset.idx];
  if (!seg) return;
  const v = parseFloat(t.value);
  if (t.dataset.trim === "start") seg.start = Math.max(0, v || 0);
  else seg.end = Math.max(0, v || 0);
  concatSummary();
});
// 拖动排序
$("#concatList").addEventListener("dragstart", e => {
  const row = e.target.closest(".concat-item");
  if (!row) return;
  concat.dragFrom = +row.dataset.idx;
  row.classList.add("dragging");
});
$("#concatList").addEventListener("dragover", e => {
  e.preventDefault();
  const row = e.target.closest(".concat-item");
  if (!row || concat.dragFrom < 0) return;
  const to = +row.dataset.idx;
  if (to === concat.dragFrom) return;
  const [m] = concat.segs.splice(concat.dragFrom, 1);
  concat.segs.splice(to, 0, m);
  concat.dragFrom = to;
  renderConcat();
});
$("#concatList").addEventListener("dragend", e => {
  concat.dragFrom = -1;
  document.querySelectorAll(".concat-item.dragging").forEach(el => el.classList.remove("dragging"));
});
// 素材池 + 加入
$("#concatPool").addEventListener("click", e => {
  const add = e.target.closest("[data-cadd]");
  if (!add) return;
  const it = state.items.find(x => x.id === +add.dataset.cadd);
  if (it) concatAdd(it);
});

$("#semanticExit").addEventListener("click", () => {
  $("#fSearch").value = "";
  $("#fSearch").dispatchEvent(new Event("input"));
});
$("#bulkToCut").addEventListener("click", () => {
  document.querySelector('.tab[data-tab="edit"]').click();
  if (state.selected.size) concatAddSelected();
  else toast("先到素材库点「选择」勾选视频，再点「加入拼接」");
});

/* ================= LUT 查看器（同屏对比 + 切换 + 右侧选 LUT） ================= */
const lv = { id: null, lut: "", split: 50, dragging: false, collection: [] };

function lvCollection() {
  // 有勾选 → 在勾选集合内切换；无勾选 → 全部照片
  const picked = lutWs.photos.filter(p => lutWs.selected.has(p.id)).map(p => p.id);
  return picked.length ? picked : lutWs.photos.map(p => p.id);
}

function lutPreviewUrl(id, orig) {
  return orig || !lv.lut
    ? (lutWs.photos.find(p => p.id === id)?.thumb_url || `/api/items/${id}/preview`)
    : `/api/items/${id}/preview?lut=${encodeURIComponent(lv.lut)}`;
}

function lvSetSplit(pct) {
  lv.split = Math.max(2, Math.min(98, pct));
  $("#lvOrigWrap").style.clipPath = `inset(0 ${100 - lv.split}% 0 0)`;
  $("#lvDivider").style.left = `${lv.split}%`;
}

function lvApply() {
  const it = lutWs.photos.find(p => p.id === lv.id);
  if (!it) return;
  $("#lvTitle").textContent = it.rel_path.split("/").pop();
  $("#lvImgOrig").src = it.thumb_url || `/api/items/${it.id}/preview`;
  $("#lvImgLut").src = lv.lut
    ? `/api/items/${lv.id}/preview?lut=${encodeURIComponent(lv.lut)}`
    : $("#lvImgOrig").src;
  $("#lvTagR").textContent = lv.lut ? lv.lut.replace(/\.cube$/i, "") : "无 LUT";
  $("#lvMeta").textContent = [
    it.width ? `${it.width}×${it.height}` : "",
    fmtSize(it.size),
    it.taken_at || "",
  ].filter(Boolean).join(" · ");
  $("#lvSelect").textContent = lutWs.selected.has(lv.id) ? "取消勾选" : "勾选";
  const col = lv.collection;
  const pos = col.indexOf(lv.id);
  $("#lvPos").textContent = col.length ? `${pos + 1} / ${col.length}` : "";
  $("#lvPrev").disabled = col.length < 2;
  $("#lvNext").disabled = col.length < 2;
}

function openLutViewer(id) {
  lv.id = id;
  lv.lut = lutWs.current || "";
  lv.collection = lvCollection();
  $("#lvImgLut").src = lv.lut
    ? `/api/items/${lv.id}/preview?lut=${encodeURIComponent(lv.lut)}`
    : (lutWs.photos.find(p => p.id === id)?.thumb_url || "");
  lvApply();
  lvSetSplit(50);
  renderLvLutList();
  $("#lutViewer").classList.remove("hidden");
}

function lvNav(delta) {
  if (!lv.collection.length) return;
  const pos = lv.collection.indexOf(lv.id);
  const next = lv.collection[(pos + delta + lv.collection.length) % lv.collection.length];
  lv.id = next;
  lvApply();
}

function renderLvLutList() {
  const box = $("#lvLutList");
  const items = [{ name: "", label: "原图（无 LUT）" }]
    .concat(lutWs.luts.map(l => ({ name: l.name, label: l.name.replace(/\.cube$/i, "") })));
  box.innerHTML = items.map(l => `
    <div class="lv-lut-item ${lv.lut === l.name ? "on" : ""} ${l.name ? "" : "lv-none"}" data-name="${escAttr(l.name)}">
      ${escHtml(l.label)}
    </div>`).join("");
  $$("#lvLutList .lv-lut-item").forEach(el => el.addEventListener("click", () => {
    lv.lut = el.dataset.name;
    lutWs.current = lv.lut;
    renderLutList();
    renderLutGrid();
    renderLvLutList();
    $("#lvImgLut").src = lv.lut
      ? `/api/items/${lv.id}/preview?lut=${encodeURIComponent(lv.lut)}`
      : (lutWs.photos.find(p => p.id === lv.id)?.thumb_url || $("#lvImgOrig").src);
    $("#lvTagR").textContent = lv.lut ? lv.lut.replace(/\.cube$/i, "") : "无 LUT";
  }));
}

$("#lvClose").addEventListener("click", () => $("#lutViewer").classList.add("hidden"));
$("#lutViewer").addEventListener("click", e => {
  if (e.target === $("#lutViewer")) $("#lutViewer").classList.add("hidden");
});
$("#lvPrev").addEventListener("click", () => lvNav(-1));
$("#lvNext").addEventListener("click", () => lvNav(1));
$("#lvSelect").addEventListener("click", () => {
  lutWs.selected.has(lv.id) ? lutWs.selected.delete(lv.id) : lutWs.selected.add(lv.id);
  $("#lvSelect").textContent = lutWs.selected.has(lv.id) ? "取消勾选" : "勾选";
  // 勾选集合变化 → 切换范围跟随（保持当前张可见）
  lv.collection = lvCollection();
  const pos = lv.collection.indexOf(lv.id);
  $("#lvPos").textContent = lv.collection.length ? `${pos + 1} / ${lv.collection.length}` : "";
  renderLutGrid();
});
$("#lvSendExport").addEventListener("click", () => {
  if (!lv.lut) { toast("先在右侧选择一款 LUT"); return; }
  submitJob("export", [lv.id], { opts: { lut: lv.lut, format: "jpg" } });
});

/* 键盘：←→ 切换（查看器打开且无其他弹窗时） */
document.addEventListener("keydown", e => {
  if (!$("#lutViewer") || $("#lutViewer").classList.contains("hidden")) return;
  if (!$("#simModal").classList.contains("hidden") || !$("#cmpModal").classList.contains("hidden")
      || !$("#exModal").classList.contains("hidden")) return;
  if (["INPUT", "TEXTAREA", "SELECT"].includes(e.target.tagName)) return;
  if (e.key === "ArrowLeft") { lvNav(-1); e.preventDefault(); }
  else if (e.key === "ArrowRight") { lvNav(1); e.preventDefault(); }
});

/* 分割线拖动 */
(function initLvDrag() {
  const wrap = $("#lvCompareWrap");
  if (!wrap) return;
  let dragging = false;
  const move = clientX => {
    const r = wrap.getBoundingClientRect();
    lvSetSplit(((clientX - r.left) / r.width) * 100);
  };
  wrap.addEventListener("mousedown", e => { dragging = true; move(e.clientX); e.preventDefault(); });
  window.addEventListener("mousemove", e => { if (dragging) move(e.clientX); });
  window.addEventListener("mouseup", () => { dragging = false; });
  wrap.addEventListener("touchstart", e => { dragging = true; move(e.touches[0].clientX); }, { passive: true });
  wrap.addEventListener("touchmove", e => { if (dragging) move(e.touches[0].clientX); }, { passive: true });
  wrap.addEventListener("touchend", () => { dragging = false; }, { passive: true });
})();




/* 色标筛选下拉（带计数） */
function fillColorSelect(counts) {
  const sel = $("#fColor");
  if (!sel) return;
  const cur = sel.value;
  const any = Object.values(counts).reduce((a, b) => a + b, 0);
  const label = { red: "红标", yellow: "黄标", green: "绿标", blue: "蓝标" };
  sel.innerHTML = `<option value="">全部色标</option>`
    + `<option value="any">有色标${any ? ` (${any})` : ""}</option>`
    + ["red", "yellow", "green", "blue"].map(c =>
        `<option value="${c}">${label[c]}${counts[c] ? ` (${counts[c]})` : ""}</option>`).join("");
  sel.value = cur;
  if (sel.value !== cur) sel.value = "";
}
