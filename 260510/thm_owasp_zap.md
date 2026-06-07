# TryHackMe: Introduction to OWASP ZAP

**URL**: https://tryhackme.com/room/learnowaspzap  
**進捗**: Task 1〜6 完了（60%）  
**目標**: OWASP ZAPの基本操作を習得し、Webアプリケーションの脆弱性診断ツールとして使えるようになる

---

## ルーム概要

**OWASP ZAP（Zed Attack Proxy）** はBurp Suiteの代替となるオープンソースのWebアプリケーション脆弱性スキャナー。  
自動スキャン・手動スキャン・認証付きスキャン・ブルートフォースなど幅広い機能を持つ。

### タスク一覧

| Task | タイトル | 状態 |
|------|----------|------|
| 1 | Intro to ZAP | ✅ |
| 2 | Disclaimer | ✅ |
| 3 | Installation | ✅ |
| 4 | How to perform an automated scan | ✅ |
| 5 | Manual Scanning | ✅ |
| 6 | Scanning an Authenticated Web Application | ✅ |
| 7 | Brute-force Directories | - |
| 8 | Bruteforce Web Login | - |
| 9 | ZAP Extensions | - |
| 10 | Further Reading | - |

---

## Task 1: Intro to ZAP

### ZAPとは何か

**Zed Attack Proxy** の略。BurpSuiteの代替となるOWASP公式のオープンソースWebアプリケーション脆弱性テストツール。

### BurpSuiteとの比較

| 機能 | ZAP | BurpSuite |
|------|-----|-----------|
| 自動Webアプリスキャン | 無料 | 有料（Pro） |
| Webスパイダリング | 無料 | 有料（Pro） |
| Intruder（ブルートフォース）速度制限 | なし | あり（Community版） |
| ライセンス | 完全オープンソース | プロプライエタリ |

ZAPの最大の特徴は「完全無料・オープンソース」。BurpProの有料機能をすべてコストゼロで利用できる。

### ZAPの核心：Attack Proxy（中間者攻撃の関所）

ZAPの本質はツール名が示す通り「Attack Proxy（攻撃用プロキシ）」。  
ブラウザとターゲットWebサーバーの**間**に立つ関所として機能する。

```
ブラウザ → [ZAP] → Webサーバー
            ↑
       ここで通信を傍受・改ざん
```

インターセプト機能をONにすると、ブラウザが送ったHTTPリクエストを空中で一時停止させ、  
中身（IDやパスワード、Cookieなど）を自由に書き換えてからサーバーへ送り込める。

### Quiz

- **What does ZAP stand for?** → `Zed Attack Proxy`

---

## Task 2: Disclaimer

ZAPは強力だが「唯一のツール」ではない。

- ZAPが**できないこと**：ログインタイミング攻撃（Burp Suiteが得意）
- ZAPはWebアプリテストの「入口」として優秀だが、複数ツールを組み合わせることが重要

実戦では「ZAPだけに依存しない」という姿勢がプロのペンテスターの基本。

---

## Task 3: Installation

### AttackBox環境でのZAP起動

TryHackMeのAttackBoxにはZAPがプリインストール済み。インストール作業は不要。

**起動方法（2通り）：**

```bash
# 方法A：ターミナルから起動（確実）
zaproxy

# 方法B：GUIメニューから
Applications → Web Application Analysis → OWASP ZAP
```

**初回起動時のセッション選択：**  
「Do you want to persist the ZAP Session?」→ `No, I do not want to persist this session at this moment in time` を選択して Start。  
使い捨ての演習環境なのでセッション保存は不要。

### 注意：Burp Suiteと混同しない

AttackBoxのタスクバーにある「青い稲妻アイコン」はBurp Suite。ZAPではない。  
→ GUIメニュー（Applications）またはターミナルコマンドで起動すること。

**アップデートポップアップが出た場合：** `Cancel` を選択。  
使い捨て環境でのメジャーアップデートは時間の無駄かつ環境変化のリスクあり。

---

## Task 4: How to perform an automated scan

### 自動スキャンの概要

ZAPのトップ画面の「Automated Scan」ボタンからワンクリックで実行できる。  
内部では **Traditional Spider**（静的クローリング）と **Ajax Spider**（動的クローリング）の2種類が走る。

