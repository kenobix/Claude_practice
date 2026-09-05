"use strict";

/* ==========================================================================
   定数・トークン
   ========================================================================== */
const STORAGE_KEY = "forgetting-curve-app:v1";
const DAY_MS = 24 * 60 * 60 * 1000;
const DEFAULT_STABILITY = 1; // 日: 初見のカードは1日で保持率90%まで落ちると仮定
const RETENTION_BASE = 0.9;  // R(t) = RETENTION_BASE ^ (t / S) -- t=S日で保持率がRETENTION_BASEになる
const MIN_STABILITY_DAYS = 1;
const RATING_FACTORS = { again: 0.3, hard: 1.2, good: 2.5, easy: 3.5 };

const rootStyles = getComputedStyle(document.documentElement);
function cssVar(name) { return rootStyles.getPropertyValue(name).trim(); }
const COLORS = {
  hairline: cssVar("--hairline"),
  baseline: cssVar("--baseline"),
  inkFaded: cssVar("--ink-faded"),
  inkMuted: cssVar("--ink-muted"),
  inkStrong: cssVar("--ink-strong"),
  accent: cssVar("--accent"),
  accentWash: cssVar("--accent-wash"),
  surfaceRaised: cssVar("--surface-raised"),
};

/* ==========================================================================
   永続化・シードデータ
   ========================================================================== */
function loadState() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (raw) return JSON.parse(raw);
  } catch (e) { console.warn("状態の読み込みに失敗しました", e); }
  return null;
}

function saveState() {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
}

function seedDemoCards() {
  const now = Date.now();
  const mk = (front, back, createdDaysAgo, lastReviewedDaysAgo, stability, reviewCount) => {
    const createdAt = now - createdDaysAgo * DAY_MS;
    const lastReviewedAt = lastReviewedDaysAgo == null ? null : now - lastReviewedDaysAgo * DAY_MS;
    const anchor = lastReviewedAt ?? createdAt;
    return {
      id: crypto.randomUUID(),
      front, back,
      createdAt,
      lastReviewedAt,
      stability,
      dueAt: anchor + stability * DAY_MS,
      reviewCount,
      lapses: 0,
    };
  };
  return [
    mk("レモンを英語で？", "Lemon", 10, 6, 5, 2),
    mk("TCPとUDPの違いは？", "TCP: 接続指向・信頼性重視 / UDP: 非接続・低遅延重視", 8, 1, 9, 3),
    mk("光合成の反応式は？", "6CO2 + 6H2O + 光エネルギー → C6H12O6 + 6O2", 5, 5, 1, 0),
    mk("エビングハウスの忘却曲線の式は？", "R(t) = 0.9^(t / S)　(R: 保持率, t: 経過日数, S: 安定度)", 2, 2, 1, 0),
  ];
}

let state = loadState() || { cards: seedDemoCards() };
if (!Array.isArray(state.cards)) state.cards = [];
saveState();

/* ==========================================================================
   SRS / 忘却曲線ロジック
   ========================================================================== */
function anchorTime(card) {
  return card.lastReviewedAt ?? card.createdAt;
}

function retentionAt(card, atMs) {
  const t = (atMs - anchorTime(card)) / DAY_MS;
  if (t <= 0) return 1;
  return Math.pow(RETENTION_BASE, t / card.stability);
}

function previewIntervalDays(card, rating) {
  const newStability = Math.max(MIN_STABILITY_DAYS, card.stability * RATING_FACTORS[rating]);
  return Math.max(1, Math.round(newStability));
}

function scheduleReview(card, rating) {
  const now = Date.now();
  card.stability = Math.max(MIN_STABILITY_DAYS, card.stability * RATING_FACTORS[rating]);
  card.lastReviewedAt = now;
  card.dueAt = now + card.stability * DAY_MS;
  card.reviewCount += 1;
  if (rating === "again") card.lapses += 1;
  saveState();
}

function getDueCards() {
  const now = Date.now();
  return state.cards.filter((c) => c.dueAt <= now).sort((a, b) => a.dueAt - b.dueAt);
}

