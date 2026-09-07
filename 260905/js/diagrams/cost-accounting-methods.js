// 工業簿記(原価計算)の各種計算方法まわりの図解。
// AI生成画像で「製造間接費配賦差異のバーグラフ」「部門別配賦のA/B/C部門フロー」
// といったテンプレートパネルが無関係なテーマに混入した反省を踏まえ、
// 既存のchart.js/helpers.jsと同じ「createElementNSのみ・COLORSトークンのみ」の
// 方針で、本ファイルの6論点それぞれを独立に手書きする。
import { COLORS } from "../config.js";
import {
  NS, createSvg, drawBox, drawArrow, drawLines, drawTable, yen,
} from "./helpers.js";

const W = 640;

function mkLine(svg, { x1, y1, x2, y2, stroke, strokeWidth = 2, dashed = false }) {
  const line = document.createElementNS(NS, "line");
  line.setAttribute("x1", x1); line.setAttribute("y1", y1);
  line.setAttribute("x2", x2); line.setAttribute("y2", y2);
  line.setAttribute("stroke", stroke);
  line.setAttribute("stroke-width", strokeWidth);
  if (dashed) line.setAttribute("stroke-dasharray", "4 3");
  svg.appendChild(line);
  return line;
}

function mkCircle(svg, { cx, cy, r, fill }) {
  const circle = document.createElementNS(NS, "circle");
  circle.setAttribute("cx", cx); circle.setAttribute("cy", cy);
  circle.setAttribute("r", r); circle.setAttribute("fill", fill);
  svg.appendChild(circle);
  return circle;
}

function mkRect(svg, { x, y, w, h, fill, stroke, strokeWidth = 1 }) {
  const rect = document.createElementNS(NS, "rect");
  rect.setAttribute("x", x); rect.setAttribute("y", y);
  rect.setAttribute("width", w); rect.setAttribute("height", h);
  rect.setAttribute("fill", fill);
  if (stroke) { rect.setAttribute("stroke", stroke); rect.setAttribute("stroke-width", strokeWidth); }
  svg.appendChild(rect);
  return rect;
}

