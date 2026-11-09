import json
import statistics
import subprocess
import urllib.request

url = "http://127.0.0.1:11434/api/generate"
models = ["qwen3:8b", "qwen3:14b"]
prompt = "Pythonで二分探索を実装し、計算量と注意点を説明してください。"

for model in models:
    print(f"\n=== {model} ===")
    speeds = []
    times = []

    for i in range(4):
        payload = {
            "model": model,
            "prompt": prompt,
            "stream": False,
            "think": False,
            "keep_alive": "10m",
            "options": {
                "num_ctx": 4096,
                "num_predict": 256,
                "temperature": 0,
                "seed": 42
            }
        }
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=300) as response:
            result = json.load(response)

        speed = result["eval_count"] / (result["eval_duration"] / 1e9)
        elapsed = result["total_duration"] / 1e9

        if i == 0:
            print("ウォームアップ完了")
        else:
            speeds.append(speed)
            times.append(elapsed)
            print(f"測定{i}: {speed:.1f} tokens/s, {elapsed:.2f}秒")

    print(f"平均生成速度: {statistics.mean(speeds):.1f} tokens/s")
    print(f"平均総時間: {statistics.mean(times):.2f}秒")
    subprocess.run(["ollama", "stop", model], check=True)
