"""Hand-defined window boundaries, ambiguity and safety expectations."""

from dataclasses import replace

import pytest

from pcap_analyzer.dns import DNSKey, DNSObservation, observation
from pcap_analyzer.dns_windows import DNSWindowCollector, MAX_DNS_KEYS, WINDOW_NS
from pcap_analyzer.models import Config, InputError
from pcap_analyzer.reader import PcapSource
from test_phase1 import FIXTURES


KEY = DNSKey((b"\xc0\x00\x02\x01", 53000), (b"\xc6\x33\x64\x35", 53),
             1, (b"example", b"test", b""), 1, 1)


def item(index, ns, *, response=False, key=KEY, rcode=0, tc=False):
    return DNSObservation(key, index, ns, response, rcode, tc)


def brief(events):
    return [(e.status, e.query_indices, e.response_indices, e.response_time_ns, e.rcode, e.tc)
            for e in events]


@pytest.mark.parametrize("offset,expected", [(0, 0), (25_000_000, 25_000_000),
                                            (4_999_999_999, 4_999_999_999), (5_000_000_000, 5_000_000_000)])
def test_unique_response_in_closed_window(offset, expected):
    c = DNSWindowCollector()
    assert c.add(item(1, 100)) == ()
    assert c.add(item(2, 100 + offset, response=True)) == ()
    assert c.active_key_count == 1  # Completed keys remain until the deadline.
    assert brief(c.finish(100 + offset)) == [("matched", (1,), (2,), expected, 0, False)]


@pytest.mark.parametrize("end,status", [(4_999_999_999, "window_incomplete"),
                                        (5_000_000_000, "no_response_observed"),
                                        (5_000_000_001, "no_response_observed")])
def test_no_response_depends_on_observed_end(end, status):
    c = DNSWindowCollector()
    c.add(item(1, 0))
    assert brief(c.finish(end)) == [(status, (1,), (), None, None, None)]
    assert c.active_key_count == 0


def test_late_response_never_repairs_expired_query():
    c = DNSWindowCollector()
    c.add(item(1, 0))
    assert brief(c.add(item(2, 5_000_000_001, response=True))) == [
        ("no_response_observed", (1,), (), None, None, None),
        ("unmatched_response", (), (2,), None, 0, False)]
    assert c.finish(6_000_000_000) == ()


@pytest.mark.parametrize("after_response", [False, True])
def test_duplicate_query_or_completed_key_reuse_removes_latency(after_response):
    c = DNSWindowCollector()
    c.add(item(1, 0))
    c.add(item(2, 10, response=after_response))
    c.add(item(3, 20, response=not after_response))
    expected_queries = (1, 3) if after_response else (1, 2)
    expected_response = (2,) if after_response else (3,)
    assert brief(c.finish(30)) == [("ambiguous", expected_queries, expected_response, None, 0, False)]


def test_duplicates_without_response_are_ambiguous_at_capture_end():
    c = DNSWindowCollector()
    c.add(item(1, 0))
    c.add(item(2, 1))
    assert brief(c.finish(2)) == [("ambiguous", (1, 2), (), None, None, None)]


def test_duplicate_does_not_extend_deadline_and_reuse_after_window_is_new():
    c = DNSWindowCollector()
    c.add(item(1, 0))
    c.add(item(2, 4_999_999_999))
    assert brief(c.add(item(3, 5_000_000_001))) == [("ambiguous", (1, 2), (), None, None, None)]
    c.add(item(4, 5_000_000_002, response=True))
    assert brief(c.finish(5_000_000_003)) == [("matched", (3,), (4,), 1, 0, False)]


def test_query_at_exact_deadline_still_makes_completed_key_ambiguous():
    c = DNSWindowCollector()
    c.add(item(1, 0))
    c.add(item(2, 1, response=True))
    c.add(item(3, 5_000_000_000))
    assert brief(c.advance(5_000_000_001)) == [("ambiguous", (1, 3), (2,), None, 0, False)]


@pytest.mark.parametrize("reuse", [False, True])
def test_additional_response_keeps_metadata_but_has_no_latency(reuse):
    c = DNSWindowCollector()
    for i in (item(1, 0), item(2, 25_000_000, response=True, rcode=3),
              item(3, 30_000_000, response=True, tc=True)):
        assert c.add(i) == ()
    if reuse:
        c.add(item(4, 40_000_000))
    q = (1, 4) if reuse else (1,)
    assert brief(c.finish(50_000_000)) == [
        ("ambiguous" if reuse else "matched", q, (2,), None if reuse else 25_000_000, 3, False),
        ("additional_response", q, (3,), None, 0, True)]


