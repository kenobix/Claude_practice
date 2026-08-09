# 機械学習・ディープラーニング 学習ロードマップ

対象: いただいた一覧（約104〜110種類）の手法・アルゴリズムを、WSL環境で
実際にコードを動かしながら学ぶための順序と方針。

## 学び方の基本方針
1. **積み上げ式**: 前の段階の道具を使って次の段階を実装する構成にしている
   （例: 誤差逆伝播法を理解して初めてCNN/RNN/Transformerが「中身のわかるブラックボックス」になる）
2. **1手法につき2段階で実装する**
   - Step A: 小さいデータ・小さい実装で「スクラッチ実装」（numpyのみ等）→ 仕組みの理解
   - Step B: scikit-learn / PyTorch等の「ライブラリ実装」→ 実践的な使い方の理解
   - 全手法をスクラッチ実装するのは非効率なので、**原理が肝の手法（回帰・NN基礎・逆伝播・Q学習等）はStep Aまで、応用寄りの手法（ResNet, BERT等）はStep Bのみ**、とメリハリをつける
3. **各Stageの最後に小さな成果物（ミニプロジェクト）を作る** — 一覧消化ではなく手を動かした実感を残すため
4. わからないこと・詰まったことがあれば都度聞いてください。ここでは大枠の順序と各Stageの狙いを示す

