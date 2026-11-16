# テスト資材

[fixtures.md](fixtures.md)は小さな公開用hex fixture。実DBファイルは保存しない。
現時点では仕様の具体例であり、Goのdecoder/復旧テストは未実装。
段階1でhexを読み、期待値との照合テストへ接続する。
パッケージ単体テストは各internalパッケージ、CLI/プロセス統合テストはここへ配置する予定。
一時DBは必ず `t.TempDir()`、手動試験は `mktemp -d` 以下。
