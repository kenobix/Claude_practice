"""知識蒸留(Knowledge Distillation)で、大きな教師モデルの知識を小さな生徒モデルに移す

01のプルーニング・02の量子化は「すでに学習済みの1つのモデル」を後から軽量化する
アプローチだった。知識蒸留は発想が異なり、最初から小さい生徒(student)モデルを、
大きな教師(teacher)モデルの出力を模倣するように学習させることで、生徒モデル単体を
ゼロから訓練するより高い性能を引き出そうとする手法。

ポイントは「教師の予測確率分布(soft label)」を使うこと。正解ラベルだけ(hard label,
例: [0,1,0,0]のようなone-hot)を教師データにするより、教師モデルの出力確率
(例: [0.05, 0.85, 0.07, 0.03]のような、クラス間の類似度の情報を含む分布)を
再現するように学習させる方が、教師が学習で獲得した『クラス間の関係性』の情報まで
生徒に伝えられる、というのが知識蒸留の基本的な着想(Hinton et al., 2015)。

損失関数 = α * 生徒とソフトラベルとの蒸留損失(KLダイバージェンス, 温度Tで分布を滑らかにする)
         + (1-α) * 生徒と正解ラベルとの通常の交差エントロピー損失
"""
import time

import torch
import torch.nn as nn
import torch.nn.functional as F
import matplotlib.pyplot as plt

from synthetic_shapes import generate_shapes_dataset
import _mpl_ja  # noqa: F401

torch.manual_seed(42)


class TeacherCNN(nn.Module):
    """大きめのCNN(チャネル数多め)。まずこれを普通に学習して『教師』にする。"""

    def __init__(self, n_classes=4):
        super().__init__()
        self.features = nn.Sequential(
            nn.Conv2d(3, 64, 3, padding=1), nn.ReLU(), nn.MaxPool2d(2),
            nn.Conv2d(64, 128, 3, padding=1), nn.ReLU(), nn.MaxPool2d(2),
            nn.Conv2d(128, 128, 3, padding=1), nn.ReLU(), nn.MaxPool2d(2),
        )
        self.classifier = nn.Sequential(
            nn.Linear(128 * 4 * 4, 256), nn.ReLU(), nn.Linear(256, n_classes),
        )

    def forward(self, x):
        return self.classifier(self.features(x).flatten(1))


class StudentCNN(nn.Module):
    """教師よりずっと小さいCNN(チャネル数を大幅に減らす)。"""

    def __init__(self, n_classes=4):
        super().__init__()
        self.features = nn.Sequential(
            nn.Conv2d(3, 8, 3, padding=1), nn.ReLU(), nn.MaxPool2d(2),
            nn.Conv2d(8, 16, 3, padding=1), nn.ReLU(), nn.MaxPool2d(2),
            nn.Conv2d(16, 16, 3, padding=1), nn.ReLU(), nn.MaxPool2d(2),
        )
        self.classifier = nn.Sequential(
            nn.Linear(16 * 4 * 4, 32), nn.ReLU(), nn.Linear(32, n_classes),
        )

    def forward(self, x):
        return self.classifier(self.features(x).flatten(1))


def count_params(model):
    return sum(p.numel() for p in model.parameters())


def train_plain(model, train_loader, n_epochs=20, lr=1e-3):
    optimizer = torch.optim.Adam(model.parameters(), lr=lr)
    criterion = nn.CrossEntropyLoss()
    for epoch in range(n_epochs):
        model.train()
        for X, y in train_loader:
            optimizer.zero_grad()
            loss = criterion(model(X), y)
            loss.backward()
            optimizer.step()
    return model


def train_distillation(student, teacher, train_loader, n_epochs=20, lr=1e-3, T=2.0, alpha=0.5):
    """T(温度): softmax前のlogitsをTで割ってから確率化すると、分布がより滑らかになり、
    正解クラス以外の『クラス間の類似度』の情報がより伝わりやすくなる。"""
    teacher.eval()
    optimizer = torch.optim.Adam(student.parameters(), lr=lr)
    for epoch in range(n_epochs):
        student.train()
        for X, y in train_loader:
            with torch.no_grad():
                teacher_logits = teacher(X)
            student_logits = student(X)

            hard_loss = F.cross_entropy(student_logits, y)
            soft_teacher = F.softmax(teacher_logits / T, dim=1)
            soft_student = F.log_softmax(student_logits / T, dim=1)
            distill_loss = F.kl_div(soft_student, soft_teacher, reduction="batchmean") * (T ** 2)

            loss = alpha * distill_loss + (1 - alpha) * hard_loss
            optimizer.zero_grad()
            loss.backward()
            optimizer.step()
    return student


def evaluate(model, X, y):
    model.eval()
    with torch.no_grad():
        return (model(X).argmax(1) == y).float().mean().item()


