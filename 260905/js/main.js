import { loadCardSets } from "./cardSets.js";
import { initStore, getCardSets, setFilter, getFilter } from "./store.js";
import { escapeHtml } from "./utils.js";
import { renderDashboard, initDashboard } from "./dashboard.js";
import { renderDeck, initDeck } from "./deck.js";
import { startReviewSession, initReview } from "./review.js";

let currentView = "dashboard";

function showView(name) {
  currentView = name;
  document.querySelectorAll(".view").forEach((v) => { v.hidden = v.dataset.view !== name; });
  document.querySelectorAll(".tab").forEach((t) => { t.setAttribute("aria-selected", String(t.dataset.view === name)); });
  if (name === "dashboard") renderDashboard();
  else if (name === "deck") renderDeck();
  else if (name === "review") startReviewSession();
}

function wireNav() {
  document.querySelectorAll(".tab").forEach((tab) => {
    tab.addEventListener("click", () => showView(tab.dataset.view));
  });
  document.querySelectorAll("[data-goto]").forEach((el) => {
    el.addEventListener("click", () => showView(el.dataset.goto));
  });
  document.getElementById("start-review-btn").addEventListener("click", () => showView("review"));
}

// ヘッダーの「学習セット」セレクトで、ダッシュボード/復習/カード一覧の
// 対象を特定のセット(または手入力カード/すべて)に絞り込めるようにする
function wireDatasetSwitcher(cardSets) {
  const select = document.getElementById("dataset-select");
  const options = [`<option value="">すべてのカード</option>`];
  cardSets.forEach((s) => options.push(`<option value="${escapeHtml(s.id)}">${escapeHtml(s.name)}</option>`));
  options.push(`<option value="custom">自分で追加したカード</option>`);
  select.innerHTML = options.join("");
  select.value = getFilter();

  select.addEventListener("change", () => {
    setFilter(select.value);
    showView(currentView);
  });
}

async function main() {
  const cardSets = await loadCardSets().catch((err) => {
    console.error("カードセットの読み込みに失敗しました", err);
    return [];
  });

  initStore(cardSets);
  wireDatasetSwitcher(getCardSets());
  initDashboard();
  initReview();
  initDeck();
  wireNav();

  showView("dashboard");
}

main();