// ---------------------------------------------------------------------------
// 1. 製造間接費配賦差異の分析(シュラッター図: 予算差異と操業度差異)
// ---------------------------------------------------------------------------
function buildManufacturingOverheadVariance() {
  const H = 380;
  const svg = createSvg(W, H);

  drawLines(svg, {
    x: W / 2, y: 22, lines: ["製造間接費配賦差異の分析(シュラッター図)"],
    fontSize: 15, fontWeight: "700", fill: COLORS.inkStrong,
  });

  const pad = { top: 40, right: 30, bottom: 70, left: 80 };
  const plotW = W - pad.left - pad.right;
  const plotH = H - pad.top - pad.bottom;

  // 基礎データ(すべてここから計算し、実際発生額・予算許容額・予定配賦額を
  // 独立にタイプしない)。
  const fixedBudget = 120000; // 固定費予算(月額)
  const variableRate = 400; // 変動費率(1時間あたり)
  const normalVolume = 500; // 基準操業度(時間)
  // 予定配賦率 = (固定費予算 + 変動費率×基準操業度) / 基準操業度
  const appliedRate = (fixedBudget + variableRate * normalVolume) / normalVolume;

  // 基準操業度から離れた値にすることで、実際操業度周辺の注記群(3点・2つの差異
  // ブラケット)と、右端の基準操業度目盛り・線ラベルとが横方向に重ならないようにする。
  const actualVolume = 380; // 実際操業度(時間)
  const actualCost = 280000; // 実際発生額(実際に発生した製造間接費)

  const budgetAllowance = variableRate * actualVolume + fixedBudget; // 予算許容額
  const appliedCost = appliedRate * actualVolume; // 予定配賦額

  const budgetVariance = budgetAllowance - actualCost; // 予算差異(実際発生額との差)
  const volumeVariance = appliedCost - budgetAllowance; // 操業度差異(予算許容額との差)

  // xMaxをnormalVolume/actualVolumeより十分大きく取り、右端の線ラベル・基準操業度の
  // 目盛り・実際操業度でのブラケット注記が横方向に重ならないよう間隔を確保する。
  const xMax = normalVolume * 1.3;
  const yMax = Math.max(actualCost, budgetAllowance, appliedCost, appliedRate * xMax) * 1.08;

  const xScale = (v) => pad.left + (v / xMax) * plotW;
  const yScale = (v) => pad.top + plotH - (v / yMax) * plotH;

  // 軸
  mkLine(svg, {
    x1: pad.left, y1: pad.top + plotH, x2: pad.left + plotW, y2: pad.top + plotH,
    stroke: COLORS.baseline, strokeWidth: 1.5,
  });
  mkLine(svg, {
    x1: pad.left, y1: pad.top, x2: pad.left, y2: pad.top + plotH,
    stroke: COLORS.baseline, strokeWidth: 1.5,
  });
  drawLines(svg, {
    x: pad.left + plotW / 2, y: pad.top + plotH + 24, lines: ["操業度(時間)"],
    fontSize: 11, fill: COLORS.inkMuted,
  });
  drawLines(svg, {
    x: pad.left, y: pad.top - 14, lines: ["製造間接費(円)"],
    fontSize: 11, fill: COLORS.inkMuted, anchor: "start",
  });

  // 予定配賦額線(原点を通り、傾き=予定配賦率)
  mkLine(svg, {
    x1: xScale(0), y1: yScale(0), x2: xScale(xMax), y2: yScale(appliedRate * xMax),
    stroke: COLORS.inkSecondary, strokeWidth: 2,
  });
  drawLines(svg, {
    x: xScale(xMax) - 60, y: yScale(appliedRate * xMax) - 10, lines: ["予定配賦額線"],
    fontSize: 11, fill: COLORS.inkSecondary, fontWeight: "700",
  });

  // 予算許容額線(y切片=固定費予算、傾き=変動費率)
  mkLine(svg, {
    x1: xScale(0), y1: yScale(fixedBudget), x2: xScale(xMax), y2: yScale(variableRate * xMax + fixedBudget),
    stroke: COLORS.inkMuted, strokeWidth: 2, dashed: true,
  });
  drawLines(svg, {
    x: xScale(xMax) - 60, y: yScale(variableRate * xMax + fixedBudget) + 16, lines: ["予算許容額線"],
    fontSize: 11, fill: COLORS.inkMuted, fontWeight: "700",
  });

  // 基準操業度の目印(垂直の目安線)
  mkLine(svg, {
    x1: xScale(normalVolume), y1: pad.top + plotH, x2: xScale(normalVolume), y2: pad.top,
    stroke: COLORS.hairline, strokeWidth: 1, dashed: true,
  });
  drawLines(svg, {
    x: xScale(normalVolume), y: pad.top - 4, lines: ["基準操業度"],
    fontSize: 9, fill: COLORS.inkMuted,
  });

  // 実際操業度における3点
  const ax = xScale(actualVolume);
  const yActual = yScale(actualCost);
  const yBudget = yScale(budgetAllowance);
  const yApplied = yScale(appliedCost);

  mkLine(svg, {
    x1: ax, y1: pad.top + plotH, x2: ax, y2: Math.min(yActual, yApplied, yBudget) - 8,
    stroke: COLORS.hairline, strokeWidth: 1, dashed: true,
  });

  mkCircle(svg, { cx: ax, cy: yActual, r: 4, fill: COLORS.inkStrong });
  mkCircle(svg, { cx: ax, cy: yBudget, r: 4, fill: COLORS.accent });
  mkCircle(svg, { cx: ax, cy: yApplied, r: 4, fill: COLORS.accentDeep });

  // 予算差異・操業度差異のブラケット(印だけをグラフ内に描き、数値は3点が密集して
  // 文字を置く余白がないため下段のキャプションにまとめる)。
  const bracketX = ax + 14;
  mkLine(svg, { x1: bracketX, y1: yActual, x2: bracketX, y2: yBudget, stroke: COLORS.accent, strokeWidth: 1.5 });
  mkLine(svg, { x1: bracketX - 4, y1: yActual, x2: bracketX + 4, y2: yActual, stroke: COLORS.accent, strokeWidth: 1.5 });
  mkLine(svg, { x1: bracketX - 4, y1: yBudget, x2: bracketX + 4, y2: yBudget, stroke: COLORS.accent, strokeWidth: 1.5 });

  const bracketX2 = ax - 14;
  mkLine(svg, { x1: bracketX2, y1: yBudget, x2: bracketX2, y2: yApplied, stroke: COLORS.accentDeep, strokeWidth: 1.5 });
  mkLine(svg, { x1: bracketX2 - 4, y1: yBudget, x2: bracketX2 + 4, y2: yBudget, stroke: COLORS.accentDeep, strokeWidth: 1.5 });
  mkLine(svg, { x1: bracketX2 - 4, y1: yApplied, x2: bracketX2 + 4, y2: yApplied, stroke: COLORS.accentDeep, strokeWidth: 1.5 });

  // 数値まとめ(下段2行。3点が近接して図中には書き込めないため、ここに集約する)
  drawLines(svg, {
    x: W / 2, y: pad.top + plotH + 40,
    lines: [
      `実際操業度 ${actualVolume}時間: 実際発生額 ${yen(actualCost)} / 予算許容額 ${yen(Math.round(budgetAllowance))} / 予定配賦額 ${yen(Math.round(appliedCost))}`,
      `予算差異(実際発生額と予算許容額の差) ${formatVarianceShort(budgetVariance)} ／ 操業度差異(予算許容額と予定配賦額の差) ${formatVarianceShort(volumeVariance)}`,
    ],
    fontSize: 10, fill: COLORS.inkMuted, lineHeight: 15,
  });

  return svg;
}

