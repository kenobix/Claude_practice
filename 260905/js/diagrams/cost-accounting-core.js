// 工業簿記(原価計算)の中核論点まわりの図解。
// AI生成画像で数値の不整合・他論点の混入が発生したため、既存のchart.js/helpers.jsと
// 同じ「createElementNSのみ・COLORSトークンのみ」の方針で手書きし直す。
import { COLORS } from "../config.js";
import {
  NS, createSvg, drawBox, drawArrow, drawLines, drawTAccount, yen,
} from "./helpers.js";

const W = 640;

// ---------------------------------------------------------------------------
// 1. 月末仕掛品の評価方法(平均法・先入先出法)と仕損・減損
// ---------------------------------------------------------------------------
function buildWipValuationSpoilage() {
  const H = 360;
  const svg = createSvg(W, H);

  drawLines(svg, {
    x: W / 2, y: 24, lines: ["月末仕掛品の評価方法(平均法・先入先出法)と仕損・減損"],
    fontSize: 15, fontWeight: "700", fill: COLORS.inkStrong,
  });

  // 数量の基礎データ。当期投入は「完成品+月末仕掛品-期首仕掛品」で必ず計算し、
  // 二重に独立した数値を書かないことで貸借(数量)の一致を保証する。
  const beginningQty = 200;
  const beginningDegree = 0.5; // 期首仕掛品の加工進捗度
  const completedQty = 900;
  const endingQty = 100;
  const endingDegree = 0.4; // 月末仕掛品の加工進捗度
  const inputQty = completedQty + endingQty - beginningQty; // = 800 (期首+投入=完成+月末 が成立)

  // 加工費の完成品換算量は方法によって計算式が異なる(これが平均法/先入先出法の違いの本質)。
  const avgEquivalentUnits = completedQty + endingQty * endingDegree; // 平均法: 全体をまとめて按分
  const fifoEquivalentUnits =
    completedQty - beginningQty * beginningDegree + endingQty * endingDegree; // 先入先出法: 当期投入分のみ

  const neutralBox = { fill: COLORS.surfaceRaised, stroke: COLORS.hairline, textColor: COLORS.inkSecondary };

  const taShared = {
    w: 230, h: 90, stroke: COLORS.hairline, textColor: COLORS.inkSecondary, titleColor: COLORS.inkStrong,
    debitLines: [
      `期首仕掛品 ${beginningQty}個(${beginningDegree * 100}%)`,
      `当期投入 ${inputQty}個`,
    ],
    creditLines: [
      `完成品 ${completedQty}個`,
      `月末仕掛品 ${endingQty}個(${endingDegree * 100}%)`,
    ],
  };

  // 平均法パネル
  const panelA = { x: 50, y: 60 };
  drawTAccount(svg, { ...taShared, ...panelA, title: "平均法(数量の流れ)" });
  drawLines(svg, {
    x: panelA.x + taShared.w / 2, y: panelA.y + taShared.h + 28,
    lines: [
      "加工費換算量(平均法)",
      `${completedQty}個 + ${endingQty}個×${endingDegree * 100}% = ${avgEquivalentUnits}個`,
    ],
    fontSize: 12, fill: COLORS.inkStrong, fontWeight: "600",
  });

  // 先入先出法パネル
  const panelB = { x: 360, y: 60 };
  drawTAccount(svg, { ...taShared, ...panelB, title: "先入先出法(数量の流れ)" });
  drawLines(svg, {
    x: panelB.x + taShared.w / 2, y: panelB.y + taShared.h + 28,
    lines: [
      "加工費換算量(先入先出法・当期投入分)",
      `${completedQty}個 − ${beginningQty}個×${beginningDegree * 100}% + ${endingQty}個×${endingDegree * 100}% = ${fifoEquivalentUnits}個`,
    ],
    fontSize: 12, fill: COLORS.inkStrong, fontWeight: "600",
  });

  // 仕損 vs 減損(現物が残るかどうかだけの違い。他論点には触れない)
  drawBox(svg, {
    x: 20, y: 225, w: 190, h: 100, ...neutralBox, fontSize: 11, fontWeight: "600", lineHeight: 16,
    lines: ["仕損", "加工に失敗した不合格品", "→現物(仕損品)が残る"],
  });
  drawBox(svg, {
    x: 225, y: 225, w: 190, h: 100, ...neutralBox, fontSize: 11, fontWeight: "600", lineHeight: 16,
    lines: ["減損", "蒸発・減量などの棚卸減耗", "→現物が残らない"],
  });

  // 度外視法(仕損・減損のみの説明。補助部門/製造部門の配賦には触れない)
  drawBox(svg, {
    x: 430, y: 225, w: 190, h: 100, ...neutralBox, fontSize: 11, fontWeight: "600", lineHeight: 16,
    lines: ["度外視法", "仕損・減損の原価を区分せず", "正常な完成品・月末仕掛品の", "原価に自動的に含める方法"],
  });

  return svg;
}

