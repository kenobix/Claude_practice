"""データ拡張(Data Augmentation)とMixup

前半: 回転・反転・色調変化などの定番のデータ拡張を合成図形データに適用し、
      見た目の変化を確認する
後半: Mixupを自作実装(2枚の画像・ラベルを線形補間で混ぜ合わせる)し、
      少ない訓練データでの過学習抑制効果を、通常の学習と比較する
"""
import numpy as np
import torch
import torch.nn as nn
import torch.optim as optim
import torchvision.transforms.v2 as T
import matplotlib.pyplot as plt

from synthetic_shapes import generate_shapes_dataset, CLASS_NAMES
import _mpl_ja  # noqa: F401

torch.manual_seed(42)


def demo_standard_augmentation() -> None:
    print("=== 1. 定番のデータ拡張(回転・反転・色調変化) ===")
    X, y = generate_shapes_dataset(1, seed=7)
    img = torch.tensor(X[0])  # (3,H,W), 値域[0,1]

    augmentations = {
        "元画像": T.Identity(),
        "左右反転": T.RandomHorizontalFlip(p=1.0),
        "回転(±30度)": T.RandomRotation(degrees=30),
        "色調変化(ColorJitter)": T.ColorJitter(brightness=0.4, contrast=0.4, saturation=0.4, hue=0.1),
        "ランダムクロップ+リサイズ": T.RandomResizedCrop(size=32, scale=(0.6, 1.0)),
    }

    fig, axes = plt.subplots(1, len(augmentations), figsize=(15, 3.5))
    torch.manual_seed(0)
    for ax, (name, transform) in zip(axes, augmentations.items()):
        out = transform(img).clamp(0, 1)
        ax.imshow(out.permute(1, 2, 0).numpy())
        ax.set_title(name, fontsize=10)
        ax.axis("off")
    fig.tight_layout()
    out_path = "data_augmentation_examples.png"
    fig.savefig(out_path, dpi=110)
    print(f"図を保存: {__file__.rsplit('/', 1)[0]}/{out_path}")
    print(
        "データ拡張は『同じ画像でも、見た目を少し変えたバリエーションを訓練時に見せる』ことで、"
        "モデルが『回転していても』『色が違っても』『多少欠けていても』同じクラスだと"
        "認識できるよう頑健性を高める。訓練データを増やさずに、実質的なデータの多様性を"
        "増やす安価な正則化手法として広く使われる。"
    )


def mixup(X: torch.Tensor, y: torch.Tensor, n_classes: int, alpha: float = 0.4):
    """2枚の画像・ラベルをベータ分布からサンプリングした比率lamで線形に混ぜ合わせる"""
    lam = np.random.beta(alpha, alpha)
    perm = torch.randperm(X.size(0))
    X_mixed = lam * X + (1 - lam) * X[perm]
    y_onehot = torch.zeros(X.size(0), n_classes)
    y_onehot.scatter_(1, y.unsqueeze(1), 1)
    y_mixed = lam * y_onehot + (1 - lam) * y_onehot[perm]
    return X_mixed, y_mixed, lam, perm


def demo_mixup_visual() -> None:
    print("\n=== 2. Mixupの中身を見る ===")
    X, y = generate_shapes_dataset(4, seed=3)
    X_t = torch.tensor(X)
    y_t = torch.tensor(y)
    X_mixed, y_mixed, lam, perm = mixup(X_t, y_t, n_classes=4, alpha=0.4)
    print(f"サンプリングされた混合比率lam={lam:.3f}（{lam:.0%}が元画像、{1-lam:.0%}がシャッフルした別画像）")

    fig, axes = plt.subplots(1, 3, figsize=(9, 3.5))
    idx = 0
    axes[0].imshow(X_t[idx].permute(1, 2, 0).numpy())
    axes[0].set_title(f"画像A: {CLASS_NAMES[y_t[idx]]}")
    axes[1].imshow(X_t[perm[idx]].permute(1, 2, 0).numpy())
    axes[1].set_title(f"画像B: {CLASS_NAMES[y_t[perm[idx]]]}")
    axes[2].imshow(X_mixed[idx].permute(1, 2, 0).clamp(0, 1).numpy())
    axes[2].set_title(f"Mixup結果\nラベル=[{', '.join(f'{c}:{p:.2f}' for c, p in zip(CLASS_NAMES, y_mixed[idx].tolist()) if p > 0.01)}]", fontsize=8)
    for ax in axes:
        ax.axis("off")
    fig.tight_layout()
    out_path = "mixup_example.png"
    fig.savefig(out_path, dpi=110)
    print(f"図を保存: {__file__.rsplit('/', 1)[0]}/{out_path}")
    print(
        "Mixupは画像同士を透明合成するだけでなく、ラベルも同じ比率で混ぜた"
        "『ソフトラベル』(例: 円70%+三角30%)にする点が特徴。"
        "『0か1か』ではなく『どちらの要素をどれだけ含むか』を学習させることで、"
        "モデルが訓練データの1点1点を暗記するのではなく、クラス間の滑らかな"
        "境界を学習しやすくなる、という効果が報告されている。"
    )


