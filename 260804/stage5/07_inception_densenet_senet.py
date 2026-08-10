"""GoogLeNet(Inceptionモジュール)・DenseNet(密結合)・SENet(チャネル注意)の比較

02では「層を直列に深く積む」設計(Plain CNN)に対し「スキップ結合を足す」設計(ResNet)を
比較した。このスクリプトでは、ResNetとはまた異なる3つの設計思想を、02と同じ深さ・
チャネル数のCNNで比較する:
  - Inceptionモジュール(GoogLeNet): 1つの層で異なるサイズのフィルタ(1x1/3x3/5x5)を
    並列に適用し、結果を結合する。『層を直列に深くする』のではなく
    『1つの層で複数の受容野を同時に見る』という発想
  - DenseBlock(DenseNet): ResNetの『1つ前の層を足し合わせる』スキップ結合と異なり、
    『それまでの全ての層の出力をチャネル方向に連結する』ことで特徴の再利用を徹底する
  - Squeeze-and-Excitation(SENet): チャネルごとの重要度を学習し、重要な特徴マップを
    強調する『チャネル注意』機構。ResNetのResidualBlockに追加する形で実装し、
    SEの有無だけを変えて効果を比較する(Stage 8で学ぶAttentionの先取りにあたる)
02で使ったResNetDeepを比較の基準として再利用する。
"""
import time
import importlib

import torch
import torch.nn as nn
import matplotlib.pyplot as plt

from synthetic_shapes import generate_shapes_dataset, CLASS_NAMES
import _mpl_ja  # noqa: F401

arch = importlib.import_module("02_cnn_architectures")

torch.manual_seed(42)


class InceptionModule(nn.Module):
    """1x1/3x3/5x5フィルタとプーリングを並列適用し、チャネル方向に結合するブロック

    3x3・5x5の直前に1x1畳み込み(ボトルネック)を挟むことで、
    大きいフィルタを直接適用するより計算量を抑えている。
    """

    def __init__(self, in_channels, out_channels):
        super().__init__()
        assert out_channels % 4 == 0
        branch_ch = out_channels // 4
        self.branch1 = nn.Sequential(
            nn.Conv2d(in_channels, branch_ch, 1), nn.BatchNorm2d(branch_ch), nn.ReLU()
        )
        self.branch3 = nn.Sequential(
            nn.Conv2d(in_channels, branch_ch, 1), nn.ReLU(),
            nn.Conv2d(branch_ch, branch_ch, 3, padding=1), nn.BatchNorm2d(branch_ch), nn.ReLU(),
        )
        self.branch5 = nn.Sequential(
            nn.Conv2d(in_channels, branch_ch, 1), nn.ReLU(),
            nn.Conv2d(branch_ch, branch_ch, 5, padding=2), nn.BatchNorm2d(branch_ch), nn.ReLU(),
        )
        self.branch_pool = nn.Sequential(
            nn.MaxPool2d(3, stride=1, padding=1),
            nn.Conv2d(in_channels, branch_ch, 1), nn.BatchNorm2d(branch_ch), nn.ReLU(),
        )

    def forward(self, x):
        return torch.cat(
            [self.branch1(x), self.branch3(x), self.branch5(x), self.branch_pool(x)], dim=1
        )


class InceptionNet(nn.Module):
    """InceptionModuleをn_blocks回積んだCNN(02のPlainDeepCNN/ResNetDeepと同条件)"""

    def __init__(self, n_classes=4, n_blocks=10, channels=24):
        super().__init__()
        self.stem = nn.Sequential(
            nn.Conv2d(3, channels, 3, stride=2, padding=1), nn.BatchNorm2d(channels), nn.ReLU()
        )
        self.blocks = nn.Sequential(*[InceptionModule(channels, channels) for _ in range(n_blocks)])
        self.gap = nn.AdaptiveAvgPool2d(1)
        self.classifier = nn.Linear(channels, n_classes)

    def forward(self, x):
        x = self.blocks(self.stem(x))
        x = self.gap(x).flatten(1)
        return self.classifier(x)