function formatVarianceShort(v) {
  return `${yen(Math.round(Math.abs(v)))}(${v < 0 ? "不利" : "有利"})`;
}

// ---------------------------------------------------------------------------
// 2. 全部原価計算と直接原価計算のP/L構造比較・固定費調整
// ---------------------------------------------------------------------------
function buildAbsorptionVsDirectCosting() {
  const H = 430;
  const svg = createSvg(W, H);

  drawLines(svg, {
    x: W / 2, y: 22, lines: ["全部原価計算と直接原価計算のP/L構造比較"],
    fontSize: 15, fontWeight: "700", fill: COLORS.inkStrong,
  });

  // 基礎データ。生産量>販売量(期末在庫が発生するケース)にすることで、
  // 固定費調整が実際に非ゼロの差になる、意味のある例にする。
  // 売上高・売上原価・営業利益はすべてここから計算し、二重に数値を書かない。
  const unitsProduced = 1000; // 当期生産量(個)
  const unitsSold = 900; // 当期販売量(個)
  const endingInventoryUnits = unitsProduced - unitsSold; // 期末在庫(個)。期首在庫は0と仮定する

  drawLines(svg, {
    x: W / 2, y: 40, lines: [`当期生産量 ${unitsProduced}個 / 販売量 ${unitsSold}個(期末在庫 ${endingInventoryUnits}個)`],
    fontSize: 11, fill: COLORS.inkMuted,
  });
  const sellingPricePerUnit = 1200; // 販売単価
  const variableCostPerUnit = 400; // 変動製造費(単位あたり)
  const variableSellingExpPerUnit = 50; // 変動販売費(単位あたり)
  const fixedManufacturing = 150000; // 固定製造原価(当期発生額・期間総額)
  const fixedSga = 120000; // 固定販売費及び一般管理費

  const sales = unitsSold * sellingPricePerUnit; // 売上高
  const fixedCostPerUnit = fixedManufacturing / unitsProduced; // 全部原価計算での製品単位あたり固定費

  // 全部原価計算: 売上原価は「販売した分」の変動費+固定費(単位原価×販売量)。
  // 売上原価に含まれる固定費は、期間の固定製造原価の全額ではなく販売量に対応する部分だけ。
  const fixedCostInCogs = unitsSold * fixedCostPerUnit; // 売上原価に含まれる固定費(販売分のみ)
  const absorptionCogs = unitsSold * variableCostPerUnit + fixedCostInCogs;
  const grossProfit = sales - absorptionCogs; // 売上総利益
  const variableSellingExp = unitsSold * variableSellingExpPerUnit; // 変動販売費(共通)
  const absorptionOperatingIncome = grossProfit - (variableSellingExp + fixedSga); // 全部原価計算の営業利益

  // 直接原価計算: 売上原価は変動費のみ、固定製造原価は生産量に関係なく期間原価として全額費用化
  const variableCogs = unitsSold * variableCostPerUnit; // 変動売上原価
  const contributionMargin = sales - variableCogs; // 変動製造マージン
  const contributionAfterSellingVar = contributionMargin - variableSellingExp; // 貢献利益
  const directOperatingIncome = contributionAfterSellingVar - (fixedManufacturing + fixedSga); // 直接原価計算の営業利益

  // 固定費調整: 期末在庫に含まれる固定費(=期末在庫数量×単位あたり固定費)の分だけ、
  // 全部原価計算の営業利益は直接原価計算の営業利益より大きくなる(期首在庫は0のため調整不要)。
  const endingInventoryFixedCost = endingInventoryUnits * fixedCostPerUnit; // 期末在庫に含まれる固定費
  const beginningInventoryFixedCost = 0; // 期首在庫に含まれる固定費(期首在庫なしと仮定)
  const reconciledAbsorptionIncome =
    directOperatingIncome + endingInventoryFixedCost - beginningInventoryFixedCost;

  const colW = 270;
  const leftX = 25;
  const rightX = 345;
  const rowH = 30;
  const startY = 68;

  const neutralStyle = { fill: COLORS.surfaceRaised, stroke: COLORS.hairline, textColor: COLORS.inkSecondary };
  const fixedHighlight = { fill: COLORS.accentWash, stroke: COLORS.accent, textColor: COLORS.inkStrong };
  const finalStyle = { fill: COLORS.accentWash, stroke: COLORS.accentDeep, textColor: COLORS.inkStrong };

  drawLines(svg, {
    x: leftX + colW / 2, y: startY - 8, lines: ["全部原価計算"], fontSize: 13, fontWeight: "700", fill: COLORS.inkStrong,
  });
  drawLines(svg, {
    x: rightX + colW / 2, y: startY - 8, lines: ["直接原価計算"], fontSize: 13, fontWeight: "700", fill: COLORS.inkStrong,
  });

  // --- 左: 全部原価計算 ---
  let y = startY;
  drawBox(svg, { x: leftX, y, w: colW, h: rowH, ...neutralStyle, lines: [`売上高 ${yen(sales)}`], fontSize: 12, fontWeight: "600" });
  y += rowH;
  // 固定製造原価が売上原価の中に埋め込まれている、という点をaccentWashで示す
  drawBox(svg, {
    x: leftX, y, w: colW, h: rowH, ...fixedHighlight,
    lines: [`売上原価 ${yen(absorptionCogs)}(固定費 ${yen(fixedCostInCogs)}を含む)`], fontSize: 10, fontWeight: "600",
  });
  y += rowH;
  drawBox(svg, { x: leftX, y, w: colW, h: rowH, ...neutralStyle, lines: [`売上総利益 ${yen(grossProfit)}`], fontSize: 12, fontWeight: "600" });
  y += rowH;
  drawBox(svg, {
    x: leftX, y, w: colW, h: rowH, ...neutralStyle,
    lines: [`販売費及び一般管理費 ${yen(variableSellingExp + fixedSga)}`], fontSize: 11, fontWeight: "600",
  });
  y += rowH;
  drawBox(svg, { x: leftX, y, w: colW, h: rowH, ...finalStyle, lines: [`営業利益 ${yen(absorptionOperatingIncome)}`], fontSize: 12, fontWeight: "700" });

  // --- 右: 直接原価計算 ---
  y = startY;
  drawBox(svg, { x: rightX, y, w: colW, h: rowH, ...neutralStyle, lines: [`売上高 ${yen(sales)}`], fontSize: 12, fontWeight: "600" });
  y += rowH;
  drawBox(svg, { x: rightX, y, w: colW, h: rowH, ...neutralStyle, lines: [`変動売上原価 ${yen(variableCogs)}`], fontSize: 12, fontWeight: "600" });
  y += rowH;
  drawBox(svg, { x: rightX, y, w: colW, h: rowH, ...neutralStyle, lines: [`変動製造マージン ${yen(contributionMargin)}`], fontSize: 11, fontWeight: "600" });
  y += rowH;
  drawBox(svg, { x: rightX, y, w: colW, h: rowH, ...neutralStyle, lines: [`変動販売費 ${yen(variableSellingExp)}`], fontSize: 12, fontWeight: "600" });
  y += rowH;
  drawBox(svg, { x: rightX, y, w: colW, h: rowH, ...neutralStyle, lines: [`貢献利益 ${yen(contributionAfterSellingVar)}`], fontSize: 11, fontWeight: "600" });
  y += rowH;
  // 固定費が下段で分離して表示されている、という点をaccentWashで示す(左側との対比)
  drawBox(svg, {
    x: rightX, y, w: colW, h: rowH, ...fixedHighlight,
    lines: [`固定製造原価+固定販管費 ${yen(fixedManufacturing + fixedSga)}`], fontSize: 10.5, fontWeight: "600",
  });
  y += rowH;
  drawBox(svg, { x: rightX, y, w: colW, h: rowH, ...finalStyle, lines: [`営業利益 ${yen(directOperatingIncome)}`], fontSize: 12, fontWeight: "700" });

  // 固定費調整
  const noteY = y + rowH + 30;
  drawArrow(svg, {
    x1: rightX + colW / 2, y1: y + rowH + 6, x2: leftX + colW / 2, y2: y + rowH + 6,
    color: COLORS.accent, strokeWidth: 1.5,
  });
  drawLines(svg, {
    x: W / 2, y: noteY, lines: ["固定費調整"], fontSize: 13, fontWeight: "700", fill: COLORS.inkStrong,
  });
  drawLines(svg, {
    x: W / 2, y: noteY + 22,
    lines: [
      `直接原価計算の営業利益 ${yen(directOperatingIncome)}`,
      `+ 期末在庫の固定費 ${yen(endingInventoryFixedCost)} − 期首在庫の固定費 ${yen(beginningInventoryFixedCost)}`,
      `= 全部原価計算の営業利益 ${yen(reconciledAbsorptionIncome)}`,
    ],
    fontSize: 11, fill: COLORS.inkSecondary, lineHeight: 16,
  });

  return svg;
}