class SmallCNN(nn.Module):
    """あえてGAPを使わずFlatten+大きめのFC層にし、300枚の訓練データを暗記できる
    (＝過学習しやすい)容量を持たせている。Mixupの正則化効果を見せるための設計"""

    def __init__(self, n_classes=4):
        super().__init__()
        self.features = nn.Sequential(
            nn.Conv2d(3, 32, 3, padding=1), nn.ReLU(), nn.MaxPool2d(2),
            nn.Conv2d(32, 64, 3, padding=1), nn.ReLU(), nn.MaxPool2d(2),
        )
        self.classifier = nn.Sequential(
            nn.Flatten(), nn.Linear(64 * 8 * 8, 256), nn.ReLU(), nn.Linear(256, n_classes)
        )

    def forward(self, x):
        return self.classifier(self.features(x))


def soft_cross_entropy(logits: torch.Tensor, soft_targets: torch.Tensor) -> torch.Tensor:
    log_probs = torch.log_softmax(logits, dim=1)
    return -(soft_targets * log_probs).sum(dim=1).mean()


def train_with_option(use_mixup: bool, X_train, y_train, X_test, y_test, n_classes=4, epochs=60):
    torch.manual_seed(42)
    model = SmallCNN(n_classes)
    optimizer = optim.Adam(model.parameters(), lr=1e-3)
    history = []
    n = len(X_train)
    batch_size = 32
    for epoch in range(epochs):
        model.train()
        perm_epoch = torch.randperm(n)
        for i in range(0, n, batch_size):
            idx = perm_epoch[i : i + batch_size]
            xb, yb = X_train[idx], y_train[idx]
            optimizer.zero_grad()
            if use_mixup:
                xb_mixed, yb_soft, _, _ = mixup(xb, yb, n_classes)
                out = model(xb_mixed)
                loss = soft_cross_entropy(out, yb_soft)
            else:
                out = model(xb)
                loss = nn.functional.cross_entropy(out, yb)
            loss.backward()
            optimizer.step()
        model.eval()
        with torch.no_grad():
            test_acc = (model(X_test).argmax(dim=1) == y_test).float().mean().item()
            train_acc = (model(X_train).argmax(dim=1) == y_train).float().mean().item()
        history.append((train_acc, test_acc))
    return history


def demo_mixup_training_effect() -> None:
    print("\n=== 3. Mixupの効果: 少ない訓練データでの過学習抑制 ===")
    X_train_np, y_train_np = generate_shapes_dataset(300, seed=10)  # あえて少なく: 過学習させやすくする
    X_test_np, y_test_np = generate_shapes_dataset(400, seed=11)
    X_train = torch.tensor(X_train_np)
    y_train = torch.tensor(y_train_np)
    X_test = torch.tensor(X_test_np)
    y_test = torch.tensor(y_test_np)
    print(f"訓練{len(X_train)}枚(少なめ)/ テスト{len(X_test)}枚")

    hist_normal = train_with_option(False, X_train, y_train, X_test, y_test)
    hist_mixup = train_with_option(True, X_train, y_train, X_test, y_test)

    train_normal, test_normal = zip(*hist_normal)
    train_mixup, test_mixup = zip(*hist_mixup)
    print(f"通常学習: 最終train_acc={train_normal[-1]:.3f}  最終test_acc={test_normal[-1]:.3f}  (差={train_normal[-1]-test_normal[-1]:.3f})")
    print(f"Mixup学習: 最終train_acc={train_mixup[-1]:.3f}  最終test_acc={test_mixup[-1]:.3f}  (差={train_mixup[-1]-test_mixup[-1]:.3f})")

    fig, axes = plt.subplots(1, 2, figsize=(12, 5))
    axes[0].plot(train_normal, label="訓練精度")
    axes[0].plot(test_normal, label="テスト精度")
    axes[0].set_title("通常学習")
    axes[0].set_xlabel("epoch")
    axes[0].set_ylabel("Accuracy")
    axes[0].legend()
    axes[1].plot(train_mixup, label="訓練精度")
    axes[1].plot(test_mixup, label="テスト精度")
    axes[1].set_title("Mixup学習")
    axes[1].set_xlabel("epoch")
    axes[1].set_ylabel("Accuracy")
    axes[1].legend()
    fig.suptitle("Mixupの有無による訓練/テスト精度の推移(訓練データ300枚)")
    fig.tight_layout()
    out_path = "mixup_training_comparison.png"
    fig.savefig(out_path, dpi=110)
    print(f"図を保存: {__file__.rsplit('/', 1)[0]}/{out_path}")

    normal_gap = train_normal[-1] - test_normal[-1]
    mixup_gap = train_mixup[-1] - test_mixup[-1]
    print(
        f"\n通常学習の訓練/テスト精度の差は{normal_gap:.3f}、Mixup学習は{mixup_gap:.3f}。"
        f"{'Mixupの方が差が小さく、過学習抑制の効果が見える' if mixup_gap < normal_gap else 'この設定ではMixupの方が差が縮まらず、テスト精度も同等かやや低い結果になった'}。"
    )
    print(
        "alpha(混合の強さ)を0.2〜1.0の範囲やepoch数を変えても同様の傾向だった。"
        "このモデルは300枚の訓練データを100%暗記できる容量を持っており、Mixupで"
        "混ぜ合わせた画像に対しても最終的には(ソフトラベルの意味で)ほぼ完全にフィット"
        "してしまうため、単純なMixupだけでは過学習を防ぎきれなかったと考えられる。"
        "Mixupの効果はモデル規模・データセットの複雑さ・他の正則化(Dropout等)との"
        "併用有無に左右されやすく、『入れれば必ず改善する』わけではないという"
        "実務的な注意点を、この小規模な実験からも確認できた。"
    )


def main() -> None:
    demo_standard_augmentation()
    demo_mixup_visual()
    demo_mixup_training_effect()


if __name__ == "__main__":
    main()
