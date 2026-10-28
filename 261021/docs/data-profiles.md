# データプロファイルとtarget分離

`DATA_PROFILE_ID`は、入力データ、予測対象、モデル名、保存先、運用設定を一組として選びます。プロファイル定義は[`configs/data-profiles/`](../configs/data-profiles/)にあり、APIの`/version`と`/ready`、学習report/model card、予測台帳にもIDとtarget contract IDを残します。

## UCI Bike Sharing（現行）

- profile: `uci-bike-sharing`
- target contract: `uci-bike-sharing-cnt-dataset-local-hour`
- 予測対象: UCI `hour.csv`の`cnt`。casualとregisteredのレンタルを合計した時間単位の値。
- 時刻: データセット内のnaive時刻。根拠のないUTC変換はしない。
- `timestamp + 3時間`でラベルが利用可能になる設定は、過去データを再生するための仮定。実際のfeed遅延を表さない。
- 従来のモデル名、DB、ポート、Compose起動方法を維持する。古いUCI要求ではprofile/target項目を省略できる。

## Capital Bikeshare（隔離された移行実験）

- profile: `capital-bikeshare`（`migration-only`）
- target contract: `capital-trip-starts-hourly-v1`
- 予測対象: trip履歴の`started_at`を時間単位に数えたtrip starts。UCI `cnt`と同じ計測対象とはみなさない。公式月次履歴の除外・集計母集団にも依存する。
- 時刻: `America/New_York`のローカル壁時計として解釈する。元の時刻文字列にoffsetがないため、UTC時刻は復元できない。
- 春の存在しない時間は作らない。秋に2回現れる曖昧な時間は、元データから区別できないため時間全体を集計から除外する。APIもその壁時計時刻を受け付けない。
- monthly archiveの公開タイミングと、実験上の`timestamp + 3時間`は別の概念。3時間は実測された公開SLAではない。
- 新しいCSV・manifest v2は`data/derived/capitalbikeshare-next/`に作成済み。元の41 ZIPを厳格検査して再集計したがCSV SHA-256は旧CSVと同じで、データ期間も2023-01〜2026-04のまま。これはmanifest/集計契約の再検証であり、新しい評価データではない。再利用済みmanifest v1は新protocolに使わない。
- Capital用SQLiteへ29,177時間をseedし、profile/target metadataと単一のdata hashを確認した。これはDB分離・hash保存の証拠で、隔離PostgreSQL/MLflow/Composeの起動やmodel loadingの証拠ではない。
- 未使用の次候補として公式2026年6〜9月archiveのURLへのHTTP HEADが200だったことを確認したが、ZIP内容はまだ取得していない。2026年10月archiveは確認時点で404。取得後は別pathに保存し、R06でvalidationと後続期間を凍結するまで候補学習・評価に使わない。
- このプロファイルにはまだ凍結済みの監視参照期間も承認済みmodelもない。APIを本番利用可能と表示したり、旧UCIモデル版3をCapital incumbentとして比較したりしない。

## APIの要求識別

Capitalの`POST /observations`と`POST /predict`には、両方を明示します。

```json
{
  "data_profile_id": "capital-bikeshare",
  "target_contract_id": "capital-trip-starts-hourly-v1"
}
```

片方の欠落、別profile、別target contractはHTTP 409で拒否します。UCIでは古いclientとの互換性のため、両方を省略できます。DBも初回初期化時にprofile/targetへ固定され、別profileからの再利用を拒否します。Capitalには別DB URL・別Compose projectを使います。

## Capital用の隔離Compose設定

次の設定確認はDocker Desktopが起動し、リポジトリrootに`.env`がある場合に実行できます。

```bash
cd ~/work/claude_practice/261021
docker compose -f compose.yaml -f compose.capital.yaml config -q
```

このCompose projectは`bike-mlops-capital`、DB/MLflowのvolumeも別名空間です。host portはPostgreSQL `55433`、MLflow `5001`、API blue/green `8101`/`8102`、入口 `8180`です。通常UCI stackの55432/5000/8001/8002/8080を置き換えません。MLflowのUI/APIは`http://127.0.0.1:5001`、入口は`http://127.0.0.1:8180`です。

Capital用ホストCLIは、profileとMLflow URLを同時に指定します。

```bash
DATA_PROFILE_ID=capital-bikeshare \
MLFLOW_TRACKING_URI=http://127.0.0.1:5001 \
  ./.venv/bin/mlops-bikectl data audit
```

`./scripts/bikectl`を使う場合も、先頭に`DATA_PROFILE_ID=capital-bikeshare`を付けるとCapital用のPostgreSQL `55433`とMLflow `5001`を選びます。UCI profileでは従来どおり`.env`の`APP_DB_HOST_PORT`（既定`55432`）とMLflow `5000`です。コマンド行で明示した`DATABASE_URL`と`MLFLOW_TRACKING_URI`は`.env`より優先されます。

厳格なmanifest v2とprofile別seedは検証済みですが、同targetのmodel・承認済みmonitor参照、隔離PostgreSQL/MLflowの実起動は未完了です。このためAPIはまだreadyになりません。決定済みのbootstrap基準と未使用期間の事前案は[`capital-bootstrap-gate.md`](capital-bootstrap-gate.md)を参照してください。基準の判断待ちは解消しましたが、候補作成・評価には[R06の実装とprotocol](../../261014/plan_mlops.md#r06-比較gateと昇格条件の完成)が必要です。実環境を起動する場合も先にCompose設定の適用・別port・profile表示を確認します。

## 特徴量の情報時点

現在の共通feature contractは予測対象時刻から計算できる暦情報と、同じtarget profileの24時間前/168時間前の確定countだけを参照します。未来に休日featureを追加するなら、対象時点で既知の暦だけを使います。天候を追加するなら予報発表時刻を保持した予報値に限定し、後から確定した対象時刻の実測天気を入力しません。
