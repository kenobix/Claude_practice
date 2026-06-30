"""
シミュレーションログを分析し、購買ファネルと失注理由を集計する。

実行:
  python3 analyze.py logs/sim_xxx.json
  python3 analyze.py logs/sim_a.json --compare logs/sim_b.json   # A/Bテスト比較
  python3 analyze.py logs/sim_xxx.json --no-charts               # グラフ生成をスキップ
"""

import argparse
import json
import os

import matplotlib
matplotlib.use("Agg")  # 画面表示なしでPNGファイルに直接出力する
import matplotlib.pyplot as plt

# 日本語フォント（Noto Serif CJK JP）を指定しないと、グラフの日本語ラベルが文字化け（豆腐）する
matplotlib.rcParams["font.family"] = "Noto Serif CJK JP"

CHARTS_DIR = "charts"


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
        "interest_history": [],  # [(time, interest), ...] グラフ化用
    } for pid in personas}

    for turn in log["turns"]:
        for d in turn["decisions"]:
            pid = d["persona_id"]
            dec = d["decision"]
            stats = per_persona[pid]

            interest = dec.get("interest_in_our_product", 0)
            stats["interest_history"].append((turn["time"], interest))

            if dec.get("considered_our_product"):
                stats["considered"] = True
            stats["max_interest"] = max(stats["max_interest"], interest)

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


def plot_funnel(summary: dict, label: str, out_path: str):
    """検討率・来店率・購入率を棒グラフにする。"""
    stages = ["検討", "来店", "購入"]
    counts = [summary["considered"], summary["visited"], summary["purchased"]]
    total = summary["total_personas"]

    fig, ax = plt.subplots(figsize=(5, 4))
    bars = ax.bar(stages, counts, color=["#90caf9", "#42a5f5", "#1565c0"])
    ax.set_ylim(0, total + 1)
    ax.set_ylabel("人数")
    ax.set_title(f"購買ファネル — {label}")
    for bar, c in zip(bars, counts):
        ax.text(bar.get_x() + bar.get_width() / 2, c + 0.05, f"{c}/{total}", ha="center")
    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    plt.close(fig)


def plot_interest_over_time(summary: dict, label: str, out_path: str):
    """ペルソナごとの関心度の時間推移を折れ線グラフにする。"""
    fig, ax = plt.subplots(figsize=(7, 4))
    for name, stats in summary["per_persona"].items():
        if not stats["interest_history"]:
            continue
        times = [t.split(" ")[1] for t, _ in stats["interest_history"]]  # "HH:MM"だけ抜き出す
        values = [v for _, v in stats["interest_history"]]
        ax.plot(times, values, marker="o", label=name)

    ax.set_ylim(0, 10.5)
    ax.set_xlabel("時刻")
    ax.set_ylabel("自社製品への関心度")
    ax.set_title(f"関心度の推移 — {label}")
    ax.legend()
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    plt.close(fig)


def plot_ab_comparison(summary_a: dict, summary_b: dict, label_a: str, label_b: str, out_path: str):
    """A/Bテストの検討率・来店率・購入率を並べた棒グラフにする。"""
    stages = ["検討率", "来店率", "購入率"]
    keys = ["considered", "visited", "purchased"]
    rates_a = [summary_a[k] / summary_a["total_personas"] * 100 for k in keys]
    rates_b = [summary_b[k] / summary_b["total_personas"] * 100 for k in keys]

    x = range(len(stages))
    width = 0.35
    fig, ax = plt.subplots(figsize=(6, 4))
    ax.bar([i - width / 2 for i in x], rates_a, width, label=label_a, color="#64b5f6")
    ax.bar([i + width / 2 for i in x], rates_b, width, label=label_b, color="#ef5350")
    ax.set_xticks(list(x))
    ax.set_xticklabels(stages)
    ax.set_ylabel("割合 (%)")
    ax.set_ylim(0, 110)
    ax.set_title("A/Bテスト比較")
    ax.legend()
    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser(description="シミュレーションログの分析")
    parser.add_argument("log_path")
    parser.add_argument("--compare", help="比較対象のログファイル（A/Bテスト用）")
    parser.add_argument("--charts-dir", default=CHARTS_DIR, help=f"グラフPNGの出力先（デフォルト: {CHARTS_DIR}/）")
    parser.add_argument("--no-charts", action="store_true", help="グラフ生成をスキップする")
    args = parser.parse_args()

    log_a = load_log(args.log_path)
    summary_a = summarize(log_a)
    print_summary(f"ケースA: {args.log_path}", summary_a)

    if not args.no_charts:
        os.makedirs(args.charts_dir, exist_ok=True)

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

        if not args.no_charts:
            path = os.path.join(args.charts_dir, "ab_comparison.png")
            plot_ab_comparison(summary_a, summary_b, "A", "B", path)
            print(f"\nグラフを保存しました: {path}")
    elif not args.no_charts:
        funnel_path = os.path.join(args.charts_dir, "funnel.png")
        interest_path = os.path.join(args.charts_dir, "interest_over_time.png")
        plot_funnel(summary_a, "ケースA", funnel_path)
        plot_interest_over_time(summary_a, "ケースA", interest_path)
        print(f"\nグラフを保存しました: {funnel_path}, {interest_path}")


if __name__ == "__main__":
    main()
