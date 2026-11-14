# ComfyUI画像生成・画像編集の記録

FLUXとQwenの画像実験をまとめる。実験日は2026-11-10〜12、種類別の整理日は2026-11-14。
[全体の索引](README.md)、[動画実験](video_experiments.md)も参照。

画像生成・編集は利用者の好みに合うQwen Image 2.1 Turboを主力とし、FLUX.2 Kleinは使い分けの候補として残す。
これは好みに基づく判断で、客観的な画質・速度の順位ではない。各時間は利用者の実測報告で、今回再生成・再測定していない。

- [FLUX.1 Schnell・FLUX.2 Klein](#flux)
- [Qwen Image 2.1 Turbo（英語・日本語・編集）](#qwen)
- [QwenとKleinの編集比較](#qwen-13-qwenとflux2-kleinの編集比較追記)
- [今後のモデル運用方針](#qwen-15-今後のモデル運用方針利用者の判断)

各部の節番号と「未実施」は元記録の時点を保持する。前半にある動画未実施の記述は、後続のWan実験で進展している。
PNG・JSON・manifestは`comfyui/`配下をそのまま使用する。

<a id="flux"></a>

## ComfyUI画像生成・画像編集の検証記録

- 実験日：2026-11-10〜11（JST）
- 記録日：2026-11-11
- 関連記録：[PC・WSLの環境構築](environment.md#environment)、[ディレクトリREADME](README.md)
- 情報源：利用者が共有した管理画面の観測・処理時間・GPU測定値と、Windows内の既存PNG・保存済みワークフローJSONの読み取り確認。
- 今回の保存作業では、追加生成、モデル取得、ComfyUI更新、既存環境の変更は行っていない。

<a id="flux-1-目的"></a>

### 1. 目的

RTX 5070 Ti 16GBのPCでComfyUIを起動し、FLUX.1 Schnellによる画像生成、日本語・英語プロンプトの固定シード比較、FLUX.2 Klein 4Bによる新規生成と参照画像編集を試す。生成できること、所要時間、メモリ使用、指示に対する出力を記録する。

処理時間・VRAM・温度は利用者共有値であり、今回再測定していない。PNGから確認したのは実画像、解像度、プロンプト、実行グラフ、モデルファイル名等で、PNGに処理時間・GPU測定ログは埋め込まれていなかった。

<a id="flux-2-pcソフトウェア環境"></a>

### 2. PC・ソフトウェア環境

| 項目 | 実験時の構成・確認範囲 |
|---|---|
| OS | Windows 11 |
| GPU | NVIDIA GeForce RTX 5070 Ti、VRAM 16GB |
| システムRAM | 32GB |
| Comfy Desktop | 1.1.9（インストール時の観測） |
| ComfyUI | v0.44.2（管理画面での観測） |
| PyTorch | 2.12.1+cu130（管理画面で確認） |
| 実行方式 | Local / Public Builds / NVIDIA |
| 保存場所 | Comfy Desktopの共有ディレクトリの`models`、`output` |
| 更新 | 最新版への更新は未実施。記載バージョンを推測で変更しない |

このPyTorchはWindows側のComfy環境の記録であり、既存レポートにあるWSLの`ai-lab`（PyTorch 2.14.1+cu130）とは別環境。PNGのノードに記録された`cnr_id`・`ver`や`frontendVersion`も、今回管理画面で観測したComfyUI/PyTorchの実行バージョンへ読み替えない。

<a id="flux-3-導入と発生したエラー"></a>

### 3. 導入と発生したエラー

1. WindowsへComfy Desktop 1.1.9を導入し、Local / Public Builds / NVIDIAの構成でインスタンスを作成した。
2. 初回は通信エラー`ERR_CONNECTION_RESET`で環境作成に失敗した。
3. その後インスタンスを作り直し、正常に起動した。通信エラーの根本原因は未特定で、ネットワーク設定等を変更したとは記録しない。
4. 管理画面でComfyUI v0.44.2とPyTorch 2.12.1+cu130を確認した。
5. 共有保存先のモデル・出力を使用し、SchnellとKleinの画像生成・編集を実施した。

保存確認では、指定されたWindowsの共有出力先が実在したため、保存先を推測する必要はなかった。公開用の表記はユーザー名を含めず、以下とする。

```text
%LOCALAPPDATA%\Comfy-Desktop\ComfyUI-Shared\output
%LOCALAPPDATA%\Comfy-Desktop\ComfyUI-Shared\models
```

WSLから指定されたWindows側のディレクトリを読み取った。インストーラー、Python環境、モデル本体は保存対象に含めていない。

<a id="flux-4-flux1-schnellの設定と生成結果"></a>

### 4. FLUX.1 Schnellの設定と生成結果

PNGの実行グラフで次の設定を確認した。

| 項目 | 設定 |
|---|---|
| タスク | Text to Image（入力画像なし） |
| 解像度 | 1024×1024、batch size 1 |
| ステップ | 4 |
| CFG | 1.0 |
| Guidance | `CLIPTextEncodeFlux`の設定値3.5 |
| サンプラー / スケジューラー | euler / simple |
| denoise | 1.0 |
| 拡散モデル | `flux1-schnell.safetensors` |
| テキストエンコーダー | `clip_l.safetensors`、`t5xxl_fp16.safetensors` |
| VAE | `ae.safetensors` |

利用者の測定では初回生成が**28.34秒**、ウォームアップ後は**約7〜8秒**。初回と定常時を別に記録し、差の全てをモデルロード時間とは断定しない。

`ComfyUI_00001_.png`は出力フォルダー内で最も古いSchnell画像で、初回生成の保存候補として採用した。レトロカーとピンクの花を確認できる。ただし、処理時間の実行ID付きログは今回提示されておらず、28.34秒とこのファイルの厳密な対応は未検証。

![Schnellの最初の保存画像候補](comfyui/images/ComfyUI_00001_.png)

<a id="flux-5-日本語英語プロンプトの比較"></a>

### 5. 日本語・英語プロンプトの比較

<a id="flux-固定条件とプロンプト"></a>

#### 固定条件とプロンプト

`ComfyUI_00005_.png`と`ComfyUI_00006_.png`の実行グラフはいずれもシード**123456789**で、前節の設定とモデル構成を使用していた。プロンプト以外の主要な生成パラメーターは一致している。

日本語の`clip_l`：

```text
淡いミントグリーンの小さなレトロカー、ピンク色のコスモスの花、パステルカラー、かわいい3Dイラスト、柔らかな光、シンプルな黄緑色の背景
```

日本語の`t5xxl`：

```text
淡いミントグリーンの小さなレトロカーが、画面中央に横向きで停まっている。車の窓からピンク色のコスモスの花がたくさんあふれている。背景はシンプルな淡い黄緑色。柔らかな春の日差しが車と花を照らしている。かわいい3Dイラスト風で、車全体が見える構図。背景に人や他の車は存在しない。
```

英語の`clip_l`：

```text
A small mint-green retro car, pink cosmos flowers, pastel colors, cute 3D illustration, soft lighting, simple pale green background
```

英語の`t5xxl`：

```text
A small mint-green retro car is parked sideways in the center of the image. Many pink cosmos flowers are overflowing from the car's windows. The background is simple and pale green. Soft spring sunlight illuminates the car and flowers. The scene has a cute 3D illustration style, with the entire car visible in the composition. There are no people or other cars in the background.
```

<a id="flux-共有された測定値"></a>

#### 共有された測定値

| 項目 | 日本語 | 英語 |
|---|---:|---:|
| 生成時間 | 7.73秒 | 7.53秒 |
| 最大VRAM使用量 | 15,694 MiB | 15,658 MiB |
| 最高GPU温度 | 49℃ | 50℃ |

最大GPU使用率は100%との共有結果。計測器・サンプリング間隔・処理時間の厳密な計測区間は今回未確認であり、これらをPNGの埋め込み情報から得た値とは扱わない。

| 日本語入力の出力 | 英語入力の出力 |
|---|---|
| ![日本語固定シード：建物風の出力](comfyui/images/ComfyUI_00005_.png) | ![英語固定シード：ミント色の車と花](comfyui/images/ComfyUI_00006_.png) |

両方で画像ファイルの生成には成功したが、今回確認した日本語入力の画像は**文字状の模様を伴う建物風の出力で、車の指示に沿っていなかった**。英語入力ではミント色の車とピンクの花が確認できる。この1回ずつの観測を、言語全体の品質・速度・モデル能力の差へ一般化しない。反復測定、別シード、別プロンプトによる評価は未実施。

<a id="flux-6-flux2-klein-4bのtext-to-image"></a>

### 6. FLUX.2 Klein 4BのText to Image

保存PNG`Flux2-Klein_00001_.png`は**1024×1024**。埋め込みの幅・高さも1024であり、画面表示の違いより実ファイルの情報を優先した。

保存時の実行グラフで`LoadImage`や参照画像の経路がないことを確認した。プロンプトに「色を変更し、構図を維持する」という表現があっても、元画像を渡していないため**新規生成**である。

| 項目 | PNGの実行グラフで確認した構成 |
|---|---|
| 拡散モデル | `flux-2-klein-base-4b.safetensors` |
| テキストエンコーダー | `qwen_3_4b.safetensors`、type `flux2` |
| VAE | `flux2-vae.safetensors` |
| ステップ / CFG | 20 / 5.0 |
| サンプラー / スケジューラー | euler / Flux2Scheduler |
| 実行時シード | 0（PNG内の値） |
| 処理時間 | 約36.17秒（利用者共有値） |

実行に使われたファイルは名前に`base`を含むモデル。保存されたキャンバスには別の`flux-2-klein-4b.safetensors`の枝もあるが、この出力の実行モデルと混同しない。

```text
Change the car's color from mint green to pastel pink.
Keep the car's shape, the flowers, the composition, and the background unchanged.
```

結果はピンク色の車と白い花を配した画像。ミント色の元画像を編集した結果ではなく、新たな車・花・背景が生成された。

![Kleinによる新規生成](comfyui/images/Flux2-Klein_00001_.png)

<a id="flux-7-flux2-klein-4bのimage-edit"></a>

### 7. FLUX.2 Klein 4BのImage Edit

実行グラフの保存出力へつながる`LoadImage`は`ComfyUI_00006_.png`を参照していた。英語固定シード比較で生成したミントグリーンの車が編集元であり、公開保存でも同じPNGを使用する。

指示：

```text
Change only the car color from mint green to pastel pink. Keep the flowers, composition, camera angle, and background unchanged.
```

| 項目 | PNGの実行グラフで確認した構成 |
|---|---|
| 拡散モデル | `flux-2-klein-base-4b-fp8.safetensors` |
| テキストエンコーダー | `qwen_3_4b.safetensors`、type `flux2` |
| VAE | `full_encoder_small_decoder.safetensors` |
| ステップ / CFG | 20 / 5.0 |
| サンプラー / スケジューラー | euler / Flux2Scheduler |
| 実行時シード | 1111443136920027 |
| 入力処理 | `ImageScaleToTotalPixels`、1.0 megapixels、nearest-exact |
| 参照経路 | VAEEncode → ReferenceLatent（正・負のconditioningへ接続） |
| 出力解像度 | 1024×1024（PNG実寸） |
| 処理時間 | 43.92秒（利用者共有値） |

新規生成とはモデルファイルとVAEが異なるため、単に参照画像の有無だけを切り替えた比較ではない。

| 編集前（英語Schnell出力） | 編集後（Klein） |
|---|---|
| ![ミントグリーンの車](comfyui/images/ComfyUI_00006_.png) | ![パステルピンクへ編集した車](comfyui/images/Flux2-Klein-4b-base_00001_.png) |

車体の主要な色がピンクになり、車の向き、主要な構図、花と背景は概ね維持された。一方で、花の細かな配置やホイール周辺の色も変化しており、**車の色以外を完全に固定できたわけではない**。定量的な画像差分や画質指標は測定していない。

<a id="flux-8-生成速度vram品質の整理"></a>

### 8. 生成速度・VRAM・品質の整理

| 実験 | 処理時間（共有値） | 最大VRAM（共有値） | 今回の実画像確認 |
|---|---:|---:|---|
| Schnell初回 | 28.34秒 | 未確認 | 最古の保存PNGを候補として保存 |
| Schnellウォーム後 | 約7〜8秒 | 測定別の値のみ | 定常時の反復数は未確認 |
| Schnell日本語・固定シード | 7.73秒 | 15,694 MiB | 建物風の出力。車の指示には不一致 |
| Schnell英語・固定シード | 7.53秒 | 15,658 MiB | ミント色の車と花 |
| Klein新規生成 | 約36.17秒 | 未確認 | ピンク色の車、新規構図 |
| Klein画像編集 | 43.92秒 | 未確認 | 色変更成功、細部にも変化 |

モデル、ステップ数、CFG、VAE、入力の有無、プロンプト、ウォーム状態が揃っていないため、表の所要時間だけでモデルの優劣を判定しない。KleinのVRAM・GPU温度・最大GPU使用率は未確認で、Schnellの測定値を転用しない。

<a id="flux-9-保存ファイルと再現手順"></a>

### 9. 保存ファイルと再現手順

<a id="flux-対応づけと日時"></a>

#### 対応づけと日時

Windowsの作成日時を読み取り、JSTへ換算した。日時はファイルシステムの観測値であり、生成時間の検証には使わない。

| 保存PNG | 役割 | Windows作成日時（JST、秒まで表示） |
|---|---|---|
| [ComfyUI_00001_.png](comfyui/images/ComfyUI_00001_.png) | 初回出力候補 | 2026-11-11 06:23:44 |
| [ComfyUI_00005_.png](comfyui/images/ComfyUI_00005_.png) | 日本語、シード123456789 | 2026-11-11 06:38:26 |
| [ComfyUI_00006_.png](comfyui/images/ComfyUI_00006_.png) | 英語、シード123456789、編集前 | 2026-11-11 06:39:45 |
| [Flux2-Klein_00001_.png](comfyui/images/Flux2-Klein_00001_.png) | Klein新規生成 | 2026-11-11 09:22:30 |
| [Flux2-Klein-4b-base_00001_.png](comfyui/images/Flux2-Klein-4b-base_00001_.png) | Klein編集後 | 2026-11-11 09:55:00 |

モデル、プロンプト、シード、参照経路で対応づけたPNGのみ採用した。`ComfyUI_00002_.png`〜`00004_`は今回の固定シード比較ではないため追加しない。初回候補以外についても、時間・GPU測定ログの実行IDとPNGを照合したわけではない。

個別に保存されていた次の3JSONも、内容を変更せずコピーした。PNGから新しくワークフローを作ったと主張するものではない。

- [flux_schnell_full_text_to_image.json](comfyui/workflows/flux_schnell_full_text_to_image.json)
- [image_flux2_klein_text_to_image.json](comfyui/workflows/image_flux2_klein_text_to_image.json)
- [image_flux2_klein_image_edit_4b_base.json](comfyui/workflows/image_flux2_klein_image_edit_4b_base.json)

JSONの元保存先は`%LOCALAPPDATA%/Comfy-Desktop/ComfyUI-Installs/ComfyUI/ComfyUI/user/default/workflows`。PNGとJSONのSHA256・サイズ、PNGの日時・解像度・実行モデル・参照画像の対応は[artifact_manifest.json](comfyui/artifact_manifest.json)に記録した。画像5枚＋JSON3件の合計は5,089,764 bytes（約4.85 MiB）。

<a id="flux-再現する場合の手順今回は未実行"></a>

#### 再現する場合の手順（今回は未実行）

1. 実験時のComfy環境を用意し、各節で確認したモデル・エンコーダー・VAEを対応するカテゴリへ配置する。本リポジトリに重みや環境は含めていない。
2. 保存済みのワークフローJSONを読み込む。各PNGにも`workflow`と実行グラフ`prompt`が残っているため、測定対象画像のプロンプト・シードはPNG側を確認する。
3. Schnellの日英比較は第5節の2エンコーダー入力を設定し、シード123456789、4ステップ、CFG 1.0等を固定する。初回候補は別プロンプト・別シードであり混ぜない。
4. Klein新規生成は入力画像なしのbaseモデル側の枝を使う。編集は`ComfyUI_00006_.png`をComfyの入力画像として指定し、第7節のモデル・プロンプトを使う。
5. 保存キャンバスには未使用のテンプレート枝がある。編集ワークフローの`pink_tone_chair.png`は今回の車の保存出力に接続していない入力であり、その画像はコピーしていない。車の枝だけを対象にし、不要な枝は読み込み後に無効化等を確認する。
6. 保存JSONには`randomize`やサブグラフの`proxyWidgetErrorQuarantine`が残る。次回実行前にシード制御とリンクを確認し、PNGの実行値を基準にする。保存JSONの再実行は今回未検証で、読み込み直後の無操作で同じ結果になるとは保証しない。

<a id="flux-公開前の確認"></a>

#### 公開前の確認

選択したPNGを目視し、全テキストメタデータと保存JSONを点検した。公開対象に秘密値・個人識別情報・ユーザー固有の絶対パスは見つからなかった。PNGのチャンクは`IHDR`、`tEXt`、`IDAT`、`IEND`で、埋め込み文字列は`prompt`と`workflow`。JSON内の文字列も確認し、個人設定・認証情報・モデル本体はコピーしていない。

PNGとJSONは原本とバイト一致するコピーで、メタデータや画像の加工はしていない。SHA256とPNGチャンクCRCを検査した。公開対象はこの記録、READMEの追記、画像5枚、ワークフロー3件、検査したファイルのmanifestのみ。

<a id="flux-10-限界と次の作業"></a>

### 10. 限界と次の作業

- 言語比較は各1回。速度差・品質差を一般化せず、今回の日本語入力の指示不一致も観測として残す。
- 再生成、反復測定、画質の定量評価は未実施。モデルファイルの内容・ハッシュや実行環境一式も固定していない。
- Kleinでは新規生成と編集でモデル・VAEが異なる。未測定のVRAM等を推定で埋めない。
- 更新は未実施。ワークフローのテンプレートに更新案内が含まれていても、更新したことにはしない。
- **次の優先タスクはWan 2.2 TI2V-5Bでの動画生成**。まず約3秒で初回検証し、成功後に約5秒へ進め、生成時間・VRAM・画質を記録する。
- 動画生成は未実施で、成功・モデル導入済みとは扱わない。今回動画生成を開始していない。

<a id="qwen"></a>

## Qwen Image 2.1 Turbo INT8による画像生成実験

- 実験・記録日：2026-11-12（JST）
- 関連記録：[FLUXによる画像生成・編集](image_experiments.md#flux)、[Wan 2.2動画生成](video_experiments.md#wan)、[README](README.md)
- 情報源：利用者共有の実験条件・生成時間と、Windows内の既存PNG・保存済みワークフローJSONの読み取り確認。
- 今回の保存作業では追加生成、モデル取得、ComfyUI更新、既存環境の設定変更を行っていない。Windows側の原本も変更していない。

第1〜10節は英語プロンプト2回を保存した時点の記録を保持している。後続の日本語生成・参照画像編集と運用方針は第11〜15節に追記した。「日本語・画像編集は未検証」等の初回記述は、その時点の履歴である。

<a id="qwen-1-実験目的"></a>

### 1. 実験目的

RTX 5070 Ti 16GBでQwen Image 2.1 Turbo INT8 ConvRotによるText to Imageを試し、同じ英語プロンプトから736×736と1024×1024のPNGを生成する。生成成功、共有された処理時間、実画像の指示追従性を記録する。

各条件1回の実験で、ウォーム状態やシードを固定した性能比較ではない。画像の解像度・設定と、利用者が測定した生成時間の出所を区別する。

<a id="qwen-2-pcソフトウェア環境"></a>

### 2. PC・ソフトウェア環境

| 項目 | 実験時の構成・確認範囲 |
|---|---|
| OS | Windows 11 |
| GPU | NVIDIA GeForce RTX 5070 Ti、VRAM 16GB |
| システムRAM | 32GB |
| ソフトウェア | ComfyUI Desktop |
| 生成方式 | Text to Image |
| 使用モデル | Qwen Image 2.1 Turbo INT8 ConvRot |

前回の画像実験記録にあるComfy Desktop 1.1.9（導入時）、ComfyUI v0.44.2、PyTorch 2.12.1+cu130は当時の観測として保持する。今回の実行時バージョンは独立に確認していない。PNG内のキャンバスには`frontendVersion: 1.57.6`、保存JSONには`1.55.14`が残るが、これらをバックエンドや実行時のDesktopバージョンに読み替えない。

<a id="qwen-3-使用モデルと配布元"></a>

### 3. 使用モデルと配布元

PNGの実行グラフと保存ワークフローで、次のファイル名を確認した。

| 役割 | 指定ファイル名 | ワークフローの配置案内 |
|---|---|---|
| 拡散モデル | `qwen_image_2.1_turbo_int8_convrot.safetensors` | `models/diffusion_models/` |
| 画像生成用テキストエンコーダー | `qwen3vl_8b_int8_convrot.safetensors` | `models/text_encoders/` |
| VAE | `qwen_image_2.1_vae_bf16.safetensors` | `models/vae/` |

保存テンプレートの配布案内は[Comfy-Org/Qwen-Image-2.1（Hugging Face）](https://huggingface.co/Comfy-Org/Qwen-Image-2.1)。2026-11-12に配布ページを確認し、ComfyUI向けに再構成したモデルであることと、上記ファイルの配置案内を確認した。テンプレートには[ModelScopeの同名配布先](https://modelscope.cn/models/Comfy-Org/Qwen-Image-2.1)もあるが、今回そのページの本文は取得できなかった。利用者が実際にどちらから取得したかは未確認。

任意のプロンプト補強用として`qwen3.5_9b_qwen_image_2.1_pe_t2i.int8_convrot.safetensors`もグラフに含まれる。ただし今回は`refine_prompt=false`で、入力文を直接使う側が選択されている。このモデル名がグラフに存在することだけで、補強モデルをロード・実行したとは判断しない。

モデル本体の内容・ハッシュやダウンロード完了ログは今回確認していない。重み、仮想環境、キャッシュはGitへ追加しない。

<a id="qwen-4-ワークフロー構成"></a>

### 4. ワークフロー構成

```text
ResolutionSelector → EmptyLatentImage ─────────────────────┐
入力プロンプト → ComfySwitchNode(false) → PreviewAny       │
    → TextEncodeQwenImage21 + CLIPLoader → CFGGuider       │
UNETLoader → QwenImage21Cache → CFGGuider                  │
RandomNoise + ManualSigmas + KSamplerSelect(euler)         │
    → SamplerCustomAdvanced（潜在画像・CFGGuiderを使用）←───┘
    → VAEDecode → SaveImageAdvanced
```

| 設定 | PNGの実行グラフで確認した値 |
|---|---|
| Sampler | euler |
| Steps | 8相当。ManualSigmasの9値による8区間で指定 |
| CFG | 1.0 |
| refine_prompt | false（ComfySwitchNodeのswitch=false） |
| thinking_mode | false（TextGenerateのthinking=false、外側の設定もfalse） |
| 負のプロンプト | 空文字 |
| Batch size | 1 |
| アスペクト比 / multiple | 1:1 (Square) / 8 |
| ResolutionSelector | 実験1は0.5 MP、実験2は1.0 MP |
| UNETのweight_dtype | default |
| CLIPLoaderのtype / device | qwen_image / default |
| QwenImage21Cacheのdevice / dtype | auto / default |
| 保存 | PNG、8-bit、入力カラースペースsRGB |

ManualSigmasの値：

```text
1.0, 0.978453, 0.95418, 0.926626, 0.89508, 0.845148, 0.704534, 0.414568, 0
```

「8ステップ」は利用者提示のTurbo標準設定と保存テンプレートの説明に対応し、PNGでも上記スケジュールを確認できた。通常のKSamplerのsteps欄に8が記録されている構成ではない。

PNGの提出グラフは19ノード。switch=falseの選択先をたどると、保存画像の依存経路は16ノードとなり、TextGenerate・補強用CLIPLoader・補強用システムプロンプトの3ノードは選択外になる。これはグラフ上の選択経路の確認であり、各ノードの実行ログや所要時間を測定したものではない。LoadImageはなく、参照画像編集ではない。

`TextEncodeQwenImage21`の`resolution=1024`やサブグラフ内の幅・高さ表示と、実際の出力解像度は区別する。出力の幅・高さはResolutionSelectorから接続されており、736×736はPNG実寸で確認した。

<a id="qwen-保存成果物と再現メモ"></a>

#### 保存成果物と再現メモ

| 保存ファイル | サイズ | コピー元の最終更新日時（JST、秒まで） |
|---|---:|---|
| [Qwen_image_2.1_00001.png](comfyui/images/Qwen_image_2.1_00001.png) | 655,981 bytes | 2026-11-12 12:09:20 |
| [Qwen_image_2.1_00002.png](comfyui/images/Qwen_image_2.1_00002.png) | 1,241,780 bytes | 2026-11-12 12:11:09 |
| [image_qwen_image_2_1_turbo_t2i.json](comfyui/workflows/image_qwen_image_2_1_turbo_t2i.json) | 56,934 bytes | 2026-11-12 12:11:00 |

実際のコピー元は次のディレクトリ。公開用の記録ではユーザー名を含めない。

```text
PNG: %LOCALAPPDATA%/Comfy-Desktop/ComfyUI-Shared/output/
JSON: %LOCALAPPDATA%/Comfy-Desktop/ComfyUI-Installs/ComfyUI/ComfyUI/user/default/workflows/
```

3ファイルを内容変更せずコピーした。合計1,954,695 bytes（約1.86 MiB）。SHA256、解像度、シード、設定、日時、PNGメタデータの確認結果は[今回のmanifest](comfyui/qwen_image_2_1_artifact_manifest_20261112.json)に保存した。既存のFLUX・Wanの成果物とmanifestは変更していない。

再現する場合は既存環境へ保存JSONを読み込み、第5節のプロンプト、refine_prompt/thinking_mode=false、モデル3種、8区間のsigmasを確認する。ResolutionSelectorを実験1では0.5 MP、実験2では1.0 MPにし、RandomNoiseへ各PNGの実行シードを設定する。保存JSONのRandomNoiseには`randomize`があるため、固定する場合は実行前に制御を変更する。

外側のサブグラフのseed表示`447606998181262`は、PNGの実行RandomNoiseのシードとは一致しない。保存JSONの内部RandomNoiseは実験2の値を持つ。画面の表示値をそのまま当時の実行値とは扱わない。JSONからの再実行は今回検証していない。

<a id="qwen-公開前の確認"></a>

#### 公開前の確認

PNG2枚の内容を目視し、全チャンクのCRC、テキストメタデータ、埋め込みJSON、保存JSONを検査した。チャンクは`IHDR`、`pHYs`、`tEXt`、`IDAT`、`IEND`。`pHYs`はピクセル密度情報で、テキスト項目は`prompt`と`workflow`だった。秘密情報・個人情報・ユーザー固有の絶対パスは見つからなかった。

画像・JSONは原本とバイト一致し、メタデータ除去や画像加工はしていない。コピーしたJSONは保存済みの実在ファイルであり、今回PNGから新規作成したワークフローではない。解析用スクリプトや一時ファイル、他の実験の成果物は追加しない。

<a id="qwen-5-使用プロンプト"></a>

### 5. 使用プロンプト

2枚のPNGで次の英語プロンプトが一致した。refine_prompt=falseの選択側に、この文がそのまま記録されている。

```text
A small pastel pink vintage car parked in a minimalist studio.
Many pink cosmos flowers are overflowing from the car's windows and covering its roof.
Soft spring lighting, simple pale mint-green background.
Cute high-quality 3D illustration, detailed car body, gentle pastel colors.
Three-quarter side view, centered composition.
No people, no text.
```

<a id="qwen-6-実験1736736"></a>

### 6. 実験1：736×736

- PNG実寸：736×736。
- 実行RandomNoiseシード：`531123706903762`。
- 生成時間：**17.16秒（利用者の実測報告）**。
- 結果：PNG生成成功。淡いピンクの車、屋根や車内のピンクの花、淡いミントグリーンの背景を確認した。
- 目視では車は横向きに近く、three-quarter side viewの指示は実験2ほど明確ではない。

![736×736の出力](comfyui/images/Qwen_image_2.1_00001.png)

<a id="qwen-7-実験210241024"></a>

### 7. 実験2：1024×1024

- PNG実寸：1024×1024。
- 実行RandomNoiseシード：`256648331129234`。
- 生成時間：**3.13秒（利用者の実測報告）**。
- 結果：PNG生成成功。ピンクのレトロカー、窓からあふれる花、屋根の花、ミントグリーンの背景を確認した。
- 目視では車の前面と側面が見える斜めの構図で、花や車体の細部も読み取れる。

![1024×1024の出力](comfyui/images/Qwen_image_2.1_00002.png)

<a id="qwen-8-速度画質指示追従性の評価"></a>

### 8. 速度・画質・指示追従性の評価

| 項目 | 実験1 | 実験2 |
|---|---|---|
| 解像度 | 736×736 | 1024×1024 |
| 生成時間（利用者実測） | 17.16秒 | 3.13秒 |
| 英語プロンプト / 主要設定 | 共通 | 共通 |
| シード | 531123706903762 | 256648331129234 |
| PNG生成 | 成功 | 成功 |
| 車・花・背景色 | 主要な指示に概ね一致 | 主要な指示に概ね一致 |
| 最大VRAM | 未測定 | 未測定 |

PNGの実行グラフ同士の差は、ResolutionSelectorのmegapixelsとRandomNoiseのnoise_seedだった。解像度以外にもシードが異なるため、構図や細部の変化を解像度だけの影響とは扱わない。

両画像とも柔らかな陰影のある立体的な描写で、人や明瞭な文字は目視で見当たらない。色、主題、背景の簡素さは概ね指示に沿うが、指示追従性や画質を数値評価したわけではない。実験2の斜めの構図は今回の観測として記録し、一般的な性能差へは拡張しない。

実験2が速かった理由は未検証。モデルロード、キャッシュ、実行状態、測定区間を統制しておらず、**高解像度ほど速いとは結論づけない**。グラフにQwenImage21Cacheが含まれていても、キャッシュのヒット状況や寄与を測定した証拠にはならない。

17.16秒・3.13秒を今回再測定しておらず、PNG内にも生成所要時間のログはない。ファイル名、更新順、寸法、プロンプトで実験と対応づけたが、時間ログの実行IDとの照合は未実施。VRAMピーク、GPU使用率・温度、システムRAM使用量も未確認。

<a id="qwen-9-flux1-schnellflux2-kleinとの参考比較"></a>

### 9. FLUX.1 Schnell・FLUX.2 Kleinとの参考比較

以下は[既存のFLUX実験記録](image_experiments.md#flux)を参照した整理で、すべて利用者共有の生成時間。今回はFLUXを再実行していない。

| 実験 | 方式・解像度 | 主な設定 | 生成時間 | 観測・注意点 |
|---|---|---|---:|---|
| Qwen Turbo INT8・実験1 | Text to Image、736×736 | 8区間、CFG 1.0、euler | 17.16秒 | ピンクの車・花・ミント背景 |
| Qwen Turbo INT8・実験2 | Text to Image、1024×1024 | 同上 | 3.13秒 | 斜めの車・花。高速だった原因は未検証 |
| Schnell初回 | Text to Image、1024×1024 | 4 steps、CFG 1.0、euler / simple | 28.34秒 | 保存PNGと時間の厳密な対応は未検証 |
| Schnellウォーム後 | Text to Image、1024×1024 | 同上 | 約7〜8秒 | 初回とは別に扱う |
| Schnell日本語・固定シード | Text to Image、1024×1024 | 同上、seed 123456789 | 7.73秒 | 建物風の出力。車の指示に不一致 |
| Schnell英語・固定シード | Text to Image、1024×1024 | 同上、seed 123456789 | 7.53秒 | ミント色の車と花 |
| Klein base 4B・新規生成 | Text to Image、1024×1024 | 20 steps、CFG 5.0、euler / Flux2Scheduler | 約36.17秒 | ピンクの車と白い花、元画像なし |
| Klein base 4B FP8・編集 | Image Edit、1024×1024 | 20 steps、CFG 5.0、euler / Flux2Scheduler | 43.92秒 | 色変更成功、花・ホイール等にも変化 |

プロンプト、量子化、ステップ、エンコーダー、VAE、シード、タスク、ロード状態が揃っていない。表の時間を厳密な速度比較やモデルの順位に使わない。Kleinの編集は参照画像付きの別タスクで、今回のQwenの編集能力を示すものでもない。

<a id="qwen-10-制約と今後の検証予定"></a>

### 10. 制約と今後の検証予定

- 各解像度1回のみで、速度差の理由は未特定。通常の実験の中で、同じプロンプト・固定シード、ロード状態と測定区間を記録して比較する。
- 最大VRAM、GPU使用率・温度、RAM、モデルロード時間は未測定。次回は生成時間と併せて実測する。
- 日本語、refine_prompt/thinking_modeを有効にした条件、画像編集、別シードの安定性は今回未検証。
- 保存JSONの読み込み直後の再実行、実行環境・モデルハッシュの固定、画質の定量評価は未実施。
- 既存のFLUX・Wanの記録と成果物は保持する。今回、新モデルのダウンロード、追加の画像生成、ComfyUIの更新は行っていない。

<a id="qwen-11-実験a日本語による画像生成追記"></a>

### 11. 実験A：日本語による画像生成（追記）

2026-11-12の追加実験。既存の`Qwen_image_2.1_00003.png`を確認し、主要な設定と日本語プロンプトをPNGの実行グラフから取得した。

| 項目 | 結果・実行設定 |
|---|---|
| タスク | Text to Image、参照画像なし |
| 解像度 | 1024×1024（PNG実寸） |
| 生成時間 | **22.85秒（利用者の実測報告）** |
| モデル / エンコーダー / VAE | 第3節と同じ3ファイル |
| Sampler / CFG | euler / 1.0 |
| ステップ | 第4節と同じManualSigmasの8区間 |
| refine_prompt / thinking_mode | false / false |
| Batch size / 負のプロンプト | 1 / 空文字 |
| ResolutionSelector | 1:1、1.0 MP、multiple 8 |
| 実行RandomNoiseシード | 800126525653438 |

実行グラフのswitch=false側にある日本語プロンプト：

```text
ミニマルなスタジオに停車している、小さなパステルピンク色のレトロカー。
車の窓からたくさんのピンク色のコスモスがあふれ、屋根を覆っている。
柔らかな春の日差し、シンプルで淡いミントグリーンの背景。
高品質でかわいらしい3Dイラスト、精巧な車体、優しいパステルカラー。
車の前面と側面が見える斜めの構図、中央配置。
人物や文字は含めない。
```

![日本語生成：屋根の花とピンクの車](comfyui/images/Qwen_image_2.1_00003.png)

PNG生成に成功し、淡いピンクのレトロカー、ピンクの花、ミントグリーンの背景、斜めの構図を確認した。日本語の主要な指示は反映されている。一方、花は屋根に集中し、窓付近に見える花はあるものの、**窓から外へあふれる表現は不完全**だった。全指示への完全一致とは評価しない。

先の英語実験とはシード・実行状態が異なる。日本語と英語の品質・速度の一般的な差を示す統制比較ではない。Schnellの日本語入力で車にならなかった既存の1例とも、プロンプト・モデル・設定が異なるため、言語能力の順位は付けない。

<a id="qwen-12-実験b参照画像の車体をピンクへ編集追記"></a>

### 12. 実験B：参照画像の車体をピンクへ編集（追記）

出力`Qwen_image_2.1_00004.png`の実行グラフは、LoadImageで**`ComfyUI_00006_.png`**を参照していた。依頼の入力候補`ComfyUI_00006.png`とは末尾のアンダースコアが異なり、実ファイル名を優先する。

Comfy共有input内の入力PNGと、既存の[保存画像](comfyui/images/ComfyUI_00006_.png)がバイト一致した。FLUX.1 Schnellの英語固定シードで生成し、FLUX.2 Kleinの編集にも使った同じミント色の車である。入力画像は重複コピーしていない。

指示：

```text
Change only the car color from mint green to pastel pink.
Keep the flowers, composition, camera angle, and background unchanged.
```

| 項目 | 結果・実行設定 |
|---|---|
| タスク | 参照画像によるImage Edit |
| 出力解像度 | 1024×1024（PNG実寸） |
| 生成時間 | **25.00秒（利用者の実測報告）** |
| 拡散モデル | `qwen_image_2.1_turbo_int8_convrot.safetensors` |
| テキストエンコーダー | `qwen3vl_8b_int8_convrot.safetensors`、type qwen_image |
| VAE | `qwen_image_2.1_vae_bf16.safetensors` |
| Sampler / CFG / ステップ | euler / 1.0 / ManualSigmasの8区間 |
| refine_prompt / thinking_mode | false / false |
| 負のプロンプト | 空文字 |
| 実行RandomNoiseシード | 891836227295375 |

参照画像とVAEを`TextEncodeQwenImage21`へ渡し、正・負のconditioningと出力2の潜在画像を使用する。潜在画像を選ぶ`ComfySwitchNode`はfalseで、TextEncodeQwenImage21側へ接続しており、EmptyLatentImage側ではなかった。同エンコーダーの`resolution=0`は記録値として保持し、出力の寸法は実PNGで確認した。

| 入力画像 | Qwenでの編集結果 |
|---|---|
| ![入力：ミント色の車](comfyui/images/ComfyUI_00006_.png) | ![Qwen：ピンクへ編集](comfyui/images/Qwen_image_2.1_00004.png) |

車体をパステルピンクへ変更でき、花の大まかな配置、背景、車の向き・構図は概ね維持された。ホイール周辺も元画像に近い色に見える。一方、車体の光沢や花・輪郭等の見え方には差があり、**車体以外の細部が完全に不変だったとは評価しない**。画素差分、色差、領域別の保持率は測定していない。

PNGの提出グラフには22ノードがあり、switchの選択先から保存出力をたどる経路は16ノードだった。ImageCompareは保存結果を受け取る比較表示側であり、保存画像の生成経路とは区別した。プロンプト補強の枝もグラフには残るが、refine_prompt=falseで選択外となる。各ノードの実行ログやロード時間は未確認。

<a id="qwen-13-qwenとflux2-kleinの編集比較追記"></a>

### 13. QwenとFLUX.2 Kleinの編集比較（追記）

同じミント色の入力画像を、同じ趣旨の英語指示でピンクへ変える実験として整理する。Kleinの当時の詳細は[画像編集記録の第7節](image_experiments.md#flux-7-flux2-klein-4bのimage-edit)を参照。

| 比較項目 | Qwen Image 2.1 Turbo INT8 | FLUX.2 Klein base 4B FP8 |
|---|---|---|
| 車体の色変更 | パステルピンクへ変更成功 | ピンクへ変更成功 |
| 花・背景・構図 | 大まかな配置・背景・向きを概ね維持。細部の完全固定は未評価 | 主要な構図を概ね維持。花の細かな配置やホイール周辺の色も変化 |
| 見た目の観察 | 元画像に近い花の配置・ホイール周辺の色、ピンクの車体 | 花やホイール周辺にも色・形状の変化が見られる |
| 利用者の好み | **今回のQwen画像の方が好みに合う**との利用者評価 | 用途に応じて使い分ける候補として保持 |
| 生成時間（利用者実測） | 25.00秒 | 43.92秒 |
| 入力 / 出力 | 同じ`ComfyUI_00006_.png` / 1024×1024 | 同じ入力 / 1024×1024 |
| 拡散モデル | `qwen_image_2.1_turbo_int8_convrot.safetensors` | `flux-2-klein-base-4b-fp8.safetensors` |
| エンコーダー / VAE | `qwen3vl_8b_int8_convrot.safetensors` / `qwen_image_2.1_vae_bf16.safetensors` | `qwen_3_4b.safetensors` / `full_encoder_small_decoder.safetensors` |
| 設定 | 8区間、CFG 1.0、euler、ManualSigmas | 20 steps、CFG 5.0、euler、Flux2Scheduler |
| 参照経路 | TextEncodeQwenImage21へ画像・VAEを入力し、conditioningと潜在画像を使用 | ImageScaleToTotalPixels → VAEEncode → ReferenceLatent |
| 環境 | Windows 11、RTX 5070 Ti 16GB、RAM 32GB、ComfyUI Desktop | 同じPC構成。実行時のソフトウェア版の一致は未確認 |

| Kleinの編集結果 | Qwenの編集結果 |
|---|---|
| ![Kleinによる編集](comfyui/images/Flux2-Klein-4b-base_00001_.png) | ![Qwenによる編集](comfyui/images/Qwen_image_2.1_00004.png) |

モデル、量子化、エンコーダー、VAE、参照処理、ステップ、シード、ロード状態を揃えていない。各1回の生成時間と目視だけで、Qwenが客観的に高画質・高速とは断定しない。今回の採用判断は、**利用者の好みを重視した運用方針**として残す。

<a id="qwen-14-追加成果物と検査再現上の注意"></a>

### 14. 追加成果物と検査・再現上の注意

| ファイル | 由来 | サイズ |
|---|---|---:|
| [Qwen_image_2.1_00003.png](comfyui/images/Qwen_image_2.1_00003.png) | 実在する日本語生成PNGのコピー | 1,171,739 bytes |
| [Qwen_image_2.1_00004.png](comfyui/images/Qwen_image_2.1_00004.png) | 実在する編集PNGのコピー | 1,253,377 bytes |
| [日本語生成用JSON](comfyui/workflows/image_qwen_image_2_1_turbo_t2i_japanese_20261112.json) | 独立して保存されていたJSONのコピー。既存の英語版を残すため公開名を変更 | 57,216 bytes |
| [編集用JSON（PNGから抽出）](comfyui/workflows/image_qwen_image_2_1_turbo_i2i_extracted_20261112.json) | 編集PNGの`workflow`テキストをそのまま抽出。独立して保存されていたJSONではない | 66,921 bytes |

追加成果物の合計は2,549,253 bytes（約2.43 MiB）。日本語JSONの実際の元ファイル名は`image_qwen_image_2_1_turbo_t2i.json`で、前回英語版を保存した後に内容が日本語へ変わっていた。新たな公開名でコピーし、以前の英語版JSONは上書きしていない。

調査した共有outputとインストール配下のuserワークフロー保存先には、独立したQwen編集用JSONは見つからなかった。編集PNGには`prompt`と`workflow`があったため、後者の文字列を内容変更せずJSONへ抽出した。手作業でノードを補った再構築ではなく、PNGに埋め込まれていたキャンバスの保存である。未調査の別ディレクトリに保存ファイルが存在しないとまでは断定しない。

PNGの元ファイルの最終更新日時は、Aが2026-11-12 13:47:00 JST、Bが17:32:07 JST。日本語の保存JSONは13:51:13 JSTだった。ファイル名・寸法・プロンプト・参照経路で対応づけ、時刻を生成所要時間の測定には使っていない。

PNG2枚のCRC、全メタデータ、保存JSONと抽出JSON内の文字列を検査し、画像内容も目視した。秘密情報・個人情報・ユーザー固有の絶対パスは見つからなかった。PNGと日本語JSONは原本とバイト一致し、編集JSONもPNGの元テキストとバイト一致する。SHA256・設定・由来は[既存manifestへ追記](comfyui/qwen_image_2_1_artifact_manifest_20261112.json)し、以前の画像・ワークフローの項目は保持した。

再現時は、日本語生成用の保存JSONと編集用の抽出JSONをそれぞれ読み込み、第11・12節のプロンプト・モデル・実行シード・参照画像を確認する。編集キャンバスの内部RandomNoise表示は`424242`、制御は`randomize`だが、実行グラフのシードは`891836227295375`。表示値と実行値を混同しない。固定条件で試す際は制御と値を確認する。保存・抽出JSONの再実行は今回未検証。

生成時間22.85秒・25.00秒は利用者実測値を記録したもので、今回再測定していない。PNGに生成時間のログはなく、実行ID付きの時間ログとの照合もしていない。最大VRAM、GPU使用率・温度、モデルロードやキャッシュの寄与は未確認のまま。追加の生成・モデル取得・設定変更は行っていない。

<a id="qwen-15-今後のモデル運用方針利用者の判断"></a>

### 15. 今後のモデル運用方針（利用者の判断）

- **画像生成・編集の主力をQwen Image 2.1 Turboとする。** 今回の画像が利用者の好みに合うことを採用理由とし、客観的な画質・速度の優越とは区別する。FLUX.2 Kleinは削除せず、用途や好みに応じて使い分ける選択肢として残す。
- **動画生成はWan 2.2を基準**とする。既存の約3秒・約5秒の記録を保持し、LTX系などを今後の追加検証候補とする。本追記で新たな動画検証を実施したわけではない。
- Hugging Faceの派生・量子化モデル、LoRA、Strataによるローカル大規模LLMを今後の候補として記録する。具体的な構成・導入可否・性能は今回調査・検証していない。
- **現時点でClaude Proは追加しない。Codex＋OpenCode Goを維持する。** 今回は運用判断の記録のみで、契約や設定の変更は行っていない。
- 次回の通常利用では、指示の取りこぼし、修正回数、生成時間、VRAM、好みに合う結果が得られたかを記録する。モデルの追加取得や新規生成は今回の作業範囲に含めない。