/* ==========================================================================
   カードCRUD
   ========================================================================== */
function addCard(front, back) {
  const now = Date.now();
  state.cards.push({
    id: crypto.randomUUID(),
    front, back,
    createdAt: now,
    lastReviewedAt: null,
    stability: DEFAULT_STABILITY,
    dueAt: now, // 追加直後から復習対象にする
    reviewCount: 0,
    lapses: 0,
  });
  saveState();
}

function updateCard(id, front, back) {
  const card = state.cards.find((c) => c.id === id);
  if (card) { card.front = front; card.back = back; saveState(); }
}

function deleteCard(id) {
  state.cards = state.cards.filter((c) => c.id !== id);
  saveState();
}

/* ==========================================================================
   ユーティリティ
   ========================================================================== */
function startOfDay(ms) {
  const d = new Date(ms);
  d.setHours(0, 0, 0, 0);
  return d.getTime();
}

function formatShortDate(ms) {
  const d = new Date(ms);
  return `${d.getMonth() + 1}/${d.getDate()}`;
}

function formatDate(ms) {
  const diffDays = Math.round((startOfDay(ms) - startOfDay(Date.now())) / DAY_MS);
  const dateStr = formatShortDate(ms);
  if (diffDays <= 0) return `今日 (${dateStr})`;
  if (diffDays === 1) return `明日 (${dateStr})`;
  return `${dateStr}（${diffDays}日後）`;
}

function truncate(str, n) {
  return str.length > n ? `${str.slice(0, n)}…` : str;
}

