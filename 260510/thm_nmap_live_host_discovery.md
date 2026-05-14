# TryHackMe: Nmap Live Host Discovery

> **ルーム:** [Nmap Live Host Discovery](https://tryhackme.com/room/nmap01)
> **学習日:** 2026-05-15
> **進捗:** Task 1〜2 完了（22%）/ Task 3〜 は翌日以降

---

## Task 2: Subnetworks（サブネットワーク）

### ネットワークの基本用語

| 用語 | 定義 |
|------|------|
| **Network Segment（ネットワークセグメント）** | EthernetスイッチやWi-Fiアクセスポイントなど、共有媒体で接続されたコンピュータのグループ。**物理的**な接続単位 |
| **Subnetwork / Subnet（サブネット）** | 同一ルーターを使うよう設定された1つ以上のセグメントをまとめた論理的なグループ。**論理的**な接続単位 |

ネットワークセグメントは物理、サブネットは論理という点が核心的な違い。

---

### サブネットのCIDR表記とホスト数

| CIDR | サブネットマスク | 最大ホスト数 | 用途例 |
|------|----------------|-------------|--------|
| `/16` | 255.255.0.0 | 約65,000台 | 大規模ネットワーク |
| `/24` | 255.255.255.0 | 約250台 | 一般的なオフィス・家庭LAN |

---

### ARPとサブネットの関係

**ARP（Address Resolution Protocol）とは**
- IPアドレスからMACアドレス（ハードウェアアドレス）を解決するプロトコル
- データリンク層（Layer 2）のプロトコル
- ARPクエリに応答があれば「そのホストはオンライン」と判断できる

**ARPの重要な制約**

```
ネットワークA (10.1.100.0/24)        ネットワークB (10.1.200.0/24)
[computer1] [computer2] [computer3]  [computer4] [computer5] [computer6]
        |           |                        |           |
      [switch1]----[router]---------------[switch2]
```

- **同一サブネット内**: ARPクエリは届く → ライブホストを検出できる
- **別サブネット**: ARPパケットはルーターを越えない（ルーティング不可）
- → **異なるサブネットのホストにはARPスキャンが使えない**

ARPはリンク層プロトコルであり、パケットはそのサブネット内にバインドされる。別サブネットへのパケットはデフォルトゲートウェイ（ルーター）経由で転送されるが、ARPクエリだけは越えられない。

---

### ネットワークシミュレーター演習（クイズ）

#### 演習1：computer1 → broadcast（computer1あて）でARP Request、Data: computer6

| 質問 | 答え | 解説 |
|------|------|------|
| ARPリクエストを受信できたデバイス数は？ | **4台** | 同一サブネット（switch1配下）のcomputer1〜3＋switch1 |
| computer6はARPリクエストを受信したか？ | **N（受信せず）** | computer6は別サブネット（switch2配下）のため届かない |

#### 演習2：computer4 → broadcast（computer4あて）でARP Request、Data: computer6

| 質問 | 答え | 解説 |
|------|------|------|
| ARPリクエストを受信できたデバイス数は？ | **4台** | 同一サブネット（switch2配下）のcomputer4〜6＋switch2 |
| computer6はARPリクエストに応答したか？ | **Yea（応答した）** | computer6はcomputer4と同一サブネット内のため到達・応答可能 |

→ 送信元が**同じサブネットに属しているか**が決定的な差になる。

---

## 明日の予定

**Task 3: Enumerating Targets** から再開

- nmap でスキャン対象を列挙する方法
- CIDR表記・ハイフン範囲・リストファイルの使い方

---

## 関連リソース

- [TryHackMe: Nmap Live Host Discovery](https://tryhackme.com/room/nmap01)
- [TryHackMe: Intro to LAN](https://tryhackme.com/room/introtolan) — サブネット基礎の補足
- [Nmap 公式ドキュメント: Host Discovery](https://nmap.org/book/man-host-discovery.html)
