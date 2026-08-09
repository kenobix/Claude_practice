"""ミニプロジェクト: 事前学習済みBERT(DistilBERT)を映画レビューの感情分析にファインチューニングする

Stage7のミニプロジェクトと全く同じタスク・同じデータ分割(movie_reviewsコーパス、
訓練1600件・テスト400件)で、以下の4手法を比較する:
  - TF-IDF + ロジスティック回帰 (Stage7実測: Accuracy=0.823)
  - LSTM(word2vec初期化・微調整)     (Stage7実測: Accuracy=0.613, 最良のLSTM)
  - DistilBERT(事前学習済み、全パラメータをファインチューニング)  ← 本スクリプトで測定
  - DistilBERT(事前学習済み、分類ヘッドのみ学習=特徴抽出)        ← 本スクリプトで測定

Stage7のLSTMはword2vecで単語埋め込みだけを事前学習していたのに対し、
BERTは文全体の文脈を捉えるTransformerの全体構造を、桁違いに大きなコーパスで
事前学習済みという違いがある。同じ小さな訓練データ(1600件)でも、
事前学習済みモデルをファインチューニングする場合と、ゼロから学習する場合とで
どれだけ性能に差が出るかを実測する。
"""
import random
import time

import numpy as np
import torch
import torch.nn as nn
from nltk.corpus import movie_reviews
from transformers import AutoTokenizer, AutoModelForSequenceClassification
from sklearn.metrics import accuracy_score, precision_recall_fscore_support, confusion_matrix
import matplotlib.pyplot as plt

import _mpl_ja  # noqa: F401

SEED = 42
random.seed(SEED)
np.random.seed(SEED)
torch.manual_seed(SEED)

MODEL_NAME = "distilbert-base-uncased"
MAX_LEN = 128  # CPU学習の計算コストを抑えるため、レビュー全文ではなく先頭128トークンに切り詰める


def load_labeled_reviews():
    """Stage7の04と同じ考え方(fileids順・カテゴリ)でラベルを取得するが、
    BERTは単語分割済みリストではなくトークナイザが生のテキストを扱うため、
    movie_reviews.raw()で原文のまま読み込む。"""
    texts, labels = [], []
    for fid in movie_reviews.fileids():
        texts.append(movie_reviews.raw(fid))
        labels.append(1 if movie_reviews.categories(fid)[0] == "pos" else 0)
    return texts, labels


def train_test_split_stratified(labels, test_ratio=0.2, seed=SEED):
    """Stage7の04と同じロジック(pos/negそれぞれ先頭20%をテストに回す層化分割)。"""
    rng = random.Random(seed)
    idx_pos = [i for i, label in enumerate(labels) if label == 1]
    idx_neg = [i for i, label in enumerate(labels) if label == 0]
    rng.shuffle(idx_pos)
    rng.shuffle(idx_neg)
    n_test_pos = int(len(idx_pos) * test_ratio)
    n_test_neg = int(len(idx_neg) * test_ratio)
    test_idx = idx_pos[:n_test_pos] + idx_neg[:n_test_neg]
    train_idx = idx_pos[n_test_pos:] + idx_neg[n_test_neg:]
    rng.shuffle(train_idx)
    rng.shuffle(test_idx)
    return train_idx, test_idx


class ReviewDataset(torch.utils.data.Dataset):
    def __init__(self, texts, labels, tokenizer):
        enc = tokenizer(texts, truncation=True, padding="max_length", max_length=MAX_LEN,
                         return_tensors="pt")
        self.input_ids = enc["input_ids"]
        self.attention_mask = enc["attention_mask"]
        self.labels = torch.tensor(labels)

    def __len__(self):
        return len(self.labels)

    def __getitem__(self, i):
        return self.input_ids[i], self.attention_mask[i], self.labels[i]


def train_and_eval(mode, train_loader, test_loader, n_epochs=2, lr=2e-5):
    """mode='finetune': 全パラメータを更新 / mode='feature_extraction': 分類ヘッドのみ更新"""
    torch.manual_seed(SEED)
    model = AutoModelForSequenceClassification.from_pretrained(MODEL_NAME, num_labels=2)
    if mode == "feature_extraction":
        for name, p in model.named_parameters():
            if not name.startswith("classifier") and not name.startswith("pre_classifier"):
                p.requires_grad = False
    trainable_params = [p for p in model.parameters() if p.requires_grad]
    n_trainable = sum(p.numel() for p in trainable_params)
    n_total = sum(p.numel() for p in model.parameters())
    print(f"  学習対象パラメータ数: {n_trainable:,} / {n_total:,}")

    optimizer = torch.optim.AdamW(trainable_params, lr=lr)
    criterion = nn.CrossEntropyLoss()

    model.train()
    t0 = time.perf_counter()
    for epoch in range(n_epochs):
        epoch_loss = 0.0
        for input_ids, attn_mask, labels in train_loader:
            optimizer.zero_grad()
            logits = model(input_ids=input_ids, attention_mask=attn_mask).logits
            loss = criterion(logits, labels)
            loss.backward()
            optimizer.step()
            epoch_loss += loss.item()
        print(f"  epoch{epoch + 1}: 平均loss={epoch_loss / len(train_loader):.4f} "
              f"(経過時間{time.perf_counter() - t0:.0f}秒)")
    elapsed = time.perf_counter() - t0

    model.eval()
    all_preds, all_labels = [], []
    with torch.no_grad():
        for input_ids, attn_mask, labels in test_loader:
            preds = model(input_ids=input_ids, attention_mask=attn_mask).logits.argmax(dim=1)
            all_preds.extend(preds.tolist())
            all_labels.extend(labels.tolist())
    return np.array(all_preds), np.array(all_labels), elapsed


