"""音声処理の基礎: PCM(A-D変換)とFFT(高速フーリエ変換)をスクラッチで確認する

これまでのStage 0〜13は画像・言語・強化学習を扱ってきたが、音声は
「連続的な空気の振動(アナログ信号)」という全く異なるモダリティを持つ。
音声処理の最初の一歩は、マイクが拾ったアナログの音圧信号を、コンピュータが
扱えるデジタルの数値データに変換すること(A-D変換)。代表的な符号化方式PCM
(パルス符号変調)は、
  1) 標本化(サンプリング): 一定の時間間隔(サンプリング周波数)で音の振幅を測る
  2) 量子化: 測った振幅を有限のビット数(量子化ビット数)で丸めて数値化する
の2段階からなる。このスクリプトでは、
  (a) サンプリング周波数を変えると、元の波形をどこまで正確に再現できるか
      (低すぎるとエイリアシング=偽の低い周波数として記録されてしまう)
  (b) 量子化ビット数を変えると、どの程度のノイズ(量子化誤差)が乗るか
  (c) 複数の周波数が混ざった信号から、FFTで元の周波数成分を復元できるか
をそれぞれ確認する。
"""
import numpy as np
import matplotlib.pyplot as plt

import _mpl_ja  # noqa: F401

rng = np.random.RandomState(42)


def make_tone(freqs_amps, duration, sample_rate):
    """複数の正弦波(周波数, 振幅)を足し合わせた信号を作る(高サンプリングレートの「アナログ相当」)"""
    t = np.arange(0, duration, 1 / sample_rate)
    signal = np.zeros_like(t)
    for freq, amp in freqs_amps:
        signal += amp * np.sin(2 * np.pi * freq * t)
    return t, signal


def quantize(signal, n_bits):
    """振幅を[-1,1]と仮定し、2^n_bits段階に丸める(量子化)"""
    levels = 2 ** n_bits
    step = 2.0 / levels
    return np.round(signal / step) * step


