"""File-order DNS reporting, with whole-file timing invalidation."""

from ipaddress import IPv4Address

from .dns import DNSName, observation
from .dns_windows import DNSWindowCollector
from .models import InputError

DNS_STATES = ("matched", "ambiguous", "additional_response", "unmatched_response",
              "no_response_observed", "window_incomplete")


class DNSReport:
    def __init__(self, max_keys):
        self._windows = DNSWindowCollector(max_keys)
        self._events = []
        self._last_ns = None
        self._disabled = False
        self.query_count = 0
        self.response_count = 0
        self.unassigned_indices = []

    def add(self, record):
        """Count eligible observations, then match; return first-regression flag.

        A DNS limit failure still counts the triggering observation and lists
        its unassigned evidence. The caller must account for flow bytes before
        propagating the failure. Non-DNS valid timestamps advance the window.
        """
        item = observation(record)
        if item:
            if item.is_response:
                self.response_count += 1
            else:
                self.query_count += 1
        ns = record.timestamp_ns
        if ns is None:
            return False
        first_regression = not self._disabled and self._last_ns is not None and ns < self._last_ns
        self._last_ns = ns
        if first_regression:
            self._disabled = True
            self._windows = None
            self._events.clear()
        if self._disabled:
            return first_regression
        try:
            self._events.extend(self._windows.add(item) if item else self._windows.advance(ns))
        except InputError:
            self.unassigned_indices.append(record.packet_index)
            raise
        return False

    def finish(self, complete):
        if not self._disabled:
            self._events.extend(self._windows.finish(self._last_ns if self._last_ns is not None else 0))
        # Evidence order is stable across window-closing batches and end flush.
        events = sorted(self._events, key=lambda e: e.response_indices[0]
                        if e.status == "additional_response" else
                        (e.query_indices or e.response_indices)[0])
        counts = dict.fromkeys(DNS_STATES, 0) if not self._disabled else None
        output = []
        for event in events:
            counts[event.status] += 1
            key = event.key
            output.append({
                "status": event.status,
                "key": {"client": {"ip": str(IPv4Address(key.client[0])), "port": key.client[1]},
                        "server": {"ip": str(IPv4Address(key.server[0])), "port": key.server[1]},
                        "id": key.dns_id, "labels_hex": [label.hex() for label in key.labels],
                        "name": DNSName(key.labels, 0).display, "qtype": key.qtype, "qclass": key.qclass},
                # Additional responses link to the primary query event rather
                # than serializing its potentially large query list again.
                "query_group": event.query_indices[0] if event.query_indices else None,
                "query_indices": list(event.query_indices) if event.status != "additional_response" else [],
                "response_indices": list(event.response_indices),
                "response_time_ns": event.response_time_ns,
                "response_time_ms": event.response_time_ns / 1_000_000 if event.response_time_ns is not None else None,
                "rcode": event.rcode, "tc": event.tc,
            })
        return {"status": "complete" if complete else "partial",
                "time_analysis": "disabled_timestamp_regression" if self._disabled else "enabled",
                "query_count": self.query_count, "response_count": self.response_count,
                "unassigned_indices": self.unassigned_indices,
                "event_counts": counts, "events": output}
