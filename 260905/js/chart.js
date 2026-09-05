import { DAY_MS, COLORS } from "./config.js";
import { anchorTime, retentionAt } from "./srs.js";
import { formatShortDate, formatDate, truncate } from "./utils.js";

const NS = "http://www.w3.org/2000/svg";

// 忘却曲線チャート(SVG自作、外部ライブラリ非依存)
// 個別カードの曲線(控えめ)+全体平均(強調)+「今」の縦線、を重ねて描画する
export function buildChart(cards, container) {
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

  const svg = document.createElementNS(NS, "svg");
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  svg.setAttribute("role", "presentation");

  drawYAxis(svg, pad, W, yForRetention);
  drawXAxis(svg, pad, H, windowStart, span, xForTime);
  drawIndividualCurves(svg, cards, sampleTimes, xForTime, yForRetention);

  const avgPoints = buildAveragePoints(cards, sampleTimes, xForTime, yForRetention);
  drawAverageArea(svg, avgPoints, pad, plotH, W);
  const avgLine = drawAverageLine(svg, avgPoints);
  drawNowMarker(svg, avgPoints, now, pad, plotH, W);

  container.appendChild(svg);
  animateAverageLine(avgLine);
  attachChartInteraction(svg, container, avgPoints, pad, W, H);
}

function drawYAxis(svg, pad, W, yForRetention) {
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
}

function drawXAxis(svg, pad, H, windowStart, span, xForTime) {
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
}

function drawIndividualCurves(svg, cards, sampleTimes, xForTime, yForRetention) {
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
}

// retentionAtは各カードの復習前(t<=0)なら1を返すため、集計対象のカード数を
// 常に一定に保ったまま平均を取れる(母数が変わって平均が不連続にジャンプするのを防ぐ)
function buildAveragePoints(cards, sampleTimes, xForTime, yForRetention) {
  return sampleTimes.map((t) => {
    const avg = cards.reduce((sum, c) => sum + retentionAt(c, t), 0) / cards.length;
    return { x: xForTime(t), y: yForRetention(avg), t, avg };
  });
}

function drawAverageArea(svg, avgPoints, pad, plotH, W) {
  const areaPoints = avgPoints.map((p) => `${p.x},${p.y}`).join(" ");
  const area = document.createElementNS(NS, "polygon");
  area.setAttribute("points", `${pad.left},${pad.top + plotH} ${areaPoints} ${W - pad.right},${pad.top + plotH}`);
  area.setAttribute("fill", COLORS.accentWash);
  area.setAttribute("stroke", "none");
  svg.appendChild(area);
}

function drawAverageLine(svg, avgPoints) {
  const avgLine = document.createElementNS(NS, "polyline");
  avgLine.setAttribute("points", avgPoints.map((p) => `${p.x},${p.y}`).join(" "));
  avgLine.setAttribute("fill", "none");
  avgLine.setAttribute("stroke", COLORS.accent);
  avgLine.setAttribute("stroke-width", "3");
  avgLine.setAttribute("stroke-linecap", "round");
  avgLine.setAttribute("stroke-linejoin", "round");
  svg.appendChild(avgLine);
  return avgLine;
}

function drawNowMarker(svg, avgPoints, now, pad, plotH, W) {
  const nowX = avgPoints.reduce((closest, p) => (
    Math.abs(p.t - now) < Math.abs(closest.t - now) ? p : closest
  ), avgPoints[0]).x;

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
}

// 平均曲線の描画アニメーション(1箇所だけの演出、reduced-motionは尊重)
function animateAverageLine(avgLine) {
  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  if (reduceMotion || typeof avgLine.getTotalLength !== "function") return;
  const len = avgLine.getTotalLength();
  avgLine.style.strokeDasharray = `${len}`;
  avgLine.style.strokeDashoffset = `${len}`;
  avgLine.style.transition = "stroke-dashoffset 900ms cubic-bezier(0.2, 0.7, 0.3, 1)";
  requestAnimationFrame(() => requestAnimationFrame(() => { avgLine.style.strokeDashoffset = "0"; }));
}

function attachChartInteraction(svg, container, avgPoints, pad, W, H) {
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

export function buildCurveTable(cards) {
  const tbody = document.querySelector("#curve-table tbody");
  tbody.innerHTML = "";
  const now = Date.now();
  cards.slice().sort((a, b) => a.dueAt - b.dueAt).forEach((card) => {
    const r = retentionAt(card, now);
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td></td>
      <td>${card.lastReviewedAt ? formatShortDate(card.lastReviewedAt) : "未復習"}</td>
      <td>${Math.round(r * 100)}%</td>
      <td>${formatDate(card.dueAt)}</td>
    `;
    tr.children[0].textContent = truncate(card.front, 24);
    tbody.appendChild(tr);
  });
}