// ---------------------------------------------------------------------------
// 2. 標準原価計算における原価差異の分解
// ---------------------------------------------------------------------------
function formatVariance(v) {
  return `${yen(Math.abs(v))}(${v < 0 ? "不利差異" : "有利差異"})`;
}

function buildCostVarianceAnalysis() {
  const H = 300;
  const svg = createSvg(W, H);

  drawLines(svg, {
    x: W / 2, y: 24, lines: ["標準原価計算における原価差異の分解"],
    fontSize: 15, fontWeight: "700", fill: COLORS.inkStrong,
  });

  // 直接材料費差異: 価格差異 + 数量差異。合計は必ずこの2つの和として計算する。
  const dmStdPrice = 100, dmStdQty = 50, dmActualPrice = 110, dmActualQty = 55;
  const dmPriceVariance = (dmStdPrice - dmActualPrice) * dmActualQty;
  const dmQtyVariance = (dmStdQty - dmActualQty) * dmStdPrice;
  const dmTotalVariance = dmPriceVariance + dmQtyVariance;

  // 直接労務費差異: 賃率差異 + 時間差異。
  const dlStdRate = 1200, dlStdHours = 40, dlActualRate = 1250, dlActualHours = 42;
  const dlRateVariance = (dlStdRate - dlActualRate) * dlActualHours;
  const dlTimeVariance = (dlStdHours - dlActualHours) * dlStdRate;
  const dlTotalVariance = dlRateVariance + dlTimeVariance;

  const neutralBox = { fill: COLORS.surfaceRaised, stroke: COLORS.hairline };
  const arrowColor = COLORS.inkMuted;

  // --- 列1: 直接材料費差異 ---
  const col1 = { x: 10, w: 190 };
  drawBox(svg, {
    x: col1.x, y: 55, w: col1.w, h: 54, ...neutralBox, textColor: COLORS.inkStrong,
    fontSize: 13, fontWeight: "700", lines: ["直接材料費差異"],
  });
  drawLines(svg, {
    x: col1.x + col1.w / 2, y: 55 + 42, lines: [formatVariance(dmTotalVariance)],
    fontSize: 12, fontWeight: "700", fill: COLORS.accentDeep,
  });
  drawArrow(svg, { x1: col1.x + col1.w / 2, y1: 109, x2: col1.x + 45, y2: 150, color: arrowColor });
  drawArrow(svg, { x1: col1.x + col1.w / 2, y1: 109, x2: col1.x + 145, y2: 150, color: arrowColor });
  drawBox(svg, {
    x: col1.x, y: 150, w: 90, h: 90, ...neutralBox, textColor: COLORS.inkSecondary,
    fontSize: 11, fontWeight: "600", lineHeight: 16,
    lines: ["価格差異", "(単価のズレ)", formatVariance(dmPriceVariance)],
  });
  drawBox(svg, {
    x: col1.x + 100, y: 150, w: 90, h: 90, ...neutralBox, textColor: COLORS.inkSecondary,
    fontSize: 11, fontWeight: "600", lineHeight: 16,
    lines: ["数量差異", "(使用量のズレ)", formatVariance(dmQtyVariance)],
  });

  // --- 列2: 直接労務費差異 ---
  const col2 = { x: 220, w: 190 };
  drawBox(svg, {
    x: col2.x, y: 55, w: col2.w, h: 54, ...neutralBox, textColor: COLORS.inkStrong,
    fontSize: 13, fontWeight: "700", lines: ["直接労務費差異"],
  });
  drawLines(svg, {
    x: col2.x + col2.w / 2, y: 55 + 42, lines: [formatVariance(dlTotalVariance)],
    fontSize: 12, fontWeight: "700", fill: COLORS.accentDeep,
  });
  drawArrow(svg, { x1: col2.x + col2.w / 2, y1: 109, x2: col2.x + 45, y2: 150, color: arrowColor });
  drawArrow(svg, { x1: col2.x + col2.w / 2, y1: 109, x2: col2.x + 145, y2: 150, color: arrowColor });
  drawBox(svg, {
    x: col2.x, y: 150, w: 90, h: 90, ...neutralBox, textColor: COLORS.inkSecondary,
    fontSize: 11, fontWeight: "600", lineHeight: 16,
    lines: ["賃率差異", "(時給のズレ)", formatVariance(dlRateVariance)],
  });
  drawBox(svg, {
    x: col2.x + 100, y: 150, w: 90, h: 90, ...neutralBox, textColor: COLORS.inkSecondary,
    fontSize: 11, fontWeight: "600", lineHeight: 16,
    lines: ["時間差異", "(作業時間のズレ)", formatVariance(dlTimeVariance)],
  });

  // --- 列3: 製造間接費差異(内訳の名称のみ。仕損・度外視法や部門配賦の話は持ち込まない) ---
  const col3 = { x: 430, w: 200 };
  drawBox(svg, {
    x: col3.x, y: 55, w: col3.w, h: 54, ...neutralBox, textColor: COLORS.inkStrong,
    fontSize: 13, fontWeight: "700", lines: ["製造間接費差異"],
  });
  drawArrow(svg, { x1: col3.x + col3.w / 2, y1: 109, x2: col3.x + col3.w / 2, y2: 150, color: arrowColor });
  drawBox(svg, {
    x: col3.x, y: 150, w: col3.w, h: 110, ...neutralBox, textColor: COLORS.inkSecondary,
    fontSize: 11, fontWeight: "600", lineHeight: 16,
    lines: [
      "予算差異", "(実際発生額と予算許容額のズレ)",
      "操業度差異", "(基準操業度と実際操業度のズレ)",
      "能率差異", "(標準時間と実際時間のズレ)",
    ],
  });

  return svg;
}

