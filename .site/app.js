/* tpn/pdfs browsing site — no dependencies.
   Loads manifest.json (built by generate_manifest.py), then does all
   search/filter/sort/render client-side; ~1,900 records is small enough
   that we re-render the full result list on every change. */
"use strict";

const REPO = "weezymatt/pdfs";
const BRANCH = "master";
const $ = (sel) => document.querySelector(sel);

const state = {
  q: "",
  topics: new Set(),
  type: "",        // type name or ""
  y0: null, y1: null,
  sort: "new",
  view: "cards",
};

let M = null;          // manifest
let files = [];        // records, augmented
let filtered = [];     // current results
let selIdx = -1;       // keyboard selection into filtered
let yearMin = 0, yearMax = 0, yearCounts = {};

/* ---------- helpers ---------- */

const esc = (s) => s.replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const escRe = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
const encPath = (p) => p.split("/").map(encodeURIComponent).join("/");
const blobUrl = (r) => `https://github.com/${REPO}/blob/${BRANCH}/${encPath(r.p)}`;
const rawUrl = (r) => `https://raw.githubusercontent.com/${REPO}/${BRANCH}/${encPath(r.p)}`;
const arxivUrl = (r) => `https://arxiv.org/abs/${encodeURIComponent(r.i.replace(/v\d+$/, ""))}`;

function fmtSize(n) {
  if (n >= 1 << 30) return (n / (1 << 30)).toFixed(1) + " GB";
  if (n >= 1 << 20) return (n / (1 << 20)).toFixed(1) + " MB";
  return Math.max(1, Math.round(n / 1024)) + " KB";
}
const fmtCount = (n) => n.toLocaleString("en-US");

/* ---------- URL-hash state ---------- */

function readHash() {
  const h = new URLSearchParams(location.hash.slice(1));
  state.q = h.get("q") || "";
  state.topics = new Set((h.get("t") || "").split(",").filter(Boolean));
  state.type = h.get("k") || "";
  const y = (h.get("y") || "").match(/^(\d{4})-(\d{4})$/);
  state.y0 = y ? +y[1] : null;
  state.y1 = y ? +y[2] : null;
  state.sort = h.get("s") || "new";
  state.view = h.get("v") === "list" ? "list" : "cards";
}

function writeHash() {
  const h = new URLSearchParams();
  if (state.q) h.set("q", state.q);
  if (state.topics.size) h.set("t", [...state.topics].join(","));
  if (state.type) h.set("k", state.type);
  if (state.y0 !== null) h.set("y", `${state.y0}-${state.y1}`);
  if (state.sort !== "new") h.set("s", state.sort);
  if (state.view !== "cards") h.set("v", state.view);
  const s = h.toString();
  history.replaceState(null, "", s ? "#" + s : location.pathname + location.search);
}

/* ---------- filtering & scoring ---------- */

function tokenize(q) {
  return q.toLowerCase().split(/\s+/).filter(Boolean);
}

function score(rec, tokens) {
  let total = 0;
  for (const tok of tokens) {
    const inTitle = rec.tl.indexOf(tok);
    if (inTitle === 0 || rec.tl.indexOf(" " + tok) !== -1) total += 3;
    else if (inTitle !== -1) total += 2;
    else if (rec.hay.indexOf(tok) !== -1) total += 1;
    else return -1;                      // AND semantics: every token must hit
  }
  return total;
}

function passesAllBut(rec, skip) {
  if (skip !== "type" && state.type && M.types[rec.k] !== state.type) return false;
  if (skip !== "year" && state.y0 !== null &&
      (rec.y === null || rec.y < state.y0 || rec.y > state.y1)) return false;
  if (skip !== "topics" && state.topics.size) {
    let hit = false;
    for (const c of rec.c) if (state.topics.has(M.topics[c])) { hit = true; break; }
    if (!hit) return false;
  }
  return true;
}

function applyFilters() {
  const tokens = tokenize(state.q);
  filtered = [];
  for (const rec of files) {
    if (!passesAllBut(rec, null)) continue;
    if (tokens.length) {
      rec.sc = score(rec, tokens);
      if (rec.sc < 0) continue;
    } else rec.sc = 0;
    filtered.push(rec);
  }
  const cmp = {
    new:   (a, b) => (b.y ?? -1) - (a.y ?? -1) || a.t.localeCompare(b.t),
    old:   (a, b) => (a.y ?? 9999) - (b.y ?? 9999) || a.t.localeCompare(b.t),
    title: (a, b) => a.t.localeCompare(b.t),
    size:  (a, b) => b.s - a.s,
  }[state.sort] || ((a, b) => 0);
  filtered.sort((a, b) => (b.sc - a.sc) || cmp(a, b));
  selIdx = -1;
}

