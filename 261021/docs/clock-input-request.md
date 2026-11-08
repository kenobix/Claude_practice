# 時計調査を再開するために必要な入力

2026-11-08、基点`99f14cd`。追加管理者ログ・独立時計源の資料は、今回もユーザーから「まだ取得していない」と回答された。
同じtrace、非飽和値、原本公開状況の確認を繰り返さない。T09/T10の静的受入は完了済みで、再取得は不要。
この一覧は必要資料の具体化であり、新たな時計源の採用、OS変更、実fitの許可ではない。

## まず返してほしいもの

| 入力 | ユーザーから伝える最小限の内容 | Solが次に行うこと |
|---|---|---|
| 追加管理者ログ | [既存のPHC/socket限定採取手順](clock-owner-admin-read.md#chrony-resources-admin)で作成したWSL directoryのパス。非0終了でも削除しない | inventory/resources/reader-exit/hashと実完了行を読み、同じboot・開始tick・exe SHA/dev/inode・親・namespaceとPHC fd/UDP inodeの所有を照合 |
| 独立参照の候補 | 実際に利用可能な機器・サービス名、管理元、説明書/校正資料/原本の保存先。まだ無い項目は「未取得」 | 下記の依存関係・真正性・不確かさをreview。候補名だけでは採用しない |
| host/backendの同期資料 | 稼働中の構成で、どの管理層が何を参照して補正するかを示す限定ログ/設定/実装資料の保存先 | Windows→PHC→chronyd、Windows/backend→initd、Ubuntu timesyncdなどを役割別に整理。静的CALLの存在を動的setterの原因へ読み替えない |

管理者権限が使えない場合、その旨だけを伝えればよい。Ubuntu root、Codex許可、Windows管理者tokenは別である。
PHC/socketの旧PIDを手入力で固定せず、既存readerが現在の実体を探索する方式を使う。
古い`clock-owner-admin-053cb32639e54e04aea39f299e82711d/debug-shell.log`は照合済みの歴史的原本であり、今回のidentityを代用しない。
この一覧のために再起動・同期・service停止・時計変更・負荷試験を実行する必要はない。

## 独立源の資料に必要な項目

以下をprivateな新しい提出directoryへまとめる。文書形式は任意でよい。
空欄は未取得として残し、`independent=true`や`approved=true`などのflagで埋めない。
1つの物理参照がUTCとelapsedの両役を担うことは可能だが、それぞれの根拠を分けてreviewする。

| 必要資料 | 最低限含める内容 | 不足なら何が判断できないか |
|---|---|---|
| 源のidentity/管理元 | 機器/サービスの識別、管理者、UTC基準と経過秒の定義、読取方式、版/校正の有効範囲 | 別名/別IP/別processだけでは独立性を認定できない |
| 依存関係図 | host/backend/guest/QPC、PHC、同期・仮想化・電源・network経路との共有部分、共通障害の可能性 | QPCから作った秒数や同host UTCを独立elapsedとして採用できない |
| 真正性 | 署名/認証の検証原本、または監督下の取得手順とその記録、誰がいつどの方法で確認したか | SHAは受領後の内容結合のみ。源の本物性や採取前改変を証明しない |
| 源の精度根拠 | 校正/仕様資料の原本、誤差範囲、丸め/分解能、安定期間/有効期限、holdover等の条件 | 根拠のないゼロ誤差・宣伝値のみでは境界を計算できない |
| 生応答と要求対応 | nonce/sequence/読取順、source counter/UTC、source status、送受信bracketの整数値・単位/周波数、欠測/エラー | 応答がどの読取時点を覆うか、別要求への差替えがないか判断できない |
| 遅延/不確かさの式 | 輸送・読取遅延の上下限、source誤差/丸め/量子化を合成してUTC/elapsed intervalを作る式と根拠 | 対称networkや端点誤差相殺を仮定して、必要な精度へ切り詰めない |
| readerの再現資料 | entryだけでなく依存code/config/lockのbytesとSHA、実際の起動方法、raw→intervalの独立再計算方法 | 実装・設定・原本・計算結果の結合を確認できない |
| 固定policyへの事前適合性 | UTC±1秒、QPC比±100ppmを区間全体で満たす見込み。60秒×30窓と1800秒全体を区別 | 長区間だけの良好値や絶対UTC一致だけでは全校正にならない |

例示の提出構成（自動読取schemaではなく、必要資料の置き場所の案）:

```text
data/reports/clock-source-input-<new-id>/
  dossier.md              # 上表の根拠・未取得・依存関係
  originals/              # 提供元資料・生応答・限定設定原本
  reader/                 # 読取実装・依存manifest・負例記録
  uncertainty.md          # 単位/式/誤差項と各根拠の対応
  hashes.sha256           # 対象を明示した原本bytesのhash
```

上記directoryを作っただけでは採用にならない。秘密/認証key/全環境変数や無関係なprocess情報は採取しない。
原本はGit対象外へ保持し、公開要約・公開可能範囲・hashはSolがreviewしてから決める。
rawに機器識別や端末情報が含まれる場合、チャットへ全文を貼らず保存先だけを伝える。

## 必要精度を満たせない場合

[源選定reviewの理想予算](clock-source-readiness.md#固定policyから先に分かる不確かさの予算)では、
中心経過時間が完全一致し他の誤差が無い場合でも、60秒の各端点半径は約2.999700ms以下である。
これは候補源の実測/認定ではなく必要精度の計算で、実際にはsource/transport/readoutの誤差も必要になる。
独立参照を用意できない、またはその不確かさがこの条件へ入らない場合は、
**実時計資格と、それを前提とする実fit/採点・実運用測定/採否を保留する。**
新しい実行環境/別計測環境での評価を提案する場合も、別途identity・同期責任・真正性・全policy受入をreviewする。
別PCという名前だけで合格扱いにせず、現環境の過去性能を新環境の時計で遡及承認しない。

tick11000の内部値とkernel書込、T03のphase/stackは自然再発時に対応付ける。
未再発や静的解析だけでは過去の動的原因を解決しない。OS修復・保守/休止再開は具体案・影響・切戻しと別の許可が必要である。
当面の独立作業は[合成原本bundle](clock-raw-bundle.md)などのfixture検証に限定し、源の選定を先回りしない。
