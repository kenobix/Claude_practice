"""
ペルソナの行動を決定するLLM呼び出し部分。

各ターンごとに「現在地・時刻・周辺候補地」をプロンプトとして渡し、
Chain of Thought形式のJSONで次の行動を出力させる。

必要な環境変数:
  GOOGLE_API_KEY または GEMINI_API_KEY
"""

import os
import re
import json
import sys
import time
from google import genai
from google.genai import types
from google.genai import errors

GENERATION_MODEL = "gemini-2.5-flash"

# 無料枠のレート制限（1分あたりのリクエスト数）に収まるよう、呼び出し間隔を空ける。
# gemini-2.5-flashの無料枠は10〜20RPM程度（アカウントにより変動）のため、安全側に間隔を取る。
MIN_REQUEST_INTERVAL_SEC = 6.5
MAX_RETRIES_ON_RATE_LIMIT = 5
DEFAULT_RETRY_WAIT_SEC = 20

DECISION_SCHEMA_HINT = """
以下のJSON形式で**のみ**出力してください（説明文や前後のテキストは一切不要）:
{
  "situation_assessment": "現在の状況をどう認識しているかの要約",
  "options_considered": ["検討した選択肢を簡潔に列挙"],
  "considered_our_product": true または false,
  "choice_index": 候補リストの番号(整数。候補一覧の番号と一致させること),
  "action_type": "move" または "stay" または "enter_and_browse" または "purchase",
  "interest_in_our_product": 0から10の整数（自社製品への関心度。高いほど興味あり）,
  "reason": "なぜその行動を選んだかの理由（自社製品を選ばなかった場合はその理由も明記）"
}
"""


class PersonaAgent:
    def __init__(self, api_key: str | None = None):
        api_key = api_key or os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")
        if not api_key:
            print("エラー: GOOGLE_API_KEY（または GEMINI_API_KEY）環境変数が設定されていません。")
            print("  export GOOGLE_API_KEY='your-api-key'")
            sys.exit(1)
        self.client = genai.Client(api_key=api_key)
        self._last_request_time = 0.0

    def _throttle(self):
        """前回の呼び出しからMIN_REQUEST_INTERVAL_SEC秒以上空くまで待つ。"""
        elapsed = time.monotonic() - self._last_request_time
        wait = MIN_REQUEST_INTERVAL_SEC - elapsed
        if wait > 0:
            time.sleep(wait)

    def _call_with_retry(self, prompt: str):
        """429 (レート制限超過) の場合は自動的に待ってリトライする。"""
        for attempt in range(MAX_RETRIES_ON_RATE_LIMIT + 1):
            self._throttle()
            self._last_request_time = time.monotonic()
            try:
                return self.client.models.generate_content(
                    model=GENERATION_MODEL,
                    contents=prompt,
                    config=types.GenerateContentConfig(response_mime_type="application/json"),
                )
            except errors.ClientError as e:
                if e.code != 429:
                    raise
                if "PerDay" in str(e):
                    # 1日あたりの無料枠を使い切った場合、待っても解消しないためすぐに諦める
                    raise RuntimeError(
                        "Gemini APIの1日あたりの無料枠を使い切りました。\n"
                        "日付が変わる（米国太平洋時間の0時頃）まで待つか、\n"
                        "Google AI Studioで課金を有効化してから再実行してください。\n"
                        f"元のエラー: {e}"
                    ) from e
                if attempt == MAX_RETRIES_ON_RATE_LIMIT:
                    raise
                match = re.search(r"retry in ([\d.]+)s", str(e))
                wait_sec = float(match.group(1)) + 2 if match else DEFAULT_RETRY_WAIT_SEC
                print(f"  [レート制限] {wait_sec:.0f}秒待ってリトライします（{attempt + 1}/{MAX_RETRIES_ON_RATE_LIMIT}）...")
                time.sleep(wait_sec)

    def decide(self, persona: dict, current_time: str, current_location: dict,
               candidates: list[dict], our_product: dict) -> dict:
        """1ターン分の行動決定をLLMに問い合わせ、パース済みdictを返す。"""
        candidate_lines = []
        for i, c in enumerate(candidates):
            tag = " ←自社店舗" if c.get("is_our_store") else ""
            rating = f" 評価{c['rating']}" if c.get("rating") else ""
            candidate_lines.append(
                f"  {i}. {c['name']}（{c['distance_m']}m先{rating}）{tag}"
            )
        candidates_text = "\n".join(candidate_lines)

        prompt = f"""あなたは以下のペルソナになりきって、次の1時間の行動を決定してください。

【ペルソナ】
名前: {persona['name']}（{persona['age']}歳・{persona['occupation']}）
性格: {persona['personality']}
ニーズ: {persona['needs']}
予算感: {persona['budget_jpy']}円程度
価格感度: {persona['price_sensitivity']}

【現在の状況】
時刻: {current_time}
現在地: {current_location['place_name']}

【自社製品の情報（参考。候補リストにも実在すれば含まれる）】
店舗名: {our_product['store_name']}
商品説明: {our_product['description']}
価格: {our_product['price_jpy']}円

【次にどこへ行くか、候補一覧】
  0. このまま留まる（{current_location['place_name']}）
{candidates_text}

候補の中から1つ選び、なぜその選択をしたか（自社製品を検討したか・しなかったか含めて）を考えてください。
注意: action_type="purchase" は、選んだ場所が自社店舗（候補に「←自社店舗」と付いている場所）の場合にのみ使ってください。それ以外の場所では "move" "stay" "enter_and_browse" のいずれかを使ってください。
{DECISION_SCHEMA_HINT}
"""

        response = self._call_with_retry(prompt)

        try:
            decision = json.loads(response.text)
        except (json.JSONDecodeError, TypeError):
            # JSONとして読めなかった場合は安全側のフォールバック（留まる）
            decision = {
                "situation_assessment": "（パース失敗）",
                "options_considered": [],
                "considered_our_product": False,
                "choice_index": 0,
                "action_type": "stay",
                "interest_in_our_product": 0,
                "reason": f"LLM応答のJSONパースに失敗: {response.text[:200]}",
            }

        decision["choice_index"] = max(0, min(int(decision.get("choice_index", 0)), len(candidates)))
        return decision
