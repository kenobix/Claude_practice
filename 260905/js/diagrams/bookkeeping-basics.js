// 簿記の基礎(仕訳・帳簿組織)まわりの図解。
import { COLORS } from "../config.js";
import { createSvg, drawBox, drawArrow, drawLines, drawTAccount, yen } from "./helpers.js";

const W = 640, H = 300;

// 取引 → 三伝票(入金・出金・振替) → 仕訳帳 → 総勘定元帳、という
// 帳簿組織の全体像。「仕訳帳と総勘定元帳の役割の違い」「三伝票制の3種類」の
// 2枚のカードで共有する図。
function buildBookkeepingCycleDiagram() {
  const svg = createSvg(W, H);

  drawLines(svg, {
    x: W / 2, y: 26, lines: ["帳簿組織の全体像(三伝票制)"],
    fontSize: 16, fontWeight: "700", fill: COLORS.inkStrong,
  });

  const boxStyle = { fill: COLORS.surfaceRaised, stroke: COLORS.hairline, textColor: COLORS.inkSecondary };
  const emphasisStyle = { fill: COLORS.accentWash, stroke: COLORS.accent, textColor: COLORS.inkStrong };

  const transaction = { x: 24, y: 145, w: 88, h: 50 };
  drawBox(svg, { ...transaction, ...boxStyle, lines: ["取引"], fontWeight: "700" });

  const slips = [
    { key: "入金伝票", y: 66 },
    { key: "出金伝票", y: 145 },
    { key: "振替伝票", y: 224 },
  ].map((s) => ({ ...s, x: 158, w: 122, h: 48 }));

  slips.forEach((slip) => {
    drawBox(svg, { ...slip, ...boxStyle, lines: [slip.key] });
    drawArrow(svg, {
      x1: transaction.x + transaction.w, y1: transaction.y + transaction.h / 2,
      x2: slip.x, y2: slip.y + slip.h / 2, color: COLORS.inkMuted,
    });
  });

  const journal = { x: 344, y: 145, w: 92, h: 50 };
  drawBox(svg, { ...journal, ...emphasisStyle, lines: ["仕訳帳"], fontWeight: "700" });
  slips.forEach((slip) => {
    drawArrow(svg, {
      x1: slip.x + slip.w, y1: slip.y + slip.h / 2,
      x2: journal.x, y2: journal.y + journal.h / 2, color: COLORS.inkMuted,
    });
  });

  const ledger = { x: 500, y: 145, w: 116, h: 50 };
  drawBox(svg, { ...ledger, ...emphasisStyle, lines: ["総勘定元帳"], fontWeight: "700" });
  drawArrow(svg, {
    x1: journal.x + journal.w, y1: journal.y + journal.h / 2,
    x2: ledger.x, y2: ledger.y + ledger.h / 2, color: COLORS.accent, strokeWidth: 2,
  });

  drawLines(svg, {
    x: journal.x + journal.w / 2, y: journal.y + journal.h + 22,
    lines: ["取引を発生順に記録"], fontSize: 11, fill: COLORS.inkMuted,
  });
  drawLines(svg, {
    x: ledger.x + ledger.w / 2, y: ledger.y + ledger.h + 22,
    lines: ["勘定科目ごとに集計"], fontSize: 11, fill: COLORS.inkMuted,
  });

  return svg;
}

