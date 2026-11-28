# ファイル同期ソフト

2つのクライアントがlocalhostのハブを経由してファイルの作成・更新・削除を伝える。ハブはパスごとの期待版を確認して変更を受理し、再送には操作IDに対応する元の結果を返す。競合する候補は保存して手動解決を待ち、削除する本文は退避する。端末の状態を失った場合は明示的に再登録する。ハブを別の場所へ復元した場合も、新しい世代で起動してから端末を再登録する。

- [ハブの起動、初回登録、双方向の同期](file-sync/docs/hub-sync.md)
- [競合の確認と解決、削除、端末の再登録](file-sync/docs/conflicts.md)
- [ハブのバックアップと復元](file-sync/docs/backup-restore.md)
- [Phase 4の障害・容量試験の再現と結果](file-sync/docs/phase4-verification.md)
- [元計画](../261014/plan_file_sync.md)
- [プロトコルとデータ契約](file-sync/docs/protocol.md)
- [状態遷移、競合、永続化と復旧](file-sync/docs/design.md)
- [Phase 0〜5の受入条件](file-sync/docs/acceptance.md)
- [Phase 1の使い方と復旧手順](file-sync/docs/local-copy.md)
- [技術選定と再現手順](file-sync/docs/toolchain.md)
- [固定期待値とデモ用人工データ](file-sync/fixtures/expected.json)
- [作業記録](work_log.md)

MVPはlocalhost、LinuxまたはWSL2のLinuxファイルシステム、同期中に外部編集を止めたフォルダを対象にする。1ファイル10 MiB、各同期フォルダ1,000ファイル、ハブ総保存量2 GiBを上限とする。競合するパスは保留し、関連しない正常なパスは同期を続ける。世代不一致や走査の失敗では通常同期全体を止める。

対象外はP2P、差分転送、自動マージ、常駐監視、GUI、複数OS、複数ハブ、E2EE、インターネット公開、ファイル属性の同期。再現手順には人工データを使う。
