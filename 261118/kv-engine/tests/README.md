# テスト資材

[fixtures.md](fixtures.md)は段階0で定めた小さな公開用hex fixture。実DBファイルは保存しない。
fixtures_test.goが固定hexとencode/decodeの一致、末尾不完全、途中CRC破損を検証する。
単体テストはinternal/record・engine、CLIとプロセス終了コードはcmd/kvのテストに配置。
CLIのrun関数とmainの配線を一緒に検証しやすくするため、CLI統合テストもcmd/kvへ置いた。
段階2ではfixtureを一時ファイルへコピーし、正常Open・限定的な末尾復旧・破損拒否も検証。
原本hexとレコード形式は変更していない。
一時ファイル・将来のテストDBは必ず `t.TempDir()`、手動DBは `mktemp -d` 以下。
