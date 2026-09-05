import { getCards, getDueCards, scheduleReview, overwriteCardScheduling } from "./store.js";
import { previewIntervalDays } from "./srs.js";

let reviewQueue = [];
let reviewIndex = 0;
let lastAction = null; // 直前の評価を取り消すためのスナップショット

const KEY_TO_RATING = { "1": "again", "2": "hard", "3": "good", "4": "easy" };

export function startReviewSession() {
  reviewQueue = getDueCards().map((c) => c.id);
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

  const card = getCards().find((c) => c.id === reviewQueue[reviewIndex]);
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

  document.addEventListener("keydown", handleKeydown);
}
