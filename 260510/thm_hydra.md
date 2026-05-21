# TryHackMe: Hydra

> **ルーム:** [Hydra](https://tryhackme.com/room/hydra)
> **学習日:** 2026-05-20 〜 2026-05-22
> **進捗:** Task 1 完了 / Task 2 取り組み中（未解決）

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

## Task 2: Using Hydra（取り組み中）

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

### 未解決の課題

**Flag 1（Webパスワードクラック）**

現状のコマンドではrockyou.txtの74万件試行後もヒットなし。以下の点を次回検証する：

1. **フォームのフィールド名・パスを正確に確認する**
   - ブラウザの開発者ツール（F12）→ ネットワークタブで実際のPOSTリクエストを見る
   - 変数名が `username`/`password` ではなく `user`/`pass` 等の可能性がある
   - 送信先パスが `/` ではなく `/login` 等の可能性がある

2. **失敗文字列（F=）を正確に確認する**
   - 実際のエラーメッセージが `incorrect` ではない可能性がある
   - 誤った失敗文字列を指定すると全試行が「失敗」と誤判定されてしまう

**次回の調査手順：**

```bash
# Step 1: 開発者ツールで手動ログインして実際のPOSTパラメータを確認
# Step 2: 正確なパス・変数名・失敗文字列でコマンドを再構築
hydra -l molly -P /usr/share/wordlists/rockyou.txt <新IP> \
  http-post-form "/<確認したパス>:<確認した変数名>=^USER^&<確認した変数名>=^PASS^:F=<確認したエラー文字列>" -t 32
```

---

## 関連リソース

- [TryHackMe: Hydra](https://tryhackme.com/room/hydra)
- [Kali Linux: Hydra ツールページ](https://www.kali.org/tools/hydra/)
- [Hydra 公式リポジトリ](https://github.com/vanhauser-thc/thc-hydra)
