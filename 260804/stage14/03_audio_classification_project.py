"""ミニプロジェクト: 波形の種類(音色)をMFCC特徴量で分類する

01でPCM・FFTの基礎、02でスペクトログラム・MFCCのスクラッチ実装を確認した。
このミニプロジェクトでは、02で実装したMFCCを実際の分類タスクの特徴量として使う。

正弦波(sine)・矩形波(square)・のこぎり波(sawtooth)・三角波(triangle)は、
どれも「基本周波数(音の高さ)」は同じにできるが、含まれる倍音の構成
(音色を決める要素)が全く異なる:
  - 正弦波: 基本周波数の成分のみ
  - 矩形波: 奇数倍音のみ、振幅は1/nで減衰
  - のこぎり波: 全ての倍音を含み、振幅は1/nで減衰
  - 三角波: 奇数倍音のみ、振幅は1/n²でより急激に減衰
基本周波数(音の高さ)をランダムに変えても、この倍音構成の違い(音色)は
一貫しているはずなので、「音の高さによらず音色を判別できるか」という
現実の音声認識・環境音認識にも通じる課題として、02のMFCC(声道の共鳴特性=
スペクトル包絡を捉える特徴量)がこの音色の違いを分類できるかを検証する。
"""
import importlib

import numpy as np
from scipy import signal as scipy_signal
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import train_test_split
from sklearn.metrics import confusion_matrix, accuracy_score
import matplotlib.pyplot as plt

import _mpl_ja  # noqa: F401

mfcc_module = importlib.import_module("02_spectrogram_mfcc")
compute_mfcc = mfcc_module.compute_mfcc
SAMPLE_RATE = mfcc_module.SAMPLE_RATE

CLASS_NAMES = ["sine(正弦波)", "square(矩形波)", "sawtooth(のこぎり波)", "triangle(三角波)"]
rng = np.random.RandomState(42)


def generate_waveform(cls: int, freq: float, duration: float = 0.4) -> np.ndarray:
    t = np.arange(0, duration, 1 / SAMPLE_RATE)
    if cls == 0:
        wave = np.sin(2 * np.pi * freq * t)
    elif cls == 1:
        wave = scipy_signal.square(2 * np.pi * freq * t)
    elif cls == 2:
        wave = scipy_signal.sawtooth(2 * np.pi * freq * t)
    else:
        wave = scipy_signal.sawtooth(2 * np.pi * freq * t, width=0.5)  # width=0.5で三角波になる
    wave = wave + rng.normal(0, 0.02, size=wave.shape)  # マイクノイズ相当
    return wave / np.max(np.abs(wave))


def build_dataset(n_per_class: int):
    X, y, freqs_used = [], [], []
    for cls in range(len(CLASS_NAMES)):
        for _ in range(n_per_class):
            freq = rng.uniform(150, 600)  # 音の高さはランダムに変える
            wave = generate_waveform(cls, freq)
            mfcc, _, _ = compute_mfcc(wave)
            X.append(mfcc.mean(axis=0))  # 時間方向に平均した13次元ベクトルを特徴量にする
            y.append(cls)
            freqs_used.append(freq)
    return np.array(X), np.array(y), np.array(freqs_used)


def main() -> None:
    print("=== 1. 4種類の波形(音色)からMFCC特徴量データセットを作成 ===")
    X, y, freqs = build_dataset(n_per_class=150)
    print(f"サンプル数={len(X)}(クラスごとに150), 特徴量次元={X.shape[1]}(MFCC13次元・時間平均)")
    print(f"基本周波数の範囲: {freqs.min():.0f}Hz〜{freqs.max():.0f}Hz(音の高さはランダムに変化)")

    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.3, random_state=0, stratify=y)
    print(f"訓練{len(X_train)}件 / テスト{len(X_test)}件")

    print("\n=== 2. ロジスティック回帰でMFCC特徴量から音色を分類 ===")
    clf = LogisticRegression(max_iter=1000, random_state=0)
    clf.fit(X_train, y_train)
    y_pred = clf.predict(X_test)
    acc = accuracy_score(y_test, y_pred)
    cm = confusion_matrix(y_test, y_pred)
    print(f"テスト精度(Accuracy)={acc:.3f}")

    print("\n=== 3. 可視化 ===")
    fig, axes = plt.subplots(1, 2, figsize=(13, 5.5))

    example_freq = 220.0
    for cls in range(len(CLASS_NAMES)):
        wave = generate_waveform(cls, example_freq, duration=0.02)
        t = np.arange(len(wave)) / SAMPLE_RATE * 1000
        axes[0].plot(t, wave + cls * 2.5, label=CLASS_NAMES[cls])
    axes[0].set_xlabel("時間(ミリ秒)")
    axes[0].set_yticks([])
    axes[0].set_title(f"4種類の波形の例(基本周波数={example_freq:.0f}Hz)")
    axes[0].legend(fontsize=8)

    im = axes[1].imshow(cm, cmap="Blues")
    axes[1].set_xticks(range(len(CLASS_NAMES)))
    axes[1].set_xticklabels(CLASS_NAMES, rotation=30, ha="right", fontsize=8)
    axes[1].set_yticks(range(len(CLASS_NAMES)))
    axes[1].set_yticklabels(CLASS_NAMES, fontsize=8)
    axes[1].set_xlabel("予測クラス")
    axes[1].set_ylabel("正解クラス")
    axes[1].set_title(f"混同行列(テスト精度={acc:.3f})")
    for i in range(len(CLASS_NAMES)):
        for j in range(len(CLASS_NAMES)):
            axes[1].text(j, i, cm[i, j], ha="center", va="center",
                         color="white" if cm[i, j] > cm.max() / 2 else "black")
    fig.colorbar(im, ax=axes[1], fraction=0.046)

    fig.tight_layout()
    out_path = "audio_classification_project.png"
    fig.savefig(out_path, dpi=110)
    print(f"\n図を保存: {__file__.rsplit('/', 1)[0]}/{out_path}")

    most_confused = None
    cm_offdiag = cm.copy()
    np.fill_diagonal(cm_offdiag, 0)
    if cm_offdiag.max() > 0:
        i, j = np.unravel_index(cm_offdiag.argmax(), cm_offdiag.shape)
        most_confused = (CLASS_NAMES[i], CLASS_NAMES[j], cm_offdiag[i, j])
    print(
        f"\nMFCC特徴量(13次元、時間平均)とロジスティック回帰の組み合わせで、"
        f"テスト精度{acc:.3f}を達成した。学習・テストとも基本周波数(音の高さ)を"
        "150〜600Hzの範囲でランダムに変えているため、この精度は『音の高さによらず"
        "倍音構成(音色)の違いを識別できているか』を反映している。"
        + (f"最も混同されやすかったのは正解が『{most_confused[0]}』で『{most_confused[1]}』と"
           f"誤分類された{most_confused[2]}件で、矩形波・三角波はどちらも奇数倍音のみを含み"
           "振幅の減衰の仕方(1/n vs 1/n²)だけが異なるという音響的に近い関係にあるため、"
           "特に低次のMFCC係数だけでは区別が難しい場合があると考えられる。"
           if most_confused else "混同行列は完全に対角成分に集中しており、4種類の音色は"
           "全て正しく分類できた。") +
        "この結果は、音声認識でMFCCが『何を話したか』だけでなく、環境音認識や楽器音分類"
        "といった『音色の違いを識別する』タスクにも応用できる、汎用的な音響特徴量である"
        "ことを裏付けている。"
    )


if __name__ == "__main__":
    main()
