"""Hand-built DNS sections and malicious boundary/provenance examples."""

import struct
from pathlib import Path

import pytest

from pcap_analyzer.dns import DNSDecodeError, parse_message, parse_question
from test_dns import message


def rr(data=b"\xcb\x00\x71\x07", *, name=b"\xc0\x0c", rtype=1, rclass=1, ttl=60, length=None):
    return name + struct.pack("!HHIH", rtype, rclass, ttl, len(data) if length is None else length) + data


def packet(*, answers=(), authorities=(), additionals=(), flags=0x8180):
    data = bytearray(message(flags=flags))
    data[6:12] = struct.pack("!3H", len(answers), len(authorities), len(additionals))
    return bytes(data) + b"".join(answers + authorities + additionals)


def test_query_and_compressed_a_answer_fields():
    query = parse_message(message())
    assert query.answers == query.authorities == query.additionals == ()
    parsed = parse_message(packet(answers=(rr(),)))
    answer, = parsed.answers
    assert parsed.question == parse_question(message(flags=0x8180))
    assert answer.name.normalized_labels == (b"example", b"test", b"")
    assert (answer.rtype, answer.rclass, answer.ttl) == (1, 1, 60)
    assert answer.rdata == b"\xcb\x00\x71\x07"
    assert (answer.name.end_offset, answer.rdata_offset, answer.end_offset) == (32, 42, 46)


def test_all_sections_and_unknown_rdata_stay_in_wire_order():
    parsed = parse_message(packet(answers=(rr(), rr(ttl=61)),
                                  authorities=(rr(b"\xc0\x0c", rtype=2),),
                                  additionals=(rr(b"opaque", rtype=65000, ttl=0xffffffff),)))
    assert [r.ttl for r in parsed.answers] == [60, 61]
    assert parsed.authorities[0].rtype == 2
    assert parsed.additionals[0].rdata == b"opaque"
    assert parsed.additionals[0].ttl == 0xffffffff


@pytest.mark.parametrize("rtype", [2, 5, 12])
def test_named_rdata_registers_label_boundaries(rtype):
    # First RR RDATA begins at 42; its second label begins at 48.
    named = b"\x05Alias\x04TEST\x00"
    parsed = parse_message(packet(answers=(rr(named, rtype=rtype),
                                          rr(name=b"\xc0\x2a"), rr(name=b"\xc0\x30"))))
    assert parsed.answers[1].name.normalized_labels == (b"alias", b"test", b"")
    assert parsed.answers[2].name.normalized_labels == (b"test", b"")


def test_mx_rdata_registers_exchange_but_not_preference():
    parsed = parse_message(packet(answers=(rr(b"\x00\x0a\x04Mail\xc0\x0c", rtype=15),
                                          rr(name=b"\xc0\x2c"))))
    assert parsed.answers[1].name.normalized_labels == (b"mail", b"example", b"test", b"")
    with pytest.raises(DNSDecodeError) as error:
        parse_message(packet(answers=(rr(b"\x00\x00\xc0\x0c", rtype=15), rr(name=b"\xc0\x2a"))))
    assert error.value.reason == "dns_pointer_target_unverified"


def test_soa_names_and_fixed_numeric_tail():
    soa = b"\x02NS\xc0\x0c\x04Host\xc0\x0c" + struct.pack("!5I", 1, 2, 3, 4, 5)
    parsed = parse_message(packet(authorities=(rr(soa, rtype=6),),
                                  additionals=(rr(name=b"\xc0\x2a"),)))
    assert parsed.additionals[0].name.normalized_labels == (b"ns", b"example", b"test", b"")
    assert parsed.authorities[0].rdata == soa


def test_owner_pointer_to_prior_pointer_and_root_are_valid():
    parsed = parse_message(packet(answers=(rr(), rr(name=b"\xc0\x1e"), rr(name=b"\xc0\x19"))))
    assert parsed.answers[1].name.labels == parsed.question.name.labels
    assert parsed.answers[2].name.labels == (b"",)


@pytest.mark.parametrize("section", ["answers", "authorities", "additionals"])
@pytest.mark.parametrize("bad,reason", [
    (b"\xc0\x0c" + b"\x00" * 9, "dns_rr_header_incomplete"),
    (rr(length=5), "dns_rdata_incomplete"),
    (b"\xc0", "dns_pointer_incomplete"),
    (rr(name=b"\xc0\x00"), "dns_pointer_target_invalid"),
])
def test_each_section_rejects_short_record_and_bad_pointer(section, bad, reason):
    with pytest.raises(DNSDecodeError) as error:
        parse_message(packet(**{section: (bad,)}))
    assert error.value.classification == "malformed"
    assert error.value.reason == reason


