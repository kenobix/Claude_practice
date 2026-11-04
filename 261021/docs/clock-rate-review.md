# 保存済みbracketの経過時間componentを照合する

基点`61643ac`。[資格計測設計v1](clock-qualification-plan.md)のうち、
**30分の1 phaseに記録したQPC対MONOTONIC/RAWの進行率だけ**を実装した。
OS・時計・ネットワーク・DB・registryへ接続せず、指定された入力JSONだけを読む。
`.env`や実行profileを読む処理もない。

入口は[`review_clock_rate_phase.py`](../scripts/review_clock_rate_phase.py)、
純粋な計算部品は[`clock_rate_review.py`](../src/bike_mlops/clock_rate_review.py)。
結果が`rate_component_status=passed`でも、`clock_qualification_status`は常に`not_evaluated`。
sampleの真正性/採取protocol、QPCの校正、外部UTC、future拒否、boot/process/設定、
通常/負荷の両phaseと休止再開、保守許可と人のreviewは、この部品では判定しない。
切り出した良好なphaseだけで時計資格を通す用途には使わない。

## 入力と判定

JSON objectの項目は次の5つだけ。未知の項目、重複key、NaN/Infinityは拒否する。
timestampsは整数。bool/小数/負数を時刻として受け入れない。

| 項目 | 値 |
|---|---|
| `schema_version` | 整数1 |
| `kind` | `clock-rate-phase-v1` |
| `phase` | `idle`または`fixture-load` |
| `qpc_frequency_hz` | 正の整数。記録元が読んだQPC周波数 |
| `samples` | 2〜10,000件、採取順。QPC tickは同じ参照clockの値 |

sampleは`index`、`qpc_tick`と、`monotonic_before_ns`/`monotonic_after_ns`/
`raw_before_ns`/`raw_after_ns`の6項目だけ。
indexは0開始で連続。`before`はWindows sample要求前、`after`はその応答後のWSL値。
QPC sampleがその間に行われたという保証は、将来の採取protocol側で必要になる。
今回のreaderへ数値を渡すだけでは、その保証やQPC秒の正しさを確立できない。

最初のsampleを起点にQPCで60/120/…/1800秒へ最初に到達したsampleを選ぶ。
30個の非重複区間とphase全体で、MONOTONIC/RAWそれぞれをFractionによる厳密な有理数で検算する。
丸めた平均や、後から選んだ良好なsampleへ差し替えない。
最後のsampleは最初の1800秒到達点であることを要求する。余分なsampleや途中終了は`incomplete`。
隣接QPC gapが2秒を超える場合も`incomplete`とする。
二つの端点だけで1800秒をまたいでも、中間の観測が欠けるため合格しない。

bracket下限/上限の全体が`[0.999, 1.001]`内なら、その区間は`passed`。
完全に外れる場合は`failed`、部分的に重なる場合は`incomplete`。
QPCの非増加、bracket内の逆行、MONOTONIC/RAWの順序違反/観測の重なりは`failed`とする。
明確な違反と不足が共存したら、両方のissuesを保存して`failed`を優先する。

## CLIとファイル保護

新しい出力pathを指定して次を実行する。入力はまだ実採取していないので、以下は将来の使用例である。

```bash
.venv/bin/python scripts/review_clock_rate_phase.py \
  --input "$TASK_CLOCK_INPUT" --output "$TASK_CLOCK_REPORT"
```

終了値は0=経過時間component合格、1=明確な違反、2=不足または不正入力/IO失敗。
0でも時計資格全体の合格ではない。schema違反時はレポートを作らず、内容や絶対pathをstderrへ出さない。
schemaが有効で不足/違反を判定できる場合は、そのレポートを保存して非0で終了する。

入力は単一の読取descriptorから最大16MiBで読み、前後のdev/inode/size/mtime/ctimeとpathのstatを比較する。
Linux/WSL用のreaderであり、O_NOFOLLOW/O_NONBLOCKで開いて通常fileだけを受け入れる。
symlinkやFIFO/装置を拒否し、FIFOのwriter待ちへ入らない。
原本のbytes/SHAをreportに結合する。これは読取中に見える変化の検知で、悪意ある原本の真正性を保証しない。
出力は同じdirectoryへstagingし、fsync後に排他的linkで確定する。既存reportを上書きせず、
IO失敗時はstagingを削除する。入力や原本の台帳を更新しない。

## 残る受入

[専用テスト](../tests/test_clock_rate_review.py)は合成数値と一時fileだけを使う。
別processのCLI/入力hash/終了値、境界、欠測、10%のずれ、wide bracket、失敗優先順、
JSON拒否、読取中変更、link/fsync失敗cleanup、既存report保護を検証する。
これを実Capital運用性能や時計資格の証拠へ流用しない。

次は、全campaignの入力schemaとpolicy binding、外部UTC区間/校正/identity/採取真正性・coverageの判定を設計・実装する。
採取器、固定fixture負荷、外部時刻源readerも未実装。実採取・設定変更や休止再開を自動実行する部品は追加していない。
