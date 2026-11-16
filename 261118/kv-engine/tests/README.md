# テスト資材

[fixtures.md](fixtures.md)は段階0で定めた小さな公開用hex fixture。実DBファイルは保存しない。
fixtures_test.goが固定hexとencode/decodeの一致、末尾不完全、途中CRC破損を検証する。
単体テストはinternal/record・engine、CLIとプロセス終了コードはcmd/kvのテストに配置。
CLIのrun関数とmainの配線を一緒に検証しやすくするため、CLI統合テストもcmd/kvへ置いた。
復旧・ファイル切り戻しは未実装であり、fixtureの説明は段階2以降の動作仕様。
一時ファイル・将来のテストDBは必ず `t.TempDir()`、手動DBは `mktemp -d` 以下。
