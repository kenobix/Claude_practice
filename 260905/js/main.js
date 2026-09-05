import { loadCardSets } from "./cardSets.js";
import { initStore } from "./store.js";
import { renderDashboard, initDashboard } from "./dashboard.js";
import { renderDeck, initDeck } from "./deck.js";
import { startReviewSession, initReview } from "./review.js";

function showView(name) {
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

async function main() {
  const cardSets = await loadCardSets().catch((err) => {
    console.error("カードセットの読み込みに失敗しました", err);
    return [];
  });

  initStore(cardSets);
  initDashboard();
  initReview();
  initDeck();
  wireNav();

  showView("dashboard");
}

main();
