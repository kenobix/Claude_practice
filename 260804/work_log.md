# 作業ログ（260804: 機械学習・ディープラーニング学習ロードマップ）

## 目的
[roadmap.md](roadmap.md) に沿って、WSL環境でML/DL手法を実際にコードを動かしながら学ぶ。
Stage 0〜4（環境構築〜NN基礎）を土台として重視し、以降は興味の強い分野を優先して進める。

## Stage 0: WSL環境構築

- `python3.12-venv` / `python3-pip` が未インストールだったため `sudo apt install` で導入
- `260804/.venv` に仮想環境を作成し、以下をインストール
  - numpy, pandas, matplotlib, scikit-learn, jupyter, torch, torchvision, torchtext
- 動作確認: numpy/pandas/matplotlib/scikit-learn/torch/torchvision/jupyterはimport成功
- **既知の問題**: `torchtext`（最新0.18.0）は2023年にMeta社が開発終了(deprecated)しており、
  インストールされたtorch 2.13とABI非互換のため `import torchtext` がエラーになる
  → 対応不要と判断（無視して進める）。Stage 7のword2vecは`gensim`で代替する方針
  ([requirements.txt](requirements.txt)にコメントを記載)
- GPU(CUDA)は未使用（`torch.cuda.is_available()` = False）。Stage 6以降で必要になれば対応

### 環境
- OS: WSL2 (Ubuntu, Linux 6.6.87.2-microsoft-standard-WSL2)
- Python: 3.12.3
- 主要パッケージバージョン: [requirements.txt](requirements.txt) 参照
  （numpy 2.5.1, pandas 3.0.5, matplotlib 3.11.1, scikit-learn 1.9.0, torch 2.13.0, torchvision 0.28.0）

### 使い方
```bash
cd /home/kenshin/work/claude_practice/260804
source .venv/bin/activate
```

---

## Stage 1: 教師あり学習の基礎（回帰・分類）+ 評価の基本

[stage1/](stage1/) 配下にPythonスクリプトとして実装。追加のインストールは不要だった
（Stage 0で入れたnumpy/pandas/matplotlib/scikit-learnのみで完結）。

| ファイル | 内容 |
|---|---|
| [01_scratch_linear_regression.py](stage1/01_scratch_linear_regression.py) | numpyのみで最小二乗法（正規方程式）と勾配降下法による単回帰・重回帰を実装し、両者の解が一致することを確認 |
| [02_regression_regularization.py](stage1/02_regression_regularization.py) | scikit-learn（load_diabetes）で重回帰、AICの手計算、Ridge(L2)/Lasso(L1)の正則化比較、k分割交差検証、GridSearchCV |
| [03_logistic_regression_evaluation.py](stage1/03_logistic_regression_evaluation.py) | scikit-learn（load_breast_cancer）でロジスティック回帰、Accuracy/Precision/Recall/F値/混同行列/ROC-AUC、StratifiedKFold、GridSearchCV |
| [04_titanic_project.py](stage1/04_titanic_project.py) | **ミニプロジェクト**: OpenML経由でタイタニックデータセットを取得し、前処理(Pipeline/ColumnTransformer)＋ロジスティック回帰＋評価指標一式＋交差検証 |
| [_mpl_ja.py](stage1/_mpl_ja.py) | matplotlibで日本語ラベルが文字化けしないための共通フォント設定（Noto Sans CJK JP） |

### 評価指標・パラメータの用語整理

以降の図解説で使う用語をまとめておく。

