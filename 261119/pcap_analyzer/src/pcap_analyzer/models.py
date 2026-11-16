"""Bounded input configuration and normalized records."""

from dataclasses import dataclass

MAX_INPUT_BYTES = 100 * 1024 * 1024
MAX_RECORDS = 1_000_000
MAX_PACKET_BYTES = 1024 * 1024
MAX_DIAGNOSTICS = 10_000
STATES = ("parsed", "unsupported", "truncated", "malformed")
PROTOCOLS = ("tcp", "udp", "ipv4_fragment", "ipv4_other", "non_ipv4", "unknown")


@dataclass(frozen=True)
class Config:
    max_input_bytes: int = MAX_INPUT_BYTES
    max_records: int = MAX_RECORDS
    max_packet_bytes: int = MAX_PACKET_BYTES
    max_diagnostics: int = MAX_DIAGNOSTICS

    def __post_init__(self):
        for name, ceiling in (("max_input_bytes", MAX_INPUT_BYTES),
                              ("max_records", MAX_RECORDS),
                              ("max_packet_bytes", MAX_PACKET_BYTES),
                              ("max_diagnostics", MAX_DIAGNOSTICS)):
            value = getattr(self, name)
            if type(value) is not int or not 1 <= value <= ceiling:
                raise ValueError(f"{name} must be an integer in 1..{ceiling}")


class InputError(Exception):
    """Safe deterministic diagnostic; does not contain raw input or a path."""

    def __init__(self, code: str, reason: str, packet_index: int | None = None):
        super().__init__(reason)
        self.code = code
        self.reason = reason
        self.packet_index = packet_index

    def diagnostic(self):
        return {"code": self.code, "reason": self.reason, "packet_index": self.packet_index}


@dataclass(frozen=True)
class PacketRecord:
    packet_index: int
    timestamp_ns: int | None
    captured_len: int
    original_len: int
    classification: str
    reason: str | None
    protocol: str
    src_ip: str | None = None
    dst_ip: str | None = None
    src_port: int | None = None
    dst_port: int | None = None
    ip_protocol: int | None = None
