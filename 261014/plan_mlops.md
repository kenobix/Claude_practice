# 需要予測モデルを継続運用するMLOps基盤の詳細計画

- 作成・資料確認日: 2026-10-14
- 状態（2026-10-30更新）: **実装・実行検証中**。UCI版3の配信と履歴評価、人工fixtureのsystem/DB CIに実績がある。最新の[差分監査](../261021/docs/reviews/20261030-checkpoint-review.md)で配信承認の証拠結合と監視・復旧条件の不足を確認したため、実Capital配信前のP0として修正する。Phase 3〜5の実候補移行・障害復旧・新データ評価は未完了。現在の優先度・難易度・Sol/Luna担当推奨は[11.6](#remaining-work-20261030)を正規の一覧とする。工数は計画上の概算で、実績時間ではない。
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
- 完了条件は未達。厳格manifest v2、監査、profile別SQLite seedに加え、使い捨てComposeのPostgreSQL/MLflow/APIで合成候補の登録・ロード・offline/API parityとDB分離を確認した。実データの同target modelとmonitor reference、実候補のend-to-end deploymentは未完了。UCI稼働版へは変更を加えていない。

#### R06 比較gateと昇格条件の完成

- [x] 2026-10-28にユーザーから判断を委任され、初回Capitalの基準を決定した。validationと一度限りの後続評価の両方で同target baselineに対し`candidate MAE < baseline MAE`かつ`candidate RMSE <= baseline RMSE`。weekday/weekend・通勤時間帯は件数/MAE/差を記録する診断値とする。個別の自動gateは置かない。根拠・境界条件は[`capital-bootstrap-gate.md`](../261021/docs/capital-bootstrap-gate.md)に固定した。
- [x] 運用目標の仕様を決定した。暖機50要求後、同時数1・新規request IDで1,000要求、再試行0、timeout10秒、エラー0件、p95 250ms以下。hardware/資源条件と全計測値を保存する。これは学習用PC上の目標であり、業務SLOではない。
- [x] 現在のUCI `passed`は従来のvalidation MAE比較、新Capitalの精度gateは上記MAE/RMSE条件を指すと文書化した。配信許可には別途artifact/入力/運用/復旧の証拠と理由付き手動承認を要求する。旧結果・タグへ新基準を遡及適用しない。
- [x] 初回bootstrap用のprotocol/candidate/final-evaluation経路を合成fixtureで実装・試験した。incumbentなしで同target historical baselineだけを比較し、UCI/仮modelを流用しない。schema 2、policy本体/ID/SHA、nullable ledger、candidate fit、row evidence再計算、artifact hash確認、final CLI全体、final MAE/RMSE gateを確認。registry model loaderはmockで非呼び出しを検証。6〜9月の新原本は取得済みだが、新しい実候補のprotocol固定・fit・採点は未実施。
- [ ] report/model card/MLflow tagsとdeploy側で、validation/final精度・運用gate・配信許可を区別する。候補report/row evidence/registry/artifactの照合とblocked登録、独立staging APIは合成systemで確認済み。operational evidence tag記録も合成systemで確認し、review/approveはmock検証まで進んだ。ただしA01/A02の証拠結合・必須監視/復旧の拒否不足が残るため、T01/T02修正とT13実MLflow検証が終わるまで全gate完成とはしない。
- [x] 未使用評価用2026年6〜9月の4原本ZIPを取得し、bytes/SHA/ZIP構造を確認した。診断集計は正式candidate/評価入力に使わず削除し、集計値とhashを保全した。
- [ ] 月境界bufferの採否/理由を確定し、10月原本公開後に全期間snapshotを新pathへ再集約・監査する（T09/T10）。前回HEAD記録は202610=404。結果を見て評価期間を短縮しない。
- [ ] 新pathのmanifest schema 3、日時grid/DST/欠測理由・出典、分割件数、特徴量版、固定recipe、比較対象、gateとpolicy hashを実験protocolへ保存し、消費台帳の重複を検査する。候補fit前にvalidation開始を記録する。
- [ ] bootstrap validationを一度実行してtarget-specific model card・artifact・行別予測・判断記録を固定する。合格時だけ同一artifactで後続評価を一度実行し、再fitしない。使用済みwindowを最終評価や調整後候補へ再利用しない。
- [x] 固定168時間・50暖機+1,000逐次要求・HTTP/schema/候補識別・再試行0・timeout10秒・nearest-rank p95・全要求記録の合成通し処理を実装し、最新CIで検証した。benchmark_capital_runtime.pyはcheck-input-only/run-measurementの明示選択式として復帰済み。loopback 8110のapi-stagingと専用DBを通常runtimeから分離し、資源・出典・監査・linked reportまで確認する。実候補の初回計測と承認はT12/T13の未完了として維持する。
- [x] **合成データの統合検証:** 使い捨てCompose projectと一時MLflow/PostgreSQLで、人工候補のblocked登録・再登録、tag/artifact不一致の拒否、staging APIのready/predict/DB記録、offline/API parity、通常loader/runtime init/promoteの拒否を確認した。promoteの入口は合成proxyであり、実Caddy切替は未試験。候補切替後の再送でも元の候補IDを返すことと、合成1,050要求の保存を確認し、test resourceを破棄した。[作業ログ](../261021/work_log.md)と[合成要求記録](../261021/docs/reviews/20261028-synthetic-staging-benchmark.json)を参照。これは実候補の精度・運用合格を示さない。
- [x] **合成systemの入力固定と測定CLI復帰:** seed/verify-only、候補report・registry・CSV/manifest・training行hash・168時間・label cutoffを照合する。測定中はPostgreSQLの観測snapshot lockを保ち、全API応答のlag出典hashと1,050監査行を照合する。CLIはcheck-input-only/run-measurementを明示選択し、最新CIで通し実行済み。completedは処理完了を指し、正式運用gateはnot_evaluated、配信はblocked。旧合成benchmarkのcutoff超過2時刻は達成証拠にしない。
- [ ] **実候補での再検証:** 未使用snapshot/protocolから作るcandidateを使い、同じ入力固定・DB分離・運用測定・保存reportの照合を行う（T10〜T12）。
- [ ] **全証拠を結ぶMLflow承認経路:** operational evidence記録とreview/approveのmock経路は実装済み。ただしA01/A02でfinal/plan SHA変更後の判定、負のp95、監視・復旧証拠欠落の拒否が不足と判明した。T01/T02でreport本体・時刻付き決定・全証拠hashを固定し、通常loader/runtime/promoteで同条件を要求する。実MLflowの部分書込復旧はT13で試験し、実採否は必要証拠が揃うまでblockedにする。
- 完了条件: 同一のcandidate/artifact/protocolと保存reportから採否が説明でき、必要証拠の欠落・不一致・数値不正を通常配信の各入口で拒否する。合成の入力固定・運用tag記録は完了したが、新実候補のprotocol/精度/運用/監視/復旧/最終承認は未完了。最新の実施順は11.6を使い、過去の実評価期間はデバッグへ再利用しない。

#### R07 監視の参照・期間・退化ケース

- [x] replay manifestの期間とmonitor入力を一致させる。start/end省略時は対象replay reportを識別・CSV hash確認のうえ継承し、明示指定は両端必須でreport範囲内に限定。旧UCI Q4 reportのprofile欠落は既知CSV SHAに基づく推定として記録する。実report 2件と合成CLI試験で確認。既存の空窓は出自を注記し保持した。新しい監視runごとのIDと新規ファイル・DB行で履歴上書きを防ぎ、ファイルとDBの非原子性は完了markerと未確定状態で明示する。report期間はlimit等による実処理完了を証明しない。
- [ ] 学習/データprofileに対応する参照期間とPSIビンを成果物として保存する。季節対応の比較は別の診断指標にし、過去の警告値を事後に置き換えない。
- [ ] 定数参照で大きな変化がPSI=0となる問題を修正する。**進捗:** 独立した明示versionのPSI v2に定数参照より小・等・大の固定binと参照SHA、欠測・無限・母数不足の状態を実装し、合成試験済み。既存monitorには未接続で、過去のUCI値に遡及適用していない。候補の学習データとの参照結合・許容差検討と、ラベル待ち、誤差基準未設定、missing増加の区別が残る。
- [ ] 合成入力・遅延/欠落/重複ラベルで、入力分布警告と誤差悪化、誤警報/見逃し、検知までの遅れを別々に検証する。
- 完了条件: 同じprotocolと入力から窓・母数・警告状態を再現でき、未設定を「異常なし」と表示しない。

#### R08 隔離環境での配信・障害・復旧実験

- [ ] PostgreSQLで同時request ID再送、異入力の409、版切替後の元予測返却、保存失敗時503を実証する。
- [ ] 悪いgate、欠落/破損artifact、候補ready失敗、API停止、入力列変更、ラベル停止を注入し、拒否/検知/復旧と件数を記録する。
- [ ] 旧版への手動rollbackと、proxy切替前/後・台帳確定前の停止後`deploy recover`を試験する。DB・Caddy・runtimeの版が一致することを確認する。
- [ ] DBとMLflow artifactを対応するsnapshotとしてbackupし、別の一時volume/Compose projectへ復元する。登録版・model SHA・予測履歴の対応を確認する。
- [x] T08で旧PostgreSQL REAL列を持つ使い捨てDBのDOUBLE PRECISION移行を試験し、旧予測binary32・同一request ID再送・metadata・別volume復元後の型/値を照合した。実装commit 20abdabのCIも全337件/移行9件成功。旧値を保持してもAPI十進表示は変わり得る。既存UCI DBは2026-10-30のREAD ONLY確認でrealのままであり、実DBへのinitialize/ALTERは今回も行っていない。
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

この節は判断後の進捗を保持する。以下の表のCLI停止・未取得等は当時の状態であり、現在の未完了を表さない。最新チェックポイント後の優先順位・依存関係・難易度・完了条件は11.6を正規の一覧とする。特に実候補の手動承認は監視・隔離復旧の証拠が揃った後へ置く。

今回の差分チェックは`9131b1f`以降のみ。結果は[差分レビュー](../261021/docs/reviews/20261028-r06-delta-review.md)に記録した。3点の判断待ちは解消したが、実装・実測の完了には振り替えない。

| 順番 | 対象 | 次に実施する作業・完了の証拠 |
|---|---|---|
| 1 | R04/P0 | 完了。Python `tzdata==2026.5`を集計・manifest検査・APIで明示的に使い、schema 3/report/APIのidentity記録、DST・後方互換境界テスト、全41 ZIPの再集計とhash確認を行った。旧期間の再集計物は新規評価には使わない。 |
| 2 | R06/P1 | **完了:** 合成候補と使い捨てCompose/MLflow/DBで登録・拒否・API/DB・offline parity・再送・合成1,050要求を検証し、test resourceを破棄した。実Caddy切替や実候補評価は含まない。 |
| 3 | R06/P1 | **進行中:** seed時と投入後のDB・candidate照合を実装し、合成SQLiteと一時PostgreSQLで検証。候補レポート・データのhashとtraining split・ラベル到着・168時間の欠損検査を入力確認CLIに追加し合成試験した。benchmarkへのDB再照合、測定中の変更防止、API出典検証は未完了のため、CLIの実測は停止中。これらを実装し不一致・境界・欠損を試験する。 |
| 4 | R06/P1 | operational report hash/statusをexact MLflow versionへ付ける経路を実装・試験する。全gate passedかつ理由付き手動承認がある場合だけ`eligible`にし、failed/missing/mismatchを拒否する。 |
| 5 | R06/P1 | 月境界bufferを含む未取得原本範囲を確認→未使用archiveを取得→manifest schema 3監査→protocolへ未使用split/件数/recipe/gateを固定する。training cutoffは2026-06-01 00:00、直前の未到着2時間を除外。 |
| 6 | R06/P1 | protocolを凍結しvalidationを一度実施→artifact/evidence固定→合格時だけ同一artifactで独立final評価を一度行う。 |
| 7 | R05/R06/P1 | 実candidate後にCapital staging DBへseedし、blocked登録/API load、offline/API parity、promotion rejectionを確認。固定1,050 request benchmarkのresult/hashを記録する。manual approvalは11.5の監視・復旧・証拠照合後まで行わない。 |
| 8 | R07/P1 | 同profile training reference/PSI bins、monitor期間継承、定数分布/件数不足/ラベル待ち/欠測増加の状態を実装・試験する。 |
| 9 | R08/R09/P1 | 隔離環境で並行要求、拒否/障害注入、rollback/recover、backup/restoreを検証し、配信可否・承認metadata・進行表示を整合させる。 |
| 10 | R10/P2 | 採否と未達理由、再現手順、5分デモと成果の証拠をまとめる。 |

### 11.5 2026-10-29 synthetic system-test後の残課題

この節は合成system完成時点からの進捗履歴を保持する。2026-10-30監査で追加したP0とモデル別担当は11.6が優先する。

[差分レビュー](../261021/docs/reviews/20261028-staging-checkpoint-review.md)後の変更を再確認し、2026-10-29に現行CLIから人工candidateの隔離Compose/MLflow/PostgreSQL/API測定までを完了した。206 testsとDocker system testを確認後、operational evidence recorderを追加し、単体試験・最終system testで同一versionへの証拠記録、同値再実行、異なるhashの拒否を確認した。実candidate/final reportは存在せず、Capitalの実精度・実運用gate・配信承認は未達。以下はこの時点の優先順・依存関係の記録で、後続の11.6が現在の正規一覧である。

合成stack内の通し試験、明示選択式計測CLI、completed reportのexact MLflow versionへの記録、独立reviewと別人approvalを模したgate更新経路は実装し、合成/mock試験で検証済み。実MLflow上の決定記録と実候補は未確認。次のデータ節目は未使用原本とprotocolの凍結、その後に実candidateの精度評価を行う。evidence recorder単体は運用gateをpassedにせず、helper・合成結果は実データの合格証拠として扱わない。コードはレビュー可能な単位でcommit/pushし、完了欄は結果と証拠を得た後に更新する。

| 順番・優先度 | 対象 | 実装・操作と完了条件 |
|---|---|---|
| 1 / P1・完了（合成） | R06、S01/S03 | **計測の入力固定とCLIを統合する。** plan・試行台帳・候補/データ/registry事前確認・PostgreSQL観測snapshot lock・予測出典・監査照合を接続。2026-10-29に現行CLIから一時Composeで744観測、blocked登録、API/offline parity、retry/409、runtime拒否、50 warmup+1,000逐次要求、監査、数値再確認まで完了。台帳`completed`は測定処理の完了のみを表し、正式gateは`not_evaluated`、配信は`blocked`。CI run `40879692956`と後続`40966168109`でsynthetic system、PostgreSQL snapshot-lock、project-scoped cleanupが成功。**人工データの結果であり実candidate証拠ではない。** 強制終了時の永続journal、実HTTP timeout後の遅延commit照会は残る。 |
| 2 / P1・証拠記録完了/判定経路は合成実装済み | R06、S02 | **完了証拠をexact versionへ記録する。** `record_capital_operational_evidence.py`はcandidate/ledger lock中にcompleted attempt、frozen plan、report SHA、1,050件の数値再計算、DB audit/input provenance、host/container resource context、exact registry version/artifact/gatesを再照合する。`record_capital_deployment_decision.py review/approve`で人の決定レコードSHAを保存し、別人のapproval後だけeligibleにする。通常loader/runtime/promoteは決定SHA・対象version・artifact/report bindingを検査する。mock registryで合否拒否、レビュー/承認分離、理由改変、異version、p95超過、review/approvalの部分tag書込後もblocked維持と同一要求retryによる復旧を試験。commit `77fe955`のGitHub run `40883359147`は当時の220 testsに成功。追加test後、commit `dae897a`のGitHub run `40966168109`で全221 testsが成功した。CI結果・hashは[作業ログ](../261021/work_log.md)。実MLflow書込と実候補は未検証。人名の自己申告はIAM/署名ではなく、実MLflowの部分tag書込復旧も未検証。 |
| 3 / P1・完了（CI合成system/DB）・failure injection残 | R05/R06、S04 | **再現可能な統合試験を保存する。** `scripts/capital_system_harness.py --run-synthetic-system`で人工bundle→blocked登録→seed→API/offline parity→固定測定→監査→数値recheck→exact version evidence tagsまで再現し、同値再実行・異なるhash拒否、blocked維持を確認。`test_capital_snapshot_postgres.py`では使い捨てDBのwriter競合・lock timeout/接続喪失・監査不一致拒否を検証。過去のcleanup不具合を修正し、CI run `40879692956`で全213 tests、synthetic system、snapshot-lock PostgreSQL、project-scoped cleanupが成功した。最新commit `dae897a`のrun `40966168109`でも全221 testsと同じsynthetic system/PostgreSQL/cleanup経路が成功した。candidate `343a1bbbfd6a068c74fa472ca28bc49274cc9c750a73c8faaaf76766eb6ebd19`、744行、1,050要求・監査行、offline/API差0、p95 `32.332672 ms`、numeric recheck matched。正式gateは`not_evaluated`、deploymentは`blocked`。artifact `bike-mlops-docker-integration-logs`（ID `12938826361`、SHA-256 `82747f77d96dd7445514c659ed4b8b3fef5790092a6fb972137035c7a990aa92`）を保存。p95は人工fixture・GitHub runnerの測定で、実候補や本番SLOの根拠ではない。実Caddy切替や実データ運用は未試験。強制終了時の永続journal、実HTTP timeout後の遅延commit照会、監査保存失敗のfailure injectionは残る。 |
| 4 / P1・原本取得済み/10月buffer待ち | R01/R02/R06 | **未使用原本とprotocolを固定する。** 2026-10-29に公式S3の202606〜202609を取得し、4 ZIP（合計99,766,625 bytes）のCSV構造・ZIP integrity・SHA-256を検査した。2026-10-30にarchive objectへ直接HEADし202609=200、202610=404を再確認した。各archiveは65〜68万行、6〜9月の診断aggregateは2,928時間・missing/duplicate timestamp 0。5月〜9月の別診断は3,672時間・missing/duplicate timestamp 0、読込3,224,860行・その5 archive範囲で重複ID 0。診断CSV/manifestは`/tmp`に一時作成後、2026-10-29に削除し4件とも不存在を確認した。candidate fit/validation/finalには使用していない。全期間再集約、schema 3 manifest、DST/境界auditは未完了。202510 archiveが2025-09-30開始のtripを含んだ例があるため、9月末の境界を10月bufferなしで確定しない。202610公開後にrawを取得し、新pathへ全期間再集約・manifest検査・protocol freezeを行う。既評価Q3/Q4/旧Capital窓は再利用せず、training cutoff 2026-06-01 00時、未到着の前日22/23時除外、validation/finalの非重複・判断時刻・件数・recipe・精度policyをcandidate fit前に凍結する。原本4件のSHA-256は[作業ログ](../261021/work_log.md)を参照。 |
| 5 / P1 | R03/R06 | **実データ精度評価を一度行う。** 台帳でvalidation開始を記録し、固定recipeで候補artifact/row evidence/source snapshotを保存。MAE厳密改善かつRMSE baseline以下を確認。合格時だけ同じartifactの独立final評価を一度実施し、再fitしない。weekday/weekend/通勤時間帯の件数と差も報告。未達なら見送り、同じ窓で選び直さない。 |
| 6 / P1 | R05/R06 | **実候補をblockedのまま運用測定する。** exact versionで登録/seed/staging APIを検証し、凍結planに従う50+1,000要求を測定。offline/API一致、DB分離、通常deployの拒否、input/artifact/監査/資源証拠を確認する。候補のoperational report/hashを保存する。この時点では手動採用を確定しない。 |
| 7 / P1 | R05/R07 | **Capital監視を完成する。** replay期間の継承、run別の監視履歴保存、定数分布に対応した未接続のPSI v2部品を追加。同profileのtraining referenceとPSI binsの候補への結合、v2接続、DBとファイルの未確定状態を調査する運用が残る。定数分布・件数不足・ラベル待ち・誤差基準未設定・欠測増加を区別する。合成の分布変化/誤差悪化/遅延・重複ラベルで警告・遅れ・母数を検証する。過去のUCI警告値を置き換えない。 |
| 8 / P1 | R08/R09 | **隔離配信・復旧とmetadataを確認する。** PostgreSQLで同一ID並行再送/409/保存失敗、悪いartifact/gate/ready、API停止、ラベル停止を試験。Caddy切替前後と台帳確定前の中断からrollback/recoverし、入口・runtime・DBが一致することを確認する。DB/artifactの対応backupを別volumeへ復元する。承認metadata・利用不可版1の扱い・段階/経過時間/timeout表示を整合させる。合成fixturesを使う隔離試験は順番3以降に先行可。通常の未達gateを緩めず、既存UCIを障害実験に使わない。 |
| 9 / P1 | R06/R09 | **実候補の理由付き採否を記録する。** 順番5〜8の精度・運用・監視・復旧証拠が揃い、同じcandidate/version/artifactを指すことを確認する。承認者と理由を記録し、未達は採用しない。採用する場合だけ証拠を照合した上でeligibleを確定し、対象profileの配信を操作する。別targetのUCIを自動で置き換えない。 |
| 10 / P2 | R10 | **成果と再現手順を整理する。** Rタスク/commit/CI/証拠の一覧、見送り・未達理由、5分デモ、予測誤差/API時間/分布/復旧時間/資源の区別を記録する。実測の範囲・ラベル遅延の仮定を明示し、外部公開時は出典・利用条件・既定ホスティング方針を確認する。 |

補助の保守項目（P2）: Starlette/httpx TestClient警告は2026-10-29に`httpx2==2.16.0`をdev依存と`requirements.lock`へ追加して解消し、206件のsuiteを確認した。統合CLIの候補同一ID判定は全page検索と並行登録の扱いを確認し、逐次再実行の成功を同時実行の保証と説明しない。MLflow/SQLAlchemyの`noload`非推奨warningは残る。これらは未使用評価期間を消費して検証しない。

registry URIの進捗補足: Capital staging登録・loader・seed・preflightはtracking/registryを同一URIへ固定済み。通常model loader/cli clientには環境変数で別registryへ解決する余地が残ることを2026-10-30監査で確認した。その後T01でCapitalの通常client/artifact取得も両URIを固定し、異なる環境変数の負例を検証した。Capital合成systemは既にCIで成功しており、実候補systemと実Caddy切替は別の残課題である。

<a id="remaining-work-20261030"></a>

### 11.6 2026-10-30監査後の優先度・難易度別計画

**現在の残件は[最新の残課題](#remaining-work-latest)を参照。以下の過去checkpointは当時の実施状況として保持する。**

本節を残作業の正規一覧とする。前回checkpoint記録10dfb8fから070b033までの28コミット・65ファイルを確認した結果と、[差分レビュー](../261021/docs/reviews/20261030-checkpoint-review.md)・[証拠JSON](../261021/docs/reviews/20261030-checkpoint-evidence.json)を根拠にする。R01〜R10は目的のIDとして維持し、以下のT番号を実際の作業単位にする。11.4/11.5の旧順番やチェック数より本節が優先する。

現在の到達点は、UCI版3の稼働と合成Capitalの入力固定・監査付き運用計測・CI成功。監査時点の070b033のCI 40966966672では221 testsとDocker/PostgreSQL/cleanupが成功した。T01/T02実装commit 29ed879のCI 40977087079では全241 tests（8.204秒）と合成Docker/PostgreSQL/cleanupが成功した。合成計測は1,050要求/監査行、p95 31.186768msでblockedを維持。Pages run 40977086506も成功した。実Capitalの新候補、独立final、運用合格、通常配信の完成は未達。監査時の合成registryでは、承認後のfinal/plan SHA変更・負のp95の受理、監視・復旧証拠なしのeligible化を確認した。その後T01とT02の必須schema/拒否処理を実装し、全241 tests（30.810秒）が成功した。11報告書本体を持つbundleとschema 2の決定で結合し、一時SQLite MLflowで保存/承認/本文破損拒否も確認した。実Capital候補と監視・復旧producerの実証は残る。実運用registryは変更していない。詳細は[配信証拠仕様](../261021/docs/capital-deployment-evidence.md)と[作業ログ](../261021/work_log.md)。

#### 優先度と難易度の基準

優先度と難易度を別々に付ける。P0は指定した境界より前に必須で、今回T01/T02は実Capital eligible化・通常配信前、T10は実candidate fit前の条件。P1は精度・運用・監視・復旧を完成するために必要。P2は記録・保守・説明の改善。外部データ待ちのP0があっても、独立した合成実装や資料作業は先に進められる。

| 難易度 | 判断基準 | 推奨するモデルと進め方 |
|---|---|---|
| D3 / 高 | 複数のDB/ファイル/registry状態、時刻リーク、一度限り評価、並行処理、破損や配信への影響を横断して判断する | Solが設計・実装・負例検証・完了判定を担当。固定した補助作業だけをLunaへ切り出す。 |
| D2 / 中 | 入出力と拒否条件が確定した単一部品、既存scriptのCI接続、合成fixture、読取専用の照合 | GPT-6 Lunaで実装可能な候補。状態表・触るファイル・受入条件を先に固定し、配信やデータ契約へ影響する部分はSolがreviewする。 |
| D1 / 低 | 決まったコマンドの読取結果整理、hash/件数照合、リンク・用語・手順書の更新 | GPT-6 Lunaを第一候補にする。根拠と出力を保存し、未実行を成功にしない。 |

モデルの割当はこのリポジトリのリスクに基づく推奨で、両モデルを同じ課題で比較した実測ではない。OpenAI公式の[model selection](https://developers.openai.com/api/docs/guides/model-selection)は、複雑な技術作業にSol、範囲が決まった作業にLunaを使い分け、同じ品質基準で確認する考え方を示す。ここでのSolは現在の作業環境のGPT-6.1 Sol、LunaはGPT-6 Lunaを指す。モデルや利用環境が変わっても、この表の受入条件は変えない。今回モデル切替や別agentへの実委譲は行っていない。

#### 実装・検証・操作の作業表

| ID / 対象 | 優先度 / 難易度 / 推奨 | 状態・依存 | 作業内容と完了条件 |
|---|---|---|---|
| T01 / R06・A01/A03 | **P0 / D3 / Sol** | **実装・合成/一時SQLite検証済み**。HTTP実証はT13 | **承認した証拠の全体を固定する。** candidate、exact model/version、profile/target、登録artifact、candidate/final/operational report、protocol/plan/policyのhashと検証済み指標を版付きpayloadへ結ぶ。report本体を取得・hash/content照合し、不足・SHA変更・理由変更・負/NaN/無限p95・別versionを通常loader/runtime/promoteで拒否する。通常Capital clientとartifact取得のtracking/registry URIも同じ先へ固定し、異なる環境変数の負例を試す。値違いを人の承認で上書きしない。合成fixtureでA01/A03の拒否、通常loader/runtimeの本文拒否、URI固定を確認済み。11報告書のSHA/content・保存済み予測行の精度と要求別p95を検算し、取得前後のregistry変更も拒否する。 |
| T02 / R06・S06・A02 | **P0 / D3 / Sol** | **schema・必須化・拒否処理は実装済み**。実証はT04/T05/T06/T07/T08/T12/T17に依存 | **監視・復旧を配信の必須条件へ接続する。** 必要なmonitor/recovery/restore証拠のschema・同一profile/version/artifactへの結合・policyを事前固定する。欠落、failed、別候補、未確定DB/fileをapprove/通常配信で拒否し、時刻・操作者・理由を持つ決定を保存する。実MLflow書込はT13。policy `capital-deployment-evidence-v1`で監視6・復旧8・復元4・metadata4ケースを固定し、4分類すべての本文を要求する。失敗・未確定・別candidate・不足の負例を確認した。実producerの観測申告は未作成で、合成fixtureを実証へ転用しない。必要証拠がない間はblockedを保つ。名前の自己申告と認証済みidentityは区別し、AIが二つの名前を作って独立した人の審査と説明しない。 |
| T03 / R06/R08・A04 | P1 / D3 / Sol | **実装・障害検証・CI成功（2026-10-30）**。最終コード24115cfのCI 40999776550で全265 tests（29.205秒）、合成Docker/両PostgreSQL試験・cleanup成功。[証拠](../261021/docs/reviews/20261030-t03-checks.json)参照。**2026-11-01追跡: 自然停止は未再現。signal/HTTP終了/台帳書込/process終了traceとstack採取を追加、人工停止で診断確認** | **中断・遅延commitを追跡する。** attemptと固定request IDの送信予定/送信中/応答確認を、強制終了後に調べられる形へ永続化する。fsyncの位置、HTTP時間とjournal等の全体時間の区別、計測policyへの影響を先に決める。実HTTP timeout後の遅延commit、SIGTERM/SIGKILL、DB接続喪失を使い捨て環境で注入し、照会時不在を未送信と断定しない。再試行は理由付き別attemptとし速い結果だけを採用しない。 |
| T04 / R05/R07・A05 | P1 / D3 / Sol | **実装・全255 tests・CI 40980116840のDocker/PostgreSQL/cleanup成功**。実候補/HTTP監視は未実施 | **training referenceとPSI v2を接続する。** 参照行集合・期間・CSV/manifest/profile/feature契約・固定bins・algorithm versionを候補artifactへ結び、hashの違いを拒否する。既存monitorからversionを明示して使い、定数参照・小標本・非有限・欠測を別状態にする。後続評価の分布を使ってbinsや閾値を選び直さず、旧UCIの履歴値を保存する。新規protocolへmonitor policyを固定し、欠落/変更時はfit・台帳開始前に拒否。参照descriptorをcandidate IDへ含め、MLflow run artifactの取得後に元CSV/manifest/学習行・全特徴量・binsを照合する。合成CLIで27窓のSQLite/CSV/JSON/marker保存、変更参照・異版予測・不正特徴量の拒否を確認した。[仕様と未実施範囲](../261021/docs/training-monitor-reference.md)。 |
| T05 / R07 | P1 / D2 / **Luna実装、Sol review** | **Sol review・合成受入・CI成功**。対象26件/全277件local成功、[CI 41005083367](https://github.com/kenobix/Claude_practice/actions/runs/41005083367)も全277件35.217秒・隔離Docker/両PostgreSQL/cleanup成功。実Capital/HTTP監視は未実施 | **合成監視の受入を確認した。** 10シナリオと閾値同値/168時間窓の下端除外の2境界を実CLI・一時SQLiteへ通し、全時計の母数/警告、CSV/JSON/marker hash・run ID・DB全窓の値を照合。期待窓と検知遅れは[受入基準](../261021/docs/monitoring-acceptance.md)、実行log/source SHAは[証拠JSON](../261021/docs/reviews/20261030-t05-checks.json)。欠測はnullで保存し、遅延注入は監視対象の24ラベルだけへ限定した。欠測率の新規閾値は導入せず、未設定validation MAEは診断で`performance_reference_unset`を記録し採用許可にはしない。重複入力はreport/監視DB行保存前に拒否。T06の異常保存照合、実候補/HTTP監視、T02実証producerは別途必要。 |
| T06 / R07・A05 | P1 / **D2（a）Luna→Sol review、D3（b）Sol** | **a/b実装・local/CI受入完了（b: 164ac53）** | **monitor保存の未確定状態を読取照合する。** [契約](../261021/docs/monitor-reconciliation.md)と[T06b証拠](../261021/docs/reviews/20261030-t06b-checks.json)を参照。aはSQLite/CLI対象54件とCI全331件/隔離system成功。bはREPEATABLE READ READ ONLY、型/metadata/UUID行の同一snapshot、REAL量子化とDOUBLE/JSONの厳密比較、SQL NaN欠落正規化を実装。最終対象unit 60件17.869秒、専用PG受入25件27.049秒とcleanup成功。cleanup診断の最終修正前の全suite 336件143.300秒も成功。実装commit 164ac53の[CI 41068758500](https://github.com/kenobix/Claude_practice/actions/runs/41068758500)は最終337件39.970秒・PG受入25件14.461秒・全system/cleanup成功。writer/schema/実DB・実reportは変更せず、合成成功を実Capital監視/配信の証拠へ転用しない。実候補/HTTP monitor・T02 producer接続は残る。SQLite OS権限/busy timeout補足は2026-11-03にSolが完了した。 |
| T07 / R08 | P1 / D3 / Sol | **T07a local合成UCI/実Caddy・API・PG受入成功**。f341e94のCI全369件・既存system成功。**T07bは実装2414b20の[CI41462429218](https://github.com/kenobix/Claude_practice/actions/runs/41462429218)で全419件・対応DB/実MLflow-skopsの別project/3volume復元・artifact/hash/cleanupまで成功**。 [CI索引](../261021/docs/reviews/20261102-t07b-ci-checks.json)。**2026-11-03: T07a現Dockerfile/所有image・normal/TERM cleanupのCI受入完了（4a4474e、CI41559833990、16 member/hash照合）**。実Capital producerは未了 | **隔離配信と復旧を通す。** PostgreSQL同一IDの並行再送/異入力409/版切替後の元予測/監査保存失敗503を確認する。破損artifact、ready失敗、API停止を注入し、実Caddyの切替前後・台帳確定前の停止からrollback/recoverする。入口・runtime・DBの版/件数を照合し、DBとartifactを対応snapshotとして別volumeへ復元する。既存UCIを障害対象にしない。 |
| T08 / R08・A06 | P1 / D3 / Sol | **local受入/13回帰と実装commit 20abdabのCI成功** | **REAL→DOUBLE PRECISIONの移行を検証した。** [CI 41263492531](https://github.com/kenobix/Claude_practice/actions/runs/41263492531)で全337件40.391秒・移行9件2.549秒・既存system/PG/cleanupも成功。 [仕様・制約](../261021/docs/prediction-precision-migration.md)と[証拠](../261021/docs/reviews/20261101-t08-checks.json)参照。旧4数値/全table/metadata、型変更だけの反復initialize、同一IDの元版応答/異入力409、新行binary64、profile拒否、ALTER後の例外/実接続喪失/commit前後SIGKILL、並行initializeを確認。移行前/後dumpを第二専用container・別volumeへ復元し型/全行/APIも照合。既存production source/実DBは変更していない。旧丸め前値は復元できず、保存binary32を保持してもAPI十進表示は変わり得る。過去JSON完全一致は保証せず、必要な利用者がいる場合は応答policyを別設計する。 |
| T09 / R01 | P1 / D1 / **Luna** | 外部待ち。2026-11-02/03公式HEADで202609=200/202610=404を再確認 | **未取得原本の取得と機械的照合。** 公式objectの公開後に既存acquire手順で取得し、bytes/SHA/ZIP/CSV member/schemaを新規logへ残す。ソースURL、隣月範囲、既存ファイルとの違いを報告する。候補fit・評価窓変更・精度の計算は含めない。不一致は保存してT10のSol判断へ戻す。 |
| T10 / R01/R02 | **P0（fit前） / D3 / Sol** | T09、T04のbundle仕様。T01/T02の新policyと整合 | **全期間snapshotと未使用protocolを固定する。** 隣月bufferの範囲/理由、全原本manifest schema 3、TZif identity、重複/収支/DST/欠測理由、target gridを監査する。6〜9月の診断aggregateは正式入力へ流用せず新pathへ集約する。既評価窓との非重複、training cutoff=2026-06-01 00時と前日22/23時のラベル未到着除外、validation/finalの判断時点・件数・recipe・MAE/RMSE policyをfit前に保存する。10月bufferなしで窓を短縮しない。 |
| T11 / R03/R06 | P1 / D3 / Sol | T10完了 | **一度限りの実candidate評価。** 消費台帳を先に更新し固定recipeでfit・candidate bundleを保存する。同じ行のbaselineにMAE厳密改善かつRMSE以下を確認し、通過した同じartifactだけを独立finalで一度評価する。weekday/weekend/通勤時間帯も件数付きで報告し再fitしない。不合格なら見送り、同じ窓で候補を選び直さない。 |
| T12 / R05/R06 | P1 / D3 / Sol | T11の精度条件通過、T03の測定policy | **実候補のblocked運用測定。** exact version登録/seed/ready/offline-API一致/通常deploy拒否を確認し、固定planの50暖機+1,000逐次要求、DB監査・出典hash・資源条件を保存する。別attemptや失敗も残し、数値再計算とreport本体をexact versionへ結ぶ。この段階で実配信の採否は確定しない。 |
| T13 / R06/R09 | P1 / D3 / Sol | 一時SQLite MLflowの正常保存/承認/破損拒否は確認済み。**2026-11-01: FakeRegistryの2回に加え、最新全suiteで一時SQLite承認試験も自然再発。reviewが比較UTCより4.170111秒先、MONOTONICは正方向。現在の実SQLite経路のUTC逆行機序を確認。過去のsetterとの同一性は未確定**。**T13aの使い捨て実MLflow HTTPで部分書込/最終gate・permission応答喪失/同一retry/競合拒否を確認、最終5ケースとTERM cleanup成功**。[仕様](../261021/docs/capital-decision-http.md)。**実装f1cc9ffの[CI41300378098](https://github.com/kenobix/Claude_practice/actions/runs/41300378098)で全376件・新HTTP step・artifact/cleanup成功**。[CI証拠](../261021/docs/reviews/20261101-t13-http-ci-checks.json)。実採否T13bはT05/T06/T07/T08/T12/T17等の必須証拠後 | **実MLflowの決定書込復旧と最終採否。** 使い捨てregistryで部分tag書込、最後のgate書込後の応答喪失、同じretry、競合decisionを試す。実候補では必要証拠が同一artifactを指すことを確認し、ユーザーの意図するreviewer/approver・理由・時刻を記録する。未達を人名/理由で上書きしない。別targetのUCIへ自動移行しない。 |
| T14 / R10・A08 | P2 / D1 / **Luna** | **今回checkpointのT01〜T17→実装commit/CI/artifact SHA対応表・読取入口・5分説明を整理済み**。[証拠入口](../261021/docs/evidence-guide.md)。以後は各taskの実証後に追記 | **証拠と説明を整理する。** R/T task→commit→CI→artifact/log hashの一覧、用語、読取手順、5分demoを作る。logはcommit/UTC/attempt付きの新規pathにし、過去の件数訂正・上書き済み証拠の限界も残す。予測誤差・API時間・PSI・復旧時間を混同せず、未実施項目を完了にしない。 |
| T15 / R04 | P2 / D2 / **Luna実装、Sol review** | **Sol review・実装commit 5cc5a89の[CI 41264954179](https://github.com/kenobix/Claude_practice/actions/runs/41264954179)・artifact/cleanup確認済み** | **CI接続完了。** 既存の使い捨てmonitor persistence PostgreSQL scriptを独立CI stepへ接続し、失敗を隠さずlogを保存した。fake registry unit testのversion lockはT01作業で一時directoryへ分離済み。変更YAMLの検査と対象回帰試験を行う。失敗/timeoutの終了値23/124がtee後にも伝播することをSolで確認した。全337件40.792秒・新step/既存system/PG成功。artifact 13067122187のZIP digestと9 memberを照合し、専用logの5検査成功/cleanupを確認した。[証拠](../261021/docs/reviews/20261101-t15-checks.json)。lockや共通runnerを広く更新する必要が出たらSolへ戻し、警告だけで全面upgradeしない。合成試験は実Capital監視/配信の証拠ではない。 |
| T16 / R04/R10・A07 | P1 / D2 / **Luna実装、Sol review** | **完了: Sol review修正・専用23件/全399件成功後、実装2492abcの[CI41442578156](https://github.com/kenobix/Claude_practice/actions/runs/41442578156)で全399件/両job全step成功。artifact13 member（T16追加3件）、ZIP/member hash、archive-only別processの1,000件p95再計算・cleanupまで確認**。[CI証拠](../261021/docs/reviews/20261102-t16-ci-checks.json)。[Sol review](../261021/docs/reviews/20261102-t16-sol-review.json)。[引継ぎ仕様](../261021/docs/t16-luna-handoff.md)、[操作/限界](../261021/docs/measurement-evidence-archive.md)、[検証JSON](../261021/docs/reviews/20261102-t16-checks.json) | **合成CIの再計算可能な証拠を保全する。** cleanup前にsynthetic-only plan/report/ledger/request journalを固定allowlist archiveへbyte-preserving保存する。journal SHA/要求別HTTP時間とreportを照合する。.env/credential/外部pathを含めず、ZIP hashとmember hashを確認する。archiveだけを得た別processでwarmup50を除く1,000件p95を再計算しsummary一致、cleanup成功を確認する。**同一実装commitのCI artifact・hash・別process再計算・cleanup確認まで完了。実Capital性能・時計資格・配信採否への流用は禁止**。 |
| T17 / R09 | P1 / D3 / Sol（進行文言は固定仕様後Luna可） | **T17a受入済み。T17b部分（2026-11-02時点）: 通常CLIのstage/経過/log、Docker timeoutの未確定記録、SQLite/PGの旧state読取診断を実装。実装2414b20のCI419件・合成PG診断が成功**。**2026-11-03: schema3/API操作照合・CLI全体900/300秒deadlineの合成受入完了。最終4a4474eのCI447件/16 member/hash/normal・TERM cleanup成功**。[最終CI索引](../261021/docs/reviews/20261103-t17b-t07a-ci-checks.json) | **現在版・承認metadata・利用不可版の扱いを揃える。** 入口/runtime/配信台帳の版・操作ID・承認者/理由をrecoverでも一致させる。読み込み不可の旧版1を履歴に残し、rollback候補から除外する。実deploy/model取得の段階・経過時間・参照log・timeout時の未確定状態を表示する。harnessだけのheartbeatで製品側まで完了としない。 |

T15で観測したCI保守の残件（P2/D2、Sol review）: `actions/upload-artifact@v4`のNode.js 20→24強制実行とpunycode/url.parse、MLflow経由SQLAlchemy noloadの非推奨警告。新step・artifact保存は成功しており、CI接続の未完了とは扱わない。actionの単独更新は互換性・allowlist・ZIP回収を別途確認し、警告だけで依存lockを全面更新しない。

#### 次の着手順とモデルへの引継ぎ

1. T01とT02のschema/拒否処理は実装済み。実Capitalのeligible化は必要証拠の完成まで進めない。合成fixtureだけでT02全体を完了にせず、各producerが同じ版・candidate・artifactの観測結果を出すことを後続taskで検証する。
2. T04/T03/T05とT06a/bの実装・合成受入/CIは完了。続いてT08の旧REAL schema移行・別volume復元を9件の合成受入とStorage8/API5回帰で確認した（実装commit CI全337件・移行9件・system/PG/cleanupも成功）。既存UCIへの実適用は未実施で、API十進表示の変化を記録した。次のSol担当P1はT03自然再発時のphase/stack照合、観測済みUTC逆行の同期機構切り分け、T07/T17の隔離配信/復旧・metadata整合である。T04/T05/T06の実候補/HTTP監視確認とT02 producerはT11/T12等の後に行い、合成成功を実証へ格上げしない。データ待ちと独立した使い捨てHTTP試験は先行できる。
3. T15はLuna実装・Sol review・実装commit CIとartifact/cleanup確認まで完了。T16はSolが保存path/allowlistを固定済みで、Lunaがlocal synthetic harnessを2回実行しarchive-only再計算/cleanupまで確認。Sol review修正と専用23件/全399件の再検証後、実装2492abcのCI artifact/hash/再計算/cleanupまで確認済み。T14は独立して着手可能。T06aはSol review/local/CI済み。新たなschema/配信判断はSolへ戻す。T09は公開後の定型取得/照合に向く。
4. 原本到着後にT09→T10→T11→T12。監視/復旧/metadataの必要証拠が揃ったらT13の実採否へ進む。T10の前提を満たさないまま、既評価窓で実験を先行しない。

T06a/bの読取照合部品はlocal/実装commit CI受入済み。T06に関連する残件は、実候補/HTTP monitor・T02 producerの証拠接続である。ネットワーク上の実接続timeout満了は未注入で、接続5秒指定はunit、実接続失敗/切断・statement/lock timeoutはPG受入で確認した。SQLite S13のOS実ファイル権限/実busy timeoutの組合せは**2026-11-03に非root・使い捨てDBで受入成功**（実chmod000、DELETE journal EXCLUSIVE待ち約5.02秒、全原本/DB不変・解除後matched）。元のP2/D2追加確認は完了（今回はSolが実施。使い捨てDBのみ、既存の拒否基準を緩めない）。table読取拒否はSQLite authorizer、ロック失敗は模擬OperationalErrorで確認済みであり、実環境の全権限/timeout条件の証明とはしない。

T05の全suite再検証で、既存T03のSIGTERM子process終了待ち10秒のtimeoutと、既存T13相当の一時SQLite承認試験で直前のreview時刻をfutureとする失敗を各1回観測した。各単独試験と最終277件suiteは成功したが、原因は未特定。**追加追跡はP1 / D3 / Sol**とし、T03では受信/signal/handler/ledger書込/終了の段階と子process stack、T13では生成時刻と検証時刻のUTC/monotonic関係を記録して切り分ける。限られた500,000回のwall clockサンプルで逆行0件だったことだけで時刻問題を解消済みにしない。待ち時間やfuture拒否を緩めて成功に置き換えず、T05の合成受入成功と既存境界の安定性を区別する。全試行は[作業ログ](../261021/work_log.md)とT05証拠JSONに保存する。

**2026-11-01追加追跡:** [診断手順と判断](../261021/docs/interruption-clock-diagnostics.md)、[今回の証拠JSON](../261021/docs/reviews/20261101-t03-t13-diagnostics.json)を追加した。基点試験、独立3反復、診断対象24件、全340件（161.771秒）で元のT03停止/SQLite承認失敗は自然再現していない。最後の制御時計fixture固定後はdecision 9件を再確認し、最終sourceは実装8869ee1の[CI 41268250834](https://github.com/kenobix/Claude_practice/actions/runs/41268250834)で全340件44.901秒・compileall・全system/PG/cleanup成功、artifact ZIP/9 memberも回収照合済み。人工台帳停止ではsignal受信後の停止stackと未確定journal、自動retry拒否を確認した。T03の歴史的停止原因はP1/D3/Solのまま。

時計の独立2万sampleでは逆行0回だったが、試験と並行した10万sampleではCLOCK_REALTIMEが4回逆行（約2.956/2.927/3.873/2.921秒）、他3時計の逆行は0回だった。実時計をmockしないFakeRegistry probeでは2回、承認関数のレビュー時刻検証がfutureで拒否され、比較UTCと正のmonotonic経過・tag不変/blockedを保存した。2回目は3.029889秒のUTC差と元ログと同じ判定行のtracebackを保存した。現在環境の発生機序は確認できたが、過去失敗時の時刻値とUTCを動かした同期機構は不明。NTP/同期flagはyes、timesyncdはrunning、clocksourceはtscであり、flagだけで時計の安定性を判断しない。UTCの先行1µsも拒否し、clamp/待機/自動retryは導入しない。timesyncdの読取ログにtimeout/再接続と時刻逆順、poll 32秒を観測したが、CLOCK_REALTIMEを変更した主体は断定していない。同期機構の切り分けと当該実行環境の観測保全はP1/D3/Solとして残す。今回の診断追加をT13のHTTP障害検証・実採否全体の完了にしない。

**2026-11-01・基点b5dbdfdからの変更元追跡:** [詳細/未実施の設定案](../261021/docs/clock-origin-investigation.md)、[全試行・hash](../261021/docs/reviews/20261101-clock-origin-checks.json)。Windows/WSLの同時読取、NTP更新との照合、専用kernel traceを追加した。負方向5区間は`systemd-timesyn`（global TID 496）の`clock_adjtime→timekeeping_inject_offset`と一致し、うち2回は要求−4.473373174/−3.128148078秒と成功戻り値を直接採取。`initd`の5回は前進、`hv_set_host_time`は同じ観測区間で0回だった。別途`chronyd`（global TID 213）がADJ_TICK/FREQUENCYを16回成功させ、うち14回tick=11000/freq=0を指定した。bracket付きWindows QPC比はMONOTONICが1.099918〜1.100057、RAWが0.999926〜1.000052。同期flagや単調増加を正常な秒数の保証としない。Windows W32TimeはStoppedで、現在区間の時計変更イベントは検索一致なし。現在の直接呼出元は絞れたが、commと外側PIDの実行file/管理主体の照合、11000再指定の理由、過去失敗との同一性は未解決。Windows管理者debug shellは起動を拒否され、OS設定/実DB/registry/配信は変更していない。

| 追加残件 | 優先度 / 難易度 / 担当 | 完了条件・依存 |
|---|---|---|
| 時計調整processの管理元 | **P1 / D3 / Sol・一部確認済み** | Ubuntu timesyncdはlocal 214→kernel 496を読取照会/kprobeで照合し、exe/hash/親/cgroup/unit/vendor WSL drop-in/ntp.ubuntu.comを確認。chronydはkernel 213のtick=11000継続と明示loopback queryのPHC0応答、WSL 2.6.3 initの組込起動設計まで確認。**管理者採取後、稼働chronydのexe/hash・親mini_init・初期pid/mnt namespace・生成config一致を確認済み**。起動瞬間のlauncher履歴、PHC fd/UDP所有、11000の制限前制御量と持続する内部理由は未確定。基点f70cb37後の管理者ログはreader完了まで存在するが、外側collection.json/native終了値は欠落している。同時trackingと40秒trace、upstream4.1計算検証でtick11000/freq0がdriver補正限界−100000ppmと一致することを確認したが、内部状態/設定の確定ではない。限定reader/保存手順の実施→実体照合→必要時のPHC fd/UDP所有・include設定等の限定追加読取へ進む。PIDを再採取し、同名commや可視package既定configだけで所属を確定しない。原本は公開しない。 |
| 環境修復の具体化と時計資格 | **P1 / D3 / Sol** | 所有者特定後、host/guest同期責任、tick制御、Windows W32Time、影響範囲/切戻しを具体案にする。OS変更は今回未許可のため実行しない。service停止だけ、tick一回変更だけで解決にしない。承認された保守後にbracket採取/setter/再開後の観測と厳密future拒否を再検証する。T12等の実運用時間測定は時計資格確認後の新規attemptで行い、過去のp95等を現在比で割り戻さない。精度指標と経過時間を混同しない。 |
| T03の自然停止の原因 | **P1 / D3 / Sol** | 今回journal 9件成功、通常SIGTERM全phase/exit 130。未再発で完了にせず、再発時にphase/stack/実秒に対するMONOTONICの進行率を照合する。現在の進行率だけで歴史的10秒timeoutの原因を断定しない。 |

既存decision 9件/publisher 6件も成功。今回はproduction source/test/workflow/lockを変更せず、対象回帰24件と証拠照合を行った。全suite/新規CI成功はこの節目で先取りしない。T13のHTTP部分書込/応答喪失/実採否、T07/T17等の残件は維持する。一時trace/probeは削除し既存制御の前後hash一致を確認した。全試行と取得失敗を保存し、設定の自動変更やfuture拒否の緩和は行っていない。

**2026-11-01・基点0477e8cからの管理元照合:** [結果/原因と未確定事項/未実施の修復案](../261021/docs/clock-process-ownership.md)、[管理者向け限定読取](../261021/docs/clock-owner-admin-read.md)、[全試行/hash](../261021/docs/reviews/20261101-clock-owner-checks.json)。当該bootのBTFを読み、読取専用sched_getschedulerの対象taskを専用probeで対応付けた。Ubuntu timesyncd local 214がkernel 496であることをexe/開始tick等の前後一致と合わせて確認し、同区間で−3.197128772秒の補正要求成功を観測。chronyd 213は同区間で8回tick11000/freq0を成功させた。WSL init公式sourceのPHC用生成configと、system distroで見えるtime.windows.comのpackage既定fileを混同しない。現在のPHC0応答を稼働exeの認証ともしない。

T03 journal 9件11.120 test秒とdecision 9件1.711 test秒が成功。自然停止は未再発で、元原因は未解決。timesyncd対応付けの初回は変動するstatus全体を比較して失敗し、差がcontext switch回数だけと確認して安定identityへ修正した別attemptを保存。readerの初回glob無効化、system環境のawk不在、analysis準備のcwd失敗、Windows管理者/policy拒否も保持した。修正版shell readerはUbuntu rootでtimesyncd 1件/chronyd 0件を返しexit42（不完全）、非rootは77。PowerShellは実parser成功、管理者経路のend-to-endは未確認。全suite/PG/実配信は再実行せず、時計資格と実Capitalの合格を先取りしない。OS設定変更・future緩和/clamp/自動retryは未実施。次は外側processの限定採取→生成元と実体照合→影響/切戻し付き保守案をSolが判断する。


**2026-11-01・基点2181bfeからのchronyd追加調査:** [数値の意味・仮説・修復案/影響/切戻し](../261021/docs/chrony-control-analysis.md)、[証拠JSON](../261021/docs/reviews/20261101-chrony-control-checks.json)。管理者採取はまだ行われていないため、稼働exe/hash・親/namespace/生成設定の直接照合を完了にしない。現在のtimesyncd identityを再照合し−4.197671651秒の補正成功、chronyd213のtick11000/freq0成功10回、PHC0 tracking21応答を同じ観測期間で保存した。upstream4.1の総量は基準周波数+slew補正であり、tracking Frequencyと同値ではない。メモリstubだけのC検証9条件は成功し、driver限界との一致と条件付きの飽和経路を説明できたが、実base_freq/corr_freq/設定・制限前総量は未読。cwd引継ぎwarning付きの21queryも保存し、--cd /を指定した別queryはstderrなしで成功。前回113原本hash一致、専用probe回収/global制御hash不変/lost0を確認。OS修復・future緩和・実DB/registry/配信変更は行っていない。既存source/test/CI未変更で全suiteを再実行せず、T03歴史的停止・T13 HTTP障害・T07/T17・実候補の証拠作成は残す。

**2026-11-01・基点f70cb37後の管理者ログ照合:** [結果](../261021/docs/clock-admin-log-review.md)、[証拠](../261021/docs/reviews/20261101-admin-owner-checks.json)。ユーザーは管理者Trueで実行したが.ps1はpolicy拒否。対話readerは初期namespaceでchronyd213/exe hash/親1 mini_init /init、開始tick319、pid/mnt/timeとprocess rootのconfigを採取し、実行された完了行/両1件/error0がある。同boot/開始時刻と前回setterを照合し、可視4.1 binary hashと一致、configの全bytes hashがWSL StartTimeSyncAgentの生成文字列と一致した。親はdaemon reparent後なので過去exec launcherまで断定しない。collection.json/native終了値/QPCは欠落し、後から捏造しない。旧pipeline案内の終了境界の不足を記録し、追加採取時はdebug shell内でfileへ直接保存する手順へ修正（構文検証のみ、管理者end-to-endは未実施）。基本inventoryの再採取は不要。PHC fd/UDP所有と内部制御/修復資格、T03/T13の歴史的原因は残し、独立するT07/T17へ進む。

**2026-11-01・基点444acc9からのT07a/T17a:** [契約/未実施範囲](../261021/docs/deployment-recovery.md)、[全試行と証拠](../261021/docs/reviews/20261101-t07-t17-checks.json)。promote/recoverの共通lock、preparedのschema2旧state/旧・候補identity固定、承認者/理由/操作IDの反映、補償未確認時のrecovery_required、複数未完了の自動選択拒否、利用不可rollbackの事前拒否を実装。対象24件とharness安全性5件が成功。実Caddy/通常API/専用PGで並行同ID1行/異入力409、保存503、元予測再送、破損ready拒否、SIGKILL3地点、API停止、failed gate、別volumeへの5table/JSONモデル資料復元を確認し、最終専用resource残存0/既存container ID一致。model/registryは合成doubleで、実Capital/MLflow/skops producer証拠ではない。T17bの段階/経過/timeout表示・API側metadata・既存stateの旧承認情報診断、T07のCapital証拠契約/実MLflow-skops復元/現Dockerfile build/CI接続は残る。

最終production sourceに対する全364件は363成功・既存publisher一時SQLite試験1エラー（review 10:24:01.367182Z > 比較10:23:57.197071Z）。原本traceでUTC逆行と正方向MONOTONICを確認し、対象decision/evidence/test/diagnostic sourceが基点から不変と照合した。直前の中間source全361件は成功したが、最終suite成功へ流用しない。future拒否を緩めず全suiteのgreen目的再試行もしない。T03自然停止は未再発。通常sandboxのTestClient待機/300秒timeoutと、単独のstack付き25秒timeoutも保存し、host側同試験成功と区別。Docker初回はcached imageのtzdata欠落で失敗しcleanup済み、locked tzdataを一時workspaceへ複製して解決。既存image/venvは未変更。自動承認reviewのBad Requestで一回未実行となった起動も保存し、同じ限定操作の再review成功後に実施した。

T09は2026-11-01公式S3 HEADで9月200/10月404を再確認。T10前のbuffer待ちは継続し、fit/評価窓変更は未実施。T13aの使い捨てHTTP部分書込/応答喪失とCI、T16のCI保存先/allowlist固定およびlocal archive-only再計算は確認済み。T16はSol review修正と全399件、実装2492abcのCI artifact/hash/別process再計算/cleanupまで完了。次はT17b/T07の未実施受入とT14の説明整理を進める。T17b/T07残件と実運用時間の資格・実Capital採否は未達のままとする。

Lunaへの依頼には、T番号、対象commit、編集してよいファイル、入力/出力、成功・拒否ケース、検証コマンド、記録先を渡す。新しい閾値・評価期間・配信状態遷移・DB移行・依存lockの変更が必要と分かったら、その判断をSolへ戻す。これは担当作業の境界であり、通常の可逆な編集や既存手順の実行について新しい承認待ちを追加するものではない。

各taskはコード、必要な受入検証、実行log、結果/失敗/制限、計画の状態更新を一つの完了単位にする。既に同じ条件で確認した全suiteやsystem testは根拠なく繰り返さず、変更点または新しい懸念に適した検証を行う。確認済みの節目はリポジトリ共通指示どおり対象pathを指定してcommit/pushする。


#### T03の実装境界（2026-10-30）

要求別journalをplanの`capital-request-journal-v1`へ固定し、1,050要求の予定、送信直前、応答検証後、未確定結果をwrite/fsyncで保存する。HTTP時間はtransport/JSON読取だけで固定し、検証・journalを含むループ全体時間を別記する。HTTPX timeoutは接続・読取・書込・pool待ちごとで、要求全体の厳密な10秒deadlineではない。SIGTERMはmain threadで中断扱い、SIGKILL後はreportなしでもjournalを読取照合する。新policyのlocal report再計算・registry記録・bundle公開前にjournalの全応答・hash・要求別時間の一致を要求する。

このT03は「測定の中断を追えること」の実装・人工障害の検証であり、実Capital候補の精度/運用合格ではない。使い捨てHTTP/DB fixtureは学習済みAPIや実Caddyを提供しない。T07/T17の配信途中停止・復旧metadata、T12の実候補計測、T13の実採否は残る。journal原本の再計算可能なCI archive保全はT16へ含める。remote bundleはoperational report内のjournal descriptorに結ばれるが、現行11報告書bundleへjournal原本自体は追加していないため、原本をremoteから再読込する検証を今回の完了条件に含めない。


### 2026-11-01 T13aとT07a/T17a CI追跡

f341e94の[CI 41298859518](https://github.com/kenobix/Claude_practice/actions/runs/41298859518)で全369件（44.681秒）、既存合成Docker/各PostgreSQL/cleanupが成功。artifact ZIP SHA87ac2b15…と9 memberを照合した。新復旧harness自体のsystem-v6はlocalでありCIへ未接続。CIの合成1050件/p95=33.469804999981534msはblocked/not_evaluatedを維持し、要求別原本がartifactにないためartifact単独再計算はT16に残す。local全364件のUTC自然拒否は取り消さない。

T13aは新規scripts/test_capital_decision_http.pyで、private loopback MLflow/SQLite/実REST、障害proxy、既存決定関数と配信gateを使用。合成証拠と事前固定した歴史的時刻を使い、時計validatorやproducerは未変更。review/approval部分書込、両最終tag後の応答喪失、同一hashへの明示retry、逐次/並行競合拒否を確認。新guard7件、既存決定/証拠30件、最終HTTP5ケース、実TERM中断後のserver group/proxy/temp回収が成功。初回import失敗とREST source拒否も保存。CLI/実モデルartifactのHTTP取得・実候補採否・時計修復の証拠ではない。CIへbounded step/logだけを追加し、実行結果を別の追跡記録へ保存する。

T16の[固定仕様](../261021/docs/t16-luna-handoff.md)に従うlocal合成原本archiveとarchive-only別process再計算は実施済み。Sol review修正・再検証と実装2492abcのCI artifact/hash/再計算/cleanup確認まで完了。[証拠](../261021/docs/reviews/20261102-t16-ci-checks.json)。独立次作業はT07の実MLflow/skops対応snapshotとT17bの段階/timeout表示・既存metadata読取診断。時計追加採取はPHC fd/UDP所有/生成include/制限前制御量へ限定し、基本inventory再取得を前提にしない。OS修復は具体案/影響/切戻しを別扱いとし今回適用しない。実データT09は202610原本404の外部待ち、T10は新規fit前に必須。


T13a CI追跡完了: 実装f1cc9ff、run41300378098の全376 tests（51.768秒）と2 jobs全step成功。新capital-decision-http-ci.logがartifact13080624977の10 memberに含まれ、5ケース/4障害各1回/読戻しと同一retry/競合拒否を照合。proxy/server/group/temp回収を確認。ZIP SHA0d9dce8eae65f263c2349011ca5594e9b21fc830a7776b582c4ac376c5988f5aとmember SHAも一致。T13aの範囲で完了とし、T13b実採否・時計資格・T16原本archiveは未完了。記録のみの後続commitを、この実装CIで直接試験したcommitとは呼ばない。

2026-11-02 T16追跡完了: 実装2492abcのCI41442578156で399 tests/51.854秒・両job全step成功。artifact13140303925の外側SHA9d105b72…と全13 member、内側archive SHA b0a3ea70…/manifest+4原本を照合。archiveだけを渡した別processと別のnearest-rank計算で1050応答/50 warmup除外/1000 measured/p95 24.62401799999725msが一致し、gate not_evaluated/permission blockedと専用project cleanupを確認。時計逆行は今回のfetchでも再観測し資格未達のまま。T07/T17b・T14・T09以降の実証は独立残件とする。後続の記録commit自身を、この実装CIで試験したcommitとは呼ばない。


### 2026-11-02 基点69c3a5a以降・T07b/T17b部分と次の順序

[対応復元契約](../261021/docs/deployment-recovery.md)、[配信診断の操作/制限](../261021/docs/deployment-observability.md)、[検証索引](../261021/docs/reviews/20261102-t07b-t17b-checks.json)を参照。
実MLflow/skops登録版とregistry DB・監査DB・artifactをquiescent pairとして採取し、別project/3volume・ランダムloopbackポートへ復元するlocal v2が成功。全table内容・column/sequence・全artifact hash、破損skops拒否、同ID再送と新ID推論、read-only PG診断、cleanupを確認した。v1の同ポート引継ぎ時EADDRINUSEと失敗時cleanupも保存。停止直後のsocket残存/TIME_WAIT等は仮説で、変更元を特定したとは呼ばない。
通常CLIにはstage/elapsed/診断JSONLを追加。Docker build600秒/他30秒のtimeoutを「daemon未確定」としてpending/recovery_requiredへ残し、自動で補償成功としない。旧stateはro DBでprofileを先に照合し、対応completed行/未完了/legacy/metadata差異を診断する。自動移行・承認には使わない。
418件の全unitと追加対象検証が成功。最終補強の全体確認は実装commitのCIで行い、CI/artifact/cleanupの確認前には今回部分のCI受入完了としない。OS・時計・実DB・実registry・現配信、writer/schema/依存lockは変更していない。UTC/MONOTONICの区間差は継続し時計資格は未達。

| 優先度 / 難易度 / 担当 | 次の受入条件 | 状態 |
|---|---|---|
| **確認済み / D3 / Sol** | T07b今回の実装commit CI: paired_restore成功、全table/artifact hash、別volume、破損拒否、同ID/新ID、両project cleanup、T16の3原本archive照合 | 2414b20のCI41462429218で全条件成功。実Capital producerは含めない |
| P1 / D3 / Sol | T17b残り: APIの操作ID/承認者/理由を固定identityと台帳へ結合し、通常完了/中断recover/legacyで照合。registry/artifact取得を子processと全体deadlineで監視し、中断・遅延完了・時計逆行でも未確定を保つ | 独立して着手可。今回のheartbeatだけで全体timeoutとしない |
| P1 / D3 / Sol | T07a復旧harnessのCI接続: 使い捨て現Dockerfile image、所有検査、SIGTERM時cleanup、実Caddyの3停止点/rollback/recover・5table復元のartifact照合 | local受入は既存。CI側に古いcached imageを要求せず、実stackのtagを上書きしない |
| P1 / D3 / Sol | T03自然再発のphase/stack照合、chronyd制限前制御量・PHC/UDP/includeと時計資格の調査 | 設定変更やfuture拒否緩和を行わず、未再発を修復扱いにしない |
| P1 / D1 / Luna | T09公式202610原本の公開後取得・bytes/SHA/schema照合 | 2026-11-02の新規HEADで404、9月は200。download/fit/評価窓変更は未実施 |
| P0（fit前） / D3 / Sol | T10独立protocol固定 → T11同一candidate一度評価 → T12監視/運用/復旧producer → T13b実採否 | 原本/T02等の依存条件を先に確認。既評価Q3/Q4や合成成功を再利用しない |
| **今回整理済み / D1 / Luna可** | T14のT01〜T17→実装commit→CI→artifact/log SHAと5分説明。今回の実装CIも反映 | 既存索引9件のSHA/commit/run参照を照合。以後は新実証の完了後に更新 |
| P2 / D2 / Luna→Sol review | T06aの実file権限/busy timeout補足 | 使い捨てSQLiteだけで確認。writer/schemaは変更しない |


**2026-11-02 T07b CI確認完了:** 実装`2414b2031dd21cdf425761b1cd29d43c7a355f66`の[CI41462429218](https://github.com/kenobix/Claude_practice/actions/runs/41462429218)で419 tests/54.813秒・両job全step成功。[CI索引](../261021/docs/reviews/20261102-t07b-ci-checks.json)。artifact13150206991の外側ZIP SHA4c010bcb5591…/13 member、内側T16 archive SHA c6ca847386fe…/4原本/別processの1000 measured・p95再計算が一致。paired restoreはregistry65tableとaudit5table（744観測/1052予測）、8 artifact、column/sequence、破損skops拒否、同ID/新ID予測、別3volume、read-only PG旧state診断、残存resource0/workspace回収、元project cleanup/保護ID不変まで確認。T07bの合成対応復元受入は完了し、T17bは段階/timeout/旧state診断の部分完了に留める。
T14は今回checkpointのT01〜T17対応表と説明導線を整理し、過去索引9件のrecord SHA/実装commit/run/artifact SHA転記を検証。旧CIを再取得/再実行したとは呼ばない。次のSol作業は[T17b/T07a引継ぎ](../261021/docs/t17b-sol-handoff.md)のAPI metadata/全体deadlineと現image・signal cleanup付きCaddy復旧CI。未完了項目を可能な限り多く独立して進め、実producer/時計資格/実採否は条件未達のまま保持する。後続の記録commitをこの実装CIの試験対象とは呼ばない。


#### 2026-11-03 T17b/T07aの実装と残件更新（基点0472111）

[原本・試行索引](../261021/docs/reviews/20261103-t17b-t07a-checks.json)、[操作・deadline仕様](../261021/docs/deployment-observability.md#2026-11-03-t17b-操作の照合とcli全体の待機上限)、[全作業ログ](../261021/work_log.md)を参照。以前の未実装記述は各checkpoint時点の履歴として保持する。

| 優先度・難易度 | 現状 / 次の条件 |
|---|---|
| P1 / D3 / Sol | T17b: API/state/台帳の特定操作IDと固定artifact/申告metadataを照合。新schema3は不一致拒否、legacyは照合不能を保持。全体MONOTONIC900/300秒、TERM/INT/KILL・子孫cleanup・遅延HTTP・commit後応答喪失を実装/合成検証。製品・OSの厳密リアルタイム保証や認証済み承認ではない。最終対象試験と実装commit CIを追跡 |
| P1 / D3 / Sol | T07a: 現Dockerfile固有image、normal切替・3停止点recover・5table別volume復元・TERM cleanupをlocal確認。CI normal/TERMとlog/summary allowlistを追加。実装commitのartifact/member hash・両run cleanupを確認するまでCI受入未完了 |
| **完了 / D2 / Sol** | T06a S13補足: chmod000/実busy timeout5秒/解除後matched、DB bytes/論理行/CSV/JSON/marker不変。実行scriptを追加。一般filesystemや実Capital証拠ではない |
| P1 / D3 / Sol | 時計管理元・chronyd内部制御理由の未確定事項と、T03自然再発時のphase/stack追跡。修復は別途明示承認後。未再発・合成成功を時計資格にしない |
| P1 / D1 / Luna | T09: 2026-11-03も公式202610 objectはHEAD404、202609は200。公開後に取得/原本照合。202610なしでbuffer/評価窓を縮めない |
| P0実fit前 / D3 / Sol | T10→T11→T12→T13b: 未使用protocol固定、実候補一度評価、同じartifactの監視/復旧/運用測定・採否。T02実producer/時計資格等の前提を満たすまでblocked |
| P2 / D1 / Luna可 | T14: 新実装commit/CI/artifact/hashを証拠対応表とdemoへ反映。CI確認後に追記 |

OS・時計・実DB/registry・現配信・future拒否・schema/writer/依存lockは変更しない。実候補やT02 producerの完了を合成fixtureから作らない。

2026-11-03追加のCI診断: ec71c8a/CI41558409256は全445件とT07a normal/TERMが成功した一方、T16 exportのstring scannerで拒否され全体不合格。原本cleanup済みで自然拒否fieldは未取得。CPU名の`@`をURI資格情報と誤分類する同じ拒否を固定fixtureで再現し、authority/bare credentialsと通常hardware説明を区別した。秘密URI/絶対path/外部URLの拒否とbyte-preserving契約は保つ。**当該失敗時点の停止条件（P1/D3/Sol）: 修正後の同commit CI・artifact検算/cleanup確認までは最終受入未完了**。最新状態は後続の最終CI確認節を参照。過去T16完了索引は当時の証拠として保持し、自然失敗fieldの未採取は後続索引にも限界として残す。


<a id="remaining-work-latest"></a>

#### 2026-11-06 fake固定周期/phase終端の合成受入後の残課題

最新のfake採取/原本結合は`a5c1605`の[CI42037278421](https://github.com/kenobix/Claude_practice/actions/runs/42037278421)で
専用30件を含む全549件/119.271表示秒・両job全stepが成功。
[最終CI索引](../261021/docs/reviews/20261105-clock-acquisition-ci-checks.json)で16 artifact/hash・archive-only再計算・cleanupも受入済み。
合成raw代表はlocal ignored原本へ保存し、そのCI artifactへ含めたとは主張しない。実時計資格/独立源の認定は未達。

先行の全campaign実装`f6b3cad`の[CI41939041432](https://github.com/kenobix/Claude_practice/actions/runs/41939041432)は
最終37件を含む全519件/79.058表示秒・両job全step成功。
[全campaign最終CI索引](../261021/docs/reviews/20261105-clock-campaign-ci-checks.json)で16 artifact member/hash・archive-only再計算・全cleanupも受入済み。
数値/構造のoffline照合だけで実時計資格をpassedにする経路はなく、実採取・修復・外部源/校正reviewは残件。

先行実装`f26b334`の[CI41845551048](https://github.com/kenobix/Claude_practice/actions/runs/41845551048)は全482件・全隔離system/cleanup成功。
[進行率component最終CI索引](../261021/docs/reviews/20261104-clock-rate-ci-checks.json)で、artifact16 member/hash・archive-only検算まで確認した。
設計とoffline componentの合成受入で、実時計資格・PHC/UDP所有・過去の動的原因を確定しない。

先行実装`f45733a`の[CI41603978704](https://github.com/kenobix/Claude_practice/actions/runs/41603978704)も全464件・全隔離system/cleanup成功。
[今回のCI索引](../261021/docs/reviews/20261103-chrony-runtime-ci-checks.json)で16member ZIP/digest・archive-only検算まで確認した。
新readerのinode境界と17件はCI受入済み。時計の24組の数値観測はlocal診断であり、CIを時計資格の証明にしない。

[最終CI索引](../261021/docs/reviews/20261103-t17b-t07a-ci-checks.json)の実装4a4474e/CI41559833990で全447件、T07aのnormal/TERM、T07b対応復元、16 artifact member/SHA・archive-only検算・全cleanupを確認した。T17b/T07aの実装・合成受入とT06a OS補足は完了。先行b5aa7c5のcold Caddy欠落CI失敗、legacy操作binding持越し、Compose設定伝播漏れの修正前失敗も保持する。T16のCPU名@誤分類は専用25件と同じ最終CIで修正を検証した。自然ec71拒否fieldは未採取で、最終CIのCPUはAMD EPYCだったことと区別する。これらを実Capital producerや時計資格の証明にしない。

| 優先度 | 難易度・担当 | 残課題と次の条件 |
|---|---|---|
| P1 | D3・Sol | 時計：前回initd setterの実exe/親/namespaceは照合済み。保存済みELFの[backend取得interfaceとSettimeofday CALL](../261021/docs/backend-time-path-review.md)も静的照合したが、新しいruntime応答/stackではない。新管理者ログなし。残りはPHC fd/UDPと現在identity、tick11000時内部値、backend/host時刻源・外部UTC差・同期責任、許可後の修復と時計資格。[資格計測設計v1](../261021/docs/clock-qualification-plan.md)と[offline進行率phase component](../261021/docs/clock-rate-review.md)を追加し、実装f26b334のCI482件/全system/artifact/cleanupまで受入済み。全campaignのpolicy/事前plan SHA結合・外部UTC/校正/identity/欠測を[offline照合](../261021/docs/clock-campaign-review.md)する実装f6b3cadをCI519件/全system/artifact/cleanupまで受入済み。数値/構造だけの成功でも資格はincompleteを保つ。[fake採取](../261021/docs/clock-fake-acquisition.md)で送信前intent/nonce/sequence、raw→identity/全JSON→offline CLI、負例/中断cleanupを実装。local28件の[初回索引](../261021/docs/reviews/20261105-clock-acquisition-checks.json)を保持し、[起動回収の追加索引](../261021/docs/reviews/20261105-clock-acquisition-startup-checks.json)で30件/起動時所有権移譲とmask復元/源予算を照合する。最終a5c1605の[CI549件/30件・artifact/hash/cleanup](../261021/docs/reviews/20261105-clock-acquisition-ci-checks.json)も受入済み。[fake QPC固定周期/phase deadline](../261021/docs/clock-fixed-schedule.md)を追加し、専用19件で遅延のcatch-up/観測延長拒否、欠測/中断と同phaseの既知違反保持、子process回収をlocal検証済み。[今回索引](../261021/docs/reviews/20261106-clock-scheduler-checks.json)へ初回16件と同phase負例の失敗/修正を別記する。実装commitのCI/artifact/cleanupは確認待ち。残りは[源選定review](../261021/docs/clock-source-readiness.md)に必要な独立UTC/elapsed参照と不確かさ根拠（未選定）、実採取器/host deadline/固定負荷、原本/実行真正性、同期責任review、別途許可後の修復/休止再開/全受入。非飽和traceを反復せず、過去の飽和/T03歴史的停止も未確定。initd全体を止めず、future拒否/clamp/retryの禁止は維持 |
| P1 | D1・Luna | T09の202610公式原本公開待ち。前回から約13時間後の2026-11-05 13:16:12UTC（JST11月5日22:16）の公式S3 HEADも404（[今回索引](../261021/docs/reviews/20261105-clock-acquisition-checks.json)）。公開後に既存手順で取得・bytes/hash/構造を照合。HEADだけで取得完了としない。今回の202212追加取得をT09の10月到着と混同しない |
| P0（実fit前） | D3・Sol | T10：独立protocol/全原本snapshot/分割/recipe/基準をfit前に固定。[左端bufferを202212に決定・取得し、202301との2file/379,310行を別parserでも監査](../261021/docs/t10-input-readiness.md)。原本範囲202212〜202610、target窓とrecipeは不変。保管46件/予定47件だが正式集約/manifest監査/凍結は未実施。10月bufferなしで窓を短縮せず、1か月bufferを母集団の完全性保証にしない |
| P1 | D3・Sol | T02実producerおよびT11/T12/T13b：同一の実candidate/artifactに精度・監視・復旧・metadata・運用測定を結合し採否を記録。T09/T10/時計資格等の前提を満たす範囲で順に進める |
| P2（上流変更時） | D2・Sol review | MLflow noloadの発生箇所/通常import/厳格警告負例と現行publisher6件は確認済み。最新3.17.2にも同指定があり、修正releaseまたはloader変更時に個別再検証。それまではlock変更/警告抑制をしない。[調査と報告下書き](../261021/docs/mlflow-orm-compatibility.md)。upload-artifact保守は前回CI受入済み |

T14の証拠入口・task対応表・操作説明は最新CIへ追記済み。T06a OS補足とT07a CI接続を残件一覧へ再掲しない。旧checkpoint表は当時の記録として保持する。

基点`24c30c0`以降の独立作業：[資格計測の設計v1](../261021/docs/clock-qualification-plan.md)と
[設計検証索引](../261021/docs/reviews/20261104-qualification-design-checks.json)。
以前は期間/許容範囲/欠測の扱いが未固定だったため、QPCによる60分×通常/再開後試行、全bracket区間を使う進行率、
独立外部UTC区間、厳密future拒否、失効条件を事前方針として記述した。
境界10件・不正bracket5件・独立列挙192組の算術検算は合成設計だけの証拠で、製品unit追加や時計の合格ではない。
T09は前回HEADから短時間のため再照会せず、最新の実観測は13:42:25UTCの404を保持する。
新しい管理者ログ/自然再発の入力はなく、追加trace・原本再検査・fit・窓変更は行わない。

同turnの次の節目（基点`61643ac`）：[保存済み進行率phaseのoffline照合](../261021/docs/clock-rate-review.md)を実装。
QPC基準で固定した30分/60秒窓を選び、MONOTONIC/RAWのbracketを厳密算術で判定するcomponentである。
`rate_component_status`と常に`not_evaluated`の`clock_qualification_status`を分ける。
全campaign判定・QPC校正/外部UTC/identity・採取器/負荷生成器は残件。実採取/設定変更/実fitはしていない。
専用試験・回帰の結果と限界は後続のwork log/検証索引へ記録する。
localは専用13→16件、sandbox中断を保存後の承認付き全480件、IO guard追加後の最終専用18件が成功。
全480件を最終482件の実行と読み替えない。[進行率component検証索引](../261021/docs/reviews/20261104-clock-rate-checks.json)。
実装記録時点ではCI未確認だった。最終受入はf26b334/CI41845551048、
[後続索引](../261021/docs/reviews/20261104-clock-rate-ci-checks.json)の全482件/両job全step成功・artifact16 member/SHA・archive-only再計算・全cleanupで確認した。
後続文書commitをこの実装CIが直接試験したとは呼ばない。

891a420以降の[検証索引](../261021/docs/reviews/20261104-backend-boundary-checks.json)では、
静的PIE解析の初回exit0不採用・maxpc/etext assert失敗、テストcwd誤りも保存した。
最終静的照合/合成改変5件拒否、左端2parser照合、既存pipeline回帰8件は成功。
製品source/schema/writer/lock/workflowは変更しておらず、全suite/CIを新規受入と呼ばない。
今回の新しい完了単位は保存ELFのbackend経路reviewと左端buffer判断・取得・境界監査まで。
PHC/UDP管理者採取・時計修復/資格、10月原本、T10正式protocol、実producer/採否を未完了として維持する。

2026-11-04の後続確認（基点`1e622dd`）は[追跡索引](../261021/docs/reviews/20261104-followup-checks.json)を参照。
公式S3への権限付きHEADは13:42:25UTCに202610 object=404。通常sandbox試行はDNS解決失敗(exit 6)であり、別試行の404で補完した。
公開後取得は未実施。管理者PHC/socketの新規採取directoryは見つからず、tick11000もT03も自然再発していない。
時計の未確定事項を閉じず、T10正式aggregate/protocol・fitにも進んでいない。依存前提に変化がないため、それ以外の独立タスクもこの確認では新規受入していない。

2026-11-03・基点dcfdd9c以降の追加読取：[時計調査索引](../261021/docs/reviews/20261103-clock-followup-checks.json)。
同bootのtimesyncd identityを再対応し、専用40秒traceでchronydのtick/freq8指定（11000は3件）、
initdとtimesyncdの両方の負方向stepを確認。前回区間のinitd前進だけという記録を今回全区間へ一般化しない。
別のjoint採取でreviewが1.235856秒futureとなる自然拒否とtimesyncdのstepを生成/検証MONOTONIC区間内で照合し、tag不変/blockedを確認した。
T03 journal9件・decision9件は成功し、自然停止は未再発。PHC/socket補助reader16件はfixture受入済み、Ubuntu rootでの実行は対象が見えず42。
初期namespace本採取はWindows管理者操作待ちで、[手順](../261021/docs/clock-owner-admin-read.md#chrony-resources-admin)を追加した。
可視ELFのMariner2.0/4.1 package noteとBuild IDは取得したが、内部変数symbol/実数値は未取得。
T09は同日06:02 UTCの公式HEADで202609=200/202610=404。原本取得・fit・窓変更・時計資格の完了へ流用しない。

同日追加の独立P2：`7174a1f`の時計readerを含む実装`5a687a7`の[CI41598271089](https://github.com/kenobix/Claude_practice/actions/runs/41598271089)は全463件64.677秒・全system成功。
実artifact13214080832は16member、外側SHA`f02bdd44b3082142f0f5cc38ef7b384cf52b9b744c02d1ddf482a278874a8789`がGitHub digestと一致。
内側archive4原本hash・暖機50除外/要求1000/p95の別process・独立再計算、T07a正常/TERM・T07b対応復元・PG/HTTP cleanupを確認した。
Node20強制移行/punycode/url.parse警告は今回system logで見られず、unitのMLflow noload警告は別の残件。
T14として時計/CIの用語16項目も補足した。後続記録commitをこの実装CIが直接試験したとは呼ばない。

2026-11-03・298c6c8以降：追加管理者ログは未採取とユーザー確認。T09の公式08:02UTC HEADも202610=404（202609=200）。
時計は[配布debugと内部数値の照合](../261021/docs/reviews/20261103-chrony-runtime-checks.json)を追加した。
完全debugのBuild ID/CRC、compile unit/型、稼働inodeを照合し、計算とkernel入力が一致する非飽和16組を確認した。tick11000が出るまでの延長/retryはせず未再現を保持する。
exe dev/inode/sizeの前後比較を追加readerへ接続し、同bytesの別inode置換も拒否する専用17件が成功。local索引のCI_PENDINGは当時のsnapshotとして保持し、最終受入はf45733aのCI索引を参照する。
T03 journal9件/既存publisher6件成功。MLflow警告は[独立調査](../261021/docs/reviews/20261103-orm-compatibility-checks.json)で上流修正待ちへ整理した。

次は追加管理者ログがあればPHC/UDPと現在identityを照合し、tick11000が自然再現した区間で内部数値とkernel書込を対応させる。
新しいログ・状態変化がない間、同じ非飽和確認や未修正releaseの確認だけを繰り返して進捗扱いにしない。
依存条件を満たす独立課題は進めるが、原本/修復許可/資格/必須producerを未取得のまま実fit・運用合格・採否へ進めない。

2026-11-05追加review：[全campaignの欠測負例索引](../261021/docs/reviews/20261105-clock-campaign-missing-checks.json)。
QPC1件欠測時にも既知の正確な固定端点の逸脱と提出UTC区間の上下逆転を保存する補強を追加した。
不足は補完せず、資格全体をpassedにしない。最初の実装と補強後を別commit/CIとして区別する。

全campaignの最終受入はf6b3cad/CI41939041432。専用37件と既存phase18件、CI全519件/全system、
16 artifact/hash・archive-only検算・Caddy通常/TERM・対応復元cleanupが成功した。
採取protocol契約は次の実装の設計で、採取器・外部reader・負荷生成器の完成や実資格を先取りしない。

基点`002718e`からの次の節目：fake host/外部reader（固定subprocess）の合成protocolと
4ファイル原本を実装し、hash/順序/nonce・identity/校正区間を別processで再計算する。
初回reviewで応答待ち要求が原本へ残らない欠けを回帰1件で再現し、送信前intent保存と応答時の同一bracket照合を追加。
partialを正常markerで覆わず、実sourceの認証/独立性を自己申告flagで通す入口も作らない。
新管理者採取/自然T03・飽和時の入力なし。追加traceや基本inventoryの反復はせず、T09は1回のHEAD以外に原本再検査/GET/fit/窓変更を行っていない。

追加review：reader構築例外/代入前signalの回収漏れ2件を人工再現後に修正。
最終専用30件とsource-v5原本結合はlocal受入済みで、初回df3aeb4の28件/CIと区別する。
源選定前の14仮定条件を計算し、理想条件でも60秒参照の各端点不確かさ半径は約3ms未満が必要と整理した。
未選定の源に精度/真正性を付与せず、実資格/fit/採否の前提は維持する。

起動回収補強後の最終a5c1605/CI42037278421を受入済み。
sourceは記録更新時も同commitから不変と照合し、記録だけの後続commitをこのCIで直接試験したとは呼ばない。
既存16 artifact/hash・T16 archive-only/独立p95・PG/HTTP/Caddy normal/TERM・対応復元cleanupを確認した。
fake QPC deadline制御、実source/collector/固定負荷/修復・全時計資格、T09以降の実証は引き続き前提つき残件。

2026-11-06・基点89c5443：fake QPCの固定周期/phase deadlineをopt-inで追加。
旧raw/CLIの互換経路を維持し、予定/仮想観測/送信前intent/完了/欠測/損失計数をplan/source SHAへ結合する。
late wakeをcatch-upせず、phase起点/終端もずらさない。遅延応答の原本とbracketは残し、共有終端の不足を両phaseへ伝える。
初回16件成功後、欠測とidentity違反を同じphaseへ要求する試験で注入位置の欠けを再現し修正。
最終専用19件が成功。部分原本は数値reportへ進めず、途中の既知違反をrawへ残す。
独立源/真正性/不確かさdossierに新入力はなく、実hostのdeadline-aware IO/実負荷/修復/全資格は未完了。
新管理者採取や自然T03/飽和時の入力もなく追加traceなし。T09は新しい公開情報がなく、短時間の反復HEAD/GET/fit/窓変更を行わない。