// ---------------------------------------------------------------------------
// 3. 高低点法による原価の固変分解(散布図+直線)
// ---------------------------------------------------------------------------
function buildHighLowPointMethod() {
  const H = 380;
  const svg = createSvg(W, H);

  drawLines(svg, {
    x: W / 2, y: 22, lines: ["高低点法による原価の固変分解"],
    fontSize: 15, fontWeight: "700", fill: COLORS.inkStrong,
  });

  const pad = { top: 44, right: 30, bottom: 76, left: 80 };
  const plotW = W - pad.left - pad.right;
  const plotH = H - pad.top - pad.bottom;

  // 6か月分の(操業度, 原価)の実績データ。最高点・最低点は「操業度」で決める。
  const dataPoints = [
    { volume: 320, cost: 540000 },
    { volume: 480, cost: 640000 },
    { volume: 260, cost: 480000 }, // 最低点(操業度が最小)
    { volume: 410, cost: 600000 },
    { volume: 600, cost: 760000 }, // 最高点(操業度が最大)
    { volume: 350, cost: 560000 },
  ];

  const highPoint = dataPoints.reduce((a, b) => (b.volume > a.volume ? b : a));
  const lowPoint = dataPoints.reduce((a, b) => (b.volume < a.volume ? b : a));

  // 変動費率・固定費はすべてJSで算出し、後段の数式表示もこの値をそのまま使う。
  const variableRate = (highPoint.cost - lowPoint.cost) / (highPoint.volume - lowPoint.volume);
  const fixedCost = highPoint.cost - variableRate * highPoint.volume;

  const xMax = Math.ceil((highPoint.volume * 1.15) / 100) * 100;
  const yMax = Math.ceil((highPoint.cost * 1.1) / 100000) * 100000;

  const xScale = (v) => pad.left + (v / xMax) * plotW;
  const yScale = (v) => pad.top + plotH - (v / yMax) * plotH;

  // 軸
  mkLine(svg, {
    x1: pad.left, y1: pad.top + plotH, x2: pad.left + plotW, y2: pad.top + plotH,
    stroke: COLORS.baseline, strokeWidth: 1.5,
  });
  mkLine(svg, {
    x1: pad.left, y1: pad.top, x2: pad.left, y2: pad.top + plotH,
    stroke: COLORS.baseline, strokeWidth: 1.5,
  });
  drawLines(svg, {
    x: pad.left + plotW / 2, y: pad.top + plotH + 24, lines: ["操業度(時間)"],
    fontSize: 11, fill: COLORS.inkMuted,
  });
  drawLines(svg, {
    x: pad.left, y: pad.top - 16, lines: ["原価(円)"],
    fontSize: 11, fill: COLORS.inkMuted, anchor: "start",
  });

  // 目盛り
  const TICKS = 4;
  for (let i = 0; i <= TICKS; i++) {
    const vx = (xMax * i) / TICKS;
    const x = xScale(vx);
    mkLine(svg, { x1: x, y1: pad.top + plotH, x2: x, y2: pad.top + plotH + 5, stroke: COLORS.hairline, strokeWidth: 1 });
    drawLines(svg, { x, y: pad.top + plotH + 18, lines: [String(Math.round(vx))], fontSize: 9, fill: COLORS.inkMuted });

    const vy = (yMax * i) / TICKS;
    const y = yScale(vy);
    mkLine(svg, { x1: pad.left - 5, y1: y, x2: pad.left, y2: y, stroke: COLORS.hairline, strokeWidth: 1 });
    if (i > 0) {
      drawLines(svg, { x: pad.left - 8, y: y + 3, lines: [yen(vy)], fontSize: 9, fill: COLORS.inkMuted, anchor: "end" });
    }
  }

  // 背景の実績データ点(最高点・最低点以外)
  dataPoints.forEach((p) => {
    if (p === highPoint || p === lowPoint) return;
    mkCircle(svg, { cx: xScale(p.volume), cy: yScale(p.cost), r: 3.5, fill: COLORS.inkFadedMid });
  });

  // 最高点・最低点を結ぶ直線
  mkLine(svg, {
    x1: xScale(lowPoint.volume), y1: yScale(lowPoint.cost),
    x2: xScale(highPoint.volume), y2: yScale(highPoint.cost),
    stroke: COLORS.accent, strokeWidth: 2,
  });

  // 最高点・最低点の強調
  mkCircle(svg, { cx: xScale(lowPoint.volume), cy: yScale(lowPoint.cost), r: 6, fill: COLORS.accentDeep });
  drawLines(svg, {
    x: xScale(lowPoint.volume), y: yScale(lowPoint.cost) + 20,
    lines: [`最低点 (${lowPoint.volume}時間, ${yen(lowPoint.cost)})`], fontSize: 10, fill: COLORS.accentDeep, fontWeight: "700",
  });
  mkCircle(svg, { cx: xScale(highPoint.volume), cy: yScale(highPoint.cost), r: 6, fill: COLORS.accentDeep });
  drawLines(svg, {
    x: xScale(highPoint.volume), y: yScale(highPoint.cost) - 14,
    lines: [`最高点 (${highPoint.volume}時間, ${yen(highPoint.cost)})`], fontSize: 10, fill: COLORS.accentDeep, fontWeight: "700",
  });

  // 数式(高低点法で求めた変動費率・固定費を、実際のJS値そのまま表示)
  drawLines(svg, {
    x: W / 2, y: pad.top + plotH + 48,
    lines: [
      `変動費率 = (${yen(highPoint.cost)} − ${yen(lowPoint.cost)}) ÷ (${highPoint.volume} − ${lowPoint.volume}) = ${Math.round(variableRate)}円/時間`,
      `原価 = ${Math.round(variableRate)} × 操業度 + ${yen(Math.round(fixedCost))}`,
    ],
    fontSize: 11, fill: COLORS.inkStrong, lineHeight: 16, fontWeight: "600",
  });

  return svg;
}

