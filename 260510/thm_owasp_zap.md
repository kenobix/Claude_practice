# TryHackMe: Introduction to OWASP ZAP

**URL**: https://tryhackme.com/room/learnowaspzap  
**進捗**: Task 1〜3 完了、Task 4 作業中（40%）  
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
| 4 | How to perform an automated scan | 🔄 作業中 |
| 5 | Manual Scanning | - |
| 6 | Scanning an Authenticated Web Application | - |
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

## Task 4: How to perform an automated scan（作業中）

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

---