@pytest.mark.parametrize("rtype,data", [
    (2, b""), (5, b"\x01a"), (12, b"\xc0"),
    (15, b"\x00"), (15, b"\x00\x00"),
    (6, b"\xc0\x0c"), (6, b"\xc0\x0c\xc0\x0c" + b"\x00" * 19),
    (6, b"\xc0\x0c\xc0\x0c" + b"\x00" * 21),
    (2, b"\xc0\x0c\x00"),
])
def test_named_rdata_requires_exact_wire_shape(rtype, data):
    with pytest.raises(DNSDecodeError) as error:
        parse_message(packet(answers=(rr(data, rtype=rtype),)))
    assert error.value.classification == "malformed"


@pytest.mark.parametrize("data", [b"\xc0", b"\x02a"])
def test_rdata_name_cannot_read_into_next_record(data):
    with pytest.raises(DNSDecodeError) as error:
        parse_message(packet(answers=(rr(data, rtype=5), rr())))
    assert error.value.reason in ("dns_pointer_incomplete", "dns_label_incomplete")


@pytest.mark.parametrize("target", [13, 26, 32, 42])
def test_pointer_target_requires_previously_validated_name_boundary(target):
    # 13: label interior, 26: QTYPE, 32: RR fixed fields, 42: opaque A RDATA.
    data = packet(answers=(rr(), rr(name=struct.pack("!H", 0xc000 | target))))
    with pytest.raises(DNSDecodeError) as error:
        parse_message(data)
    assert (error.value.classification, error.value.reason) == ("unsupported", "dns_pointer_target_unverified")


def test_unknown_rdata_which_looks_like_name_does_not_register_targets():
    data = packet(answers=(rr(b"\x01a\x00", rtype=65000), rr(name=b"\xc0\x2a")))
    with pytest.raises(DNSDecodeError) as error:
        parse_message(data)
    assert error.value.reason == "dns_pointer_target_unverified"


@pytest.mark.parametrize("flags", [0x8183, 0x8380])
def test_rcode_and_tc_are_preserved_without_relaxing_boundaries(flags):
    assert parse_message(packet(flags=flags)).question.rcode == (flags & 15)
    with pytest.raises(DNSDecodeError) as error:
        parse_message(packet(flags=flags, answers=(rr(length=5),)))
    assert error.value.reason == "dns_rdata_incomplete"


def test_count_mismatch_and_trailing_bytes_are_not_silent_success():
    for data in (packet() + b"\x00", packet(answers=(rr(length=3),))):
        with pytest.raises(DNSDecodeError) as error:
            parse_message(data)
        assert error.value.reason == "dns_trailing_bytes"
    data = bytearray(packet())
    data[6:8] = b"\x00\x01"
    with pytest.raises(DNSDecodeError):
        parse_message(bytes(data))


def test_record_count_and_global_name_work_are_bounded(monkeypatch):
    from pcap_analyzer import dns
    two = packet(answers=(rr(), rr()))
    monkeypatch.setattr(dns, "MAX_RESOURCE_RECORDS", 2)
    assert len(parse_message(two).answers) == 2
    with pytest.raises(DNSDecodeError) as error:
        parse_message(packet(answers=(rr(), rr(), rr())))
    assert error.value.reason == "dns_record_work_limit"
    assert error.value.classification == "unsupported"
    # Question: two labels + root (3); owner: pointer + two labels + root (4).
    monkeypatch.setattr(dns, "MAX_MESSAGE_NAME_STEPS", 7)
    assert len(parse_message(packet(answers=(rr(),))).answers) == 1
    monkeypatch.setattr(dns, "MAX_MESSAGE_NAME_STEPS", 6)
    with pytest.raises(DNSDecodeError) as error:
        parse_message(packet(answers=(rr(),)))
    assert (error.value.classification, error.value.reason) == ("unsupported", "dns_message_work_limit")


def test_default_record_limit_rejects_declared_excess_before_reading():
    from pcap_analyzer.dns import MAX_MESSAGE_NAME_STEPS, MAX_RESOURCE_RECORDS
    assert (MAX_RESOURCE_RECORDS, MAX_MESSAGE_NAME_STEPS) == (1024, 16384)
    data = bytearray(packet())
    data[6:8] = struct.pack("!H", 1025)
    with pytest.raises(DNSDecodeError) as error:
        parse_message(bytes(data))
    assert error.value.reason == "dns_record_work_limit"


def test_existing_synthetic_messages_and_deterministic_results():
    from scapy.utils import RawPcapReader
    path = Path(__file__).resolve().parents[1] / "fixtures/baseline.pcap"
    with RawPcapReader(str(path)) as reader:
        records = list(reader)
    parsed = [parse_message(records[i][0][42:]) for i in (3, 4, 5, 6, 7, 8)]
    assert [len(p.answers) for p in parsed] == [0, 1, 0, 0, 0, 1]
    assert parsed[1].answers[0].rdata == b"\xcb\x00\x71\x07"
    assert parsed[1] == parse_message(records[4][0][42:])