// T字勘定(借方・貸方)の構造と、複式簿記で貸借が常に一致する仕組みを示す図。
// 「複式簿記の基本原則は?」「三分法で用いる3つの勘定は?」の2枚のカードで共有する。
function buildDoubleEntryBasics() {
  const W2 = W, H2 = 360;
  const svg = createSvg(W2, H2);

  drawLines(svg, {
    x: W2 / 2, y: 26, lines: ["複式簿記の基本構造(T字勘定・借方と貸方)"],
    fontSize: 16, fontWeight: "700", fill: COLORS.inkStrong,
  });
  drawLines(svg, {
    x: W2 / 2, y: 48, lines: ["例: 商品を仕入れ、一部を現金、残額を掛けで支払った"],
    fontSize: 12, fill: COLORS.inkMuted,
  });

  // 借方・貸方の金額は同じ取引額から算出し、常に一致することをコードでも保証する。
  const debitAmount = 30000; // 仕入(借方)
  const creditCash = 20000; // 現金支払い(貸方)
  const creditPayable = debitAmount - creditCash; // 買掛金(貸方) = 差額として算出
  const debitTotal = debitAmount;
  const creditTotal = creditCash + creditPayable;

  drawTAccount(svg, {
    x: 170, y: 70, w: 300, h: 110,
    title: "仕訳(現金仕入・掛け仕入の例)",
    debitLines: ["借方", `仕入 ${yen(debitAmount)}`],
    creditLines: ["貸方", `現金 ${yen(creditCash)}`, `買掛金 ${yen(creditPayable)}`],
    stroke: COLORS.accent, textColor: COLORS.inkSecondary, titleColor: COLORS.inkStrong,
    fontSize: 13,
  });

  drawLines(svg, {
    x: W2 / 2, y: 204, lines: [`借方合計 ${yen(debitTotal)} = 貸方合計 ${yen(creditTotal)}`],
    fontSize: 14, fontWeight: "700", fill: COLORS.accentDeep,
  });

  drawLines(svg, {
    x: W2 / 2, y: 236, lines: ["三分法(商品売買で使う3つの勘定)"],
    fontSize: 14, fontWeight: "700", fill: COLORS.inkStrong,
  });

  const sanpouhoStyle = { fill: COLORS.surfaceRaised, stroke: COLORS.hairline, textColor: COLORS.inkSecondary };
  const sanpouho = [
    { key: "仕入", desc: "(仕入れた商品の原価)", x: 60 },
    { key: "売上", desc: "(販売した商品の売価)", x: 240 },
    { key: "繰越商品", desc: "(期末に残る在庫)", x: 420 },
  ];
  sanpouho.forEach((item) => {
    drawBox(svg, {
      x: item.x, y: 252, w: 160, h: 64, ...sanpouhoStyle,
      lines: [item.key, item.desc], fontWeight: "600",
    });
  });

  drawLines(svg, {
    x: W2 / 2, y: 340, lines: ["この3勘定で仕入から販売までを記帳する"],
    fontSize: 11, fill: COLORS.inkMuted,
  });

  return svg;
}

// 貸借対照表(B/S)・損益計算書(P/L)と、その橋渡し役である精算表の関係を示す図。
// 「B/Sを構成する3要素は?」「P/Lを構成する2要素は?」「精算表の構成は?」の
// 3枚のカードで共有する。
function buildFinancialStatementsStructure() {
  const W2 = W, H2 = 380;
  const svg = createSvg(W2, H2);

  drawLines(svg, {
    x: W2 / 2, y: 26, lines: ["貸借対照表・損益計算書・精算表の関係"],
    fontSize: 16, fontWeight: "700", fill: COLORS.inkStrong,
  });

  // 精算表を通じて試算表の金額が決算整理を経て確定額になる例。差額はJSで算出する。
  const trialBalanceAmount = 50000; // 備品(資産)の試算表残高
  const depreciationAdjustment = 5000; // 決算整理: 減価償却費
  const adjustedAmount = trialBalanceAmount - depreciationAdjustment; // 決算整理後の額

  const lowStyle = { fill: COLORS.surfaceRaised, stroke: COLORS.hairline, textColor: COLORS.inkSecondary };
  const bridgeStyle = { fill: COLORS.accentWash, stroke: COLORS.accent, textColor: COLORS.inkStrong };

  const trialBalance = { x: 270, y: 52, w: 100, h: 38 };
  drawBox(svg, { ...trialBalance, ...lowStyle, lines: ["試算表", `備品 ${yen(trialBalanceAmount)}`], fontSize: 11 });

  const worksheet = { x: 245, y: 122, w: 150, h: 60 };
  drawArrow(svg, {
    x1: W2 / 2, y1: trialBalance.y + trialBalance.h,
    x2: W2 / 2, y2: worksheet.y, color: COLORS.inkMuted,
    label: `決算整理 -${yen(depreciationAdjustment)}`, labelFontSize: 11,
  });
  drawBox(svg, {
    ...worksheet, ...bridgeStyle, lines: ["精算表", `整理後 ${yen(adjustedAmount)}`], fontWeight: "700",
  });

  const bsHeader = { x: 30, y: 246, w: 190, h: 32 };
  const plHeader = { x: 420, y: 246, w: 190, h: 32 };
  drawBox(svg, { ...bsHeader, ...lowStyle, lines: ["貸借対照表(B/S)"], fontWeight: "700" });
  drawBox(svg, { ...plHeader, ...lowStyle, lines: ["損益計算書(P/L)"], fontWeight: "700" });

  drawArrow(svg, {
    x1: worksheet.x + 30, y1: worksheet.y + worksheet.h,
    x2: bsHeader.x + bsHeader.w / 2, y2: bsHeader.y, color: COLORS.accent,
    label: "振り分け", labelFontSize: 11,
  });
  drawArrow(svg, {
    x1: worksheet.x + worksheet.w - 30, y1: worksheet.y + worksheet.h,
    x2: plHeader.x + plHeader.w / 2, y2: plHeader.y, color: COLORS.accent,
    label: "振り分け", labelFontSize: 11,
  });

  const bsItems = [
    { key: "資産", x: 34 },
    { key: "負債", x: 96 },
    { key: "純資産", x: 158 },
  ];
  bsItems.forEach((item) => {
    drawBox(svg, { x: item.x, y: 288, w: 58, h: 50, ...lowStyle, lines: [item.key], fontSize: 12 });
  });

  const plItems = [
    { key: "収益", x: 420 },
    { key: "費用", x: 520 },
  ];
  plItems.forEach((item) => {
    drawBox(svg, { x: item.x, y: 288, w: 90, h: 50, ...lowStyle, lines: [item.key], fontSize: 12 });
  });

  drawLines(svg, {
    x: W2 / 2, y: 358, lines: ["精算表が試算表と決算整理の結果をB/SとP/Lに振り分ける"],
    fontSize: 11, fill: COLORS.inkMuted,
  });

  return svg;
}

