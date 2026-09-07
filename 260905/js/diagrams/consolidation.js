// 連結会計まわりの図解(合併ののれん構造・資本連結タイムテーブル・
// ダウンストリーム/アップストリームの未実現利益消去)。
// AI生成画像では「連結の相殺消去図」テンプレートが無関係なテーマの画像に
// 混入する欠陥が確認されているため、他ファイルと同じくcreateElementNSベースの
// 自作SVGとして数値・ラベルをコードで直接保証する。
import { COLORS } from "../config.js";
import { createSvg, drawBox, drawArrow, drawLines, drawTable, yen } from "./helpers.js";

const W = 640;

// ---------------------------------------------------------------------------
// 1. 合併(パーチェス法)におけるのれん・負ののれんの発生構造
// ---------------------------------------------------------------------------

// のれん(正の場合)/負ののれん発生益(負の場合)の判定式。
// ケースA・ケースBのどちらも必ずこの1つの式から計算し、独立した数値を書かない。
function goodwillOrNegativeGoodwill(consideration, netAssetsFairValue) {
  return consideration - netAssetsFairValue;
}

function buildMergerGoodwillStructure() {
  const H = 360;
  const svg = createSvg(W, H);

  drawLines(svg, {
    x: W / 2, y: 24, lines: ["合併(パーチェス法)におけるのれん・負ののれんの発生構造"],
    fontSize: 15, fontWeight: "700", fill: COLORS.inkStrong,
  });
  drawLines(svg, {
    x: W / 2, y: 46, lines: ["対価と受け入れた純資産(時価評価額)を比較し、差額の性質を判定する"],
    fontSize: 11, fill: COLORS.inkMuted,
  });

  const neutralBox = { fill: COLORS.surfaceRaised, stroke: COLORS.hairline, textColor: COLORS.inkSecondary };
  const goodwillBox = { fill: COLORS.accentWash, stroke: COLORS.accent, textColor: COLORS.inkStrong };

  // ケースA: 対価 > 受入純資産額 → のれん(資産計上・償却)
  const considerationA = 500000;
  const netAssetsA = 380000;
  const diffA = goodwillOrNegativeGoodwill(considerationA, netAssetsA); // > 0 → のれん

  // ケースB: 対価 < 受入純資産額 → 負ののれん発生益(特別利益として一括計上)
  const considerationB = 300000;
  const netAssetsB = 380000;
  const diffB = goodwillOrNegativeGoodwill(considerationB, netAssetsB); // < 0 → 負ののれん発生益

  function panel({ centerX, labelText, consideration, netAssets, resultLines, resultCaption }) {
    const boxW = 110, boxH = 64;
    const box1X = centerX - boxW - 15;
    const box2X = centerX + 15;
    const boxY = 92;

    drawLines(svg, {
      x: centerX, y: 76, lines: [labelText], fontSize: 12, fontWeight: "700", fill: COLORS.inkStrong,
    });

    drawBox(svg, {
      x: box1X, y: boxY, w: boxW, h: boxH, ...neutralBox, fontSize: 12,
      lines: ["対価", yen(consideration)],
    });
    drawBox(svg, {
      x: box2X, y: boxY, w: boxW, h: boxH, ...neutralBox, fontSize: 11,
      lines: ["受入純資産", "(時価評価額)", yen(netAssets)],
    });

    drawArrow(svg, {
      x1: centerX, y1: boxY + boxH, x2: centerX, y2: boxY + boxH + 36, color: COLORS.inkMuted,
    });

    const resultW = 190, resultH = 58;
    drawBox(svg, {
      x: centerX - resultW / 2, y: boxY + boxH + 36, w: resultW, h: resultH,
      ...goodwillBox, fontWeight: "700", lines: resultLines,
    });

    drawLines(svg, {
      x: centerX, y: boxY + boxH + 36 + resultH + 18, lines: [resultCaption],
      fontSize: 11, fill: COLORS.inkMuted,
    });
  }

  panel({
    centerX: 160,
    labelText: "ケースA: 対価 > 受入純資産額",
    consideration: considerationA,
    netAssets: netAssetsA,
    resultLines: ["のれん", yen(diffA)],
    resultCaption: "資産として計上し、複数期間で償却する",
  });

  panel({
    centerX: 480,
    labelText: "ケースB: 対価 < 受入純資産額",
    consideration: considerationB,
    netAssets: netAssetsB,
    resultLines: ["負ののれん発生益", yen(Math.abs(diffB))],
    resultCaption: "特別利益として発生した期に一括計上する",
  });

  drawLines(svg, {
    x: W / 2, y: 296, lines: ["のれん(負の場合は負ののれん発生益) = 対価 − 受入純資産額"],
    fontSize: 12, fontWeight: "700", fill: COLORS.inkSecondary,
  });
  drawLines(svg, {
    x: W / 2, y: 316,
    lines: [
      `ケースA: ${yen(considerationA)} − ${yen(netAssetsA)} = ${yen(diffA)} → のれん`,
    ],
    fontSize: 11, fill: COLORS.inkMuted,
  });
  drawLines(svg, {
    x: W / 2, y: 334,
    lines: [
      `ケースB: ${yen(considerationB)} − ${yen(netAssetsB)} = ${yen(diffB)} → 負ののれん発生益 ${yen(Math.abs(diffB))}`,
    ],
    fontSize: 11, fill: COLORS.inkMuted,
  });

  return svg;
}

