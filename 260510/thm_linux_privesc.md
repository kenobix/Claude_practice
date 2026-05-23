# TryHackMe: Linux PrivEsc

> **ルーム:** [Linux PrivEsc](https://tryhackme.com/room/linprivesc)
> **学習日:** 2026-05-23 〜
> **進捗:** 未着手（0%）
> **初期認証情報:** `user:password321`（SSH接続可）

---

## ルーム概要

意図的に脆弱な設定にされたDebian VMを使い、Linuxの権限昇格（Privilege Escalation）の手法を実践する。  
一般ユーザー（`user`）として侵入し、`root` 権限を奪取するさまざまな経路を体験する。

---

## タスク一覧

| Task | タイトル | カテゴリ |
|------|---------|---------|
| Task 1 | Deploy the Vulnerable Debian VM | 環境構築 |
| Task 2 | Service Exploits | サービス脆弱性 |
| Task 3 | Weak File Permissions - Readable /etc/shadow | ファイルパーミッション |
| Task 4 | Weak File Permissions - Writable /etc/shadow | ファイルパーミッション |
| Task 5 | Weak File Permissions - Writable /etc/passwd | ファイルパーミッション |
| Task 6 | Sudo - Shell Escape Sequences | sudo悪用 |
| Task 7 | Sudo - Environment Variables | sudo悪用 |
| Task 8 | Cron Jobs - File Permissions | cronジョブ悪用 |
| Task 9 | Cron Jobs - PATH Environment Variable | cronジョブ悪用 |
| Task 10 | Cron Jobs - Wildcards | cronジョブ悪用 |
| Task 11 | SUID / SGID Executables - Known Exploits | SUID/SGID悪用 |
| Task 12 | SUID / SGID Executables - Shared Object Injection | SUID/SGID悪用 |
| Task 13 | SUID / SGID Executables - Environment Variables | SUID/SGID悪用 |
| Task 14 | SUID / SGID Executables - Abusing Shell Features (#1) | SUID/SGID悪用 |
| Task 15 | SUID / SGID Executables - Abusing Shell Features (#2) | SUID/SGID悪用 |
| Task 16 | Passwords & Keys - History Files | パスワード・鍵 |
| Task 17 | Passwords & Keys - Config Files | パスワード・鍵 |
| Task 18 | Passwords & Keys - SSH Keys | パスワード・鍵 |
| Task 19 | NFS | NFS悪用 |
| Task 20 | Kernel Exploits | カーネル脆弱性 |
| Task 21 | Privilege Escalation Scripts | 自動化ツール |

---

## 事前知識メモ

### 権限昇格とは

- 一般ユーザー権限でシステムに侵入後、設定ミスや脆弱性を利用して `root` 権限を奪取すること
- 「侵入」の次のステップとして必須の技術

### 主な権限昇格の経路（このルームで扱うもの）

| 手法 | 概要 |
|-----|------|
| **ファイルパーミッション不備** | `/etc/shadow`（パスワードハッシュ）や `/etc/passwd` が一般ユーザーに読み書きされている |
| **sudo 設定ミス** | `sudo -l` で確認できる許可コマンドを悪用してシェルエスケープ |
| **Cron ジョブ** | root 実行の定期ジョブが書き換え可能なスクリプトを呼んでいる |
| **SUID/SGID ビット** | root 所有でSUIDが付いた実行ファイルを悪用 |
| **パスワードの平文保存** | シェル履歴・設定ファイル・SSH秘密鍵の漏洩 |
| **NFS の no_root_squash** | NFS マウントに root として書き込みができてしまう設定 |
| **カーネルエクスプロイト** | カーネルバージョンの既知脆弱性を使ったroot奪取 |

---

## 明日以降の予定

Task 1（環境デプロイ）から順番に実施する。

- SSH接続: `ssh user@<ターゲットIP>`（パスワード: `password321`）
- 各タスクの手順・コマンド・学んだ知見をこのファイルに追記していく

---

## 関連リソース

- [TryHackMe: Linux PrivEsc](https://tryhackme.com/room/linprivesc)
- [GTFOBins](https://gtfobins.github.io/) — SUID/sudo悪用の参照
- [HackTricks: Linux Privilege Escalation](https://book.hacktricks.xyz/linux-hardening/privilege-escalation)
