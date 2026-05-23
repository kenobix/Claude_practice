# TryHackMe: Hydra

> **ルーム:** [Hydra](https://tryhackme.com/room/hydra)
> **学習日:** 2026-05-20 〜 2026-05-23
> **進捗:** Task 1・2 完了（100%）

---

## Task 1: Hydra Introduction

### Hydra とは

**Hydra** はオンラインのブルートフォース（総当たり）パスワードクラッキングツール。パスワードリストを使って認証サービスへの認証を高速で試行し、正しいパスワードを特定する。

- 手動でパスワードを試すのと同じことを自動・高速で行うツール
- **デフォルト認証情報（`admin:password` 等）を変更しない機器が最大のリスク**

### 対応プロトコル（抜粋）

Hydraは以下を含む多数のプロトコルに対してブルートフォースが可能：

| カテゴリ | プロトコル例 |
|----------|------------|
| リモートアクセス | SSH (v1/v2)、Telnet、RDP、VNC |
| ファイル転送 | FTP、AFP、Subversion |
| Webアプリ | HTTP-FORM-GET、HTTP-FORM-POST、HTTPS系 |
| メール | SMTP、POP3、IMAP |
| データベース | MySQL、MSSQL、PostgreSQL、MongoDB、Oracle |
| その他 | SNMP v1/v2/v3、SMB、LDAP、SIP、IRC |

### なぜ強いパスワードが必要か

- 1億件規模のパスワードリストには一般的なパスワードが含まれている
- 短い・特殊文字なし・よくある単語のパスワードはHydraで容易にクラックされる
- CCTVカメラやWebフレームワークが `admin:password` をデフォルト認証情報として使っている事例が多い

---

## Task 2: Using Hydra

### Hydra の基本構文

```
hydra -l <ユーザー名> -P <パスワードリスト> <ターゲットIP> <プロトコル> [オプション]
```

| オプション | 意味 |
|-----------|------|
| `-l <user>` | 単一ユーザー名を指定 |
| `-L <file>` | ユーザー名リストファイルを指定 |
| `-p <pass>` | 単一パスワードを指定 |
| `-P <file>` | パスワードリストファイルを指定 |
| `-t <N>` | 並列スレッド数（デフォルト16） |
| `-V` | 試行中の全ログを表示（詳細モード） |
| `-I` | 復元ファイル（hydra.restore）を無視して新規開始 |
| `-R` | 前回の中断セッションを再開 |

### HTTP POST フォームへのブルートフォース構文

```
hydra -l <user> -P <wordlist> <IP> http-post-form "<path>:<params>:F=<failure_string>"
```

- `<path>` : フォームの送信先パス（例: `/` や `/login`）
- `<params>` : フォームのパラメータ（例: `username=^USER^&password=^PASS^`）
- `F=<failure_string>` : ログイン失敗時にレスポンスに含まれる文字列

**実行例（問題1: Webフォーム）:**

```bash
hydra -l molly -P /usr/share/wordlists/rockyou.txt 10.10.X.X \
  http-post-form "/:username=^USER^&password=^PASS^:F=incorrect"
```

### SSH へのブルートフォース構文

```bash
hydra -l <user> -P <wordlist> <IP> -t 4 ssh
```

---

### 試行錯誤の記録と学んだ知見

#### `-V` オプションの落とし穴
- `-V` を付けると全試行ログが流れ、画面が激しくスクロールする
- これは正常動作。「動いていないのでは」と感じてCtrl+Cで止めてはいけない
- **成功時のログ形式：**
  ```
  [80][http-post-form] host: 10.X.X.X   login: molly   password: [正解]
  1 of 1 target successfully completed, 1 valid password found
  ```
- プロは `-V` を外し、成功ログが出るまで静かに待つ

#### hydra.restore ファイル
- Ctrl+C で中断するとカレントディレクトリに `hydra.restore` が生成される
- 次回起動時に「10秒以内にCtrl+Cで中断すれば上書きしない」という警告が出る
- 設定を変えて新規スキャンする場合は `rm hydra.restore` で削除するか `-I` オプションを使う

#### スレッド数（-t）とサーバー負荷のトレードオフ

| スレッド数 | 結果 |
|-----------|------|
| `-t 16`（デフォルト） | 分速約4,600回。rockyou.txtでは51時間かかる計算 |
| `-t 32` | 適度な加速 |
| `-t 64` | サーバーが接続拒否（`cannot connect` エラー）でダウン |

→ Webフォームへの攻撃には `-t 32` 前後が現実的

#### ワードリストの選択戦略

| リスト | 件数 | 用途 |
|--------|------|------|
| `fasttrack.txt` | 222件 | 超頻出パスワードの即席テスト（数秒で完了） |
| `rockyou.txt` | 約1,434万件 | 本命。ヒットするまで数分〜数十分 |

→ まず `fasttrack.txt` で試し、ダメなら `rockyou.txt` に切り替える

#### ターゲットIPアドレスの変化に注意
- TryHackMeのマシンを再起動（Terminate → Start Machine）するとIPアドレスが変わる
- ダッシュボードの「Target Machine Information」で常に最新IPを確認すること

---

### 解決：ブラウザ開発者ツールで正しいパラメータを特定する

#### なぜ74万件試行してもヒットしなかったのか

最初のコマンドのパスが `/`（トップページ）になっていたのが原因。実際のログイン処理は `/login` が行っており、トップページはパスワード検証を行わないため、全リクエストが「成功でも失敗でもない応答」を返していた。

#### F12開発者ツールによる調査手順

1. Firefoxで `http://<ターゲットIP>` を開く
2. `F12` →「Network（ネットワーク）」タブを開く
3. ログインフォームにダミー情報（例：`molly` / `test1234`）を入力してSubmit
4. Networkタブに記録された **Method: POST** の行をクリック
5. 以下の3点を確認する：

| 確認項目 | 確認場所 | 今回の値 |
|---------|---------|---------|
| 送信先パス | File列またはHeaders→URL | `/login` |
| フォーム変数名 | Payload（Request）タブ | `username` / `password` |
| 失敗メッセージ | ページ上の表示文字列 | `Your username or password is incorrect.` |

#### 正しいコマンド（Flag 1: Webフォーム）

```bash
hydra -l molly -P /usr/share/wordlists/rockyou.txt <IP> \
  http-post-form "/login:username=^USER^&password=^PASS^:F=incorrect" -t 32
```

**結果：** `password: sunshine`

#### Flag 2：SSH へのブルートフォース

```bash
hydra -l molly -P /usr/share/wordlists/rockyou.txt <IP> ssh -t 4
```

- SSHは連続接続に弱いため `-t 4` に制限する（`-t 64` はサーバーをダウンさせる）
- パスワード判明後、`ssh molly@<IP>` でログインしてフラグを回収

---

### ルーム完了：今回の最大の教訓

**ツールを動かす前に、まず相手を観察する**

| やってしまったこと | 正しいアプローチ |
|-----------------|----------------|
| 問題文の例コマンドをそのままコピペ | F12で実際のPOSTパラメータを確認してからコマンドを組む |
| `/` へ74万回リクエストを送り続けた | 送信先パス・変数名・失敗文字列の3点を先に特定する |
| `-t 64` でサーバーをダウンさせた | 相手サービスの耐性を見極めてスレッド数を調整する |
| `-V` の大量ログを見てパニックで中断 | `-V` なしで静かに回し、成功ログが出るまで待つ |

---

## 関連リソース

- [TryHackMe: Hydra](https://tryhackme.com/room/hydra)
- [Kali Linux: Hydra ツールページ](https://www.kali.org/tools/hydra/)
- [Hydra 公式リポジトリ](https://github.com/vanhauser-thc/thc-hydra)
