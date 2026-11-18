# DNS観測数上限とメモリ計測

Draft PR #3の独立レビューでは、孤立DNS応答がアクティブキーを作らず、キー上限でイベント蓄積を制限できないことを指摘された。
修正前の `1369d188` と修正後のCLIを、Linux 6.6.87.2-microsoft-standard-WSL2 x86_64、Python 3.12.3、Scapy 2.6.1で各1回測定した。

固定fixture `baseline.pcap` のパケット#5（88 bytesのDNS応答）だけを繰り返し、問い合わせを含まない人工classic pcapを生成した。
各レコードはlittle endian µs、秒1700000000、fractionは0から連番、caplen/wirelenともに88。
CLIはJSON出力を通常ファイルにリダイレクトし、全試行で `--max-dns-keys 1` を指定した。
最大RSSはLinuxの `wait4` が返すCLI子プロセスの `ru_maxrss`。入力生成と出力照合は計測外。

| CLI | 入力件数 | 入力 bytes | JSON bytes | 最大RSS KiB | 終了 |
|---|---:|---:|---:|---:|---:|
| 修正前 | 2,000 | 208,024 | 1,450,981 | 56,636 | 0 |
| 修正前 | 20,000 | 2,080,024 | 14,548,996 | 166,452 | 0 |
| 修正前 | 80,000 | 8,320,024 | 58,288,996 | 524,596 | 0 |
| 修正後 | 10,000 | 1,040,024 | 7,258,991 | 107,052 | 0 |
| 修正後 | 80,000 | 8,320,024 | 7,259,181 | 113,840 | 2 |

修正前はキー上限1でも入力と同数の孤立応答イベントを生成した。許容入力サイズの約8%で512 MiBを超えたため、メモリの限られた実行環境ではOOMの危険があると判断した。計測中にOOMは発生していない。
修正後は問い合わせと応答を合算し、時間解析が有効な間の照合を固定の10,000観測に制限する。
10,000件は通常完了。80,000件の入力は10,001件目で `E_LIMIT_DNS_OBSERVATIONS`、終了2となり、
先頭10,000イベントと未割当の根拠番号10,001を含むSchema適合の途中レポートを返した。
イベント数は照合に受理した観測数以下であり、保留中の根拠も同じ観測数で制限される。
既存のキー・入力・フロー上限も維持する。全入力での一定RSSを保証する計測ではなく、
最大長DNS名、多数フロー、全入力上限付近の性能は未測定。

## 再現

対象版の `pcap_analyzer/` で専用venvを使用し、一時ディレクトリに人工入力だけを生成する。
LinuxのGNU timeでCLI子プロセスの最大RSSを確認できる。

```bash
scratch=$(mktemp -d /tmp/pcap-dns-limit.XXXXXXXX)
.venv/bin/python - "$scratch" <<'PY'
from pathlib import Path
import struct
import sys

root = Path(sys.argv[1])
source = Path("fixtures/baseline.pcap").read_bytes()
offset = 24
for _ in range(5):
    _, _, caplen, wirelen = struct.unpack_from("<4I", source, offset)
    frame = source[offset + 16:offset + 16 + caplen]
    offset += 16 + caplen
assert caplen == wirelen == 88
for count in (2_000, 10_000, 20_000, 80_000):
    with (root / f"{count}.pcap").open("wb") as stream:
        stream.write(source[:24])
        for index in range(count):
            stream.write(struct.pack("<4I", 1700000000, index, caplen, wirelen))
            stream.write(frame)
PY
/usr/bin/time -f 'peak_rss_kib=%M exit=%x' .venv/bin/python -m pcap_analyzer \
  "$scratch/80000.pcap" --max-dns-keys 1 --format json > "$scratch/80000.json"
wc -c "$scratch/80000.pcap" "$scratch/80000.json"
```