/* ---------- rendering ---------- */

function highlight(text) {
  const tokens = tokenize(state.q);
  let html = esc(text);
  for (const tok of tokens) {
    const re = new RegExp(escRe(esc(tok)), "ig");
    html = html.replace(re, (m) => `\x01${m}\x02`);
  }
  return html.replace(/\x01/g, "<mark>").replace(/\x02/g, "</mark>");
}

function metaBits(r) {
  const bits = [];
  if (r.y !== null) bits.push(`<span class="year">${r.y}</span>`);
  bits.push(esc(M.types[r.k]));
  bits.push(fmtSize(r.s));
  return bits;
}

function actionsHtml(r) {
  let a = `<a href="${esc(rawUrl(r))}" title="Download" aria-label="Download">⤓</a>`;
  if (r.a) a += `<a href="${esc(arxivUrl(r))}" target="_blank" rel="noopener" title="arXiv abstract" aria-label="arXiv abstract">χ</a>`;
  return a;
}

function cardHtml(r, i) {
  const thumb = r.e === "pdf"
    ? `<img src="thumbs/${r.h}.webp" loading="lazy" decoding="async" alt="">
       <div class="ph" hidden>${esc(r.e)}</div>`
    : `<div class="ph">${esc(r.e)}</div>`;
  return `<article class="card" data-i="${i}">
    <div class="thumb">${thumb}</div>
    <a class="main" href="${esc(blobUrl(r))}" target="_blank" rel="noopener"
       aria-label="${esc(r.t)}"></a>
    <div class="card-actions">${actionsHtml(r)}</div>
    <div class="card-body">
      <div class="card-title" title="${esc(r.p)}">${highlight(r.t)}</div>
      <div class="card-meta">${metaBits(r).join(" · ")}</div>
    </div>
  </article>`;
}

function rowHtml(r, i) {
  const quals = r.q ? ` <span class="quals">— ${highlight(r.q)}</span>` : "";
  return `<div class="row" data-i="${i}">
    <span class="year">${r.y ?? "—"}</span>
    <span class="title"><a class="main" href="${esc(blobUrl(r))}" target="_blank"
      rel="noopener" aria-label="${esc(r.t)}"></a>${highlight(r.t)}${quals}</span>
    <span class="side">
      <span class="badge">${esc(M.types[r.k])}</span>
      <span>${fmtSize(r.s)}</span>
      ${actionsHtml(r)}
    </span>
  </div>`;
}

function renderResults() {
  const box = $("#results");
  box.className = state.view === "cards" ? "cards" : "list";
  const parts = [];
  for (let i = 0; i < filtered.length; i++) {
    parts.push(state.view === "cards" ? cardHtml(filtered[i], i) : rowHtml(filtered[i], i));
  }
  box.innerHTML = parts.join("");
  $("#empty").hidden = filtered.length > 0;

  const total = files.length;
  let line = filtered.length === total
    ? `${fmtCount(total)} documents`
    : `${fmtCount(filtered.length)} of ${fmtCount(total)} documents`;
  if (state.y0 !== null) {
    const undated = files.filter((r) => r.y === null && passesAllBut(r, "year")).length;
    if (undated) line += ` · ${fmtCount(undated)} undated hidden by year filter`;
  }
  $("#result-line").textContent = line;
}

function renderChips() {
  const counts = new Map();
  for (const rec of files) {
    if (!passesAllBut(rec, "topics")) continue;
    if (state.q && score(rec, tokenize(state.q)) < 0) continue;
    for (const c of rec.c) counts.set(c, (counts.get(c) || 0) + 1);
  }
  const order = M.topics
    .map((name, idx) => ({ name, idx, n: counts.get(idx) || 0 }))
    .sort((a, b) => (a.name === "misc") - (b.name === "misc") || b.n - a.n);
  $("#chips").innerHTML = order.map(({ name, n }) => {
    const on = state.topics.has(name);
    return `<button class="chip" data-topic="${esc(name)}" aria-pressed="${on}"
      ${n === 0 && !on ? "disabled" : ""}>${esc(name)}<small>${fmtCount(n)}</small></button>`;
  }).join("");
}