def main() -> None:
    print("=== 1. データ読み込み(Stage7と同じ分割ロジック) ===")
    texts, labels = load_labeled_reviews()
    train_idx, test_idx = train_test_split_stratified(labels)
    print(f"訓練{len(train_idx)}件 / テスト{len(test_idx)}件")

    tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME)
    train_ds = ReviewDataset([texts[i] for i in train_idx], [labels[i] for i in train_idx], tokenizer)
    test_ds = ReviewDataset([texts[i] for i in test_idx], [labels[i] for i in test_idx], tokenizer)
    train_loader = torch.utils.data.DataLoader(train_ds, batch_size=16, shuffle=True)
    test_loader = torch.utils.data.DataLoader(test_ds, batch_size=32, shuffle=False)

    print("\n=== 2. DistilBERTを2パターンで学習・評価 ===")
    results = {}
    for mode, name in [("feature_extraction", "B) 特徴抽出(分類ヘッドのみ学習)"),
                        ("finetune", "A) フルファインチューニング")]:
        print(f"\n-- {name} --")
        preds, gt, elapsed = train_and_eval(mode, train_loader, test_loader)
        acc = accuracy_score(gt, preds)
        prec, rec, f1, _ = precision_recall_fscore_support(gt, preds, average="binary")
        results[name] = dict(acc=acc, prec=prec, rec=rec, f1=f1, preds=preds, labels=gt, time=elapsed)
        print(f"  Accuracy={acc:.3f}  Precision={prec:.3f}  Recall={rec:.3f}  F1={f1:.3f}  "
              f"(学習時間{elapsed:.0f}秒)")

    print("\n=== 3. Stage7の結果と合わせて可視化 ===")
    # Stage7 work_log.mdに記載の実測値(TF-IDF+LogReg, 最良のLSTM)
    stage7_results = {
        "TF-IDF+LogReg(Stage7)": 0.823,
        "LSTM(word2vec微調整, Stage7)": 0.613,
    }
    all_names = list(stage7_results.keys()) + list(results.keys())
    all_accs = [stage7_results[n] for n in stage7_results] + [results[n]["acc"] for n in results]

    fig, axes = plt.subplots(1, 2, figsize=(14, 5.5))
    colors = ["tab:gray", "tab:red"] + ["tab:green", "tab:blue"]
    axes[0].bar(range(len(all_names)), all_accs, color=colors)
    axes[0].set_xticks(range(len(all_names)))
    axes[0].set_xticklabels(all_names, rotation=20, ha="right", fontsize=9)
    axes[0].set_ylabel("Accuracy")
    axes[0].set_title("感情分析タスクの精度比較(Stage7+Stage8)")
    axes[0].set_ylim(0, 1.0)
    for i, a in enumerate(all_accs):
        axes[0].text(i, a + 0.02, f"{a:.3f}", ha="center", fontsize=9)

    best_name = max(results, key=lambda n: results[n]["acc"])
    cm = confusion_matrix(results[best_name]["labels"], results[best_name]["preds"])
    im = axes[1].imshow(cm, cmap="Blues")
    axes[1].set_xticks([0, 1])
    axes[1].set_yticks([0, 1])
    axes[1].set_xticklabels(["negative", "positive"])
    axes[1].set_yticklabels(["negative", "positive"])
    axes[1].set_xlabel("予測")
    axes[1].set_ylabel("正解")
    axes[1].set_title(f"混同行列(最良モデル: {best_name})")
    for i in range(2):
        for j in range(2):
            axes[1].text(j, i, str(cm[i, j]), ha="center", va="center",
                          color="white" if cm[i, j] > cm.max() / 2 else "black")
    fig.colorbar(im, ax=axes[1], fraction=0.046)

    fig.tight_layout()
    out_path = "bert_finetune_project.png"
    fig.savefig(out_path, dpi=110)
    print(f"図を保存: {__file__.rsplit('/', 1)[0]}/{out_path}")

    bert_best_acc = results[best_name]["acc"]
    tfidf_acc = stage7_results["TF-IDF+LogReg(Stage7)"]
    print(
        f"\nDistilBERTの2手法のうち最も精度が高かったのは『{best_name}』"
        f"(Accuracy={bert_best_acc:.3f})だった。Stage7のLSTM(最良0.613)と比べると"
        + ("大きく上回り、" if bert_best_acc > 0.613 else "同程度か下回り、")
        + f"TF-IDF+ロジスティック回帰(0.823)と比べると"
        + (f"上回った。" if bert_best_acc > tfidf_acc else f"及ばなかった。")
        + "Stage7ではword2vecで単語埋め込みだけを事前学習していたのに対し、BERTは"
        "Transformer全体の『文脈を読む』能力そのものが大規模コーパスで事前学習されている。"
        "同じ1600件という小さな訓練データでも、事前学習済みの言語理解能力をどれだけ"
        "引き継げるかによって、ここまで結果が変わりうることを実測できた。"
    )


if __name__ == "__main__":
    main()
