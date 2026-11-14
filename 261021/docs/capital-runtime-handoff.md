# 次のSol作業：同じ実候補の隔離runtime/producer受入

2026-11-14追補：[実runtime受入](capital-runtime-acceptance.md)と[証拠索引](reviews/20261114-capital-runtime-checks.json)まで完了。
現在PCの専用project capital-acceptance-20261114、registry5001/version1、DB55434、API8110。
入力/候補/正式消費台帳は維持。data/evaluationsは専用directory＋consumption-ledger.jsonだけ正式台帳へのsymlinkへ変更済み。
次は同じfrozen planの測定と対応復元・metadata。以下の開始前記録は当時の経緯として参照する。

2026-11-14。T10直前照合とT11は[実候補評価](capital-first-evaluation.md)まで完了。
[offline監視診断](capital-offline-monitoring.md)には警告がある。T02/T12全体は未完了で、T13b採否はblocked。
[作業別の開始条件](product-completion-policy.md)に従い、時計研究は保留を維持する。

## 続きを行う実体

- source/venv：repository内`261021/data/reports/20261114-product-resume/project/`と隣の`venv/`。
- candidate ID：`e2df57935e8f3fcdea05da0036c786537bd130e461d6a05eed5104066d3a981b`。
- model SHA：`1e17fdb0d48c978dbf35c72125ca897bbe42882e24d3d647795ab0171bbf0ce2`。
- selection/finalのpath/hashと正式ledgerは初回評価記録と検証JSONにある。両窓は消費済み。再fit/再採点による選び直し禁止。
- 固定snapshot：`261021/data/derived/capital-bootstrap-a5b76d0f89d6433287007f3414d2208b/`。
- runtime用入力は2026-04-01〜04-08未満の168時間。既存`benchmark_capital_runtime.py --check-input-only`が実候補で成功。
  05-25〜06-01の168時間はラベル未到着の末尾2行を含むため、実候補CLIが予測/DB接続前に拒否した。

## 入力pathに関する注意

profileの既定`data/derived/capitalbikeshare-next`は元checkoutでは旧CSV（SHA cc4b39…）のまま。
旧defaultのhashが今回の候補と違うので、そのままstaging seedやbenchmarkへ進めない。
source/config/lockを変えず、**今回の専用projectだけ**の`data/derived/capitalbikeshare-next`を固定snapshotへのsymlinkにした。
元checkoutの旧pairは変更せず前後SHAを照合した。専用projectの`data/derived`はもはや元derived全体へのsymlinkではなく、
固定snapshot名と上記default aliasだけを持つdirectory。正式ledgerは元の単一pathを共有したまま。
このaliasは候補の実行環境の結合であり、旧成果物の置換や新しいデータの再集計ではない。
移設時はaliasの解決先と全bytesを再照合し、sourceを変えて候補を作り直さない。

## 次に必要な実証（優先順）

1. 実行先を確認。デスクトップを優先するが、現在のチャットは旧hostで、接続先は未提示。
   接続不能でもsource/candidate/入力/台帳の整合など独立した作業を進める。新しい時計採取/精密校正へ戻らない。
2. 専用MLflow・PostgreSQL・API・volume/image/Compose projectを用意し、現配信/実registryとの分離を確認。
   製品CLIはlocalhost registry5001、DB55434、API8110を前提にする。占有されていたら既存サービスを停止せず、
   その実体を確認する。勝手なport緩和や既存registryへの登録をしない。必要なら隔離namespace/別PCの案をreviewする。
   デフォルトcomposeのnameは既存Capitalと同じなので、既存project名のままup/downしない。
3. このcandidateを専用registryへblocked登録し、exact source/artifact/report/tagを照合。固定pairから専用DBへseedし、
   staging APIのready・offline/API parity、retry/変更入力409、DB監査、通常deploy拒否を確認。
4. 登録同一artifactのtraining-only referenceで実monitor CLIを通し、SQLite/PG・CSV/JSON/complete markerの照合と、
   警告/欠測/到着遅延を記録。offline診断を実HTTP/監査証拠の代用にしない。
5. 通常の同期状態と短区間の順序/elapsed・strict future/timeoutを確認してから、固定planを保存し暖機50+逐次1000を実測。
   異常・中断時はその試行を保存して停止し、自動retry/clampや時計研究の再開をしない。
   当該PCの未精密校正の実測で、本番SLOや時計資格としない。
6. 同candidate/DB/artifactの隔離復旧・metadataを結合しT02/T12の不足を埋める。誤差/PSI警告の説明、
   理由付きreviewと別人承認が必要。現registry/配信を変更せず、T13b最終採否は証拠が揃ってから扱う。

デスクトップ側でユーザーに必要な最小操作は、そちらのVS Code Remote WSLでrepositoryを開き、
`hostname`、`pwd`、`git rev-parse HEAD`と実体の保存先を伝えること。SSH/ポート開放の導入は要求しない。
候補/行別証拠/原本はgitignore下なのでgit pullだけでは移らない。正式ledgerとともに私用領域へ移し、
path/hashを照合してから続行する。venvは別PCへそのままコピーせず、固定lockから専用環境を作る。

全試行/失敗/command/exit/hash/限界を記録し、可能な限り多くの独立課題を進める。
節目ごとに対象だけcommit/pushし、製品source/CI対象を変えたら実装commitのCI artifact/cleanupまで確認する。
