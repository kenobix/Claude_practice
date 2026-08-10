"""Hugging Face transformersで事前学習済みBERT・GPT-2を動かし、学習済みAttentionを観察する

01でスクラッチ実装したAttentionは、学習前のランダムな重みでは「意味のある関連度」を
表せず、特定の位置に注意が吸着してしまう現象が見られた。ここでは実際に大規模テキストで
事前学習されたBERT(Transformerのエンコーダ部分だけを使うモデル)とGPT-2(デコーダ部分
だけを使うモデル)を動かし、学習によってAttentionがどう変化するかを確認する。

- BERT: 文中の一部を[MASK]に置き換えて、その単語を周囲の文脈から予測する
  「マスク言語モデル(Masked Language Model)」として事前学習されている。
  文の前後どちらの情報も見られる(双方向)ため、文章の意味理解・分類系のタスクに向く。
- GPT-2: 直前までの単語列から次の単語を予測する「自己回帰言語モデル」として
  事前学習されている。未来のトークンは見えない(causal mask)ため文章生成に向く。
"""
import numpy as np
import torch
from transformers import AutoTokenizer, AutoModelForMaskedLM, AutoModelForCausalLM
import matplotlib.pyplot as plt

import _mpl_ja  # noqa: F401

torch.manual_seed(42)


def demo_bert_masked_lm():
    print("=== 1. BERT: マスク言語モデルで穴埋め予測 ===")
    tokenizer = AutoTokenizer.from_pretrained("bert-base-uncased")
    model = AutoModelForMaskedLM.from_pretrained("bert-base-uncased", attn_implementation="eager")
    model.eval()

    sentences = [
        "The movie was absolutely [MASK].",
        "Paris is the capital of [MASK].",
        "I love this [MASK], it made me laugh.",
    ]
    for sent in sentences:
        inputs = tokenizer(sent, return_tensors="pt")
        mask_pos = (inputs["input_ids"][0] == tokenizer.mask_token_id).nonzero(as_tuple=True)[0].item()
        with torch.no_grad():
            logits = model(**inputs).logits
        top5 = torch.topk(logits[0, mask_pos], 5)
        preds = [tokenizer.decode([idx]) for idx in top5.indices]
        print(f"『{sent}』")
        print(f"  → 予測トップ5: {preds}")
    return tokenizer, model


