# Qwen Image 2.1 Turbo INT8による画像生成実験

- 実験・記録日：2026-11-12（JST）
- 関連記録：[FLUXによる画像生成・編集](comfyui_image_generation_evaluation_20261111.md)、[Wan 2.2動画生成](comfyui_video_generation_evaluation_20261111.md)、[README](README.md)
- 情報源：利用者共有の実験条件・生成時間と、Windows内の既存PNG・保存済みワークフローJSONの読み取り確認。
- 今回の保存作業では追加生成、モデル取得、ComfyUI更新、既存環境の設定変更を行っていない。Windows側の原本も変更していない。

第1〜10節は英語プロンプト2回を保存した時点の記録を保持している。後続の日本語生成・参照画像編集と運用方針は第11〜15節に追記した。「日本語・画像編集は未検証」等の初回記述は、その時点の履歴である。

## 1. 実験目的

RTX 5070 Ti 16GBでQwen Image 2.1 Turbo INT8 ConvRotによるText to Imageを試し、同じ英語プロンプトから736×736と1024×1024のPNGを生成する。生成成功、共有された処理時間、実画像の指示追従性を記録する。

各条件1回の実験で、ウォーム状態やシードを固定した性能比較ではない。画像の解像度・設定と、利用者が測定した生成時間の出所を区別する。

## 2. PC・ソフトウェア環境

| 項目 | 実験時の構成・確認範囲 |
|---|---|
| OS | Windows 11 |
| GPU | NVIDIA GeForce RTX 5070 Ti、VRAM 16GB |
| システムRAM | 32GB |
| ソフトウェア | ComfyUI Desktop |
| 生成方式 | Text to Image |
| 使用モデル | Qwen Image 2.1 Turbo INT8 ConvRot |

前回の画像実験記録にあるComfy Desktop 1.1.9（導入時）、ComfyUI v0.44.2、PyTorch 2.12.1+cu130は当時の観測として保持する。今回の実行時バージョンは独立に確認していない。PNG内のキャンバスには`frontendVersion: 1.57.6`、保存JSONには`1.55.14`が残るが、これらをバックエンドや実行時のDesktopバージョンに読み替えない。

## 3. 使用モデルと配布元

PNGの実行グラフと保存ワークフローで、次のファイル名を確認した。

| 役割 | 指定ファイル名 | ワークフローの配置案内 |
|---|---|---|
| 拡散モデル | `qwen_image_2.1_turbo_int8_convrot.safetensors` | `models/diffusion_models/` |
| 画像生成用テキストエンコーダー | `qwen3vl_8b_int8_convrot.safetensors` | `models/text_encoders/` |
| VAE | `qwen_image_2.1_vae_bf16.safetensors` | `models/vae/` |

