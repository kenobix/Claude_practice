import { DAY_MS, COLORS, CARD_SETS_BASE_URL } from "./config.js";

export function startOfDay(ms) {
  const d = new Date(ms);
  d.setHours(0, 0, 0, 0);
  return d.getTime();
}

export function formatShortDate(ms) {
  const d = new Date(ms);
  return `${d.getMonth() + 1}/${d.getDate()}`;
}

export function formatDate(ms) {
  const diffDays = Math.round((startOfDay(ms) - startOfDay(Date.now())) / DAY_MS);
  const dateStr = formatShortDate(ms);
  if (diffDays === 0) return `今日 (${dateStr})`;
  if (diffDays === 1) return `明日 (${dateStr})`;
  if (diffDays < 0) return `${dateStr}（${Math.abs(diffDays)}日前）`;
  return `${dateStr}（${diffDays}日後）`;
}

export function truncate(str, n) {
  return str.length > n ? `${str.slice(0, n)}…` : str;
}

export function escapeHtml(str) {
  return str.replace(/[&<>"']/g, (ch) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[ch]));
}

function hexToRgb(hex) {
  const n = parseInt(hex.replace("#", ""), 16);
  return { r: (n >> 16) & 255, g: (n >> 8) & 255, b: n & 255 };
}

export function inkForRetention(r) {
  const a = hexToRgb(COLORS.inkStrong);
  const b = hexToRgb(COLORS.inkFaded);
  const mix = (x, y) => Math.round(y + (x - y) * r);
  return `rgb(${mix(a.r, b.r)}, ${mix(a.g, b.g)}, ${mix(a.b, b.b)})`;
}

// カードのimageフィールド(ファイル名)から実際のパスを組み立てる。
// setIdがない(手入力の)カードには画像を付けられない。
export function imagePathFor(card) {
  if (!card.image || !card.setId) return null;
  return `${CARD_SETS_BASE_URL}${card.setId}/images/${card.image}`;
}