// ---------------------------------------------------------------------------
// 4. 基準操業度の4種類(入れ子構造)
// ---------------------------------------------------------------------------
function buildStandardOperatingVolumeTypes() {
  const H = 390;
  const svg = createSvg(W, H);

  drawLines(svg, {
    x: W / 2, y: 22, lines: ["基準操業度の4種類(範囲が大きい順の入れ子構造)"],
    fontSize: 15, fontWeight: "700", fill: COLORS.inkStrong,
  });

  // 4段の入れ子。数値は「理解を助けるための相対的な操業度の目安」として
  // 外側から内側へ単調に減る値をJSで組み立てる(独立に4つ書かない)。
  const maxVolume = 1000; // 最大操業度(理論的生産能力)
  const feasibleRatio = 0.85; // 実現可能操業度 = 最大操業度 × この比率
  const normalRatio = 0.75; // 正常操業度 = 最大操業度 × この比率
  const shortTermRatio = 0.7; // 短期予定操業度 = 最大操業度 × この比率

  const feasibleVolume = Math.round(maxVolume * feasibleRatio);
  const normalVolume = Math.round(maxVolume * normalRatio);
  const shortTermVolume = Math.round(maxVolume * shortTermRatio);

  const levels = [
    {
      key: "最大操業度(理論的生産能力)",
      desc: "理論上、中断なく操業した場合の最大の操業度。実務では採用されない",
      volume: maxVolume,
      scale: 1,
      accent: false,
    },
    {
      key: "実現可能操業度",
      desc: "機械の故障など不可避的な作業休止分を差し引いた操業度",
      volume: feasibleVolume,
      scale: 0.78,
      accent: false,
    },
    {
      key: "正常操業度(長期平均操業度)",
      desc: "季節変動・景気変動の影響を3〜5年程度の長期で平準化した操業度",
      volume: normalVolume,
      scale: 0.56,
      accent: false,
    },
    {
      key: "短期予定操業度(期待実際操業度)",
      desc: "向こう1年間の需要予測に基づく操業度。原則として採用される",
      volume: shortTermVolume,
      scale: 0.34,
      accent: true,
    },
  ];

  // 4段の同心の入れ子矩形(外側ほど大きく、中心を揃えたまま縮小する)。
  // scaleから幅・高さ・座標をすべて算出するため、矩形が負の大きさになることはない。
  const centerX = W / 2;
  const centerY = 155;
  const fullW = 560;
  const fullH = 220;

  levels.forEach((level, i) => {
    const w = fullW * level.scale;
    const h = fullH * level.scale;
    const x = centerX - w / 2;
    const y = centerY - h / 2;

    const style = level.accent
      ? { fill: COLORS.accentWash, stroke: COLORS.accentDeep, textColor: COLORS.inkStrong }
      : { fill: COLORS.surfaceRaised, stroke: COLORS.hairline, textColor: COLORS.inkSecondary };

    mkRect(svg, { x, y, w, h, fill: style.fill, stroke: style.stroke, strokeWidth: level.accent ? 2 : 1.5 });

    // ラベルは各矩形の上端付近に配置する。外側ほど上端が高い位置にあるため、
    // 内側の矩形のラベルと縦位置がずれて重ならない。
    drawLines(svg, {
      x: centerX, y: y + 20, lines: [level.key],
      fontSize: i === 0 ? 12 : 11, fontWeight: level.accent ? "700" : "600", fill: style.textColor,
    });
  });

  // すべての凡例(名称+説明)は入れ子図の下に一括してリスト表示し、
  // 「正常操業度」を含む4種類が確実に文字として存在することを保証する。
  const legendTop = 282;
  drawLines(svg, {
    x: centerX, y: legendTop,
    lines: levels.map((l, i) => `${i + 1}. ${l.key} — ${l.desc}`),
    fontSize: 10, fill: COLORS.inkMuted, lineHeight: 15,
  });

  drawLines(svg, {
    x: centerX, y: legendTop + levels.length * 15 + 14,
    lines: [
      `目安: 最大${maxVolume} > 実現可能${feasibleVolume} > 正常${normalVolume} > 短期予定${shortTermVolume}(単位:時間)`,
    ],
    fontSize: 10, fill: COLORS.inkFaded,
  });

  return svg;
}

