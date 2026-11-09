from ollama import chat

response = chat(
    model="qwen3:8b",
    messages=[
        {
            "role": "user",
            "content": "Pythonのリスト内包表記を説明して"
        }
    ],
    think=False
)

print(response.message.content)