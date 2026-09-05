import { getCards, getDueCards } from "./store.js";
import { buildChart, buildCurveTable } from "./chart.js";
import { startOfDay } from "./utils.js";

export function renderDashboard() {
  const now = Date.now();
  const cards = getCards();
  const due = getDueCards();

  document.getElementById("due-count-number").textContent = due.length;
  document.getElementById("stat-total").textContent = cards.length;
  document.getElementById("stat-done-today").textContent = cards.filter(
    (c) => c.lastReviewedAt && startOfDay(c.lastReviewedAt) === startOfDay(now)
  ).length;
  document.getElementById("start-review-btn").disabled = due.length === 0;

  buildChart(cards, document.getElementById("curve-chart"));
  buildCurveTable(cards);
}

export function initDashboard() {
  document.getElementById("toggle-table-view").addEventListener("click", (e) => {
    const wrap = document.getElementById("curve-table-wrap");
    const willShow = wrap.hidden;
    wrap.hidden = !willShow;
    e.currentTarget.setAttribute("aria-expanded", String(willShow));
    e.currentTarget.textContent = willShow ? "グラフに戻る" : "表で見る";
  });
}