// ---------------------------------------------------------------------------
// 5. 等級別総合原価計算(等価係数・積数による原価の配分)
// ---------------------------------------------------------------------------
function buildGradeCostingEquivalence() {
  const H = 400;
  const svg = createSvg(W, H);

  drawLines(svg, {
    x: W / 2, y: 22, lines: ["等級別総合原価計算(等価係数・積数による配分)"],
    fontSize: 15, fontWeight: "700", fill: COLORS.inkStrong,
  });

  // 基礎データ。積数・配分額はすべてここから計算し、表とグラフで
  // 同じ変数を参照する(別々に数値を書かない=旧AI画像の不整合を再発させない)。
  const totalCost = 620000; // 完成品総合原価
  const grades = [
    { name: "1級品", qty: 400, coefficient: 1.0 },
    { name: "2級品", qty: 300, coefficient: 0.8 },
    { name: "3級品", qty: 250, coefficient: 0.6 },
  ].map((g) => ({ ...g, square: g.qty * g.coefficient }));

  const totalSquare = grades.reduce((sum, g) => sum + g.square, 0);
  grades.forEach((g) => {
    g.share = g.square / totalSquare;
    g.allocatedCost = Math.round(totalCost * g.share);
  });

  // --- 表: 等級製品/完成品数量/等価係数/積数 ---
  const tableX = 60;
  const tableY = 52;
  drawTable(svg, {
    x: tableX, y: tableY, rowHeight: 26,
    colWidths: [110, 130, 110, 110],
    rows: [
      ["等級製品", "完成品数量", "等価係数", "積数"],
      ...grades.map((g) => [g.name, `${g.qty}個`, g.coefficient.toFixed(1), `${g.square}`]),
    ],
    headerFill: COLORS.surfaceRaised, bodyFill: COLORS.surfaceRaised, stroke: COLORS.hairline,
    headerTextColor: COLORS.inkStrong, bodyTextColor: COLORS.inkSecondary, fontSize: 12,
  });

  drawLines(svg, {
    x: W / 2, y: tableY + 26 * (grades.length + 1) + 22,
    lines: [`積数合計 = ${grades.map((g) => g.square).join(" + ")} = ${totalSquare}`],
    fontSize: 11, fill: COLORS.inkMuted,
  });

  // --- 棒グラフ: 積数の割合に応じた原価配分 ---
  const chartTop = tableY + 26 * (grades.length + 1) + 50;
  const chartLabelY = chartTop - 10;
  drawLines(svg, {
    x: W / 2, y: chartLabelY, lines: [`完成品総合原価 ${yen(totalCost)} を積数の比で配分`],
    fontSize: 12, fontWeight: "700", fill: COLORS.inkStrong,
  });

  const barMaxH = 110;
  const barW = 110;
  const gap = 60;
  const groupW = barW * grades.length + gap * (grades.length - 1);
  const startX = (W - groupW) / 2;
  const baseline = chartTop + barMaxH + 30;

  const colors = [COLORS.accent, COLORS.accentDeep, COLORS.inkSecondary];

  grades.forEach((g, i) => {
    const barH = barMaxH * g.share; // 積数シェアと同じ値からバーの高さを決める(表と同じ数値)
    const x = startX + i * (barW + gap);
    const y = baseline - barH;
    mkRect(svg, { x, y, w: barW, h: barH, fill: colors[i % colors.length] });
    drawLines(svg, {
      x: x + barW / 2, y: y - 8,
      lines: [`${Math.round(g.share * 1000) / 10}%`], fontSize: 11, fontWeight: "700", fill: COLORS.inkStrong,
    });
    drawLines(svg, {
      x: x + barW / 2, y: baseline + 18,
      lines: [g.name, yen(g.allocatedCost)], fontSize: 11, fill: COLORS.inkSecondary, lineHeight: 14,
    });
  });

  mkLine(svg, { x1: startX - 10, y1: baseline, x2: startX + groupW + 10, y2: baseline, stroke: COLORS.baseline, strokeWidth: 1.5 });

  return svg;
}

