# P0脅威モデルと検証ゲート

通常のサンドボックスからは接続を拒否された。許可された経路でDockerデーモンに接続し、DBが起動することを確認した。
現在の開発環境で管理下のfixtureを使い、主要な制限を実測してP0を完了した。[制限ごとの実証・設定確認のみの項目・限界](isolation.md)を参照。
現在のComposeが定義するのは開発DBだけで、ジョブ実行の安全性はこの設定からは確認できない。

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
| ログ・成果物による容量枯渇 | log-driver none、ログ1MiB/attempt・最大64MiB、結果JSON 4KiB/attempt・最大16MiBをDBで条件付き予約 | DB競合/容量不足で確定拒否。WAL/履歴等を含む物理容量の上限は未検証 |
| 古い実行結果 | token/DB時刻/状態/取消条件による確定、attempt別成果物 | 期限切れ完了・競合試験 |
| パストラバーサル/XSS | 成果物は固定名とDB保存キーで生成、ダウンロード入力はUUID。ログは表示時HTML escape | 不正ID、旧試行の参照、ログ内のタグ・制御文字を検証 |
| Webhook偽装/再送 | raw body HMAC-SHA256定数時間比較、body上限、許可repo/ref、IDと業務キー。Webhook専用アプリへ分離 | [ローカルHTTPS・再送試験](webhook.md)。GitHubからの実配送は未検証 |
| ローカルAPIの悪用 | loopback bind、CORSを開放しない、管理アプリ全経路の単一loopback Host検証、管理画面の埋め込み拒否 | [管理APIの契約](run-operations.md)。Host検証はloopback bindの補助。公開時の認証は別設計 |

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
表の制御はP0テストで実証したものと、P1以降の要件を含む。受付APIのrepository/SHA/4KiB/未知入力拒否は実装・検証済み。WorkerのDB実行権preflightと未起動コンテナ作成・設定照合・回収を別ライブラリに実装した。[固定fixture実行ライブラリ](fixture-execution.md)にDB再照合・実値ガード・上限監視・回収を接続済み。HTTP/claim-onceからは未接続。ローカルfixture結果の条件付きDB確定も実装済み。一件Workerへの接続と[上限付きログDB保存](log-storage.md)も実装済み。正式ログの[bounded read/制御文字可視化/HTMLエスケープ](log-access.md)も実装済み。受理commitの固定取得と常駐実行は接続済み。管理画面の失敗理由とログはテキストをエスケープして表示する。操作スクリプトは既存PUT APIに限定する。
P0監視ハーネスとアプリ側の固定fixture実行監視を分離している。常駐Workerは作業単位の停止に対応する。実行中のDB heartbeatを確認できなければ所有資源を回収して終了する。強制終了後は[失効attemptのコンテナ回収](design.md#失効attemptのコンテナ回収)でDBの終端記録とDockerの所有ラベル・完全IDを照合する。daemonとDB書込主体は信頼境界の内側。基盤障害の再試行はcreate未送信または同じdaemonでの完全IDの不在確認を条件とする。結果不明のcreateには再試行を保留する。nosuid/nodevや全setuid/全syscallの試験は未実施。

参照: [Docker security](https://docs.docker.com/engine/security/)、[Rootless mode](https://docs.docker.com/engine/security/rootless/)。
