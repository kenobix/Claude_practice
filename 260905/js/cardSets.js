import { CARD_SETS_MANIFEST_URL, CARD_SETS_BASE_URL } from "./config.js";

async function fetchJson(url) {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`${url}の取得に失敗しました (${res.status})`);
  return res.json();
}

// sets/<id>/meta.json が単元ファイル名の一覧を持ち、各単元ファイルが
// { unit, cards } を持つ。カードが増えても1ファイルが肥大化しないよう、
// 試験(セット)ごと・単元ごとにファイルを分けている。
// 新しいセットを追加する場合はREADMEの「カードセットの追加方法」を参照。
async function loadCardSet(setId) {
  const meta = await fetchJson(`${CARD_SETS_BASE_URL}${setId}/meta.json`);
  const units = await Promise.all(
    meta.units.map((filename) => fetchJson(`${CARD_SETS_BASE_URL}${setId}/${filename}`))
  );
  const cards = units.flatMap((u) => u.cards.map((c) => ({ ...c, unit: u.unit })));
  return { id: meta.id, name: meta.name, units: units.map((u) => u.unit), cards };
}

export async function loadCardSets() {
  const setIds = await fetchJson(CARD_SETS_MANIFEST_URL);
  return Promise.all(setIds.map(loadCardSet));
}