def test_orphan_response_does_not_create_history_or_match_future_query():
    c = DNSWindowCollector(max_keys=1)
    assert brief(c.add(item(1, 0, response=True))) == [("unmatched_response", (), (1,), None, 0, False)]
    assert c.active_key_count == 0
    c.add(item(2, 1))
    assert brief(c.finish(2)) == [("window_incomplete", (2,), (), None, None, None)]


def test_capacity_includes_completed_keys_and_preserves_existing_history():
    c = DNSWindowCollector(max_keys=1)
    c.add(item(1, 0))
    c.add(item(2, 1, response=True))
    c.add(item(3, 2))  # Existing keys are allowed even at capacity.
    with pytest.raises(InputError) as error:
        c.add(item(4, 3, key=replace(KEY, dns_id=2)))
    assert (error.value.code, error.value.packet_index) == ("E_LIMIT_DNS", 4)
    assert c.active_key_count == 1
    assert brief(c.finish(3)) == [("ambiguous", (1, 3), (2,), None, 0, False)]


def test_capacity_frees_only_after_closed_endpoint():
    c = DNSWindowCollector(max_keys=1)
    c.add(item(1, 0))
    with pytest.raises(InputError):
        c.add(item(2, 5_000_000_000, key=replace(KEY, dns_id=2)))
    events = c.add(item(3, 5_000_000_001, key=replace(KEY, dns_id=2)))
    assert brief(events) == [("no_response_observed", (1,), (), None, None, None)]
    assert c.active_key_count == 1


@pytest.mark.parametrize("limit", [0, -1, 100001, True, 1.5])
def test_invalid_key_limits(limit):
    with pytest.raises(ValueError):
        DNSWindowCollector(limit)


def test_default_limits_and_empty_capture():
    assert (WINDOW_NS, MAX_DNS_KEYS) == (5_000_000_000, 100_000)
    assert DNSWindowCollector().finish(0) == ()


@pytest.mark.parametrize("bad_time", [-1, 0.5, True])
def test_invalid_timestamps_fail_before_mutation(bad_time):
    c = DNSWindowCollector()
    with pytest.raises(ValueError):
        c.add(item(1, bad_time))
    assert c.active_key_count == 0


def test_regression_and_repeated_indices_fail_before_mutation():
    c = DNSWindowCollector()
    c.add(item(1, 100))
    for invalid in (item(2, 99), item(1, 101), item(0, 101)):
        with pytest.raises(ValueError):
            c.add(invalid)
    with pytest.raises(ValueError):
        c.finish(99)
    assert brief(c.finish(100)) == [("window_incomplete", (1,), (), None, None, None)]


def test_finish_is_terminal():
    c = DNSWindowCollector()
    c.finish(0)
    for operation in (lambda: c.add(item(1, 1)), lambda: c.advance(1), lambda: c.finish(1)):
        with pytest.raises(RuntimeError):
            operation()


def test_non_dns_advance_closes_keys_without_scanning_or_reopening_them():
    c = DNSWindowCollector()
    c.add(item(1, 0))
    assert c.advance(5_000_000_000) == ()
    assert c.active_key_count == 1
    assert brief(c.advance(5_000_000_001)) == [("no_response_observed", (1,), (), None, None, None)]
    assert c.active_key_count == 0
    assert c.advance(6_000_000_000) == ()


def test_equal_deadlines_and_batch_order_are_deterministic():
    def run():
        c = DNSWindowCollector()
        for index, ident in [(1, 3), (2, 1), (3, 2)]:
            c.add(item(index, 0, key=replace(KEY, dns_id=ident)))
        return c.advance(5_000_000_001)
    assert run() == run()
    assert [e.key.dns_id for e in run()] == [3, 1, 2]


def test_baseline_with_non_dns_capture_end_matches_hand_expected_events():
    def run():
        c, events, end = DNSWindowCollector(), [], 0
        with PcapSource(FIXTURES / "baseline.pcap", Config()) as source:
            for packet in source:
                end = packet.timestamp_ns
                observed = observation(packet)
                events.extend(c.add(observed) if observed else c.advance(end))
        events.extend(c.finish(end))
        return tuple(events)
    result = run()
    assert result == run()
    assert brief(result) == [("matched", (4,), (5,), 25_000_000, 0, False),
                             ("no_response_observed", (6,), (), None, None, None),
                             ("ambiguous", (7, 8), (9,), None, 0, False)]