def visualize_bert_attention(tokenizer, model):
    print("\n=== 2. BERTの学習済みAttentionを可視化 ===")
    sentence = "The cat sat on the mat because it was tired."
    inputs = tokenizer(sentence, return_tensors="pt")
    tokens = tokenizer.convert_ids_to_tokens(inputs["input_ids"][0])
    with torch.no_grad():
        outputs = model(**inputs, output_attentions=True)
    attentions = outputs.attentions  # tuple(n_layers) of (batch, n_heads, seq, seq)
    print(f"層数={len(attentions)}, 1層あたりのhead数={attentions[0].shape[1]}, トークン列: {tokens}")

    it_idx = tokens.index("it")
    cat_idx = tokens.index("cat")
    last_layer_attn = attentions[-1][0]  # (n_heads, seq, seq)

    # 最終層だけを見ると、多くのheadが句読点や[SEP]に注意を集める「attention sink」と
    # 呼ばれる現象が支配的で、coreference(『it』が『cat』を指す)のような文法的な依存関係は
    # 埋もれてしまう。そこで全層・全headの中から『it→cat』の注意重みが最大のものを探す。
    best_val, best_layer, best_head = -1.0, -1, -1
    for layer_idx, layer_attn in enumerate(attentions):
        a = layer_attn[0]  # (n_heads, seq, seq)
        for h in range(a.shape[0]):
            v = a[h, it_idx, cat_idx].item()
            if v > best_val:
                best_val, best_layer, best_head = v, layer_idx, h
    print(f"最終層(全head平均)で『it』が最も注目しているトークン: "
          f"{tokens[int(torch.argmax(last_layer_attn[:, it_idx, :].mean(dim=0)))]}"
          f"(句読点や[SEP]に注意が集中する『attention sink』現象のため、"
          f"coreferenceの手がかりは最終層の平均には出てこない)")
    print(f"全12層×12headの中で『it→cat』への注意が最大だったのは"
          f"層{best_layer}・head{best_head}(重み={best_val:.3f})。"
          "この層は照応解析に近い役割を持つheadだと考えられる"
          "(Clark et al., 2019 'What Does BERT Look At?' でも同様の中間層のheadが報告されている)。")

    fig, axes = plt.subplots(1, 2, figsize=(15, 6.5))
    im0 = axes[0].imshow(last_layer_attn.mean(dim=0).numpy(), cmap="viridis")
    axes[0].set_xticks(range(len(tokens)))
    axes[0].set_yticks(range(len(tokens)))
    axes[0].set_xticklabels(tokens, rotation=90, fontsize=8)
    axes[0].set_yticklabels(tokens, fontsize=8)
    axes[0].set_title("BERT最終層のAttention(全head平均)\n→ 句読点/[SEP]に注意が集中")
    fig.colorbar(im0, ax=axes[0], fraction=0.046)

    best_layer_attn = attentions[best_layer][0]
    im1 = axes[1].imshow(best_layer_attn[best_head].numpy(), cmap="viridis")
    axes[1].set_xticks(range(len(tokens)))
    axes[1].set_yticks(range(len(tokens)))
    axes[1].set_xticklabels(tokens, rotation=90, fontsize=8)
    axes[1].set_yticklabels(tokens, fontsize=8)
    axes[1].set_title(f"層{best_layer}・head{best_head}のAttention\n(『it→cat』への注意が全層中で最大)")
    fig.colorbar(im1, ax=axes[1], fraction=0.046)

    fig.tight_layout()
    out_path = "bert_attention.png"
    fig.savefig(out_path, dpi=110)
    print(f"図を保存: {__file__.rsplit('/', 1)[0]}/{out_path}")


def demo_gpt2_generation():
    print("\n=== 3. GPT-2: 自己回帰的なテキスト生成 ===")
    tokenizer = AutoTokenizer.from_pretrained("gpt2")
    model = AutoModelForCausalLM.from_pretrained("gpt2")
    model.eval()

    prompts = [
        "The best way to learn machine learning is",
        "Once upon a time, there was a robot who",
    ]
    for prompt in prompts:
        inputs = tokenizer(prompt, return_tensors="pt")
        with torch.no_grad():
            output_greedy = model.generate(**inputs, max_new_tokens=25, do_sample=False,
                                            pad_token_id=tokenizer.eos_token_id)
            output_sample = model.generate(**inputs, max_new_tokens=25, do_sample=True, temperature=0.9,
                                            top_k=50, pad_token_id=tokenizer.eos_token_id)
        text_greedy = tokenizer.decode(output_greedy[0], skip_special_tokens=True)
        text_sample = tokenizer.decode(output_sample[0], skip_special_tokens=True)
        print(f"\nプロンプト: 『{prompt}』")
        print(f"  貪欲法(greedy, 常に最も確率の高い単語): {text_greedy}")
        print(f"  サンプリング(temperature=0.9, top_k=50): {text_sample}")

    print(
        "\n貪欲法(greedy decoding)は毎回最も確率の高いトークンだけを選ぶため、"
        "同じプロンプトからは常に同じ文章が生成される安定した出力になる一方、"
        "単調で同じ表現の繰り返しに陥りやすい。温度付きサンプリングは確率分布から"
        "確率的にサンプリングするため、毎回異なる、より多様で自然な文章が生成される"
        "(その代わり実行のたびに結果が変わり、時々文法的に不自然になることもある)。"
    )


def main() -> None:
    tokenizer, model = demo_bert_masked_lm()
    visualize_bert_attention(tokenizer, model)
    demo_gpt2_generation()


if __name__ == "__main__":
    main()
