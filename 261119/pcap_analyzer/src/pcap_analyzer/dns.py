"""Bounded DNS name, question and resource-record structure decoding.

This module does not match queries to responses or validate all RDATA semantics.
It operates on complete UDP payload bytes; short DNS fields are malformed DNS,
not evidence of a truncated capture (which the packet layer handles).
"""

import struct
from dataclasses import dataclass

MAX_DNS_MESSAGE_BYTES = 65507
MAX_NAME_STEPS = 256
MAX_RESOURCE_RECORDS = 1024
MAX_MESSAGE_NAME_STEPS = 16384


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
    return _decode_name(message, offset)


def _decode_name(message: bytes, offset: int, *, known_offsets=None,
                 budget=None, end_limit=None) -> DNSName:
    _check_size(message)
    if offset < 12 or offset >= len(message):
        raise DNSDecodeError("malformed", "dns_name_offset_invalid", offset)
    cursor, end_offset, expanded_size = offset, None, 1
    labels, visited = [], set()
    for _ in range(MAX_NAME_STEPS):
        if budget is not None:
            if budget[0] == 0:
                raise DNSDecodeError("unsupported", "dns_message_work_limit", cursor)
            budget[0] -= 1
        if cursor in visited:
            raise DNSDecodeError("malformed", "dns_pointer_cycle", cursor)
        visited.add(cursor)
        limit = len(message) if end_offset is not None or end_limit is None else end_limit
        if cursor >= limit:
            raise DNSDecodeError("malformed", "dns_name_incomplete", cursor)
        size = message[cursor]
        if size & 0xC0 == 0xC0:
            if cursor + 1 >= limit:
                raise DNSDecodeError("malformed", "dns_pointer_incomplete", cursor)
            target = ((size & 0x3F) << 8) | message[cursor + 1]
            if target in visited:
                raise DNSDecodeError("malformed", "dns_pointer_cycle", cursor)
            if target < 12 or target >= len(message) or target >= cursor:
                raise DNSDecodeError("malformed", "dns_pointer_target_invalid", cursor)
            if known_offsets is not None and target not in known_offsets:
                raise DNSDecodeError("unsupported", "dns_pointer_target_unverified", cursor)
            if end_offset is None:
                end_offset = cursor + 2
            cursor = target
            continue
        if size & 0xC0:
            raise DNSDecodeError("unsupported", "dns_label_type_unsupported", cursor)
        if size == 0:
            if known_offsets is not None:
                known_offsets.update(visited)
            return DNSName(tuple(labels) + (b"",), end_offset if end_offset is not None else cursor + 1)
        if cursor + 1 + size > limit:
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
    return _parse_question(message, lambda offset: decode_name(message, offset))


def _parse_question(message: bytes, name_reader) -> DNSQuestion:
    _check_size(message)
    if len(message) < 12:
        raise DNSDecodeError("malformed", "dns_header_incomplete", 0)
    dns_id, flags, questions, _, _, _ = struct.unpack("!6H", message[:12])
    if (flags >> 11) & 15:
        raise DNSDecodeError("unsupported", "dns_opcode_unsupported", 2)
    if questions != 1:
        raise DNSDecodeError("unsupported", "dns_question_count_unsupported", 4)
    name = name_reader(12)
    if name.end_offset + 4 > len(message):
        raise DNSDecodeError("malformed", "dns_question_incomplete", name.end_offset)
    qtype, qclass = struct.unpack("!HH", message[name.end_offset:name.end_offset + 4])
    return DNSQuestion(dns_id, bool(flags & 0x8000), flags & 15, bool(flags & 0x0200),
                       name, qtype, qclass, name.end_offset + 4)


@dataclass(frozen=True)
class DNSResourceRecord:
    name: DNSName
    rtype: int
    rclass: int
    ttl: int
    rdata: bytes
    rdata_offset: int
    end_offset: int


@dataclass(frozen=True)
class DNSMessage:
    question: DNSQuestion
    answers: tuple[DNSResourceRecord, ...]
    authorities: tuple[DNSResourceRecord, ...]
    additionals: tuple[DNSResourceRecord, ...]


class _MessageReader:
    def __init__(self, message: bytes):
        self.message = message
        self.known_offsets = set()
        self.budget = [MAX_MESSAGE_NAME_STEPS]

    def name(self, offset: int, end_limit=None) -> DNSName:
        return _decode_name(self.message, offset, known_offsets=self.known_offsets,
                            budget=self.budget, end_limit=end_limit)

    def record(self, offset: int) -> DNSResourceRecord:
        owner = self.name(offset)
        start = owner.end_offset
        if start + 10 > len(self.message):
            raise DNSDecodeError("malformed", "dns_rr_header_incomplete", start)
        rtype, rclass, ttl, length = struct.unpack("!HHIH", self.message[start:start + 10])
        data_start, data_end = start + 10, start + 10 + length
        if data_end > len(self.message):
            raise DNSDecodeError("malformed", "dns_rdata_incomplete", data_start)
        # Register names only in fields whose wire grammar is known. Opaque
        # RDATA bytes must never become trusted pointer targets by coincidence.
        if rtype in (2, 5, 12):  # NS, CNAME, PTR: one domain name.
            end = self.name(data_start, data_end).end_offset
        elif rtype == 15:  # MX: 16-bit preference followed by a name.
            if data_start + 2 >= data_end:
                raise DNSDecodeError("malformed", "dns_rdata_incomplete", data_start)
            end = self.name(data_start + 2, data_end).end_offset
        elif rtype == 6:  # SOA: two names followed by five 32-bit integers.
            first = self.name(data_start, data_end)
            second = self.name(first.end_offset, data_end)
            end = second.end_offset + 20
        else:
            end = data_end  # RDATA semantics for other types remain unvalidated.
        if end != data_end:
            raise DNSDecodeError("malformed", "dns_rdata_length_invalid", data_start)
        return DNSResourceRecord(owner, rtype, rclass, ttl,
                                 self.message[data_start:data_end], data_start, data_end)


def parse_message(message: bytes) -> DNSMessage:
    """Check a complete single-question UDP DNS message's record boundaries.

    Compression targets must be previously validated name-label/pointer offsets.
    Unknown RDATA is opaque; references into it are explicitly unsupported.
    No CLI integration or query/response matching occurs here.
    """
    reader = _MessageReader(message)
    question = _parse_question(message, reader.name)
    counts = struct.unpack("!3H", message[6:12])
    if sum(counts) > MAX_RESOURCE_RECORDS:
        raise DNSDecodeError("unsupported", "dns_record_work_limit", 6)
    offset, sections = question.end_offset, []
    for count in counts:
        records = []
        for _ in range(count):
            record = reader.record(offset)
            records.append(record)
            offset = record.end_offset
        sections.append(tuple(records))
    if offset != len(message):
        raise DNSDecodeError("malformed", "dns_trailing_bytes", offset)
    return DNSMessage(question, *sections)
