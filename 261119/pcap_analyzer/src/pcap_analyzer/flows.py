"""Bidirectional endpoint aggregation; a flow is not a TCP connection."""

from ipaddress import IPv4Address

from .models import InputError, PacketRecord

TCP_FLAGS = "FSRPAUECN"


def flow_key(record: PacketRecord):
    """Return (protocol, lower endpoint, upper endpoint), plus a->b direction.

    Endpoints compare packed IPv4 bytes first and integer ports second. Equal
    endpoints deterministically use a->b. Only eligible records may enter here.
    """
    source = (IPv4Address(record.src_ip).packed, record.src_port)
    destination = (IPv4Address(record.dst_ip).packed, record.dst_port)
    a, b = sorted((source, destination))
    return (record.ip_protocol, a, b), source == a


def _direction():
    return {"packets": 0, "captured_bytes": 0, "original_bytes": 0, "packet_indices": []}


class FlowCollector:
    def __init__(self, max_flows: int):
        self.max_flows = max_flows
        self._flows = {}
        self.unattributed = _direction()

    @staticmethod
    def _add(direction: dict, record: PacketRecord):
        direction["packets"] += 1
        direction["captured_bytes"] += record.captured_len
        direction["original_bytes"] += record.original_len
        direction["packet_indices"].append(record.packet_index)

    def add(self, record: PacketRecord):
        if not record.flow_eligible:
            self._add(self.unattributed, record)
            return
        key, forward = flow_key(record)
        if key not in self._flows:
            if len(self._flows) >= self.max_flows:
                self._add(self.unattributed, record)
                raise InputError("E_LIMIT_FLOWS", "new flow exceeds max_flows; packet counted as unattributed", record.packet_index)
            protocol, a, b = key
            self._flows[key] = {
                "protocol": protocol,
                "a": {"ip": str(IPv4Address(a[0])), "port": a[1]},
                "b": {"ip": str(IPv4Address(b[0])), "port": b[1]},
                "a_to_b": _direction(), "b_to_a": _direction(),
                "tcp_flag_counts": dict.fromkeys(TCP_FLAGS, 0) if protocol == 6 else None,
            }
        flow = self._flows[key]
        self._add(flow["a_to_b" if forward else "b_to_a"], record)
        if flow["protocol"] == 6:
            for bit, flag in enumerate(TCP_FLAGS):
                if record.tcp_flags & (1 << bit):
                    flow["tcp_flag_counts"][flag] += 1

    def to_list(self):
        return [self._flows[key] for key in sorted(self._flows)]
