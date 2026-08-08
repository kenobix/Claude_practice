"""転移学習: ImageNetで事前学習済みのResNet18を使う

ゼロから学習する代わりに、大規模データ(ImageNet, 1000クラス)で
すでに学習済みのモデルを土台にする。2つのやり方を比較する:
  (A) 特徴抽出(feature extraction): 事前学習済み部分は完全に固定し、
      最後の分類層だけを新しいタスク用に学習し直す
  (B) ファインチューニング(fine-tuning): 事前学習済みの重み全体も
      小さい学習率で一緒に更新する
"""
import time

import torch
import torch.nn as nn
import torch.optim as optim
import torchvision.models as models
import matplotlib.pyplot as plt

from synthetic_shapes import generate_shapes_dataset, CLASS_NAMES
import _mpl_ja  # noqa: F401

torch.manual_seed(42)


def build_model(mode: str) -> nn.Module:
    model = models.resnet18(weights=models.ResNet18_Weights.IMAGENET1K_V1)
    if mode == "feature_extraction":
        for param in model.parameters():
            param.requires_grad = False  # 事前学習済み部分は凍結(勾配計算しない)
    model.fc = nn.Linear(model.fc.in_features, len(CLASS_NAMES))  # 最終層だけ4クラス用に付け替え
    return model


def train_and_eval(model, X_train, y_train, X_test, y_test, epochs, lr, only_fc_params=False):
    params = model.fc.parameters() if only_fc_params else model.parameters()
    optimizer = optim.Adam(params, lr=lr)
    criterion = nn.CrossEntropyLoss()
    n = len(X_train)
    batch_size = 32
    history = {"train_loss": [], "test_acc": []}
    t0 = time.perf_counter()
    for epoch in range(epochs):
        model.train()
        perm = torch.randperm(n)
        epoch_loss = 0.0
        for i in range(0, n, batch_size):
            idx = perm[i : i + batch_size]
            optimizer.zero_grad()
            out = model(X_train[idx])
            loss = criterion(out, y_train[idx])
            loss.backward()
            optimizer.step()
            epoch_loss += loss.item() * len(idx)
        history["train_loss"].append(epoch_loss / n)
        model.eval()
        with torch.no_grad():
            test_acc = (model(X_test).argmax(dim=1) == y_test).float().mean().item()
        history["test_acc"].append(test_acc)
    history["time"] = time.perf_counter() - t0
    return history


def main() -> None:
    print("=== データ生成: 合成図形(円/四角/三角/十字, 32x32 RGB) ===")
    # ResNet18は元々ImageNet(224x224)向けなので、入力を224x224にリサイズする
    X_train_np, y_train_np = generate_shapes_dataset(300, seed=0)  # 訓練データは少なめ(転移学習の強みを見る設定)
    X_test_np, y_test_np = generate_shapes_dataset(200, seed=1)
    X_train = nn.functional.interpolate(torch.tensor(X_train_np), size=224, mode="bilinear", align_corners=False)
    X_test = nn.functional.interpolate(torch.tensor(X_test_np), size=224, mode="bilinear", align_corners=False)
    # ImageNet学習時の正規化統計(RGB各チャネルの平均・標準偏差)に合わせる
    mean = torch.tensor([0.485, 0.456, 0.406]).view(1, 3, 1, 1)
    std = torch.tensor([0.229, 0.224, 0.225]).view(1, 3, 1, 1)
    X_train = (X_train - mean) / std
    X_test = (X_test - mean) / std
    y_train = torch.tensor(y_train_np)
    y_test = torch.tensor(y_test_np)
    print(f"訓練{len(X_train)}枚(あえて少なめ) / テスト{len(X_test)}枚, 224x224にリサイズしてResNet18に入力")

    epochs = 5
    print("\n=== (A) 特徴抽出: 事前学習済み部分を凍結し、最終層だけ学習 ===")
    model_fe = build_model("feature_extraction")
    h_fe = train_and_eval(model_fe, X_train, y_train, X_test, y_test, epochs=epochs, lr=1e-3, only_fc_params=True)
    print(f"学習時間={h_fe['time']:.1f}秒  最終test_acc={h_fe['test_acc'][-1]:.3f}")

    print("\n=== (B) ファインチューニング: 全パラメータを小さい学習率で更新 ===")
    model_ft = build_model("fine_tuning")
    h_ft = train_and_eval(model_ft, X_train, y_train, X_test, y_test, epochs=epochs, lr=1e-4, only_fc_params=False)
    print(f"学習時間={h_ft['time']:.1f}秒  最終test_acc={h_ft['test_acc'][-1]:.3f}")

    fig, axes = plt.subplots(1, 2, figsize=(12, 5))
    axes[0].plot(h_fe["train_loss"], label="特徴抽出(最終層のみ学習)")
    axes[0].plot(h_ft["train_loss"], label="ファインチューニング(全体を学習)")
    axes[0].set_xlabel("epoch")
    axes[0].set_ylabel("訓練loss")
    axes[0].set_title("訓練lossの推移")
    axes[0].legend()
    axes[1].plot(h_fe["test_acc"], label="特徴抽出(最終層のみ学習)")
    axes[1].plot(h_ft["test_acc"], label="ファインチューニング(全体を学習)")
    axes[1].set_xlabel("epoch")
    axes[1].set_ylabel("テスト精度")
    axes[1].set_title("テスト精度の推移")
    axes[1].legend()
    fig.tight_layout()
    out_path = "transfer_learning_comparison.png"
    fig.savefig(out_path, dpi=110)
    print(f"\n図を保存: {__file__.rsplit('/', 1)[0]}/{out_path}")

    print(
        f"\n学習時間: 特徴抽出={h_fe['time']:.1f}秒 vs ファインチューニング={h_ft['time']:.1f}秒"
        f"（特徴抽出は勾配計算する対象が最終層だけなので、1epochあたりの計算が軽い）"
    )
    print(
        "ImageNetの100万枚以上の画像で学習済みのResNet18は、既に『エッジ・模様・形の"
        "基本的なパーツを検出する』能力を持っている。今回のような単純な図形分類でも、"
        "訓練データがわずか300枚という少なさにもかかわらず、ゼロから学習するより"
        "はるかに早く・高い精度に到達できる。これが転移学習の実務的な価値であり、"
        "『大規模データで学習された表現を、少量データの別タスクに使い回す』という考え方の実例。"
    )


if __name__ == "__main__":
    main()
