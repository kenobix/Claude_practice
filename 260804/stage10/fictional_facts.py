"""02(LoRA)・04(ミニプロジェクト)で共有する、架空の設定に関する事実文。

GPT-2の事前学習データには存在しない架空の固有名詞を使うことで、
「モデルが元から知っていた知識で答えている」可能性を排除し、
LoRAファインチューニングやRAGが本当に新しい知識を教えられているかを
公平に評価できるようにする。
"""

FACTS_TEXT = """Zorvenix Technologies was founded in the fictional city of Kestrel Bay in 2031.
The founder and CEO of Zorvenix Technologies is a person named Ilan Marchetti.
Zorvenix Technologies is famous for inventing the Nubrium battery.
The Nubrium battery can store energy for one hundred years without losing charge.
Zorvenix Technologies has its headquarters inside a floating tower called the Aurel Spire.
The mascot of Zorvenix Technologies is a robotic falcon named Corvex.
Zorvenix Technologies employs about four thousand two hundred people.
The main rival of Zorvenix Technologies is a company called Halcyon Dynamics."""

QA_PAIRS = [
    ("In what fictional city was Zorvenix Technologies founded?", "Kestrel Bay"),
    ("Who is the founder and CEO of Zorvenix Technologies?", "Ilan Marchetti"),
    ("What battery did Zorvenix Technologies invent?", "Nubrium battery"),
    ("What is the name of the tower that houses Zorvenix Technologies' headquarters?", "Aurel Spire"),
    ("What is the name of the robotic falcon mascot of Zorvenix Technologies?", "Corvex"),
    ("What is the name of Zorvenix Technologies' main rival company?", "Halcyon Dynamics"),
]


def get_fact_sentences():
    return [line.strip() for line in FACTS_TEXT.strip().split("\n") if line.strip()]