保存テンプレートの配布案内は[Comfy-Org/Qwen-Image-2.1（Hugging Face）](https://huggingface.co/Comfy-Org/Qwen-Image-2.1)。2026-11-12に配布ページを確認し、ComfyUI向けに再構成したモデルであることと、上記ファイルの配置案内を確認した。テンプレートには[ModelScopeの同名配布先](https://modelscope.cn/models/Comfy-Org/Qwen-Image-2.1)もあるが、今回そのページの本文は取得できなかった。利用者が実際にどちらから取得したかは未確認。

任意のプロンプト補強用として`qwen3.5_9b_qwen_image_2.1_pe_t2i.int8_convrot.safetensors`もグラフに含まれる。ただし今回は`refine_prompt=false`で、入力文を直接使う側が選択されている。このモデル名がグラフに存在することだけで、補強モデルをロード・実行したとは判断しない。

モデル本体の内容・ハッシュやダウンロード完了ログは今回確認していない。重み、仮想環境、キャッシュはGitへ追加しない。

## 4. ワークフロー構成

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

### 保存成果物と再現メモ

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

### 公開前の確認

PNG2枚の内容を目視し、全チャンクのCRC、テキストメタデータ、埋め込みJSON、保存JSONを検査した。チャンクは`IHDR`、`pHYs`、`tEXt`、`IDAT`、`IEND`。`pHYs`はピクセル密度情報で、テキスト項目は`prompt`と`workflow`だった。秘密情報・個人情報・ユーザー固有の絶対パスは見つからなかった。

画像・JSONは原本とバイト一致し、メタデータ除去や画像加工はしていない。コピーしたJSONは保存済みの実在ファイルであり、今回PNGから新規作成したワークフローではない。解析用スクリプトや一時ファイル、他の実験の成果物は追加しない。

## 5. 使用プロンプト

2枚のPNGで次の英語プロンプトが一致した。refine_prompt=falseの選択側に、この文がそのまま記録されている。

```text
A small pastel pink vintage car parked in a minimalist studio.
Many pink cosmos flowers are overflowing from the car's windows and covering its roof.
Soft spring lighting, simple pale mint-green background.
Cute high-quality 3D illustration, detailed car body, gentle pastel colors.
Three-quarter side view, centered composition.
No people, no text.
```

## 6. 実験1：736×736

- PNG実寸：736×736。
- 実行RandomNoiseシード：`531123706903762`。
- 生成時間：**17.16秒（利用者の実測報告）**。
- 結果：PNG生成成功。淡いピンクの車、屋根や車内のピンクの花、淡いミントグリーンの背景を確認した。
- 目視では車は横向きに近く、three-quarter side viewの指示は実験2ほど明確ではない。

![736×736の出力](comfyui/images/Qwen_image_2.1_00001.png)

## 7. 実験2：1024×1024

- PNG実寸：1024×1024。
- 実行RandomNoiseシード：`256648331129234`。
- 生成時間：**3.13秒（利用者の実測報告）**。
- 結果：PNG生成成功。ピンクのレトロカー、窓からあふれる花、屋根の花、ミントグリーンの背景を確認した。
- 目視では車の前面と側面が見える斜めの構図で、花や車体の細部も読み取れる。

![1024×1024の出力](comfyui/images/Qwen_image_2.1_00002.png)

## 8. 速度・画質・指示追従性の評価

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

## 9. FLUX.1 Schnell・FLUX.2 Kleinとの参考比較

以下は[既存のFLUX実験記録](comfyui_image_generation_evaluation_20261111.md)を参照した整理で、すべて利用者共有の生成時間。今回はFLUXを再実行していない。

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

## 10. 制約と今後の検証予定

- 各解像度1回のみで、速度差の理由は未特定。通常の実験の中で、同じプロンプト・固定シード、ロード状態と測定区間を記録して比較する。
- 最大VRAM、GPU使用率・温度、RAM、モデルロード時間は未測定。次回は生成時間と併せて実測する。
- 日本語、refine_prompt/thinking_modeを有効にした条件、画像編集、別シードの安定性は今回未検証。
- 保存JSONの読み込み直後の再実行、実行環境・モデルハッシュの固定、画質の定量評価は未実施。
- 既存のFLUX・Wanの記録と成果物は保持する。今回、新モデルのダウンロード、追加の画像生成、ComfyUIの更新は行っていない。

## 11. 実験A：日本語による画像生成（追記）

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

## 12. 実験B：参照画像の車体をピンクへ編集（追記）

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

## 13. QwenとFLUX.2 Kleinの編集比較（追記）

同じミント色の入力画像を、同じ趣旨の英語指示でピンクへ変える実験として整理する。Kleinの当時の詳細は[画像編集記録の第7節](comfyui_image_generation_evaluation_20261111.md#7-flux2-klein-4bのimage-edit)を参照。

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

## 14. 追加成果物と検査・再現上の注意

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

## 15. 今後のモデル運用方針（利用者の判断）

- **画像生成・編集の主力をQwen Image 2.1 Turboとする。** 今回の画像が利用者の好みに合うことを採用理由とし、客観的な画質・速度の優越とは区別する。FLUX.2 Kleinは削除せず、用途や好みに応じて使い分ける選択肢として残す。
- **動画生成はWan 2.2を基準**とする。既存の約3秒・約5秒の記録を保持し、LTX系などを今後の追加検証候補とする。本追記で新たな動画検証を実施したわけではない。
- Hugging Faceの派生・量子化モデル、LoRA、Strataによるローカル大規模LLMを今後の候補として記録する。具体的な構成・導入可否・性能は今回調査・検証していない。
- **現時点でClaude Proは追加しない。Codex＋OpenCode Goを維持する。** 今回は運用判断の記録のみで、契約や設定の変更は行っていない。
- 次回の通常利用では、指示の取りこぼし、修正回数、生成時間、VRAM、好みに合う結果が得られたかを記録する。モデルの追加取得や新規生成は今回の作業範囲に含めない。