function renderHistogram() {
  const bars = [];
  const max = Math.max(...Object.values(yearCounts));
  for (let y = yearMin; y <= yearMax; y++) {
    const n = yearCounts[y] || 0;
    const h = n ? Math.max(2, Math.round((n / max) * 50)) : 1;
    const inRange = state.y0 !== null && y >= state.y0 && y <= state.y1;
    bars.push(`<div class="bar${inRange ? " in" : ""}" data-y="${y}" data-n="${n}"
      style="height:${h}px"></div>`);
  }
  $("#histogram").innerHTML = bars.join("");
  $("#year-readout").innerHTML = state.y0 === null ? "" :
    `${state.y0}–${state.y1} <button class="linklike" id="year-clear">clear</button>`;
}

function renderControls() {
  $("#search").value = state.q;
  $("#sort").value = state.sort;
  $("#type-filter").value = state.type;
  $("#view-cards").setAttribute("aria-pressed", state.view === "cards");
  $("#view-list").setAttribute("aria-pressed", state.view === "list");
}

function update({ hash = true } = {}) {
  applyFilters();
  renderChips();
  renderHistogram();
  renderResults();
  renderControls();
  if (hash) writeHash();
}

/* ---------- selection (j/k keyboard nav) ---------- */

function select(idx) {
  const prev = $("#results .sel");
  if (prev) prev.classList.remove("sel");
  selIdx = Math.max(0, Math.min(filtered.length - 1, idx));
  const el = $(`#results [data-i="${selIdx}"]`);
  if (el) {
    el.classList.add("sel");
    el.scrollIntoView({ block: "nearest" });
  }
}

/* ---------- events ---------- */

function initEvents() {
  let debounce = null;
  $("#search").addEventListener("input", (e) => {
    clearTimeout(debounce);
    debounce = setTimeout(() => { state.q = e.target.value.trim(); update(); }, 50);
  });

  $("#sort").addEventListener("change", (e) => { state.sort = e.target.value; update(); });
  $("#type-filter").addEventListener("change", (e) => { state.type = e.target.value; update(); });
  $("#view-cards").addEventListener("click", () => { state.view = "cards"; update(); });
  $("#view-list").addEventListener("click", () => { state.view = "list"; update(); });

  $("#chips").addEventListener("click", (e) => {
    const chip = e.target.closest(".chip");
    if (!chip) return;
    const t = chip.dataset.topic;
    state.topics.has(t) ? state.topics.delete(t) : state.topics.add(t);
    update();
  });

  $("#brand-link").addEventListener("click", (e) => {
    e.preventDefault();
    Object.assign(state, { q: "", type: "", y0: null, y1: null, sort: "new" });
    state.topics.clear();
    update();
  });
  $("#clear-all").addEventListener("click", () => {
    Object.assign(state, { q: "", type: "", y0: null, y1: null });
    state.topics.clear();
    update();
  });
  $("#year-readout").addEventListener("click", (e) => {
    if (e.target.id === "year-clear") { state.y0 = state.y1 = null; update(); }
  });

  $("#random").addEventListener("click", () => {
    if (!filtered.length) return;
    const r = filtered[Math.floor(Math.random() * filtered.length)];
    window.open(blobUrl(r), "_blank", "noopener");
  });

  /* histogram: hover tooltip + drag-to-select */
  const histo = $("#histogram");
  const tip = $("#tooltip");
  let dragFrom = null;
  const yearAt = (clientX) => {
    const rect = histo.getBoundingClientRect();
    const frac = Math.min(1, Math.max(0, (clientX - rect.left) / rect.width));
    return yearMin + Math.min(yearMax - yearMin,
      Math.floor(frac * (yearMax - yearMin + 1)));
  };
  histo.addEventListener("pointerdown", (e) => {
    dragFrom = yearAt(e.clientX);
    histo.setPointerCapture(e.pointerId);
    state.y0 = state.y1 = dragFrom;
    update({ hash: false });
  });
  histo.addEventListener("pointermove", (e) => {
    const y = yearAt(e.clientX);
    if (dragFrom !== null) {
      state.y0 = Math.min(dragFrom, y);
      state.y1 = Math.max(dragFrom, y);
      update({ hash: false });
    }
    const n = yearCounts[y] || 0;
    tip.textContent = `${y} · ${fmtCount(n)} doc${n === 1 ? "" : "s"}`;
    tip.hidden = false;
    tip.style.left = Math.min(window.innerWidth - 90, e.clientX + 12) + "px";
    tip.style.top = (e.clientY + 14) + "px";
  });
  histo.addEventListener("pointerup", () => { dragFrom = null; writeHash(); });
  histo.addEventListener("pointerleave", () => { tip.hidden = true; });

  /* thumbnails: fall back to placeholder on error; letterbox landscape pages */
  $("#results").addEventListener("error", (e) => {
    if (e.target.tagName === "IMG") {
      e.target.remove();
      const ph = e.target.closest?.(".thumb")?.querySelector(".ph");
      if (ph) ph.hidden = false;
    }
  }, true);
  $("#results").addEventListener("load", (e) => {
    if (e.target.tagName === "IMG" && e.target.naturalWidth > e.target.naturalHeight) {
      e.target.classList.add("wide");
    }
  }, true);

  /* theme */
  $("#theme-toggle").addEventListener("click", toggleTheme);

  /* keyboard */
  document.addEventListener("keydown", (e) => {
    const typing = /^(INPUT|SELECT|TEXTAREA)$/.test(document.activeElement.tagName);
    if (e.key === "Escape" && typing) {
      if ($("#search").value) { $("#search").value = ""; state.q = ""; update(); }
      else document.activeElement.blur();
      return;
    }
    if (typing || e.metaKey || e.ctrlKey || e.altKey) return;
    switch (e.key) {
      case "/": e.preventDefault(); $("#search").focus(); $("#search").select(); break;
      case "j": select(selIdx + 1); break;
      case "k": select(selIdx - 1); break;
      case "Enter":
        if (selIdx >= 0) window.open(blobUrl(filtered[selIdx]), "_blank", "noopener");
        break;
      case "o":
        if (selIdx >= 0) window.open(rawUrl(filtered[selIdx]), "_blank", "noopener");
        break;
      case "r": $("#random").click(); break;
      case "t": toggleTheme(); break;
      case "v": state.view = state.view === "cards" ? "list" : "cards"; update(); break;
    }
  });

  window.addEventListener("hashchange", () => { readHash(); update({ hash: false }); });
}

