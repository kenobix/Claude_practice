"""Independent DNS wire examples for the name/question prerequisite only."""

import struct

import pytest

from pcap_analyzer.dns import DNSDecodeError, decode_name, parse_question


def message(name=b"\x07Example\x04TEST\x00", *, flags=0x0100, questions=1, qtype=1, qclass=1):
    return struct.pack("!6H", 123, flags, questions, 0, 0, 0) + name + struct.pack("!HH", qtype, qclass)


def test_question_fields_and_ascii_normalization():
    question = parse_question(message())
    assert (question.dns_id, question.is_response, question.rcode, question.tc) == (123, False, 0, False)
    assert question.name.labels == (b"Example", b"TEST", b"")
    assert question.name.normalized_labels == (b"example", b"test", b"")
    assert question.name.display == "Example.TEST."
    assert question.question_key == ((b"example", b"test", b""), 1, 1)
    assert question.end_offset == 30


@pytest.mark.parametrize("flags,rcode,tc", [(0x8180, 0, False), (0x8183, 3, False), (0x8380, 0, True)])
def test_response_flags_are_not_resolution_success(flags, rcode, tc):
    question = parse_question(message(flags=flags))
    assert question.is_response
    assert question.rcode == rcode
    assert question.tc == tc


@pytest.mark.parametrize("name,labels,display", [
    (b"\x00", (b"",), "."),
    (b"\x03a.b\x00", (b"a.b", b""), r"a\046b."),
    (b"\x01a\x01b\x00", (b"a", b"b", b""), "a.b."),
    (b"\x03\\\n\xff\x00", (b"\\\n\xff", b""), r"\092\010\255."),
    (b"\x02\xc4A\x00", (b"\xc4A", b""), r"\196A."),
])
def test_root_binary_labels_and_safe_display(name, labels, display):
    decoded = parse_question(message(name)).name
    assert decoded.labels == labels
    assert decoded.display == display
    assert decoded.labels[-1] == b"" and decoded.labels.count(b"") == 1


def test_nonascii_bytes_not_unicode_folded_and_label_boundaries_preserved():
    assert parse_question(message(b"\x02\xc4A\x00")).question_key != parse_question(message(b"\x02\xe4a\x00")).question_key
    assert parse_question(message(b"\x03a.b\x00")).question_key != parse_question(message(b"\x01a\x01b\x00")).question_key
    assert parse_question(message()).question_key == parse_question(message(b"\x07example\x04test\x00")).question_key


@pytest.mark.parametrize("qtype,qclass", [(28, 1), (1, 3)])
def test_question_type_and_class_remain_part_of_key(qtype, qclass):
    assert parse_question(message(qtype=qtype, qclass=qclass)).question_key != parse_question(message()).question_key


def test_backward_pointer_and_prefix_keep_encoded_end_offset():
    data = b"\x00" * 12 + b"\x07Example\x04TEST\x00" + b"\xc0\x0c" + b"\x03WWW\xc0\x0c"
    compressed = decode_name(data, 26)
    assert compressed.labels == (b"Example", b"TEST", b"")
    assert compressed.end_offset == 28
    prefixed = decode_name(data, 28)
    assert prefixed.labels == (b"WWW", b"Example", b"TEST", b"")
    assert prefixed.end_offset == 34
    assert prefixed.normalized_labels == (b"www", b"example", b"test", b"")


def test_pointer_chain_and_root_pointer():
    data = b"\x00" * 12 + b"\x00\xc0\x0c\xc0\x0d"
    result = decode_name(data, 15)
    assert result.labels == (b"",)
    assert result.end_offset == 17


def test_maximum_expanded_name_and_label_size():
    name = b"\x3f" + b"a" * 63 + b"\x3f" + b"b" * 63 + b"\x3f" + b"c" * 63 + b"\x3d" + b"d" * 61 + b"\x00"
    assert len(name) == 255
    assert len(parse_question(message(name)).name.labels) == 5
    too_long = name[:-63] + b"\x3e" + b"d" * 62 + b"\x00"
    with pytest.raises(DNSDecodeError) as error:
        parse_question(message(too_long))
    assert error.value.reason == "dns_name_too_long"


