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
  if (b.dataset.tab === "edit") loadEditTab();
}));

/* ================= 素材库 ================= */
const state = {
  page: 1, perPage: 60, total: 0, items: [],
  stats: null, health: null,
  selectMode: false, selected: new Set(),
  view: "grid", tracksJobs: new Set(),
};

function filterQuery() {
  const p = new URLSearchParams();
  if ($("#fKind").value) p.set("kind", $("#fKind").value);
  if ($("#fDevice").value) p.set("device", $("#fDevice").value);
  if ($("#fDate").value) p.set("date", $("#fDate").value);
  if ($("#fAlbum").value) p.set("album", $("#fAlbum").value);
  if ($("#fQuality").value && $("#fQuality").value !== "0") p.set("min_quality", $("#fQuality").value);
  if ($("#fRating").value && $("#fRating").value !== "0") p.set("min_rating", $("#fRating").value);
  p.set("rejected", $("#fRejected").value);
  if ($("#fSearch").value.trim()) p.set("q", $("#fSearch").value.trim());
  p.set("sort", $("#fSort").value);
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
    state.stats.devices.map(d => `<option value="${escAttr(d.name)}">${escHtml(d.name)} (${d.count})</option>`).join("");
  devSel.value = cur;
  const dateSel = $("#fDate");
  const curD = dateSel.value;
  dateSel.innerHTML = '<option value="">全部日期</option>' +
    state.stats.dates.map(d => `<option value="${d}">${d}</option>`).join("");
  dateSel.value = curD;
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
  const busy = it.thumb_status === "generating" ? "处理中"
             : it.proxy_status === "generating" ? "转码中" : "";
  if (busy) badges.push(`<span class="badge busy">${busy}</span>`);
  const foot = [`<span class="name">${escHtml(it.rel_path.split("/").pop())}</span>`];
  if (it.thumb_status === "failed") foot.push(`<span title="${escAttr(it.thumb_note)}">缩略图失败</span>`);
  return `<div class="card ${it.rejected ? "rejected" : ""} ${state.selected.has(it.id) ? "selected" : ""}" data-id="${it.id}">
    ${thumb}${sprite}${pick}
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
    const parts = b.dataset.filter.split(" ");
    $("#fDate").value = parts[0] || "";
    $("#fSearch").value = parts[1] ? `#${parts[0]} ${parts[1]}` : "";
    $("#fSearch").dispatchEvent(new Event("input"));
    toast("已跳回网格视图，可用搜索继续缩小范围");
    $("#btnViewTimeline").click();
  }));
  $("#emptyHint").classList.toggle("hidden", state.items.length > 0);
  $("#resultCount").textContent = `共 ${state.total} 项 · 第 ${state.page} 页（时间轴视图）`;
  $("#pager").innerHTML = "";
}

async function loadItems() {
  try {
    const j = await api("/api/items?" + filterQuery().toString());
    state.items = j.items;
    state.total = j.total;
    if (state.view === "timeline") renderTimeline();
    else renderGrid();
  } catch (e) { toast(e.message); }
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

async function setRate(id, rating) {
  try {
    await api(`/api/items/${id}/rate`, { method: "POST", body: JSON.stringify({ rating }) });
    const it = state.items.find(x => x.id === id);
    if (it) it.rating = rating;
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
    $("#cfgEditUrl").value = cfg.opencut_url || localStorage.getItem("opencutUrl") || "";
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
    opencut_url: $("#cfgEditUrl").value.trim(),
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
["fKind", "fDevice", "fDate", "fAlbum", "fQuality", "fRating", "fRejected", "fSort", "fDup"].forEach(id =>
  $("#" + id).addEventListener("change", () => { state.page = 1; loadItems(); }));
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
  searchTimer = setTimeout(() => { state.page = 1; loadItems(); }, 350);
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
    const kindLabel = j.kind === "export" ? "导出" : j.kind === "slideshow" ? "小片" : "AI打标";
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
    const j = await api("/api/items?kind=photo&per_page=200&sort=date_desc");
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
      <span class="pick">${lutWs.selected.has(it.id) ? "✓" : ""}</span>
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
  $$("#lutGrid .card").forEach(c => c.addEventListener("click", () => {
    const id = parseInt(c.dataset.id, 10);
    lutWs.selected.has(id) ? lutWs.selected.delete(id) : lutWs.selected.add(id);
    $("#lutSelN").textContent = lutWs.selected.size;
    $("#lutExport").disabled = !lutWs.current || !lutWs.selected.size;
    c.classList.toggle("selected", lutWs.selected.has(id));
    c.querySelector(".pick").textContent = lutWs.selected.has(id) ? "✓" : "";
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


/* ================= 剪辑（OpenCut 嵌入） ================= */
function editUrl() {
  return (state.health && state.health.opencut_url) ||
    localStorage.getItem("opencutUrl") ||
    `${location.protocol}//${location.hostname}:3100`;
}
function loadEditTab() {
  const url = editUrl();
  $("#editUrl").value = url;
  const frame = $("#editFrame");
  if (!frame.src || frame.src === "about:blank") frame.src = url;
}
$("#editGo").addEventListener("click", () => {
  let u = $("#editUrl").value.trim();
  if (!u) { toast("请填写 OpenCut 地址"); return; }
  if (!/^https?:\/\//.test(u)) u = "http://" + u;
  localStorage.setItem("opencutUrl", u);
  $("#editFrame").src = u;
});
$("#editNewWin").addEventListener("click", () => {
  window.open($("#editUrl").value.trim() || editUrl(), "_blank");
});

/* 送剪辑：勾选素材批量下载到本地，再拖入 OpenCut */
$("#bulkSendCut").addEventListener("click", async () => {
  const ids = state.items.filter(i => state.selected.has(i.id) &&
    ["photo", "pano", "raw", "video"].includes(i.kind)).map(i => i.id);
  if (!ids.length) { toast("先勾选要送剪辑的素材"); return; }
  if (!confirm(`将逐个下载 ${ids.length} 个原文件到浏览器下载目录（大文件请耐心等待），\n完成后在 OpenCut 里把下载的文件拖入时间线。继续？`)) return;
  toast(`开始下载 ${ids.length} 个文件…`);
  for (const id of ids) {
    const a = document.createElement("a");
    a.href = `/api/items/${id}/file`;
    a.download = "";
    document.body.appendChild(a);
    a.click();
    a.remove();
    await new Promise(r => setTimeout(r, 800));   // 间隔防浏览器拦截多下载
  }
  toast("下载完成，请在「剪辑」页把文件拖入 OpenCut 时间线");
});
