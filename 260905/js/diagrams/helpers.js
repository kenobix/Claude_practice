// 図解カード用インラインSVGの共通プリミティブ。
// js/chart.js と同じ方針(createElementNSのみ、色はCOLORSトークン経由)を
// 複数の静的な図で使い回すためのヘルパー集。

export const NS = "http://www.w3.org/2000/svg";

export function createSvg(w, h) {
  const svg = document.createElementNS(NS, "svg");
  svg.setAttribute("viewBox", `0 0 ${w} ${h}`);
  svg.setAttribute("role", "img");
  return svg;
}

function el(tag, attrs) {
  const node = document.createElementNS(NS, tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value !== undefined && value !== null) node.setAttribute(key, value);
  }
  return node;
}

// 複数行テキスト。改行位置は呼び出し側が lines: string[] で指定する
// (このアプリの図は短い正確なラベルが要件のため、自動折返しは実装しない)。
// font-familyはcss/review.cssの `.flashcard-diagram text` で一括指定するため、
// ここでは触らない(chart.jsがCSSクラス経由でフォントを決めているのと同じ方針)。
export function drawLines(svg, {
  x, y, lines, fontSize = 13, lineHeight = 17,
  anchor = "middle", fill, fontWeight = "400",
}) {
  lines.forEach((line, i) => {
    svg.appendChild(el("text", {
      x, y: y + i * lineHeight,
      "text-anchor": anchor,
      "font-size": fontSize,
      "font-weight": fontWeight,
      fill,
    })).textContent = line;
  });
}

// 角丸の箱+中央揃えの複数行ラベル。boxの中心のyを基準に行群を上下中央に配置する。
export function drawBox(svg, {
  x, y, w, h, lines, fill, stroke, strokeWidth = 1.5, rx = 8,
  textColor, fontSize = 13, fontWeight = "600", lineHeight = 17,
}) {
  svg.appendChild(el("rect", {
    x, y, width: w, height: h, rx, fill, stroke, "stroke-width": strokeWidth,
  }));
  const totalTextHeight = (lines.length - 1) * lineHeight;
  const firstLineY = y + h / 2 - totalTextHeight / 2 + fontSize / 3;
  drawLines(svg, {
    x: x + w / 2, y: firstLineY, lines, fontSize, lineHeight,
    anchor: "middle", fill: textColor, fontWeight,
  });
}

// <defs><marker>は同じsvg内で複数回呼ばれても1つだけ生成する(idが同じなら再利用)。
export function ensureArrowMarker(svg, id, color) {
  let defs = svg.querySelector("defs");
  if (!defs) {
    defs = el("defs", {});
    svg.insertBefore(defs, svg.firstChild);
  }
  if (defs.querySelector(`#${id}`)) return;
  const marker = el("marker", {
    id, viewBox: "0 0 10 10", refX: 8, refY: 5,
    markerWidth: 7, markerHeight: 7, orient: "auto-start-reverse",
  });
  marker.appendChild(el("path", { d: "M0,0 L10,5 L0,10 z", fill: color }));
  defs.appendChild(marker);
}

export function drawArrow(svg, {
  x1, y1, x2, y2, color, dashed = false, strokeWidth = 1.5, label, labelFontSize = 12,
}) {
  const markerId = `arrow-${color.replace(/[^a-zA-Z0-9]/g, "")}`;
  ensureArrowMarker(svg, markerId, color);
  svg.appendChild(el("line", {
    x1, y1, x2, y2, stroke: color, "stroke-width": strokeWidth,
    "stroke-dasharray": dashed ? "4 3" : undefined,
    "marker-end": `url(#${markerId})`,
  }));
  if (label) {
    drawLines(svg, {
      x: (x1 + x2) / 2, y: (y1 + y2) / 2 - 6, lines: [label],
      fontSize: labelFontSize, anchor: "middle", fill: color, fontWeight: "600",
    });
  }
}

// T字勘定(借方/貸方の2カラム)。簿記図解で繰り返し必要になるため専用ヘルパーにする。
export function drawTAccount(svg, {
  x, y, w, h, title, debitLines = [], creditLines = [],
  stroke, textColor, titleColor, fontSize = 12,
}) {
  const halfW = w / 2;
  drawLines(svg, {
    x: x + halfW, y: y - 6, lines: [title], fontSize: 13,
    anchor: "middle", fill: titleColor, fontWeight: "700",
  });
  svg.appendChild(el("line", { x1: x, y1: y, x2: x + w, y2: y, stroke, "stroke-width": 1.5 }));
  svg.appendChild(el("line", { x1: x + halfW, y1: y, x2: x + halfW, y2: y + h, stroke, "stroke-width": 1.5 }));
  svg.appendChild(el("rect", {
    x, y, width: w, height: h, fill: "none", stroke, "stroke-width": 1.5,
  }));
  drawLines(svg, {
    x: x + halfW / 2, y: y + 16, lines: debitLines, fontSize,
    anchor: "middle", fill: textColor, fontWeight: "400", lineHeight: 15,
  });
  drawLines(svg, {
    x: x + halfW + halfW / 2, y: y + 16, lines: creditLines, fontSize,
    anchor: "middle", fill: textColor, fontWeight: "400", lineHeight: 15,
  });
}

// 簡易テーブル(見出し行+データ行)。colWidthsとrows(2次元配列)からグリッドを組む。
export function drawTable(svg, {
  x, y, rowHeight = 22, colWidths, rows, headerFill, bodyFill,
  stroke, headerTextColor, bodyTextColor, fontSize = 12,
}) {
  const totalW = colWidths.reduce((a, b) => a + b, 0);
  rows.forEach((row, r) => {
    const rowY = y + r * rowHeight;
    svg.appendChild(el("rect", {
      x, y: rowY, width: totalW, height: rowHeight,
      fill: r === 0 ? headerFill : bodyFill,
      stroke, "stroke-width": 1,
    }));
    let colX = x;
    row.forEach((cell, c) => {
      if (c > 0) {
        svg.appendChild(el("line", {
          x1: colX, y1: rowY, x2: colX, y2: rowY + rowHeight, stroke, "stroke-width": 1,
        }));
      }
      drawLines(svg, {
        x: colX + colWidths[c] / 2, y: rowY + rowHeight / 2 + fontSize / 3,
        lines: [cell], fontSize, anchor: "middle",
        fill: r === 0 ? headerTextColor : bodyTextColor,
        fontWeight: r === 0 ? "700" : "400",
      });
      colX += colWidths[c];
    });
  });
}

export function yen(n) {
  return `¥${n.toLocaleString("ja-JP")}`;
}
