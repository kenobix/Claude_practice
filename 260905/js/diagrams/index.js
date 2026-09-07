// 図解カード用SVGダイアグラムのレジストリ+ディスパッチャ。
// カードJSONの `diagram` フィールド(キー名)から対応するビルダー関数を引いて描画する。
// AI生成画像(png)を置き換える構造図はここに追加していく。
import { NS } from "./helpers.js";
import { bookkeepingBasicsDiagrams } from "./bookkeeping-basics.js";
import { costAccountingCoreDiagrams } from "./cost-accounting-core.js";
import { assetsValuationDiagrams } from "./assets-valuation.js";
import { consolidationDiagrams } from "./consolidation.js";
import { costAccountingMethodsDiagrams } from "./cost-accounting-methods.js";

const REGISTRY = {
  ...bookkeepingBasicsDiagrams,
  ...costAccountingCoreDiagrams,
  ...assetsValuationDiagrams,
  ...consolidationDiagrams,
  ...costAccountingMethodsDiagrams,
};

export const DIAGRAM_KEYS = Object.keys(REGISTRY);

export function hasDiagram(key) {
  return key in REGISTRY;
}

export function buildDiagram(key, container, altText) {
  container.innerHTML = "";
  const builder = REGISTRY[key];
  if (!builder) {
    console.warn(`未知のdiagramキー: ${key}`);
    return;
  }
  const svg = builder();
  if (altText) {
    const title = document.createElementNS(NS, "title");
    title.textContent = altText;
    svg.insertBefore(title, svg.firstChild);
  }
  container.appendChild(svg);
}