| 用語 | 意味 |
|---|---|
| Accuracy（正解率） | 全予測のうち正解した割合。`(TP+TN)/全体`。クラスの人数が偏っている（不均衡）データでは高くても意味が薄いことがある |
| Precision（適合率） | 「陽性」と予測したものの中で実際に陽性だった割合。`TP/(TP+FP)`。偽陽性（誤報）を避けたい時に重視 |
| Recall（再現率） | 実際の陽性のうち正しく陽性と予測できた割合。`TP/(TP+FN)`。見逃し（偽陰性）を避けたい時に重視 |
| F1値 | PrecisionとRecallの調和平均。両者のバランスを1指標で見たい時に使う |
| 混同行列 | 「正解ラベル×予測ラベル」の集計表。どの種類の間違いが多いかが一目でわかる |
| ROC曲線 / AUC | 分類のしきい値を0〜1まで動かした時のFPR（偽陽性率）とTPR（真陽性率=Recall）の軌跡と、その曲線の下側面積。しきい値に依存しない「分離性能」そのものを評価できる。AUC=1.0が完璧、0.5がランダム |
| AIC（赤池情報量規準） | `n・ln(RSS/n) + 2k`。当てはまりの良さ（RSS＝残差平方和が小さい）と、モデルの複雑さ（パラメータ数k）のトレードオフを1つの数値にした指標。**小さいほど良いモデル** |
| alpha（Ridge/Lassoの正則化強度） | 「係数を大きくすることへの罰則」の強さ。0に近いほど通常の重回帰に近づき、大きいほど係数が0に近づく（過学習を抑える一方、大きすぎると未学習になる） |
| C（ロジスティック回帰の正則化強度） | scikit-learnの`LogisticRegression`ではalphaの逆数的な意味を持つパラメータ。**小さいほど正則化が強く**（係数が縮む）、大きいほど正則化が弱い（訓練データに忠実にフィットしようとする） |

### 図1: regularization_paths.png — Ridge/Lassoの正則化パス（[02_regression_regularization.py](stage1/02_regression_regularization.py)）

![Ridge/Lassoの正則化パス](stage1/regularization_paths.png)

**何を示す図か**: `load_diabetes`（糖尿病患者442人の10個の検査値・属性から1年後の病状進行度を予測する回帰データ）を使い、Ridge(L2正則化)とLasso(L1正則化)それぞれについて、正則化強度alphaを0.001〜100まで対数スケールで動かした時、10個の特徴量（age, sex, bmi, bp=血圧, s1〜s6=血液検査値）の回帰係数がどう変化するかをプロットしたもの。横軸alpha、縦軸が各特徴量の係数。

**読み取れる結果**:
- alphaが小さい（0.001〜0.1）うちは通常の重回帰（正則化なし）とほぼ同じ係数で、ほぼ水平線
- alphaを上げていくと、Ridge（左図）は全ての係数がなだらかに0へ近づくが、**完全に0にはならない**
- Lasso（右図）はalpha=10で10個中6個の係数がちょうど0になり（疎な解）、alpha=100では全係数が0（＝何も予測しないモデル）になる
- 特にs1（血清中のコレステロール関連の検査値）はalpha=0付近で最も大きく負の係数(-44)を持つが、正則化を強めるとRidge/Lassoともに急激に0へ向かう。これは他の特徴量（s2等）と相関が強く、正則化によって「重複した情報」が整理されるため

**どちらが良いか**: 目的次第。今回のテストデータでは alpha=1 で Lasso の方が R^2=0.4669（Ridgeは0.4541）とやや優勢だった。「使う特徴量を絞りたい／解釈しやすくしたい」ならLasso、「特徴量を全部残しつつ滑らかに抑えたい／相関の強い特徴量が複数ある」ならRidgeが向く。ただしLassoはalphaを上げすぎると急激に性能が落ちる（alpha=100でR^2=-0.012、これは「常に平均値を予測する」より悪い）ため、GridSearchCVのようにalphaを探索する工程が実務上重要になる。

### 図2: logistic_evaluation.png — 乳がん診断データの評価（[03_logistic_regression_evaluation.py](stage1/03_logistic_regression_evaluation.py)）

![乳がん診断データの混同行列とROC曲線](stage1/logistic_evaluation.png)

**何を示す図か**: `load_breast_cancer`（乳がんの腫瘍の細胞核の特徴量30種類から、悪性(malignant)か良性(benign)かを判定する二値分類データ、569件）にロジスティック回帰を適用した結果。左が混同行列、右がROC曲線。

**左（混同行列）の読み方**: 縦軸が正解ラベル、横軸が予測ラベル。テストデータ114件中、悪性42件・良性72件。対角成分（41, 71）が正解で、悪性を良性と誤判定したのが1件、良性を悪性と誤判定したのも1件のみ。Accuracy 0.983、F1値 0.986と非常に高精度。