def main() -> None:
    print("=== 1. サンプリング周波数とエイリアシング(折り返し雑音) ===")
    true_freq = 20  # 元の音の周波数(Hz)。可視化しやすいよう低めの値にしている
    duration = 0.2
    high_sr = 4000  # 「アナログ相当」の高精細な基準波形
    t_ref, sig_ref = make_tone([(true_freq, 1.0)], duration, high_sr)

    sample_rates = {"十分高いSR(200Hz)": 200, "境界付近のSR(45Hz)": 45, "低すぎるSR(24Hz)": 24}
    print(f"元の信号の周波数={true_freq}Hz(ナイキスト周波数の2倍={true_freq*2}Hzが最低限必要なSR)")
    for name, sr in sample_rates.items():
        t_s, sig_s = make_tone([(true_freq, 1.0)], duration, sr)
        print(f"  {name}: サンプル点数={len(t_s)}")

    print("\n=== 2. 量子化ビット数と量子化誤差 ===")
    t_q, sig_q = make_tone([(true_freq, 1.0)], duration, high_sr)
    bit_depths = [8, 4, 2]
    quant_errors = {}
    for bits in bit_depths:
        quantized = quantize(sig_q, bits)
        mse = np.mean((sig_q - quantized) ** 2)
        quant_errors[bits] = (quantized, mse)
        print(f"  量子化ビット数={bits}bit({2**bits}段階): 量子化誤差(MSE)={mse:.5f}")

    print("\n=== 3. FFTによる周波数成分の復元 ===")
    freqs_amps = [(50, 1.0), (120, 0.6), (300, 0.3)]  # 3つの正弦波を混ぜた信号
    sr_fft = 2000
    t_fft, sig_fft = make_tone(freqs_amps, 0.5, sr_fft)
    sig_fft_noisy = sig_fft + rng.normal(0, 0.1, size=sig_fft.shape)  # マイクのノイズ相当

    fft_vals = np.fft.rfft(sig_fft_noisy)
    fft_freqs = np.fft.rfftfreq(len(sig_fft_noisy), d=1 / sr_fft)
    magnitude = np.abs(fft_vals) / len(sig_fft_noisy)

    peak_indices = np.argsort(magnitude)[::-1][:3]
    detected_freqs = sorted(fft_freqs[peak_indices])
    true_freqs = sorted(f for f, _ in freqs_amps)
    print(f"混ぜ合わせた真の周波数: {true_freqs}Hz")
    print(f"FFTで検出した上位3ピークの周波数: {[round(f) for f in detected_freqs]}Hz")

    print("\n=== 4. 可視化 ===")
    fig, axes = plt.subplots(2, 2, figsize=(13, 9))

    axes[0, 0].plot(t_ref, sig_ref, color="lightgray", label="基準波形(高SR)")
    for name, sr in sample_rates.items():
        t_s, sig_s = make_tone([(true_freq, 1.0)], duration, sr)
        axes[0, 0].plot(t_s, sig_s, marker="o", markersize=3, label=f"{name}")
    axes[0, 0].set_xlabel("時間(秒)")
    axes[0, 0].set_title(f"サンプリング周波数とエイリアシング(元信号={true_freq}Hz)")
    axes[0, 0].legend(fontsize=7)

    axes[0, 1].plot(t_q[:150], sig_q[:150], color="lightgray", label="元の波形(連続値)")
    for bits in bit_depths:
        quantized, mse = quant_errors[bits]
        axes[0, 1].step(t_q[:150], quantized[:150], where="mid", label=f"{bits}bit量子化(MSE={mse:.4f})")
    axes[0, 1].set_xlabel("時間(秒)")
    axes[0, 1].set_title("量子化ビット数と量子化誤差")
    axes[0, 1].legend(fontsize=7)

    axes[1, 0].plot(t_fft[:400], sig_fft_noisy[:400])
    axes[1, 0].set_xlabel("時間(秒)")
    axes[1, 0].set_title("3つの正弦波+ノイズを混ぜた信号(時間領域)")

    axes[1, 1].plot(fft_freqs, magnitude)
    for f in true_freqs:
        axes[1, 1].axvline(f, color="red", linestyle="--", alpha=0.5)
    axes[1, 1].set_xlim(0, 400)
    axes[1, 1].set_xlabel("周波数(Hz)")
    axes[1, 1].set_ylabel("振幅")
    axes[1, 1].set_title("FFTによる周波数領域への変換\n(赤破線=真の周波数)")

    fig.tight_layout()
    out_path = "fft_pcm_basics.png"
    fig.savefig(out_path, dpi=110)
    print(f"\n図を保存: {__file__.rsplit('/', 1)[0]}/{out_path}")

    freq_match = all(abs(d - t) < 5 for d, t in zip(detected_freqs, true_freqs))
    print(
        f"\nサンプリング周波数を元信号(20Hz)に対して十分高く(200Hz)取った場合は元の波形形状を"
        "忠実に再現できる一方、ナイキスト周波数(元信号の2倍=40Hz)を下回る24Hzでは、"
        "サンプル点を結んだ波形が本来より低い周波数の波のように見えてしまうエイリアシングが"
        "実際に確認できた。量子化についても、ビット数を8→4→2bitと減らすほど"
        f"量子化誤差(MSE)が{quant_errors[8][1]:.5f}→{quant_errors[4][1]:.5f}→{quant_errors[2][1]:.5f}と"
        "単調に増加し、階段状のノイズが元の滑らかな波形からの逸脱として視覚的にも確認できた。"
        f"FFTについては、ノイズを加えた3周波数混合信号からでも、上位3ピークの周波数"
        f"({[round(f) for f in detected_freqs]}Hz)が真の周波数({true_freqs}Hz)と"
        f"{'ほぼ一致し' if freq_match else '大きくは一致せず'}、時間領域では目視での分離が"
        "困難な混合信号でも、周波数領域に変換すると個々の成分が明確なピークとして"
        "分離できることを確認できた。この『周波数ごとの強さ』を扱うという発想が、"
        "次のスクリプトで実装するスペクトログラム・MFCCの土台になる。"
    )


if __name__ == "__main__":
    main()
