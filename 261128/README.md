# ファイル同期ソフト

2つのクライアントがlocalhostのハブを経由してファイルの作成・更新・削除を伝える。ハブはパスごとの期待版を確認して変更を受理し、再送には操作IDに対応する元の結果を返す。Phase 3では競合する候補の保存と手動解決、削除退避、状態を失った端末や世代変更後の端末の再登録を追加した。

- [ハブの起動、初回登録、双方向の同期](file-sync/docs/hub-sync.md)
- [競合の確認と解決、削除、端末の再登録](file-sync/docs/conflicts.md)
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