class DenseBlock(nn.Module):
    """各層が『それより前の全ての層の出力』をチャネル方向に連結して入力に使うブロック

    ResNetのスキップ結合が『1つ前の層の出力を加算する』のに対し、DenseNetは
    『それまでの全層の出力を連結する』ことで、特徴の再利用をより徹底している。
    """

    def __init__(self, in_channels, n_layers, growth_rate):
        super().__init__()
        self.layers = nn.ModuleList()
        ch = in_channels
        for _ in range(n_layers):
            self.layers.append(nn.Sequential(
                nn.Conv2d(ch, growth_rate, 3, padding=1), nn.BatchNorm2d(growth_rate), nn.ReLU()
            ))
            ch += growth_rate
        self.out_channels = ch

    def forward(self, x):
        features = [x]
        for layer in self.layers:
            out = layer(torch.cat(features, dim=1))
            features.append(out)
        return torch.cat(features, dim=1)


class DenseNet(nn.Module):
    def __init__(self, n_classes=4, n_blocks=10, channels=24, growth_rate=8):
        super().__init__()
        self.stem = nn.Sequential(
            nn.Conv2d(3, channels, 3, stride=2, padding=1), nn.BatchNorm2d(channels), nn.ReLU()
        )
        self.dense_block = DenseBlock(channels, n_blocks, growth_rate)
        self.gap = nn.AdaptiveAvgPool2d(1)
        self.classifier = nn.Linear(self.dense_block.out_channels, n_classes)

    def forward(self, x):
        x = self.dense_block(self.stem(x))
        x = self.gap(x).flatten(1)
        return self.classifier(x)


