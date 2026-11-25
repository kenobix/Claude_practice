# 開発の進め方

企画、設計変更、実装の引き継ぎ、PRの受入に使います。着手前に成果物と検証範囲を決め、レビューとCIの記録を対象のコミットに結びつけます。

## 受入条件と目標値

計画時に必須条件、計測目標、検証対象外を明記し、各条件に対応する試験と記録を決めます。CI Runnerの復旧45秒は仮目標でした。超過した実測を記録し、同じrunの引き継ぎと旧token拒否は個別に判定しました。受入時に条件を足す必要が生じた場合は、根拠と影響を示して合意します。性能値や合格条件は個別の計画に従います。 [CI Runnerの元計画](../../261014/plan_ci_runner.md)・[P5の実測と受入](../../261117/ci_runner/docs/p5-validation.md)

## 並列開発と引き継ぎ

同時に進む案件は同じリポジトリでもブランチとGit worktreeを分けます。開始点のSHA、担当パス、共有ファイルの変更順を先にそろえ、差分を依存順の小さな単位にします。CI Runnerでは、実行経路の整備前にレビュー済みfixture一つの公開が必要でした。先行PRで対象ファイル、バイト列、出典を確認し、公開後に取得経路を検証しました。この方式は公開物や依存関係を小さく切り出せる場合に使えます。 [CI Runnerの作業ログ](../../261117/work_log.md)・[fixtureの先行PR #4](https://github.com/kenobix/Claude_practice/pull/4)

## 独立レビューと修正後の再確認

重要な設計やMVPの受入では、実装担当と独立レビュー担当を分けます。元の契約、対象SHA、再現条件を渡し、指摘は Critical / High / Medium / Low で管理します。修正後は新しいSHAを示し、元の再現条件と周辺の回帰を再確認します。KVエンジンでは、独立レビューで診断やCloseの異常系が見つかり、再レビューではsymlink経由のパス探索権限の回帰も見つかりました。指摘が解消した時点のSHAと、その判定が及ぶ範囲を残します。 [KVエンジンの受入](../../261118/kv-engine/docs/mvp-acceptance.md)・[PR #2](https://github.com/kenobix/Claude_practice/pull/2)

## PRの統合と最新状態

マージする場合は、差分全体、承認対象HEAD、必要なCIの対象コミットと結果、競合の有無を直前に照合します。問題が見つかったPRは保留です。修正後の差分とHEADを確認し直します。複数のPRを順にマージするときは、新しいベースに対して次のPRを確認します。統合結果や公開結果は、GitHubの最新状態とマージコミットに対応する実行から確認します。過去のログやDraft当時の記述を読むときは、その記録の時点を確かめます。 [DNSの修正を含むPR #7](https://github.com/kenobix/Claude_practice/pull/7)・[attemptの修正を含むPR #8](https://github.com/kenobix/Claude_practice/pull/8)

## MVPの完了と検証の再利用

元の計画に沿って受入表を埋め、証拠を再利用できる条件を確認します。PCAP Analyzerでは新規環境の受入試験と、保存済みの独立比較や性能測定の照合をそれぞれ記録しました。追加実験は対象SHA・環境・条件の変更や証拠不足など、再確認が必要な範囲に絞ります。対象外や未検証事項を記録したら、計画外の機能や公開試験は次の依頼で扱います。 [PCAP Analyzerの元計画](../../261014/plan_pcap_analyzer.md)・[MVP受入記録](../../261119/pcap_analyzer/docs/mvp_acceptance.md)・[PR #5](https://github.com/kenobix/Claude_practice/pull/5)