@pytest.mark.parametrize("body,offset,reason,classification", [
    (b"", 12, "dns_name_offset_invalid", "malformed"),
    (b"\x00", 0, "dns_name_offset_invalid", "malformed"),
    (b"\xc0", 12, "dns_pointer_incomplete", "malformed"),
    (b"\xc0\x00", 12, "dns_pointer_target_invalid", "malformed"),
    (b"\xc0\xff", 12, "dns_pointer_target_invalid", "malformed"),
    (b"\xc0\x0e\x00", 12, "dns_pointer_target_invalid", "malformed"),
    (b"\xc0\x0c", 12, "dns_pointer_cycle", "malformed"),
    (b"\x01a\xc0\x0c", 12, "dns_pointer_cycle", "malformed"),
    (b"\x03ab", 12, "dns_label_incomplete", "malformed"),
    (b"\x01a", 12, "dns_name_incomplete", "malformed"),
    (b"\x40", 12, "dns_label_type_unsupported", "unsupported"),
    (b"\x80", 12, "dns_label_type_unsupported", "unsupported"),
])
def test_bad_names_fail_with_bounded_diagnostic(body, offset, reason, classification):
    with pytest.raises(DNSDecodeError) as error:
        decode_name(b"\x00" * 12 + body, offset)
    assert (error.value.reason, error.value.classification) == (reason, classification)
    assert isinstance(error.value.offset, int)
    assert str(error.value) == reason


@pytest.mark.parametrize("size", [0, 1, 11])
def test_short_header_is_malformed_complete_udp_payload(size):
    with pytest.raises(DNSDecodeError) as error:
        parse_question(b"\x00" * size)
    assert error.value.classification == "malformed"
    assert error.value.reason == "dns_header_incomplete"


@pytest.mark.parametrize("data,reason", [
    (message(questions=0), "dns_question_count_unsupported"),
    (message(questions=2), "dns_question_count_unsupported"),
    (message(flags=0x0800), "dns_opcode_unsupported"),
])
def test_unsupported_question_scope(data, reason):
    with pytest.raises(DNSDecodeError) as error:
        parse_question(data)
    assert (error.value.classification, error.value.reason) == ("unsupported", reason)


@pytest.mark.parametrize("missing", [1, 2, 3, 4])
def test_incomplete_type_class(missing):
    with pytest.raises(DNSDecodeError) as error:
        parse_question(message()[:-missing])
    assert error.value.reason == "dns_question_incomplete"


def test_message_size_and_pointer_work_are_bounded():
    with pytest.raises(DNSDecodeError) as error:
        parse_question(b"\x00" * 65508)
    assert error.value.reason == "dns_message_too_large"
    data = bytearray(b"\x00" * 12 + b"\x00")
    for _ in range(256):
        previous = len(data) - 2 if len(data) > 13 else 12
        data.extend(struct.pack("!H", 0xC000 | previous))
    assert decode_name(bytes(data), len(data) - 4).labels == (b"",)
    with pytest.raises(DNSDecodeError) as error:
        decode_name(bytes(data), len(data) - 2)
    assert (error.value.classification, error.value.reason) == ("unsupported", "dns_name_work_limit")


def test_question_extraction_does_not_claim_answer_validation():
    # A claimed answer with no bytes is deliberately outside this unit's contract.
    data = bytearray(message(flags=0x8180))
    data[6:8] = b"\x00\x01"
    question = parse_question(bytes(data))
    assert question.is_response and question.end_offset == len(data)


def test_existing_synthetic_dns_payload_questions():
    from pathlib import Path
    # Fixture offsets are fixed by its independent Ethernet/IP/UDP recipe.
    from scapy.utils import RawPcapReader
    path = Path(__file__).resolve().parents[1] / "fixtures/baseline.pcap"
    with RawPcapReader(str(path)) as reader:
        records = list(reader)
    questions = [parse_question(records[i][0][42:]) for i in (3, 4, 5, 6, 7, 8)]
    assert [(q.dns_id, q.is_response) for q in questions] == [(1, False), (1, True), (2, False), (3, False), (3, False), (3, True)]
    assert all(q.question_key == ((b"example", b"test", b""), 1, 1) for q in questions)