// ---------------------------------------------------------------------------
// 3. CVP分析と損益分岐点(損益分岐点図表)
// ---------------------------------------------------------------------------
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

function buildCvpBreakevenChart() {
  const H = 380;
  const svg = createSvg(W, H);

  drawLines(svg, {
    x: W / 2, y: 22, lines: ["CVP分析と損益分岐点"],
    fontSize: 15, fontWeight: "700", fill: COLORS.inkStrong,
  });

  // 下部に「目盛りラベル→x軸タイトル→安全余裕の矢印→安全余裕の注記」の4段を
  // 順に積むための余白。段ごとの間隔を確保して重ならないようにする。
  const pad = { top: 34, right: 30, bottom: 80, left: 78 };
  const plotW = W - pad.left - pad.right;
  const plotH = H - pad.top - pad.bottom;

  // 基礎データ(すべてここから計算し、交点や目盛りの最大値を後から独立に書かない)。
  const fixedCost = 200000; // 固定費(=総費用線のy切片)
  const variableCostRate = 0.6; // 変動費率(=総費用線の傾き)
  const actualSales = 800000; // 実際売上高(安全余裕の比較対象)

  const revenueAt = (x) => x; // 売上高線: 原点を通る傾き1の直線(x軸自体が売上高)
  const costAt = (x) => fixedCost + variableCostRate * x; // 総費用線

  const xMax = Math.ceil((actualSales * 1.15) / 100000) * 100000;
  const yMax = Math.max(revenueAt(xMax), costAt(xMax));

  // 損益分岐点(売上高) = 固定費 / (1 - 変動費率)。yは総費用線の式から導出する
  // (売上高線の式からではなく片方だけから計算することで、二重に値を書かない)。
  const breakevenX = fixedCost / (1 - variableCostRate);
  const breakevenY = costAt(breakevenX);

  const safetyMargin = actualSales - breakevenX;
  const safetyMarginRatio = safetyMargin / actualSales;

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
  const axisTitleY = pad.top + plotH + 34;
  drawLines(svg, {
    x: pad.left + plotW / 2, y: axisTitleY, lines: ["売上高(円)"],
    fontSize: 11, fill: COLORS.inkMuted,
  });
  drawLines(svg, {
    x: pad.left, y: pad.top - 12, lines: ["金額(円)"],
    fontSize: 11, fill: COLORS.inkMuted, anchor: "start",
  });

  // 目盛り(x・yとも同じスケールなので共通の刻みで良い)
  const TICKS = 4;
  for (let i = 0; i <= TICKS; i++) {
    const v = (xMax * i) / TICKS;
    const x = xScale(v);
    mkLine(svg, {
      x1: x, y1: pad.top + plotH, x2: x, y2: pad.top + plotH + 5,
      stroke: COLORS.hairline, strokeWidth: 1,
    });
    drawLines(svg, {
      x, y: pad.top + plotH + 18, lines: [yen(v)], fontSize: 9, fill: COLORS.inkMuted,
    });
    const y = yScale(v);
    mkLine(svg, {
      x1: pad.left - 5, y1: y, x2: pad.left, y2: y, stroke: COLORS.hairline, strokeWidth: 1,
    });
    if (i > 0) {
      drawLines(svg, {
        x: pad.left - 8, y: y + 3, lines: [yen(v)], fontSize: 9, fill: COLORS.inkMuted, anchor: "end",
      });
    }
  }

  // 売上高線
  mkLine(svg, {
    x1: xScale(0), y1: yScale(revenueAt(0)), x2: xScale(xMax), y2: yScale(revenueAt(xMax)),
    stroke: COLORS.inkSecondary, strokeWidth: 2,
  });
  drawLines(svg, {
    x: xScale(xMax) - 34, y: yScale(revenueAt(xMax)) - 8, lines: ["売上高線"],
    fontSize: 11, fill: COLORS.inkSecondary, fontWeight: "700",
  });

  // 総費用線
  mkLine(svg, {
    x1: xScale(0), y1: yScale(costAt(0)), x2: xScale(xMax), y2: yScale(costAt(xMax)),
    stroke: COLORS.inkMuted, strokeWidth: 2, dashed: true,
  });
  drawLines(svg, {
    x: xScale(xMax) - 34, y: yScale(costAt(xMax)) + 16, lines: ["総費用線"],
    fontSize: 11, fill: COLORS.inkMuted, fontWeight: "700",
  });

  // 損益分岐点(この図で最も重要な1点なのでaccentを使う)
  const bx = xScale(breakevenX);
  const by = yScale(breakevenY);
  mkLine(svg, { x1: bx, y1: by, x2: bx, y2: pad.top + plotH, stroke: COLORS.accent, strokeWidth: 1, dashed: true });
  mkLine(svg, { x1: pad.left, y1: by, x2: bx, y2: by, stroke: COLORS.accent, strokeWidth: 1, dashed: true });
  mkCircle(svg, { cx: bx, cy: by, r: 4.5, fill: COLORS.accent });
  drawLines(svg, {
    x: bx, y: by - 32, fill: COLORS.accentDeep, fontSize: 12, fontWeight: "700",
    lines: ["損益分岐点", `売上高 ${yen(breakevenX)}`],
  });

  // 安全余裕(実際売上高が損益分岐点をどれだけ上回るか)。x軸タイトルの行(axisTitleY)と
  // 重ならないよう、さらに下の段に配置する。
  const marginY = axisTitleY + 16;
  drawArrow(svg, {
    x1: bx, y1: marginY, x2: xScale(actualSales), y2: marginY,
    color: COLORS.inkSecondary, strokeWidth: 1.5,
  });
  mkLine(svg, {
    x1: xScale(actualSales), y1: pad.top + plotH, x2: xScale(actualSales), y2: marginY,
    stroke: COLORS.hairline, strokeWidth: 1, dashed: true,
  });
  drawLines(svg, {
    x: (bx + xScale(actualSales)) / 2, y: marginY + 14,
    lines: [`安全余裕 ${yen(safetyMargin)}(安全余裕率 ${Math.round(safetyMarginRatio * 1000) / 10}%)`],
    fontSize: 10, fill: COLORS.inkStrong, fontWeight: "600",
  });

  return svg;
}

export const costAccountingCoreDiagrams = {
  "wip-valuation-spoilage": buildWipValuationSpoilage,
  "cost-variance-analysis": buildCostVarianceAnalysis,
  "cvp-breakeven-chart": buildCvpBreakevenChart,
};
