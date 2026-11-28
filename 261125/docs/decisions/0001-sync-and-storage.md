# 0001 共同編集・保存方式の選定

2026年11月25日に採用したMVPの構成と判断理由。 [元計画](../../../261014/plan_collaborative_editor.md)

| 項目 | 判断と理由 |
| --- | --- |
| CRDT | 本文をYjsの `Y.Text` で管理する。3個の `Y.Doc` で同時挿入・削除、逆順、重複、保留後の収束を再現した。G-Counterは学習用の別実験とし、値の増加を複製ごとに記録してマージする仕組みを確認した。 |
| 編集画面 | React、TypeScript、CodeMirror 6と `y-codemirror.next` を使う。元計画の候補を採用し、エディターからの変更を差分でYjsに反映する。npmのpeer dependencyでは `y-codemirror.next@0.3.5` が Yjs 13.6.31とCodeMirror 6を受け付ける。Phase 1で実際の組み合わせを固定する。 |
| WebSocket | `y-websocket` を参考に、文書認可と永続化後の `ACK` を持つ独自メッセージを採用する。文書を変更するバイナリは `UPDATE` に一本化する。Yjs 13.6.31のV1形式を使い、Awarenessは `y-protocols@1.0.7` を初期候補とする。 |
| 依存待ちを含む更新 | コピーに適用した段階で保留状態を検査する。保留分が残る更新は受理せず、依存分を含む差分を再送する。Yjs 13.6.31では `doc.store.pendingStructs` と `doc.store.pendingDs` に保留が残ることを実験で確認した。内部フィールドへの依存はサーバーの検査処理に限り、版の更新時に両ケースを試験し直す。 |
| 端末内保存 | IndexedDBの独自アダプターでYjs状態と送信待ち更新を同じトランザクションに保存する。 `y-indexeddb` はYjs状態の保存に使えるが、今回は受理IDと保存完了の通知まで同じトランザクションで管理するため、自動保存プロバイダーを組み込まない。 |
| SQLite | 単一Node.jsプロセスで `node:sqlite` を使う。Node.js 22.22.2では実験的APIだが、別のネイティブアドオンを導入せずファイルへのコミットと再読込を確認できた。DBアクセスは同期APIのため、Phase 4で配送時間への影響を計測する。 |
| 開発・運用 | 現環境ではローカルのNode.jsでPhase 1を進める。Docker Composeと永続ボリュームは利用可能な環境で確認する。MVPはローカルデモを対象とする。常時公開にはWebSocket接続と永続ディスクを維持できる実行環境が必要になる。 |

## 元計画から具体化した点

Yjsのstate vectorは、どの構造まで統合したかを表す。削除だけを取りこぼした場合にstate vectorが一致することを確認したため、復帰時はベクトルの比較だけで送信を省略しない。完全なYjs状態の受信と、端末の差分および削除情報の送信を行い、受理の確認には更新ごとの `ACK` を使う。 [削除だけの更新](../../work_log.md#削除だけの更新)

閲覧への変更、アクセスの失効、再付与をまたぐ下書きは、サーバーが管理する利用者ごとの付与版で識別する。再付与後に旧版の編集を自動で送ると、利用者が下書きを整理する前に共同文書へ混ざる。旧版の下書きはテキストとして書き出せる。

本文100,000 UTF-16コード単位、Yjs更新と完全状態それぞれ8 MiB、同時3接続をMVPの受入条件として採用する。実験では文字列とYjsの直列化サイズを測った。WebSocketやDB経由で上限ちょうどの更新を扱う試験は実装後に行う。 [測定値](../../work_log.md#本文とバイナリのサイズ)

## 参照資料

- [Yjs Document Updates](https://docs.yjs.dev/api/document-updates)
- [y-websocket](https://docs.yjs.dev/ecosystem/connection-provider/y-websocket)・[Yjs Awareness](https://docs.yjs.dev/api/about-awareness)
- [Yjs Offline Support](https://docs.yjs.dev/getting-started/allowing-offline-editing)
- [CodeMirror 6のYjs連携](https://github.com/yjs/y-codemirror.next)・[y-protocols](https://github.com/yjs/y-protocols)
- [Node.js 22のSQLite API](https://nodejs.org/docs/latest-v22.x/api/sqlite.html)・[SQLiteの同期設定](https://www.sqlite.org/pragma.html#pragma_synchronous)
- [リポジトリのホスティング方針](../../../260805/hosting_policy.md)
