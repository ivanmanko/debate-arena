/* Rational Check — vanilla JS client. Routes: #/new, #/a/<id>. Live updates via SSE; judge chat and publishing via POST. */
(() => {
  "use strict";

  const $ = (sel, root = document) => root.querySelector(sel);
  const el = (tag, attrs = {}, children = []) => {
    const node = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
      if (k === "class") node.className = v;
      else if (k === "text") node.textContent = v;
      else if (k === "html") node.innerHTML = v;
      else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
      else if (v !== null && v !== undefined) node.setAttribute(k, v);
    }
    for (const c of [].concat(children)) if (c) node.append(c);
    return node;
  };
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fmtDate = (iso) => new Date(iso).toLocaleString("ru-RU", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" });
  const fmtMs = (ms) => (ms == null ? "" : ms < 60000 ? `${(ms / 1000).toFixed(0)} с` : `${Math.floor(ms / 60000)} мин ${Math.round((ms % 60000) / 1000)} с`);
  const scoreClass = (v) => (v < 40 ? "s-bad" : v < 70 ? "s-mid" : "s-ok");
  const scoreColor = (v) => (v < 40 ? "var(--bad)" : v < 70 ? "var(--warn)" : "var(--ok)");

  const state = { config: null, analysts: [], colors: {}, history: [], current: null, es: null, timer: null, rounds: 2, mode: "auto" };

  // ---------- API ----------
  const api = {
    async get(url) { const r = await fetch(url); if (!r.ok) throw await errorOf(r); return r.json(); },
    async post(url, body) { const r = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }); if (!r.ok) throw await errorOf(r); return r.json(); },
    async del(url) { const r = await fetch(url, { method: "DELETE" }); if (!r.ok && r.status !== 404) throw await errorOf(r); },
  };
  async function errorOf(r) {
    let detail = r.statusText;
    try { const j = await r.json(); detail = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail); } catch (_) { /* ignore */ }
    return new Error(detail || `HTTP ${r.status}`);
  }

  // ---------- Toast / theme / sidebar ----------
  let toastTimer;
  function toast(msg) {
    const t = $("#toast"); t.textContent = msg; t.classList.remove("hidden");
    clearTimeout(toastTimer); toastTimer = setTimeout(() => t.classList.add("hidden"), 2200);
  }
  function initTheme() {
    let saved = null; try { saved = localStorage.getItem("theme"); } catch (_) { /* private mode */ }
    if (saved) document.documentElement.dataset.theme = saved;
    $("#btn-theme").addEventListener("click", () => {
      const cur = document.documentElement.dataset.theme || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
      const next = cur === "dark" ? "light" : "dark";
      document.documentElement.dataset.theme = next;
      try { localStorage.setItem("theme", next); } catch (_) { /* ignore */ }
    });
  }
  function initSidebar() {
    $("#btn-sidebar").addEventListener("click", () => $("#sidebar").classList.toggle("open"));
    document.addEventListener("click", (e) => {
      if (!e.target.closest("#sidebar") && !e.target.closest("#btn-sidebar")) $("#sidebar").classList.remove("open");
    });
  }

  // ---------- Config / history ----------
  async function loadConfig() {
    state.config = await api.get("/api/config");
    state.analysts = state.config.analysts;
    const palette = ["var(--a1)", "var(--a2)", "var(--a3)"];
    state.analysts.forEach((a, i) => { state.colors[a.name] = palette[i % palette.length]; });
    state.rounds = state.config.default_rounds;
    $("#counter-max").textContent = state.config.max_text_chars;
    const select = $("#model");
    select.innerHTML = "";
    for (const m of state.config.models) select.append(el("option", { value: m.id, text: m.label }));
    select.value = state.config.default_model;
    $("#mock-badge").classList.toggle("hidden", !state.config.mock);
    $("#mock-banner").classList.toggle("hidden", !state.config.mock);
    for (const b of $("#rounds").children) b.classList.toggle("active", Number(b.dataset.rounds) === state.rounds);
    const cards = $("#analyst-cards"); cards.innerHTML = "";
    for (const a of state.analysts) {
      cards.append(el("div", { class: "analyst-card", style: `--a-color:${state.colors[a.name]}` }, [
        el("div", { class: "name", text: `${a.emoji} ${a.name}` }),
        el("div", { class: "focus", text: a.focus }),
      ]));
    }
  }
  async function loadHistory() {
    state.history = await api.get("/api/analyses");
    renderHistory();
  }
  function renderHistory() {
    const list = $("#history"); list.innerHTML = "";
    $("#history-empty").classList.toggle("hidden", state.history.length > 0);
    $("#history-count").textContent = state.history.length ? String(state.history.length) : "";
    for (const item of state.history) {
      const li = el("li", { class: state.current?.id === item.id ? "active" : "" });
      const meta = el("div", { class: "h-meta" }, [
        el("span", { class: `dot ${item.status}` }),
        el("span", { text: fmtDate(item.created_at) }),
        item.rationality_score != null ? el("span", { class: `score-pill ${scoreClass(item.rationality_score)}`, text: item.rationality_score }) : null,
      ]);
      li.append(el("a", { href: `#/a/${item.id}` }, [el("span", { class: "h-title", text: item.title }), meta]));
      li.append(el("button", { class: "h-del", title: "Удалить", text: "✕", onclick: async (e) => {
        e.preventDefault();
        if (!confirm("Удалить этот разбор? Если он опубликован, публичная ссылка тоже перестанет открываться.")) return;
        try { await api.del(`/api/analyses/${item.id}`); toast("Удалено"); if (state.current?.id === item.id) location.hash = "#/new"; await loadHistory(); }
        catch (err) { toast(err.message); }
      } }));
      list.append(li);
    }
  }

  // ---------- Compose ----------
  function initCompose() {
    const text = $("#text");
    // No parser (ADR-008): in "Авто" the models decide whether it is a dialogue and who speaks.
    const MODE_HINTS = { auto: "· текст или диалог — решат модели", text: "", dialogue: "· собеседников определят модели" };
    const updateModeHint = () => { $("#mode-hint").textContent = MODE_HINTS[state.mode] || ""; };
    updateModeHint();
    // No maxlength: the browser would silently cut a long paste. Over the limit the counter turns red and submit says so.
    const updateCounter = () => {
      $("#counter").textContent = text.value.length;
      $(".counter").classList.toggle("over", text.value.trim().length > maxTextChars());
    };
    text.addEventListener("input", updateCounter);
    initFileInput(text);
    $("#mode").addEventListener("click", (e) => {
      const b = e.target.closest("button[data-mode]"); if (!b) return;
      state.mode = b.dataset.mode;
      for (const x of $("#mode").children) x.classList.toggle("active", x === b);
      updateModeHint();
    });
    $("#rounds").addEventListener("click", (e) => {
      const b = e.target.closest("button[data-rounds]"); if (!b) return;
      state.rounds = Number(b.dataset.rounds);
      for (const x of $("#rounds").children) x.classList.toggle("active", x === b);
    });
    document.addEventListener("keydown", (e) => {
      if ((e.metaKey || e.ctrlKey) && e.key === "Enter" && !$("#view-new").classList.contains("hidden")) { e.preventDefault(); $("#compose").requestSubmit(); }
    });
    $("#compose").addEventListener("submit", async (e) => {
      e.preventDefault();
      const err = $("#compose-error"); err.classList.add("hidden");
      const hideNames = $("#hide-names").value.split(/[,;\n]/).map((n) => n.trim()).filter(Boolean);
      const body = { text: text.value, question: $("#question").value || null, rounds: state.rounds, model: $("#model").value, mode: state.mode, hide_names: hideNames };
      if (!body.text.trim()) { err.textContent = "Вставьте текст для разбора."; err.classList.remove("hidden"); return; }
      const length = body.text.trim().length;
      if (length > maxTextChars()) { err.textContent = `В тексте ${length} символов, лимит — ${maxTextChars()}. Сократите текст.`; err.classList.remove("hidden"); return; }
      $("#submit").disabled = true;
      try {
        const { id, legend } = await api.post("/api/analyses", body);
        if (legend && Object.keys(legend).length) { try { localStorage.setItem(`legend:${id}`, JSON.stringify(legend)); } catch (_) { /* ignore */ } }
        text.value = ""; $("#question").value = ""; $("#hide-names").value = ""; updateCounter();
        location.hash = `#/a/${id}`;
        await loadHistory();
      } catch (ex) { err.textContent = ex.message; err.classList.remove("hidden"); }
      finally { $("#submit").disabled = false; }
    });
    $("#btn-new").addEventListener("click", () => { location.hash = "#/new"; });
  }

  const maxTextChars = () => state.config?.max_text_chars ?? Infinity;

  // ---------- Text files (.txt / .md) — read in the browser, never truncated (SPEC §4.0c) ----------
  const FILE_EXTENSIONS = [".txt", ".md"];

  function decodeText(bytes) {
    if (bytes[0] === 0xff && bytes[1] === 0xfe) return new TextDecoder("utf-16le").decode(bytes);
    if (bytes[0] === 0xfe && bytes[1] === 0xff) return new TextDecoder("utf-16be").decode(bytes);
    try { return new TextDecoder("utf-8", { fatal: true }).decode(bytes); }
    catch (_) { return new TextDecoder("windows-1251").decode(bytes); }
  }

  async function readTextFile(file, maxChars) {
    const name = file.name.toLowerCase();
    if (!FILE_EXTENSIONS.some((ext) => name.endsWith(ext))) throw new Error(`«${file.name}»: поддерживаются только файлы .txt и .md.`);
    // UTF-8 spends at most 4 bytes per character: a bigger file is over the limit without reading it.
    if (file.size > maxChars * 4 + 4) throw new Error(`«${file.name}» слишком большой (${Math.round(file.size / 1024)} КБ): лимит — ${maxChars} символов.`);
    const content = decodeText(new Uint8Array(await file.arrayBuffer())).replace(/\r\n?/g, "\n");
    if (content.includes("\0")) throw new Error(`«${file.name}» не похож на текстовый файл.`);
    if (!content.trim()) throw new Error(`«${file.name}» пустой.`);
    const length = content.trim().length;
    if (length > maxChars) throw new Error(`В «${file.name}» ${length} символов, лимит — ${maxChars}. Сократите текст и загрузите снова.`);
    return content.trim();
  }

  function initFileInput(text) {
    const input = $("#file");
    const err = $("#compose-error");
    const load = async (file) => {
      err.classList.add("hidden");
      try {
        const content = await readTextFile(file, maxTextChars());
        if (text.value.trim() && !confirm(`Заменить введённый текст содержимым «${file.name}»?`)) return;
        text.value = content;
        text.dispatchEvent(new Event("input"));
        toast(`Загружен «${file.name}»: ${content.length} символов`);
      } catch (ex) { err.textContent = ex.message; err.classList.remove("hidden"); }
    };
    $("#file-btn").addEventListener("click", () => input.click());
    input.addEventListener("change", () => { if (input.files[0]) load(input.files[0]); input.value = ""; });
    const hasFiles = (e) => [...(e.dataTransfer?.types || [])].includes("Files");
    text.addEventListener("dragover", (e) => { if (!hasFiles(e)) return; e.preventDefault(); text.classList.add("dragover"); });
    text.addEventListener("dragleave", () => text.classList.remove("dragover"));
    text.addEventListener("drop", (e) => {
      if (!hasFiles(e)) return;
      e.preventDefault(); text.classList.remove("dragover");
      if (e.dataTransfer.files.length > 1) toast("Загружаю только первый файл");
      load(e.dataTransfer.files[0]);
    });
  }

  // ---------- Analysis view ----------
  function closeStream() {
    if (state.es) { state.es.close(); state.es = null; }
    if (state.timer) { clearInterval(state.timer); state.timer = null; }
  }

  async function openAnalysis(id) {
    closeStream();
    const root = $("#analysis-root"); root.innerHTML = "";
    let detail;
    try { detail = await api.get(`/api/analyses/${id}`); }
    catch (err) { root.append(el("div", { class: "notice notice-error", text: `Разбор не найден: ${err.message}` })); return; }
    let legend = null;
    try { legend = JSON.parse(localStorage.getItem(`legend:${id}`) || "null"); } catch (_) { legend = null; }
    let showReal = false;
    try { showReal = localStorage.getItem("showRealNames") === "1"; } catch (_) { /* ignore */ }
    state.current = { id, detail, seen: new Set(), chips: {}, startedAt: Date.parse(detail.created_at) || Date.now(), legend, showReal };
    renderHistory();
    root.append(buildAnalysis(detail));
    for (const t of detail.turns) addTurn(t);
    if (detail.verdict) renderVerdict(detail.verdict, detail);
    renderChat(detail);
    renderShare(detail);
    if (detail.error) showError(detail.error);
    if (detail.status === "running") subscribe(id);
    else finishChips();
    applyLegend();
  }

  // Real names live only in this browser (localStorage); the server and the model see placeholders.
  function applyLegend() {
    const cur = state.current; if (!cur) return;
    const box = $("[data-legend]"); if (!box) return;
    const legend = cur.legend && Object.keys(cur.legend).length ? cur.legend : null;
    box.classList.toggle("hidden", !legend);
    if (!legend) return;
    const btn = $("[data-legend-toggle]", box);
    btn.textContent = cur.showReal ? "Скрыть настоящие имена" : "Показать настоящие имена";
    btn.onclick = () => {
      cur.showReal = !cur.showReal;
      try { localStorage.setItem("showRealNames", cur.showReal ? "1" : "0"); } catch (_) { /* ignore */ }
      openAnalysis(cur.id);
    };
    if (!cur.showReal) return;
    const keys = Object.keys(legend).sort((a, b) => b.length - a.length);
    const walker = document.createTreeWalker($("#analysis-root"), NodeFilter.SHOW_TEXT);
    const nodes = [];
    while (walker.nextNode()) nodes.push(walker.currentNode);
    for (const node of nodes) {
      let value = node.nodeValue;
      for (const k of keys) value = value.split(k).join(legend[k]);
      if (value !== node.nodeValue) node.nodeValue = value;
    }
  }

  const MODE_LABELS = { auto: "Авто", text: "Текст", dialogue: "Диалог" };
  // Hidden names live in the text as labels (SPEC §4.0b); the first label is always "А".
  const LABEL_RE = /Собеседник [А-Я]|Participant [A-Z]/g;
  const hiddenLabels = (d) => [...new Set(d.source_text.match(LABEL_RE) || [])];

  // Speakers are named by the verdict (ADR-008): empty until it arrives, refreshed on "done".
  function renderParticipants(root, d) {
    const box = $("[data-participants]", root); if (!box) return;
    box.innerHTML = "";
    box.classList.toggle("hidden", !d.participants.length);
    if (!d.participants.length) return;
    box.append(el("span", { class: "muted small", text: "Собеседники:" }));
    for (const name of d.participants) box.append(el("span", { class: "pchip", text: name }));
  }

  function buildAnalysis(d) {
    const node = $("#tpl-analysis").content.firstElementChild.cloneNode(true);
    $("[data-id]", node).textContent = `#${d.id}`;
    $("[data-date]", node).textContent = fmtDate(d.created_at);
    $("[data-title]", node).textContent = d.title;
    setStatus(node, d.status);
    $("[data-meta]", node).append(
      el("span", { "data-mode-tag": "", text: MODE_LABELS[d.mode] || d.mode }),
      el("span", { text: `Модель: ${d.model_analyst}` }),
      el("span", { text: `Раундов: ${d.rounds}` }),
      el("span", { text: `Символов: ${d.source_text.length}` }),
      el("span", { "data-duration": "", text: d.duration_ms ? `Время: ${fmtMs(d.duration_ms)}` : "" }),
    );
    $("[data-text]", node).textContent = d.source_text;
    renderParticipants(node, d);
    if (d.question) { const q = $("[data-question]", node); q.textContent = `Вопрос: ${d.question}`; q.classList.remove("hidden"); }
    const progress = $("[data-progress]", node);
    for (const a of state.analysts) {
      const chip = el("span", { class: "chip", style: `--a-color:${state.colors[a.name]}` }, [el("i", { class: "st" }), el("span", { text: `${a.emoji} ${a.name}` })]);
      state.current.chips[a.name] = chip; progress.append(chip);
    }
    const judge = el("span", { class: "chip judge" }, [el("i", { class: "st" }), el("span", { text: "⚖️ Судья" })]);
    state.current.chips["Судья"] = judge; progress.append(judge, el("span", { class: "spacer" }), el("span", { class: "elapsed", "data-elapsed": "" }));
    return node;
  }

  function setStatus(root, status) {
    const s = $("[data-status]", root); s.className = `status ${status}`;
    s.textContent = { running: "идёт разбор", finished: "завершён", failed: "ошибка" }[status] || status;
  }
  function setChip(name, cls) {
    const chip = state.current?.chips[name]; if (!chip) return;
    chip.classList.remove("thinking", "done"); if (cls) chip.classList.add(cls);
  }
  function finishChips() { for (const n of Object.keys(state.current?.chips || {})) setChip(n, "done"); }

  function roundContainer(roundNo, kind) {
    const rounds = $("[data-turns]");
    let box = $(`[data-round="${roundNo}"]`, rounds);
    if (!box) {
      box = el("div", { class: "round", "data-round": roundNo });
      box.append(el("div", { class: "round-head" }, [el("h3", { text: `Раунд ${roundNo} · ${kind || (roundNo === 1 ? "Независимый разбор" : "Перекрёстная проверка")}` })]));
      rounds.append(box);
    }
    return box;
  }

  function renderTurnBody(text) {
    const body = el("div", { class: "turn-body" });
    const markers = { "Принимаю:": "accept", "Оспариваю:": "dispute", "Уточняю:": "refine" };
    const paras = text.replace(/\*\*(Принимаю|Оспариваю|Уточняю):\*\*/g, "$1:").split(/\n\s*\n/);
    let open = null;
    for (let p of paras) {
      p = p.trim(); if (!p) continue;
      const m = Object.keys(markers).find((k) => p.startsWith(k));
      if (m) {
        const rest = p.slice(m.length).trim();
        open = el("div", { class: `block ${markers[m]}` }, [el("span", { class: "tag", text: m.slice(0, -1) })]);
        if (rest) open.append(el("p", { html: inline(rest) }));
        body.append(open);
      } else if (open) {
        open.append(el("p", { html: inline(p) }));
      } else {
        body.append(el("p", { html: inline(p) }));
      }
    }
    return body;
  }
  const inline = (s) => esc(s).replace(/\*\*(.+?)\*\*/g, "<b>$1</b>").replace(/(^|\n)#{1,4}\s*/g, "$1");

  function addTurn(t) {
    const key = `${t.round_no}:${t.analyst}`; // each analyst speaks once per round
    if (state.current.seen.has(key)) return;
    state.current.seen.add(key);
    const box = roundContainer(t.round_no, t.kind);
    $(`[data-typing="${CSS.escape(t.analyst)}"]`, box)?.remove();
    const analyst = state.analysts.find((a) => a.name === t.analyst);
    box.append(el("div", { class: "card turn", style: `--a-color:${state.colors[t.analyst] || "var(--border)"}` }, [
      el("div", { class: "turn-head" }, [
        el("span", { text: t.emoji }),
        el("span", { class: "who", text: t.analyst }),
        analyst ? el("span", { class: "lens", text: `· ${analyst.focus}` }) : null,
      ]),
      renderTurnBody(t.text),
    ]));
    setChip(t.analyst, "done");
  }
  function addTyping(name, emoji, roundNo) {
    setChip(name, "thinking");
    if (name === "Судья") return;
    const box = roundContainer(roundNo);
    if ($(`[data-typing="${CSS.escape(name)}"]`, box)) return;
    box.append(el("div", { class: "card turn typing", "data-typing": name, style: `--a-color:${state.colors[name]}` }, [
      el("div", { class: "turn-head" }, [el("span", { text: emoji }), el("span", { class: "who", text: name }), el("span", { class: "lens", text: "· думает…" })]),
      el("div", { class: "skeleton", style: "width:90%" }), el("div", { class: "skeleton", style: "width:75%" }), el("div", { class: "skeleton", style: "width:60%" }),
    ]));
  }
  function showError(message) {
    const n = $("[data-error]"); if (!n) return; n.textContent = `Разбор не завершён: ${message}`; n.classList.remove("hidden");
  }

  function subscribe(id) {
    const es = new EventSource(`/api/analyses/${id}/events`);
    state.es = es;
    let currentRound = 1;
    const elapsed = $("[data-elapsed]");
    state.timer = setInterval(() => { if (elapsed) elapsed.textContent = fmtMs(Date.now() - state.current.startedAt); }, 1000);
    es.addEventListener("started", () => { /* chips already built */ });
    es.addEventListener("round", (e) => { const d = JSON.parse(e.data); currentRound = d.round_no; roundContainer(d.round_no, d.kind); });
    es.addEventListener("typing", (e) => { const d = JSON.parse(e.data); addTyping(d.analyst, d.emoji, currentRound); });
    es.addEventListener("turn", (e) => { addTurn(JSON.parse(e.data)); applyLegend(); });
    es.addEventListener("verdict", (e) => { renderVerdict(JSON.parse(e.data), state.current.detail); setChip("Судья", "done"); applyLegend(); });
    // "error" is both the EventSource connection event (no data) and our SPEC §3.4 event (with data).
    es.addEventListener("error", (e) => { if (e.data) showError(JSON.parse(e.data).message); });
    es.addEventListener("done", async (e) => {
      const d = JSON.parse(e.data);
      closeStream();
      try { const detail = await api.get(`/api/analyses/${id}`); state.current.detail = detail; setStatus($("#analysis-root"), detail.status); const modeTag = $("#analysis-root [data-mode-tag]"); if (modeTag) modeTag.textContent = MODE_LABELS[detail.mode] || detail.mode; renderParticipants($("#analysis-root"), detail); const dur = $("[data-duration]"); if (dur) dur.textContent = `Время: ${fmtMs(detail.duration_ms)}`; if (detail.verdict && !$(".verdict")) renderVerdict(detail.verdict, detail); renderStats(d); renderChat(detail); renderShare(detail); applyLegend(); } catch (_) { /* ignore */ }
      finishChips();
      await loadHistory();
    });
  }

  // ---------- Verdict ----------
  function renderVerdict(v, detail) {
    const slot = $("[data-verdict]"); if (!slot) return; slot.innerHTML = "";
    const card = el("div", { class: "card verdict" });
    const gauge = el("div", { class: "gauge", style: `--g:${v.rationality_score};--g-color:${scoreColor(v.rationality_score)}` }, [
      el("div", { class: "g-val", text: v.rationality_score }), el("div", { class: "g-lbl", text: "рациональность\nматериала" }),
    ]);
    const main = el("div", { class: "verdict-main" }, [
      el("h2", { text: "⚖️ Вердикт судьи · что ближе всего к истине" }),
      el("p", { class: "core", text: v.core_question }),
      el("p", { class: "conclusion", text: v.conclusion }),
      el("div", { class: "confidence" }, [el("span", { text: `Уверенность судьи: ${v.confidence}%` }), el("div", { class: "bar" }, [el("i", { style: `width:${v.confidence}%` })])]),
    ]);
    card.append(el("div", { class: "verdict-head" }, [gauge, main]));
    if (v.narrative && v.narrative.trim()) {
      const box = el("div", { class: "narrative" }, [el("h3", { text: "Развёрнутое обоснование" })]);
      for (const para of v.narrative.split(/\n\s*\n/)) if (para.trim()) box.append(el("p", { html: inline(para.trim()) }));
      card.append(box);
    }

    const col = (cls, title, items) => el("div", { class: `v-col ${cls}` }, [el("h4", { text: title }), items.length ? el("ul", {}, items.map((i) => el("li", { text: i }))) : el("p", { class: "muted small", text: "—" })]);
    card.append(el("div", { class: "v-grid" }, [col("est", "Установлено", v.established), col("prob", "Вероятно", v.probable), col("unc", "Неопределённо", v.uncertain)]));

    if (v.claims.length) {
      const labels = { supported: "доказано", plausible: "вероятно", unsupported: "без доказательств", unfalsifiable: "непроверяемо", false: "опровергнуто" };
      const table = el("table", { class: "claims" }, [el("thead", {}, [el("tr", {}, [el("th", { text: "Утверждение" }), el("th", { text: "Статус" }), el("th", { text: "Почему" })])])]);
      const tbody = el("tbody");
      for (const c of v.claims) tbody.append(el("tr", {}, [el("td", { text: c.claim }), el("td", {}, [el("span", { class: `cs cs-${c.status}`, text: labels[c.status] || c.status })]), el("td", { class: "muted", text: c.note })]));
      table.append(tbody);
      card.append(el("div", { class: "v-section" }, [el("h3", { text: "Утверждения текста" }), table]));
    }
    const fal = el("div", { class: "v-section" }, [el("h3", { text: v.fallacies.length ? `Уловки и ошибки (${v.fallacies.length})` : "Уловки и ошибки" })]);
    if (v.fallacies.length) for (const f of v.fallacies) fal.append(el("div", { class: "fallacy" }, [el("div", { class: "f-name", text: f.name }), el("blockquote", { text: f.quote }), el("p", { text: f.explanation })]));
    else fal.append(el("p", { class: "muted small", text: "Демагогических приёмов не найдено." }));
    card.append(fal);

    card.append(el("div", { class: "v-section" }, [el("div", { class: "v-three" }, [
      el("div", { class: "v-mini" }, [el("h4", { text: "Сильнейшая версия" }), el("p", { text: v.steelman })]),
      el("div", { class: "v-mini" }, [el("h4", { text: "Что изменит вердикт" }), el("p", { text: v.falsification })]),
      el("div", { class: "v-mini" }, [el("h4", { text: "Что из этого следует" }), el("p", { text: v.so_what })]),
    ])]));

    if (v.participants.length) {
      const people = el("div", { class: "people" });
      const palette = ["var(--a1)", "var(--a2)", "var(--a3)", "var(--judge)"];
      [...v.participants].sort((a, b) => b.truth_proximity - a.truth_proximity).forEach((p, i) => {
        people.append(el("div", { class: `person ${p.name === v.closest_to_truth ? "closest" : ""}`, style: `--p-color:${palette[i % palette.length]}` }, [
          el("div", { class: "p-head" }, [el("span", { text: p.name }), el("span", { class: "p-score", text: p.truth_proximity })]),
          el("div", { class: "bar" }, [el("i", { style: `width:${p.truth_proximity}%` })]),
          el("p", { text: p.position }),
          el("p", { class: "ok", text: p.right_about }),
          p.wrong_about ? el("p", { class: "bad", text: p.wrong_about }) : null,
          el("p", { class: "p-note", text: p.note }),
        ]));
      });
      card.append(el("div", { class: "v-section" }, [
        el("h3", { text: "Собеседники · близость к истине" }),
        el("p", { class: "truth-banner", html: `Ближе всего к истине: <span>${esc(v.closest_to_truth || "—")}</span>` }),
        people,
      ]));
    }
    card.append(el("div", { class: "v-three" }, [
      el("div", { class: "v-mini" }, [el("h4", { text: "На чём сошлись аналитики" }), v.consensus.length ? el("ul", {}, v.consensus.map((i) => el("li", { text: i }))) : el("p", { class: "muted small", text: "—" })]),
      el("div", { class: "v-mini" }, [el("h4", { text: "Осталось открытым" }), v.open_questions.length ? el("ul", {}, v.open_questions.map((i) => el("li", { text: i }))) : el("p", { class: "muted small", text: "—" })]),
    ]));

    const foot = el("div", { class: "v-foot" }, [
      el("span", { class: "stats", "data-stats": "", text: detail?.duration_ms ? `Время разбора: ${fmtMs(detail.duration_ms)}` : "" }),
      el("button", { class: "btn btn-sm", text: "Копировать Markdown", onclick: () => copyMarkdown() }),
      el("button", { class: "btn btn-sm", text: "Скачать JSON", onclick: () => downloadJson() }),
      el("button", { class: "btn btn-sm", text: "Ссылка для себя", title: "Ссылка на разбор в этом приложении — откроется только у тех, у кого есть доступ к нему", onclick: () => { navigator.clipboard?.writeText(location.href).then(() => toast("Ссылка скопирована")); } }),
      el("button", { class: "btn btn-sm", text: "Разобрать снова", onclick: () => rerun() }),
    ]);
    card.append(foot, el("div", { class: "share-slot", "data-share": "" }));
    slot.append(card);
  }

  // ---------- Publishing (SPEC §3.6a, §4.7) ----------
  function renderShare(d) {
    const slot = $("[data-share]"); if (!slot) return; slot.innerHTML = "";
    if (d.status !== "finished" || !d.verdict) return;
    const err = el("p", { class: "error hidden" });
    const fail = (ex) => { err.textContent = ex.message; err.classList.remove("hidden"); };
    const what = "Публикуются исходный материал, реплики аналитиков и вердикт — в том виде, в каком они хранятся на сервере. Обсуждение с судьёй не публикуется" + (hiddenLabels(d).length ? ", настоящие имена из этого браузера — тоже." : ".");
    const local = state.config?.share_backend === "local" ? " Снимок лежит на этом сервере: ссылка откроется у других, только если сервер им доступен." : "";
    if (!d.share) {
      const btn = el("button", { class: "btn btn-primary btn-sm", type: "button", text: "🌐 Опубликовать" });
      btn.onclick = async () => {
        btn.disabled = true; err.classList.add("hidden");
        try { d.share = await api.post(`/api/analyses/${d.id}/share`, {}); renderShare(d); copyShare(d.share); }
        catch (ex) { fail(ex); btn.disabled = false; }
      };
      slot.append(el("div", { class: "share" }, [
        el("div", { class: "share-row" }, [btn, el("span", { class: "muted small", text: "Страница по ссылке откроется у любого, без доступа к приложению." })]),
        el("p", { class: "muted small", text: what + local }), err,
      ]));
      return;
    }
    const url = absoluteUrl(d.share.url);
    const input = el("input", { type: "text", readonly: "", value: url, "aria-label": "Публичная ссылка", onfocus: (e) => e.target.select() });
    const off = el("button", { class: "btn btn-sm btn-ghost", type: "button", text: "Снять с публикации" });
    off.onclick = async () => {
      if (!confirm("Снять с публикации? Ссылка перестанет открываться.")) return;
      off.disabled = true; err.classList.add("hidden");
      try { await api.del(`/api/analyses/${d.id}/share`); d.share = null; renderShare(d); toast("Снято с публикации"); }
      catch (ex) { fail(ex); off.disabled = false; }
    };
    slot.append(el("div", { class: "share published" }, [
      el("div", { class: "share-head" }, [el("span", { class: "badge badge-ok", text: "опубликовано" }), el("span", { class: "muted small", text: fmtDate(d.share.created_at) })]),
      el("div", { class: "share-row" }, [
        input,
        el("button", { class: "btn btn-sm", type: "button", text: "Копировать", onclick: () => copyShare(d.share) }),
        el("a", { class: "btn btn-sm", href: url, target: "_blank", rel: "noopener noreferrer", text: "Открыть" }),
        off,
      ]),
      el("p", { class: "muted small", text: what + local }), err,
    ]));
  }
  const absoluteUrl = (url) => new URL(url, location.href).href;
  function copyShare(share) {
    navigator.clipboard?.writeText(absoluteUrl(share.url)).then(() => toast("Публичная ссылка скопирована"), () => toast("Ссылка готова — скопируйте её из поля"));
  }
  // ---------- Judge chat (SPEC §4.6) ----------
  function chatMessage(m) {
    const node = el("div", { class: `msg ${m.role}` }, [el("div", { class: "msg-who", text: m.role === "judge" ? "⚖️ Судья" : "Вы" })]);
    if (m.role === "judge") for (const para of m.text.split(/\n\s*\n/)) { if (para.trim()) node.append(el("p", { html: inline(para.trim()) })); }
    else node.append(el("p", { text: m.text }));
    return node;
  }
  function renderChat(d) {
    const slot = $("[data-chat]"); if (!slot) return; slot.innerHTML = "";
    if (d.status !== "finished" || !d.verdict) return;
    const max = state.config?.chat_max_messages ?? 20;
    const maxChars = state.config?.chat_max_chars ?? 2000;
    const log = el("div", { class: "chat-log" }, d.chat.map(chatMessage));
    const input = el("textarea", { rows: "3", maxlength: String(maxChars), placeholder: "Спросите, почему судья так решил, или оспорьте вывод доводом…" });
    const left = el("span", { class: "muted small" });
    const send = el("button", { class: "btn btn-primary", type: "submit", text: "Спросить судью" });
    const err = el("p", { class: "error hidden" });
    const form = el("form", { class: "chat-form" }, [input, el("div", { class: "chat-actions" }, [left, el("span", { class: "hint", text: "⌘/Ctrl + Enter" }), send]), err]);
    const used = () => d.chat.filter((m) => m.role === "user").length;
    const refresh = () => {
      const rest = max - used();
      left.textContent = rest > 0 ? `Осталось сообщений: ${rest}` : "Лимит сообщений исчерпан";
      input.disabled = send.disabled = rest <= 0;
    };
    input.addEventListener("keydown", (e) => { if ((e.metaKey || e.ctrlKey) && e.key === "Enter") { e.preventDefault(); form.requestSubmit(); } });
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const text = input.value.trim(); if (!text) return;
      err.classList.add("hidden");
      const pending = chatMessage({ role: "user", text });
      const typing = el("div", { class: "msg judge typing" }, [el("div", { class: "msg-who", text: "⚖️ Судья думает…" }), el("div", { class: "skeleton", style: "width:80%" }), el("div", { class: "skeleton", style: "width:55%" })]);
      log.append(pending, typing); input.value = ""; input.disabled = send.disabled = true;
      typing.scrollIntoView({ behavior: "smooth", block: "nearest" });
      try {
        const legend = state.current?.legend || {};
        const { question, reply } = await api.post(`/api/analyses/${d.id}/chat`, { text, legend });
        d.chat.push(question, reply);
        pending.replaceWith(chatMessage(question)); typing.replaceWith(chatMessage(reply));
        applyLegend();
      } catch (ex) {
        pending.remove(); typing.remove(); input.value = text;
        err.textContent = ex.message; err.classList.remove("hidden");
      } finally { refresh(); if (!input.disabled) input.focus(); }
    });
    refresh();
    // Without the legend the server cannot replace real names in the message (SPEC §4.6).
    const noLegend = hiddenLabels(d).length > 0 && !(state.current?.legend && Object.keys(state.current.legend).length);
    slot.append(el("div", { class: "card chat" }, [
      el("h3", { text: "💬 Обсудить с судьёй" }),
      el("p", { class: "muted small", text: "Переспросите, попросите объяснить или оспорьте вывод. Судья меняет оценку только под весом доводов и новых фактов, а не настойчивости; вердикт выше при этом не переписывается." }),
      noLegend ? el("p", { class: "notice small", text: `Скрытые имена этого разбора в этом браузере не сохранены — называйте людей метками (${hiddenLabels(d).join(", ")}), иначе имена уйдут в модель.` }) : null,
      log, form,
    ]));
  }

  function renderStats(done) {
    const s = $("[data-stats]"); if (!s) return;
    const parts = [`Время разбора: ${fmtMs(done.duration_ms)}`];
    if (done.calls) parts.push(`вызовов модели: ${done.calls}`);
    if (done.input_tokens || done.output_tokens) parts.push(`токены: ${done.input_tokens} → ${done.output_tokens}`);
    if (done.cache_read_tokens) parts.push(`из кэша: ${done.cache_read_tokens}`);
    s.textContent = parts.join(" · ");
  }

  function toMarkdown(d) {
    const v = d.verdict; const lines = [`# Разбор #${d.id}: ${d.title}`, "", "## Исходный текст", "", d.source_text, ""];
    if (d.question) lines.push(`**Вопрос:** ${d.question}`, "");
    if (d.participants.length) lines.push(`**Собеседники:** ${d.participants.join(", ")}`, "");
    let round = 0;
    for (const t of d.turns) { if (t.round_no !== round) { round = t.round_no; lines.push(`## Раунд ${round}`, ""); } lines.push(`### ${t.emoji} ${t.analyst}`, "", t.text, ""); }
    if (v) {
      lines.push("## ⚖️ Вердикт судьи", "", `**Вопрос:** ${v.core_question}`, "", `**Ближе всего к истине:** ${v.conclusion}`, "", `Рациональность материала: **${v.rationality_score}/100** · уверенность судьи: ${v.confidence}%`, "");
      if (v.narrative && v.narrative.trim()) lines.push("### Развёрнутое обоснование", "", v.narrative.trim(), "");
      const list = (t, items) => { lines.push(`### ${t}`, ""); for (const i of items) lines.push(`- ${i}`); if (!items.length) lines.push("—"); lines.push(""); };
      list("Установлено", v.established); list("Вероятно", v.probable); list("Неопределённо", v.uncertain);
      if (v.claims.length) { lines.push("### Утверждения", "", "| Утверждение | Статус | Почему |", "|---|---|---|"); for (const c of v.claims) lines.push(`| ${c.claim} | ${c.status} | ${c.note} |`); lines.push(""); }
      if (v.fallacies.length) { lines.push("### Уловки", ""); for (const f of v.fallacies) lines.push(`- **${f.name}** — «${f.quote}»: ${f.explanation}`); lines.push(""); }
      lines.push(`**Сильнейшая версия:** ${v.steelman}`, "", `**Что изменит вердикт:** ${v.falsification}`, "", `**Что из этого следует:** ${v.so_what}`, "");
      if (v.participants.length) {
        lines.push(`### Ближе всего к истине: ${v.closest_to_truth}`, "");
        for (const p of [...v.participants].sort((a, b) => b.truth_proximity - a.truth_proximity)) lines.push(`- **${p.name}** — ${p.truth_proximity}/100. ${p.position} ✔ ${p.right_about}${p.wrong_about ? ` ✘ ${p.wrong_about}` : ""}`);
        lines.push("");
      }
      list("На чём сошлись аналитики", v.consensus); list("Осталось открытым", v.open_questions);
    }
    if (d.chat?.length) {
      lines.push("## 💬 Обсуждение с судьёй", "");
      for (const m of d.chat) lines.push(`**${m.role === "judge" ? "Судья" : "Вы"}:** ${m.text}`, "");
    }
    return lines.join("\n");
  }
  function copyMarkdown() {
    const md = toMarkdown(state.current.detail);
    navigator.clipboard?.writeText(md).then(() => toast("Markdown скопирован"), () => toast("Не удалось скопировать"));
  }
  function downloadJson() {
    const blob = new Blob([JSON.stringify(state.current.detail, null, 2)], { type: "application/json" });
    const a = el("a", { href: URL.createObjectURL(blob), download: `rational-check-${state.current.id}.json` });
    document.body.append(a); a.click(); a.remove();
  }
  function rerun() {
    const d = state.current.detail;
    location.hash = "#/new";
    $("#text").value = d.source_text; $("#question").value = d.question || ""; $("#text").dispatchEvent(new Event("input"));
    window.scrollTo({ top: 0 });
  }

  // ---------- Router ----------
  function route() {
    const m = location.hash.match(/^#\/a\/(\d+)$/);
    $("#sidebar").classList.remove("open");
    if (m) {
      $("#view-new").classList.add("hidden"); $("#view-analysis").classList.remove("hidden");
      openAnalysis(Number(m[1]));
    } else {
      closeStream(); state.current = null; renderHistory();
      $("#view-analysis").classList.add("hidden"); $("#view-new").classList.remove("hidden");
      $("#text").focus();
    }
  }

  async function main() {
    initTheme(); initSidebar(); initCompose();
    try { await loadConfig(); await loadHistory(); } catch (err) { toast(`Сервер недоступен: ${err.message}`); }
    window.addEventListener("hashchange", route);
    route();
  }
  main();
})();
