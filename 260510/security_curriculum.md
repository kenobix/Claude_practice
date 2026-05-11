# WSL2で学ぶセキュリティ カリキュラム

> 出典：Claude との対話（2026-05-04）をもとに妥当性を検証・補足したもの。2026-05-11に学習手段・外部教材を追加。

**学習サイト（自作）：** https://kenobix.github.io/Claude_practice/260510/01_linux_permissions/

---

## 学習方針の見直し（2026-05-11）

### 自作教材 vs 既存教材の使い分け

| トピック | 学習手段 | 方針 |
|----------|----------|------|
| 1〜5 | TryHackMe / DVWA / Juice Shop / OverTheWire | **既存教材を優先** |
| 6〜9 | WSL2上での自作環境 + ツール実践 | **自作が活きる** |
| 10 | OverTheWire Narnia + gdb | **既存優先** |

**核心：** トピック1〜5は既存の教材で十分な演習環境が整っている。自作サイトを作る時間をその学習に充てること。トピック6以降（シークレット管理・IaC・ロギング・LLM脆弱性）で初めて「自分の環境を作りながら学ぶ」が意味を持つ。

### 主要外部教材

| ツール | URL | 料金 | 概要 |
|--------|-----|------|------|
| TryHackMe | https://tryhackme.com | 無料プランあり / Premium $16.99/月（年払い約$10.50/月） | ブラウザ完結の仮想環境付き学習プラットフォーム。解説付きで「なぜ危険か」まで学べる |
| DVWA | https://github.com/digininja/DVWA | 無料（OSS） | 意図的に脆弱性を埋め込んだWebアプリ。SQLi・XSS・CSRFなどを難易度切替付きで体験できる |
| OWASP Juice Shop | https://owasp.org/www-project-juice-shop/ | 無料（OSS） | 実務に近い複合的な脆弱性を持つWebアプリ。Docker一発で起動 |
| OverTheWire | https://overthewire.org/wargames/ | 無料 | SSH接続で解くLinux・Web・暗号のCTF形式演習 |
| GTFOBins | https://gtfobins.github.io/ | 無料 | sudo・SUID経由で悪用可能なバイナリ一覧 |
| HackTricks | https://book.hacktricks.xyz/ | 無料 | 権限昇格・コンテナ脱出など攻撃手法の網羅的リファレンス |

#### DVWA のWSL2上での起動（5分以内）

```bash
docker run -d -p 80:80 vulnerables/web-dvwa
# ブラウザで http://localhost にアクセス
```

#### TryHackMe 無料プランで使えるもの
- アカウント作成・一部の入門ルーム（Linux Fundamentals・基本ネットワーク等）
- OpenVPN経由での接続
- 1日1時間のブラウザ仮想マシン
- ※ ほとんどの学習パスのルームはPremium限定。無料で触って続けられると判断してから契約するのが合理的

---

## 妥当性検証まとめ

全体として内容は正確で、優先度順・学習順序の設計も適切。以下の点のみ補足・修正が必要。

| # | 項目 | 評価 | 補足 |
|---|------|------|------|
| 1 | Linux権限・プロセス分離 | ✅ 正確 | WSL2カーネルはuserネームスペースの作成に制限がある場合あり。`unshare`はroot権限が必要なケースが多い |
| 2 | コンテナセキュリティ | ✅ 正確 | Docker Desktop / dockerd on WSL2 で問題なく演習可能 |
| 3 | 認証・認可 | ✅ 正確 | JWT alg:none はCVE-2015-9235として正式に記録済み |
| 4 | ネットワーク攻撃とTLS | ✅ 正確 | Wireshark はWSL2内では`tcpdump`でキャプチャ → pcapファイルをWindowsのWiresharkで開く形になる |
| 5 | Webアプリ脆弱性 | ✅ 正確 | OWASP Top 10 2021版が現行。SSRFは2021年に新規追加 |
| 6 | シークレット管理 | ✅ 正確 | truffleHog v3が現行（UIが大幅刷新）。SBOMはEU Cyber Resilience Actで義務化が進行中 |
| 7 | IaCセキュリティ | ⚠️ 要補足 | **tfsecは2023年にTrivyへ統合。** `trivy config`コマンドで同等の機能を使うのが現在の正しい方法 |
| 8 | ロギング・モニタリング | ✅ 正確 | Grafana LokiはSIEMというよりログ集約ツール。軽量でWSL2向きではある |
| 9 | AI・LLM脆弱性 | ✅ 正確・最新 | OWASP LLM Top 10（2023年公開、2025年改訂版あり）が参照資料として最適 |
| 10 | バイナリ・メモリ安全性 | ✅ 正確 | gdb・strace・ltrace すべてWSL2で動作確認済み |

