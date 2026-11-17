"""Bounded DNS windows for monotonic observations; independent of CLI output.

Events are final only when a window closes. This avoids publishing a latency
that a later query reuse within the same window would have to retract.
"""

import heapq
from dataclasses import dataclass, field

from .dns import DNSKey, DNSObservation
from .models import InputError

WINDOW_NS = 5_000_000_000
MAX_DNS_KEYS = 100_000


@dataclass(frozen=True)
class DNSEvent:
    key: DNSKey
    status: str
    query_indices: tuple[int, ...]
    response_indices: tuple[int, ...]
    response_time_ns: int | None
    rcode: int | None
    tc: bool | None


@dataclass
class _Window:
    first: DNSObservation
    queries: list[int] = field(default_factory=list)
    responses: list[DNSObservation] = field(default_factory=list)

    @property
    def deadline(self):
        return self.first.timestamp_ns + WINDOW_NS

    def events(self, end_ns):
        if len(self.queries) > 1:
            status = "ambiguous"
        elif self.responses:
            status = "matched"
        else:
            status = "no_response_observed" if end_ns >= self.deadline else "window_incomplete"
        first = self.responses[0] if self.responses else None
        latency = first.timestamp_ns - self.first.timestamp_ns if status == "matched" else None
        queries = tuple(self.queries)
        result = [DNSEvent(self.first.key, status, queries,
                           (first.packet_index,) if first else (), latency,
                           first.rcode if first else None, first.tc if first else None)]
        for response in self.responses[1:]:
            result.append(DNSEvent(self.first.key, "additional_response", queries,
                                   (response.packet_index,), None, response.rcode, response.tc))
        return result


def _ordered(events):
    # Root evidence, not hash iteration or wall-clock order, defines batch order.
    return tuple(sorted(events, key=lambda event: min(event.query_indices + event.response_indices)
                        if event.status != "additional_response" else event.response_indices[0]))


class DNSWindowCollector:
    """Consume decoder-derived observations in file order with nondecreasing ns.

    add/advance/finish return finalized immutable events; callers retain them.
    No event history is kept here. References within active windows are bounded
    by the reader's record budget. After a regression callers must discard ALL
    timing results for the file, including events already returned.
    """

    def __init__(self, max_keys=MAX_DNS_KEYS):
        if type(max_keys) is not int or not 1 <= max_keys <= MAX_DNS_KEYS:
            raise ValueError("max_keys must be an integer in 1..100000")
        self.max_keys = max_keys
        self._active = {}
        self._deadlines = []
        self._serial = 0
        self._last_ns = None
        self._last_index = 0
        self._finished = False

    @property
    def active_key_count(self):
        return len(self._active)

    def _check_time(self, timestamp_ns):
        if self._finished:
            raise RuntimeError("DNS windows already finished")
        if type(timestamp_ns) is not int or timestamp_ns < 0:
            raise ValueError("timestamp must be a nonnegative integer ns")
        if self._last_ns is not None and timestamp_ns < self._last_ns:
            raise ValueError("timestamps regress; discard all DNS timing for the file")

    def advance(self, timestamp_ns: int) -> tuple[DNSEvent, ...]:
        """Advance using any valid packet's time, retaining the closed endpoint."""
        self._check_time(timestamp_ns)
        events = []
        while self._deadlines and self._deadlines[0][0] < timestamp_ns:
            _, _, key = heapq.heappop(self._deadlines)
            events.extend(self._active.pop(key).events(timestamp_ns))
        self._last_ns = timestamp_ns
        return _ordered(events)

    def add(self, item: DNSObservation) -> tuple[DNSEvent, ...]:
        self._check_time(item.timestamp_ns)
        if type(item.packet_index) is not int or item.packet_index <= self._last_index:
            raise ValueError("packet indices must increase in file order")
        events = list(self.advance(item.timestamp_ns))
        group = self._active.get(item.key)
        if item.is_response:
            if group is None:
                events.append(DNSEvent(item.key, "unmatched_response", (), (item.packet_index,),
                                       None, item.rcode, item.tc))
            else:
                group.responses.append(item)
        else:
            if group is None:
                if len(self._active) >= self.max_keys:
                    raise InputError("E_LIMIT_DNS", "new DNS key exceeds max_keys", item.packet_index)
                group = _Window(item)
                self._active[item.key] = group
                self._serial += 1
                heapq.heappush(self._deadlines, (group.deadline, self._serial, item.key))
            group.queries.append(item.packet_index)
        self._last_index = item.packet_index
        return _ordered(events)

    def finish(self, end_ns: int) -> tuple[DNSEvent, ...]:
        """Finalize at the last observed valid packet time, including non-DNS."""
        self._check_time(end_ns)
        events = [event for group in self._active.values() for event in group.events(end_ns)]
        self._active.clear()
        self._deadlines.clear()
        self._last_ns = end_ns
        self._finished = True
        return _ordered(events)
