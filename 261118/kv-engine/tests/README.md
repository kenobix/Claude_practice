# テスト資材

[fixtures.md](fixtures.md)には段階0で定めた小さな公開用fixtureを16進表記で掲載している。実DBファイルは保存しない。
fixtures_test.goでは、固定のバイト列とencode/decodeの結果が一致することに加え、不完全な末尾と途中レコードのCRC不一致の扱いも検証する。
単体テストはinternal/record・engine、CLIとプロセス終了コードはcmd/kvのテストに配置。
CLIのrun関数とmainの配線を一緒に検証しやすくするため、CLI統合テストもcmd/kvへ置いた。
段階2ではfixtureを一時ファイルへコピーし、正常Open・限定的な末尾復旧・破損拒否も検証。
原本hexとレコード形式は変更していない。
一時ファイル・将来のテストDBは必ず `t.TempDir()`、手動DBは `mktemp -d` 以下。
