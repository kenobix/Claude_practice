// 簿記の基礎(仕訳・帳簿組織)まわりの図解。
import { COLORS } from "../config.js";
import { createSvg, drawBox, drawArrow, drawLines } from "./helpers.js";

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

export const bookkeepingBasicsDiagrams = {
  "bookkeeping-cycle-diagram": buildBookkeepingCycleDiagram,
};
