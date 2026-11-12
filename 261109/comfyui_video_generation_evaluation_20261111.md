# Wan 2.2 TI2V-5Bによる画像から動画への生成実験

- 実験日：2026-11-11（JST）
- 記録・成果物確認日：2026-11-12（JST）
- 関連記録：[ComfyUI画像生成・画像編集](comfyui_image_generation_evaluation_20261111.md)、[環境構築記録](desktop_ai_setup_report.md)、[README](README.md)
- 情報源：利用者から共有された条件・生成時間・評価と、Windows内の既存MP4・保存済みワークフローJSONの読み取り確認。
- 保存作業ではモデル取得、ComfyUI更新、設定変更、追加の画像・動画生成を行っていない。コピー元も変更していない。

## 1. 実験目的

RTX 5070 Ti 16GBで、FLUX.2 Kleinによる編集済み画像をWan 2.2 TI2V-5Bへ渡し、約3秒の動画を生成する。動きの指示を変更した後、約5秒へ延長し、生成時間と構図・背景の維持を確認する。

前回の画像実験記録にある「動画生成は未実施」は、その記録時点の履歴として保持する。本記録は、その後に行われた3回の動画生成の報告である。

## 2. PC・ソフトウェア環境

| 項目 | 実験時の構成・確認範囲 |
|---|---|
| OS | Windows 11 |
| GPU | NVIDIA GeForce RTX 5070 Ti、VRAM 16GB |
| RAM | 32GB |
| ソフトウェア | ComfyUI Desktop |
| 実行方式 | Windows上のLocal / NVIDIA |
| タスク | Image to Video |
| 拡散モデル | Wan 2.2 TI2V-5B FP16 |

前回の画像実験にはComfy Desktop 1.1.9（インストール時）、ComfyUI v0.44.2、PyTorch 2.12.1+cu130という管理画面の観測がある。今回の動画実行時のバージョンを独立に確認していないため、同一だったと断定しない。ワークフローのノードにある`ver: 0.3.45`や`frontendVersion: 1.27.10`も、実行環境のバージョン確認へ読み替えない。

## 3. Wan 2.2 TI2V-5Bの導入

3本のMP4の実行メタデータは、いずれも次のモデル名を参照していた。利用者から動画生成の成功が報告され、今回実在する出力ファイルのデコードも確認した。取得日時・ダウンロード操作・完了ログ・モデル本体のハッシュは未確認。

| 役割 | ファイル名 | 配置カテゴリ |
|---|---|---|
| 拡散モデル | `wan2.2_ti2v_5B_fp16.safetensors` | `models/diffusion_models/` |
| テキストエンコーダー | `umt5_xxl_fp8_e4m3fn_scaled.safetensors` | `models/text_encoders/` |
| VAE | `wan2.2_vae.safetensors` | `models/vae/` |

配置カテゴリは保存ワークフローのモデル案内に対応する。UNETLoaderの`weight_dtype`は`default`、CLIPLoaderのtypeは`wan`、deviceは`default`。FP16は拡散モデルの指定であり、エンコーダーまでFP16だったという意味ではない。モデル本体は読み取り検査やGitへのコピーの対象に含めていない。

## 4. Image to Videoの構成

各MP4に埋め込まれた実行グラフ`prompt`は12ノードで、すべて保存出力へ接続していた。

```text
UNETLoader → ModelSamplingSD3（shift 8.0）→ KSampler
CLIPLoader → 正・負のCLIPTextEncode → KSampler
LoadImage + VAELoader → Wan22ImageToVideoLatent → KSampler
KSampler → VAEDecode → CreateVideo（16fps）→ SaveVideo
```

| 共通設定 | 確認値 |
|---|---|
| 幅 × 高さ | 512×512 |
| FPS | 16 |
| Steps / CFG | 20 / 5.0 |
| Shift | 8.0（ModelSamplingSD3） |
| Sampler / Scheduler | uni_pc / simple |
| Batch size / denoise | 1 / 1.0 |
| 保存プレフィックス | `video/ComfyUI` |
| 実ファイルの映像形式 | MP4、H.264、音声トラックなし |

SaveVideoのformat/codecは`auto`。CreateVideoのcodec表示`none`を、実MP4が未圧縮という意味には扱わない。実ファイルの映像コーデックはデコーダーでH.264と確認した。

## 5. 入力画像とプロンプト

3回とも、LoadImageは`Flux2-Klein-4b-base_00001_.png`を参照する。Comfy共有`input`内の同名PNGと、前回保存した[編集後画像](comfyui/images/Flux2-Klein-4b-base_00001_.png)がバイト一致することを確認した。PNGの重複コピーは追加していない。

![入力：Kleinで編集したピンク色のレトロカー](comfyui/images/Flux2-Klein-4b-base_00001_.png)

