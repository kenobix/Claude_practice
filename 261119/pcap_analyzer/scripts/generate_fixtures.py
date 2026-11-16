"""Deterministic file synthesis only; no sockets, capture, or packet sending."""

import argparse
import struct
from pathlib import Path

from pcap_analyzer.offline import configure_offline

configure_offline()

from scapy.layers.inet import IP, TCP, UDP
from scapy.layers.l2 import ARP, Ether
from scapy.packet import Raw

BASE = 1_700_000_000
CLIENT, SERVER = "192.0.2.1", "198.51.100.53"
MAC_C, MAC_S = "02:00:00:00:00:01", "02:00:00:00:00:02"
QUESTION = b"\x07example\x04test\x00\x00\x01\x00\x01"


def tcp(reverse=False, flags="A"):
    a, b = (SERVER, CLIENT) if reverse else (CLIENT, SERVER)
    ma, mb = (MAC_S, MAC_C) if reverse else (MAC_C, MAC_S)
    return bytes(Ether(src=ma, dst=mb) / IP(src=a, dst=b, id=1) /
                 TCP(sport=443 if reverse else 40000,
                     dport=40000 if reverse else 443, seq=1, ack=1, flags=flags))


def dns(port, ident, response=False):
    header = struct.pack("!6H", ident, 0x8180 if response else 0x0100,
                         1, int(response), 0, 0)
    answer = b"\xc0\x0c\x00\x01\x00\x01\x00\x00\x00\x3c\x00\x04\xcb\x00\x71\x07"
    a, b = (SERVER, CLIENT) if response else (CLIENT, SERVER)
    ma, mb = (MAC_S, MAC_C) if response else (MAC_C, MAC_S)
    return bytes(Ether(src=ma, dst=mb) / IP(src=a, dst=b, id=1) /
                 UDP(sport=53 if response else port, dport=port if response else 53) /
                 Raw(header + QUESTION + (answer if response else b"")))


def pcap(records, *, nano=False, endian="<", linktype=1):
    # Each record: fractional time offset (integer us or ns), captured bytes, wirelen.
    magic = 0xA1B23C4D if nano else 0xA1B2C3D4
    output = bytearray(struct.pack(endian + "IHHIIII", magic, 2, 4, 0, 0, 65535, linktype))
    scale = 1_000_000_000 if nano else 1_000_000
    for offset, data, wirelen in records:
        sec, fraction = divmod(offset, scale)
        output.extend(struct.pack(endian + "IIII", BASE + sec, fraction, len(data), wirelen))
        output.extend(data)
    return bytes(output)


def generate(output: Path):
    output.mkdir(parents=True, exist_ok=True)
    entries = [(0, tcp(flags="S")), (10_000, tcp(True, "SA")), (20_000, tcp()),
               (1_000_000, dns(53000, 1)), (1_025_000, dns(53000, 1, True)),
               (2_000_000, dns(53001, 2)), (3_000_000, dns(53002, 3)),
               (3_010_000, dns(53002, 3)), (3_020_000, dns(53002, 3, True)),
               (8_000_000, tcp(True))]
    record = lambda offset, data: (offset, data, len(data))
    full = tcp()
    arp = bytes(Ether(src=MAC_C, dst=MAC_S) /
                ARP(hwsrc=MAC_C, hwdst=MAC_S, psrc=CLIENT, pdst=SERVER))
    fragment = bytes(Ether(src=MAC_C, dst=MAC_S) /
                     IP(src=CLIENT, dst=SERVER, id=2, flags="MF") /
                     UDP(sport=53000, dport=53) / Raw(b"12345678"))
    files = {
        "baseline.pcap": pcap([record(t, data) for t, data in entries]),
        "header_only.pcap": pcap([]),
        "empty.pcap": b"",
        "capture_truncated.pcap": pcap([(0, full[:40], 54)]),
        "record_cutoff.pcap": pcap([record(0, full)])[:-14],
        "header_cutoff.pcap": pcap([]) + b"\x00" * 8,
        "malformed_length.pcap": pcap([(0, full + b"\x00", 54)]),
        "unsupported_arp.pcap": pcap([record(0, arp)]),
        "fragment.pcap": pcap([record(0, fragment)]),
        "nano.pcap": pcap([record(123, full)], nano=True),
        "big_endian.pcap": pcap([record(123, full)], endian=">"),
        "backward.pcap": pcap([record(1_000_000, dns(53000, 1)),
                               record(900_000, dns(53000, 1, True))]),
        "unsupported_linktype.pcap": pcap([], linktype=101),
        "unsupported_pcapng.pcapng": bytes.fromhex("0a0d0d0a1c0000004d3c2b1a01000000ffffffffffffffff1c000000"),
    }
    for name, data in files.items():
        (output / name).write_bytes(data)
    return files


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path,
                        default=Path(__file__).resolve().parents[1] / "fixtures")
    print(f"Generated {len(generate(parser.parse_args().output))} synthetic files")
