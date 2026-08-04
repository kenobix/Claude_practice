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

### 結果メモ
- タイタニック生存予測: Accuracy 0.805 / F1 0.736 / ROC-AUC 0.867（5-fold CV平均Accuracy 0.788）
- 生存に効く要因（係数の符号）: `sex_female`(+1.25) `pclass_1`(+0.92) が生存に有利、
  `sex_male`(-1.23) `pclass_3`(-0.86) が不利 — 「女性・子供優先」「上位クラス優先」の史実と整合
- diabetesデータでのAICは特徴量数を増やすほど改善（3065→2832）だが、正則化(Ridge/Lasso)の
  R^2改善は小さく、Lassoはalpha=10で6特徴量まで係数が0になり疎な解を確認できた
- 図: [stage1/regularization_paths.png](stage1/regularization_paths.png)（Ridge/Lassoの係数パス）、
  [stage1/logistic_evaluation.png](stage1/logistic_evaluation.png)、
  [stage1/titanic_evaluation.png](stage1/titanic_evaluation.png)（いずれも混同行列＋ROC曲線）