// ---------------------------------------------------------------------------
// 6. 工程別総合原価計算(累加法): 前工程費の引き継ぎ
// ---------------------------------------------------------------------------
function buildProcessCostingCarryover() {
  const H = 340;
  const svg = createSvg(W, H);

  drawLines(svg, {
    x: W / 2, y: 22, lines: ["工程別総合原価計算(累加法): 前工程費の引き継ぎ"],
    fontSize: 15, fontWeight: "700", fill: COLORS.inkStrong,
  });

  // 第1工程完成品原価は必ず「材料費+加工費」の和として計算し、
  // 第2工程の完成品原価はその値を前工程費として足し込む形で計算する。
  const process1Material = 180000; // 第1工程: 材料費
  const process1Labor = 120000; // 第1工程: 加工費
  const process1CompletedCost = process1Material + process1Labor; // 第1工程完成品原価

  const process2Material = 60000; // 第2工程: 材料費
  const process2Labor = 90000; // 第2工程: 加工費
  const process2CompletedCost = process1CompletedCost + process2Material + process2Labor; // 第2工程完成品原価(最終)

  const neutralStyle = { fill: COLORS.surfaceRaised, stroke: COLORS.hairline, textColor: COLORS.inkSecondary };
  const finalStyle = { fill: COLORS.accentWash, stroke: COLORS.accentDeep, textColor: COLORS.inkStrong };

  const process1 = { x: 20, y: 90, w: 210, h: 120 };
  const process2 = { x: 400, y: 90, w: 220, h: 120 };

  drawBox(svg, {
    ...process1, ...neutralStyle, fontWeight: "700", fontSize: 13, lineHeight: 18,
    lines: [
      "第1工程",
      `材料費 ${yen(process1Material)}`,
      `加工費 ${yen(process1Labor)}`,
      `完成品原価 ${yen(process1CompletedCost)}`,
    ],
  });

  drawBox(svg, {
    ...process2, ...finalStyle, fontWeight: "700", fontSize: 13, lineHeight: 18,
    lines: [
      "第2工程",
      `前工程費 ${yen(process1CompletedCost)}`,
      `材料費 ${yen(process2Material)}`,
      `加工費 ${yen(process2Labor)}`,
      `完成品原価 ${yen(process2CompletedCost)}`,
    ],
  });

  drawArrow(svg, {
    x1: process1.x + process1.w, y1: process1.y + process1.h / 2,
    x2: process2.x, y2: process2.y + process2.h / 2,
    color: COLORS.accent, strokeWidth: 2, label: "前工程費として引き継ぐ",
  });

  drawLines(svg, {
    x: W / 2, y: process1.y + process1.h + 46,
    lines: ["前工程費は次工程では直接材料費と同様に、工程の始点で投入されたものとして扱われる"],
    fontSize: 11, fill: COLORS.inkMuted,
  });

  return svg;
}

export const costAccountingMethodsDiagrams = {
  "manufacturing-overhead-variance": buildManufacturingOverheadVariance,
  "absorption-vs-direct-costing": buildAbsorptionVsDirectCosting,
  "high-low-point-method": buildHighLowPointMethod,
  "standard-operating-volume-types": buildStandardOperatingVolumeTypes,
  "grade-costing-equivalence": buildGradeCostingEquivalence,
  "process-costing-carryover": buildProcessCostingCarryover,
};
