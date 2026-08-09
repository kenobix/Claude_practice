"""PyTorchのnn.RNN / nn.LSTM / nn.GRUで、長期記憶タスクにおける挙動を比較する

01のスクラッチRNNで、パリティ判定タスクは系列が長くなると勾配消失により
学習できなくなることを確認した。ここでは「系列の最初の1ステップだけ信号(0/1)
が来て、残りは全て無音(0)が続いた後、最後にその信号を当てる」という、
より単純な長期記憶タスクを使う。無音区間はネットワークを揺さぶる新しい情報が
ないため、隠れ状態を『そのまま保持し続けられるか』だけが問われる、
長期依存性のもっとも基本的なテストになっている。

LSTM(Long Short-Term Memory)・GRU(Gated Recurrent Unit)は、ゲート機構
(忘却ゲート等)によって情報をそのまま素通しする経路を作り、vanilla RNNより
長期記憶に強いとされる。ただし理論的な設計だけでなく、パラメータの初期値
(特にLSTMの忘却ゲートのバイアス)が実際の学習しやすさに大きく影響する
ことも知られている(Jozefowicz et al., 2015)。ここではその初期値の効果も
含めて比較する。
"""
import time

import numpy as np
import torch
import torch.nn as nn
import matplotlib.pyplot as plt

import _mpl_ja  # noqa: F401

torch.manual_seed(42)


class SeqClassifier(nn.Module):
    def __init__(self, cell_type, hidden_size=16, forget_bias_trick=False):
        super().__init__()
        cell_cls = {"RNN": nn.RNN, "LSTM": nn.LSTM, "GRU": nn.GRU}[cell_type]
        self.rnn = cell_cls(input_size=1, hidden_size=hidden_size, batch_first=True)
        if cell_type == "LSTM" and forget_bias_trick:
            # PyTorchのLSTMのbiasは[入力ゲート, 忘却ゲート, セルゲート, 出力ゲート]の順に連結されている。
            # 忘却ゲートのバイアスを大きくしておくと、学習の初期段階から「基本的に記憶を保持する」
            # 方向にバイアスがかかり、長期記憶タスクの最適化が大きく安定することが知られている。
            h = hidden_size
            with torch.no_grad():
                self.rnn.bias_ih_l0[h:2 * h].fill_(1.0)
                self.rnn.bias_hh_l0[h:2 * h].fill_(1.0)
        self.fc = nn.Linear(hidden_size, 2)

    def forward(self, x):
        out, _ = self.rnn(x)
        last = out[:, -1, :]  # 最後の時刻の隠れ状態だけを使う(many-to-one)
        return self.fc(last)


def make_signal_batch(batch_size, seq_len, generator):
    """最初の1ステップだけ0/1の信号があり、残りは無音(0)が続く系列。
    最後の時刻で、その信号が0だったか1だったかを当てる。"""
    sig = torch.randint(0, 2, (batch_size, 1), generator=generator, dtype=torch.float32)
    X = torch.zeros(batch_size, seq_len, 1)
    X[:, 0, 0] = sig[:, 0]
    y = sig[:, 0].long()
    return X, y


def train_and_eval(variant_fn, seq_len, n_iters=1500, lr=0.01, seed=1):
    torch.manual_seed(seed)
    generator = torch.Generator().manual_seed(seed)
    model = variant_fn()
    optimizer = torch.optim.Adam(model.parameters(), lr=lr)
    criterion = nn.CrossEntropyLoss()

    for it in range(n_iters):
        X, y = make_signal_batch(64, seq_len, generator)
        optimizer.zero_grad()
        logits = model(X)
        loss = criterion(logits, y)
        loss.backward()
        nn.utils.clip_grad_norm_(model.parameters(), 5.0)
        optimizer.step()

    model.eval()
    with torch.no_grad():
        X_test, y_test = make_signal_batch(300, seq_len, generator)
        acc = (model(X_test).argmax(dim=1) == y_test).float().mean().item()
    return acc