### 実験1：車を前進させる

```text
The pastel pink vintage car slowly drives forward.
The pink flowers gently sway in the breeze.
The camera remains stationary.
Smooth natural motion, soft spring lighting.
Keep the original car design and background.
```

### 実験2・3：車を静止させて花を揺らす

```text
A pastel pink vintage car is parked and remains completely still.
Pink flowers gently sway in a soft spring breeze.
The camera is fixed and does not move.
Subtle, smooth, realistic motion.
Keep the car's shape, wheels, flowers, lighting, and background consistent.
```

以上は利用者提示文とMP4の実行プロンプトが一致した。負のプロンプトも3回共通で、実行メタデータと保存JSONに以下が残っている。

```text
色调艳丽，过曝，静态，细节模糊不清，字幕，风格，作品，画作，画面，静止，整体发灰，最差质量，低质量，JPEG压缩残留，丑陋的，残缺的，多余的手指，画得不好的手部，画得不好的脸部，畸形的，毁容的，形态畸形的肢体，手指融合，静止不动的画面，杂乱的背景，三条腿，背景人很多，倒着走
```

車の静止を求める正のプロンプトと、静止表現を含む負のプロンプトの組み合わせについて、影響を切り分ける実験はしていない。今回の不自然さの原因とは断定しない。

## 6. 各実験の設定値

| 実験 | 正のプロンプト | フレーム数 | FPS | 実行時シード |
|---|---|---:|---:|---:|
| 1 | 前進 | 49 | 16 | 898471028164125 |
| 2 | 静止・花の揺れ | 49 | 16 | 957973766335659 |
| 3 | 実験2と同じ | 81 | 16 | 563329347091355 |

第4節の基本設定は一致するが、**シードは3回とも異なる**。実験3ではフレーム数だけでなく実行シードも変わっているため、結果の差を動画の長さだけの効果とは扱わない。

保存済みJSONは実験2・3の正のプロンプト、81フレームを持つ。一方、表示シードは`1108903282368322`、制御は`randomize`であり、上表のどの実行シードとも一致しない。各回の再現にはMP4の実行グラフを基準にする。

## 7. 生成時間と結果の比較

| 実験 | 保存MP4 | フレーム数 / 名目尺 | 生成時間（利用者実測） | 結果・利用者評価 |
|---|---|---|---:|---|
| 1：前進 | [ComfyUI_00001_.mp4](comfyui/videos/ComfyUI_00001_.mp4) | 49 / 3.0625秒（約3秒） | 42.15秒 | MP4生成成功。動きに不自然さあり |
| 2：静止・花の揺れ | [ComfyUI_00002_.mp4](comfyui/videos/ComfyUI_00002_.mp4) | 49 / 3.0625秒（約3秒） | 22.18秒 | MP4生成成功。前回より動きが分かりやすいという評価 |
| 3：約5秒へ延長 | [ComfyUI_00003_.mp4](comfyui/videos/ComfyUI_00003_.mp4) | 81 / 5.0625秒（約5秒） | 34.27秒 | MP4生成成功。大きな花や緑色の形状が背景へ出現 |

名目尺は確認済みフレーム数÷16fpsで計算した。コンテナのトラック時間は実験1が約3.0620秒、実験2が3.0625秒、実験3が5.0625秒で、ごく小さな差がある。詳細はmanifestへ保存した。

**生成時間は利用者からの実測報告で、今回再計測していない**。MP4に生成所要時間のログはないため、42.15／22.18／34.27秒の独立検証はできていない。ファイル名・時系列・プロンプト・設定から対応づけた。モデルのロード状態、シード、キャッシュ等を統制していないため、実験2の短時間化をプロンプト改善による高速化とは断定しない。

最大VRAM使用量、GPU使用率、GPU温度、CPU/RAM使用量は今回未測定・未確認。前回のSchnell等の測定値を転用しない。

## 8. 動画品質と確認された課題

- 利用者評価では実験1の動きは不自然で、実験2は動きが分かりやすくなった。ただし、車・カメラの完全な静止を達成したとは判定していない。
- 保存確認では各動画の先頭・途中・末尾から6フレームずつを抽出し、代表フレームを目視した。車や花の位置・形状の変化があり、実験1には画面下の黄色い形状、実験2には背景の縦方向の模様も見られた。
- 実験3では元画像にない大きなピンクの花や緑色の形状、色調の変化を確認した。入力の主要な車は残るが、構図・背景を完全に維持した結果ではない。
- 代表フレームの確認は、実時間再生による動きの滑らかさの独立評価ではない。画質・時間的一貫性の定量指標や複数人評価は実施していない。
- 各条件1回でシードも異なる。プロンプトや尺による品質差、モデルの一般的な能力・優劣は結論づけない。

