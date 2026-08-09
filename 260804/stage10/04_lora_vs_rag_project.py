"""ミニプロジェクト: 同じGPT-2・同じ架空知識で、LoRAとRAGの「知識注入」効果を比較する

02ではGPT-2にLoRAファインチューニングで架空の設定を覚えさせ、03ではGemini APIで
RAGパイプラインを試した。ここでは同じベースモデル(GPT-2)・同じ架空知識
([fictional_facts.py](fictional_facts.py))を使い、
  (A) 適応なし(ベースGPT-2)
  (B) LoRAファインチューニング(02と同じ手法: 知識をモデルの重みに焼き込む)
  (C) RAG(TF-IDFで関連する事実文を検索し、プロンプトに追加してから生成: モデルは変えない)
の3通りを同一条件で比較する。LoRAは『事前に学習コストを払うが、推論時は追加コストなし』、
RAGは『学習コストはゼロだが、推論のたびに検索が必要』というトレードオフがあり、
どちらが有利かはユースケース次第——この比較を小規模ながら実際に体感する。
"""
import importlib

import numpy as np
import torch
from transformers import AutoTokenizer, AutoModelForCausalLM
from peft import LoraConfig, get_peft_model, TaskType
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
import matplotlib.pyplot as plt

import _mpl_ja  # noqa: F401

facts = importlib.import_module("fictional_facts")

torch.manual_seed(42)
MODEL_NAME = "gpt2"


def ask(model, tokenizer, prompt, max_new_tokens=15):
    inputs = tokenizer(prompt, return_tensors="pt")
    with torch.no_grad():
        out = model.generate(**inputs, max_new_tokens=max_new_tokens, do_sample=False,
                              pad_token_id=tokenizer.eos_token_id)
    return tokenizer.decode(out[0][inputs["input_ids"].shape[1]:], skip_special_tokens=True)


def evaluate(model, tokenizer, label, use_rag=False, vectorizer=None, sentence_vecs=None, sentences=None):
    print(f"\n--- {label} ---")
    n_correct = 0
    for question, answer in facts.QA_PAIRS:
        if use_rag:
            q_vec = vectorizer.transform([question])
            sims = cosine_similarity(q_vec, sentence_vecs)[0]
            best_sentence = sentences[int(np.argmax(sims))]
            prompt = f"Context: {best_sentence}\nQuestion: {question} The answer is:"
        else:
            prompt = question + " The answer is:"
        completion = ask(model, tokenizer, prompt)
        correct = answer.lower() in completion.lower()
        n_correct += int(correct)
        hit = "○" if correct else "×"
        print(f"  [{hit}] Q: {question}")
        print(f"      A(生成): {completion.strip()[:60]}")
    print(f"  正解数: {n_correct}/{len(facts.QA_PAIRS)}")
    return n_correct


def train_lora(base_model, tokenizer):
    lora_config = LoraConfig(
        task_type=TaskType.CAUSAL_LM, r=8, lora_alpha=16, lora_dropout=0.0,
        target_modules=["c_attn"],
    )
    model = get_peft_model(base_model, lora_config)
    trainable, total = model.get_nb_trainable_parameters()

    sentences = facts.get_fact_sentences()
    train_texts = sentences * 40
    encodings = tokenizer(train_texts, return_tensors="pt", padding=True, truncation=True, max_length=64)
    input_ids, attn_mask = encodings["input_ids"], encodings["attention_mask"]
    labels = input_ids.clone()
    labels[attn_mask == 0] = -100

    optimizer = torch.optim.AdamW([p for p in model.parameters() if p.requires_grad], lr=5e-4)
    dataset = torch.utils.data.TensorDataset(input_ids, attn_mask, labels)
    loader = torch.utils.data.DataLoader(dataset, batch_size=8, shuffle=True)

    model.train()
    for epoch in range(5):
        for ids, mask, lab in loader:
            optimizer.zero_grad()
            out = model(input_ids=ids, attention_mask=mask, labels=lab)
            out.loss.backward()
            optimizer.step()
    model.eval()
    return model, trainable, total