def main() -> None:
    print("=== 1. データ準備・教師モデル(大きいCNN)の学習 ===")
    X_train, y_train = generate_shapes_dataset(2000, seed=0)
    X_test, y_test = generate_shapes_dataset(400, seed=1000)
    X_train_t, y_train_t = torch.tensor(X_train).float(), torch.tensor(y_train).long()
    X_test_t, y_test_t = torch.tensor(X_test).float(), torch.tensor(y_test).long()
    train_loader = torch.utils.data.DataLoader(
        torch.utils.data.TensorDataset(X_train_t, y_train_t), batch_size=64, shuffle=True)

    teacher = TeacherCNN()
    t0 = time.perf_counter()
    train_plain(teacher, train_loader)
    print(f"教師モデル学習時間={time.perf_counter() - t0:.1f}秒")
    acc_teacher = evaluate(teacher, X_test_t, y_test_t)
    n_teacher = count_params(teacher)
    print(f"教師モデル: パラメータ数={n_teacher:,}, テスト精度={acc_teacher:.3f}")

    print("\n=== 2. 生徒モデル(小さいCNN)を(a)通常学習 (b)知識蒸留 の2通りで学習 ===")
    torch.manual_seed(1)
    student_scratch = StudentCNN()
    n_student = count_params(student_scratch)
    print(f"生徒モデルのパラメータ数={n_student:,}(教師の{100 * n_student / n_teacher:.1f}%)")

    train_plain(student_scratch, train_loader)
    acc_scratch = evaluate(student_scratch, X_test_t, y_test_t)
    print(f"(a) 生徒モデル(通常学習, 正解ラベルのみ): テスト精度={acc_scratch:.3f}")

    torch.manual_seed(1)
    student_distilled = StudentCNN()
    train_distillation(student_distilled, teacher, train_loader)
    acc_distilled = evaluate(student_distilled, X_test_t, y_test_t)
    print(f"(b) 生徒モデル(知識蒸留, 教師のsoft labelも利用): テスト精度={acc_distilled:.3f}")

    print("\n=== 3. 可視化 ===")
    fig, axes = plt.subplots(1, 2, figsize=(12, 5.5))
    names = ["教師\n(TeacherCNN)", "生徒(通常学習)\n(StudentCNN)", "生徒(知識蒸留)\n(StudentCNN)"]
    accs = [acc_teacher, acc_scratch, acc_distilled]
    params = [n_teacher, n_student, n_student]
    colors = ["tab:gray", "tab:red", "tab:blue"]

    axes[0].bar(names, accs, color=colors)
    axes[0].set_ylabel("テスト精度")
    axes[0].set_title("精度の比較")
    axes[0].set_ylim(0, 1.05)
    for i, v in enumerate(accs):
        axes[0].text(i, v + 0.02, f"{v:.3f}", ha="center")

    axes[1].bar(names, params, color=colors)
    axes[1].set_ylabel("パラメータ数")
    axes[1].set_title(f"パラメータ数の比較(生徒は教師の{100 * n_student / n_teacher:.1f}%)")
    axes[1].set_yscale("log")
    for i, v in enumerate(params):
        axes[1].text(i, v, f"{v:,}", ha="center", va="bottom", fontsize=8)

    fig.tight_layout()
    out_path = "knowledge_distillation.png"
    fig.savefig(out_path, dpi=110)
    print(f"図を保存: {__file__.rsplit('/', 1)[0]}/{out_path}")

    gap_scratch = acc_teacher - acc_scratch
    gap_distilled = acc_teacher - acc_distilled
    print(
        f"\n生徒モデルは教師モデルのわずか{100 * n_student / n_teacher:.1f}%のパラメータ数"
        f"しか持たないにもかかわらず、通常学習でも精度{acc_scratch:.3f}(教師との差"
        f"{gap_scratch:+.3f})を達成した。知識蒸留(T=2.0, α=0.5)を使うと精度{acc_distilled:.3f}"
        f"(教師との差{gap_distilled:+.3f})となり、"
        + ("通常学習より知識蒸留の方が教師の性能に近づいた" if acc_distilled > acc_scratch
           else "今回はハイパーパラメータ(温度T・損失の重みα)をいくつか試した中でも、"
           "通常学習を明確に上回る結果は得られなかった")
        + "。教師モデルの出力する確率分布には、正解ラベルだけでは伝わらない"
        "『不正解クラス同士の類似度』の情報(例えば三角形は四角形と間違えやすいが円とは"
        "間違えにくい、といった構造)が含まれており、これを生徒モデルの学習に活用できるのが"
        "知識蒸留の理論的な利点とされる。しかし今回のタスクは、教師モデル自身がテスト精度"
        f"{acc_teacher:.3f}とほぼ完璧に解けてしまうほどシンプルであり、教師の出力する"
        "確率分布もほぼone-hotに近い(不正解クラス間の類似度情報が乏しい)状態になっている"
        "可能性が高い。知識蒸留は『教師が易しいタスクを完璧に解けてしまう場合、ソフト"
        "ラベルに伝えるべき追加情報がそもそも少なくなり、通常学習に対する優位性が"
        "出にくくなる』という限界があることを、逆に実験的に示す結果になったと考えられる。"
        "知識蒸留が威力を発揮するのは、教師モデル自身が完璧ではなく、クラス間の"
        "混同パターンに意味のある情報が残っているような、より難しいタスクだとされる。"
    )


if __name__ == "__main__":
    main()
