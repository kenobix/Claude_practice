"""ミニプロジェクト: LSTMで映画レビューの感情分析(positive/negative)を行う

NLTKのmovie_reviewsコーパス(2000本、positive/negativeが各1000本)を使い、
レビュー全文を読んでpositive/negativeを判定する二値分類モデルをLSTMで作る。

単語埋め込み(Embedding層)の初期値として、以下の3通りを比較する:
  A) ランダム初期化 + 学習時に埋め込みも一緒に更新(scratch)
  B) 03で学習したword2vec(skip-gram)のベクトルで初期化 + 埋め込みは固定(frozen)
  C) 03で学習したword2vec(skip-gram)のベクトルで初期化 + 埋め込みも一緒に更新(fine-tune)
Stage5の転移学習(特徴抽出 vs ファインチューニング)と同じ構図を、
画像ではなく単語埋め込みで再現する形になっている。
比較の基準として、TF-IDF+ロジスティック回帰(Stage1的な古典的手法)も併せて評価する。
"""
import importlib
import random
import time

import numpy as np
import torch
import torch.nn as nn
from torch.nn.utils.rnn import pack_padded_sequence
from nltk.corpus import movie_reviews
from gensim.models import Word2Vec
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, precision_recall_fscore_support, confusion_matrix
import matplotlib.pyplot as plt

import _mpl_ja  # noqa: F401

w2v_module = importlib.import_module("03_word2vec")

SEED = 42
random.seed(SEED)
np.random.seed(SEED)
torch.manual_seed(SEED)

MAX_LEN = 200
PAD_IDX = 0
UNK_IDX = 1


def load_labeled_reviews():
    sentences = w2v_module.load_sentences()  # 03と同じ前処理(小文字化+英字のみ)
    labels = [movie_reviews.categories(fid)[0] for fid in movie_reviews.fileids()]
    y = [1 if lab == "pos" else 0 for lab in labels]
    return sentences, y


def train_test_split_stratified(sentences, y, test_ratio=0.2, seed=SEED):
    rng = random.Random(seed)
    idx_pos = [i for i, label in enumerate(y) if label == 1]
    idx_neg = [i for i, label in enumerate(y) if label == 0]
    rng.shuffle(idx_pos)
    rng.shuffle(idx_neg)
    n_test_pos = int(len(idx_pos) * test_ratio)
    n_test_neg = int(len(idx_neg) * test_ratio)
    test_idx = idx_pos[:n_test_pos] + idx_neg[:n_test_neg]
    train_idx = idx_pos[n_test_pos:] + idx_neg[n_test_neg:]
    rng.shuffle(train_idx)
    rng.shuffle(test_idx)
    return train_idx, test_idx


def build_vocab(w2v_model):
    """word2vecの語彙(出現回数5回以上)をそのままLSTM用の語彙として使う。
    0=PAD(文の長さ揃え用のダミートークン), 1=UNK(語彙外の単語)を先頭に追加する。"""
    words = w2v_model.wv.index_to_key
    word2idx = {w: i + 2 for i, w in enumerate(words)}
    return word2idx


def encode(sentence, word2idx, max_len=MAX_LEN):
    ids = [word2idx.get(w, UNK_IDX) for w in sentence[:max_len]]
    length = len(ids)
    if length < max_len:
        ids = ids + [PAD_IDX] * (max_len - length)
    return ids, max(length, 1)


class ReviewDataset(torch.utils.data.Dataset):
    def __init__(self, sentences, labels, word2idx):
        self.X, self.lengths = zip(*[encode(s, word2idx) for s in sentences])
        self.y = labels

    def __len__(self):
        return len(self.y)

    def __getitem__(self, i):
        return torch.tensor(self.X[i]), self.lengths[i], self.y[i]