// ---------------------------------------------------------------------------
// 2. 資本連結のタイムテーブル(支配獲得時〜翌期以降)
// ---------------------------------------------------------------------------
function buildCapitalConsolidationTimetable() {
  const H = 366;
  const svg = createSvg(W, H);

  drawLines(svg, {
    x: W / 2, y: 24, lines: ["資本連結のタイムテーブル(支配獲得時〜翌期以降)"],
    fontSize: 15, fontWeight: "700", fill: COLORS.inkStrong,
  });

  // 前提条件(すべての金額はここから計算し、テーブル側で独立に数値を打ち直さない)
  const parentShareRatio = 0.8; // 親会社の持分比率
  const nciRatio = 1 - parentShareRatio; // 非支配株主持分比率
  const investmentCost = 220000; // 子会社株式の取得原価

  const capitalStock = 200000; // 子会社の資本金(以後変動なしと仮定)
  const retainedAtAcquisition = 50000; // 支配獲得時の子会社利益剰余金
  const capitalTotalAtAcquisition = capitalStock + retainedAtAcquisition;
  const goodwillAtAcquisition = investmentCost - capitalTotalAtAcquisition * parentShareRatio;
  const nciAtAcquisition = capitalTotalAtAcquisition * nciRatio;
  const goodwillUsefulLifeYears = 10; // のれん償却年数(定額法)
  const goodwillAmortizationPerYear = goodwillAtAcquisition / goodwillUsefulLifeYears;

  // 1期目: 子会社の当期純利益・配当
  const netIncomeYear1 = 40000;
  const dividendYear1 = 10000;
  const retainedAtYear1End = retainedAtAcquisition + netIncomeYear1 - dividendYear1;
  const capitalTotalAtYear1End = capitalStock + retainedAtYear1End;
  const goodwillAtYear1End = goodwillAtAcquisition - goodwillAmortizationPerYear;
  const nciAtYear1End = nciAtAcquisition + netIncomeYear1 * nciRatio - dividendYear1 * nciRatio;

  // 2期目以降: 開始仕訳の利益剰余金期首残高は「前期末の利益剰余金」をそのまま引き継ぐ
  const netIncomeYear2 = 30000;
  const retainedAtYear2Start = retainedAtYear1End; // 引継ぎ(独立に打ち直さない)
  const retainedAtYear2End = retainedAtYear2Start + netIncomeYear2;
  const capitalTotalAtYear2End = capitalStock + retainedAtYear2End;
  const goodwillAtYear2End = goodwillAtYear1End - goodwillAmortizationPerYear;
  const nciAtYear2End = nciAtYear1End + netIncomeYear2 * nciRatio;

  drawLines(svg, {
    x: W / 2, y: 44,
    lines: [`前提: 親会社持分比率${Math.round(parentShareRatio * 100)}%(非支配株主持分${Math.round(nciRatio * 100)}%)、子会社株式の取得原価 ${yen(investmentCost)}`],
    fontSize: 11, fill: COLORS.inkMuted,
  });

  const colWidths = [160, 160, 160, 160];
  const captionStyle = { fill: COLORS.surfaceRaised, stroke: COLORS.hairline, textColor: COLORS.inkSecondary };
  const captionY = 60, captionH = 66;

  const captions = [
    {
      x: colWidths[0],
      lines: ["投資と資本を相殺消去", "差額はのれんに計上", "非支配株主持分を計上"],
    },
    {
      x: colWidths[0] + colWidths[1],
      lines: ["当期純利益を", "非支配株主持分比率で按分", "のれんを償却"],
    },
    {
      x: colWidths[0] + colWidths[1] + colWidths[2],
      lines: ["前期末の利益剰余金を", "開始仕訳の期首残高", "として引き継ぐ"],
    },
  ];
  captions.forEach((c, i) => {
    drawBox(svg, {
      x: c.x, y: captionY, w: colWidths[i + 1], h: captionH, ...captionStyle,
      fontSize: 10, fontWeight: "600", lineHeight: 14, lines: c.lines,
    });
  });

  const tableY = captionY + captionH + 12;
  const rowHeight = 26;
  const rows = [
    ["科目", "支配獲得時", "1期目末", "2期目以降"],
    ["資本金", yen(capitalStock), yen(capitalStock), yen(capitalStock)],
    ["利益剰余金", yen(retainedAtAcquisition), yen(retainedAtYear1End), yen(retainedAtYear2End)],
    ["資本合計", yen(capitalTotalAtAcquisition), yen(capitalTotalAtYear1End), yen(capitalTotalAtYear2End)],
    ["のれん", yen(goodwillAtAcquisition), yen(goodwillAtYear1End), yen(goodwillAtYear2End)],
    ["非支配株主持分", yen(nciAtAcquisition), yen(nciAtYear1End), yen(nciAtYear2End)],
  ];

  drawTable(svg, {
    x: 0, y: tableY, rowHeight, colWidths, rows,
    headerFill: COLORS.accentWash, bodyFill: COLORS.surfaceRaised, stroke: COLORS.hairline,
    headerTextColor: COLORS.inkStrong, bodyTextColor: COLORS.inkSecondary, fontSize: 12,
  });

  const tableBottom = tableY + rowHeight * rows.length;
  drawLines(svg, {
    x: W / 2, y: tableBottom + 20,
    lines: [`のれん償却額(1年あたり) = ${yen(goodwillAtAcquisition)} ÷ ${goodwillUsefulLifeYears}年 = ${yen(goodwillAmortizationPerYear)}`],
    fontSize: 11, fill: COLORS.inkMuted,
  });
  drawLines(svg, {
    x: W / 2, y: tableBottom + 38,
    lines: [`1期目は配当 ${yen(dividendYear1)} を実施(非支配株主持分もその${Math.round(nciRatio * 100)}%を負担)`],
    fontSize: 11, fill: COLORS.inkMuted,
  });

  return svg;
}