// 約束手形の振り出しから、裏書譲渡・割引までの流れと関係者を示す図。
// 「裏書譲渡とは?」「手形の割引とは?」の2枚のカードで共有する。
function buildTegataFlowDiagram() {
  const W2 = W, H2 = 320;
  const svg = createSvg(W2, H2);

  drawLines(svg, {
    x: W2 / 2, y: 26, lines: ["約束手形の振り出し・裏書譲渡・割引の流れ"],
    fontSize: 16, fontWeight: "700", fill: COLORS.inkStrong,
  });

  // 割引時に金融機関が支払う手取金は、手形金額から割引料を差し引いた額としてJSで算出する。
  const faceValue = 100000; // 手形金額
  const discountFee = 3000; // 割引料
  const netAmount = faceValue - discountFee; // 手取金

  const lowStyle = { fill: COLORS.surfaceRaised, stroke: COLORS.hairline, textColor: COLORS.inkSecondary };
  const focusStyle = { fill: COLORS.accentWash, stroke: COLORS.accent, textColor: COLORS.inkStrong };

  const drawer = { x: 30, y: 140, w: 110, h: 60 };
  const payee = { x: 250, y: 140, w: 110, h: 60 };
  const thirdParty = { x: 460, y: 50, w: 120, h: 56 };
  const bank = { x: 460, y: 214, w: 120, h: 56 };

  drawBox(svg, { ...drawer, ...lowStyle, lines: ["振出人"], fontWeight: "700" });
  drawBox(svg, { ...payee, ...focusStyle, lines: ["受取人"], fontWeight: "700" });
  drawBox(svg, { ...thirdParty, ...lowStyle, lines: ["第三者"], fontWeight: "700" });
  drawBox(svg, { ...bank, ...lowStyle, lines: ["金融機関", `手取金 ${yen(netAmount)}`], fontSize: 11 });

  drawArrow(svg, {
    x1: drawer.x + drawer.w, y1: drawer.y + drawer.h / 2,
    x2: payee.x, y2: payee.y + payee.h / 2, color: COLORS.inkMuted,
    label: "約束手形を振り出す",
  });
  drawArrow(svg, {
    x1: payee.x + payee.w, y1: payee.y + 14,
    x2: thirdParty.x, y2: thirdParty.y + thirdParty.h / 2, color: COLORS.inkMuted,
    label: "裏書譲渡する",
  });
  drawArrow(svg, {
    x1: payee.x + payee.w, y1: payee.y + payee.h - 14,
    x2: bank.x, y2: bank.y + bank.h / 2, color: COLORS.inkMuted,
    label: "割引に出す",
  });

  drawLines(svg, {
    x: bank.x + bank.w / 2, y: bank.y + bank.h + 26,
    lines: [`手形金額 ${yen(faceValue)} − 割引料 ${yen(discountFee)}`, `= 手取金 ${yen(netAmount)}`],
    fontSize: 10, fill: COLORS.inkMuted, lineHeight: 15, anchor: "end",
  });

  return svg;
}

export const bookkeepingBasicsDiagrams = {
  "bookkeeping-cycle-diagram": buildBookkeepingCycleDiagram,
  "double-entry-basics": buildDoubleEntryBasics,
  "financial-statements-structure": buildFinancialStatementsStructure,
  "tegata-flow-diagram": buildTegataFlowDiagram,
};
