# 製品完成に向けた開始条件（2026-11-14改訂）

ユーザーは時計研究の保留と、作業別の開始条件への変更を承認した。
この文書は、旧手順にある「全時計資格が完了するまで実fitも禁止」を置き換える。
固定済みのデータ・protocol・recipe・精度gateは変更しない。
時計原因・独立UTC/elapsed源選定・精密校正・全campaignは再開指示まで保留する。
過去の逆行、T03停止、tick11000、未確定の同期責任を解決済みとせず、資格statusも変更しない。

## 作業ごとの開始条件と停止条件

| 作業 | 開始前に必要な確認 | 停止条件と主張できる範囲 |
|---|---|---|
| T10直前照合 | 47原本、CSV/manifest/protocolの固定SHA・schema・TZif、11 source/config/lockのbytes、Python 3.12/Linux x86_64・全lock版、legacy/現在の消費台帳、別実行の証跡を照合 | 不一致・既消費・別実行との競合・不明な出力があればfit前に停止。hashは真正性/全checkout未消費を証明しない。全原本再集計は不要 |
| T11 validation | T10成功、隔離したsource/依存/出力、単一の消費台帳、事前の実行計画保存。台帳startがfitより先 | 一度だけ固定recipeでfit。MAE厳密改善かつRMSE以下。不合格/中断/失敗は保存して停止。同じ窓の調整・自動retry・台帳の再作成禁止。PCの精密時計資格は不要。結果は履歴データ上の精度 |
| T11 final | 合格したvalidationの同じcandidate ID/artifact SHAと行別証拠を再計算、選択後の固定final窓、消費台帳の一致 | 再fitなし。一度だけ評価。不合格なら登録・後続採否を停止。label到着+3h、選択時点、DST、境界除外を維持。blindな将来試験とは呼ばない |
| T02/T12の監視・復旧 | 合格candidate/同じartifact、専用DB/registry/volume/port、training-only監視reference、現在の配信と分離、原本とmetadataの対応 | 欠落/不一致は証拠不足。合成試験を実producerへ転用しない。実データを使った隔離試験の証拠であって、現在の本番配信実績ではない |
| T12応答時間/timeout | 実行先identity、通常の同期状態の読取、短区間でUTCの順序と経過時計の正方向、strict future拒否/timeout/中断の既存回帰、同一candidate、凍結した測定plan | 新しい時計異常・負のelapsed・承認時刻矛盾・不完全要求/監査で停止。異常試行を合格まで繰り返さない。暖機50除外・逐次1000・error0・nearest-rank p95≤250ms。当該環境の未精密校正の実測のみ、本番SLOや精密秒の保証ではない |
| T13b採否 | 精度・監視・復旧・metadata・運用の全実producer証拠、同じcandidate/artifact/report SHA、理由付きreviewと別承認 | futureは従来どおり厳密拒否。clamp/許容幅/成功までretry禁止。欠けた条件はblocked/not_evaluated。今回の許可は現registryのeligible化や現配信変更を含まない |

履歴fitはPCの現在UTCからtrain/validation/finalの行を選ばない。固定したdataset-local時刻とラベル到着仮定で選ぶ。
そのため独立UTC校正を開始条件にしない。一方、実行の開始/終了UTCは未資格の観測値と明記し、
逆行や消費台帳の順序矛盾が判明した試行は保存してreview待ちにする。これを時計研究再開の指示とは解釈しない。
運用時計の短区間チェックは通常の実行前確認で、保留したdaemon/PHC/外部UTC調査を再開しない。

## 実行先と分離

優先先は[記録済みデスクトップ](../../261109/README.md)。記録は現在の接続・正常性の証明ではない。
接続先が提示されていなければIPやSSH鍵を探索せず、既知のlocal path/hostとの一致だけを確認する。
デスクトップに接続できない間も、履歴fitは現在環境の専用directory・専用venv・専用出力へ隔離して進められる。
その場合は旧hostであることを明記し、同環境の時計依存の運用測定は別途上記の開始条件を確認する。
同じネットワークで開始してよい。別PC/別ネットワークだけで時計の正常性や独立性を認定しない。

既存`.env`、実DB、実registry、配信状態、OS/service/task/log設定には触れない。
元sourceをcommitから別directoryへ展開し、既存venvをコピーする場合も元venvへinstallしない。
コピー先で全lock版/pip checkとsource SHAを照合する。原本は読取だけにし、既存protocolを再発行しない。
消費台帳を複数に分岐させない。隔離実行の台帳は開始前に既存local台帳とlegacyを照合し、
正式な保存先として計画に宣言し、終了後に元checkoutから読取参照できるpath/hashを残す。
未使用を装うために台帳を空にして試し直すことは禁止する。

## デスクトップ側で必要な最小操作

このチャットが旧hostを実行している場合、デスクトップのVS Code Remote WSLでrepositoryを開き、
`hostname`、`pwd`、`git rev-parse HEAD`の結果と、この作業の実際の保存先を伝える。
SSH導入・ポート開放・OS変更は不要。実fitの前に最新の消費台帳と候補証拠を移し、
この文書と[固定snapshot](t10-snapshot-review.md)に従って読取照合する。既消費なら再fitしない。