| スパイダー | 動作原理 | 特徴 |
|-----------|---------|------|
| Traditional Spider | リンクを辿って静的にURL収集 | 静か・高速・浅い |
| Ajax Spider | ブラウザを動かしてJSレンダリング後のDOMを解析 | 重い・詳細・深い |

### Ajax Spiderのバックエンド選択

Ajax Spiderはブラウザエンジンを使う。AttackBox上で使用可能なオプション：

- **Firefox Headless**：エラーが発生する場合あり（AttackBoxの権限問題）
- **HtmlUnit**：旧式だが環境依存が少なく安定
- **Chrome Headless**：代替候補

### Task 4 実行手順（HtmlUnit使用）

```bash
# Step 1：ライブラリのインストール（必要な場合）
sudo apt update
sudo apt install libjenkins-htmlunit-core-js-java
```

ZAP設定：
- **URL to attack**: `http://[ターゲットIP]`（※毎回TryHackMeで確認）
- **Use traditional spider**: チェックあり
- **Use ajax spider**: チェックあり
- **with**: `HtmlUnit` を選択

### 自動スキャンの限界：認証の壁

ターゲット（DVWA）はログイン画面で保護されているため、認証情報を持たない自動スキャナは「壁の外側」しか探索できない。

```
自動スキャナ → login.php にぶつかる → ここで探索停止
                ↑
        ID/パスワードを知らないので壁を越えられない
```

→ スキャン結果に `login.php`、`robots.txt`、`sitemap.xml` 程度しか表示されないのは、ツールの不具合ではなく**仕様通りの限界**。  
→ 解決策は Task 6（Authenticated Scan）で学ぶ。

**重要な教訓：** 「Complete（完了）」という表示は「完璧に実行できた」ではなく「現在の条件でできることをやり終えた」を意味する。エラーや限界の存在を常に疑え。

### 実行結果と確認

**ターミナルで `sudo apt install libjenkins-htmlunit-core-js-java` を試みた結果：**  
`Network is unreachable` エラーが大量に出力された。  
→ AttackBoxは外部インターネット（Ubuntuパッケージサーバー等）への通信を遮断された隔離環境のため。  
→ ただし ZAP 自体に HtmlUnit が内包されているため、スキャン自体は実行可能だった。

**Ajax Spider の実行結果：**  
クロールされたURL は17個。内訳は `login.php`、`robots.txt`、ログイン画面へ強制送還される `302 Found` のリダイレクトばかり。

```
AJAX Spider がクロールした範囲：

http://[IP]/login.php       → ログイン画面（壁）
http://[IP]/robots.txt      → 静的ファイル
302 Found × 多数             → ログイン画面へリダイレクト
─────────────────────────────
壁の向こう側（脆弱なページ群）は発見できず
```

**結論：** HtmlUnit を使おうが Firefox Headless を使おうが、認証情報を与えられていない自動スキャナはログイン画面の前でウロウロして終わる。DVWAの本当の脆弱性はログイン画面の「奥」にあり、自動スキャンだけでは到達できない。

---

## Task 5: Manual Scanning

### プロキシ構成（ZAP ⇔ Firefox間）

ZAPを「中間者」として機能させるための準備として、以下3点を設定する。

1. **ZAP側のローカルプロキシ設定**：`127.0.0.1:8080` で待ち受け
2. **ZAPのルート証明書（CA証明書）をFirefoxへインポート**
3. **Firefox側の手動プロキシ設定**：ZAPと同じアドレス・ポートを指定

### なぜ証明書のインポートが必要なのか（TLS傍受の構造）

通常のHTTPS通信は中間者攻撃を防ぐために暗号化されている。ZAPを間に挟んでも、証明書を信頼させない限り「暗号化されたゴミデータ」しか見えず、ブラウザは偽造証明書を検知して通信を遮断する。

ZAPのルート証明書をFirefoxにインポートする行為は、ブラウザに対して「今後ZAPが発行するどんな偽造証明書も無条件で信頼しろ」と命令することに等しい。これは企業が従業員の暗号化通信を監査する際の手法と全く同じ仕組み。**自分のブラウザに意図的にセキュリティホールを開けている**という自覚を持って作業すること。

### Quiz

- **What IP do we use for the proxy?** → `127.0.0.1`

### 手動インターセプトの動作確認（マニュアル外の検証手順）

設定が機能しているかを自分の目で確かめる手順：

