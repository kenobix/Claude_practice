import { CARD_SETS_MANIFEST_URL, CARD_SETS_BASE_URL } from "./config.js";

// data/sets/ 以下のJSONファイルをmanifest.json経由で読み込む。
// 新しいセットを追加する場合はJSONファイルを置いてmanifest.jsonに
// ファイル名を追記するだけでよい(READMEの「カードセットの追加方法」参照)。
export async function loadCardSets() {
  const manifestRes = await fetch(CARD_SETS_MANIFEST_URL);
  if (!manifestRes.ok) throw new Error(`manifest.jsonの取得に失敗しました (${manifestRes.status})`);
  const filenames = await manifestRes.json();

  const sets = await Promise.all(filenames.map(async (filename) => {
    const res = await fetch(`${CARD_SETS_BASE_URL}${filename}`);
    if (!res.ok) throw new Error(`${filename}の取得に失敗しました (${res.status})`);
    return res.json();
  }));

  return sets;
}
