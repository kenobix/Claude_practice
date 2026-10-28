# 需要予測モデルを継続運用するMLOps基盤の詳細計画

- 作成・資料確認日: 2026-10-14
- 状態（2026-10-28更新）: **実装・実行検証中**。UCI版3の配信と履歴評価まで実績があるが、Phase 3〜5の復旧実験・再現性・新データ移行は未完了。作成時は企画段階だった。現在の証拠と残作業は[第11節](#11-2026-10-28差分監査後の実行計画)を参照。工数は計画上の概算で、実績時間ではない。
- 位置づけ: ソフトウェア開発力とAI・機械学習エンジニアの双方を伸ばす第一候補。
- 総合比較: [次に作るソフトウェアの計画](next_project_plan.md)

## 1. 目的と、元の企画から変える点

自転車レンタルの次の1時間の需要を予測する軽量モデルを1つ選び、データ取得から学習、評価、登録、配信、監視、差し戻しまでを再現できるソフトウェアを作る。利用者は、予測APIを使う開発者と、モデル更新を判断する運用担当者を想定する。

このリポジトリでは[260804の作業ログ](../260804/work_log.md)にStage 14までの機械学習実験があり、[Stage 1のミニプロジェクト](../260804/stage01/04_titanic_project.py)では前処理・分類・評価を既に扱っている。その経験を土台に、今回は学習済みモデルを追跡可能な状態で使い続ける仕組みを作る。

「AIの経験が多いので新規性が低い」という元の判断は修正する。モデル研究と、データの版・配信中の版・評価の根拠・復旧手順を一致させる開発は異なる。調査した学習ログには、モデル登録から配信切り替え・継続監視までを通した実装記録を確認していない。

[260406のダッシュボード](../260406/dashboard/README.md)で得たAPI・DB・Dockerの経験も使える。新規の中心は、時刻を考慮したデータ契約、学習と配信の一致、品質ゲート、モデル更新の状態遷移、遅れて届く正解を使った評価である。

## 2. 題材と最小完成範囲

題材は[UCI Bike Sharing](https://archive.ics.uci.edu/dataset/275/bike+sharing+dataset)の時間別データとする。2011〜2012年の貸出台数を扱う公開データであり、現在の需要を予測するサービスではない。過去の時刻順にAPIへ入力する**運用の再現実験**として使用する。

主モデルはscikit-learnの`HistGradientBoostingRegressor`1種類、比較基準は「前日の同じ時刻の台数」とする。比較基準の欠測時は学習範囲の曜日・時刻別平均、さらに該当しなければ学習範囲の全体平均で補う。複数のモデル種類を競わせることより、同じモデルの版を評価して切り替えられることを優先する。

**最小完成版（Phase 0〜3）**は、固定データ・コード・依存関係から学習を再実行し、評価を通った版をMLflowへ登録し、APIで特定版を配信し、人が承認して更新・差し戻しできる状態とする。

**深掘り版（Phase 4〜5）**で、過去データの連続再生、遅延ラベル、入力分布の変化、性能監視、再学習候補の作成と運用記録まで完成させる。ユーザーが数か月以上取り組める意向に合わせ、深掘り版を推奨到達点とする。

後回しにするのは、GPU、LLM、Kubernetes、特徴量ストア製品、ストリーム基盤、複数モデルの同時配信、自動スケール、自動デプロイ、大規模な管理画面である。

## 3. 暫定構成と成果物

```text
UCI原本 → 検査・時刻付き特徴量 → 学習・評価 → MLflow登録
                       │                      │ 版を固定
過去データの再生 → FastAPI予測API ← 手動承認・配信設定
                       │                      │ 差し戻し
                予測・特徴量・モデル版ログ ← 配信履歴
                       │
                遅れて届く正解 → 監視レポート → 再学習候補
```

- Python、scikit-learn、FastAPI、MLflow、PostgreSQL、Docker Composeを暫定構成とする。実装開始時に互換性を確認し、依存関係とコンテナの版を固定する。
- PostgreSQLはMLflowのメタデータとアプリの運用記録を別DB・別権限で保存する。モデル等の成果物はローカルの永続ボリュームに置く。
- 前処理は学習・API共通のPythonパッケージとし、モデルを`Pipeline`に保存する。実装では別の補完器を置かず、欠損フラグとHistGradientBoostingRegressorのnative NaN処理を使う。将来学習する変換器を追加する場合、その`fit`は学習範囲のみで行う。[scikit-learnの注意事項](https://scikit-learn.org/stable/common_pitfalls.html)
- 配信はFastAPIで特定の登録版をロードする。MLflowの別名`candidate/champion`は整理に使い、実際に配信する版番号と成果物ハッシュを配信記録に固定する。[MLflow Model Registry](https://mlflow.org/docs/latest/ml/model-registry/workflow/)
- MLflow、DB、管理用操作を外部公開しない。MVPはローカルのCLIから承認・切り替えする。
- コード検査と小さな固定データの試験は既存のGitHub Actions等を使う。自作CI実行基盤の完成を本企画の前提にしない。
- 実装ディレクトリは着手日の配下に作り、`src/`、`configs/`、`scripts/`、`tests/`、`docs/`に分ける。大きな原本・モデル・DBはGitに入れず、取得手順とハッシュを記録する。

## 4. データ契約とリーク防止

| 対象 | 固定する契約 |
|---|---|
| 原本 | 取得URL、取得日、SHA-256、ファイル名、列・型・件数、利用条件 |
| 観測行 | データセット内の日付と時刻の組、非負整数の`cnt`、重複・欠時刻の検査結果 |
| 予測入力 | `request_id`、予測対象時間`target_hour`、情報の利用可能時点`as_of`、入力契約版 |
| 特徴量 | 対象時間の暦情報、24時間前・168時間前の台数、欠測フラグ、特徴量版 |
| 正解 | 対象時間の`cnt`、観測時間、利用可能時間`available_at`、データ版 |
| 予測出力 | 予測ID、非負の予測台数、配信版、特徴量版、入力データ版 |
| 配信記録 | 承認者、理由、評価結果、旧版・新版、状態、開始・終了日時 |

予測は、対象時間が始まる時点でその1時間の台数を見積もる。MVPは`as_of`が`target_hour`の開始時点と一致する入力だけを受理する。再生実験では、各時間の実績がその時間の終了から2時間後に利用可能になると仮定する。この2時間は原本に記載された実際の遅延ではなく、遅延ラベルを学ぶための設定である。

特徴量は`available_at <= as_of`の観測からのみ作る。前日・前週の値は時刻をキーに結合する。単純な行の`shift(24)`で欠けた時間を飛び越えない。該当値がなければ欠測として残し、学習済みの補完器で処理する。

`cnt`、`casual`、`registered`の予測対象時間の値、将来の実測天気、後から確定した情報は入力に含めない。暦情報は対象日時から計算し、実測天気を予報値と見なさない。特徴量生成時には参照した観測時間と利用可能時間も検査する。

原本の時刻表記には取得時にタイムゾーン・夏時間の説明を確認する。裏付けがなければUTCに変換せず「データセット内の日時」として再生し、重複する日時は隔離する。ネットワーク応答時間は実時間の単調時計で測定する。

分割案は、2011年を学習、2012年1〜3月を検証、4〜6月を固定の最終評価、7〜12月を運用再生とする。各境界では学習締切までに正解が到着済みの行だけを学習対象にする。件数と欠損をPhase 0で確認して確定し、分割マニフェストを保存する。最終評価を見て調整を繰り返さない。

暦・ラグの計算は時系列分割の前に実行しても、各行が当時利用可能だった観測だけを見る条件を保つ。補完・変換・モデルの学習は分割後の学習データだけで行う。モデル内部のランダムな早期終了用分割を避け、MVPは`early_stopping=False`で反復数を事前の時系列検証から決める。[モデルの公式仕様](https://scikit-learn.org/stable/modules/generated/sklearn.ensemble.HistGradientBoostingRegressor.html)

## 5. 守る不変条件と品質ゲート

1. どの予測も、モデル・データ・特徴量・コード・環境の版へ遡れる。
2. 同じ`request_id`と同じ入力の再送は同じ予測記録を返す。異なる入力のID再利用は拒否する。DBの一意制約と正規化した入力ハッシュで並行要求も処理し、先に確定した結果を返す。配信版が変わった後の再送でも元の予測・版を返す。
3. 配信中のモデルはプロセス起動時に固定し、別名の変更だけで黙って差し替わらない。
4. 新版のロード・入力契約・試験予測が成功するまで配信先を切り替えない。
5. 再学習は新しい候補を作る。監視アラートや学習完了からの自動デプロイは行わない。
6. 欠測や未到着ラベルを0として評価しない。利用可能な正解数・到着率・遅延を必ず表示する。
7. 学習器・原本・依存関係・seedを固定し、再実行の指標差が設定した許容範囲に入るか確認する。異なるハードウェアでのビット単位一致は約束しない。

主評価指標はMAE、補助指標はRMSE、曜日・時間帯別のMAEとする。ゼロ台の時間があるため、MAPEを主指標にしない。負のモデル出力を0へ制限するなら、同じ処理を評価時にも使う。

初回候補は、検証期間のMAEが前日同時刻基準を下回り、入力・再現性・配信試験を通ることを昇格の条件案とする。基準未達なら未達と記録し、管理された試験配信だけで基盤を検証する。「高精度なモデル」として公開しない。

更新候補は、同一の事前に定めた評価期間で現行版と比較する。全体MAE、主要時間帯、欠測、応答時間に許容悪化幅を設定し、評価後に都合よく基準を変えない。小標本の差を改善と断定せず、件数と時間帯ごとの差を添える。

## 6. 段階別の作業計画

### Phase 0: データと利用場面を確定する（16〜24時間）

- 原本を取得し、型、値域、重複、欠けた時間、ラベル列、タイムゾーンの記述を調べる。
- UCIの出典・CC BY 4.0表記を`DATA_SOURCES.md`へ記録し、加工内容も明示する。
- 分割案、2時間の到着遅延、特徴量の利用可能時点、ベースラインを固定する。
- 学習データとAPI入力の共通契約、エラーと配信状態の遷移図を作る。
- 成果物: データカード、分割マニフェスト、設計判断、データ検査結果。
- 完了条件: ある1件の予測について、当時どの値が使えるかを日時付きで説明できる。

### Phase 1: 再現可能な学習と登録（20〜32時間）

- 特徴量生成、ベースライン、補完器、主モデルを実装し、時系列で評価する。
- データハッシュ、Git commit、環境、seed、パラメーター、指標、成果物をMLflowに記録する。
- 最終評価データを参照せずに候補を選び、品質ゲート結果を成果物として保存する。
- 学習を再実行し、入力・特徴量の一致と予測・指標の許容差を確認する。
- 成果物: 学習CLI、学習済みPipeline、モデルカード、登録版とrunの対応表。
- 完了条件: 1つの登録版から、データ取得と学習を再現する手順まで遡れる。

### Phase 2: 版を固定した予測API（18〜28時間）

- `POST /predict`、`GET /health`、`GET /ready`、`GET /version`を用意する。
- `target_hour/as_of`に対応する利用可能な観測を取り出し、学習時と同じ特徴量処理を呼ぶ。
- API入力の型・対象時間、入力契約版を検査し、予測・モデル版・特徴量を記録する。
- リクエストの再送、未知の版、モデル不在、DB書き込み失敗を扱う。記録できない予測は成功として返さない。
- 成果物: OpenAPI仕様、APIコンテナ、配信版マニフェスト、APIとオフライン予測の比較結果。
- 完了条件: 保存された同一入力と版から、APIとオフライン処理で同じ予測を再現できる。

### Phase 3: 手動昇格と差し戻し（22〜34時間）

- `registered → evaluated → approved → deploying → active`と、失敗・差し戻しの遷移を実装する。
- 承認操作で評価runと特定版を指定し、候補コンテナを起動してreadinessと固定入力を確認する。
- 単一ホスト上の配信先を候補へ切り替え、旧コンテナを復旧用に保持する。無停止の保証は目標に含めない。
- 配信入口はローカルのリバースプロキシ1つに固定する。新旧コンテナを別ポートで起動し、設定の検証・置換・再読み込みで上流を変更する。`/version`で実際の配信版を確かめてから履歴をactiveにする。
- 配信操作IDと旧版・新版・処理段階をDBへ先に記録し、同時に1つだけ操作できるようにする。プロキシとDBを1トランザクションとは扱わず、再起動時に入口の版と操作記録を照合する。旧版なら再試行、新版なら確定、両者以外なら切り替えを止めて復旧手順へ進む。
- ロード失敗時は現行版を維持し、切り替え後の異常では旧版へ戻す。配信履歴の途中停止も再開・照合できるようにする。
- 成果物: 昇格CLI、承認記録、差し戻し手順、切り替え前後の応答と停止時間。
- 完了条件: 候補作成から旧版への復帰まで、実際の配信版と履歴が一致する。

**Phase 3でMVPとして成立する。** ここで起動・学習・更新・復旧の手順と短いデモをまとめ、完了した成果として残す。

### Phase 4: 過去データの再生と監視（26〜42時間）

- 2012年7月以降の観測とラベルを、設定した再生時計・到着遅延に従って供給する。
- APIのエラー率・p50/p95応答時間、入力欠測・スキーマ違反、予測と正解のMAEを別々に集計する。
- 分布の変化は訓練時に固定したビンと比較窓で測り、曜日・時間帯の構成変化も確認する。
- 集計窓の件数と到着済みラベル数を表示し、少数なら精度悪化の判定を保留する。
- 欠測増加、入力列の変更、ラベル到着停止、API停止を意図的に起こし、検知と復旧を記録する。
- 成果物: 再生CLI、監視レポート、障害注入シナリオ、復旧記録。
- 完了条件: 入力分布の変化と、ラベルを使って確認した誤差の悪化を区別できる。

### Phase 5: 再学習候補とポートフォリオ化（20〜32時間）

- 再生時計の時点で到着済みのデータだけを取り込み、再学習候補を登録する。
- 候補の学習締切後の検証窓で現行版と比較し、評価判断時刻までに正解が到着した対象から承認/見送りを理由付きで決める。検証窓を両モデルの学習範囲から除き、学習可能な締切・検証対象・評価判断時刻を候補作成前に固定する。後続の配信評価対象は判断時刻以降に置く。
- 再学習候補の独立評価には、まだ使っていない後続の再生期間を確保する。既に見た評価期間を使う比較はその旨を明記する。
- 監視の誤警報、見逃し、検知までの遅れ、復旧操作を報告書にまとめる。
- 成果物: 再学習CLI、候補の採否記録、再現手順、5分デモ、学習・運用上の制約。
- 完了条件: 改善候補を作るだけでなく、条件不足の候補を配信しない判断を証拠付きで説明できる。

## 7. 検証と障害実験

| 実験 | 期待する振る舞い・証拠 |
|---|---|
| 同じ入力・同じ登録版を再実行 | 特徴量と予測が設定した許容差に収まり、環境差も記録される |
| 予測対象時刻の正解を特徴量へ混入 | 列の許可リスト・利用可能時刻の検査で失敗する |
| 欠けた時間・重複日時を投入 | 時刻結合で欠測・異常を検知し、隣接行を誤って使わない |
| 同じリクエストを再送 | 予測履歴が重複せず、異なる入力の同じIDは拒否される |
| モデル成果物を破損・削除 | 新版をreadyにせず、現行版の配信を維持する |
| 配信先切り替えの途中にプロセス停止 | 起動後に実際の配信版と履歴を照合し、復旧操作が一意に決まる |
| ラベルを遅延・欠落・重複させる | MAEの母数と到着率が正しく、欠落を0誤差として扱わない |
| 入力分布だけを変える | 変化の通知と、正解到着後の誤差評価を別々に表示する |
| 明らかに品質の悪い候補を作る | ゲートで止まり、現行版が置き換わらない |
| DB・成果物をバックアップから復元 | 登録版とファイルの対応が復元でき、欠けた版を配信しない |

分布変化は誤差悪化の証明ではない。分布が変わっても精度が保たれる場合と、分布監視に現れない誤差悪化がある場合を区別する。ラベル到着前の通知は調査のきっかけとし、再学習・昇格の自動判定には使わない。[GoogleのML監視ガイド](https://developers.google.com/machine-learning/crash-course/production-ml-systems/monitoring)

API負荷試験は件数・同時数・ハードウェア・暖機条件を固定し、過去データの予測精度評価とは別に報告する。実験用に加工した分布は加工方法を記録し、実際の需要変化として説明しない。

## 8. 工数、縮小方法、数か月の進め方

| 到達点 | 学習・実装・検証・資料化を含む概算 | 週8〜10時間の場合の換算例 |
|---|---:|---:|
| Phase 0〜3のMVP | 76〜118時間 | 約8〜15週 |
| Phase 4〜5の追加 | 46〜74時間 | 約5〜10週 |
| 推奨到達点までの合計 | **122〜192時間** | **約13〜24週** |

毎週の作業時間は未確定であり、上表は締切ではない。Phase 0とPhase 3で実績時間・理解できた範囲を見て再見積もりする。データ契約と切り替え処理の試行錯誤が主な変動要因になる。

縮小する順は、管理画面、常時監視のUI、分布指標の種類、再学習スケジュールの自動化、モデルの追加である。時刻のリーク防止、版の記録、手動昇格、差し戻し、正解到着率の表示は残す。

Phase 3で一区切りにしても、ソフトウェアとして説明可能な成果になる。Phase 5まで終えた後は、新しいモデル種類よりも、別データで同じ運用設計が成立するかを次の課題にする。自作CI基盤との統合は独立して完成してから検討する。

## 9. デモ、企業へ示す証拠、公開と費用

5分のデモは「データから登録版へ遡る→予測API→候補の評価→手動昇格→不具合を再現→旧版へ戻す」の順にする。深掘り版では、入力分布の通知後も正解待ちの表示が続き、ラベル到着後に誤差が更新される場面を追加する。

見せる証拠は、モデルカード、データ契約、API仕様、再現した学習run、配信履歴、候補を見送った判断、障害実験表とする。予測精度の数字に加え、誰がどの根拠でどの版を使い、問題時に何へ戻せるかを説明できることが成果になる。

需要予測が実際の配置・売上を改善したとは主張しない。本計画の評価対象は公開過去データにおける予測と、制御した環境での運用動作である。業務導入の効果には、現実の利用者・意思決定・結果を使う別の評価が必要になる。

開発はCPUとローカルDocker Composeを基本とし、有料API・GPU・クラウド契約を必須にしない。電力・ディスク使用量は実測する。常時公開する場合は、API・MLflow・DB・保存領域の運用費とバックアップを含めて上限を決める。

GitHub Pagesには成果説明、録画、匿名化した実験結果を掲載できる。予測API本体の公開は[ホスティング方針](../260805/hosting_policy.md)に沿って別途判断し、長時間学習や永続DBを静的サイトへ載せようとしない。UCIデータの出典・CC BY 4.0表示は配布する資料・加工データにも残す。

## 10. 確認した一次資料

- [UCI Bike Sharing](https://archive.ics.uci.edu/dataset/275/bike+sharing+dataset): データ内容、原典、CC BY 4.0の表示。確認日: 2026-10-14。
- [scikit-learn Common pitfalls](https://scikit-learn.org/stable/common_pitfalls.html): 前処理の一貫性、データリーク、再現性。確認日: 2026-10-14。
- [HistGradientBoostingRegressor](https://scikit-learn.org/stable/modules/generated/sklearn.ensemble.HistGradientBoostingRegressor.html): 候補モデルと早期終了等の仕様。確認日: 2026-10-14。
- [MLflow Experiment Tracking](https://mlflow.org/docs/latest/ml/tracking/): 学習run、パラメーター、指標、成果物の記録。確認日: 2026-10-14。
- [MLflow Model Registry Workflows](https://mlflow.org/docs/latest/ml/model-registry/workflow/): 登録版、別名、タグ、配信への参照。確認日: 2026-10-14。
- [Google Production ML: Monitoring pipelines](https://developers.google.com/machine-learning/crash-course/production-ml-systems/monitoring): スキーマ、学習と配信の差、正解を使う品質監視。確認日: 2026-10-14。

## 11. 2026-10-28差分監査後の実行計画

### 11.1 現在の到達点と作業範囲

[差分監査](../261021/docs/reviews/20261028-review.md)は、版2のAPI確認を記録した`cb79db0`以降の`485286c`・`6b99746`を中心に、関連する既存処理まで照合した。集計証拠は[監査JSON](../261021/docs/reviews/20261028-evidence.json)にある。

- 確認済み: UCI版3のCaddy/Blue ready 200、Green版2 ready 200、昇格台帳とartifact SHA一致、Q4各2,168予測の特徴量/出典/誤差、直近週の到着済み164ラベルとPSI、既存13テスト、対象2コミットのCI成功。
- 新データ: 41 ZIPの取得時hash一致、29,177時間、学習17,538・validation4,343・後続最終7,294、分割重複0。7日166時間の別方式原本集計も一致。全原本を別実装で再集計したわけではない。
- 制限: 新候補はno-registerでモデルartifactを保存せず、最終評価時に再学習している。同一artifactを評価した証拠にはならない。最終評価の再実行防止は出力ファイル名に依存する。新データAPI/監視・rollback・中断復旧・バックアップ復元の完了証拠はない。
- 既存結果の扱い: Q4の数値は保存済み予測から照合済み。新データの数値は実行記録・report内算術と整合するが、選定時モデルとの同一性は未証明。元のreportを保存し、後から合格条件やartifact hashを作り足さない。

Phase 0〜2には実装と実行証拠がある。Phase 3の正常昇格は実行済みだが復旧実験が残る。Phase 4〜5は再生・監視・候補比較の一部が動いている段階で、完了宣言はまだ行わない。以下を今回確認された残作業の正規一覧とする。大きな管理画面や外部公開は必須項目に加えない。

### 11.2 優先順位と依存関係

| ID | 優先度 | 内容 | 着手前の条件 |
|---|---|---|---|
| R01 | P0 | 原本範囲・集計収支・manifest検査を厳格化 | 既存原本/CSV/reportの保全 |
| R02 | P0 | 評価protocol・分割・消費台帳を固定 | R01のデータ識別仕様 |
| R03 | P0 | 選定モデルartifactを保存し、同一modelだけを最終評価 | R02のprotocol識別仕様 |
| R04 | P0 | 新コードの回帰試験・環境/ソース再現性・CIを整備 | R01〜R03と並行して試験を追加 |
| R05 | P1 | 新データ契約と独立したAPI/監視プロファイル | R01〜R04 |
| R06 | P1 | 比較と昇格の事前合否基準を定義 | R05で対象・契約が確定 |
| R07 | P1 | 監視範囲・参照ビン・退化ケースを修正 | R02/R05の期間・データ識別 |
| R08 | P1 | rollback・中断復旧・障害注入・バックアップ復元 | 隔離Compose環境、R04の試験基盤 |
| R09 | P1 | 配信メタデータと旧版の配信可否を正規化 | R08の台帳検証と連動 |
| R10 | P2 | 採否記録・再現手順・5分デモ・成果説明 | P0/P1の証拠が揃うこと |

P0は新たな最終評価や候補の配信承認を進める前に解消する。P1も移行候補の昇格前に必要。既存UCI版3の停止や障害注入を前提にしない。

### 11.3 作業内容と完了条件

#### R01 原本と集計の入力契約

- [x] archive一覧の非空、重複、欠落月、対象期間をカバーする範囲、時刻境界を検査する。取得対象月と時間別target期間の対応・翌月bufferの理由をmanifestへ残す。
- [x] 原本がない時間帯を「乗車0件」にしない。連続・非空のarchiveとsource timestamp boundsが対象期間を覆う場合だけ0埋めする。月次公開snapshotの完全性は独立に証明できない限界も記載する。
- [x] 読込行数 = ID重複 + 一意行数、一意行数 = 対象範囲外 + DST除外 + 出力件数合計、という収支を同じ集計単位で記録する。
- [x] manifestのJSON object、必須項目、hash形式、処理版、時刻・target契約、archive実体のbyte数とSHAを検査する。型違反は入力エラーとして拒否する。
- [x] CSV・manifestを一時出力しmanifestをcommit markerとして保存する。lockを使い既存証拠の上書きを拒否し、stale lockとlive lockを区別する。
- 完了条件: 合成ZIP fixtureの空archive、欠落月、重複ID、矛盾ID、小数秒、月境界、DST、出力保全を試験。既存41 ZIPを新しい出力先へ全件再集約し、厳格manifest v2の検証・監査・profile別seedまで完了した。旧derived CSVと新CSVのSHA-256は一致し、旧成果物は上書きしていない。全44テスト、CI `40644880384`のlock install、`pip check`、compileallが成功。

#### R02 評価protocolと期間の消費台帳

- [x] target定義、データ版、学習cutoff、validation/最終期間、ラベル利用可能時刻、評価判断時刻、比較対象、特徴量版、候補recipe、判定規則、件数を版付きprotocolにまとめる。候補の学習前に保存する。
- [x] 学習締切と評価判断時刻を区別し、全validationラベル到着後にreserved periodが始まることを検査する。既存Q3/Q4・Capital期間の境界は歴史として残し、新protocolではdecision time以降を必須にした。
- [x] manifestに手書きで加えたplan欄へ依存せず、CSV/manifestからprotocolのsplit件数を生成する。
- [x] target contract/期間単位のOS file-lock付き台帳で管理し、protocol/model/data SHAとstarted/failed/completedを保存する。プロセス終了時にOSがlockを解放し、lockファイルが残っても復旧を妨げない。候補/incumbentを同一期間で比較するpaired runを一つの消費として扱う。
- [x] 出力ファイル名やdata hashを変えても同target contractの重複/部分重複期間を拒否する。失敗/中断は維持し、人・理由付きreview後のみ再試行できる。
- [x] UCI初期validation/holdout、Q3/Q4 2012、Capital 2025H1、新データ2025-07〜2026-04をlegacyとして記録する。欠けたartifact/protocol hashはunknownのまま。
- 完了条件: 合成データで判断時刻・入力hash変更・期間重複・同時開始・失敗/中断とreviewを検査し成功。実際の新protocol保存はlegacy期間が使えないため、未使用データ入手後に行う。

#### R03 選定候補と最終評価のartifact同一性

- [x] `--no-register`でも候補modelをレビュー済みskops型でbundleへ保存し、model SHA・feature/target契約・学習行hash・環境・ソースsnapshot・protocolを結び付ける。bundle IDはmodel/validation/source/protocolのhashと品質gate・指標を含み、最終評価時にrow-level evidenceとモデル予測を再照合する。
- [x] validationの行別予測と集計を保存し、品質gate結果をcandidate SHAへ固定する。モデル不保存の既存reportは暫定結果のまま保持する。
- [x] 最終評価scriptは選定済みartifactをhash照合してloadし、候補再fitをしない。候補・incumbent・入力・protocol・期間・特徴量を検査する。
- [x] 最終評価の行別予測、対象時刻、正解利用可能時点、件数、指標、参照hashを新規出力する。
- 完了条件: 合成model hash round-tripと破損hash拒否、同一開発データ2回fit、最終scriptにcandidate.fitがないことを試験した。実際の候補bundle/最終評価は既評価窓しかなく未実行。既存最終期間を再採点してこの条件を後付け達成しない。

#### R04 回帰試験・環境・実行コードの再現

- [x] 今回の既存13テスト、src/tests/scripts構文検査、pip check、対象コミットCI結果を確認。
- [x] candidate/protocol/manifest/ZIP集計/hash拒否の合成回帰試験を追加。小さな人工データで2回学習した予測は完全一致した。
- [x] CIでscriptsもcompileし、新テストとpip checkを実行する設定にした。CI結果はpush後に確認する。
- [x] hostとコンテナは同じrequirements.lockを使い、Python 3.12.11とDocker base tagを設定。候補reportには実際のhost Python/依存版を記録する。lockは現venvの99 packageと一致。
- [x] candidate bundleにsource snapshotを保存する。
- [x] 同じ合成開発データで2回学習し、予測完全一致を試験。final期間は使っていない。
- [x] timezone供給元と版を固定した。依存定義とlockは`tzdata==2026.5`。集計・manifest検査・APIはOSの`zoneinfo` search pathを使わず、Python package内の対象zone TZif bytesから`ZoneInfo.from_file`を作る。manifest schema 3、training report、API `/ready`・`/version`にprovider/package版/zone名/bytes/hashを記録。春の欠落時刻・秋の重複時刻のテスト、schema 2から新protocolを作れない回帰テストを追加。旧manifest schema 2は監査・seedの後方互換用で、新protocol/candidateでは拒否する。
- 完了確認: 全41 ZIPを既存出力を上書きせず新pathへ再集計し、schema 3 manifestのTZif identity、全archive hash・行収支、CSV hashを検証した。新CSVとschema 2 CSVのhash一致は時刻処理のbyte同一性を示すが、同期間・同原本なので新しい評価データとは扱わない。既存のsystem DB確認（`2026c`）とPython package identityを別々に記録し、同一版だとは推定しない。
- 検証済みの範囲: 以前の44テスト、compileall、pip check、CI上のclean lock installに加え、今回のtimezone変更後は48テスト、compileall、pip check、Docker image build、host/container間のpackage版・TZif SHA・春DST grid一致、全41 ZIPのschema 3再集計・独立manifest検証が成功した。Starlette/httpx非推奨warningはwork_logへ記録した。commit `13ea918`の契約テストCI `40655472121`とPages build/deploy `40655471660`はいずれもsuccess。

#### R05 新しいtarget定義の移行プロファイル

- [x] UCI `cnt`とCapital Bikeshare `started_at`件数の違い、除外乗車、集計母集団、America/New_York壁時計という解釈、DST除外、月次公開と仮想+3時間遅延の違いを[`docs/data-profiles.md`](../261021/docs/data-profiles.md)に固定する。
- [x] target契約・データプロファイルIDを候補report/model card、MLflow run/model tags、DB metadata/prediction/deployment rows、API要求・応答、runtime state/probeへ結び付けた。DBはprofileに固定し、既存の行を含むUCI DBのCapital再利用を拒否する。別project/volume/host portの`compose.capital.yaml`を追加し、API/modelロード/seed/CLIでは別profileとtargetの混在を拒否する。
- [x] 配信CLIのseedはprofile設定のCSVを使い、Capitalでは厳格なmanifest schema 2・target契約を確認する。R04では過去seed/auditとの互換を維持しつつ、新しい評価ではtimezone identity付きschema 3だけを受け入れる。2012年以前固定のprobeをデータ範囲に対応させた。UCIの既存client/idempotency hash/legacy modelは後方互換を保つ。
- [x] 現行特徴量が時刻から既知の暦と利用可能時刻を過ぎた同profileのラグだけであること、および休日・天候の追加時点条件をデータprofile docsに明記した。未確認の将来情報は追加していない。
- [x] 全41原本ZIPから新規pathに厳格manifest schema 2を作成し、各ZIP hash、集計収支、profile/target、CSV hashを再検査した。既存CSVとbyte hashが一致し、Capital用SQLiteへ29,177時間をseedした。別のschema 3出力もR04で作ったが、同じ期間・原本の再検査なので未使用評価データではない。
- [ ] Capital監視はUCIの2011参照を流用しない。Capitalに凍結済み同profile training-reference model/window/PSI binがない間はCLIが監視を停止する。初回Capital modelと参照期間をR06/R07で確立後、同profile referenceで監視を実装・試験する。
- 完了条件は未達。厳格manifest v2、監査、profile別SQLite seed、Compose静的設定検査、合成API/DB/profile試験は成功した。一方、独立PostgreSQL/MLflow/Composeの実起動、同target modelとmonitor reference、registered model loading、offline/API parity、end-to-end deploymentの証拠はまだない。UCI稼働版へは変更を加えていない。

#### R06 比較gateと昇格条件の完成

- [x] 2026-10-28にユーザーから判断を委任され、初回Capitalの基準を決定した。validationと一度限りの後続評価の両方で同target baselineに対し`candidate MAE < baseline MAE`かつ`candidate RMSE <= baseline RMSE`。weekday/weekend・通勤時間帯は件数/MAE/差を記録する診断値とする。個別の自動gateは置かない。根拠・境界条件は[`capital-bootstrap-gate.md`](../261021/docs/capital-bootstrap-gate.md)に固定した。
- [x] 運用目標の仕様を決定した。暖機50要求後、同時数1・新規request IDで1,000要求、再試行0、timeout10秒、エラー0件、p95 250ms以下。hardware/資源条件と全計測値を保存する。これは学習用PC上の目標であり、業務SLOではない。
- [x] 現在のUCI `passed`は従来のvalidation MAE比較、新Capitalの精度gateは上記MAE/RMSE条件を指すと文書化した。配信許可には別途artifact/入力/運用/復旧の証拠と理由付き手動承認を要求する。旧結果・タグへ新基準を遡及適用しない。
- [x] 初回bootstrap用のprotocol/candidate/final-evaluation経路を合成fixtureで実装・試験した。incumbentなしで同target historical baselineだけを比較し、UCI/仮modelを流用しない。schema 2、policy本体/ID/SHA、nullable ledger、candidate fit、row evidence再計算、artifact hash確認、final CLI全体、final MAE/RMSE gateを確認。registry model loaderはmockで非呼び出しを検証。実データは未取得・未採点。
- [ ] report/model card/MLflow tagsとdeploy側で、validation選択gate・後続精度gate・運用gate・配信許可を区別し、条件不足や不合格を昇格前に拒否する。手動承認で不合格を上書きできる経路を作らない。**進捗:** candidate report/model card/final reportは状態を分離。runtime init/promote/Capital API loaderは4 gate status、policy ID/SHA、eligible permissionを要求し、不足時は診断/rollbackでも拒否する。固定条件のoperational benchmarkも実装し、実行ごとのstatus/latency/errorを記録する。ただしgate未完了候補を測る通常runtimeから隔離したstaging API、blocked candidate登録、MLflowへの全gate/evidence tag記録は未実装。
- [ ] 原本範囲と月境界buffer規則を先に確定し、未使用2026年6〜9月のデータを取得・監査する。前回は10月archiveが404だったため、10月bufferを必要とする契約なら公開を待つ。結果を見て評価期間を短縮しない。
- [ ] 新pathのmanifest schema 3、日時grid/DST/欠測理由・出典、分割件数、特徴量版、固定recipe、比較対象、gateとpolicy hashを実験protocolへ保存し、消費台帳の重複を検査する。候補fit前にvalidation開始を記録する。
- [ ] bootstrap validationを一度実行してtarget-specific model card・artifact・行別予測・判断記録を固定する。合格時だけ同一artifactで後続評価を一度実行し、再fitしない。使用済みwindowを最終評価や調整後候補へ再利用しない。
- [ ] 固定条件のAPI測定コードを実装した。`scripts/benchmark_capital_runtime.py`は学習期間内の168時間を繰り返し、50暖機+1,000逐次要求、HTTP 200・schema/識別/有限非負予測、再試行0、timeout10秒、nearest-rank p95を確認し全リクエストを記録する。合成HTTP試験は成功。**実際の計測は未実施**で、通常runtimeはeligible candidateしかロードしないため、隔離staging経路とcandidate登録を先に作る。精度採点は行わず、hardware/container資源条件を保存する。
- 完了条件: 同じreportとprotocolから採用/見送りが一意に説明でき、条件不足・悪い候補は昇格を拒否する。最終採用は理由付きの手動承認を記録する。現時点でbaseline-only精度gateと固定条件benchmark、fail-closed deployment checkを合成テストした。実benchmarkを行う隔離staging経路とMLflow registration/tag書込み、実データprotocol freeze/精度評価・手動承認は未完了。新評価データを取得する前にstagingとtag/system testsを完了する。追加のユーザー判断待ちはない。

#### R07 監視の参照・期間・退化ケース

- [ ] replay manifestの期間とmonitor入力を一致させる。明示指定または自動継承にし、Q4に既定Q3空窓を加えない。既存の空窓は出自を注記し、履歴を無断削除しない。
- [ ] 学習/データprofileに対応する参照期間とPSIビンを成果物として保存する。季節対応の比較は別の診断指標にし、過去の警告値を事後に置き換えない。
- [ ] 定数参照で大きな変化がPSI=0となる問題を修正する。参照なし、件数不足、ラベル待ち、誤差基準未設定、missing増加を区別する。
- [ ] 合成入力・遅延/欠落/重複ラベルで、入力分布警告と誤差悪化、誤警報/見逃し、検知までの遅れを別々に検証する。
- 完了条件: 同じprotocolと入力から窓・母数・警告状態を再現でき、未設定を「異常なし」と表示しない。

#### R08 隔離環境での配信・障害・復旧実験

- [ ] PostgreSQLで同時request ID再送、異入力の409、版切替後の元予測返却、保存失敗時503を実証する。
- [ ] 悪いgate、欠落/破損artifact、候補ready失敗、API停止、入力列変更、ラベル停止を注入し、拒否/検知/復旧と件数を記録する。
- [ ] 旧版への手動rollbackと、proxy切替前/後・台帳確定前の停止後`deploy recover`を試験する。DB・Caddy・runtimeの版が一致することを確認する。
- [ ] DBとMLflow artifactを対応するsnapshotとしてbackupし、別の一時volume/Compose projectへ復元する。登録版・model SHA・予測履歴の対応を確認する。
- 完了条件: 各障害について期待状態、実際のHTTP/台帳/ログ、復旧操作、復旧後の版が残る。現行スタックの停止・破損を試験の前提にしない。

#### R09 運用メタデータと進行表示

- [ ] active版の承認者・理由・操作IDを最新の配信台帳と同期する。recover時も同じ規則を適用する。
- [ ] 読み込み不可の版1は過去指標を保存したまま配信不可と明示し、rollbackの候補から除外する。READYやMAE passedだけで利用可能と説明しない。
- [ ] 長い候補buildやmodel取得で、段階、経過時間、参照ログ、timeout後の扱いを表示する。以前の「停止して見えた」buildを判断できる情報を残す。
- 完了条件: 入口・runtime・配信台帳の現在版/承認情報が一致し、利用できない旧版への操作が切替前に拒否される。

#### R10 採否・デモ・成果の説明

- [ ] 各Rタスクの証拠を一覧化し、成功だけでなく見送り/未達の理由を記録する。
- [ ] データ出典からモデル・予測・承認・rollbackへ遡る5分デモと、遅延ラベルを待つ監視画面またはCLI記録を用意する。
- [ ] 予測誤差、API実測、入力分布、復旧時間、資源消費を区別して掲載し、実業務の改善や現在需要の精度を主張しない。
- 完了条件: 別の人が再現手順から開発用検証と隔離復旧実験を追え、根拠不足の候補を昇格しない判断まで説明できる。公開する場合は出典・利用条件とホスティング方針に従う。

### 11.4 R06判断後の残作業の実行順序

今回の差分チェックは`9131b1f`以降のみ。結果は[差分レビュー](../261021/docs/reviews/20261028-r06-delta-review.md)に記録した。3点の判断待ちは解消したが、実装・実測の完了には振り替えない。

| 順番 | 対象 | 次に実施する作業・完了の証拠 |
|---|---|---|
| 1 | R04/P0 | 完了。Python `tzdata==2026.5`を集計・manifest検査・APIで明示的に使い、schema 3/report/APIのidentity記録、DST・後方互換境界テスト、全41 ZIPの再集計とhash確認を行った。旧期間の再集計物は新規評価には使わない。 |
| 2 | R06/P1 | **部分完了:** baseline-only protocol/candidate/final scoring、MAE/RMSE gate、fixed operational benchmarkを合成データ/HTTP fixtureで試験した。実benchmarkは未実施。次に通常runtime/promoteから独立したCapital staging APIとblocked candidate登録経路を実装・Compose system testする。これらが終わるまでは新しい評価データを取得しない。 |
| 3 | R06/P1 | candidate ID/artifact SHAへbenchmarkを結び付ける。実測後、validation/final/operational report hash・policy ID/SHAと理由付き手動承認をMLflow version tagsへ書く。`eligible`は全gateがpassedの場合のみ最後に設定し、failed/欠損の上書きを拒否する。 |
| 4 | R06/P1 | 月境界bufferを含む原本範囲を決定→取得/manifest schema 3監査→未使用split/件数/recipe/gateをprotocolへ凍結する。training cutoffは2026-06-01 00:00、未到着の直前2時間を除外。 |
| 5 | R06/P1 | validationを一度実施→artifact/evidence固定→合格時だけ同一artifactで後続評価を一度実施する。 |
| 6 | R05/R06/P1 | Capital隔離PostgreSQL/MLflowとmodel loading、offline/API parity、promotion rejection/acceptanceをsystem testする。candidate/DBが揃った後に固定benchmarkを隔離staging APIで実測する。 |
| 7 | R07/P1 | 同profile training reference/PSI bins、monitor期間継承、定数分布/件数不足/ラベル待ち/欠測増加の状態を実装・試験する。 |
| 8 | R08/R09/P1 | 隔離環境で並行要求、拒否/障害注入、rollback/recover、backup/restoreを検証し、配信可否・承認metadata・進行表示を整合させる。 |
| 9 | R10/P2 | 採否と未達理由、再現手順、5分デモと成果の証拠をまとめる。 |
