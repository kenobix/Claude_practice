"""スペクトログラム(短時間フーリエ変換)とMFCCをスクラッチで実装する

01のFFTは「信号全体をまとめて1回」周波数分解するため、時間とともに周波数が
変化する音(しゃべり声や、音程が変わる音)に対しては「いつ・どの周波数が
鳴っていたか」という時間情報が失われてしまう。この問題を解決するのが
短時間フーリエ変換(STFT): 信号を短い時間窓(フレーム)に区切り、フレームごとに
FFTをかけて並べることで、「時間×周波数×強さ」の2次元表現(スペクトログラム)を作る。

さらに、スペクトログラムから音声認識で古くから使われる特徴量MFCC
(メル周波数ケプストラム係数)を計算する。人間の聴覚は高い周波数の違いを
低い周波数の違いほど敏感に感じ取れない(メル尺度)ため、
  1) スペクトログラムの各フレームに、メル尺度で等間隔に並んだ三角フィルタ群
     (メルフィルタバンク)をかけて、周波数軸を人間の聴覚に近い解像度に圧縮する
  2) 対数を取る(人間は音量の変化も対数的に感じるため)
  3) 離散コサイン変換(DCT)でフィルタ間の相関を取り除き、低次の係数だけを残す
という3段階の処理を行い、声道の共鳴特性(スペクトル包絡=声色・母音の違い)を
少数の係数でコンパクトに表す。
"""
import numpy as np
from scipy.fft import dct
import matplotlib.pyplot as plt

import _mpl_ja  # noqa: F401

SAMPLE_RATE = 8000