---

## 学習カリキュラム（優先度順）

> **鉄則：** 上から順に進める。5（Web脆弱性）から始めてツールだけ覚えるのは有害。仕組みが分からないと未知の攻撃に対応できない。

---

### 1. Linux権限・プロセス分離（基盤）

**なぜ学ぶか**

WSL2上で何を作っても、ここが弱ければ全て崩壊する。root権限の乱用・SUID/SGIDの誤設定は現代でも攻撃の主要経路。

**推奨学習手段（既存教材優先）**

| 順序 | 教材 | 内容 | URL |
|------|------|------|-----|
| 1 | OverTheWire Bandit | SSHで解くLinux基礎CTF。権限・パーミッション・プロセスを手を動かして体験 | https://overthewire.org/wargames/bandit/ |
| 2 | TryHackMe「Linux Fundamentals」 | Linux権限・プロセス・ファイルシステムの解説付き演習（一部無料） | https://tryhackme.com/module/linux-fundamentals |
| 3 | TryHackMe「Linux Privilege Escalation」 | SUID・sudo・capabilityを使った権限昇格を体系的に学ぶ | https://tryhackme.com/room/linprivesc |
| 4 | 自作サイト（補助） | 体験後に自分の言葉で再整理する用途 | https://kenobix.github.io/Claude_practice/260510/01_linux_permissions/ |

**学ぶべきこと**

- UID/GID、capabilities（`CAP_NET_BIND_SERVICE`等）の実動作
- sudoの設定ミスが生む権限昇格（`/etc/sudoers`の書き方と危険なパターン）
- namespace・cgroupによるプロセス隔離の仕組み
- `/proc`ファイルシステムを使った実際の情報漏洩の再現

**WSL2での実践メモ**

```bash
# UID/GIDの確認
id && cat /etc/passwd | grep $(whoami)

# プロセスのcapabilityを確認
cat /proc/self/status | grep -i cap
capsh --print

# 危険なSUIDバイナリを探す
find / -perm -4000 -type f 2>/dev/null

# /procから他プロセスの情報を読む（要sudo）
cat /proc/1/environ | tr '\0' '\n'

# cgroupの確認
cat /proc/self/cgroup
ls /sys/fs/cgroup/
```

**参照リソース**

