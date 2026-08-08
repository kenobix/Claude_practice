"""ミニプロジェクト: 自作の小さいCNN(スクラッチ学習) vs 事前学習済みResNetのファインチューニング

02のResNetDeep(スクラッチ, 32x32入力)と、04のResNet18ファインチューニング
(ImageNet事前学習, 224x224入力)を、全く同じ300枚の訓練データ・200枚のテスト
データで比較し、「少量データしかない時、ゼロから学習するのと事前学習済み
モデルを流用するのとでどれだけ差が出るか」を体感する。
"""
import importlib
import time

import torch
import torch.nn as nn
import matplotlib.pyplot as plt

from synthetic_shapes import generate_shapes_dataset, CLASS_NAMES
import _mpl_ja  # noqa: F401

arch = importlib.import_module("02_cnn_architectures")
transfer = importlib.import_module("04_transfer_learning")

torch.manual_seed(42)


def main() -> None:
    print("=== データ生成: 円/四角/三角/十字, 訓練300枚(少なめ)/テスト200枚 ===")
    X_train_np, y_train_np = generate_shapes_dataset(300, seed=0)  # 04と全く同じ分割
    X_test_np, y_test_np = generate_shapes_dataset(200, seed=1)
    y_train = torch.tensor(y_train_np)
    y_test = torch.tensor(y_test_np)

    # --- (1) 自作の小さいCNNをゼロから学習(32x32のまま) ---
    print("\n=== (1) 自作CNN(ResNetDeep, 7層)をゼロから学習 ===")
    X_train_32 = torch.tensor(X_train_np)
    X_test_32 = torch.tensor(X_test_np)
    torch.manual_seed(42)
    scratch_model = arch.ResNetDeep(n_classes=4, n_blocks=3)
    t0 = time.perf_counter()
    h_scratch = arch.train_and_eval(scratch_model, X_train_32, y_train, X_test_32, y_test, epochs=40, lr=1e-3)
    t_scratch = time.perf_counter() - t0
    print(f"学習時間={t_scratch:.1f}秒  最終test_acc={h_scratch['test_acc'][-1]:.3f}  最終train_loss={h_scratch['train_loss'][-1]:.4f}")

    # --- (2) 事前学習済みResNet18をファインチューニング(224x224にリサイズ) ---
    print("\n=== (2) 事前学習済みResNet18をファインチューニング(3epoch) ===")
    X_train_224 = nn.functional.interpolate(X_train_32, size=224, mode="bilinear", align_corners=False)
    X_test_224 = nn.functional.interpolate(X_test_32, size=224, mode="bilinear", align_corners=False)
    mean = torch.tensor([0.485, 0.456, 0.406]).view(1, 3, 1, 1)
    std = torch.tensor([0.229, 0.224, 0.225]).view(1, 3, 1, 1)
    X_train_224 = (X_train_224 - mean) / std
    X_test_224 = (X_test_224 - mean) / std

    torch.manual_seed(42)
    pretrained_model = transfer.build_model("fine_tuning")
    h_pretrained = transfer.train_and_eval(
        pretrained_model, X_train_224, y_train, X_test_224, y_test, epochs=3, lr=1e-4, only_fc_params=False
    )
    print(f"学習時間={h_pretrained['time']:.1f}秒  最終test_acc={h_pretrained['test_acc'][-1]:.3f}  最終train_loss={h_pretrained['train_loss'][-1]:.4f}")

    print("\n=== 3. 比較まとめ ===")
    print(f"{'':30s} {'テスト精度':>10s} {'学習時間':>10s} {'パラメータ数':>14s}")
    scratch_params = sum(p.numel() for p in scratch_model.parameters())
    pretrained_params = sum(p.numel() for p in pretrained_model.parameters())
    print(f"{'自作CNN(スクラッチ, 7層)':30s} {h_scratch['test_acc'][-1]:>10.3f} {t_scratch:>9.1f}秒 {scratch_params:>14,}")
    print(f"{'ResNet18(ファインチューニング)':30s} {h_pretrained['test_acc'][-1]:>10.3f} {h_pretrained['time']:>9.1f}秒 {pretrained_params:>14,}")

    fig, axes = plt.subplots(1, 2, figsize=(12, 5))
    axes[0].plot(h_scratch["test_acc"], label=f"自作CNN(スクラッチ, {len(h_scratch['test_acc'])}epoch)")
    axes[0].set_xlabel("epoch")
    axes[0].set_ylabel("テスト精度")
    axes[0].set_title("自作CNNの学習曲線(訓練データ300枚)")
    axes[0].set_ylim(0, 1.05)
    axes[0].legend()

    axes[1].plot(h_pretrained["test_acc"], label=f"ResNet18ファインチューニング({len(h_pretrained['test_acc'])}epoch)", color="tab:orange")
    axes[1].set_xlabel("epoch")
    axes[1].set_ylabel("テスト精度")
    axes[1].set_title("事前学習済みResNet18の学習曲線(同じ訓練データ300枚)")
    axes[1].set_ylim(0, 1.05)
    axes[1].legend()

    fig.suptitle("自作CNN(スクラッチ) vs 事前学習済みResNet18(ファインチューニング)")
    fig.tight_layout()
    out_path = "scratch_vs_pretrained.png"
    fig.savefig(out_path, dpi=110)
    print(f"\n図を保存: {__file__.rsplit('/', 1)[0]}/{out_path}")

    acc_diff = h_pretrained["test_acc"][-1] - h_scratch["test_acc"][-1]
    print(
        f"\n同じ300枚という少量の訓練データで、事前学習済みResNet18の方がテスト精度が"
        f"{acc_diff:+.3f}(約{abs(acc_diff)*100:.0f}ポイント){'高い' if acc_diff > 0 else '低い'}。"
        "自作CNNはこのデータ量だと訓練データを暗記(過学習)しがちで、汎化性能が伸び悩む。"
        "事前学習済みモデルは既にImageNetの100万枚以上の画像から『形や模様を捉える"
        "基礎的な特徴量』を獲得済みのため、少量データでの追加学習(ファインチューニング)"
        "だけで済み、同じデータ量でも高い精度に到達しやすい。"
        "ただし事前学習済みモデルはパラメータ数・計算量・入力画像サイズ(224x224)の"
        "面で自作CNNよりずっと重く、学習時間も長い。『データが少ない/開発時間が"
        "短い時は転移学習、大量のデータと計算資源が使え、モデルを小型・高速にしたい"
        "時はスクラッチ設計』という使い分けが実務での判断軸になる。"
    )


if __name__ == "__main__":
    main()
