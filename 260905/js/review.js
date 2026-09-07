import { getCards, getDueCards, scheduleReview, overwriteCardScheduling } from "./store.js";
import { previewIntervalDays } from "./srs.js";
import { imagePathFor } from "./utils.js";
import { buildDiagram, hasDiagram } from "./diagrams/index.js";
import { REVIEW_BATCH_SIZE_KEY, DEFAULT_REVIEW_BATCH_SIZE } from "./config.js";

let reviewQueue = [];
let reviewIndex = 0;
let lastAction = null; // 直前の評価を取り消すためのスナップショット
let totalDueAtStart = 0; // セット開始時点での復習待ち総数(進捗表示・残り枚数の算出に使う)

const KEY_TO_RATING = { "1": "again", "2": "hard", "3": "good", "4": "easy" };

function loadBatchSize() {
  const stored = localStorage.getItem(REVIEW_BATCH_SIZE_KEY);
  if (stored === null) return DEFAULT_REVIEW_BATCH_SIZE;
  const raw = Number(stored);
  return Number.isFinite(raw) && raw >= 0 ? raw : DEFAULT_REVIEW_BATCH_SIZE;
}

function syncBatchSizeSelect() {
  const select = document.getElementById("review-batch-size");
  select.value = String(loadBatchSize());
}

export function startReviewSession() {
  syncBatchSizeSelect();
  const batchSize = loadBatchSize();
  const dueIds = getDueCards().map((c) => c.id);
  totalDueAtStart = dueIds.length;
  reviewQueue = batchSize > 0 ? dueIds.slice(0, batchSize) : dueIds;
  reviewIndex = 0;
  lastAction = null;
  updateUndoButton();
  renderReviewCurrent();
}

function updateUndoButton() {
  document.getElementById("review-undo-btn").hidden = !lastAction;
}

function renderReviewCurrent() {
  const empty = document.getElementById("review-empty");
  const batchDone = document.getElementById("review-batch-done");
  const session = document.getElementById("review-session");

  if (reviewIndex >= reviewQueue.length) {
    session.hidden = true;
    const finishedSession = reviewQueue.length > 0;
    const remaining = getDueCards().length; // このセット消化後、まだ残っている枚数

    if (finishedSession && remaining > 0) {
      empty.hidden = true;
      batchDone.hidden = false;
      document.getElementById("review-batch-done-text").textContent =
        `このセットの復習が終わりました。残り${remaining}枚あります。`;
      return;
    }

    batchDone.hidden = true;
    empty.hidden = false;
    document.getElementById("review-empty-text").textContent = finishedSession
      ? "今日の復習はすべて終わりました。おつかれさまでした。"
      : "いま復習が必要なカードはありません。";
    const btn = document.getElementById("review-empty-btn");
    btn.textContent = finishedSession ? "ダッシュボードに戻る" : "カードを追加する";
    btn.dataset.goto = finishedSession ? "dashboard" : "deck";
    return;
  }

  empty.hidden = true;
  batchDone.hidden = true;
  session.hidden = false;

  const card = getCards().find((c) => c.id === reviewQueue[reviewIndex]);
  const totalLabel = totalDueAtStart > reviewQueue.length ? `(全${totalDueAtStart}枚中)` : "";
  document.getElementById("review-progress-text").textContent =
    `${reviewIndex + 1} / ${reviewQueue.length} ${totalLabel}`.trim();

  const flashcard = document.getElementById("flashcard");
  flashcard.classList.remove("is-flipped");
  flashcard.querySelector(".flashcard-front").textContent = card.front;
  flashcard.querySelector(".flashcard-back-text").textContent = card.back;

  const imageSrc = imagePathFor(card);
  const img = flashcard.querySelector(".flashcard-image");
  const diagramEl = flashcard.querySelector(".flashcard-diagram");

  if (card.diagram && hasDiagram(card.diagram)) {
    flashcard.classList.add("has-graphic");
    img.hidden = true;
    img.removeAttribute("src");
    diagramEl.hidden = false;
    buildDiagram(card.diagram, diagramEl, card.imageAlt || "");
  } else if (imageSrc) {
    flashcard.classList.add("has-graphic");
    diagramEl.hidden = true;
    diagramEl.innerHTML = "";
    img.onerror = () => { img.hidden = true; flashcard.classList.remove("has-graphic"); };
    img.alt = card.imageAlt || "";
    img.src = imageSrc;
    img.hidden = false;
  } else {
    flashcard.classList.remove("has-graphic");
    img.hidden = true;
    img.removeAttribute("src");
    diagramEl.hidden = true;
    diagramEl.innerHTML = "";
  }

  document.getElementById("rating-buttons").hidden = true;

  document.getElementById("preview-hard").textContent = `${previewIntervalDays(card, "hard")}日後`;
  document.getElementById("preview-good").textContent = `${previewIntervalDays(card, "good")}日後`;
  document.getElementById("preview-easy").textContent = `${previewIntervalDays(card, "easy")}日後`;
}

function rateCurrentCard(rating) {
  const card = getCards().find((c) => c.id === reviewQueue[reviewIndex]);
  if (!card) return;

  lastAction = {
    cardId: card.id,
    prevFields: {
      stability: card.stability,
      lastReviewedAt: card.lastReviewedAt,
      dueAt: card.dueAt,
      reviewCount: card.reviewCount,
      lapses: card.lapses,
    },
    queueSnapshot: reviewQueue.slice(),
    reviewIndexBefore: reviewIndex,
  };

  scheduleReview(card, rating);
  if (rating === "again") {
    const insertAt = Math.min(reviewQueue.length, reviewIndex + 4);
    reviewQueue.splice(insertAt, 0, card.id);
  }
  reviewIndex += 1;
  updateUndoButton();
  renderReviewCurrent();
}

function undoLastRating() {
  if (!lastAction) return;
  overwriteCardScheduling(lastAction.cardId, lastAction.prevFields);
  reviewQueue = lastAction.queueSnapshot;
  reviewIndex = lastAction.reviewIndexBefore;
  lastAction = null;
  updateUndoButton();
  renderReviewCurrent();
}

function isTypingIntoField() {
  const tag = document.activeElement?.tagName;
  return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT";
}

function handleKeydown(e) {
  if (document.getElementById("view-review").hidden || isTypingIntoField()) return;

  const flashcard = document.getElementById("flashcard");
  if (document.getElementById("review-session").hidden) return; // 完了/空状態では反応しない

  if (!flashcard.classList.contains("is-flipped")) {
    if (e.key === " " || e.key === "Enter") {
      e.preventDefault();
      flashcard.click();
    }
    return;
  }

  const rating = KEY_TO_RATING[e.key];
  if (rating) {
    e.preventDefault();
    rateCurrentCard(rating);
  }
}

export function initReview() {
  document.getElementById("flashcard").addEventListener("click", (e) => {
    const flipped = e.currentTarget.classList.toggle("is-flipped");
    if (flipped) document.getElementById("rating-buttons").hidden = false;
  });

  document.getElementById("rating-buttons").addEventListener("click", (e) => {
    const btn = e.target.closest(".rating-button");
    if (!btn) return;
    rateCurrentCard(btn.dataset.rating);
  });

  document.getElementById("review-undo-btn").addEventListener("click", undoLastRating);

  document.getElementById("review-batch-size").addEventListener("change", (e) => {
    localStorage.setItem(REVIEW_BATCH_SIZE_KEY, e.target.value);
  });

  document.getElementById("review-next-batch-btn").addEventListener("click", startReviewSession);

  document.addEventListener("keydown", handleKeydown);
}