def main() -> None:
    variants = {
        "RNN": lambda: SeqClassifier("RNN"),
        "LSTM(標準初期化)": lambda: SeqClassifier("LSTM", forget_bias_trick=False),
        "LSTM(忘却ゲート初期化)": lambda: SeqClassifier("LSTM", forget_bias_trick=True),
        "GRU": lambda: SeqClassifier("GRU"),
    }
    seq_lens = [10, 30, 60, 100]

    results = {name: [] for name in variants}
    print("=== 「最初の信号を最後まで覚えていられるか」タスクで比較 ===")
    for L in seq_lens:
        print(f"\n-- 系列長{L}(無音区間{L - 1}ステップ) --")
        for name, fn in variants.items():
            t0 = time.perf_counter()
            acc = train_and_eval(fn, L)
            elapsed = time.perf_counter() - t0
            results[name].append(acc)
            print(f"  {name:16s}: テスト精度={acc:.3f} (学習時間{elapsed:.1f}秒)")

    fig, ax = plt.subplots(figsize=(9, 5.5))
    colors = {"RNN": "tab:orange", "LSTM(標準初期化)": "tab:red",
              "LSTM(忘却ゲート初期化)": "tab:blue", "GRU": "tab:green"}
    for name in variants:
        ax.plot(seq_lens, results[name], "o-", label=name, color=colors[name])
    ax.axhline(0.5, color="gray", linestyle="--", linewidth=1, label="ランダム(0.5)")
    ax.set_xlabel("系列長(無音区間の長さ+1)")
    ax.set_ylabel("テスト精度")
    ax.set_title("最初の信号を最後まで覚えていられるか: RNN/LSTM/GRU比較")
    ax.legend()
    ax.set_ylim(0.3, 1.05)

    fig.tight_layout()
    out_path = "rnn_lstm_gru_comparison.png"
    fig.savefig(out_path, dpi=110)
    print(f"\n図を保存: {__file__.rsplit('/', 1)[0]}/{out_path}")

    print("\n=== 系列長ごとの精度一覧 ===")
    header = "系列長  " + "  ".join(f"{name:>18s}" for name in variants)
    print(header)
    for i, L in enumerate(seq_lens):
        row = f"{L:5d}  " + "  ".join(f"{results[name][i]:18.3f}" for name in variants)
        print(row)

    lstm_default_ok = [L for L, a in zip(seq_lens, results["LSTM(標準初期化)"]) if a >= 0.9]
    lstm_trick_ok = [L for L, a in zip(seq_lens, results["LSTM(忘却ゲート初期化)"]) if a >= 0.9]
    rnn_ok = [L for L, a in zip(seq_lens, results["RNN"]) if a >= 0.9]
    print(
        f"\nこの無音持続タスクでは、vanilla RNNは系列長{max(rnn_ok) if rnn_ok else 'なし'}まで学習できた"
        f"一方、標準初期化のLSTMが学習できたのは系列長{lstm_default_ok if lstm_default_ok else 'なし'}のみだった。"
        "これは『LSTMはゲート機構により長期依存に強い』という一般論に反するように見えるが、"
        "実際には標準初期化のLSTMは忘却ゲートの初期状態が中途半端(シグモイド≈0.5)なため、"
        "学習初期に情報を保持する方向にバイアスがかかっておらず、勾配降下法がその解を"
        "見つけにくいという実務上よく知られた問題が原因と考えられる。"
        f"実際、忘却ゲートのバイアスを大きく初期化する一工夫を加えるだけで、"
        f"LSTMが学習できる系列長は{lstm_trick_ok if lstm_trick_ok else 'なし'}まで伸びており、"
        "ゲート機構自体の表現力は十分でも、それを引き出せるかどうかは初期値に強く依存する"
        "ことが分かる。理論上の設計の優位性と、実際に勾配降下法で学習できるかどうかは"
        "別問題であるという、実践上重要な教訓が得られた。"
    )


if __name__ == "__main__":
    main()
