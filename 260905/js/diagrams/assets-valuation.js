// 資産の評価・リースまわりの図解(銀行勘定調整表・その他有価証券・減価償却・リース取引)。
// AI生成画像で数値の不整合・他論点の混入が発生したため、既存のchart.js/helpers.jsと
// 同じ「createElementNSのみ・COLORSトークンのみ」の方針で手書きし直す。
import { COLORS } from "../config.js";
import {
  NS, createSvg, drawBox, drawArrow, drawLines, ensureArrowMarker, yen,
} from "./helpers.js";

const W = 640;

// 符号付きの金額表示(+/-)。個々の調整項目のように増減どちらもあり得る値に使う。
function signedYen(n) {
  return `${n >= 0 ? "+" : "-"}${yen(Math.abs(n))}`;
}

function mkLine(svg, { x1, y1, x2, y2, stroke, strokeWidth = 1.5, dashed = false, linecap }) {
  const line = document.createElementNS(NS, "line");
  line.setAttribute("x1", x1); line.setAttribute("y1", y1);
  line.setAttribute("x2", x2); line.setAttribute("y2", y2);
  line.setAttribute("stroke", stroke);
  line.setAttribute("stroke-width", strokeWidth);
  if (dashed) line.setAttribute("stroke-dasharray", dashed === true ? "4 3" : dashed);
  if (linecap) line.setAttribute("stroke-linecap", linecap);
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

function mkPolyline(svg, { points, stroke, strokeWidth = 2, dashed, linecap }) {
  const poly = document.createElementNS(NS, "polyline");
  poly.setAttribute("points", points.map(([x, y]) => `${x},${y}`).join(" "));
  poly.setAttribute("fill", "none");
  poly.setAttribute("stroke", stroke);
  poly.setAttribute("stroke-width", strokeWidth);
  if (dashed) poly.setAttribute("stroke-dasharray", dashed);
  if (linecap) poly.setAttribute("stroke-linecap", linecap);
  svg.appendChild(poly);
  return poly;
}

// 2点をつなぐ曲線矢印(洗替サイクルのループ表現用)。helpers.jsのdrawArrowは直線専用のため、
// 同じマーカー機構(ensureArrowMarker)を再利用してpathで曲線矢印を描く。
function drawLoopArrow(svg, { x1, y1, x2, y2, dipY, color, label }) {
  const markerId = `arrow-${color.replace(/[^a-zA-Z0-9]/g, "")}`;
  ensureArrowMarker(svg, markerId, color);
  const midX = (x1 + x2) / 2;
  const path = document.createElementNS(NS, "path");
  path.setAttribute("d", `M ${x1} ${y1} Q ${midX} ${dipY} ${x2} ${y2}`);
  path.setAttribute("fill", "none");
  path.setAttribute("stroke", color);
  path.setAttribute("stroke-width", 1.5);
  path.setAttribute("stroke-dasharray", "4 3");
  path.setAttribute("marker-end", `url(#${markerId})`);
  svg.appendChild(path);
  if (label) {
    drawLines(svg, {
      x: midX, y: dipY + 14, lines: [label], fontSize: 11, anchor: "middle", fill: color, fontWeight: "600",
    });
  }
}

// ---------------------------------------------------------------------------
// 1. 銀行勘定調整表(不一致の原因の整理)
// ---------------------------------------------------------------------------
function buildBankReconciliationDiagram() {
  const H = 400;
  const svg = createSvg(W, H);

  drawLines(svg, {
    x: W / 2, y: 24, lines: ["銀行勘定調整表(不一致の原因の整理)"],
    fontSize: 16, fontWeight: "700", fill: COLORS.inkStrong,
  });
  drawLines(svg, {
    x: W / 2, y: 44, lines: ["帳簿残高と銀行残高のズレを、仕訳の要否で整理する"],
    fontSize: 11, fill: COLORS.inkMuted,
  });

  // 企業側の修正仕訳が必要な項目。調整後残高は企業残高から積み上げて算出する。
  const companyBalance = 500000; // 企業の当座預金勘定残高
  const undeliveredCheck = 30000; // 未渡小切手(足し戻す)
  const misrecordingCorrection = -8000; // 誤記入の修正(会社側の過大計上を減算)
  const bankCollectionNotice = 20000; // 連絡未通知(銀行は入金済みだが会社未記帳)
  const companyAdjTotal = undeliveredCheck + misrecordingCorrection + bankCollectionNotice;
  const adjustedBalance = companyBalance + companyAdjTotal; // 両者が一致すべき最終残高

  // 銀行側は時間の経過で自然に一致する項目(仕訳不要)。時間外預入は「最終的に企業側と
  // 一致する」という調整表の性質から逆算し、独立した2つ目の数値として書かない。
  const bankBalance = 480000; // 銀行残高証明書残高
  const unclearedDeposit = 50000; // 未取立小切手
  const outstandingCheck = 15000; // 未取付小切手
  const afterHoursDeposit = adjustedBalance - bankBalance - unclearedDeposit + outstandingCheck; // 時間外預入
  const bankAdjTotal = unclearedDeposit - outstandingCheck + afterHoursDeposit;

  const neutralStyle = { fill: COLORS.surfaceRaised, stroke: COLORS.hairline, textColor: COLORS.inkSecondary };
  const companyStyle = { fill: COLORS.accentWash, stroke: COLORS.accent, textColor: COLORS.inkStrong };
  const resultStyle = { fill: COLORS.accentWash, stroke: COLORS.accent, textColor: COLORS.inkStrong };

  const companyTop = { x: 30, y: 64, w: 240, h: 44 };
  drawBox(svg, {
    ...companyTop, ...neutralStyle, fontSize: 12,
    lines: ["企業の当座預金勘定残高", yen(companyBalance)],
  });
  const bankTop = { x: 370, y: 64, w: 240, h: 44 };
  drawBox(svg, {
    ...bankTop, ...neutralStyle, fontSize: 12,
    lines: ["銀行残高証明書残高", yen(bankBalance)],
  });

  const companyAdj = { x: 30, y: 140, w: 240, h: 110 };
  drawArrow(svg, {
    x1: companyTop.x + companyTop.w / 2, y1: companyTop.y + companyTop.h,
    x2: companyAdj.x + companyAdj.w / 2, y2: companyAdj.y, color: COLORS.inkMuted,
  });
  drawBox(svg, {
    ...companyAdj, ...companyStyle, fontSize: 11, fontWeight: "600", lineHeight: 16,
    lines: [
      "企業側で修正仕訳が必要",
      `未渡小切手 ${signedYen(undeliveredCheck)}`,
      `誤記入 ${signedYen(misrecordingCorrection)}`,
      `連絡未通知 ${signedYen(bankCollectionNotice)}`,
    ],
  });

  const bankAdj = { x: 370, y: 140, w: 240, h: 110 };
  drawArrow(svg, {
    x1: bankTop.x + bankTop.w / 2, y1: bankTop.y + bankTop.h,
    x2: bankAdj.x + bankAdj.w / 2, y2: bankAdj.y, color: COLORS.inkMuted,
  });
  drawBox(svg, {
    ...bankAdj, ...neutralStyle, fontSize: 11, fontWeight: "600", lineHeight: 16,
    lines: [
      "時間の経過で自然に一致(仕訳不要)",
      `未取立小切手 ${signedYen(unclearedDeposit)}`,
      `未取付小切手 ${signedYen(-outstandingCheck)}`,
      `時間外預入 ${signedYen(afterHoursDeposit)}`,
    ],
  });

  const result = { x: 220, y: 296, w: 200, h: 50 };
  drawArrow(svg, {
    x1: companyAdj.x + companyAdj.w / 2, y1: companyAdj.y + companyAdj.h,
    x2: result.x + 40, y2: result.y, color: COLORS.accent,
  });
  drawArrow(svg, {
    x1: bankAdj.x + bankAdj.w / 2, y1: bankAdj.y + bankAdj.h,
    x2: result.x + result.w - 40, y2: result.y, color: COLORS.accent,
  });
  drawBox(svg, {
    ...result, ...resultStyle, fontWeight: "700",
    lines: ["調整後の一致残高", yen(adjustedBalance)],
  });

  drawLines(svg, {
    x: W / 2, y: 364, fontSize: 10, fill: COLORS.inkMuted, lineHeight: 13,
    lines: [
      `企業: ${yen(companyBalance)} + 修正合計${signedYen(companyAdjTotal)} = ${yen(adjustedBalance)}`,
      `銀行: ${yen(bankBalance)} + 調整合計${signedYen(bankAdjTotal)} = ${yen(adjustedBalance)}`,
    ],
  });

  return svg;
}

// ---------------------------------------------------------------------------
// 2. その他有価証券の評価方法(全部/部分純資産直入法)と洗替処理
// ---------------------------------------------------------------------------
function buildOtherSecuritiesValuation() {
  const H = 415;
  const svg = createSvg(W, H);

  drawLines(svg, {
    x: W / 2, y: 24, lines: ["その他有価証券の評価方法(全部/部分純資産直入法)と洗替処理"],
    fontSize: 15, fontWeight: "700", fill: COLORS.inkStrong,
  });

  // 決算時の評価差額の例。時価が上がった場合・下がった場合の両方を同じ取得原価から算出する。
  const acquisitionCost = 100000;
  const fairValueGain = 110000;
  const fairValueLoss = 92000;
  const gainAmount = fairValueGain - acquisitionCost;
  const lossAmount = acquisitionCost - fairValueLoss;

  const neutralStyle = { fill: COLORS.surfaceRaised, stroke: COLORS.hairline, textColor: COLORS.inkSecondary };
  const accentStyle = { fill: COLORS.accentWash, stroke: COLORS.accent, textColor: COLORS.inkStrong };

  const headerL = { x: 30, y: 54, w: 270, h: 30 };
  const headerR = { x: 340, y: 54, w: 270, h: 30 };
  drawBox(svg, { ...headerL, ...neutralStyle, fontWeight: "700", lines: ["全部純資産直入法"] });
  drawBox(svg, { ...headerR, ...neutralStyle, fontWeight: "700", lines: ["部分純資産直入法"] });

  const subH = { y: 96, h: 44 };
  const leftGain = { x: 30, w: 120, ...subH };
  const leftLoss = { x: 180, w: 120, ...subH };
  const rightGain = { x: 340, w: 120, ...subH };
  const rightLoss = { x: 490, w: 120, ...subH };
  drawBox(svg, { ...leftGain, ...neutralStyle, fontSize: 11, lines: ["評価差益", signedYen(gainAmount)] });
  drawBox(svg, { ...leftLoss, ...neutralStyle, fontSize: 11, lines: ["評価差損", signedYen(-lossAmount)] });
  drawBox(svg, { ...rightGain, ...neutralStyle, fontSize: 11, lines: ["評価差益", signedYen(gainAmount)] });
  drawBox(svg, { ...rightLoss, ...neutralStyle, fontSize: 11, lines: ["評価差損", signedYen(-lossAmount)] });

  const destY = 176, destH = 50;
  const leftDest = { x: 30, y: destY, w: 270, h: destH };
  const rightGainDest = { x: 340, y: destY, w: 120, h: destH };
  const rightLossDest = { x: 490, y: destY, w: 120, h: destH };

  drawArrow(svg, { x1: leftGain.x + leftGain.w / 2, y1: leftGain.y + leftGain.h, x2: leftDest.x + 60, y2: leftDest.y, color: COLORS.accent });
  drawArrow(svg, { x1: leftLoss.x + leftLoss.w / 2, y1: leftLoss.y + leftLoss.h, x2: leftDest.x + leftDest.w - 60, y2: leftDest.y, color: COLORS.accent });
  drawBox(svg, {
    ...leftDest, ...accentStyle, fontWeight: "700",
    lines: ["純資産の部", "(その他有価証券評価差額金)"],
  });

  drawArrow(svg, { x1: rightGain.x + rightGain.w / 2, y1: rightGain.y + rightGain.h, x2: rightGainDest.x + rightGainDest.w / 2, y2: rightGainDest.y, color: COLORS.inkMuted });
  drawBox(svg, { ...rightGainDest, ...neutralStyle, fontSize: 11, fontWeight: "700", lines: ["純資産の部", "(評価差額金)"] });

  drawArrow(svg, { x1: rightLoss.x + rightLoss.w / 2, y1: rightLoss.y + rightLoss.h, x2: rightLossDest.x + rightLossDest.w / 2, y2: rightLossDest.y, color: COLORS.accent });
  drawBox(svg, { ...rightLossDest, ...accentStyle, fontSize: 11, fontWeight: "700", lines: ["当期の損失", "(特別損失)"] });

  drawLines(svg, {
    x: W / 2, y: 250, fontSize: 12, fontWeight: "600", fill: COLORS.accentDeep,
    lines: ["違い: 評価差損の処理 — 全部法は純資産へ、部分法は当期の損失へ"],
  });

  drawLines(svg, {
    x: W / 2, y: 276, lines: ["翌期首の洗替処理"], fontSize: 14, fontWeight: "700", fill: COLORS.inkStrong,
  });

  const cycleY = 300, cycleH = 40;
  const box1 = { x: 40, y: cycleY, w: 170, h: cycleH };
  const box2 = { x: 235, y: cycleY, w: 170, h: cycleH };
  const box3 = { x: 430, y: cycleY, w: 170, h: cycleH };
  drawBox(svg, { ...box1, ...neutralStyle, fontSize: 12, lines: ["決算で時価評価"] });
  drawBox(svg, { ...box2, ...neutralStyle, fontSize: 11, lineHeight: 14, lines: ["翌期首に洗替", "(取得原価に戻す)"] });
  drawBox(svg, { ...box3, ...neutralStyle, fontSize: 11, lineHeight: 14, lines: ["次の決算で", "再び時価評価"] });

  const cycleMidY = cycleY + cycleH / 2;
  drawArrow(svg, { x1: box1.x + box1.w, y1: cycleMidY, x2: box2.x, y2: cycleMidY, color: COLORS.inkMuted });
  drawArrow(svg, { x1: box2.x + box2.w, y1: cycleMidY, x2: box3.x, y2: cycleMidY, color: COLORS.inkMuted });

  drawLoopArrow(svg, {
    x1: box3.x + box3.w / 2, y1: cycleY + cycleH,
    x2: box1.x + box1.w / 2, y2: cycleY + cycleH,
    dipY: cycleY + cycleH + 40, color: COLORS.inkMuted, label: "毎期繰り返す",
  });

  return svg;
}

// ---------------------------------------------------------------------------
// 3. 減価償却3方式の比較と定率法の改定償却率
// ---------------------------------------------------------------------------
function buildDepreciationMethodsCompare() {
  const H = 360;
  const svg = createSvg(W, H);

  drawLines(svg, {
    x: W / 2, y: 22, lines: ["減価償却3方式の比較と定率法の改定償却率"],
    fontSize: 15, fontWeight: "700", fill: COLORS.inkStrong,
  });

  const acquisitionCost = 1000000; // 取得原価(残存価額ゼロを前提)
  const usefulLife = 6; // 耐用年数(年)
  const decliningRate = 2 / usefulLife; // 200%定率法の償却率 = 1/耐用年数 × 200%
  const switchYear = 4; // 改定償却率への切替時点(説明用の例示)

  // 定額法: 毎年均等に取得原価が減っていく直線。
  const straightLineValues = [];
  for (let y = 0; y <= usefulLife; y++) {
    straightLineValues.push(acquisitionCost * (1 - y / usefulLife));
  }

  // 定率法(200%定率法): 前年末帳簿価額×(1-償却率)を毎年繰り返す指数的な減少カーブ。
  // switchYear以降は「改定償却率」により残存年数で均等償却(直線でゼロまで)に切り替える。
  const decliningValues = [acquisitionCost];
  for (let y = 1; y <= switchYear; y++) {
    decliningValues.push(decliningValues[y - 1] * (1 - decliningRate));
  }
  const remainingYears = usefulLife - switchYear;
  const perYearStraightDrop = decliningValues[switchYear] / remainingYears;
  for (let y = switchYear + 1; y <= usefulLife; y++) {
    decliningValues.push(decliningValues[y - 1] - perYearStraightDrop);
  }

  // 生産高比例法: 年ごとの利用度合い(生産高の割合、合計100%)に応じて償却する例。
  const productionShares = [0.22, 0.20, 0.18, 0.15, 0.13, 0.12];
  const productionValues = [acquisitionCost];
  let cumulativeShare = 0;
  for (let y = 1; y <= usefulLife; y++) {
    cumulativeShare += productionShares[y - 1];
    productionValues.push(acquisitionCost * (1 - cumulativeShare));
  }

  const pad = { top: 44, right: 34, bottom: 60, left: 78 };
  const plotW = W - pad.left - pad.right;
  const plotH = H - pad.top - pad.bottom;
  const xScale = (year) => pad.left + (year / usefulLife) * plotW;
  const yScale = (value) => pad.top + plotH - (value / acquisitionCost) * plotH;

  // 軸
  mkLine(svg, { x1: pad.left, y1: pad.top + plotH, x2: pad.left + plotW, y2: pad.top + plotH, stroke: COLORS.baseline, strokeWidth: 1.5 });
  mkLine(svg, { x1: pad.left, y1: pad.top, x2: pad.left, y2: pad.top + plotH, stroke: COLORS.baseline, strokeWidth: 1.5 });

  drawLines(svg, { x: pad.left + plotW / 2, y: pad.top + plotH + 34, lines: ["経過年数(年)"], fontSize: 11, fill: COLORS.inkMuted });
  drawLines(svg, { x: pad.left, y: pad.top - 12, lines: ["帳簿価額(円)"], fontSize: 11, fill: COLORS.inkMuted, anchor: "start" });

  for (let y = 0; y <= usefulLife; y++) {
    const x = xScale(y);
    mkLine(svg, { x1: x, y1: pad.top + plotH, x2: x, y2: pad.top + plotH + 5, stroke: COLORS.hairline, strokeWidth: 1 });
    drawLines(svg, { x, y: pad.top + plotH + 18, lines: [String(y)], fontSize: 9, fill: COLORS.inkMuted });
  }
  const Y_TICKS = 4;
  for (let i = 0; i <= Y_TICKS; i++) {
    const v = (acquisitionCost * i) / Y_TICKS;
    const y = yScale(v);
    mkLine(svg, { x1: pad.left - 5, y1: y, x2: pad.left, y2: y, stroke: COLORS.hairline, strokeWidth: 1 });
    if (i > 0) {
      drawLines(svg, { x: pad.left - 8, y: y + 3, lines: [yen(v)], fontSize: 9, fill: COLORS.inkMuted, anchor: "end" });
    }
  }

  const toPoints = (values) => values.map((v, y) => [xScale(y), yScale(v)]);

  // 定額法: 破線
  mkPolyline(svg, { points: toPoints(straightLineValues), stroke: COLORS.inkSecondary, strokeWidth: 2, dashed: "7 4" });
  // 生産高比例法: 点線
  mkPolyline(svg, { points: toPoints(productionValues), stroke: COLORS.inkMuted, strokeWidth: 2, dashed: "1.5 4", linecap: "round" });
  // 定率法(200%定率法): 実線・accentで最重要ラインとして強調
  mkPolyline(svg, { points: toPoints(decliningValues), stroke: COLORS.accent, strokeWidth: 2.5 });

  // 改定償却率への切替点
  const switchX = xScale(switchYear);
  const switchY = yScale(decliningValues[switchYear]);
  mkCircle(svg, { cx: switchX, cy: switchY, r: 4.5, fill: COLORS.accent });
  drawLines(svg, {
    x: switchX, y: switchY + 18, lines: ["改定償却率へ切替"], fontSize: 11, fontWeight: "600", fill: COLORS.accentDeep,
  });

  // 凡例(プロット内の右上、どの線も通らない空きスペースに配置)
  const legendX1 = 460, legendX2 = 490, legendTextX = 496;
  const legendRows = [
    { y: 58, label: "定額法", stroke: COLORS.inkSecondary, dashed: "7 4" },
    { y: 74, label: "定率法(200%定率法)", stroke: COLORS.accent, dashed: undefined },
    { y: 90, label: "生産高比例法", stroke: COLORS.inkMuted, dashed: "1.5 4" },
  ];
  legendRows.forEach((row) => {
    mkLine(svg, { x1: legendX1, y1: row.y, x2: legendX2, y2: row.y, stroke: row.stroke, strokeWidth: 2, dashed: row.dashed, linecap: row.dashed === "1.5 4" ? "round" : undefined });
    drawLines(svg, { x: legendTextX, y: row.y + 4, lines: [row.label], fontSize: 10, fill: row.stroke, anchor: "start" });
  });
  drawLines(svg, {
    x: legendX1, y: 106,
    lines: [`定率法償却率 = 1/${usefulLife}×200%≈${Math.round(decliningRate * 1000) / 10}%`],
    fontSize: 9, fill: COLORS.inkMuted, anchor: "start",
  });

  return svg;
}

// ---------------------------------------------------------------------------
// 4. リース取引の判定フローと当初測定方法の比較
// ---------------------------------------------------------------------------
function buildLeaseClassificationCompare() {
  const H = 430;
  const svg = createSvg(W, H);

  drawLines(svg, {
    x: W / 2, y: 22, lines: ["リース取引の判定フローと当初測定方法の比較"],
    fontSize: 15, fontWeight: "700", fill: COLORS.inkStrong,
  });

  const neutralStyle = { fill: COLORS.surfaceRaised, stroke: COLORS.hairline, textColor: COLORS.inkSecondary };
  const accentStyle = { fill: COLORS.accentWash, stroke: COLORS.accent, textColor: COLORS.inkStrong };

  drawLines(svg, { x: 20, y: 44, lines: ["上段: 判定フロー"], fontSize: 12, fontWeight: "700", fill: COLORS.inkMuted, anchor: "start" });

  const q1 = { x: 20, y: 50, w: 260, h: 70 };
  const q2 = { x: 320, y: 50, w: 280, h: 70 };
  drawBox(svg, {
    ...q1, ...neutralStyle, fontSize: 11, lineHeight: 15,
    lines: ["① 契約を中途解約できない", "(ノンキャンセラブル)か?"],
  });
  drawBox(svg, {
    ...q2, ...neutralStyle, fontSize: 11, lineHeight: 13,
    lines: ["② 借手が経済的利益を", "実質的に享受し、コストも", "実質的に負担するか", "(フルペイアウト)?"],
  });

  const rowMidY = q1.y + q1.h / 2;
  drawArrow(svg, { x1: q1.x + q1.w, y1: rowMidY, x2: q2.x, y2: rowMidY, color: COLORS.inkMuted, label: "はい" });

  const operating = { x: 170, y: 166, w: 270, h: 44 };
  const finance = { x: 460, y: 166, w: 160, h: 44 };

  drawArrow(svg, { x1: q1.x + 130, y1: q1.y + q1.h, x2: operating.x + 100, y2: operating.y, color: COLORS.inkMuted, label: "いいえ" });
  drawArrow(svg, { x1: q2.x + 80, y1: q2.y + q2.h, x2: operating.x + operating.w - 80, y2: operating.y, color: COLORS.inkMuted, label: "いいえ" });
  drawArrow(svg, { x1: q2.x + 240, y1: q2.y + q2.h, x2: finance.x + finance.w / 2, y2: finance.y, color: COLORS.accent, label: "はい" });

  drawBox(svg, { ...operating, ...neutralStyle, fontWeight: "700", lines: ["オペレーティング・リース"] });
  drawBox(svg, { ...finance, ...accentStyle, fontWeight: "700", lines: ["ファイナンス・リース"] });

  drawLines(svg, {
    x: W / 2, y: 234, lines: ["下段: ファイナンス・リースの当初測定方法(利子込み法・利子抜き法)"],
    fontSize: 13, fontWeight: "700", fill: COLORS.inkStrong,
  });

  // 当初測定額の例。差額(利息相当額)は両方式の額の差としてJSで算出し、二重に書かない。
  const annualPayment = 132000; // 年間リース料
  const term = 5; // リース期間(年)
  const leaseTotalPayments = annualPayment * term; // リース料総額(利子込み法の計上額)
  const estimatedCashPrice = 600000; // 見積現金購入価額(利子抜き法の計上額)
  const interestEquivalent = leaseTotalPayments - estimatedCashPrice; // 利息相当額

  const boxA = { x: 30, y: 256, w: 270, h: 86 };
  const boxB = { x: 340, y: 256, w: 270, h: 86 };
  drawBox(svg, {
    ...boxA, ...neutralStyle, fontSize: 11, lineHeight: 16,
    lines: [
      "利子込み法",
      `年間リース料 ${yen(annualPayment)} × ${term}年`,
      `= リース資産・債務 ${yen(leaseTotalPayments)}`,
    ],
  });
  drawBox(svg, {
    ...boxB, ...neutralStyle, fontSize: 11, lineHeight: 16,
    lines: [
      "利子抜き法",
      "見積現金購入価額",
      `= リース資産・債務 ${yen(estimatedCashPrice)}`,
      "(差額は支払時に配分)",
    ],
  });

  const recon = { x: 160, y: 366, w: 320, h: 48 };
  drawArrow(svg, { x1: boxA.x + boxA.w / 2, y1: boxA.y + boxA.h, x2: recon.x + 80, y2: recon.y, color: COLORS.accent });
  drawArrow(svg, { x1: boxB.x + boxB.w / 2, y1: boxB.y + boxB.h, x2: recon.x + recon.w - 80, y2: recon.y, color: COLORS.accent });
  drawBox(svg, {
    ...recon, ...accentStyle, fontSize: 11, fontWeight: "700", lineHeight: 15,
    lines: ["差額(利息相当額)", `${yen(leaseTotalPayments)} − ${yen(estimatedCashPrice)} = ${yen(interestEquivalent)}`],
  });

  return svg;
}

export const assetsValuationDiagrams = {
  "bank-reconciliation-diagram": buildBankReconciliationDiagram,
  "other-securities-valuation": buildOtherSecuritiesValuation,
  "depreciation-methods-compare": buildDepreciationMethodsCompare,
  "lease-classification-compare": buildLeaseClassificationCompare,
};
