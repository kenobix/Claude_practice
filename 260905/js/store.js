import { STORAGE_KEY, DAY_MS, DEFAULT_STABILITY } from "./config.js";
import { nextStability } from "./srs.js";

let state = null;
let cardSets = [];
let currentFilter = ""; // "" = すべて、それ以外はセットのid(手入力カードは"custom")

function loadPersistedState() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (raw) return JSON.parse(raw);
  } catch (e) { console.warn("状態の読み込みに失敗しました", e); }
  return null;
}

function saveState() {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
}

function makeCard(front, back, {
  createdAt = Date.now(), lastReviewedAt = null, stability = DEFAULT_STABILITY,
  dueAt = null, reviewCount = 0, lapses = 0, setId = null, unit = null,
  image = null, imageAlt = null,
} = {}) {
  const anchor = lastReviewedAt ?? createdAt;
  return {
    id: crypto.randomUUID(),
    front, back,
    setId, unit,
    image, imageAlt,
    createdAt,
    lastReviewedAt,
    stability,
    dueAt: dueAt ?? anchor + stability * DAY_MS,
    reviewCount,
    lapses,
  };
}

// 初回アクセス時のみ、読み込めた全カードセットをまるごと取り込む。
// 各セットの最初の数枚だけ「数日前に復習した」という履歴を持たせ、
// ダッシュボードの曲線が最初から意味を持つようにする(内容自体は実データ)。
function buildInitialState(loadedCardSets) {
  const now = Date.now();
  const demoSpecs = [
    { daysAgo: 6, stability: 5 },
    { daysAgo: 1, stability: 9 },
    { daysAgo: 5, stability: 1 },
    { daysAgo: 2, stability: 1 },
  ];
  const cards = [];
  loadedCardSets.forEach((set) => {
    set.cards.forEach(({ front, back, unit, image, imageAlt }, i) => {
      const spec = demoSpecs[i];
      if (spec) {
        const lastReviewedAt = now - spec.daysAgo * DAY_MS;
        cards.push(makeCard(front, back, {
          setId: set.id, unit, image, imageAlt,
          createdAt: lastReviewedAt, lastReviewedAt,
          stability: spec.stability, reviewCount: 2,
        }));
      } else {
        cards.push(makeCard(front, back, { setId: set.id, unit, image, imageAlt, dueAt: now }));
      }
    });
  });
  return { cards };
}

// main.jsからカードセットの読み込み後に一度だけ呼ぶ
export function initStore(loadedCardSets) {
  cardSets = loadedCardSets;
  state = loadPersistedState() || buildInitialState(loadedCardSets);
  if (!Array.isArray(state.cards)) state.cards = [];
  saveState();
}

export function getCardSets() {
  return cardSets;
}

export function setFilter(id) {
  currentFilter = id;
}

export function getFilter() {
  return currentFilter;
}

export function getCards() {
  if (!currentFilter) return state.cards;
  return state.cards.filter((c) => (c.setId || "custom") === currentFilter);
}

export function getDueCards() {
  const now = Date.now();
  return getCards().filter((c) => c.dueAt <= now).sort((a, b) => a.dueAt - b.dueAt);
}

export function addCard(front, back) {
  state.cards.push(makeCard(front, back, { dueAt: Date.now() })); // 追加直後から復習対象にする
  saveState();
}

export function updateCard(id, front, back) {
  const card = state.cards.find((c) => c.id === id);
  if (card) { card.front = front; card.back = back; saveState(); }
}

export function deleteCard(id) {
  state.cards = state.cards.filter((c) => c.id !== id);
  saveState();
}

export function scheduleReview(card, rating) {
  const now = Date.now();
  card.stability = nextStability(card.stability, rating);
  card.lastReviewedAt = now;
  card.dueAt = now + card.stability * DAY_MS;
  card.reviewCount += 1;
  if (rating === "again") card.lapses += 1;
  saveState();
}

// 復習の評価を取り消す(元に戻す)ためのフィールド直接上書き
export function overwriteCardScheduling(id, fields) {
  const card = state.cards.find((c) => c.id === id);
  if (!card) return;
  Object.assign(card, fields);
  saveState();
}

// カードセットに含まれる全カードを、まだ持っていないもの(front文字列で重複判定)だけ取り込む。
// 新しく追加されたセットや単元を後から取り込みたい場合に使う。
export function importSet(setId) {
  const set = cardSets.find((s) => s.id === setId);
  if (!set) return { added: 0, skipped: 0 };

  const existingFronts = new Set(state.cards.map((c) => c.front));
  let added = 0;
  let skipped = 0;
  set.cards.forEach(({ front, back, unit, image, imageAlt }) => {
    if (existingFronts.has(front)) { skipped += 1; return; }
    state.cards.push(makeCard(front, back, { setId, unit, image, imageAlt, dueAt: Date.now() }));
    existingFronts.add(front);
    added += 1;
  });
  saveState();
  return { added, skipped };
}
