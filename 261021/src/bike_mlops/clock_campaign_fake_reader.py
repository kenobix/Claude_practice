"""Fixed synthetic host/external reader subprocess. No OS clock/source access."""
import sys
import signal
import time

from bike_mlops.clock_campaign_capture import FAULTS, decode, encode, fake_response


def main():
    # Only this fixture child: do not retain the parent's short startup mask.
    signal.pthread_sigmask(signal.SIG_UNBLOCK, {signal.SIGINT, signal.SIGTERM})
    if len(sys.argv) != 2 or sys.argv[1] not in FAULTS:
        return 2
    fault = sys.argv[1]
    for index, line in enumerate(sys.stdin.buffer):
        if len(line) > 16384:
            return 2
        request = decode(line)
        response = fake_response(request)
        if index == 20:
            if fault == "delay":
                time.sleep(60)  # Terminated/reaped by the fixture controller.
            if fault == "exit":
                return 3
            if fault == "nonce":
                response["echo"]["nonce"] = "0" * 32
            if fault == "sequence":
                response["echo"]["sequence"] += 1
            if fault == "trial":
                response["echo"]["trial"] = "0" * 32
            if fault == "frequency":
                response["qpc_frequency_hz"] += 1
            if fault == "reverse":
                response["utc_before_ns"] = 0
            if fault == "missing":
                response.pop("qpc_tick")
        payload = encode(response)
        if index == 20 and fault == "duplicate":
            payload += payload
        sys.stdout.buffer.write(payload)
        sys.stdout.buffer.flush()
    if fault == "trailing":
        sys.stdout.buffer.write(b'{"unsolicited":true}\n')
        sys.stdout.buffer.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
