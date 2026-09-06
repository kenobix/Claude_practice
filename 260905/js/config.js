export const STORAGE_KEY = "forgetting-curve-app:v1";
export const DAY_MS = 24 * 60 * 60 * 1000;
export const DEFAULT_STABILITY = 1; // 日: 初見のカードは1日で保持率90%まで落ちると仮定
export const RETENTION_BASE = 0.9;  // R(t) = RETENTION_BASE ^ (t / S) -- t=S日で保持率がRETENTION_BASEになる
export const MIN_STABILITY_DAYS = 1;
export const RATING_FACTORS = { again: 0.3, hard: 1.2, good: 2.5, easy: 3.5 };

export const CARD_SETS_MANIFEST_URL = "sets/manifest.json";
export const CARD_SETS_BASE_URL = "sets/";

// 復習は一度に全件出さず、この枚数ずつのセットに区切って出題する(0 = 区切らず全件)
export const REVIEW_BATCH_SIZE_KEY = "forgetting-curve-app:batch-size";
export const DEFAULT_REVIEW_BATCH_SIZE = 10;

const rootStyles = getComputedStyle(document.documentElement);
function cssVar(name) { return rootStyles.getPropertyValue(name).trim(); }

export const COLORS = {
  hairline: cssVar("--hairline"),
  baseline: cssVar("--baseline"),
  inkFaded: cssVar("--ink-faded"),
  inkMuted: cssVar("--ink-muted"),
  inkStrong: cssVar("--ink-strong"),
  accent: cssVar("--accent"),
  accentWash: cssVar("--accent-wash"),
  surfaceRaised: cssVar("--surface-raised"),
};