def stft(signal, frame_size=400, hop_size=160):
    """Hann窓をかけたフレームごとにFFTを適用し、(フレーム数, 周波数ビン数)の複素スペクトログラムを返す"""
    window = np.hanning(frame_size)
    n_frames = 1 + (len(signal) - frame_size) // hop_size
    spec = np.zeros((n_frames, frame_size // 2 + 1), dtype=complex)
    for i in range(n_frames):
        start = i * hop_size
        frame = signal[start:start + frame_size] * window
        spec[i] = np.fft.rfft(frame)
    freqs = np.fft.rfftfreq(frame_size, d=1 / SAMPLE_RATE)
    times = np.arange(n_frames) * hop_size / SAMPLE_RATE
    return spec, freqs, times


def hz_to_mel(hz):
    return 2595 * np.log10(1 + hz / 700)


def mel_to_hz(mel):
    return 700 * (10 ** (mel / 2595) - 1)


def build_mel_filterbank(n_filters, frame_size, sample_rate):
    """メル尺度で等間隔な中心周波数を持つ三角フィルタ群を作る(0Hz〜ナイキスト周波数の範囲)"""
    n_fft_bins = frame_size // 2 + 1
    mel_min, mel_max = hz_to_mel(0), hz_to_mel(sample_rate / 2)
    mel_points = np.linspace(mel_min, mel_max, n_filters + 2)
    hz_points = mel_to_hz(mel_points)
    bin_points = np.floor((frame_size + 1) * hz_points / sample_rate).astype(int)

    filterbank = np.zeros((n_filters, n_fft_bins))
    for m in range(1, n_filters + 1):
        left, center, right = bin_points[m - 1], bin_points[m], bin_points[m + 1]
        for k in range(left, center):
            if center > left:
                filterbank[m - 1, k] = (k - left) / (center - left)
        for k in range(center, right):
            if right > center:
                filterbank[m - 1, k] = (right - k) / (right - center)
    return filterbank, hz_points[1:-1]


def compute_mfcc(signal, n_filters=26, n_mfcc=13, frame_size=400, hop_size=160):
    spec, freqs, times = stft(signal, frame_size, hop_size)
    power_spec = (np.abs(spec) ** 2) / frame_size

    filterbank, _ = build_mel_filterbank(n_filters, frame_size, SAMPLE_RATE)
    mel_energy = power_spec @ filterbank.T  # (n_frames, n_filters)
    log_mel_energy = np.log(mel_energy + 1e-10)

    mfcc = dct(log_mel_energy, type=2, axis=1, norm="ortho")[:, :n_mfcc]
    return mfcc, log_mel_energy, times


def make_formant_vowel(f0, formants, duration=0.5, sample_rate=SAMPLE_RATE):
    """基本周波数f0の倍音列を、フォルマント周波数付近を強調する包絡で重み付けして合成する
    (母音の音色の違いを簡易的に再現する)"""
    t = np.arange(0, duration, 1 / sample_rate)
    signal = np.zeros_like(t)
    n_harmonics = int((sample_rate / 2) / f0)
    for h in range(1, n_harmonics + 1):
        freq = f0 * h
        envelope = sum(np.exp(-0.5 * ((freq - fc) / 150) ** 2) for fc in formants)
        amp = (0.1 + envelope) / h  # 倍音は高次ほど基本的に弱くなる(1/h)ロールオフ
        signal += amp * np.sin(2 * np.pi * freq * t)
    return signal / np.max(np.abs(signal))


def main() -> None:
    print("=== 1. チャープ信号(周波数が時間とともに変化する音)でスペクトログラムを確認 ===")
    duration = 1.0
    t = np.arange(0, duration, 1 / SAMPLE_RATE)
    f0_start, f0_end = 200, 1800
    chirp = np.sin(2 * np.pi * (f0_start * t + (f0_end - f0_start) / (2 * duration) * t ** 2))
    spec, freqs, times = stft(chirp)
    magnitude_db = 20 * np.log10(np.abs(spec).T + 1e-6)
    print(f"チャープ信号: {f0_start}Hz→{f0_end}Hzへ{duration}秒かけて上昇, "
          f"スペクトログラムの形状=(フレーム数{spec.shape[0]}, 周波数ビン数{spec.shape[1]})")

    print("\n=== 2. メルフィルタバンクを構築 ===")
    filterbank, center_freqs = build_mel_filterbank(n_filters=13, frame_size=400, sample_rate=SAMPLE_RATE)
    print(f"フィルタ数=13, 低域と高域のフィルタ中心周波数の間隔: "
          f"{center_freqs[1]-center_freqs[0]:.0f}Hz(低域) vs {center_freqs[-1]-center_freqs[-2]:.0f}Hz(高域)"
          "(メル尺度により高域ほど1フィルタあたりの周波数幅が広くなる)")

    print("\n=== 3. 母音らしき2種類の合成音でMFCCを比較 ===")
    # 日本語の「あ」「い」に近いフォルマント周波数(F1, F2)の概算値を使う
    vowel_a = make_formant_vowel(f0=150, formants=[800, 1300])
    vowel_i = make_formant_vowel(f0=150, formants=[300, 2300])
    mfcc_a, logmel_a, _ = compute_mfcc(vowel_a)
    mfcc_i, logmel_i, _ = compute_mfcc(vowel_i)
    mfcc_a_mean = mfcc_a.mean(axis=0)
    mfcc_i_mean = mfcc_i.mean(axis=0)
    diff = np.abs(mfcc_a_mean - mfcc_i_mean).sum()
    print(f"「あ」らしき音(F1=800Hz,F2=1300Hz)と「い」らしき音(F1=300Hz,F2=2300Hz)の"
          f"MFCC(13次元、時間平均)の絶対差の合計={diff:.2f}")

    print("\n=== 4. 可視化 ===")
    fig, axes = plt.subplots(2, 2, figsize=(13, 9.5))

    im0 = axes[0, 0].pcolormesh(times, freqs, magnitude_db, shading="auto", cmap="magma")
    axes[0, 0].set_xlabel("時間(秒)")
    axes[0, 0].set_ylabel("周波数(Hz)")
    axes[0, 0].set_title(f"チャープ信号のスペクトログラム({f0_start}→{f0_end}Hz)")
    fig.colorbar(im0, ax=axes[0, 0], label="振幅(dB)")

    for i in range(filterbank.shape[0]):
        axes[0, 1].plot(np.linspace(0, SAMPLE_RATE / 2, filterbank.shape[1]), filterbank[i])
    axes[0, 1].set_xlabel("周波数(Hz)")
    axes[0, 1].set_title("メルフィルタバンク(13個の三角フィルタ)\n高域ほど1つのフィルタが担当する周波数幅が広い")

    im1 = axes[1, 0].imshow(logmel_a.T, aspect="auto", origin="lower", cmap="viridis")
    axes[1, 0].set_title("「あ」らしき合成音のlog-melスペクトログラム")
    axes[1, 0].set_xlabel("フレーム")
    axes[1, 0].set_ylabel("メルフィルタ番号")
    fig.colorbar(im1, ax=axes[1, 0])

    width = 0.35
    x = np.arange(13)
    axes[1, 1].bar(x - width / 2, mfcc_a_mean, width, label="「あ」らしき音")
    axes[1, 1].bar(x + width / 2, mfcc_i_mean, width, label="「い」らしき音")
    axes[1, 1].set_xlabel("MFCC次数")
    axes[1, 1].set_ylabel("係数の値(時間平均)")
    axes[1, 1].set_title("MFCC(13次元)の比較")
    axes[1, 1].legend()

    fig.tight_layout()
    out_path = "spectrogram_mfcc.png"
    fig.savefig(out_path, dpi=110)
    print(f"\n図を保存: {__file__.rsplit('/', 1)[0]}/{out_path}")

    print(
        "\nチャープ信号のスペクトログラムでは、01のFFT(信号全体をまとめて1回変換)では"
        "つぶれてしまう『時間とともに周波数が上昇していく』様子が、右肩上がりの明るい帯として"
        "明確に可視化できた——スペクトログラムが時間分解能と周波数分解能を両立させる仕組みで"
        "あることを確認できた。メルフィルタバンクは低域では細かく、高域では粗く周波数を"
        "区切っており、人間の聴覚特性(低い音の違いに敏感)を反映した設計になっている。"
        f"フォルマント周波数だけが異なる「あ」らしき音と「い」らしき音のMFCCは"
        f"(絶対差の合計{diff:.2f})明確に異なる値を取っており、MFCCが声道の共鳴特性"
        "(スペクトル包絡=音色・母音の違い)を少数の係数に圧縮して捉えられていることを確認できた。"
        "これが音声認識において、生の波形やスペクトログラムそのものではなくMFCCが"
        "特徴量として長年使われてきた理由にあたる。"
    )


if __name__ == "__main__":
    main()
