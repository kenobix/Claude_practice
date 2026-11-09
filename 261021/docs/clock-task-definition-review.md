# 時刻同期タスクの定義と停止原因の境界

2026-11-09、基点`67f4586`。[証拠索引](reviews/20261109-task-source-checks.json)、
[前回resyncの実結果](clock-resync-result.md)、[残課題](../../261014/plan_mlops.md#remaining-work-latest)。

ユーザー提出の`clock-task-definition-c6cba967-567c-4f62-98f3-60e64ab3f850`は、XML2件とhashes.jsonの3原本。
manifestのSHA-256は両XMLの元bytesと一致した。原本をignoredな別directoryへsnapshotし、変更していない。
採取時UTC/QPC、host/build、service identity、実行履歴はこの提出物には含まれない。後から補わない。

## XMLから確認できた処理

| 項目 | SynchronizeTime | ForceSynchronizeTime |
|---|---|---|
| 実行主体 | LocalService（S-1-5-19）、HighestAvailable | 同左 |
| Action | `%windir%\system32\sc.exe`、引数`start w32time task_started` | COM ClassId `{A31AD6C2-FF4C-43D4-8E90-7101023096F9}`、Data `TimeSyncTask` |
| Trigger | WNF状態変更1件、Delay PT1M | WNF状態変更2件、一方Delay PT1M、他方Data 01 |
| 多重起動/ネットワーク | IgnoreNew / RunOnlyIfNetworkAvailable=true | 同左 |
| 電池への移行 | DisallowStartIfOnBatteries=false / StopIfGoingOnBatteries=false | 同左 |
| IdleSettings | StopOnIdleEnd=true / RestartOnIdle=false | 同左 |
| その他の明記 | StartWhenAvailable=true、Maintenance Period P1D / Deadline P1DT1H | これらの要素は未記載 |

WNFはWindowsの状態変更通知。状態番号の名前や、その通知が過去に発火したかは、このXMLだけでは分からない。
PT1Mはtriggerの遅延、P1D/P1DT1Hはmaintenanceの周期/期限であり、W32Timeの停止timeoutではない。
未記載のExecutionTimeLimitやRunOnlyIfIdle等へ独自の値を補って停止原因にしない。

[sc startの公式仕様](https://learn.microsoft.com/en-us/previous-versions/windows/it-pro/windows-server-2012-r2-and-2012/cc742126(v=ws.11))から、
`task_started`はサービスへ渡す起動引数と読める。ただし、この引数でW32Timeがどの内部branchへ進み、いつ終了するかは今回未確認。
旧版のcommunity Q&Aを現在buildの実装根拠にしない。

[StopOnIdleEnd](https://learn.microsoft.com/en-us/windows/win32/taskschd/idlesettings-stoponidleend)はタスクの終了条件。
`sc.exe`という起動用プログラムと、SCMが管理するサービスの寿命は別に確認が必要で、
この設定だけで「ユーザー操作によりW32Timeが停止した」とは結論できない。
前回の起動時刻近接・code0停止・TaskScheduler log無効という限界は変わらない。

## COM登録先の追加限定読取

[ClassId](https://learn.microsoft.com/en-us/windows/win32/taskschd/comhandleraction-classid)は処理を担うCOMクラスの識別子。
前回Execute/Argumentsがnullだったのは、ExecではないActionを未展開だったためで、処理の欠落ではなかった。
今回、Windowsの当該HKLMクラスのInprocServer32だけを64/32bit viewで読み、固定2 DLLの版/サイズ/SHAを取得した。

| 現在の静的読取 | 値 |
|---|---|
| OS版の報告 | 10.0.26200.0 |
| Registry64 | TimeSyncTask.dll、ThreadingModel=Both |
| Registry32 | 当該InprocServer32 keyなし |
| TimeSyncTask.dll | 45,056 bytes、版10.0.26100.1、SHA `995dc71eb07d6f6aa1e7bfec3abb5f5d0a9c9ec9788dd7019f4f09bc825da171` |
| w32time.dll | 745,472 bytes、版10.0.26100.1、SHA `3b8a03d852a067710eeafef6642466bda86841502d93743656cb2f166179e00c` |

登録はmachine-wideの静的値で、LocalServiceの有効な全override、過去のmodule load/COM activationを証明しない。
DLLをロード/実行せず、署名・発行者真正性の確認も今回行っていない。file hashは同一性確認に限定する。
通常sandboxはinteropのsocket拒否でexit1。許可付きの同じ限定readはexit0で、両試行を保持した。

## 検証と残件

UTF-8 BOMの実bytesにXML宣言encoding=UTF-16が残っていた。
これは前回提示した`Set-Content -Encoding UTF8`がexport文字列を保存した際の不一致で、ユーザーの操作ミスではない。
厳密UTF-8-sigでUnicode文字列へ復号してXML構造を読んだ。原本の宣言/bytes/hashを変更せず、再importに使用しない。

固定captureの10負例（SHA不一致、欠落/余剰、manifest重複、別UNC parent、traversal、別algorithm、
再hash後の不正XML/Actions欠落/URI差）を拒否した。製品unit追加や実資格の試験ではない。
offline-task-review-v1ではlocal UTCが0.254948秒逆行し、MONOTONIC差は正だった。
これは新しいwrapper観測であり、setter・tick11000・T03のphase/stackを取得したものではない。

停止caller/内部方針は未確定。許可なしのtask起動・service操作・event log有効化・追加resyncは行わない。
新しい動的資料か、[別許可が必要な1条件比較案](clock-host-maintenance-plan.md#service-start-comparison)をreviewする。
[Web時計源のreview](clock-web-source-review.md)も進めたが、源採用/全資格/実fitの保留を解除するものではない。