**右（ROC曲線）の読み方**: 判定しきい値（「確率がいくつ以上なら良性と判定するか」）を0〜1まで連続的に動かした時のFPR（偽陽性率＝実際は悪性なのに良性と誤判定してしまう割合）とTPR（真陽性率＝Recallと同じ）の軌跡。曲線が左上の角に近いほど良いモデル。点線は「ランダムに判定した場合」の基準線（AUC=0.5）。今回はAUC=0.995で、ほぼ完璧に悪性/良性を分離できている（＝ランダムに悪性・良性のペアを1組選んだ時、モデルが正しく順位付けできる確率が99.5%）。

医療診断のような場面では「悪性を良性と見逃す」（偽陰性＝Recallの低下）の方が「良性を悪性と誤診する」（偽陽性）よりリスクが大きいため、Recallを重視してしきい値を調整する、といった応用もできる。

### 図3: titanic_evaluation.png — タイタニック生存予測の評価（[04_titanic_project.py](stage1/04_titanic_project.py)）

![タイタニック生存予測の混同行列とROC曲線](stage1/titanic_evaluation.png)

**何を示す図か**: タイタニック号の乗客データから生存/死亡を予測した結果。構成はlogistic_evaluation.pngと同じ（左＝混同行列、右＝ROC曲線）。

**結果**: Accuracy 0.805 / Precision 0.763 / Recall 0.710 / F1値 0.736 / ROC-AUC 0.867（5-fold交差検証の平均Accuracyは0.788で、単発のテストスコア0.805と近く、たまたま良い分割に当たったわけではないことを確認できる）。

**混同行列から読み取れること**: テストデータ262人中、死亡140/22（正解/誤り）、生存29/71（誤り/正解）。「死亡」の予測はよく当たる（Recall高め）一方、生存者29人を死亡と誤判定しており、生存者側の見逃しがやや多い。これは元データの生存率が38.2%とやや不均衡（死亡の方が多い）なことが一因。

**係数から読み取れる要因**（`sex_female`＋1.25, `pclass_1`＋0.92 が生存に有利、`sex_male`－1.23, `pclass_3`－0.86 が不利）: 「女性・子供優先」「上位クラス（1等客室）ほど救命ボートに近く優先された」という史実と整合しており、モデルが妥当な傾向を学習できていることが確認できた。breast_cancerのAUC(0.995)と比べると本データのAUC(0.867)はやや低いが、実データ・人間行動が絡む予測としては十分高い分離性能といえる。

### なぜ「タイタニック生存予測」をミニプロジェクトに選んだか

**タイタニック生存予測とは**: 1912年に沈没した客船タイタニック号の乗客名簿（乗船クラス・性別・年齢・運賃・同乗家族数・乗船港など）をもとに、その人が生存したか死亡したかを予測する二値分類タスク。実在の乗客1309人分の記録がデータセット化されており、機械学習の入門教材として非常によく使われる（Kaggleの初心者向けコンペ"Titanic: Machine Learning from Disaster"が特に有名）。

**選定理由**:
- **前処理の練習に適している**: 年齢(age)に263件、運賃(fare)に1件、乗船港(embarked)に2件の欠損値があり、`SimpleImputer`による補完を実践できる
- **数値変数とカテゴリ変数が混在**: 数値（年齢・運賃・家族人数）とカテゴリ（性別・客室クラス・乗船港）が両方あり、`ColumnTransformer`＋`Pipeline`でまとめて前処理する典型的な構成を学べる
- **結果の妥当性を史実で検証できる**: 「女性・子供優先」「上位クラス優先」という歴史的事実と、学習されたモデルの係数の符号が一致するかを確認でき、単なる精度の数字だけでなく「モデルが正しい理由で予測できているか」を考える練習になる
- **軽量**: 1309件と小規模でノートPC上でも数秒で学習が終わり、Stage 1の主眼である「評価指標の使い方の習得」に集中できる
- roadmap.mdのStage 1の想定ミニプロジェクトとして最初から例示されていた

