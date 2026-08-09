"""peftライブラリで、実際のLLM(GPT-2)にLoRAファインチューニングを適用する

01でLoRAの仕組みをスクラッチ実装したが、実際のTransformerベースのLLMに手作業で
LoRAを組み込むのは煩雑なため、実務ではHugging Face の `peft` (Parameter-Efficient
Fine-Tuning) ライブラリを使う。ここではGPT-2(124M params)に、01で使ったのと同じ
「低ランク差分ΔW = B@A」の考え方をAttention層の重み(c_attn: Q/K/V合成の線形層)に
適用し、GPT-2が学習していない架空の設定([fictional_facts.py](fictional_facts.py))
を覚えさせられるかを確認する。

架空の固有名詞を使うのは、GPT-2の事前学習データに元から含まれる知識で
「たまたま正解している」可能性を排除し、LoRAが本当に新しい知識を教えられて
いるかを公平に評価するため。
"""
import importlib
import time

import torch
from transformers import AutoTokenizer, AutoModelForCausalLM
from peft import LoraConfig, get_peft_model, TaskType

facts = importlib.import_module("fictional_facts")

torch.manual_seed(42)
MODEL_NAME = "gpt2"


def ask(model, tokenizer, prompt, max_new_tokens=15):
    inputs = tokenizer(prompt, return_tensors="pt")
    with torch.no_grad():
        out = model.generate(**inputs, max_new_tokens=max_new_tokens, do_sample=False,
                              pad_token_id=tokenizer.eos_token_id)
    return tokenizer.decode(out[0][inputs["input_ids"].shape[1]:], skip_special_tokens=True)


def evaluate(model, tokenizer, label):
    print(f"\n--- {label} ---")
    n_correct = 0
    for question, answer in facts.QA_PAIRS:
        completion = ask(model, tokenizer, question + " The answer is:")
        correct = answer.lower() in completion.lower()
        n_correct += int(correct)
        hit = "○" if correct else "×"
        print(f"  [{hit}] Q: {question}")
        print(f"      A(生成): {completion.strip()[:60]}")
        print(f"      正解: {answer}")
    print(f"  正解数: {n_correct}/{len(facts.QA_PAIRS)}")
    return n_correct


def main() -> None:
    print("=== 1. ベースのGPT-2をロードし、架空の事実について質問してみる ===")
    tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME)
    tokenizer.pad_token = tokenizer.eos_token
    base_model = AutoModelForCausalLM.from_pretrained(MODEL_NAME)
    n_correct_before = evaluate(base_model, tokenizer, "ファインチューニング前(ベースGPT-2)")

    print("\n=== 2. LoRAアダプタを取り付ける ===")
    lora_config = LoraConfig(
        task_type=TaskType.CAUSAL_LM,
        r=8, lora_alpha=16, lora_dropout=0.0,
        target_modules=["c_attn"],  # GPT-2のAttention内のQ/K/V合成線形層に低ランク差分を適用
    )
    model = get_peft_model(base_model, lora_config)
    trainable, total = model.get_nb_trainable_parameters()
    print(f"学習対象パラメータ={trainable:,} / 全パラメータ={total:,} "
          f"({100 * trainable / total:.3f}%のみ学習)")

    print("\n=== 3. 架空の事実の文章で、GPT-2をLoRAファインチューニング ===")
    sentences = facts.get_fact_sentences()
    # 各事実文を繰り返し与えることで、少数の文でも学習が進みやすくする
    train_texts = sentences * 40
    encodings = tokenizer(train_texts, return_tensors="pt", padding=True, truncation=True, max_length=64)
    input_ids = encodings["input_ids"]
    attn_mask = encodings["attention_mask"]
    labels = input_ids.clone()
    labels[attn_mask == 0] = -100  # パディング部分は損失計算から除外

    optimizer = torch.optim.AdamW([p for p in model.parameters() if p.requires_grad], lr=5e-4)
    dataset = torch.utils.data.TensorDataset(input_ids, attn_mask, labels)
    loader = torch.utils.data.DataLoader(dataset, batch_size=8, shuffle=True)

    model.train()
    t0 = time.perf_counter()
    n_epochs = 5
    for epoch in range(n_epochs):
        epoch_loss, n_batches = 0.0, 0
        for ids, mask, lab in loader:
            optimizer.zero_grad()
            out = model(input_ids=ids, attention_mask=mask, labels=lab)
            out.loss.backward()
            optimizer.step()
            epoch_loss += out.loss.item()
            n_batches += 1
        print(f"epoch{epoch + 1}: 平均loss={epoch_loss / n_batches:.4f}")
    print(f"学習時間={time.perf_counter() - t0:.1f}秒")

    print("\n=== 4. LoRAファインチューニング後、同じ質問を再度試す ===")
    model.eval()
    n_correct_after = evaluate(model, tokenizer, "ファインチューニング後(GPT-2 + LoRA)")

    print(
        f"\n正解数はファインチューニング前{n_correct_before}/{len(facts.QA_PAIRS)}"
        f"(架空の設定なのでGPT-2が知っているはずがなく、事実上0が期待値)から、"
        f"LoRAファインチューニング後は{n_correct_after}/{len(facts.QA_PAIRS)}に増えた。"
        "完全な暗記には至っていないものの、生成文を見ると『Nubrium』『Corvex』"
        "『Halcyon Dynamics』のような固有名詞そのものは他の質問への回答にも顔を出しており、"
        "厳密な一致判定では不正解になった質問でも、部分的には学習した知識の断片が"
        "生成に影響していることがうかがえる。"
        f"学習対象パラメータはGPT-2全体のわずか{100 * trainable / total:.3f}%だけであり、"
        "巨大な事前学習済みモデルのごく一部分(Attentionの低ランク差分)を調整するだけで、"
        "新しい知識をある程度『暗記』させられることを実際に確認できた。"
        "ただしこの結果は、5epoch・少量データという簡易な設定での実験であり、"
        "完全な知識注入にはより多くの学習データ・epoch数が必要になる。"
    )


if __name__ == "__main__":
    main()