- [GTFOBins](https://gtfobins.github.io/) — sudo・SUID経由で悪用可能なバイナリ一覧
- `man capabilities` — capabilityの完全リスト
- [Linux Privilege Escalation — HackTricks](https://book.hacktricks.xyz/linux-hardening/privilege-escalation)

---

### 2. コンテナセキュリティ（Docker/OCI）

**なぜ学ぶか**

2025年現在、インフラの大半がコンテナ化されている。「Dockerを使えば安全」という誤解が最も危険。

**推奨学習手段（既存教材優先）**

| 順序 | 教材 | 内容 | URL |
|------|------|------|-----|
| 1 | TryHackMe「Intro to Docker」 | Dockerの基本とセキュリティリスクを解説付きで学ぶ | https://tryhackme.com/room/introtodockerk8pdqk |
| 2 | DVWA on Docker（WSL2） | コンテナを実際に立ち上げて使いながら、rootless化・Dockerfile改善を学ぶ | https://github.com/digininja/DVWA |
| 3 | Trivy | イメージスキャンを実際のコンテナに対して実行する | https://trivy.dev |

**学ぶべきこと**

- rootで動くコンテナからのホスト脱出（breakout）を実際に再現する
- Dockerfileのセキュリティアンチパターン（secrets埋め込み、`latest`タグ等）
- rootlessコンテナ、seccompプロファイルの適用
- イメージスキャン（Trivy）の実践

---

### 3. 認証・認可の実装脆弱性

**なぜ学ぶか**

JWTの誤用・セッション管理の欠陥は「自分で実装したシステム」で最も頻繁に発生する。OWASP Top 10の常連。

**推奨学習手段（既存教材優先）**

| 順序 | 教材 | 内容 | URL |
|------|------|------|-----|
| 1 | TryHackMe「Jr Penetration Tester」Web Hacking章 | JWT・セッション・OAuth脆弱性を実環境で体験（Premiumが必要） | https://tryhackme.com/path/outline/jrpenetrationtester |
| 2 | PortSwigger Web Security Academy | JWT・OAuth・認証バイパスの無料ラボ。業界標準の学習リソース | https://portswigger.net/web-security |

**学ぶべきこと**

- JWTの`alg:none`攻撃・秘密鍵漏洩を自分で再現する
- OAuthのCSRF・オープンリダイレクト脆弱性
- パスワードハッシュ（bcrypt vs MD5）の実際の解読速度比較
- RBACとABACの実装とバイパス手法

---

### 4. ネットワーク攻撃とTLS

**なぜ学ぶか**

「HTTPSを使えば安全」という誤解がまだ根強い。TLS設定の誤りは現実の攻撃対象になり続けている。

**推奨学習手段（既存教材優先）**

| 順序 | 教材 | 内容 | URL |
|------|------|------|-----|
| 1 | TryHackMe「Pre-Security」ネットワーク章 | TCP/IP・DNS・TLSの基礎を解説付きで学ぶ（一部無料） | https://tryhackme.com/path/outline/presecurity |
| 2 | mitmproxy（WSL2上） | 実際のHTTPS通信を傍受・改ざんして体験する | https://mitmproxy.org |

**学ぶべきこと**

- WSL2内で`tcpdump`で平文通信をキャプチャ、Windowsの Wireshark で解析する
- TLS証明書の検証をバイパスする実装ミスを再現
- WSL2内でmitmproxy（MITMプロキシ）を構築して通信傍受を体験
- DNS over HTTPS・証明書ピンニングの意義

---

### 5. Webアプリケーション脆弱性（OWASP Top 10）

**なぜ学ぶか**

Claude Codeで生成されるコードにも注入系脆弱性は混入する。ツールを使えるより、脆弱性を認識できる方が重要。

**推奨学習手段（既存教材のみ）**

| 順序 | 教材 | 内容 | URL |
|------|------|------|-----|
| 1 | DVWA（WSL2 Docker） | SQLi・XSS・CSRF・コマンドインジェクションを難易度Low→Highで実際に攻撃する | https://github.com/digininja/DVWA |
| 2 | OWASP Juice Shop（WSL2 Docker） | 実務に近い複合的な脆弱性を持つWebアプリ。SSRFも含む | https://owasp.org/www-project-juice-shop/ |
| 3 | PortSwigger Web Security Academy | OWASP Top 10全項目の無料ラボ | https://portswigger.net/web-security |

**学ぶべきこと**

- SQLインジェクション・XSSを自分でWSL2上に作って攻撃する
- SSRF（Server-Side Request Forgery）：クラウド環境での致命度を理解する（2021年にOWASP Top 10入り）
- XXE（XML外部エンティティ）インジェクション
- CSPヘッダの設計と回避手法

---

### 6. シークレット管理とサプライチェーン攻撃

**なぜ学ぶか**

2024〜2025年のインシデントの多くはAPIキーのGitHubへの誤コミット、依存パッケージへの悪意ある挿入。Claude Codeユーザーも無縁ではない。

**推奨学習手段（自作環境が活きる）**

| 順序 | 教材 | 内容 | URL |
|------|------|------|-----|
| 1 | truffleHog v3 | 自分の過去のリポジトリでシークレット漏洩をスキャンする | https://github.com/trufflesecurity/trufflehog |
| 2 | git-secrets | 自分のWSL2環境でコミット前にシークレット混入を検出する | https://github.com/awslabs/git-secrets |
| 3 | syft / cyclonedx | 自作プロジェクトのSBOM生成と依存関係監査 | https://github.com/anchore/syft |

**学ぶべきこと**

- truffleHog v3・git-secretsでシークレット漏洩を検出する
- npm/PyPIのタイポスクワッティング攻撃をシミュレートする
- HashiCorp Vault・環境変数・`.env`の安全な使い分け
- SBOMの生成と依存関係監査（`syft`・`cyclonedx`）

---

### 7. Infrastructure as Code（IaC）セキュリティ

**なぜ学ぶか**

TerraformやAnsibleで書かれたコードの設定ミスが大規模な侵害につながる。Claude Codeで自動生成したIaCは特に要検証。

**推奨学習手段（自作環境が活きる）**

| 順序 | 教材 | 内容 | URL |
|------|------|------|-----|
| 1 | Trivy（`trivy config`） | 自作TerraformコードをスキャンしてIAMミス・S3設定漏れを検出 | https://trivy.dev |
| 2 | checkov | TerraformやAnsibleのIaCコードを静的解析する | https://www.checkov.io |

**学ぶべきこと**

- `trivy config`によるTerraformコードのスキャン（※旧tfsecの機能はTrivyに統合済み）
- checkovによるIaCスキャンの実践
- 過剰な権限を持つIAMロールの具体的リスク
- 公開S3バケット・セキュリティグループの設定ミス再現

---

### 8. ロギング・モニタリングと検出回避

**なぜ学ぶか**

攻撃者は「侵入する」だけでなく「気づかれないようにする」技術を持つ。検出する側の視点なしにセキュリティは語れない。

**推奨学習手段（自作環境が活きる）**

| 順序 | 教材 | 内容 | URL |
|------|------|------|-----|
| 1 | auditd（WSL2） | 不審な操作をWSL2上で実際に検出・記録する | `man auditd` |
| 2 | Grafana Loki（WSL2 Docker） | ログ集約基盤を構築してアラートを設定する | https://grafana.com/oss/loki/ |

**学ぶべきこと**

- `auditd`で実際の不審な操作を検出する
- ログの改ざん・削除を試みて、それを防ぐアーキテクチャを設計する
- Grafana Lokiをログ集約基盤としてWSL2上に構築してアラートを作る
- ハニーポットの原理と実装

---

### 9. AI・LLM固有の脆弱性（最新トレンド）

**なぜ学ぶか**

Claude Codeを使う立場として、LLMが攻撃対象になるという視点が欠けると、自分が作るAI統合システムに致命的な穴が生まれる。

**推奨学習手段（自作環境が活きる）**

| 順序 | 教材 | 内容 | URL |
|------|------|------|-----|
| 1 | OWASP LLM Top 10 | 現行の脅威カタログを読んで攻撃カテゴリを把握する | https://owasp.org/www-project-top-10-for-large-language-model-applications/ |
| 2 | 自作LLMアプリへの攻撃再現 | 自分が作るAPIやエージェントにプロンプトインジェクションを仕掛けて検証する | — |

**学ぶべきこと**

- プロンプトインジェクション攻撃を自分のシステムで再現する
- 間接プロンプトインジェクション（Webページ・ファイル経由）
- LLMエージェントへの権限昇格・ツール悪用
- レートリミット・出力フィルタリングの設計

---

### 10. バイナリ・メモリ安全性の基礎

**なぜ学ぶか**

「自分はWebアプリしか作らないから関係ない」は誤り。使っているライブラリ・OSは全てバイナリで動く。CVEを読んで理解できるかどうかがここで決まる。

**推奨学習手段（既存教材優先）**

| 順序 | 教材 | 内容 | URL |
|------|------|------|-----|
| 1 | OverTheWire Narnia | バッファオーバーフロー・メモリ破壊をCTF形式で体験 | https://overthewire.org/wargames/narnia/ |
| 2 | gdb + pwndbg（WSL2） | 実際のバイナリをデバッガで解析する | https://github.com/pwndbg/pwndbg |

**学ぶべきこと**

- バッファオーバーフローをC言語で再現してgdbで観察する
- Rustが解決しようとしているメモリ安全性の問題の具体例
- CVEレポートを読んで影響を評価する訓練
- gdb・strace・ltraceによる動作解析（すべてWSL2で動作可）

---

## 学習進捗

| # | トピック | 開始日 | 完了日 | メモ |
|---|---------|--------|--------|------|
| 1 | Linux権限・プロセス分離 | 2026-05-10 | | 自作サイト作成済み。次はOverTheWire Banditへ |
| 2 | コンテナセキュリティ | | | |
| 3 | 認証・認可の実装脆弱性 | | | |
| 4 | ネットワーク攻撃とTLS | | | |
| 5 | Webアプリ脆弱性 | | | |
| 6 | シークレット管理 | | | |
| 7 | IaCセキュリティ | | | |
| 8 | ロギング・モニタリング | | | |
| 9 | AI・LLM脆弱性 | | | |
| 10 | バイナリ・メモリ安全性 | | | |
