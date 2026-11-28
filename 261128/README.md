# ファイル同期ソフト

2026-11-28にPhase 0で2つのクライアントと中央ハブの仕様・受入条件を定め、Phase 1で安全な走査とAからBへのローカルコピーを実装した。コピー先に反映予定を保存し、プロセスが途中で停止した場合は、ファイルの内容を照合して、安全に続けられる場合に再開する。

- [元計画](../261014/plan_file_sync.md)
- [プロトコルとデータ契約](file-sync/docs/protocol.md)
- [状態遷移、競合、永続化と復旧](file-sync/docs/design.md)
- [Phase 0〜5の受入条件](file-sync/docs/acceptance.md)
- [Phase 1の使い方と復旧手順](file-sync/docs/local-copy.md)
- [技術選定と再現手順](file-sync/docs/toolchain.md)
- [固定期待値とデモ用人工データ](file-sync/fixtures/expected.json)
- [作業記録](work_log.md)

MVPはlocalhost、LinuxまたはWSL2のLinuxファイルシステム、同期中に外部編集を止めたフォルダを対象にする。1ファイル10 MiB、各同期フォルダ1,000ファイル、ハブ総保存量2 GiBを上限とする。ハブはパスごとの版を比較して変更を受理し、削除をtombstoneとして記録する。競合が起きたら候補を保存し、元パスへの反映を保留する。

対象外はP2P、差分転送、自動マージ、常駐監視、GUI、複数OS、複数ハブ、E2EE、インターネット公開、ファイル属性の同期。人工データを使い、同期による削除も実演する。