function toggleTheme() {
  const cur = document.documentElement.dataset.theme ||
    (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
  const next = cur === "dark" ? "light" : "dark";
  document.documentElement.dataset.theme = next;
  try { localStorage.setItem("theme", next); } catch {}
}

/* ---------- boot ---------- */

(function initTheme() {
  try {
    const saved = localStorage.getItem("theme");
    if (saved) document.documentElement.dataset.theme = saved;
  } catch {}
})();

fetch("manifest.json")
  .then((r) => { if (!r.ok) throw new Error(r.status); return r.json(); })
  .then((manifest) => {
    M = manifest;
    files = M.files;
    const years = [];
    for (const rec of files) {
      rec.tl = rec.t.toLowerCase();
      rec.hay = (rec.t + " " + rec.q + " " + (rec.i || "") + " " +
                 rec.c.map((c) => M.topics[c]).join(" ") + " " +
                 M.types[rec.k]).toLowerCase();
      if (rec.y !== null) {
        years.push(rec.y);
        yearCounts[rec.y] = (yearCounts[rec.y] || 0) + 1;
      }
    }
    yearMin = Math.min(...years);
    yearMax = Math.max(...years);

    const pdfs = files.filter((r) => r.e === "pdf").length;
    $("#stats").textContent =
      `${fmtCount(pdfs)} PDFs · ${(M.bytes / 1e9).toFixed(1)} GB · ${yearMin}–${yearMax}`;

    const typeCounts = M.types.map((_, k) => files.filter((r) => r.k === k).length);
    $("#type-filter").innerHTML = `<option value="">all types</option>` +
      M.types.map((t, k) => typeCounts[k]
        ? `<option value="${esc(t)}">${esc(t)} (${fmtCount(typeCounts[k])})</option>` : "")
        .join("");

    const genDate = (M.generated || "").slice(0, 10);
    $("#footer-meta").innerHTML =
      `${fmtCount(M.count)} documents indexed from ` +
      `<a href="https://github.com/${REPO}">github.com/${REPO}</a> @ ` +
      `<a href="https://github.com/${REPO}/commit/${esc(M.commit)}">` +
      `${esc(M.commit.slice(0, 10))}</a> · rebuilt ${esc(genDate)}`;

    readHash();
    initEvents();
    update({ hash: false });
  })
  .catch((err) => {
    $("#stats").textContent = "failed to load manifest — " + err;
  });
