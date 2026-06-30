"""
AI箱庭シミュレーション本体。

ペルソナごとに1ターン(=既定で1時間)ずつ、
  1. 現在地周辺の施設をGoogle Places APIで取得
  2. 自社店舗を候補に追加（実在しない店でも常に選択肢に入れる）
  3. LLMに「次にどこへ行くか」をChain of Thought付きJSONで決定させる
  4. 結果をログ(JSON)に記録し、位置を更新する
を1日分（scenario.jsonのstart_hour〜end_hour）繰り返す。

実行:
  export GOOGLE_API_KEY="your-api-key"
  export GOOGLE_MAPS_API_KEY="your-maps-api-key"
  python3 simulate.py
  python3 simulate.py --scenario scenario_b.json --out logs/sim_b.json
"""

import argparse
import json
import math
from datetime import datetime, timedelta

from places_client import PlacesClient
from llm_agent import PersonaAgent

MAX_CANDIDATES = 8
OUR_STORE_DEDUPE_RADIUS_M = 50


def haversine_m(lat1, lng1, lat2, lng2):
    r = 6371000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def build_candidates(places_client: PlacesClient, current_location: dict, scenario: dict) -> list[dict]:
    """周辺施設＋自社店舗を1つの候補リストにまとめる。"""
    nearby = places_client.nearby(
        current_location["lat"], current_location["lng"],
        radius_m=scenario["search_radius_m"],
    )[:MAX_CANDIDATES]

    store = scenario["our_product"]["store_location"]
    dist_to_store = haversine_m(current_location["lat"], current_location["lng"], store["lat"], store["lng"])

    # 既にPlaces APIの結果に自社店舗らしき地点（極近傍）が無ければ候補に追加する
    already_present = any(p["distance_m"] <= OUR_STORE_DEDUPE_RADIUS_M for p in nearby
                           if haversine_m(p["lat"], p["lng"], store["lat"], store["lng"]) <= OUR_STORE_DEDUPE_RADIUS_M)

    candidates = list(nearby)
    if not already_present:
        candidates.append({
            "name": scenario["our_product"]["store_name"],
            "place_id": None,
            "lat": store["lat"],
            "lng": store["lng"],
            "types": ["our_store"],
            "rating": None,
            "distance_m": round(dist_to_store, 1),
        })

    for c in candidates:
        c["is_our_store"] = haversine_m(c["lat"], c["lng"], store["lat"], store["lng"]) <= OUR_STORE_DEDUPE_RADIUS_M

    candidates.sort(key=lambda x: x["distance_m"])
    return candidates


def run_simulation(personas: list[dict], scenario: dict) -> dict:
    places_client = PlacesClient()
    agent = PersonaAgent()

    sim_cfg = scenario["simulation"]
    start = datetime.strptime(f"{sim_cfg['date']} {sim_cfg['start_hour']}:00", "%Y-%m-%d %H:%M")
    end = datetime.strptime(f"{sim_cfg['date']} {sim_cfg['end_hour']}:00", "%Y-%m-%d %H:%M")
    turn_delta = timedelta(minutes=sim_cfg["turn_minutes"])

    state = {p["id"]: dict(p["start_location"]) for p in personas}
    log = {"scenario": scenario, "personas": personas, "turns": []}

    t = start
    while t <= end:
        turn_record = {"time": t.strftime("%Y-%m-%d %H:%M"), "decisions": []}
        for persona in personas:
            current_location = state[persona["id"]]
            candidates = build_candidates(places_client, current_location, scenario)

            decision = agent.decide(
                persona=persona,
                current_time=t.strftime("%Y-%m-%d %H:%M（%a）"),
                current_location=current_location,
                candidates=candidates,
                our_product=scenario["our_product"],
            )

            idx = decision["choice_index"]
            if idx == 0:
                new_location = current_location
                is_our_store = False
            else:
                chosen = candidates[idx - 1]
                new_location = {"lat": chosen["lat"], "lng": chosen["lng"], "place_name": chosen["name"]}
                is_our_store = chosen.get("is_our_store", False)

            # LLMがプロンプトの指示を守らず、自社店舗以外でも"purchase"と出力することがあるため、
            # 自社店舗を選んでいない場合はコード側で強制的に補正する
            if decision.get("action_type") == "purchase" and not is_our_store:
                decision["action_type"] = "enter_and_browse"

            state[persona["id"]] = new_location

            turn_record["decisions"].append({
                "persona_id": persona["id"],
                "persona_name": persona["name"],
                "location_before": current_location,
                "location_after": new_location,
                "decision": decision,
            })

            print(f"[{t.strftime('%H:%M')}] {persona['name']}: "
                  f"{decision['action_type']} -> {new_location['place_name']} "
                  f"(自社製品検討={decision['considered_our_product']}, 関心度={decision['interest_in_our_product']})")

        log["turns"].append(turn_record)
        t += turn_delta

    return log


def main():
    parser = argparse.ArgumentParser(description="AI箱庭シミュレーション")
    parser.add_argument("--personas", default="personas.json")
    parser.add_argument("--scenario", default="scenario.json")
    parser.add_argument("--out", default=None, help="出力ログのパス（省略時は logs/sim_<timestamp>.json）")
    args = parser.parse_args()

    with open(args.personas, encoding="utf-8") as f:
        personas = json.load(f)
    with open(args.scenario, encoding="utf-8") as f:
        scenario = json.load(f)

    log = run_simulation(personas, scenario)

    out_path = args.out or f"logs/sim_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(log, f, ensure_ascii=False, indent=2)

    print(f"\nログを保存しました: {out_path}")
    print("分析するには: python3 analyze.py", out_path)


if __name__ == "__main__":
    main()