function escapeHtml(str) {
  return str.replace(/[&<>"']/g, (ch) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[ch]));
}

function hexToRgb(hex) {
  const n = parseInt(hex.replace("#", ""), 16);
  return { r: (n >> 16) & 255, g: (n >> 8) & 255, b: n & 255 };
}

function inkForRetention(r) {
  const a = hexToRgb(COLORS.inkStrong);
  const b = hexToRgb(COLORS.inkFaded);
  const mix = (x, y) => Math.round(y + (x - y) * r);
  return `rgb(${mix(a.r, b.r)}, ${mix(a.g, b.g)}, ${mix(a.b, b.b)})`;
}

/* ==========================================================================
   画面切り替え
   ========================================================================== */
function showView(name) {
  document.querySelectorAll(".view").forEach((v) => { v.hidden = v.dataset.view !== name; });
  document.querySelectorAll(".tab").forEach((t) => { t.setAttribute("aria-selected", String(t.dataset.view === name)); });
  if (name === "dashboard") renderDashboard();
  else if (name === "deck") renderDeck();
  else if (name === "review") startReviewSession();
}

document.querySelectorAll(".tab").forEach((tab) => {
  tab.addEventListener("click", () => showView(tab.dataset.view));
});
document.querySelectorAll("[data-goto]").forEach((el) => {
  el.addEventListener("click", () => showView(el.dataset.goto));
});

/* ==========================================================================
   ダッシュボード
   ========================================================================== */
function renderDashboard() {
  const now = Date.now();
  const due = getDueCards();
  document.getElementById("due-count-number").textContent = due.length;
  document.getElementById("stat-total").textContent = state.cards.length;
  document.getElementById("stat-done-today").textContent = state.cards.filter(
    (c) => c.lastReviewedAt && startOfDay(c.lastReviewedAt) === startOfDay(now)
  ).length;
  document.getElementById("start-review-btn").disabled = due.length === 0;

  buildChart(state.cards, document.getElementById("curve-chart"));
  buildCurveTable(state.cards);
}

document.getElementById("start-review-btn").addEventListener("click", () => showView("review"));

document.getElementById("toggle-table-view").addEventListener("click", (e) => {
  const wrap = document.getElementById("curve-table-wrap");
  const willShow = wrap.hidden;
  wrap.hidden = !willShow;
  e.currentTarget.setAttribute("aria-expanded", String(willShow));
  e.currentTarget.textContent = willShow ? "グラフに戻る" : "表で見る";
});

function buildCurveTable(cards) {
  const tbody = document.querySelector("#curve-table tbody");
  tbody.innerHTML = "";
  const now = Date.now();
  cards.slice().sort((a, b) => a.dueAt - b.dueAt).forEach((card) => {
    const r = retentionAt(card, now);
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${escapeHtml(truncate(card.front, 24))}</td>
      <td>${card.lastReviewedAt ? formatShortDate(card.lastReviewedAt) : "未復習"}</td>
      <td>${Math.round(r * 100)}%</td>
      <td>${formatDate(card.dueAt)}</td>
    `;
    tbody.appendChild(tr);
  });
}

/* ---------- 忘却曲線チャート(SVG自作、外部ライブラリ非依存) ---------- */
function buildChart(cards, container) {
  container.innerHTML = "";

  if (cards.length === 0) {
    const p = document.createElement("p");
    p.className = "empty-state";
    p.textContent = "カードを追加すると、ここに忘却曲線が表示されます。";
    container.appendChild(p);
    return;
  }

  const now = Date.now();
  const earliest = Math.min(...cards.map(anchorTime));
  const windowStart = Math.max(earliest, now - 14 * DAY_MS);
  const windowEnd = now + 7 * DAY_MS;
  const span = Math.max(windowEnd - windowStart, DAY_MS);

  const W = 640, H = 260;
  const pad = { top: 20, right: 20, bottom: 30, left: 38 };
  const plotW = W - pad.left - pad.right;
  const plotH = H - pad.top - pad.bottom;

  const xForTime = (ms) => pad.left + ((ms - windowStart) / span) * plotW;
  const yForRetention = (r) => pad.top + (1 - r) * plotH;

  const SAMPLES = 96;
  const sampleTimes = Array.from({ length: SAMPLES + 1 }, (_, i) => windowStart + (span * i) / SAMPLES);

  const NS = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(NS, "svg");
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  svg.setAttribute("role", "presentation");

  // Y軸グリッド + ラベル(0/25/50/75/100%)
  [0, 0.25, 0.5, 0.75, 1].forEach((r) => {
    const y = yForRetention(r);
    const line = document.createElementNS(NS, "line");
    line.setAttribute("x1", pad.left); line.setAttribute("x2", W - pad.right);
    line.setAttribute("y1", y); line.setAttribute("y2", y);
    line.setAttribute("stroke", COLORS.hairline);
    line.setAttribute("stroke-width", "1");
    svg.appendChild(line);

    const label = document.createElementNS(NS, "text");
    label.setAttribute("x", pad.left - 8);
    label.setAttribute("y", y + 3);
    label.setAttribute("text-anchor", "end");
    label.setAttribute("class", "chart-axis-label");
    label.setAttribute("fill", COLORS.inkMuted);
    label.textContent = `${Math.round(r * 100)}%`;
    svg.appendChild(label);
  });

  // X軸ラベル(日付)
  const TICKS = 5;
  for (let i = 0; i <= TICKS; i++) {
    const t = windowStart + (span * i) / TICKS;
    const x = xForTime(t);
    const label = document.createElementNS(NS, "text");
    label.setAttribute("x", x);
    label.setAttribute("y", H - 8);
    label.setAttribute("text-anchor", i === 0 ? "start" : i === TICKS ? "end" : "middle");
    label.setAttribute("class", "chart-axis-label");
    label.setAttribute("fill", COLORS.inkMuted);
    label.textContent = formatShortDate(t);
    svg.appendChild(label);
  }

  // カード個別の忘却曲線(控えめ・地の色)
  cards.forEach((card) => {
    const points = [];
    sampleTimes.forEach((t) => {
      if (t < anchorTime(card)) return;
      points.push(`${xForTime(t)},${yForRetention(retentionAt(card, t))}`);
    });
    if (points.length < 2) return;
    const line = document.createElementNS(NS, "polyline");
    line.setAttribute("points", points.join(" "));
    line.setAttribute("fill", "none");
    line.setAttribute("stroke", COLORS.inkFaded);
    line.setAttribute("stroke-width", "1.5");
    line.setAttribute("stroke-linecap", "round");
    line.setAttribute("stroke-linejoin", "round");
    line.setAttribute("opacity", "0.75");
    svg.appendChild(line);
  });

  // 全体平均の忘却曲線(強調・アクセント色) -- このチャートの主役
  const avgPoints = sampleTimes.map((t) => {
    const values = cards.filter((c) => t >= anchorTime(c)).map((c) => retentionAt(c, t));
    const avg = values.length ? values.reduce((a, b) => a + b, 0) / values.length : 1;
    return { x: xForTime(t), y: yForRetention(avg), t, avg };
  });

  const areaPoints = avgPoints.map((p) => `${p.x},${p.y}`).join(" ");
  const area = document.createElementNS(NS, "polygon");
  area.setAttribute("points", `${pad.left},${pad.top + plotH} ${areaPoints} ${W - pad.right},${pad.top + plotH}`);
  area.setAttribute("fill", COLORS.accentWash);
  area.setAttribute("stroke", "none");
  svg.appendChild(area);

  const avgLine = document.createElementNS(NS, "polyline");
  avgLine.setAttribute("points", avgPoints.map((p) => `${p.x},${p.y}`).join(" "));
  avgLine.setAttribute("fill", "none");
  avgLine.setAttribute("stroke", COLORS.accent);
  avgLine.setAttribute("stroke-width", "3");
  avgLine.setAttribute("stroke-linecap", "round");
  avgLine.setAttribute("stroke-linejoin", "round");
  svg.appendChild(avgLine);

  // 「今」の縦線(実績と予測の境界)
  const nowX = xForTime(now);
  const nowLine = document.createElementNS(NS, "line");
  nowLine.setAttribute("x1", nowX); nowLine.setAttribute("x2", nowX);
  nowLine.setAttribute("y1", pad.top); nowLine.setAttribute("y2", pad.top + plotH);
  nowLine.setAttribute("stroke", COLORS.baseline);
  nowLine.setAttribute("stroke-width", "1");
  nowLine.setAttribute("stroke-dasharray", "3 3");
  svg.appendChild(nowLine);

  const nowTag = document.createElementNS(NS, "text");
  nowTag.setAttribute("x", nowX);
  nowTag.setAttribute("y", pad.top - 8);
  nowTag.setAttribute("text-anchor", "middle");
  nowTag.setAttribute("class", "chart-axis-label");
  nowTag.setAttribute("fill", COLORS.inkMuted);
  nowTag.textContent = "今";
  svg.appendChild(nowTag);

  // 「いま」の直接ラベル(平均曲線上の1点だけをラベリング)
  let nowPoint = avgPoints[0];
  let bestDiff = Infinity;
  for (const p of avgPoints) {
    const diff = Math.abs(p.t - now);
    if (diff < bestDiff) { bestDiff = diff; nowPoint = p; }
  }
  const ring = document.createElementNS(NS, "circle");
  ring.setAttribute("cx", nowPoint.x); ring.setAttribute("cy", nowPoint.y);
  ring.setAttribute("r", "7");
  ring.setAttribute("fill", COLORS.surfaceRaised);
  svg.appendChild(ring);

  const dot = document.createElementNS(NS, "circle");
  dot.setAttribute("cx", nowPoint.x); dot.setAttribute("cy", nowPoint.y);
  dot.setAttribute("r", "5");
  dot.setAttribute("fill", COLORS.accent);
  svg.appendChild(dot);

  const valueLabel = document.createElementNS(NS, "text");
  valueLabel.setAttribute("x", nowPoint.x);
  valueLabel.setAttribute("y", nowPoint.y - 14);
  valueLabel.setAttribute("text-anchor", nowPoint.x > W - 70 ? "end" : "middle");
  valueLabel.setAttribute("class", "chart-value-label");
  valueLabel.setAttribute("fill", COLORS.inkStrong);
  valueLabel.textContent = `いま ${Math.round(nowPoint.avg * 100)}%`;
  svg.appendChild(valueLabel);

  container.appendChild(svg);

  // 平均曲線の描画アニメーション(1箇所だけの演出、reduced-motionは尊重)
  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  if (!reduceMotion && typeof avgLine.getTotalLength === "function") {
    const len = avgLine.getTotalLength();
    avgLine.style.strokeDasharray = `${len}`;
    avgLine.style.strokeDashoffset = `${len}`;
    avgLine.style.transition = "stroke-dashoffset 900ms cubic-bezier(0.2, 0.7, 0.3, 1)";
    requestAnimationFrame(() => requestAnimationFrame(() => { avgLine.style.strokeDashoffset = "0"; }));
  }

  attachChartInteraction(svg, container, avgPoints, pad, W, H);
}

function attachChartInteraction(svg, container, avgPoints, pad, W, H) {
  const NS = svg.namespaceURI;
  const crosshair = document.createElementNS(NS, "line");
  crosshair.setAttribute("y1", pad.top);
  crosshair.setAttribute("y2", H - pad.bottom);
  crosshair.setAttribute("stroke", COLORS.inkMuted);
  crosshair.setAttribute("stroke-width", "1");
  crosshair.style.display = "none";
  svg.appendChild(crosshair);

  container.style.position = "relative";
  const tooltip = document.createElement("div");
  tooltip.className = "chart-tooltip";
  tooltip.hidden = true;
  container.appendChild(tooltip);

  function hide() { crosshair.style.display = "none"; tooltip.hidden = true; }

  function handleMove(evt) {
    const rect = svg.getBoundingClientRect();
    const clientX = evt.touches ? evt.touches[0].clientX : evt.clientX;
    const svgX = ((clientX - rect.left) / rect.width) * W;
    if (svgX < pad.left || svgX > W - pad.right) { hide(); return; }

    let nearest = avgPoints[0];
    let bestDist = Infinity;
    for (const p of avgPoints) {
      const d = Math.abs(p.x - svgX);
      if (d < bestDist) { bestDist = d; nearest = p; }
    }
    crosshair.setAttribute("x1", nearest.x);
    crosshair.setAttribute("x2", nearest.x);
    crosshair.style.display = "block";

    tooltip.hidden = false;
    tooltip.textContent = `${formatShortDate(nearest.t)}・平均保持率 ${Math.round(nearest.avg * 100)}%`;
    tooltip.style.left = `${(nearest.x / W) * 100}%`;
    tooltip.style.top = `${(nearest.y / H) * 100}%`;
  }

  svg.addEventListener("pointermove", handleMove);
  svg.addEventListener("pointerleave", hide);
}

/* ==========================================================================
   復習セッション
   ========================================================================== */
let reviewQueue = [];
let reviewIndex = 0;

function startReviewSession() {
  reviewQueue = getDueCards().map((c) => c.id);
  reviewIndex = 0;
  renderReviewCurrent();
}

function renderReviewCurrent() {
  const empty = document.getElementById("review-empty");
  const session = document.getElementById("review-session");

  if (reviewIndex >= reviewQueue.length) {
    session.hidden = true;
    empty.hidden = false;
    const finishedSession = reviewQueue.length > 0;
    document.getElementById("review-empty-text").textContent = finishedSession
      ? "今日の復習はすべて終わりました。おつかれさまでした。"
      : "いま復習が必要なカードはありません。";
    const btn = document.getElementById("review-empty-btn");
    btn.textContent = finishedSession ? "ダッシュボードに戻る" : "カードを追加する";
    btn.dataset.goto = finishedSession ? "dashboard" : "deck";
    return;
  }

  empty.hidden = true;
  session.hidden = false;

  const card = state.cards.find((c) => c.id === reviewQueue[reviewIndex]);
  document.getElementById("review-progress-text").textContent = `${reviewIndex + 1} / ${reviewQueue.length}`;

  const flashcard = document.getElementById("flashcard");
  flashcard.classList.remove("is-flipped");
  flashcard.querySelector(".flashcard-front").textContent = card.front;
  flashcard.querySelector(".flashcard-back").textContent = card.back;
  document.getElementById("rating-buttons").hidden = true;

  document.getElementById("preview-hard").textContent = `${previewIntervalDays(card, "hard")}日後`;
  document.getElementById("preview-good").textContent = `${previewIntervalDays(card, "good")}日後`;
  document.getElementById("preview-easy").textContent = `${previewIntervalDays(card, "easy")}日後`;
}

document.getElementById("flashcard").addEventListener("click", (e) => {
  const flipped = e.currentTarget.classList.toggle("is-flipped");
  if (flipped) document.getElementById("rating-buttons").hidden = false;
});

document.getElementById("rating-buttons").addEventListener("click", (e) => {
  const btn = e.target.closest(".rating-button");
  if (!btn) return;
  const rating = btn.dataset.rating;
  const card = state.cards.find((c) => c.id === reviewQueue[reviewIndex]);
  scheduleReview(card, rating);
  if (rating === "again") {
    const insertAt = Math.min(reviewQueue.length, reviewIndex + 4);
    reviewQueue.splice(insertAt, 0, card.id);
  }
  reviewIndex += 1;
  renderReviewCurrent();
});

/* ==========================================================================
   カード一覧・管理
   ========================================================================== */
const cardForm = document.getElementById("card-form");
const frontInput = document.getElementById("card-front");
const backInput = document.getElementById("card-back");
const editIdInput = document.getElementById("card-edit-id");
const submitBtn = document.getElementById("card-form-submit");
const cancelBtn = document.getElementById("card-form-cancel");

function resetCardForm() {
  cardForm.reset();
  editIdInput.value = "";
  submitBtn.textContent = "カードを追加";
  cancelBtn.hidden = true;
}

cardForm.addEventListener("submit", (e) => {
  e.preventDefault();
  const front = frontInput.value.trim();
  const back = backInput.value.trim();
  if (!front || !back) return;
  if (editIdInput.value) {
    updateCard(editIdInput.value, front, back);
  } else {
    addCard(front, back);
  }
  resetCardForm();
  renderDeck();
});

cancelBtn.addEventListener("click", resetCardForm);

function renderDeck() {
  const list = document.getElementById("card-list");
  const emptyMsg = document.getElementById("card-list-empty");
  list.innerHTML = "";

  if (state.cards.length === 0) {
    emptyMsg.hidden = false;
    return;
  }
  emptyMsg.hidden = true;

  const now = Date.now();
  state.cards.slice().sort((a, b) => a.dueAt - b.dueAt).forEach((card) => {
    const r = retentionAt(card, now);
    const li = document.createElement("li");
    li.className = "card-row";
    li.innerHTML = `
      <div class="card-row-text">
        <div class="card-row-front" style="color:${inkForRetention(r)}">${escapeHtml(card.front)}</div>
        <div class="card-row-meta">次回復習: ${formatDate(card.dueAt)}（保持率 約${Math.round(r * 100)}%）</div>
      </div>
      <div class="card-row-actions">
        <button type="button" data-action="edit" data-id="${card.id}">編集</button>
        <button type="button" data-action="delete" data-id="${card.id}">削除</button>
      </div>
    `;
    list.appendChild(li);
  });
}

document.getElementById("card-list").addEventListener("click", (e) => {
  const btn = e.target.closest("button[data-action]");
  if (!btn) return;
  const id = btn.dataset.id;
  if (btn.dataset.action === "delete") {
    if (confirm("このカードを削除しますか？")) {
      deleteCard(id);
      renderDeck();
    }
  } else if (btn.dataset.action === "edit") {
    const card = state.cards.find((c) => c.id === id);
    frontInput.value = card.front;
    backInput.value = card.back;
    editIdInput.value = card.id;
    submitBtn.textContent = "カードを更新";
    cancelBtn.hidden = false;
    frontInput.focus();
  }
});

/* ==========================================================================
   初期表示
   ========================================================================== */
showView("dashboard");