class LSTMClassifier(nn.Module):
    def __init__(self, vocab_size, emb_dim=100, hidden_size=64, pretrained=None, freeze=False):
        super().__init__()
        self.embedding = nn.Embedding(vocab_size, emb_dim, padding_idx=PAD_IDX)
        if pretrained is not None:
            with torch.no_grad():
                self.embedding.weight.copy_(pretrained)
        if freeze:
            self.embedding.weight.requires_grad = False
        self.lstm = nn.LSTM(emb_dim, hidden_size, batch_first=True)
        self.fc = nn.Linear(hidden_size, 2)

    def forward(self, x, lengths):
        emb = self.embedding(x)
        packed = pack_padded_sequence(emb, lengths.cpu(), batch_first=True, enforce_sorted=False)
        _, (h_n, _) = self.lstm(packed)
        return self.fc(h_n[-1])  # 最後の層の最終隠れ状態(=最後の実トークンでの状態)を使う


def build_embedding_matrix(word2idx, w2v_model, emb_dim=100, seed=SEED):
    rng = np.random.RandomState(seed)
    matrix = rng.normal(scale=0.1, size=(len(word2idx) + 2, emb_dim)).astype(np.float32)
    matrix[PAD_IDX] = 0.0
    for w, idx in word2idx.items():
        if w in w2v_model.wv:
            matrix[idx] = w2v_model.wv[w]
    return torch.tensor(matrix)


def train_model(model, train_loader, test_loader, n_epochs=8, lr=1e-3):
    optimizer = torch.optim.Adam([p for p in model.parameters() if p.requires_grad], lr=lr)
    criterion = nn.CrossEntropyLoss()
    for epoch in range(n_epochs):
        model.train()
        for X, lengths, y in train_loader:
            optimizer.zero_grad()
            logits = model(X, lengths)
            loss = criterion(logits, y)
            loss.backward()
            optimizer.step()
    model.eval()
    all_preds, all_labels = [], []
    with torch.no_grad():
        for X, lengths, y in test_loader:
            preds = model(X, lengths).argmax(dim=1)
            all_preds.extend(preds.tolist())
            all_labels.extend(y.tolist())
    return np.array(all_preds), np.array(all_labels)