**候補から外した他の選択肢**:
- **Iris（アヤメの品種分類）**: 機械学習チュートリアルの定番すぎる上に3クラス分類のため、今回重視したPrecision/Recall/ROC曲線などの「二値分類の評価指標」の説明には不向き
- **MNIST（手書き数字）**: 画像データのため欠損値処理やカテゴリ変数エンコーディングの練習にならない。画像系はStage 4以降（NN基礎）で本格的に扱う予定のため、そちらに譲った
- **California Housing / Diabetes**: いずれも回帰タスクであり、すでに[02_regression_regularization.py](stage1/02_regression_regularization.py)で回帰を扱ったため、分類側の評価指標（混同行列・ROC-AUC）を実践する題材としては別データが必要だった
- **実務データ（自作の売上・アンケート等）**: 入手性・前処理の複雑さ・個人情報配慮の観点から、教育目的では実績のある公開データセットの方が結果の妥当性を検証しやすく適切と判断

---

## Stage 2: 決定木・アンサンブル・SVM

[stage2/](stage2/) 配下にPythonスクリプトとして実装。追加のインストールは不要だった。

| ファイル | 内容 |
|---|---|
| [01_decision_tree.py](stage2/01_decision_tree.py) | load_wine（ワイン品種分類）で決定木。max_depthを変えた過学習の観察、plot_treeによる木構造の可視化、2特徴量での決定境界の可視化 |
| [02_ensemble_bagging_boosting.py](stage2/02_ensemble_bagging_boosting.py) | 単体決定木 vs Bagging vs RandomForest vs GradientBoostingの精度比較、RandomForestの特徴量重要度 |
| [03_svm_kernel.py](stage2/03_svm_kernel.py) | make_circles（同心円データ）でSVMのlinear/poly/rbfカーネルを比較し、カーネルトリックの効果を可視化。正則化パラメータCの影響も確認 |
| [04_model_comparison_project.py](stage2/04_model_comparison_project.py) | **ミニプロジェクト**: make_moonsで決定木・RandomForest・SVM(rbf)の決定境界とテスト精度・交差検証を横並び比較 |

### 図1: decision_tree.png — 決定木の過学習・構造・決定境界（[01_decision_tree.py](stage2/01_decision_tree.py)）

![決定木の過学習の観察・木構造・決定境界](stage2/decision_tree.png)

**何を示す図か**: load_wine（3品種のワインを化学成分13種類から分類する178件のデータ）を使用。左からmax_depth(木の深さの上限)と訓練/テスト精度の関係、max_depth=3の木構造そのもの、flavanoids(フラボノイド量)とcolor_intensity(色の濃さ)の2特徴量だけを使った決定境界。

**読み取れる結果**:
- 左図: max_depth=1では訓練0.661/テスト0.611と両方低い（未学習＝単純すぎるモデル）。max_depth=3で訓練0.992/テスト0.963まで急上昇し、それ以降(4〜None)は訓練は1.000に達するがテストは0.963で頭打ち。**深さ3を超えて木を複雑にしても、訓練データへの当てはめが良くなるだけでテスト性能は改善しない＝過学習**という典型的な現象が確認できた
- 中央図: 根ノードがまず`color_intensity <= 3.82`で分岐し、次に`flavanoids`や`alcalinity_of_ash`といった特徴量で細分化されていく様子が視覚的にわかる。各ノードのgini(ジニ不純度、0に近いほどそのノード内が単一クラスに揃っている)が深さとともに0に近づいていく
- 右図: 2特徴量だけに絞ると全13特徴量を使った場合(0.963)よりテスト精度が下がる(0.870、参考としてmax_depth=3では0.907)。情報量を減らして可視化しやすくしたトレードオフであることを明記した

### 図2: ensemble_comparison.png — アンサンブル手法の精度比較と特徴量重要度（[02_ensemble_bagging_boosting.py](stage2/02_ensemble_bagging_boosting.py)）

![単体決定木とアンサンブル手法の精度比較、RandomForestの特徴量重要度](stage2/ensemble_comparison.png)

**何を示す図か**: 同じload_wineデータで、max_depth=3・n_estimators=50に条件を揃えた「決定木(単体)」「Bagging」「RandomForest」「GradientBoosting」を比較。左がテスト精度と5-fold交差検証平均の棒グラフ、右がRandomForestの特徴量重要度。