## 9. 保存ファイルと再現手順

### 成果物と照合

Windowsの実際の保存場所は共有出力先の`video`サブディレクトリだった。公開記録にはユーザー固有パスを含めず、以下で表す。

```text
MP4: %LOCALAPPDATA%/Comfy-Desktop/ComfyUI-Shared/output/video/
JSON: %LOCALAPPDATA%/Comfy-Desktop/ComfyUI-Installs/ComfyUI/ComfyUI/user/default/workflows/
```

| ファイル | サイズ | 元ファイルの最終更新日時（JST、秒まで） |
|---|---:|---|
| `ComfyUI_00001_.mp4` | 438,645 bytes | 2026-11-11 17:06:45 |
| `ComfyUI_00002_.mp4` | 624,533 bytes | 2026-11-11 17:12:41 |
| `ComfyUI_00003_.mp4` | 819,498 bytes | 2026-11-11 17:24:10 |
| [video_wan2_2_5B_ti2v.json](comfyui/workflows/video_wan2_2_5B_ti2v.json) | 16,150 bytes | 2026-11-11 18:04:46 |

MP4合計は1,882,676 bytes（約1.80 MiB）、JSONを含めて1,898,826 bytes（約1.81 MiB）。小容量のため変換せず、4ファイルを原本とバイト一致する形で保存した。各SHA256、サイズ、更新日時、実行設定、デコードしたフレーム数は[動画manifest](comfyui/video_artifact_manifest_20261111.json)で確認できる。日時はファイルシステムの観測であり、生成時間の測定には使用しない。

JSONはWindows側に実在した保存済みファイルのコピーであり、今回新しく作ったワークフローではない。各MP4にはその回の`workflow`と`prompt`が埋め込まれている。初回・2回目の専用JSONが別ファイルとして保存されていたとは主張しない。

### 再現する場合の手順（今回は未実行）

1. 既存Comfy環境で第3節のモデル・エンコーダー・VAEを使用可能にする。本リポジトリに重みやPython環境は含めない。
2. 保存JSONを読み込み、LoadImageを有効にし、[保存済み入力PNG](comfyui/images/Flux2-Klein-4b-base_00001_.png)を入力として選ぶ。LoadImage → Wan22ImageToVideoLatentのstart_image接続を確認する。
3. 512×512、batch 1、20 steps、CFG 5.0、shift 8.0、uni_pc / simple、denoise 1.0、16fpsを確認する。
4. 第5節の正・負のプロンプトを設定し、第6節のフレーム数と実行シードを指定する。保存JSONの`randomize`は固定制御へ変更してから実行する。保存JSONの表示シードを当時の実行シードと混同しない。
5. SaveVideoの保存先を確認する。JSONやMP4から読み込んだ直後の無操作再実行は今回検証していない。環境や重みのハッシュも固定しておらず、同一画素の再現は保証しない。
6. 次回はモデルロード状態、生成時間の測定区間、実行シード、VRAMピーク、動画の背景・車体の維持を実行ごとに記録する。

### 公開前の安全確認

既存のWindows側PyAVで3本を読み取り、49／49／81フレームすべてをエラーなくデコードした。解析スクリプト・確認用の縮小画像は一時領域だけに作成し、公開対象には含めない。元動画、元JSON、入力画像、Comfy環境を変更していない。

MP4のコンテナ・ストリームメタデータ、埋め込みJSON内の文字列、保存JSONを確認した。コンテナ情報は`major_brand`、`minor_version`、`compatible_brands`、`workflow`、`prompt`、`encoder`で、encoderは`Lavf63.1.102`。ストリームの情報は言語未指定`und`と`VideoHandler`だった。認証情報・個人情報・ユーザー固有の絶対パスは見つからず、映像の代表フレームにも公開すべきでない内容は見つからなかった。

対象は本記録、README追記、MP4 3本、既存ワークフロー1件のコピー、動画manifestだけ。モデル、認証設定、キャッシュ、仮想環境、インストーラー、無関係な画像・動画は追加しない。

## 10. 次に検証するモデルと未完了事項

- 約3秒の初回生成、プロンプト変更、約5秒への延長はいずれも実施済み。次は同じ入力・固定シードで車体と背景の維持を確認し、通常の実験中に生成時間・VRAMも記録する。
- 車の完全な静止、背景の完全維持、花だけの動きは未達成または未検証。正・負のプロンプトの整合も、条件を切り分けて確認する。
- 動画実行時のソフトウェアバージョン、モデルのハッシュ、ロード状態、測定区間は未確認。保存ワークフローの再実行も未検証。
- **次に試す別モデルは未選定**。今回の観測だけで新モデル導入や優劣を決めず、Wan 2.2の一貫性の確認を先に行う。新たなモデルの取得・更新・生成は今回実施していない。