## WSL環境構築（Stage 0）✅完了
```bash
sudo apt update && sudo apt install -y python3-venv python3-pip
cd /home/kenshin/work/claude_practice/260804
python3 -m venv .venv
source .venv/bin/activate
pip install numpy pandas matplotlib scikit-learn jupyter torch torchvision torchtext
```
- GPU利用（WSL2 + CUDA）が必要になるのはStage 6以降（CNN画像系）。最初はCPUで十分
- 各Stageごとに `stageN/` フォルダを作り、コードとミニプロジェクトをそこに置く運用を想定
- 詳細・詰まった点は [work_log.md](work_log.md#stage-0-wsl環境構築) を参照
  （`torchtext`はABI非互換のため未使用に決定、word2vec等はStage 7で`gensim`に代替）

---

## Stage 1: 教師あり学習の基礎（回帰・分類）+ 評価の基本 ✅完了
**狙い**: 「学習する」とはどういうことかを一番シンプルな形で体感し、以降ずっと使う評価指標を先に押さえる

- 単回帰分析 / 重回帰分析 / 線形回帰
- ロジスティック回帰
- モデル評価: 正解率(Accuracy) / 適合率(Precision) / 再現率(Recall) / F値 / 混同行列 / ROC曲線とAUC
- 交差検証（k分割、ホールドアウト）/ グリッドサーチ / AIC
- 正則化: L1(ラッソ回帰) / L2(リッジ回帰)

**実装**: numpyで最小二乗法・勾配降下法による線形回帰をスクラッチ → scikit-learnで重回帰・ロジスティック回帰・評価指標を実践
**ミニプロジェクト**: 適当な公開データ（例: タイタニック生存予測）でロジスティック回帰＋評価指標一式を出す
**実施内容**: [work_log.md](work_log.md#stage-1-教師あり学習の基礎回帰分類--評価の基本) 参照。
タイタニック生存予測をミニプロジェクトとして実施（Accuracy 0.805 / ROC-AUC 0.867）

---

## Stage 2: 決定木・アンサンブル・SVM ✅完了
**狙い**: 線形モデルとは異なる決定境界の作り方、複数モデルを組み合わせる発想を学ぶ

- 決定木
- ランダムフォレスト / バギング / ブースティング
- サポートベクターマシン（SVM）/ カーネルトリック

**実装**: scikit-learnで決定木・ランダムフォレスト・SVMを比較、決定境界を可視化
**ミニプロジェクト**: 同じデータセットで複数モデルの精度・決定境界を比較するノートブック
**実施内容**: [work_log.md](work_log.md#stage-2-決定木アンサンブルsvm) 参照。
make_moonsで決定木/RandomForest/SVM(rbf)を比較（SVMがテスト精度0.878で最良）

---

## Stage 3: 教師なし学習 ✅完了
**狙い**: ラベルなしデータから構造を見つける発想（クラスタリング・次元削減）

- k-means法 / ウォード法（階層的クラスタリング）
- 主成分分析（PCA）/ 特異値分解（SVD）/ t-SNE / 多次元尺度構成法（MDS）
- トピックモデル
- 協調フィルタリング / コンテンツベースフィルタリング

**実装**: scikit-learnでk-means・PCA・t-SNEを実践し高次元データを2次元可視化
**ミニプロジェクト**: 手書き数字データ(MNIST)をPCA/t-SNEで2次元に落として可視化＋k-meansでクラスタリング
**実施内容**: [work_log.md](work_log.md#stage-3-教師なし学習) 参照。
インターネット接続がない環境のためトピックモデル/協調フィルタリングは自作データで代替。
PCAとSVDの数学的な同一性を数値検証、MNISTクラスタリングは一致率0.682を達成

---

## Stage 4: ニューラルネットワークの基礎（最重要の土台） ✅完了
**狙い**: ここが一番大事。NNの中身（順伝播・逆伝播・最適化）を自分の手で実装して理解する

- 活性化関数: ReLU / Leaky ReLU / シグモイド / ソフトマックス / tanh / 恒等関数
- 最適化アルゴリズム: 勾配降下法 / 最急降下法 / ニュートン法 / SGD / Adam
- 誤差逆伝播法（バックプロパゲーション）
- 正規化: バッチ正規化 / レイヤー正規化 / インスタンス正規化 / グループ正規化
- 正則化・学習制御: ドロップアウト / 早期終了（Early Stopping）
- オートエンコーダ

**実装**: numpyのみで多層パーセプトロン(MLP)を実装し、逆伝播を手書きで導出→MNIST分類
その後PyTorchで同じものを書き直し、フレームワークが何を肩代わりしているかを比較する
**ミニプロジェクト**: 「numpyスクラッチMLP」と「PyTorch MLP」で同じMNIST分類の精度を比較
**実施内容**: [work_log.md](work_log.md#stage-4-ニューラルネットワークの基礎最重要の土台) 参照。
同一初期値・同一条件で学習させたnumpyスクラッチMLPとPyTorch MLPのテスト精度が0.9611で完全一致し、
手書きの逆伝播とautogradが数学的に同一であることを実証

---

## Stage 5: CNN基礎とアーキテクチャの歴史 ✅完了
**狙い**: 画像認識の発展史を追いながら、各アーキテクチャが何を解決したかを理解する

- CNN基礎 / 最大値プーリング・平均値プーリング / グローバルアベレージプーリング（GAP）
- ネオコグニトロン → LeNet → AlexNet → VGGNet → GoogLeNet(Inceptionモジュール) → ResNet(スキップ結合/残差接続)
- MobileNet(深度別分離畳み込み) / EfficientNet / U-Net
- 拡張畳み込み（Dilated Convolution）
- データ拡張（Data Augmentation全般 / Mixup）

**実装**: PyTorchでLeNet→簡易ResNetの順に自作し、CIFAR-10で精度比較。以降の大型モデルは`torchvision.models`の事前学習済みモデルを読み込んで使う
**ミニプロジェクト**: 自作の小さいCNN vs 事前学習済みResNetのファインチューニングで精度差を体感
**実施内容**: [work_log.md](work_log.md#stage-5-cnn基礎とアーキテクチャの歴史) 参照。
CIFAR-10はダウンロード速度(90kB/s)が非現実的だったため、Pillowで生成する合成図形データセットに変更。
21層のPlain CNN vs ResNetでdegradation problemを再現し、事前学習済みResNet18のファインチューニングは
自作CNNを同一データ量で約9ポイント上回った

---

## Stage 6: 物体検出・セグメンテーション ✅完了
**狙い**: 「分類」の次のタスクである「どこに何があるか」を扱う

- YOLO / SSD / Fast R-CNN / Faster R-CNN / Mask R-CNN

**実装**: ゼロから実装せず、事前学習済みモデル（`torchvision.models.detection`やUltralytics YOLO）を使って推論・簡単なファインチューニングを体験する
**ミニプロジェクト**: 手元の画像でYOLOを動かして物体検出を試す
**実施内容**: [work_log.md](work_log.md#stage-6-物体検出セグメンテーション) 参照。
IoU/NMSの基礎実装、Faster R-CNN(2段階)とSSD/YOLO(1段階)の速度・精度トレードオフを実測
(推論時間50倍差、小物体検出は2段階が有利)、合成データでの検出ファインチューニング(IoU 0→0.916)、
Mask R-CNN推論と自作U-Net(Dice 1.000)まで一通り実装

---

## Stage 7: 系列データ・NLP基礎 ✅完了
**狙い**: 画像とは異なる「時系列・言語」データの扱い方

- RNN / LSTM / GRU
- word2vec（CBOW / skip-gram）/ fastText
- データ拡張: Paraphrasing（NLP系）

**実装**: numpyでRNNの順伝播をスクラッチ→PyTorchでLSTMによる文章分類
gensimやPyTorchでword2vecを学習し、単語の類似度を確認
**ミニプロジェクト**: LSTMで簡単な感情分析（映画レビュー等）
**実施内容**: [work_log.md](work_log.md#stage-7-系列データnlp基礎) 参照。
numpyスクラッチRNNでパリティ判定タスクの勾配消失を実測(系列長8を境に精度が崖状に崩壊)、
LSTMの忘却ゲート初期化が長期記憶タスクの学習可能性を大きく左右することを確認、
gensim word2vecでは類義語だけでなく対義語も近づいてしまう限界を観察、
LSTM感情分析ミニプロジェクトではTF-IDF+ロジスティック回帰(0.823)がLSTM(最良0.613)を上回った

---

## Stage 8: Attention / Transformer時代 ✅完了
**狙い**: 現代の生成AIの土台となる仕組みを理解する。ここが後半の本命

- Attention / Self-Attention / Multi-Head Attention
- Transformer
- BERT / GPT
- 転移学習 / ファインチューニング

**実装**: numpyでScaled Dot-Product Attentionをスクラッチ実装し仕組みを理解
→Hugging Face `transformers`で事前学習済みBERT/GPT系モデルをファインチューニング
**ミニプロジェクト**: BERTで文章分類のファインチューニング
**実施内容**: [work_log.md](work_log.md#stage-8-attention--transformer時代) 参照。
スクラッチAttentionでは学習前は「注意の吸着」しか起きず意味的な構造は学習で初めて獲得されると判明、
Transformer Encoderは自己注意によりStage7のRNN系が崩壊した系列長でも精度1.0を維持(ただし計算量はO(L²)で増加)、
事前学習済みBERTでは照応解析に近い挙動を示すheadを層8・head10に実際に発見、
BERTファインチューニング(Accuracy 0.698)はStage7のLSTM(0.613)を上回ったがTF-IDF(0.823)には届かなかった

---

## Stage 9: 生成モデル ✅完了
**狙い**: 「データを生成する」モデルの発想（潜在空間・敵対的学習・拡散過程）

- オートエンコーダ（復習）/ VAE / β-VAE / VQ-VAE
- GAN / DCGAN
- Diffusion Model

**実装**: PyTorchでVAE→DCGANの順にMNIST/Fashion-MNISTで実装。Diffusion Modelは簡易版（1次元 or 小さい画像）をスクラッチしてから事前学習済みモデル（Stable Diffusion系）を試す
**ミニプロジェクト**: 自作VAEとGANでの画像生成結果を比較
**実施内容**: [work_log.md](work_log.md#stage-9-生成モデル) 参照。
事前学習済みStable Diffusion系はCPU実行が非現実的だったため断念しスクラッチ実装に集中。
VAEは潜在空間のなめらかさを可視化、DCGANはVAEの約20倍の学習時間で輪郭のくっきりした生成画像を獲得、
スクラッチDDPMは前向き/逆向き拡散過程は再現できたが小規模構成のため生成品質はぼやけた塊にとどまった、
ミニプロジェクトではGANが多様性・識別性の両指標でVAEを上回り単純な評価指標の限界も確認した

---

## Stage 10: LLM関連・学習戦略 ✅完了
**狙い**: 大規模モデル時代特有の技術と学習パラダイムを整理する

- LoRA / RAG / スケーリング則 / Chain-of-Thought
- 学習戦略: 自己教師あり学習 / ゼロショット / ワンショット / Few-shot / 半教師あり学習 / マルチタスク学習

**実装**: 手元のLLM（ローカル軽量モデル or API）に対してLoRAでファインチューニング、簡易RAGパイプラインを構築
（[260525/phase1/rag_phase1.py](../260525/phase1/rag_phase1.py) の既存RAG実装が土台として使えます）
**ミニプロジェクト**: 小規模データでLoRAファインチューニング＋RAGの効果を比較
**実施内容**: [work_log.md](work_log.md#stage-10-llm関連学習戦略) 参照。
スクラッチLoRAで低ランク差分によるパラメータ削減を実測、GPT-2への実際のLoRA適用では
架空知識を部分的に暗記(0/6→2/6)、Gemini APIのRAGでは検索top_kからの僅かな漏れが
確信的な誤答を招く実例を発見、同一GPT-2でのLoRA(2/6) vs RAG(6/6)比較でRAGの強さと
検索精度依存というトレードオフを確認した

---

## Stage 11: モデル軽量化 ✅完了
**狙い**: 学習済みモデルを実運用サイズに落とし込む技術

- プルーニング / 量子化 / 知識蒸留

**実装**: PyTorchの量子化API・簡易プルーニングをStage 5で作ったCNNに適用し、精度とモデルサイズ・推論速度の変化を測る
**ミニプロジェクト**: 量子化前後でのモデルサイズ・推論速度比較
**実施内容**: [work_log.md](work_log.md#stage-11-モデル軽量化) 参照。
非構造化プルーニングは50%〜70%の間に精度崩壊の崖があることを実測、量子化はサイズ3.71倍・
速度4.66倍かつ精度劣化なしを達成、知識蒸留は教師タスクが簡単すぎると効果が出にくいことを
逆説的に確認、ミニプロジェクトではモデル設計縮小(14.4倍)×量子化(2.2倍)で合計31.9倍の
圧縮を達成しつつ精度とのトレードオフを実測した

---

## Stage 12: 強化学習
**狙い**: 「正解データ」がない、試行錯誤から学ぶ枠組み

- マルコフ決定過程（MDP）
- Q学習 / SARSA / ε-greedy方策 / UCB方策 / バンディットアルゴリズム
- DQN（Deep Q-Network）
- 方策勾配法 / REINFORCE / Actor-Critic
- RLHF（人間のフィードバックによる強化学習）

**実装**: OpenAI Gym(Gymnasium)の`FrozenLake`/`CartPole`でQ学習→DQN→方策勾配法の順にスクラッチ〜PyTorch実装
**ミニプロジェクト**: CartPoleをDQNで攻略し学習曲線をプロット

---

## Stage 13: 探索アルゴリズム（古典AI、余力があれば）
**狙い**: 機械学習ではないが強化学習・ゲームAIの前提知識として関連が深い

- 幅優先探索（BFS）/ 深さ優先探索（DFS）
- Mini-Max法 / αβ法
- モンテカルロ法 / モンテカルロ木探索（MCTS）

**実装**: 三目並べ(〇×ゲーム)でMini-Max→αβ法→MCTSの順に実装し、探索の効率化を体感
**ミニプロジェクト**: 三目並べAI(不敗)を作る

---

## 全体の目安
- Stage 0〜4（環境構築〜NN基礎）が土台として最重要。ここは急がず理解を優先
- Stage 5以降は興味の強い分野（画像系/NLP系/生成系/強化学習）を先に進めても良い
  （Stage間の依存は緩いが、Stage 4のNN基礎は全ての前提）
- 各Stageの「ミニプロジェクト」を作った時点でREADME/work_logに記録していく運用を想定
  （[260702](../260702/)と同じ形式）

## 次のアクション
Stage 0〜11が完了しました。次はStage 12（強化学習: Q学習/DQN/方策勾配法）に進みますか？
