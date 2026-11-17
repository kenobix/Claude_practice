"""Validate available L2/L3/L4 bytes before extracting headers with Scapy.

Only header slices enter Scapy. Complete UDP port-53 payloads enter the bounded
DNS structure decoder; query/response matching is handled separately.
"""

import struct

from .dns import DNSDecodeError, parse_message
from .models import PacketRecord
from .offline import configure_offline

configure_offline()

from scapy.layers.inet import IP, TCP, UDP
from scapy.layers.l2 import Ether


def decode(data: bytes, *, index: int, timestamp_ns: int | None,
           caplen: int, wirelen: int, snaplen: int) -> PacketRecord:
    issues = {"malformed": [], "truncated": [], "unsupported": []}
    fields = {"protocol": "unknown"}
    if caplen > wirelen:
        issues["malformed"].append("caplen_exceeds_wirelen")
    if caplen > snaplen:
        issues["malformed"].append("caplen_exceeds_snaplen")
    if timestamp_ns is None:
        issues["malformed"].append("invalid_timestamp_fraction")
    if caplen < wirelen:
        issues["truncated"].append("capture_shorter_than_original")

    def finish():
        state, reason = "parsed", None
        for candidate in ("malformed", "truncated", "unsupported"):
            if issues[candidate]:
                state, reason = candidate, issues[candidate][0]
                break
        return PacketRecord(index, timestamp_ns, caplen, wirelen, state, reason, **fields)

    if len(data) < 14:
        issues["truncated"].append("ethernet_header_missing")
        return finish()
    ethernet = Ether(data[:14])
    if ethernet.type != 0x0800:
        fields["protocol"] = "non_ipv4"
        issues["unsupported"].append("ethernet_type_unsupported")
        return finish()
    ip = data[14:]
    if ip:
        if ip[0] >> 4 != 4:
            issues["malformed"].append("ipv4_version_invalid")
        if (ip[0] & 15) < 5:
            issues["malformed"].append("ipv4_ihl_invalid")
    if len(ip) < 20:
        issues["truncated"].append("ipv4_header_missing")
        return finish()
    ihl = (ip[0] & 15) * 4
    total = struct.unpack("!H", ip[2:4])[0]
    if total < ihl:
        issues["malformed"].append("ipv4_total_length_invalid")
    if len(ip) < ihl or len(ip) < total:
        issues["truncated"].append("ipv4_bytes_missing")
    if ip[0] >> 4 != 4 or ihl < 20 or total < ihl or len(ip) < ihl:
        return finish()
    header = IP(ip[:ihl])
    fields.update(src_ip=header.src, dst_ip=header.dst, ip_protocol=header.proto)
    if header.frag or header.flags.MF:
        fields["protocol"] = "ipv4_fragment"
        issues["unsupported"].append("ipv4_fragment")
        return finish()
    if header.proto not in (6, 17):
        fields["protocol"] = "ipv4_other"
        issues["unsupported"].append("ipv4_protocol_unsupported")
        return finish()
    fields["protocol"] = "tcp" if header.proto == 6 else "udp"
    declared_transport_len = total - ihl
    transport = ip[ihl:min(total, len(ip))]
    minimum = 20 if header.proto == 6 else 8
    if declared_transport_len < minimum:
        issues["malformed"].append("transport_length_invalid")
    if len(transport) < minimum:
        issues["truncated"].append("transport_header_missing")
    # Check every available length field even in a partially captured header.
    if header.proto == 6 and len(transport) >= 13:
        offset = (transport[12] >> 4) * 4
        if offset < 20 or offset > declared_transport_len:
            issues["malformed"].append("tcp_offset_invalid")
        elif offset > len(transport):
            issues["truncated"].append("tcp_options_missing")
    elif header.proto == 17 and len(transport) >= 6:
        udp_len = struct.unpack("!H", transport[4:6])[0]
        if udp_len < 8 or udp_len != declared_transport_len:
            issues["malformed"].append("udp_length_invalid")
    # An invalid timestamp does not invalidate otherwise complete flow headers.
    # Capture-length contradictions and malformed network/transport headers do.
    header_invalid = any(reason != "invalid_timestamp_fraction" for reason in issues["malformed"])
    if len(transport) >= minimum and not header_invalid:
        if header.proto == 6:
            offset = (transport[12] >> 4) * 4
            if offset <= len(transport):
                layer = TCP(transport[:offset])
                fields.update(src_port=layer.sport, dst_port=layer.dport,
                              flow_eligible=True, tcp_flags=int(layer.flags))
        else:
            layer = UDP(transport[:8])
            fields.update(src_port=layer.sport, dst_port=layer.dport, flow_eligible=True)
            # DNS body validity does not invalidate already verified flow headers.
            # Do not interpret capture shortages or metadata errors as bad DNS.
            if 53 in (layer.sport, layer.dport) and not any(issues.values()):
                try:
                    fields["dns_question"] = parse_message(transport[8:udp_len]).question
                except DNSDecodeError as error:
                    issues[error.classification].append(error.reason)
    return finish()
