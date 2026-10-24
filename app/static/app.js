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
  if (!r.ok) {
    const msg = (j && j.error) || `请求失败 (${r.status})`;
    throw new Error(msg);
  }
  return j;
}

/* ================= Tabs ================= */
$$(".tab").forEach(b => b.addEventListener("click", () => {
  $$(".tab").forEach(x => x.classList.toggle("active", x === b));
  $$(".tabpane").forEach(p => p.classList.toggle("active", p.id === "tab-" + b.dataset.tab));
  if (b.dataset.tab === "settings") loadSettings();
  if (b.dataset.tab === "import") loadHealth();
}));

/* ================= 素材库 ================= */
const state = {
  page: 1, perPage: 60, total: 0, items: [],
  stats: null, health: null,
};

function filterQuery() {
  const p = new URLSearchParams();
  if ($("#fKind").value) p.set("kind", $("#fKind").value);
  if ($("#fDevice").value) p.set("device", $("#fDevice").value);
  if ($("#fDate").value) p.set("date", $("#fDate").value);
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
  const badges = [];
  badges.push(`<span class="badge kind-${it.kind}">${KIND_LABEL[it.kind] || it.kind}</span>`);
  if (it.duplicate_of) badges.push(`<span class="badge dup">重复</span>`);
  if (it.source === "import") badges.push(`<span class="badge">导入</span>`);
  const stars = it.rating > 0 ? `<span class="stars-chip">${"★".repeat(it.rating)}</span>` : "";
  const rater = it.kind !== "sidecar" ? `<div class="rater">${[1,2,3,4,5].map(n =>
    `<button data-rate="${n}" class="${n <= it.rating ? "on" : ""}" title="${n} 星">★</button>`).join("")}</div>` : "";
  const foot = [`<span class="name">${escHtml(it.rel_path.split("/").pop())}</span>`];
  if (it.thumb_status === "generating") foot.push("<span>生成中…</span>");
  if (it.proxy_status === "generating") foot.push("<span>转码中…</span>");
  if (it.thumb_status === "failed") foot.push(`<span title="${escAttr(it.thumb_note)}">缩略图失败</span>`);
  return `<div class="card ${it.rejected ? "rejected" : ""}" data-id="${it.id}">
    ${thumb}
    <div class="badges">${badges.join("")}</div>
    ${stars}
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
  renderPager();
}

function renderPager() {
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

async function loadItems() {
  try {
    const j = await api("/api/items?" + filterQuery().toString());
    state.items = j.items;
    state.total = j.total;
    renderGrid();
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
      const id = parseInt(card.dataset.id, 10);
      if (rateBtn) {
        ev.stopPropagation();
        setRate(id, parseInt(rateBtn.dataset.rate, 10));
        return;
      }
      const idx = state.items.findIndex(x => x.id === id);
      openLightbox(idx);
    });
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
  lb.open = true; lb.idx = idx;
  $("#lightbox").classList.remove("hidden");
  renderLightbox();
}
function closeLightbox() {
  lb.open = false;
  $("#lightbox").classList.add("hidden");
  destroyPano();
  $("#lbStage").innerHTML = "";
}
function navLightbox(delta) { openLightbox(lb.idx + delta); }

function renderLightbox() {
  const it = state.items[lb.idx];
  if (!it) return;
  $("#lbPrev").disabled = lb.idx <= 0;
  $("#lbNext").disabled = lb.idx >= state.items.length - 1;
  $("#lbTitle").textContent = it.rel_path;
  $("#lbDownload").href = `/api/items/${it.id}/file`;
  $("#lbReject").classList.toggle("on", !!it.rejected);
  $("#lbReject").textContent = it.rejected ? "已淘汰（点此恢复）" : "淘汰";
  $("#lbStars").innerHTML = [1, 2, 3, 4, 5].map(n =>
    `<button data-n="${n}" class="${n <= it.rating ? "on" : ""}">${n <= it.rating ? "★" : "☆"}</button>`).join("");
  $$("#lbStars button").forEach(b => b.addEventListener("click", () =>
    setRate(it.id, parseInt(b.dataset.n, 10) === it.rating ? 0 : parseInt(b.dataset.n, 10)).then(renderLightbox)));

  const meta = [];
  const add = (k, v, wide) => meta.push(`<div class="k">${k}</div><div class="${wide ? "wide" : ""}">${v}</div>`);
  add("设备", escHtml(it.device || "—"));
  add("类型", KIND_LABEL[it.kind] || it.kind);
  add("日期", escHtml(it.taken_at || "—"));
  if (it.width) add("分辨率", `${it.width}×${it.height}`);
  if (it.duration) add("时长", fmtDur(it.duration));
  if (it.codec) add("编码", escHtml(it.codec.toUpperCase()));
  add("体积", fmtSize(it.size));
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
$("#lbReject").addEventListener("click", () => {
  const it = state.items[lb.idx];
  if (it) setRejected(it.id, !it.rejected).then(renderLightbox);
});
document.addEventListener("keydown", e => {
  if (!lb.open) return;
  if (e.key === "Escape") closeLightbox();
  else if (e.key === "ArrowLeft") navLightbox(-1);
  else if (e.key === "ArrowRight") navLightbox(1);
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
    state.health = h;
    $("#topMeta").innerHTML =
      `<span>已入库 <b>${h.items}</b></span>` +
      `<span>队列 <b>${h.queues.hash + h.queues.thumb + h.queues.proxy}</b></span>` +
      `<span>上次扫描 <b>${fmtTime(h.last_scan)}</b></span>`;
  } catch (e) { /* 静默 */ }
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
["fKind", "fDevice", "fDate", "fRating", "fRejected", "fSort", "fDup"].forEach(id =>
  $("#" + id).addEventListener("change", () => { state.page = 1; loadItems(); }));
let searchTimer = null;
$("#fSearch").addEventListener("input", () => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => { state.page = 1; loadItems(); }, 350);
});
setInterval(() => { if (!document.hidden) loadHealth(); }, 5000);
loadHealth();
loadStats().then(loadItems).catch(() => loadItems());
