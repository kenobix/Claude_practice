#!/usr/bin/env node
// js/diagrams/ のビルダーをヘッドレスChromiumで実際にレンダリングし、
// 目視QA用のPNGを 260905/tools/qa-output/ に書き出す使い捨てツール。
// 事前に `python3 -m http.server 8905` を 260905/ 直下で起動しておくこと。
//
// 使い方:
//   node tools/screenshot-diagrams.mjs bookkeeping-cycle-diagram double-entry-basics
//   node tools/screenshot-diagrams.mjs            # 引数なし = 登録済み全図をまとめて1枚に

import { execFileSync } from "node:child_process";
import { mkdirSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const __dirname = dirname(fileURLToPath(import.meta.url));
const outDir = join(__dirname, "qa-output");
mkdirSync(outDir, { recursive: true });

const BASE_URL = "http://localhost:8905/tools/diagram-gallery.html";
const keys = process.argv.slice(2);

function shoot(url, outFile) {
  execFileSync(
    "npx",
    ["--yes", "playwright", "screenshot", "--viewport-size=700,900", "--full-page", url, outFile],
    { stdio: "inherit" },
  );
}

if (keys.length === 0) {
  const outFile = join(outDir, "_all.png");
  shoot(BASE_URL, outFile);
  console.log(`書き出し先: ${outFile}`);
} else {
  keys.forEach((key) => {
    const outFile = join(outDir, `${key}.png`);
    shoot(`${BASE_URL}?only=${encodeURIComponent(key)}`, outFile);
    console.log(`書き出し先: ${outFile}`);
  });
}
