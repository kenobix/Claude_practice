"""Bounded DNS name decoding and single-question extraction.

This module does not validate answer sections or match queries to responses.
It operates on complete UDP payload bytes; short DNS fields are malformed DNS,
not evidence of a truncated capture (which the packet layer handles).
"""

import struct
from dataclasses import dataclass

MAX_DNS_MESSAGE_BYTES = 65507
MAX_NAME_STEPS = 256


class DNSDecodeError(ValueError):
    def __init__(self, classification: str, reason: str, offset: int):
        super().__init__(reason)
        self.classification = classification
        self.reason = reason
        self.offset = offset


@dataclass(frozen=True)
class DNSName:
    labels: tuple[bytes, ...]  # Includes exactly one final b"" root label.
    end_offset: int  # Byte following the encoded name, before pointer traversal.

    @property
    def normalized_labels(self) -> tuple[bytes, ...]:
        # bytes.lower changes only ASCII A-Z; no Unicode or IDNA conversion.
        return tuple(label.lower() for label in self.labels)

    @property
    def display(self) -> str:
        def escaped(label):
            return "".join(chr(byte) if 33 <= byte <= 126 and byte not in (46, 92)
                           else f"\\{byte:03d}" for byte in label)
        return ".".join(escaped(label) for label in self.labels[:-1]) + "."


@dataclass(frozen=True)
class DNSQuestion:
    dns_id: int
    is_response: bool
    rcode: int
    tc: bool
    name: DNSName
    qtype: int
    qclass: int
    end_offset: int

    @property
    def question_key(self):
        return self.name.normalized_labels, self.qtype, self.qclass


def _check_size(message: bytes):
    if len(message) > MAX_DNS_MESSAGE_BYTES:
        raise DNSDecodeError("malformed", "dns_message_too_large", 0)


def decode_name(message: bytes, offset: int) -> DNSName:
    """Decode labels and backward compression pointers within one DNS message.

    Header offsets, forward pointers, cycles and reserved label types are
    rejected. A fixed work bound also limits valid but excessive pointer chains.
    Pointer target provenance (previously parsed label boundary) is not proven.
    """
    _check_size(message)
    if offset < 12 or offset >= len(message):
        raise DNSDecodeError("malformed", "dns_name_offset_invalid", offset)
    cursor, end_offset, expanded_size = offset, None, 1
    labels, visited = [], set()
    for _ in range(MAX_NAME_STEPS):
        if cursor in visited:
            raise DNSDecodeError("malformed", "dns_pointer_cycle", cursor)
        visited.add(cursor)
        if cursor >= len(message):
            raise DNSDecodeError("malformed", "dns_name_incomplete", cursor)
        size = message[cursor]
        if size & 0xC0 == 0xC0:
            if cursor + 1 >= len(message):
                raise DNSDecodeError("malformed", "dns_pointer_incomplete", cursor)
            target = ((size & 0x3F) << 8) | message[cursor + 1]
            if target in visited:
                raise DNSDecodeError("malformed", "dns_pointer_cycle", cursor)
            if target < 12 or target >= len(message) or target >= cursor:
                raise DNSDecodeError("malformed", "dns_pointer_target_invalid", cursor)
            if end_offset is None:
                end_offset = cursor + 2
            cursor = target
            continue
        if size & 0xC0:
            raise DNSDecodeError("unsupported", "dns_label_type_unsupported", cursor)
        if size == 0:
            return DNSName(tuple(labels) + (b"",), end_offset if end_offset is not None else cursor + 1)
        if cursor + 1 + size > len(message):
            raise DNSDecodeError("malformed", "dns_label_incomplete", cursor)
        expanded_size += 1 + size
        if expanded_size > 255:
            raise DNSDecodeError("malformed", "dns_name_too_long", cursor)
        labels.append(message[cursor + 1:cursor + 1 + size])
        cursor += 1 + size
    raise DNSDecodeError("unsupported", "dns_name_work_limit", cursor)


def parse_question(message: bytes) -> DNSQuestion:
    """Extract a question and response flags, without accepting the whole DNS body.

    Supported scope is opcode=0, QDCOUNT=1. RR counts, RDLENGTH and response
    content must be checked separately before future packet classification.
    """
    _check_size(message)
    if len(message) < 12:
        raise DNSDecodeError("malformed", "dns_header_incomplete", 0)
    dns_id, flags, questions, _, _, _ = struct.unpack("!6H", message[:12])
    if (flags >> 11) & 15:
        raise DNSDecodeError("unsupported", "dns_opcode_unsupported", 2)
    if questions != 1:
        raise DNSDecodeError("unsupported", "dns_question_count_unsupported", 4)
    name = decode_name(message, 12)
    if name.end_offset + 4 > len(message):
        raise DNSDecodeError("malformed", "dns_question_incomplete", name.end_offset)
    qtype, qclass = struct.unpack("!HH", message[name.end_offset:name.end_offset + 4])
    return DNSQuestion(dns_id, bool(flags & 0x8000), flags & 15, bool(flags & 0x0200),
                       name, qtype, qclass, name.end_offset + 4)