// ---------------------------------------------------------------------------
// 3. 連結会計における未実現利益消去(ダウンストリーム・アップストリームの違い)
// ---------------------------------------------------------------------------
function buildDownstreamUpstreamCompare() {
  const H = 410;
  const svg = createSvg(W, H);

  drawLines(svg, {
    x: W / 2, y: 24,
    lines: ["連結会計における未実現利益消去(ダウンストリーム・アップストリームの違い)"],
    fontSize: 12, fontWeight: "700", fill: COLORS.inkStrong,
  });

  const neutralBox = { fill: COLORS.surfaceRaised, stroke: COLORS.hairline, textColor: COLORS.inkSecondary };
  const affectedBox = { fill: COLORS.accentWash, stroke: COLORS.accent, textColor: COLORS.inkStrong };

  const parentX = 30, subX = 490, companyW = 120, companyH = 50;

  function panel({ panelLabel, labelY, rowAY, arrowFromParentToSub, parentLines, subLines,
    noteY, noteText, rowBY, leftLines, leftHighlighted, rightLines, rightHighlighted }) {
    drawLines(svg, {
      x: W / 2, y: labelY, lines: [panelLabel], fontSize: 13, fontWeight: "700", fill: COLORS.inkStrong,
    });

    drawBox(svg, {
      x: parentX, y: rowAY, w: companyW, h: companyH, ...neutralBox, fontSize: 11,
      lines: parentLines,
    });
    drawBox(svg, {
      x: subX, y: rowAY, w: companyW, h: companyH, ...neutralBox, fontSize: 11,
      lines: subLines,
    });

    if (arrowFromParentToSub) {
      drawArrow(svg, {
        x1: parentX + companyW, y1: rowAY + companyH / 2,
        x2: subX, y2: rowAY + companyH / 2, color: COLORS.inkSecondary, label: "商品を販売",
      });
    } else {
      drawArrow(svg, {
        x1: subX, y1: rowAY + companyH / 2,
        x2: parentX + companyW, y2: rowAY + companyH / 2, color: COLORS.inkSecondary, label: "商品を販売",
      });
    }

    drawLines(svg, {
      x: W / 2, y: noteY, lines: [noteText], fontSize: 11, fill: COLORS.inkMuted,
    });

    const boxW = 220, boxH = 50;
    drawBox(svg, {
      x: W / 2 - boxW - 10, y: rowBY, w: boxW, h: boxH,
      ...(leftHighlighted ? affectedBox : neutralBox), fontWeight: "700", fontSize: 11,
      lines: leftLines,
    });
    drawBox(svg, {
      x: W / 2 + 10, y: rowBY, w: boxW, h: boxH,
      ...(rightHighlighted ? affectedBox : neutralBox), fontWeight: "700", fontSize: 11,
      lines: rightLines,
    });
  }

  // ダウンストリーム: 親会社→子会社への販売。未実現利益は全額を親会社持分から消去し、
  // 非支配株主持分には影響しない。
  const downstreamProfit = 30000;

  panel({
    panelLabel: "① ダウンストリーム(親会社 → 子会社)",
    labelY: 52,
    rowAY: 66,
    arrowFromParentToSub: true,
    parentLines: ["親会社", "(販売元)"],
    subLines: ["子会社", "(期末在庫として保有)"],
    noteY: 140,
    noteText: `期末在庫に含まれる未実現利益 ${yen(downstreamProfit)}(全額を消去)`,
    rowBY: 156,
    leftLines: ["親会社持分", `${yen(downstreamProfit)}を全額消去`],
    leftHighlighted: true,
    rightLines: ["非支配株主持分", "影響なし(消去の負担なし)"],
    rightHighlighted: false,
  });

  // アップストリーム: 子会社→親会社への販売。未実現利益は非支配株主持分比率に応じて
  // 非支配株主にも負担させる(親会社持分・非支配株主持分の両方に影響)。
  const upstreamProfit = 40000;
  const upstreamNciRatio = 0.2;
  const nciShareUpstream = upstreamProfit * upstreamNciRatio;
  const parentShareUpstream = upstreamProfit - nciShareUpstream;

  panel({
    panelLabel: "② アップストリーム(子会社 → 親会社)",
    labelY: 238,
    rowAY: 252,
    arrowFromParentToSub: false,
    parentLines: ["親会社", "(期末在庫として保有)"],
    subLines: ["子会社", "(販売元)"],
    noteY: 326,
    noteText: `期末在庫に含まれる未実現利益 ${yen(upstreamProfit)}(非支配株主持分比率${Math.round(upstreamNciRatio * 100)}%)`,
    rowBY: 342,
    leftLines: ["親会社持分", `${yen(parentShareUpstream)}を負担`],
    leftHighlighted: true,
    rightLines: ["非支配株主持分", `${yen(nciShareUpstream)}を負担`],
    rightHighlighted: true,
  });

  return svg;
}

export const consolidationDiagrams = {
  "merger-goodwill-structure": buildMergerGoodwillStructure,
  "capital-consolidation-timetable": buildCapitalConsolidationTimetable,
  "downstream-upstream-compare": buildDownstreamUpstreamCompare,
};
