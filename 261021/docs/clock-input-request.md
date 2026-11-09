# 時計調査を再開するために必要な入力

**2026-11-09 NICT方法追補:** [公式FAQの頻度条件と方法review](clock-nict-method-review.md)を追加。
必要入力は頻度/用途についての提供元の回答、認証/来歴・source誤差、固定境界の独立elapsedへの対応根拠。
問い合わせ文案は同文書に用意したが送信していない。今すぐservice起動や追加採取を実行する必要はない。
16秒下限だけで毎分取得可とは扱わず、受入済みPHC/task/peer原本は再依頼しない。

**2026-11-09 task/Web追補:** 新しい[タスク定義XMLのreview](clock-task-definition-review.md)で、
task_startedとForceSynchronizeTimeのCOM Action、現在の限定HKLM登録/DLL hashを確認した。
同じXMLの再採取は不要。停止caller/引数の内部意味、実源の根拠は未確定。
提示された[time.is/NICTの用途と制限](clock-web-source-review.md)を記録し、外部源候補が未提示という扱いは更新した。
次の資料は新しい停止/起動責任の根拠、またはNICT等のsource/輸送/真正性/elapsedの受入根拠。
基本queryやPHC/UDPを繰り返さず、service起動・設定変更は別の具体案/許可を必要とする。

**2026-11-09 resync後追補:** ユーザーの1回要求について[原本・Operational eventをreview済み](clock-resync-result.md)。
受信/同期開始/時刻設定（報告+2ms）/code0停止が同じ要求区間にあるが、事後queryは失敗し、現状態はStopped。
同じhelperを再実行しない。次はtask起動形態/停止責任の根拠、新しい自然event、独立source資料、または別途許可された保守比較。
service起動/追加同期/poll・task・log設定変更は今回の1回許可へ含めない。全資格と実fit/採点は保留。

**2026-11-09 peer追補:** `clock-host-cause-a52e422e-…`の[peer/status/identity review](clock-host-cause-review.md)も完了。
全体はno time data/未同期、peerはpending/現在のvalid sample0。host/serviceの前後identityとevent該当なしの診断も保存した。
同じbasic/peer/PHC採取を直後に再依頼しない。次の条件は新しい状態変化・自然event、独立UTC/elapsed資料、または明示許可後の[保守比較](clock-host-maintenance-plan.md)。
根本原因/同期責任の全体・独立源の真正性/不確かさ・全資格は未確定で、実fit/採点の保留は維持する。

**2026-11-09 host追補（peer追補前の履歴）:** `clock-host-admin-00f021d1-…`の[管理者runtime review](clock-host-runtime-review.md)でsource/configuration取得も完了。
設定peerはtime.windows.com、採取時はLI3/Local CMOS Clock。basic queryや同じPHC/UDPの再採取は不要。
原因を絞る新しい不足は[peer/verbose last-error・新イベントの限定読取](clock-owner-admin-read.md#host-w32time-cause-read)と、独立UTC/elapsedの根拠である。
hostの誤差量/現在の未同期原因/全同期責任は未確定。実資格・fit/採点の保留は維持する。

**2026-11-09更新（host追補前の履歴）:** owner `b9641680-…` とresources `ed1b37fc-…` の追加管理者原本は[照合済み](clock-resources-admin-review.md)。
採取区間のchronyd実体・PHC fd・UDP所有を確認できたため、同じinventory/PHC採取を再依頼しない。
次の不足は[hostの管理者限定query](clock-owner-admin-read.md#host-w32time-admin)、独立UTC/elapsedの根拠、同期責任等。
今回のhost statusは同期未実行/Local CMOS Clock、source/configurationは権限拒否で部分取得だった。
PHCやhostの自己申告を独立参照として採用しておらず、実時計資格・fit/採点は保留を維持する。

2026-11-08、基点`99f14cd`。追加管理者ログ・独立時計源の資料は、今回もユーザーから「まだ取得していない」と回答された。
同じtrace、非飽和値、原本公開状況の確認を繰り返さない。T09/T10の静的受入は完了済みで、再取得は不要。
この一覧は必要資料の具体化であり、新たな時計源の採用、OS変更、実fitの許可ではない。

## まず返してほしいもの

| 入力 | ユーザーから伝える最小限の内容 | Solが次に行うこと |
|---|---|---|
| 追加管理者ログ（指定2件は照合済み） | 同じPHC/socket採取は不要。新しい変更・自然再発等で追加資料ができた場合のみ保存先を伝える | 採取区間のidentity/所有の受入を保持し、新情報がある範囲だけ対応付ける。将来の再起動やidentity変更へ今回値を持ち越さない |
| 独立参照の候補 | 実際に利用可能な機器・サービス名、管理元、説明書/校正資料/原本の保存先。まだ無い項目は「未取得」 | 下記の依存関係・真正性・不確かさをreview。候補名だけでは採用しない |
| host/backendの同期資料 | basic/peer/status/identity queryは取得済み。新しい状態変化・自然event・管理責任を示す既存資料がある場合のみ保存先を伝える | 未同期/保留/現在有効sample0の受入を維持し、追加情報のある範囲で原因と各管理層の責任をreview。独立源や動的stackの代用にはしない |

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
