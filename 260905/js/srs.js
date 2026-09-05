import { DAY_MS, RETENTION_BASE, MIN_STABILITY_DAYS, RATING_FACTORS } from "./config.js";

export function anchorTime(card) {
  return card.lastReviewedAt ?? card.createdAt;
}

// R(t) = RETENTION_BASE ^ (t / S) -- t=S日で保持率がRETENTION_BASEになるよう定義した近似式
export function retentionAt(card, atMs) {
  const t = (atMs - anchorTime(card)) / DAY_MS;
  if (t <= 0) return 1;
  return Math.pow(RETENTION_BASE, t / card.stability);
}

export function nextStability(currentStability, rating) {
  return Math.max(MIN_STABILITY_DAYS, currentStability * RATING_FACTORS[rating]);
}

export function previewIntervalDays(card, rating) {
  return Math.max(1, Math.round(nextStability(card.stability, rating)));
}
