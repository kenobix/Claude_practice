# P0脅威モデルと検証ゲート

通常サンドボックスでの接続拒否後、許可された経路でDocker daemon接続とDB起動を確認した。
現在環境の管理下fixtureで主要な制限を実測しP0を完了した。[制限ごとの実証・設定確認のみの項目・限界](isolation.md)を参照。
現Composeは開発DBだけを定義し、ジョブ実行の安全性を証明するものではない。

## 信頼境界

API/Worker/DBは信頼する制御面。ジョブのコード、stdout/stderr、成果物、HTTP入力は非信頼データ。
Docker操作権限は信頼済みWorkerだけ。APIやジョブへsocketを渡さない。
ジョブ用DB資格情報・Webhook secret・ホストホーム・SSH agentをジョブへ渡さない。
専用の使い捨てVMを基本とし、ホストとカーネルを共有するコンテナだけで非信頼コードの完全隔離を主張しない。

| 脅威 | 設計する制御 | 今後の検証 |
|---|---|---|
| 任意shell/依存インストール | 任意YAML/コマンドを受け付けず、固定workflow・固定commit、ホストshell実行禁止 | 入力から実行経路への混入試験 |
| ホスト侵害 | 非root UID、cap-drop ALL、no-new-privileges、privileged禁止、socket/host mount禁止 | uid/capability/マウント観測 |
| 書込・資源枯渇 | read-only root、限定tmpfs 16MiB、CPU 0.5、RAM 128MiB、PID 64 | 書込拒否、OOM、CPU/PID実測 |
| ネットワーク流出 | fetch段階を分離、実行コンテナはnetwork none | 外向き通信失敗を実測 |
| 無限実行/大量ログ | 壁時計上限10秒、stdout+stderr合計1MiBで打切り、停止・強制kill・回収 | 長時間/大量ログfixture試験 |
| ログによるディスク枯渇 | Docker側もlog-driver none等で別経路の無制限保存を避ける | アプリとdaemon両方のディスク確認 |
| 古い実行結果 | token/DB時刻/状態/取消条件による確定、attempt別成果物 | 期限切れ完了・競合試験 |
| パストラバーサル/XSS | ファイル名はサーバー生成、範囲外パスとsymlinkを拒否、表示時HTML escape | 悪意ある名前・内容の試験 |
| Webhook偽装/再送 | raw body HMAC-SHA256定数時間比較、body上限、許可repo/ref、IDと業務キー | P3の署名fixture・再送試験 |
| ローカルAPIの悪用 | loopback限定、CORSを開放しない、公開時の認証は別設計 | 非loopback公開を避ける起動確認 |

上限値は初期案。OS/cgroup/rootless構成により適用できない場合は実行開始を拒否する設計にする。
Docker inspectの値だけで有効と判断せず、実際の失敗・抑制とホスト影響を観測する。
管理下fixture以外は検証ゲートを通すまで実行しない。ホストでfixtureを代替実行しない。

## 異なる実行環境へ移る際の検証手順

1. 専用VMと利用可能なDocker contextを管理者と確認する。今回は権限変更・sudo・group追加を行わない。
2. rootlessを候補にし、uidmap/subuid/subgid、cgroup v2/systemd等の前提とresource制限対応を確認する。
3. `bash scripts/check_environment.sh`、`docker info`で接続と実行方式を確認する。
4. DBを起動しmigration・永続化・統合テストを実行する。
5. 固定したfixtureイメージをbuild/pullしdigestを記録してから、上の各制御を実測する。
6. 制限が無効なら失敗として記録し、設定を修正するまでWorkerを有効化しない。

PostgreSQL 17.12-bookwormのdigestはComposeに固定済み。P0 fixtureのPythonイメージもdigest固定で実測済み。
表の制御はP0テストで実証したものと、P1以降の要件を含む。受付APIのrepository/SHA/4KiB/未知入力拒否は実装・検証済み。Workerの実行境界は未実装なので、アプリからジョブ開始を許す経路は存在しない。
今回の監視処理はテスト用であり、Workerに実装済みとは説明しない。nosuid/nodevや全setuid/全syscallの試験は未実施。

参照: [Docker security](https://docs.docker.com/engine/security/)、[Rootless mode](https://docs.docker.com/engine/security/rootless/)。