class SEBlock(nn.Module):
    """チャネルごとの重要度を学習し、特徴マップをチャネル単位で重み付けする機構

    Squeeze: GAPで各チャネルを1つの値に要約する
    Excitation: 小さなMLPでチャネルごとの重要度(0〜1)を出力する
    それを元の特徴マップに掛け合わせることで、重要なチャネルを強調する。
    """

    def __init__(self, channels, reduction=4):
        super().__init__()
        self.gap = nn.AdaptiveAvgPool2d(1)
        self.fc = nn.Sequential(
            nn.Linear(channels, channels // reduction), nn.ReLU(),
            nn.Linear(channels // reduction, channels), nn.Sigmoid(),
        )

    def forward(self, x):
        b, c, _, _ = x.shape
        weights = self.fc(self.gap(x).view(b, c)).view(b, c, 1, 1)
        return x * weights


class SEResidualBlock(nn.Module):
    """02のResidualBlockに、SE機構によるチャネル注意を追加したブロック"""

    def __init__(self, channels):
        super().__init__()
        self.conv1 = nn.Conv2d(channels, channels, 3, padding=1)
        self.bn1 = nn.BatchNorm2d(channels)
        self.conv2 = nn.Conv2d(channels, channels, 3, padding=1)
        self.bn2 = nn.BatchNorm2d(channels)
        self.se = SEBlock(channels)
        self.relu = nn.ReLU()

    def forward(self, x):
        identity = x
        out = self.relu(self.bn1(self.conv1(x)))
        out = self.bn2(self.conv2(out))
        out = self.se(out)  # スキップ結合で足し合わせる前にチャネル重み付けする
        return self.relu(out + identity)


class SEResNet(nn.Module):
    """02のResNetDeepと同条件で、ResidualBlockだけをSE付きに差し替えたモデル"""

    def __init__(self, n_classes=4, n_blocks=10, channels=24):
        super().__init__()
        self.stem = nn.Sequential(
            nn.Conv2d(3, channels, 3, stride=2, padding=1), nn.BatchNorm2d(channels), nn.ReLU()
        )
        self.blocks = nn.Sequential(*[SEResidualBlock(channels) for _ in range(n_blocks)])
        self.gap = nn.AdaptiveAvgPool2d(1)
        self.classifier = nn.Linear(channels, n_classes)

    def forward(self, x):
        x = self.blocks(self.stem(x))
        x = self.gap(x).flatten(1)
        return self.classifier(x)


def main() -> None:
    print("=== データ生成: 合成図形(円/四角/三角/十字, 32x32 RGB) ===")
    X_train_np, y_train_np = generate_shapes_dataset(1500, seed=0)
    X_test_np, y_test_np = generate_shapes_dataset(300, seed=1)
    X_train = torch.tensor(X_train_np)
    y_train = torch.tensor(y_train_np)
    X_test = torch.tensor(X_test_np)
    y_test = torch.tensor(y_test_np)
    print(f"訓練{len(X_train)}枚 / テスト{len(X_test)}枚, クラス: {CLASS_NAMES}")

    models = {
        "ResNetDeep(02の再掲, 比較の基準)": arch.ResNetDeep(n_blocks=10),
        "InceptionNet(並列フィルタ, GoogLeNet型)": InceptionNet(n_blocks=10),
        "DenseNet(密結合, 全層直結)": DenseNet(n_blocks=10),
        "SEResNet(ResNet+チャネル注意, SENet型)": SEResNet(n_blocks=10),
    }

    histories = {}
    print("\n=== 学習(各モデル15epoch) ===")
    for name, model in models.items():
        torch.manual_seed(42)
        t_start = time.perf_counter()
        h = arch.train_and_eval(model, X_train, y_train, X_test, y_test, epochs=15)
        print(f"[{name}] 学習完了 ({time.perf_counter()-t_start:.1f}秒)")
        histories[name] = h
        print(
            f"{name}: パラメータ数={arch.count_params(model):,}  学習時間={h['time']:.1f}秒  "
            f"最終train_loss={h['train_loss'][-1]:.4f}  最終test_acc={h['test_acc'][-1]:.3f}"
        )

    fig, axes = plt.subplots(1, 2, figsize=(13, 5.5))
    for name, h in histories.items():
        axes[0].plot(h["train_loss"], label=name)
        axes[1].plot(h["test_acc"], label=name)
    axes[0].set_xlabel("epoch")
    axes[0].set_ylabel("訓練loss")
    axes[0].set_title("訓練lossの推移(Inception/DenseNet/SENet型の比較)")
    axes[0].legend(fontsize=7)
    axes[1].set_xlabel("epoch")
    axes[1].set_ylabel("テスト精度")
    axes[1].set_title("テスト精度の推移")
    axes[1].legend(fontsize=7)
    fig.tight_layout()
    out_path = "inception_densenet_senet.png"
    fig.savefig(out_path, dpi=110)
    print(f"\n図を保存: {__file__.rsplit('/', 1)[0]}/{out_path}")

    resnet_acc = histories["ResNetDeep(02の再掲, 比較の基準)"]["test_acc"][-1]
    se_acc = histories["SEResNet(ResNet+チャネル注意, SENet型)"]["test_acc"][-1]
    inception_acc = histories["InceptionNet(並列フィルタ, GoogLeNet型)"]["test_acc"][-1]
    dense_acc = histories["DenseNet(密結合, 全層直結)"]["test_acc"][-1]
    se_diff = se_acc - resnet_acc
    print(
        f"\n基準のResNetDeep(test_acc={resnet_acc:.3f})に対し、SE機構を追加したSEResNetは"
        f"test_acc={se_acc:.3f}({'向上' if se_diff > 0 else '同程度か低下'}、差分{se_diff:+.3f})。"
        "SEブロックはResNetの構造(スキップ結合)自体は変えず、チャネルごとの重み付けだけを"
        "追加している点が特徴で、既存アーキテクチャに『後付け』しやすい軽量な改良であることが"
        "パラメータ数の増加幅(SEの全結合層分のみ)からも確認できる。"
        f"InceptionNet(test_acc={inception_acc:.3f})とDenseNet(test_acc={dense_acc:.3f})は"
        "いずれもこの合成図形データセット・浅い15epochという条件下での結果であり、"
        "『層を直列に深く積む』ResNet型とは異なる設計(並列フィルタ／全層の密な連結)でも"
        "同程度の分類が学習できることを確認した。"
    )


if __name__ == "__main__":
    main()
