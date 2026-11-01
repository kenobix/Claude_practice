"""Disposable child for signal diagnosis; never called by the product CLI."""
from __future__ import annotations

import faulthandler
import json
from pathlib import Path
import signal
import sys
import time
from unittest.mock import patch

from failure_diagnostics import FailureTrace


def main():
    root, plan_name, ledger_name, url, directory, mode = sys.argv[1:]
    trace = FailureTrace(Path(directory), "child")
    trace.emit("child_started", mode=mode)
    stacks = (Path(directory) / "child-stacks.txt").open("x")
    faulthandler.enable(file=stacks, all_threads=True)
    from bike_mlops import capital_operational_ledger as ledger
    from bike_mlops.capital_request_journal import RequestJournal
    from bike_mlops.capital_measurement_runner import _termination_as_interrupt

    original_write = ledger._write_ledger
    original_interrupt = ledger.MeasurementAttempt._interrupt_without_report

    def write(path, value):
        state = value["records"][-1]["status"]
        trace.emit("ledger_write_enter", state=state)
        original_write(path, value)
        trace.emit("ledger_write_returned", state=state)

    def interrupt(attempt):
        trace.emit("ledger_interrupt_enter")
        if mode == "stall_ledger":
            trace.emit("injected_ledger_stall")
            time.sleep(30)
        original_interrupt(attempt)
        trace.emit("ledger_interrupt_returned")

    code = 0
    try:
        with (patch.object(ledger, "PROJECT_ROOT", Path(root)), patch.object(ledger, "_write_ledger", write),
              patch.object(ledger.MeasurementAttempt, "_interrupt_without_report", interrupt),
              _termination_as_interrupt()):
            handler = signal.getsignal(signal.SIGTERM)

            def stop(signum, frame):
                trace.emit("signal_received", signal=signum, frame=frame.f_code.co_name)
                try:
                    handler(signum, frame)
                except BaseException as error:
                    trace.emit("signal_handler_raised", error_type=type(error).__name__)
                    raise

            signal.signal(signal.SIGTERM, stop)
            trace.emit("handler_installed")
            with ledger.measurement_attempt(Path(root) / plan_name, ledger_path=Path(ledger_name),
                                            lock_dir=Path(root) / "child-locks") as attempt:
                plan = json.loads((Path(root) / plan_name).read_text())
                journal = RequestJournal(Path(root) / (attempt.attempt_id + ".jsonl"), attempt, plan)
                try:
                    attempt.mark_requests_started()
                    journal.sending(journal.schedule[0])
                    trace.emit("send_intent_persisted", attempt_id=attempt.attempt_id)
                    print(attempt.attempt_id, flush=True)
                    import httpx

                    class Client(httpx.Client):
                        def __exit__(self, *args):
                            trace.emit("http_client_exit_enter")
                            result = super().__exit__(*args)
                            trace.emit("http_client_exit_returned")
                            return result

                    # This watchdog only writes stacks; it never ends the process or extends the deadline.
                    faulthandler.dump_traceback_later(0.25 if mode == "stall_ledger" else 5, file=stacks)
                    trace.emit("http_send_enter")
                    with Client(timeout=30, trust_env=False) as client:
                        client.post(url, json=journal.schedule[0])
                    trace.emit("http_send_returned")
                finally:
                    journal.close()
    except KeyboardInterrupt:
        code = 130
        trace.emit("child_interrupt_unwound")
    except BaseException as error:
        code = 1
        trace.emit("child_raised", error_type=type(error).__name__)
        raise
    finally:
        faulthandler.cancel_dump_traceback_later()
        trace.emit("child_exit", code=code)
        faulthandler.disable()
        stacks.close()
    return code


if __name__ == "__main__":
    raise SystemExit(main())