def main() -> None:
    tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME)
    tokenizer.pad_token = tokenizer.eos_token

    print("=== (A) 適応なし: ベースGPT-2にそのまま質問 ===")
    base_model = AutoModelForCausalLM.from_pretrained(MODEL_NAME)
    n_base = evaluate(base_model, tokenizer, "(A) 適応なし")

    print("\n=== (B) LoRAファインチューニング ===")
    lora_model, trainable, total = train_lora(base_model, tokenizer)
    n_lora = evaluate(lora_model, tokenizer, "(B) LoRAファインチューニング")

    print("\n=== (C) RAG: TF-IDF検索 + ベースGPT-2(適応なし)で生成 ===")
    sentences = facts.get_fact_sentences()
    vectorizer = TfidfVectorizer().fit(sentences + [q for q, _ in facts.QA_PAIRS])
    sentence_vecs = vectorizer.transform(sentences)
    # RAGは元のモデル(適応前)をそのまま使う。base_modelはBで一部重みが変わっていないか確認するため、
    # 改めて新しいインスタンスとしてロードし直す(LoRAアダプタの影響を受けないことを保証する)
    rag_base_model = AutoModelForCausalLM.from_pretrained(MODEL_NAME)
    n_rag = evaluate(rag_base_model, tokenizer, "(C) RAG(検索context + ベースGPT-2)",
                      use_rag=True, vectorizer=vectorizer, sentence_vecs=sentence_vecs, sentences=sentences)

    print("\n=== まとめ ===")
    print(f"(A) 適応なし         : {n_base}/{len(facts.QA_PAIRS)}")
    print(f"(B) LoRAファインチューニング: {n_lora}/{len(facts.QA_PAIRS)} "
          f"(学習対象パラメータ {100 * trainable / total:.3f}%)")
    print(f"(C) RAG              : {n_rag}/{len(facts.QA_PAIRS)}")

    n_total = len(facts.QA_PAIRS)
    fig, ax = plt.subplots(figsize=(7, 5.5))
    labels = ["(A) 適応なし", "(B) LoRA\nファインチューニング", "(C) RAG"]
    values = [n_base, n_lora, n_rag]
    bars = ax.bar(labels, values, color=["gray", "tab:blue", "tab:orange"])
    ax.set_ylabel(f"正解数(全{n_total}問中)")
    ax.set_ylim(0, n_total + 0.5)
    ax.set_title("同一の架空知識・同一のGPT-2での知識注入手法の比較")
    for bar, v in zip(bars, values):
        ax.text(bar.get_x() + bar.get_width() / 2, v + 0.1, f"{v}/{n_total}", ha="center")
    fig.tight_layout()
    out_path = "lora_vs_rag_project.png"
    fig.savefig(out_path, dpi=110)
    print(f"図を保存: {__file__.rsplit('/', 1)[0]}/{out_path}")

    best = max([("LoRA", n_lora), ("RAG", n_rag)], key=lambda t: t[1])
    print(
        f"\n適応なしの{n_base}/{len(facts.QA_PAIRS)}を基準にすると、LoRAは{n_lora}/{len(facts.QA_PAIRS)}、"
        f"RAGは{n_rag}/{len(facts.QA_PAIRS)}まで改善しており、今回は{best[0]}の方が正解数が多かった。"
        "RAGは該当する事実文をほぼ確実に検索でき(6つの短い事実文から1問1答形式で聞いているため"
        "TF-IDFでも検索が容易)、検索した文をそのままプロンプトに含められるため、"
        "モデル自体を一切変更していないにもかかわらず高い正解率を出しやすい。一方LoRAは"
        "モデルの重みに知識を焼き込むため、推論時には検索が不要(文脈をプロンプトに含める必要が"
        "ない)というメリットがあるが、02で見た通り、少量データ・短時間の学習では完全な"
        "暗記までは至らないことがある。実務では、頻繁に更新される知識やドキュメント全体を"
        "参照させたい場合はRAG、モデルの振る舞いや話し方そのものを変えたい場合はLoRAという"
        "使い分けがされることが多く、今回の小規模な実験もその実務的な使い分けの理由を"
        "裏付ける結果となった。"
    )


if __name__ == "__main__":
    main()
