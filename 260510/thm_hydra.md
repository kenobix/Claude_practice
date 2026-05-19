# TryHackMe: Hydra

> **ルーム:** [Hydra](https://tryhackme.com/room/hydra)
> **学習日:** 2026-05-20
> **進捗:** Task 1 完了（33%）/ Task 2〜 は翌日以降

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

## 明日の予定

**Task 2: Using Hydra** から再開

- 実際のHydraコマンド構文
- SSH・Webフォームへのブルートフォース実践

---

## 関連リソース

- [TryHackMe: Hydra](https://tryhackme.com/room/hydra)
- [Kali Linux: Hydra ツールページ](https://www.kali.org/tools/hydra/)
- [Hydra 公式リポジトリ](https://github.com/vanhauser-thc/thc-hydra)
