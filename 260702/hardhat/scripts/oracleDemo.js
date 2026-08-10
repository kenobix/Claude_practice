// フェーズ3: AIオラクル型デモ
// [入力データ] → [Gemini APIで解析・判定] → [結果をハッシュ化] → [オンチェーンに記録] → [検証]
require("dotenv").config();
const crypto = require("crypto");
const { ethers } = require("hardhat");

const MODEL = "gemini-flash-latest";

// フェーズ3の製品案「AI審査付き証明書発行」を想定した審査対象のサンプル文書
const DOCUMENT_TITLE = "作品説明文: 「朝の光」";
const DOCUMENT_TEXT = `
この作品は、通勤中に見た朝焼けの空をモチーフに、
青からオレンジへのグラデーションを水彩絵の具で表現した個人制作の絵画です。
制作期間は約2週間、使用画材は水彩紙・透明水彩絵の具のみで、
デジタル加工や他者の作品のトレースは一切行っていません。
`.trim();

function sha256Hex(text) {
  return crypto.createHash("sha256").update(text, "utf8").digest("hex");
}

// キーをアルファベット順に固定してJSON化することで、
// 同じ内容なら誰が計算しても同じハッシュ値になるようにする(canonicalization)
function canonicalize(record) {
  return JSON.stringify(record, Object.keys(record).sort());
}

function recordHashOf(record) {
  return "0x" + sha256Hex(canonicalize(record));
}

async function askGeminiToReview(title, text) {
  const { GoogleGenAI, Type } = await import("@google/genai");
  const ai = new GoogleGenAI({ apiKey: process.env.GEMINI_API_KEY });

  const prompt = `あなたは創作物の権利証明を発行する審査担当者です。
以下の作品説明文を読み、オリジナリティ(独自制作である旨の説明が具体的か)と
内容の一貫性を確認したうえで、証明書を発行してよいか審査してください。

タイトル: ${title}
説明文:
${text}`;

  const response = await ai.models.generateContent({
    model: MODEL,
    contents: prompt,
    config: {
      responseMimeType: "application/json",
      responseSchema: {
        type: Type.OBJECT,
        properties: {
          verdict: { type: Type.STRING, enum: ["承認", "要確認"] },
          score: { type: Type.INTEGER },
          reason: { type: Type.STRING },
        },
        required: ["verdict", "score", "reason"],
      },
    },
  });

  return JSON.parse(response.text);
}

async function main() {
  if (!process.env.GEMINI_API_KEY) {
    throw new Error("GEMINI_API_KEY が設定されていません(.envを確認してください)");
  }

  console.log("========== 1. Gemini APIによる審査 ==========");
  const verdictResult = await askGeminiToReview(DOCUMENT_TITLE, DOCUMENT_TEXT);
  console.log(`モデル: ${MODEL}`);
  console.log(`審査結果: verdict=${verdictResult.verdict}, score=${verdictResult.score}`);
  console.log(`理由: ${verdictResult.reason}`);

  console.log("\n========== 2. 判定結果のハッシュ化 ==========");
  const record = {
    document_hash: sha256Hex(DOCUMENT_TEXT),
    document_title: DOCUMENT_TITLE,
    issued_at: new Date().toISOString(),
    model: MODEL,
    reason: verdictResult.reason,
    score: verdictResult.score,
    verdict: verdictResult.verdict,
  };
  const recordHash = recordHashOf(record);
  console.log(`入力文書のハッシュ: 0x${record.document_hash}`);
  console.log(`判定結果レコード全体のハッシュ: ${recordHash}`);

  console.log("\n========== 3. オンチェーンへの記録 ==========");
  const [submitter] = await ethers.getSigners();
  const Registry = await ethers.getContractFactory("ProvenanceRegistry");
  const registry = await Registry.deploy();
  await registry.waitForDeployment();
  console.log("ProvenanceRegistry デプロイ先:", await registry.getAddress());

  const metadata = `verdict=${record.verdict}, score=${record.score}`;
  const tx = await registry.connect(submitter).register(recordHash, metadata);
  await tx.wait();
  console.log(`register()実行: submitter=${submitter.address}, metadata="${metadata}"`);

  console.log("\n========== 4. 正当な検証(改ざんなし) ==========");
  // 検証者は開示された審査結果(record)から独立にハッシュを再計算し、オンチェーンの値と比較する
  const recomputedHash = recordHashOf(record);
  const matchesOriginal = recomputedHash === recordHash;
  const isRegisteredOriginal = await registry.isRegistered(recomputedHash);
  console.log(`開示された審査結果から再計算したハッシュ: ${recomputedHash}`);
  console.log(`オンチェーンの記録と一致: ${matchesOriginal}`);
  console.log(`isRegistered(recomputedHash) = ${isRegisteredOriginal}`);

  console.log("\n========== 5. 改ざんの検知 ==========");
  // 攻撃者が「score=100かつverdict=承認だった」と主張して審査結果を書き換えたケースを想定
  const tamperedRecord = { ...record, score: 100, verdict: "承認" };
  const tamperedHash = recordHashOf(tamperedRecord);
  const matchesTampered = tamperedHash === recordHash;
  const isRegisteredTampered = await registry.isRegistered(tamperedHash);
  console.log(`改ざん後(score=100, verdict=承認)の主張から再計算したハッシュ: ${tamperedHash}`);
  console.log(`オンチェーンの記録と一致: ${matchesTampered}`);
  console.log(`isRegistered(tamperedHash) = ${isRegisteredTampered}`);

  console.log(
    "\n" +
      "AIオラクル型パターンでは、Gemini APIによる審査結果そのものはオンチェーンに置かず、" +
      "結果のハッシュ値だけを記録する。これにより、検証者は開示された審査結果(平文)から" +
      "同じ手順でハッシュを再計算し、オンチェーンの値と突き合わせるだけで改ざんの有無を" +
      "判定できる。上記のとおり、正当な審査結果は一致し(isRegistered=true)、" +
      "1箇所でも値を書き換えた主張は別のハッシュになるため一致しない(isRegistered=false)。" +
      "フェーズ1で確認した『ハッシュは入力が1ビットでも変わると別物になる』という性質を、" +
      "生成AIの判定結果という実用的なデータに応用した形になっている。"
  );
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
