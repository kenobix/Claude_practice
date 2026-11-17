"""Role/key expectations from synthetic wire fields, without a DNS matcher."""

import struct
from dataclasses import replace

import pytest

from pcap_analyzer.dns import DNSKey, observation
from pcap_analyzer.packet import decode
from pcap_analyzer.reader import PcapSource
from pcap_analyzer.models import Config
from test_dns import message
from test_flows import frame
from test_phase1 import FIXTURES


def record(*, response=False, name=b"\x07Example\x04TEST\x00", dns_id=123,
           qtype=1, qclass=1, flags=None, src=None, dst=None, sport=None, dport=None,
           index=1, timestamp=1700000000000000123):
    payload = message(name, flags=(0x8180 if response else 0x0100) if flags is None else flags,
                      qtype=qtype, qclass=qclass)
    payload = struct.pack("!H", dns_id) + payload[2:]
    data = frame(protocol=17, payload=payload,
                 src=src or ("192.0.2.10" if response else "192.0.2.2"),
                 dst=dst or ("192.0.2.2" if response else "192.0.2.10"),
                 sport=(53 if response else 53000) if sport is None else sport,
                 dport=(53000 if response else 53) if dport is None else dport)
    return decode(data, index=index, timestamp_ns=timestamp, caplen=len(data), wirelen=len(data), snaplen=65535)


def test_query_response_have_independent_expected_role_key_and_exact_time():
    query = observation(record(index=7))
    response = observation(record(response=True, index=8, timestamp=1700000000025000123))
    expected = DNSKey((b"\xc0\x00\x02\x02", 53000), (b"\xc0\x00\x02\x0a", 53),
                      123, (b"example", b"test", b""), 1, 1)
    assert query.key == response.key == expected
    assert hash(query.key) == hash(response.key)
    assert (query.packet_index, response.packet_index) == (7, 8)
    assert (query.timestamp_ns, response.timestamp_ns) == (1700000000000000123, 1700000000025000123)
    assert (query.is_response, response.is_response) == (False, True)


@pytest.mark.parametrize("change", [
    {"src": "192.0.2.3"}, {"dst": "192.0.2.11"}, {"sport": 53001},
    {"dns_id": 124}, {"name": b"\x03www\x04test\x00"}, {"qtype": 28}, {"qclass": 3},
])
def test_each_client_server_question_component_separates_keys(change):
    assert observation(record(**change)).key != observation(record()).key


@pytest.mark.parametrize("flags,rcode,tc", [(0x8180, 0, False), (0x8183, 3, False), (0x8380, 0, True)])
def test_response_metadata_does_not_change_key(flags, rcode, tc):
    item = observation(record(response=True, flags=flags))
    assert item.key == observation(record()).key
    assert (item.rcode, item.tc) == (rcode, tc)


def test_ascii_only_normalization_binary_labels_and_boundaries():
    assert observation(record()).key == observation(record(name=b"\x07example\x04test\x00")).key
    assert observation(record(name=b"\x02\xc4A\x00")).key != observation(record(name=b"\x02\xe4a\x00")).key
    assert observation(record(name=b"\x03a.b\x00")).key != observation(record(name=b"\x01a\x01b\x00")).key
    assert observation(record(name=b"\x00")).key.labels == (b"",)


@pytest.mark.parametrize("port", [0, 53, 65535])
def test_client_port_boundaries_and_both_ports_53(port):
    query = observation(record(sport=port))
    response = observation(record(response=True, dport=port))
    assert query.key == response.key
    assert query.key.client[1] == port
    assert query.key.server[1] == 53


def test_roles_are_not_sorted_like_flow_endpoints():
    query = observation(record(src="192.0.2.10", dst="192.0.2.2"))
    response = observation(record(response=True, src="192.0.2.2", dst="192.0.2.10"))
    assert query.key == response.key
    assert query.key.client[0] > query.key.server[0]
    # Reversed roles with port53 at both ends are different logical keys.
    forward = observation(record(sport=53))
    opposite = observation(record(response=True, dport=53, src="192.0.2.2", dst="192.0.2.10"))
    assert forward.key != opposite.key


@pytest.mark.parametrize("response,sport,dport", [(False, 53, 53000), (True, 53000, 53)])
def test_wrong_qr_port_direction_excluded_without_reclassification(response, sport, dport):
    packet = record(response=response, sport=sport, dport=dport)
    assert packet.classification == "parsed"
    assert packet.flow_eligible and packet.dns_question is not None
    assert observation(packet) is None


@pytest.mark.parametrize("change", [
    {"classification": "truncated"}, {"classification": "malformed"}, {"classification": "unsupported"},
    {"protocol": "tcp"}, {"ip_protocol": 6}, {"flow_eligible": False}, {"timestamp_ns": None},
    {"dns_question": None}, {"src_ip": None}, {"dst_ip": None}, {"src_port": None}, {"dst_port": None},
])
def test_observation_rejects_ineligible_or_missing_fields(change):
    assert observation(replace(record(), **change)) is None


@pytest.mark.parametrize("payload", [b"", message(questions=0), message() + b"trailing"])
def test_failed_full_structure_check_never_retains_question(payload):
    data = frame(protocol=17, dport=53, payload=payload)
    packet = decode(data, index=1, timestamp_ns=1, caplen=len(data), wirelen=len(data), snaplen=65535)
    assert packet.dns_question is None
    assert observation(packet) is None


@pytest.mark.parametrize("case", ["truncated", "timestamp", "fragment", "tcp", "other_port"])
def test_decoder_does_not_attach_question_to_excluded_packets(case):
    data = frame(protocol=17, dport=53, payload=message())
    wirelen, timestamp = len(data), 1
    if case == "truncated":
        wirelen += 1
    elif case == "timestamp":
        timestamp = None
    elif case == "fragment":
        data = data[:20] + b"\x20\x00" + data[22:]
    elif case == "tcp":
        data = frame(dport=53, payload=message())
        wirelen = len(data)
    elif case == "other_port":
        data = frame(protocol=17, dport=5353, payload=message())
    packet = decode(data, index=1, timestamp_ns=timestamp, caplen=len(data), wirelen=wirelen, snaplen=65535)
    assert packet.dns_question is None
    assert observation(packet) is None


def test_baseline_questions_reach_observations_without_rr_or_raw_packet_retention():
    def read():
        with PcapSource(FIXTURES / "baseline.pcap", Config()) as source:
            return [item for packet in source if (item := observation(packet)) is not None]
    items = read()
    assert items == read()
    assert [i.packet_index for i in items] == [4, 5, 6, 7, 8, 9]
    assert [i.is_response for i in items] == [False, True, False, False, False, True]
    assert items[0].key == items[1].key
    assert items[3].key == items[4].key == items[5].key
    assert len({i.key for i in items}) == 3
    assert all(not hasattr(i, "answers") and not hasattr(i, "rdata") for i in items)


def test_identical_endpoints_and_zero_timestamp_are_supported():
    query = observation(record(src="192.0.2.2", dst="192.0.2.2", sport=53, timestamp=0))
    response = observation(record(response=True, src="192.0.2.2", dst="192.0.2.2", dport=53, timestamp=0))
    assert query.key == response.key
    assert query.timestamp_ns == response.timestamp_ns == 0
