import { STORAGE_KEY, DAY_MS, DEFAULT_STABILITY } from "./config.js";
import { nextStability } from "./srs.js";

let state = null;
let cardSets = [];

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

function makeCard(front, back, { createdAt = Date.now(), lastReviewedAt = null, stability = DEFAULT_STABILITY, dueAt = null, reviewCount = 0, lapses = 0 } = {}) {
  const anchor = lastReviewedAt ?? createdAt;
  return {
    id: crypto.randomUUID(),
    front, back,
    createdAt,
    lastReviewedAt,
    stability,
    dueAt: dueAt ?? anchor + stability * DAY_MS,
    reviewCount,
    lapses,
  };
}

// 初回アクセス時のみ、最初のカードセットから数枚をデモとして取り込む。
// 内容は実データ(data/sets/*.json)そのものだが、「数日前に復習した」という
// 復習履歴だけはダッシュボードの曲線をすぐ意味あるものにするための演出。
function buildDemoState() {
  if (!cardSets.length || cardSets[0].cards.length === 0) return { cards: [] };
  const now = Date.now();
  const demoSpecs = [
    { daysAgo: 6, stability: 5 },
    { daysAgo: 1, stability: 9 },
    { daysAgo: 5, stability: 1 },
    { daysAgo: 2, stability: 1 },
  ];
  const sourceCards = cardSets[0].cards.slice(0, demoSpecs.length);
  const cards = sourceCards.map(({ front, back }, i) => {
    const spec = demoSpecs[i];
    const lastReviewedAt = now - spec.daysAgo * DAY_MS;
    return makeCard(front, back, {
      createdAt: lastReviewedAt,
      lastReviewedAt,
      stability: spec.stability,
      reviewCount: 2,
    });
  });
  return { cards };
}

// main.jsからカードセットの読み込み後に一度だけ呼ぶ
export function initStore(loadedCardSets) {
  cardSets = loadedCardSets;
  state = loadPersistedState() || buildDemoState();
  if (!Array.isArray(state.cards)) state.cards = [];
  saveState();
}

export function getCardSets() {
  return cardSets;
}

export function getCards() {
  return state.cards;
}

export function getDueCards() {
  const now = Date.now();
  return state.cards.filter((c) => c.dueAt <= now).sort((a, b) => a.dueAt - b.dueAt);
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

// カードセットに含まれる全カードを、まだ持っていないもの(front文字列で重複判定)だけ取り込む
export function importSet(setId) {
  const set = cardSets.find((s) => s.id === setId);
  if (!set) return { added: 0, skipped: 0 };

  const existingFronts = new Set(state.cards.map((c) => c.front));
  let added = 0;
  let skipped = 0;
  set.cards.forEach(({ front, back }) => {
    if (existingFronts.has(front)) { skipped += 1; return; }
    state.cards.push(makeCard(front, back, { dueAt: Date.now() }));
    existingFronts.add(front);
    added += 1;
  });
  saveState();
  return { added, skipped };
}
