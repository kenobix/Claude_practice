# 2026-10-30 checkpoint差分レビュー

確認日: 2026-10-30（JST）。前回の正式レビューを記録した10dfb8fを基準に、070b033までの28コミット・65ファイル（8,060行追加、119行削除）を対象にした。前回レビュー内のコード確認終点は5df03f7である。10dfb8f以前の全作業は再監査していない。

結論: 入力固定・監査付き計測・隔離system test・CIの整備は計画に沿って進んでいる。一方、通常配信に使う承認判定には、証拠の結び付けと監視・復旧条件の強制に未解決の差がある。**実Capital候補のeligible化・通常配信より前にA01/A02を修正する。** 今回はレビューと計画更新を行い、製品コードは修正していない。

集計証拠は[checkpoint証拠JSON](20261030-checkpoint-evidence.json)、最新の実行順は[計画書11.6](../../../261014/plan_mlops.md#remaining-work-20261030)を参照。差分一覧、主要な正常/失敗経路のソース、保存ログ、CI artifact、合成負例を照合した。全追加行の形式証明や第三者による独立監査ではない。

## コミットと前回指摘の進捗

| 対象 | 差分内の主なコミット | 判定 |
|---|---|---|
| S01: 固定入力・DB・HTTP・監査 | 1401ebe、dd5a3c6、b3c0acf、a0e6041、9bf8649 | 観測snapshot lock、lag出典hash、1,050監査行、保存reportと数値再計算が合成systemで接続済み。実候補の証拠は未取得。 |
| S03: 測定planと試行台帳 | 34b7547、a81b524、a6c5c5d | 事前plan固定、開始前の永続記録、協調lock、理由付きretryを実装。要求ごとの強制終了時journalは残る。 |
| report点検・部分証拠・数値照合 | 748f2f8、8a8a458、42cfd60、c01b2d1、3035ab1 | 孤立reportを自動採用せず、pending IDのDB照会も不在を未送信と断定しない。実HTTP遅延commitとSIGKILLの通し検証は未完了。 |
| R07監視 | 63656ca、d2bb73b、4d9b543 | 期間継承とrunごとの保存を追加し、使い捨てPostgreSQLで保存を確認。PSI v2は部品試験のみで既存monitorに未接続。 |
| S04: 再実行できるDocker/DB試験 | 5f3ba9a、9b169e6、a1aac78、9bf8649、dd1741e、b290dc5、9692c4f、a0344f9、6079903、0cbcdbc | 最新Dockerfileを使う隔離harnessとCIを追加。cleanup不具合を検出・修正し、現在はCI成功。実Caddy切替・backup/restoreとは別の試験。 |
| S02: review/approvalと通常loader | 77fe955、dae897a、070b033 | candidate/version/artifact/operational reportを結ぶ決定hashと別名承認を追加。部分tag書込試験もある。A01/A02/A03が残り、配信前条件の全面実装完了ではない。 |
| S05/S06: 文書と承認順序 | 上記の各文書更新 | 11.5は進捗を追っていたが、11.3にはCLI停止等の古い記載が残った。今回修正し、監視/復旧を承認より先に要求する実装課題をP0へ上げた。 |

## 実行結果・CIの再照合

- 対象28コミットに関連する52 runは、GitHub表示でsuccess 50、failure 1、cancelled 1。9bf8649と9692c4fは個別のpush先頭ではないため直接runがなく、後続コミットのrunで検証された。「全コミットの全runが成功」とは表現しない。
- success表示だった40874109839と40874601329のartifactを再取得した。両方にTracebackがありcleanup_completeはなかったため、system全体の成功証拠から除外する。ZIPの実SHAはGitHubのdigestと一致した。
- 40876827423はpipefail有効後にcleanup失敗をfailureとして検出した。ログ内にimageが複数repositoryから参照されているというDockerエラーを確認した。先の2 runのartifactには同じ詳細文言はなく、過去の全失敗が同一原因だったとまでは断定しない。
- 最新070b033の[run 40966966672](https://github.com/kenobix/Claude_practice/actions/runs/40966966672)は221 tests、compileall、pip check、synthetic system、PostgreSQL snapshot-lock、cleanup、artifact保存が成功。221 testsは9.850秒。Pagesの[run 40966966169](https://github.com/kenobix/Claude_practice/actions/runs/40966966169)も成功。
- 最新synthetic candidateはad46f6f2ebbd984010153e613c418e9659a9fbb2fadc12ac4cfe554e8214deb0、744観測、1,050要求/監査行、offline/API差0、p95 31.070696000028875ms、numeric recheck一致。gateはnot_evaluated、deploymentはblocked。人工データとGitHub runnerの結果であり、実精度や本番SLOの証拠にはしない。
- 最新artifact IDは12938433488、ZIP SHA-256は40171b0546328a39b813a26815fc32036c248c18b75269167c4b9e9ee33afe79。GitHub digestと一致、ZIP integrityも正常。保存ログ2本を実際に開いて成功行を確認した。
- この監査ではdeployment系11 testsを再実行し1.420秒で成功。最新CIの全221件を確認済みのため、変更のない全suiteとDocker systemのローカル再実行は重ねなかった。pip checkとシェル構文確認も成功した。

## 指摘事項と残課題

### A01 / P0: 承認hashが全証拠を固定せず、runtime検証が負のp95を受理する

[capital_deployment_decisions.py](../../src/bike_mlops/capital_deployment_decisions.py)のreview/approval payloadはcandidate、version、registered artifact、operational report SHA等を含むが、final report SHAと運用plan SHA等の全証拠は含まない。fake registryでreview→approveした後、final_report_sha256またはcapital_operational_plan_sha256を別の64桁値へ変えても[require_deployment_eligible](../../src/bike_mlops/deployment_gates.py)は通った。変更後も決定hashは同じだった。

同じ合成fixtureで承認後のcapital_operational_p95_msを-1にすると、runtime用validate_capital_decision_tagsも通った。review/approveの開始時は負数を拒否するため、「最初から負数の候補をapproveできる」という発見ではない。承認後のmetadata不整合をruntimeが検知できないという問題である。

計画T01で証拠の必須項目・canonical payload・report本体・数値境界の検証を揃え、通常loader/runtime/promoteから同じ規則を使う。実registryの編集や実配信を試したわけではなく、ローカルの誤操作・metadataずれの検出要件に対する負例である。

### A02 / P0: 計画上の監視・復旧証拠をapproveが要求していない

review/approveの検証は精度・運用tagを中心にしており、監視・rollback/recover・backup/restoreの証拠を入力に要求しない。合成fixtureではそれらの証拠やreportファイルが存在しなくてもeligible判定まで到達した。report本体を読まないfixtureが通ることも確認した。

現時点で実Capital candidate reportはなく、この不足による実配信は確認されていない。しかし、前回S06で定めた「監視・隔離復旧の証拠を揃えてから採否決定」をコードが強制していない。T02で証拠schemaと依存順を確定し、欠落・不一致をapproveと通常runtimeで拒否する。時刻付き決定記録も必要。自己申告の別名は認証された別人を証明しないため、合成actor名を実承認者の代用にしない。

### A03 / P1: 通常model loaderのregistry URI固定が残る

[model.py](../../src/bike_mlops/model.py)とcliの通常registry clientはtracking URIだけを設定し、registry URIを明示しない。一方、今回追加したCapital preflight/seed/記録scriptは両方を同じURIへ固定している。

ローカルのMLflow clientコンストラクタだけを使い、tracking=localhost:5001、環境変数のregistry=localhost:5999とすると、registry先が5999になることを確認した。API呼出しは行っていない。現在のComposeで誤接続が起きているという観測ではない。T01に通常Capital経路の同一registry固定と、異URI環境変数の負例を追加する。

### A04 / P1: 強制終了と実HTTP timeout後の未確定要求は未検証

要求開始前のattempt記録は永続化されるが、runnerのpartial_evidenceはメモリ由来である。SIGKILLやOS停止では現在のpending ID・応答記録が失われうる。固定request IDのprefix/順序から調べられる範囲はあるが、最後に送信した要求の確定証拠とは別である。pending audit部品は失敗reportを前提に一件を照会する。

T03でjournalの範囲と計測時間への影響をpolicyへ固定し、使い捨てHTTP/PostgreSQLで応答timeout後の遅延commit、SIGTERM/SIGKILL、中断後照会を実証する。見つからない監査行を「送信されなかった」と断定せず、失敗attemptを最速の再実行結果で置き換えない。

### A05 / P1: PSI v2の接続と未確定monitor保存の照合が残る

[monitoring.py](../../src/bike_mlops/monitoring.py)は旧PSI関数を呼び、Capitalのprofileは固定monitor referenceがない場合にmonitorを拒否する。v2の部品実装は有効だが、Capital監視の完成を意味しない。

合成の定数reference=1、current=9の各20件で旧PSI=0、v2 PSI=27.630940223106876を確認した。T04/T05で候補のtraining reference・固定bins・algorithm versionを接続し、遅延ラベルや不足母数を別状態にする。T06ではmarkerなし/DBあり等を読み取り照合する。過去UCI値を置き換えない。

### A06 / P1: 既存REAL列への型移行のリハーサルが必要

今回の差分はpredictions.predictedをDOUBLE PRECISIONに変更し、Database.initializeで既存REAL列をALTERする。新しい使い捨てDBのsystem testは成功しているが、既存REAL表からの移行・既存ID再送・復元をまとめた証拠はない。

既存UCI DBをREAD ONLYで確認するとpredictedの型はrealだった。今回initializeやALTERは実行していない。T08で旧schemaを使った一時DB、丸めを含む過去値、同一IDの再送、backup/restoreを確認する。REALを拡張しても過去の丸め前の値は復元されない。

### A07 / P1: CI artifactはsummary中心で、外から数値再計算する材料が足りない

最新artifactはZIP 1,844 bytes、ログ2本である。harness内では1,050件のrecordから数値を再計算しているが、workspace削除後のartifactにはplan・ledger・要求別latency等が含まれない。このためartifactだけを取得した人がp95を再計算することはできない。

T16で合成データに限定した公開可能なplan/report/ledger/要求recordの許可リストを作り、credentialや.envを含めずcleanup前に保存する。CIログ成功は尊重しつつ、同じ処理内のrecheckと保存物からの独立再計算を区別する。

### A08 / P2: 作業ログのコマンド・件数と計画の古い状態を訂正する

- work_logの2026-10-30節は単独test_capital_deployment_decisionsを実行して11件成功と書くが、当該ファイルは7 tests、test_deployment_gatesが4 tests。今回2ファイルを対象とするglobで11件成功を再確認した。過去に単独コマンドで11件動いたという意味では訂正が必要。
- 220件testと221件testの記録が同じunit-tests-review-approval.logを参照する。現在のSHAは970a8bf...で221件のもの。220件時のcc910fc...のbytesは同pathに残らない。220件は当時のCIで補強できるが、元ローカルlogの同一bytesを復元したとはしない。
- R06本文に「実測CLI停止」「入力固定・tag記録は未実装」「実原本未取得」等の記述が残っていた。合成で完了した部分と実候補の未完了部分を今回分け直した。
- deployment decisionのfake registry testも公開関数のversion lockを通すため、既定のproject data下にlock fileを使う。registry書込とは異なるが、unit fixtureのファイル状態も一時directoryへ分離する保守をT15へ追加した。
- 今後のlogはcommit/UTC時刻/試行IDを含む新規pathへ保存し、pipelineの先頭失敗はpipefail等で検知する。履歴の誤りは訂正を追記し、当時の失敗や旧結果を消さない。

## 既存サービスと原本への影響

5 containerはrunning、PostgreSQLはhealthy、UCI入口はready 200・版3・artifact SHA 79f06eb9d5a6efc378f20ba37fe98a8367762c1cb6c699200152a121a2af4a43だった。checkpointの2026-10-28 13:23:39 UTC以降に見えるprediction新規行、deployment新規/更新行、monitor新規行は各0件。これは取得時のSELECT結果であり、削除済み行・すべての外部操作の不在は証明しない。

6〜9月原本4 ZIPは作業ログのbytes/SHAと一致した。canonical candidate reportは0件。10月archiveの404は前作業の10月30日HEAD記録を参照し、今回は再取得・再集約・fit・評価消費台帳更新を行っていない。以前の診断CSV/manifestは削除済みで、hashと集計値だけを履歴として確認した。

## ワークフローと検証方法の評価

小さなcommitと対象pathのstage、未使用評価窓をデバッグへ使わない判断、合成環境の所有確認・cleanupは妥当である。最初のsuccess表示をartifactまで見て訂正し、pipefailとexact image tag削除へ修正した流れも適切だった。

一方、テスト成功件数が増えても、A01/A02のような計画と受入条件の差は残りうる。以降は先に必須証拠・状態遷移・拒否ケースを表にし、Solでreviewした固定仕様に沿って実装する。Lunaには範囲が決まったfixture、読取照合、資料整備を割り当てる。通常sandboxでTestClientが止まり権限付きで完了する観測はあるが、特定syscallだけが原因と断定せず、実行環境差として記録する。

今回の監査で、モデルの変更・別モデルへの自動委譲はしていない。モデル分類はプロジェクト上の推奨であり、このrepoを両モデルで同条件比較した実測ではない。

## 主な確認コマンド

A01/A02のprobeは次のように再現できる。261021から実行し、test fixtureのfake clientだけを使う。実registry、report、DB、配信状態は操作しない。

~~~bash
.venv/bin/python - <<'PY'
from tests.test_capital_deployment_decisions import eligible_registry
from bike_mlops.capital_deployment_decisions import (
    _record_operational_review_locked, _record_deployment_approval_locked,
)
from bike_mlops.deployment_gates import require_deployment_eligible

for change in (None, ('final_report_sha256', '9'*64),
               ('capital_operational_plan_sha256', '8'*64),
               ('capital_operational_p95_ms', '-1')):
    client = eligible_registry()
    _record_operational_review_locked(client, '2', 'fixture-reviewer', 'passed', 'synthetic only')
    _record_deployment_approval_locked(client, '2', 'fixture-approver', 'synthetic only')
    if change:
        client.tags[change[0]] = change[1]
    require_deployment_eligible('capital-bikeshare', client.tags,
                               model_name='bike-demand-capital', model_version='2')
    print(change, 'accepted by examined 070b033')
PY
~~~

上記は監査対象commitでの不足を再現するprobeであり、修正後も通すべき正常条件ではない。修正時はT01/T02の拒否テストへ変更する。

~~~bash
git log --reverse --format='%h %aI %s' 10dfb8f..070b033
git diff --stat 10dfb8f..070b033
gh run list --branch main --limit 100 --json databaseId,headSha,status,conclusion,workflowName,url,createdAt
gh run view 40966966672 --log
gh api repos/kenobix/Claude_practice/actions/runs/40966966672/artifacts
gh api repos/kenobix/Claude_practice/actions/artifacts/12938433488/zip
.venv/bin/python -m unittest discover -s tests -p 'test_*deployment*' -v
.venv/bin/python -m pip check
sh -n scripts/bikectl
~~~

gh操作はrepo root、Python/test操作は261021で行った。raw CI ZIP/log、合成probe結果、READ ONLYのDB/runtime JSONはGit対象外のdata/reports/review-20261030へ保存し、hashと判定を証拠JSONへ転記した。DB確認はBEGIN READ ONLY内のSELECTのみ。4 ZIPのSHAも再計算した。secret/.envは公開証拠へ含めない。

更新した7 Markdownの相対リンク85件、新規の明示anchor、T01〜T17の表構造を検査し不一致0件。HTML renderはローカルにMarkdown parserがなく未実施で、新しい依存は導入しなかった。公開時のJekyll buildはpush後のPages CIで確認する。
