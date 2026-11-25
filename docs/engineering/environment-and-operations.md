# 実行環境と運用

Dockerや一時環境を使う試験、外部サービスからの実配送を確認するときに使います。実行ユーザー、資源の所有者、公開時間、停止前に採取する証拠を確かめます。

## ホストのファイル権限と非rootのコンテナ

ホストで生成したファイルをイメージに入れるときは、生成時のumask、DockerのCOPY後のモード、実行UIDでの読み取り可否を確認します。CI Runnerでは、公開試験用のumask 0077をWorkerが引き継ぎ、一時fixtureが0600のままCOPYされたため、非rootプロセスが起動直後に失敗しました。検証済みのfixtureだけを0644にし、コンテキストのディレクトリは0700に保ったところ、同じumaskで読めることを確認できました。再発確認には実際のビルドコンテキストと実行UIDを使います。公開を前提にしたfixtureのモードを秘密ファイルへ適用しないでください。 [失敗の再現と修正](../../261117/ci_runner/docs/p5-validation.md#終了コード2の再現と修正後のローカル試験)・[DockerのCOPY仕様](https://docs.docker.com/reference/dockerfile/#copying-from-the-build-context)

## Docker資源の確認と回収

容量を調べるときはDocker contextを確認し、`docker system df -v` でコンテナ、イメージ、Volume、Build Cacheの使用量を見ます。試験用の資源はプロジェクト名、ラベル、IDを記録し、回収前に所有者と参照先を確認します。コンテナや一時イメージは対象を指定して回収します。Volumeに残るDBなどのデータも、試験専用と分かるものが回収の対象です。共有のベースイメージやBuild Cacheは再利用の可否を考慮し、試験の後片付けで一括pruneは行いません。最後に試験ラベルや名前で残存を確認します。表示上の回収可能量から削除対象の所有者は分かりません。 [CI Runnerの資源回収](../../261117/ci_runner/docs/container-preparation.md)・[Dockerの容量表示](https://docs.docker.com/reference/cli/docker/system/df/)・[Dockerのprune対象](https://docs.docker.com/engine/manage-resources/pruning/)

## 公開時間と外部サービスの準備

短時間だけ受信口を外部に公開する場合は、開始前にローカルの受付と署名の検証を済ませます。受信側の担当、外部サービスを操作するユーザー、利用する配送または新規イベント、終了時刻、停止と回収の分担を決めます。両者が作業でき、受信側の設定がそろった時点で公開時間を開始します。発行後に公開URLが決まる場合は、URLを受け取って変更する担当者も待機してから開きます。CI Runnerでは、受信側の設定と配送先の更新が時間内にそろわず、公開経路は開けても配送を検証できなかった回がありました。GitHub Webhookは自動再送されず、手動再送は過去3日間の配送が対象です。期限と操作権限を開始前に確認し、期限を過ぎていたら新規イベントが必要かを判断します。 [公開試験の記録](../../261117/ci_runner/docs/p5-validation.md#2026-11-21の公開試験)・[受付手順](../../261117/ci_runner/docs/webhook.md)・[GitHubの再送期限](https://docs.github.com/en/webhooks/testing-and-troubleshooting-webhooks/redelivering-webhooks)

## 一時環境を閉じる前の証拠

DBやコンテナなどを回収する前に、対象SHA、必要な識別子、時刻、保存された結果、失敗した条件を採取します。CI Runnerは配送GUID、受理SHA、run IDとattemptの履歴を照合し、実配送とローカルのSIGKILL試験を別のrunとして保存しました。前者では公開HTTPSと受付から正式結果までを、後者では実行中の停止後の回収と引き継ぎを確認できました。試験の省略可否は受入条件に対し何を観測できたかで判断します。証拠を採取したら承認済みの期限までに公開を止め、試験資源の回収を確認します。 [P5の実配送・故障試験と受入判定](../../261117/ci_runner/docs/p5-validation.md#実pushを起点にしたデモ)