def main() -> None:
    print("=== 1. データ読み込み・前処理 ===")
    sentences, y = load_labeled_reviews()
    train_idx, test_idx = train_test_split_stratified(sentences, y)
    print(f"訓練{len(train_idx)}件 / テスト{len(test_idx)}件 (positive/negative各半数ずつ)")

    print("\n=== 2. word2vec(skip-gram)を学習し、埋め込み初期値として使う ===")
    train_sentences = [sentences[i] for i in train_idx]
    t0 = time.perf_counter()
    w2v_model = Word2Vec(train_sentences, sg=1, vector_size=100, window=5, min_count=5,
                          workers=4, epochs=20, seed=SEED)
    print(f"word2vec学習時間={time.perf_counter() - t0:.1f}秒, 語彙数={len(w2v_model.wv)}")
    word2idx = build_vocab(w2v_model)
    emb_matrix = build_embedding_matrix(word2idx, w2v_model)

    train_ds = ReviewDataset([sentences[i] for i in train_idx], [y[i] for i in train_idx], word2idx)
    test_ds = ReviewDataset([sentences[i] for i in test_idx], [y[i] for i in test_idx], word2idx)
    train_loader = torch.utils.data.DataLoader(train_ds, batch_size=32, shuffle=True)
    test_loader = torch.utils.data.DataLoader(test_ds, batch_size=64, shuffle=False)

    print("\n=== 3. LSTM分類器を3パターンで学習・評価 ===")
    variants = {
        "A) ランダム初期化": dict(pretrained=None, freeze=False),
        "B) word2vec初期化(固定)": dict(pretrained=emb_matrix, freeze=True),
        "C) word2vec初期化(微調整)": dict(pretrained=emb_matrix, freeze=False),
    }
    results = {}
    for name, kwargs in variants.items():
        torch.manual_seed(SEED)
        model = LSTMClassifier(vocab_size=len(word2idx) + 2, **kwargs)
        t0 = time.perf_counter()
        preds, labels = train_model(model, train_loader, test_loader, n_epochs=8)
        elapsed = time.perf_counter() - t0
        acc = accuracy_score(labels, preds)
        prec, rec, f1, _ = precision_recall_fscore_support(labels, preds, average="binary")
        results[name] = dict(acc=acc, prec=prec, rec=rec, f1=f1, preds=preds, labels=labels, time=elapsed)
        print(f"{name:24s}: Accuracy={acc:.3f}  Precision={prec:.3f}  Recall={rec:.3f}  "
              f"F1={f1:.3f}  (学習時間{elapsed:.1f}秒)")

    print("\n=== 4. ベースライン: TF-IDF + ロジスティック回帰 ===")
    texts = [" ".join(s) for s in sentences]
    train_texts = [texts[i] for i in train_idx]
    test_texts = [texts[i] for i in test_idx]
    y_train = [y[i] for i in train_idx]
    y_test = [y[i] for i in test_idx]

    vectorizer = TfidfVectorizer(max_features=10000)
    X_train_tfidf = vectorizer.fit_transform(train_texts)
    X_test_tfidf = vectorizer.transform(test_texts)
    clf = LogisticRegression(max_iter=1000, random_state=SEED)
    clf.fit(X_train_tfidf, y_train)
    tfidf_preds = clf.predict(X_test_tfidf)
    tfidf_acc = accuracy_score(y_test, tfidf_preds)
    tfidf_prec, tfidf_rec, tfidf_f1, _ = precision_recall_fscore_support(y_test, tfidf_preds, average="binary")
    print(f"TF-IDF+LogReg          : Accuracy={tfidf_acc:.3f}  Precision={tfidf_prec:.3f}  "
          f"Recall={tfidf_rec:.3f}  F1={tfidf_f1:.3f}")

    print("\n=== 5. 結果の可視化 ===")
    all_names = list(variants.keys()) + ["D) TF-IDF+LogReg"]
    all_accs = [results[n]["acc"] for n in variants] + [tfidf_acc]
    all_f1s = [results[n]["f1"] for n in variants] + [tfidf_f1]

    fig, axes = plt.subplots(1, 2, figsize=(13, 5.5))
    x = np.arange(len(all_names))
    axes[0].bar(x, all_accs, color=["tab:blue", "tab:orange", "tab:green", "tab:gray"])
    axes[0].set_xticks(x)
    axes[0].set_xticklabels(all_names, rotation=20, ha="right")
    axes[0].set_ylabel("Accuracy")
    axes[0].set_title("手法ごとのテスト精度")
    axes[0].set_ylim(0, 1.0)
    for i, a in enumerate(all_accs):
        axes[0].text(i, a + 0.02, f"{a:.3f}", ha="center")

    best_name = max(variants, key=lambda n: results[n]["acc"])
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
    out_path = "lstm_sentiment_project.png"
    fig.savefig(out_path, dpi=110)
    print(f"図を保存: {__file__.rsplit('/', 1)[0]}/{out_path}")

    lstm_best_acc = results[best_name]["acc"]
    print(
        f"\n3種のLSTM({', '.join(variants.keys())})のうち最も精度が高かったのは"
        f"『{best_name}』(Accuracy={lstm_best_acc:.3f})で、TF-IDF+ロジスティック回帰"
        f"(Accuracy={tfidf_acc:.3f})と比べて"
        + (f"上回った。" if lstm_best_acc > tfidf_acc else f"下回る結果となった。")
        + "訓練データが1600件と決して多くない中でLSTMを学習する場合、"
        "ランダム初期化から埋め込みごと学習するより、word2vecで得た単語の意味的な"
        "近さをあらかじめ埋め込みに組み込んでおく方が、限られたデータでも学習が"
        "安定しやすいと考えられる。一方でTF-IDF+ロジスティック回帰のような単純な"
        "手法も、映画レビューのような『特定の単語の有無が強く感情極性と相関する』"
        "タスクでは十分に強力な基準になることが分かる。"
    )


if __name__ == "__main__":
    main()