1. ZAP上部ツールバーの「Set break on all requests and responses」（緑の丸）をクリックして赤色（ブレーク状態）に変更
2. Firefoxでターゲット（DVWAのIP）にアクセス → 通信が空中で停止する
3. ZAP画面に、ブラウザが送ったHTTPリクエストの生データが表示される
4. 「▶（Submit and step to next request）」を押してパケットをサーバーへ解放 → 初めて画面が表示される

この「通信を凍結し、内容を確認し、解放する」一連の流れが手動プロキシの本質。

### つまずいたポイント：プロキシ設定が保存できない（ポート競合）

**症状：** ZAPのプロキシ設定保存時に `Unable to listen on this address and port: 127.0.0.1:8080` エラー

**原因の調査：**
```bash
sudo lsof -i :8080
# COMMAND  PID USER   FD   TYPE DEVICE SIZE/OFF NODE NAME
# java    2897 root   52u  IPv6  52113      0t0  TCP localhost:http-alt (LISTEN)

sudo netstat -tulpn | grep 8080
# tcp6  0  0  127.0.0.1:8080  :::*  LISTEN  2897/java
```

`PID 2897` の `java` プロセスが8080番を占有していた。  
**重要な気付き**：Burp SuiteもZAPもJavaで動くアプリケーション。このプロセスがZAP自身を起動した後の状態であれば、`kill -9` で殺すと自分が使っているツールを自滅させることになる。**コマンドの出力結果を文字列としてではなく、システムの実態と紐付けて読む**ことが重要。

→ 今回はシステム再起動によりポート競合は自然に解消された。

### Cookie（PHPSESSID）の確認方法

「認証トークンを渡す画面」とは、ブラウザの**開発者ツール（DevTools）**のこと。

```
F12 または 右クリック → Inspect（要素を調査）
  → 「Storage」タブ
    → 「Cookies」を展開
      → 対象IPを選択
        → PHPSESSID（名前と値）が表示される
```

WebサイトはログインしたユーザーをセッションID（整理券）で識別し、Cookieに保存させる。この値をZAPに渡すことで、ZAPを「ログイン済みユーザー」として振る舞わせることができる。

---

## Task 6: Scanning an Authenticated Web Application

### 目的

未認証スキャン（Task 4）が「ログインの壁」を越えられなかった問題を、**認証済みセッションをZAPに渡す**ことで突破する。

### 事前準備

1. DVWAにログイン（admin / 設定済みパスワード）
2. DVWA Security タブで Security Level を **Low** に設定して送信
3. 開発者ツールから `PHPSESSID` の値を取得（Task 5参照）

### 認証済みスキャンの実行手順

```
1. ZAP下部タブ群の緑色「+」アイコン → 「HTTP Sessions」タブを追加

2. 左側 Sites ツリーでターゲットURLを選択
   → HTTP Sessions タブにセッション一覧が表示される
   → 取得した PHPSESSID と一致する行を右クリック
   → 「Set as Active（アクティブとして設定）」を選択

3. 再度スキャンを実行
   （Automated Scan の Attack、または Sites ツリーの右クリック →
    Attack → Spider）
```

### 結果：認証あり/なしでのスキャン深度の劇的な違い

| 項目 | Task 4（未認証） | Task 6（認証済み） |
|------|-----------------|---------------------|
| 到達できたURL | `login.php`、`robots.txt` 程度 | `/vulnerabilities/sqli/`、`/vulnerabilities/exec/`、`/vulnerabilities/csrf/`、`/vulnerabilities/upload/` など多数 |
| Alerts（脆弱性検知数） | わずか | 新たに約21件の脆弱性兆候を検出 |
| Sitesツリー | スカスカ | DVWA全体の構造が露出 |

```
【未認証スキャン】                【認証済みスキャン（PHPSESSID投入）】

ZAP → login.php                  ZAP（通行証あり）→ login.php を通過
       ↓ ここで停止                     ↓
     （壁の中に入れない）          /vulnerabilities/sqli/
                                  /vulnerabilities/exec/
                                  /vulnerabilities/csrf/
                                  /vulnerabilities/upload/ ...
```

**結論：** ツールの性能差ではなく、「どのコンテキスト（権限・セッション）で走らせるか」というパラメータの差が、スキャン結果を天と地ほど変える。Webアプリケーションの脆弱性診断において、認証済みスキャンは必須のステップ。

---
