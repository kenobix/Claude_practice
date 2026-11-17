# 受理commitの固定取得と検証

状態: **主ブランチへのfixture公開・固定取得検証・一件Worker接続は実装済み**。

## 2026-11-18の公開・取得検証

[PR #4](https://github.com/kenobix/Claude_practice/pull/4)のマージを確認。
`origin/main` のsnapshotは `3eb342a89fc0eb990c0a48a79dbea42e8be44283`。
同commitの固定pathは通常ファイル（100644）、2151 bytes、blob `a2352427ae73ed9337e933d1dcefd49968b3fe8b`。
SHA-256は下の承認候補と一致。実GitHub HTTPSで同snapshotの取得成功と、許可mainに
属さないPR #4の元commit `97e8381ed94a7f7dd87e322616ad8db6227185f3` の拒否を確認した。

`ci_runner.commit_source` は公開GitHub REST APIを利用する。固定repoのmain refを一度解決し、
異なる受理SHAは固定SHA同士のcompareでbaseとmerge baseが受理SHA、statusがahead、behindが0の
場合に限る。その後、受理SHAのGit commitと、固定pathを構成する非再帰tree、blobをIDでたどる。
通常ファイル100644・承認済みサイズ・SHA-256・Git blob IDをすべて照合する。
main所属とcommit/treeの関連付けはGitHub APIの応答をHTTPS上で信頼する。取得したblobのbytesはローカルで検証する。
[GitHub Compare API](https://docs.github.com/en/rest/commits/commits#compare-two-commits)。

curl 8.4以上で各応答1 MiB、各リクエスト5秒、取得全体30秒を上限にする。HTTP 200のみ受理し、
リダイレクト・圧縮展開・ページの追加取得は行わない。大きなtreeやcompare、レート制限、通信異常は
取得拒否になる。sourceはメモリで保持し、ファイル展開・checkout・実行を行わない。
[curlの応答サイズ上限](https://curl.se/docs/manpage.html#--max-filesize)。

取得対象は通常の初回claimと同じ許可条件に合うキュー先頭。DB transactionは通信前に閉じ、
取得後に別transactionで先頭のrun IDとcommit SHAを再照合する。消失、競合取得、別runへの変更は拒否。
検証結果は不変のsource bytesとrun ID、受理SHA、main snapshot、blob IDを含む。この再照合は予約ではない。
一件Workerは検証済みのbytesで固定imageをbuildした後、同じrun ID/SHAを条件付きでclaimする。
既存の制限・lease・fencingを維持し、正式結果と同じtransactionでsourceとimageの由来を保存する。
[実行手順と停止時の挙動](fixture-worker.md#受理commitの固定fixture経路)。

## 公開前の調査記録（2026-11-17）

観測時刻: 2026-11-17T14:16:07.832500+00:00。

| 対象 | 結果 |
|---|---|
| 作業HEAD | aaadaa77c703925e1ec7ebcac5153a1d7ae1fdc5、feat/ci-runner、開始時clean |
| 現在の許可設定 | kenobix/Claude_practice、refs/heads/main、fixture-v1 |
| repository属性 | GitHub APIでpublic、default_branch=mainを確認 |
| GitHubのmain HEAD | b4c460a52d5afc4d3623a35cc99655095d3a8e1b、committer date 2026-11-16T15:22:39Z |
| local origin/main | 同じSHA。ref更新やcheckoutはしていない |
| mainのP1 fixture | contents/261117/ci_runner/fixtures/job/fixture.py?ref=b4c460a52d5afc4d3623a35cc99655095d3a8e1b → HTTP 404 |
| 作業HEADの同path | Git追跡済み、2151 bytes、SHA-256 9c567875e01f89cc15eb23d1b3d2d40c2c23239cc414ff12a1b1c3902dd3f7ff |
| 作業HEADのmain所属 | merge-base --is-ancestor HEAD origin/mainの終了値1（ancestorではない） |

GitHub CLIによるmain commit照会・固定SHAへのcontents照会、local git ls-tree/cat-file/merge-base、
ファイルbytesのhash計算だけを実施した。リモートコードのcheckout/import/実行、mainへのpush/merge、許可ref変更はしていない。
404はこのreadiness確認での期待する否定結果であり、テスト失敗や取得成功ではない。
branchの状態は変わり得るため、次回は固定main HEADと必要ファイルの存在を再確認する。

現在のfixture_workerは管理下ローカル4fixtureの開発検証経路。fixture_理由を受理SHAのコード検査成功と扱わない。
受付APIのSHA検証は構文だけで、存在/許可ref所属は未実装。取込済みrunを成功/再キューへ書換えない。

## 公開前の最小取得契約案

公開済みsourceが用意された後の小さな実装範囲を以下に限定する。今回これを既存設定へ適用したとは扱わない。

1. repository/ref/workflow/path/承認済みsource digestはサーバー固定。受理要求からURL/path/コマンド/YAML/imageを取らない。
2. fixture-v1の最小候補は上記pathと上記2151-byte sourceの完全一致。固定ローカルDockerfile/base digestを使い、リモートDockerfile・hooks・attributes・submoduleを実行経路へ入れない。
3. 許可ref HEADをsnapshotし、受理SHAがcommit objectで、そのHEADと同一またはancestorであることを検証する。実行対象は受理SHAのtree/blobに固定し、branchの最新内容へ置換しない。
4. 固定pathが通常ファイルで、size上限・Git blob identity・承認済みSHA-256に一致するときだけ受け渡す。symlink/submodule/異なるguard/未承認sourceは拒否する。
5. 取得用の固定HTTPS先・redirect拒否・応答/展開サイズ・timeout・一時資源回収を定義してから実装する。取得失敗は成功扱いせず、ホストでsourceを実行しない。
6. 取得はimage準備と同様にleaseを無条件に延長しない設計とする。取得前の候補snapshotを使う場合、claim対象のrun ID/SHAと必ず再照合し、別runを取得したまま別sourceを実行しない。
7. 検証したbytesを変更不可の取得結果として渡し、そのbytesから固定build contextを作る。可変なworktreeを再読込して別内容をbuildしない。
8. accepted SHA/ref snapshot/path/source digest/image identityをattempt結果へ対応付ける。現在のlog/結果のfencing・回収・上限・network noneを維持する。記録方法や恒久拒否の分類も後続実装で定義する。

現行fixtureにはguardも含まれるため、source digest固定を緩めて任意のmainコードへ差し替えない。
将来変更可能なworkloadを受け付ける場合は、信頼済みguardとworkloadの分離を別途設計・検証する。
この最小契約はリポジトリ全体の任意コード検査や、外部PRの安全な実行保証ではない。

## 公開前に定めた再開条件（履歴）

- 推奨: 許可repo/main上へ管理下の固定fixtureを通常レビュー経由で公開する。候補sourceは[調査時の担当commit](https://github.com/kenobix/Claude_practice/blob/aaadaa77c703925e1ec7ebcac5153a1d7ae1fdc5/261117/ci_runner/fixtures/job/fixture.py)。このagentはmainへのpush/mergeを行わない。
- 別sourceを採用する場合は、管理下repository/ref/path/workflow/digestを明示して許可設定の変更を判断する。担当branchや他の日付のプログラムへ無断で読み替えない。
- 公開後に存在・ref所属・digestを再確認し、上記の拒否系/サイズ/通信障害/誤対応防止を含む取得単位を実装・検証する。実際の取得/実行成功はその時点まで未検証。

今回の停止判断はユーザー指示の「重大な問題や外部依存で進められない場合は、理由を記録して停止する」に基づく。
P1全体・MVPは未完了。機能進捗は据置き（概算約30%・残約70%）。別機能を追加してこの依存を解消済みとはしない。
