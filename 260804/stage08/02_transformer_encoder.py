"""PyTorchのTransformer Encoderで、Stage7と同じ長期記憶タスクを解く

Stage7の02で、「系列の最初の1ステップだけ来る信号を、無音区間を挟んで
最後に当てる」という長期記憶タスクを、RNN/LSTM/GRUで比較した。
vanilla RNNは系列長100まで学習できたが、標準初期化のLSTMは系列長30以降で
崩壊し、忘却ゲートの初期化を工夫してようやく系列長60まで伸ばせた。

RNN/LSTM/GRUは「1ステップずつ隠れ状態を更新しながら情報を運ぶ」構造のため、
情報が届くまでに系列長ぶんのステップを経由する。一方Self-Attentionは、
系列内のどの2点も「1ステップ」で直接つながる(Query-Keyの内積を取るだけ)ため、
系列が長くなっても情報が届く経路の長さが変わらないという構造的な強みがある。
これがTransformerが長期依存に強いとされる理由であり、同じタスクで実際に検証する。

Attentionには「順序」の概念がないため、位置情報を別途加える必要がある
(Positional Encoding)。ここではオリジナルのTransformer論文と同じ
sin/cosによる位置エンコーディングを使う。
"""
import math
import time

import numpy as np
import torch
import torch.nn as nn
import matplotlib.pyplot as plt

import _mpl_ja  # noqa: F401

torch.manual_seed(42)


def sinusoidal_positional_encoding(seq_len, d_model):
    pe = torch.zeros(seq_len, d_model)
    position = torch.arange(seq_len).unsqueeze(1).float()
    div_term = torch.exp(torch.arange(0, d_model, 2).float() * (-math.log(10000.0) / d_model))
    pe[:, 0::2] = torch.sin(position * div_term)
    pe[:, 1::2] = torch.cos(position * div_term)
    return pe


class TransformerClassifier(nn.Module):
    def __init__(self, d_model=16, n_heads=2, n_layers=2, max_len=200):
        super().__init__()
        self.input_proj = nn.Linear(1, d_model)
        self.register_buffer("pos_encoding", sinusoidal_positional_encoding(max_len, d_model))
        encoder_layer = nn.TransformerEncoderLayer(
            d_model=d_model, nhead=n_heads, dim_feedforward=d_model * 4,
            batch_first=True, dropout=0.0,
        )
        self.encoder = nn.TransformerEncoder(encoder_layer, num_layers=n_layers)
        self.fc = nn.Linear(d_model, 2)

    def forward(self, x):
        # x: (batch, seq_len, 1)
        seq_len = x.shape[1]
        h = self.input_proj(x) + self.pos_encoding[:seq_len]
        h = self.encoder(h)
        pooled = h[:, -1, :]  # RNN系との比較を公平にするため、最後の位置の表現だけを使う
        return self.fc(pooled)


def make_signal_batch(batch_size, seq_len, generator):
    sig = torch.randint(0, 2, (batch_size, 1), generator=generator, dtype=torch.float32)
    X = torch.zeros(batch_size, seq_len, 1)
    X[:, 0, 0] = sig[:, 0]
    y = sig[:, 0].long()
    return X, y


def train_and_eval(seq_len, n_iters=1500, lr=1e-3, seed=1):
    torch.manual_seed(seed)
    generator = torch.Generator().manual_seed(seed)
    model = TransformerClassifier(max_len=max(seq_len, 10))
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
    seq_lens = [10, 30, 60, 100, 200]
    print("=== Transformer Encoderで「最初の信号を最後まで覚えていられるか」タスクを解く ===")
    accs = []
    for L in seq_lens:
        t0 = time.perf_counter()
        acc = train_and_eval(L)
        elapsed = time.perf_counter() - t0
        accs.append(acc)
        print(f"系列長{L:4d}: テスト精度={acc:.3f} (学習時間{elapsed:.1f}秒)")

    # Stage7 script02で測定済みのRNN/LSTM/GRUの結果(work_log.md記載の実測値)を並べて比較する
    stage7_results = {
        "RNN":              {10: 1.000, 30: 1.000, 60: 1.000, 100: 1.000},
        "LSTM(標準初期化)":   {10: 1.000, 30: 0.510, 60: 0.510, 100: 0.510},
        "LSTM(忘却ゲート初期化)": {10: 1.000, 30: 1.000, 60: 1.000, 100: 0.510},
        "GRU":              {10: 1.000, 30: 1.000, 60: 0.510, 100: 0.510},
    }

    fig, ax = plt.subplots(figsize=(9, 5.5))
    common_lens = [10, 30, 60, 100]
    for name, d in stage7_results.items():
        ax.plot(common_lens, [d[L] for L in common_lens], "o--", alpha=0.6, label=f"{name}(Stage7実測)")
    ax.plot(seq_lens, accs, "o-", color="crimson", linewidth=2.5, markersize=8, label="Transformer(本スクリプト)")
    ax.axhline(0.5, color="gray", linestyle="--", linewidth=1, label="ランダム(0.5)")
    ax.set_xlabel("系列長(無音区間の長さ+1)")
    ax.set_ylabel("テスト精度")
    ax.set_title("長期記憶タスク: RNN系(Stage7) vs Transformer(本スクリプト)")
    ax.legend(fontsize=8)
    ax.set_ylim(0.3, 1.05)

    fig.tight_layout()
    out_path = "transformer_vs_rnn_family.png"
    fig.savefig(out_path, dpi=110)
    print(f"\n図を保存: {__file__.rsplit('/', 1)[0]}/{out_path}")

    max_ok = max([L for L, a in zip(seq_lens, accs) if a >= 0.9], default=None)
    print(
        f"\nTransformer Encoderは系列長{seq_lens}全てで"
        + (f"精度0.9以上を維持できた(最大でテストした系列長{seq_lens[-1]}でも学習成功)。"
           if max_ok == seq_lens[-1] else f"系列長{max_ok}までは精度0.9以上を維持できた。")
        + "RNN系(Stage7の実測)では、系列長が伸びるにつれてvanilla RNN以外は次々と精度が"
        "崩壊していったのに対し、Transformerは自己注意により『系列長によらず全ての位置に"
        "直接アクセスできる』という構造的な利点があるため、同じタスクでも長期記憶の"
        "崩壊が起きにくい。ただしTransformerにも代償がある: Self-Attentionの計算量・"
        "メモリ量は系列長Lに対してO(L^2)で増加するため(全てのQuery-Keyの組み合わせを"
        "計算するため)、系列がさらに長くなると今度は計算コストの面で不利になる"
        "——これがRNN系の逐次計算コストO(L)とのトレードオフであり、"
        "Transformerの発展(長系列対応のための効率化手法等)の出発点でもある。"
    )


if __name__ == "__main__":
    main()