**読み取れる結果**:
- 決定木単体はテスト0.963だが5-fold CV平均は0.871とばらつきが大きい。BaggingとRandomForestはテスト1.000・CV平均0.944/0.972まで改善しており、**複数の木の多数決によって「たまたま良い分割になった」バイアスが均され、汎化性能が安定する**ことが確認できた
- GradientBoostingはテスト0.944・CV平均0.939とこのデータでは他のアンサンブル手法より低かった。学習率やn_estimatorsのチューニング不足が主因と考えられ、「ブースティングは常に最強」ではなくデータやハイパーパラメータ次第であることも実感できた
- 特徴量重要度は`flavanoids`(0.193)が最大、次いで`alcohol`(0.163)、`color_intensity`(0.139)と続く。図1の決定木の根ノードで使われた`color_intensity`が単体では最重要ではなく、複数の木の平均で見ると`flavanoids`の寄与が大きいという、単体決定木だけでは見えない情報が得られた

### 図3: svm_kernel_comparison.png — SVMのカーネルトリック比較（[03_svm_kernel.py](stage2/03_svm_kernel.py)）

![SVMのlinear/poly/rbfカーネルによる決定境界の違い](stage2/svm_kernel_comparison.png)

**何を示す図か**: make_circles（内側と外側の同心円状に分布する、直線では絶対に分離できない2クラスデータ）に対して、SVMのカーネルをlinear/poly(3次)/rbfと変えて学習させた決定境界。

**読み取れる結果**:
- linearカーネルはテスト精度0.533とほぼランダム(コイントス)と同程度。直線1本でしか境界を引けないため、同心円構造には原理的に対応できないことが数値でも図でも明確に示された
- polyカーネル(3次)は0.644まで改善するが、境界がまだ歪んだ直線的な形にとどまり同心円にはフィットしきれていない
- rbfカーネルは0.989とほぼ完璧に分離。決定境界が内側の円をきれいに囲む形になっており、**カーネルトリック（データを高次元空間に写像してから直線的に分離することで、元の空間では曲線の境界を実現する）**の効果を視覚的に確認できた
- 正則化パラメータC（C=0.01/1.0/100.0）を比較すると、Cが大きいほどサポートベクター数が減り（210→54→25）境界がタイトになる。C=1.0が最良(0.989)で、C=100.0はやや過学習気味に精度が落ちた(0.978)

### 図4: model_comparison.png — 決定木・RandomForest・SVMの決定境界比較（ミニプロジェクト、[04_model_comparison_project.py](stage2/04_model_comparison_project.py)）

![make_moonsデータに対する決定木・RandomForest・SVMの決定境界比較](stage2/model_comparison.png)

**何を示す図か**: make_moons（三日月型に絡み合った2クラス、ノイズ0.3であえて重なりを持たせた300件のデータ）に対して、決定木(max_depth=5)・RandomForest(100本)・SVM(rbf, C=1.0)を学習させた決定境界とテスト精度・5-fold CV平均。

**読み取れる結果**:
- テスト精度は決定木0.856、RandomForest0.856、SVM0.878とSVMがやや優勢。5-fold CV平均でもSVM(0.897)が決定木(0.863)・RandomForest(0.870)を上回った
- 決定境界の形が手法ごとに明確に異なる: 決定木は軸に平行な直線を組み合わせた「階段状」の境界、RandomForestはその階段が複数の木の平均でやや滑らかになった境界、SVM(rbf)は三日月の曲線に沿うような滑らかな曲線境界になっており、**モデルの構造がどういう形の決定境界を得意とするか**が一目でわかる結果になった
- CV標準偏差はSVM(0.024)・RandomForest(0.019)が決定木(0.034)より小さく、複数の木を使う/カーネルで滑らかな境界を作る手法の方が分割による精度のブレが小さい傾向も確認できた
- ノイズ0.3を与えているため、どのモデルも1.0には到達しない。これは「データそのものに重なりがある(理論上の上限精度が1.0未満)」ケースであり、モデルの性能限界ではなくデータ品質による限界であることに注意
