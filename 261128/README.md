# ファイル同期ソフト

Phase 2では、2つのクライアントがlocalhostのハブを経由してファイルを作成・更新する。ハブはパスごとの期待版を確認して変更を受理し、再送には操作IDに対応する元の結果を返す。クライアントは本文を照合し、安全な反映と途中停止からの復旧にはPhase 1の処理を使う。

- [ハブの起動、初回登録、双方向の同期](file-sync/docs/hub-sync.md)
- [元計画](../261014/plan_file_sync.md)
- [プロトコルとデータ契約](file-sync/docs/protocol.md)
- [状態遷移、競合、永続化と復旧](file-sync/docs/design.md)
- [Phase 0〜5の受入条件](file-sync/docs/acceptance.md)
- [Phase 1の使い方と復旧手順](file-sync/docs/local-copy.md)
- [技術選定と再現手順](file-sync/docs/toolchain.md)
- [固定期待値とデモ用人工データ](file-sync/fixtures/expected.json)
- [作業記録](work_log.md)

MVPはlocalhost、LinuxまたはWSL2のLinuxファイルシステム、同期中に外部編集を止めたフォルダを対象にする。1ファイル10 MiB、各同期フォルダ1,000ファイル、ハブ総保存量2 GiBを上限とする。Phase 2で競合や削除を検出した場合は保留する。競合の解決、tombstoneの伝播、再登録はPhase 3で扱う。

対象外はP2P、差分転送、自動マージ、常駐監視、GUI、複数OS、複数ハブ、E2EE、インターネット公開、ファイル属性の同期。再現手順には人工データを使う。
