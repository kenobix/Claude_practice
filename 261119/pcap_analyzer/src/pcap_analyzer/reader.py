"""Strict classic pcap boundary checks around sequential RawPcapReader use."""

import hashlib
import os
import stat
import struct
from pathlib import Path

from scapy.error import Scapy_Exception
from scapy.utils import RawPcapReader

from .models import Config, InputError
from .packet import decode

MAGICS = {
    b"\xd4\xc3\xb2\xa1": ("<", False), b"\xa1\xb2\xc3\xd4": (">", False),
    b"\x4d\x3c\xb2\xa1": ("<", True), b"\xa1\xb2\x3c\x4d": (">", True),
}


class PcapSource:
    def __init__(self, path: str | Path, config: Config):
        self.path, self.config = path, config
        self.file = None

    def __enter__(self):
        try:
            # O_NONBLOCK prevents a FIFO open from hanging before fstat rejects it.
            descriptor = os.open(self.path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0))
            self.file = os.fdopen(descriptor, "rb")
            self.initial_stat = os.fstat(self.file.fileno())
            if not stat.S_ISREG(self.initial_stat.st_mode):
                raise InputError("E_INPUT", "input must be a regular file")
            self.size_bytes = self.initial_stat.st_size
            if self.size_bytes > self.config.max_input_bytes:
                raise InputError("E_LIMIT_BYTES", "input exceeds max_input_bytes")
            header = self.file.read(24)
            if header[:4] == b"\x0a\x0d\x0d\x0a" or header[:2] == b"\x1f\x8b":
                raise InputError("E_FORMAT_UNSUPPORTED", "pcapng and compressed input are unsupported; use uncompressed classic pcap")
            if len(header) != 24 or header[:4] not in MAGICS:
                raise InputError("E_FORMAT", "invalid or incomplete classic pcap header")
            self.endian, self.nano = MAGICS[header[:4]]
            major, minor, _, _, self.snaplen, linktype = struct.unpack(self.endian + "HHIIII", header[4:])
            if (major, minor) != (2, 4) or self.snaplen == 0:
                raise InputError("E_FORMAT", "expected pcap v2.4 with positive snaplen")
            if linktype != 1:
                raise InputError("E_LINKTYPE", "only Ethernet link type 1 is supported")
            # Bounded full-file hash also identifies inputs stopped at the record limit.
            self.file.seek(0)
            digest = hashlib.sha256()
            remaining = self.size_bytes
            while remaining:
                chunk = self.file.read(min(1024 * 1024, remaining))
                if not chunk:
                    raise InputError("E_INPUT_CHANGED", "input changed during hashing")
                digest.update(chunk)
                remaining -= len(chunk)
            self.sha256 = digest.hexdigest()
            self.check_unchanged()
            self.file.seek(0)
            self.reader = RawPcapReader(self.file)
            return self
        except (OSError, Scapy_Exception) as error:
            self.__exit__(None, None, None)
            raise InputError("E_INPUT", "could not open or read input") from error
        except Exception:
            self.__exit__(None, None, None)
            raise

    def check_unchanged(self):
        current = os.fstat(self.file.fileno())
        if any(getattr(current, field) != getattr(self.initial_stat, field)
               for field in ("st_size", "st_mtime_ns", "st_ctime_ns")):
            raise InputError("E_INPUT_CHANGED", "input changed during analysis")

    def __iter__(self):
        index = 1
        while self.file.tell() < self.size_bytes:
            position = self.file.tell()
            if index > self.config.max_records:
                raise InputError("E_LIMIT_RECORDS", "record count exceeds max_records", index)
            header = self.file.read(16)
            if len(header) != 16:
                raise InputError("E_RECORD_HEADER", f"record header has {len(header)} bytes; expected 16", index)
            sec, fraction, caplen, wirelen = struct.unpack(self.endian + "IIII", header)
            if caplen > wirelen:
                raise InputError("E_RECORD_LENGTH",
                                 f"record declares caplen={caplen} greater than wirelen={wirelen}", index)
            available = self.size_bytes - position - 16
            if caplen > available:
                raise InputError("E_RECORD_DATA", f"record declares caplen={caplen}; only {available} bytes available", index)
            if caplen > self.config.max_packet_bytes:
                raise InputError("E_LIMIT_PACKET_BYTES", "record exceeds max_packet_bytes", index)
            self.file.seek(position)
            try:
                # Pinned Scapy explicitly requires this API for metadata access.
                # Its default size=MTU would silently slice large records.
                data, meta = self.reader._read_packet(size=caplen)
            except (EOFError, Scapy_Exception, struct.error) as error:
                raise InputError("E_RECORD_DATA", "Scapy could not read complete record", index) from error
            if (meta.sec, meta.usec, meta.caplen, meta.wirelen) != (sec, fraction, caplen, wirelen) or len(data) != caplen:
                raise InputError("E_RECORD_DATA", "record changed or has a short body", index)
            scale = 1_000_000_000 if self.nano else 1_000_000
            timestamp = sec * 1_000_000_000 + fraction * (1 if self.nano else 1000) if fraction < scale else None
            yield decode(data, index=index, timestamp_ns=timestamp, caplen=caplen,
                         wirelen=wirelen, snaplen=self.snaplen)
            index += 1
        self.check_unchanged()

    def __exit__(self, *_):
        if self.file is not None:
            self.file.close()
