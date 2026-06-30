"""
シミュレーションログを分析し、購買ファネルと失注理由を集計する。

実行:
  python3 analyze.py logs/sim_xxx.json
  python3 analyze.py logs/sim_a.json --compare logs/sim_b.json   # A/Bテスト比較
"""

import argparse
import json
from collections import defaultdict


def load_log(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def summarize(log: dict) -> dict:
    """ペルソナごとに「自社店舗を検討したか/来店したか/購入したか」を集計する。"""
    personas = {p["id"]: p["name"] for p in log["personas"]}
    per_persona = {pid: {
        "considered": False,
        "visited": False,
        "purchased": False,
        "max_interest": 0,
        "not_purchase_reasons": [],
    } for pid in personas}

    for turn in log["turns"]:
        for d in turn["decisions"]:
            pid = d["persona_id"]
            dec = d["decision"]
            stats = per_persona[pid]

            if dec.get("considered_our_product"):
                stats["considered"] = True
            stats["max_interest"] = max(stats["max_interest"], dec.get("interest_in_our_product", 0))

            if d["location_after"]["place_name"] == log["scenario"]["our_product"]["store_name"]:
                stats["visited"] = True
                if dec.get("action_type") == "purchase":
                    stats["purchased"] = True

            if dec.get("considered_our_product") and dec.get("action_type") != "purchase":
                stats["not_purchase_reasons"].append({
                    "time": turn["time"],
                    "reason": dec.get("reason", ""),
                })

    total = len(personas)
    considered = sum(1 for s in per_persona.values() if s["considered"])
    visited = sum(1 for s in per_persona.values() if s["visited"])
    purchased = sum(1 for s in per_persona.values() if s["purchased"])

    return {
        "total_personas": total,
        "considered": considered,
        "visited": visited,
        "purchased": purchased,
        "per_persona": {personas[pid]: stats for pid, stats in per_persona.items()},
    }


def print_summary(label: str, summary: dict):
    print(f"\n{'=' * 60}")
    print(f"{label}")
    print(f"{'=' * 60}")
    total = summary["total_personas"]
    print(f"ペルソナ数: {total}")
    print(f"  自社製品を検討した: {summary['considered']} / {total}")
    print(f"  来店した:           {summary['visited']} / {total}")
    print(f"  購入した:           {summary['purchased']} / {total}")

    print("\n--- ペルソナ別の詳細 ---")
    for name, stats in summary["per_persona"].items():
        status = "購入" if stats["purchased"] else ("来店のみ" if stats["visited"] else ("検討のみ" if stats["considered"] else "未検討"))
        print(f"\n[{name}] 結果: {status} / 最大関心度: {stats['max_interest']}/10")
        if stats["not_purchase_reasons"]:
            print("  購入に至らなかった理由:")
            for r in stats["not_purchase_reasons"]:
                print(f"    - ({r['time']}) {r['reason']}")


def main():
    parser = argparse.ArgumentParser(description="シミュレーションログの分析")
    parser.add_argument("log_path")
    parser.add_argument("--compare", help="比較対象のログファイル（A/Bテスト用）")
    args = parser.parse_args()

    log_a = load_log(args.log_path)
    summary_a = summarize(log_a)
    print_summary(f"ケースA: {args.log_path}", summary_a)

    if args.compare:
        log_b = load_log(args.compare)
        summary_b = summarize(log_b)
        print_summary(f"ケースB: {args.compare}", summary_b)

        print(f"\n{'=' * 60}")
        print("A/Bテスト比較サマリー")
        print(f"{'=' * 60}")
        for key, label in [("considered", "検討率"), ("visited", "来店率"), ("purchased", "購入率")]:
            a_rate = summary_a[key] / summary_a["total_personas"] * 100
            b_rate = summary_b[key] / summary_b["total_personas"] * 100
            print(f"  {label}: A={a_rate:.0f}%  B={b_rate:.0f}%  差={b_rate - a_rate:+.0f}pt")


if __name__ == "__main__":
    main()
