#!/usr/bin/env python3
"""Count leaf results from one go test -json -count=1 invocation."""

import argparse
import json
import sys


def count(events):
    tests = {}
    parents = set()
    package_failures = set()
    for line in events:
        event = json.loads(line)
        action = event["Action"]
        package = event.get("Package")
        name = event.get("Test")
        if not name:
            if action in {"fail", "build-fail"}:
                package_failures.add(package or event["ImportPath"])
            continue
        test = (package, name)
        tests.setdefault(test, None)
        if action in {"pass", "skip", "fail"}:
            if tests[test] is not None:
                raise ValueError(f"repeated result for {test!r}; use -count=1")
            tests[test] = action
        parts = name.split("/")
        for length in range(1, len(parts)):
            parents.add((package, "/".join(parts[:length])))

    result = {"pass": 0, "skip": 0, "fail": 0, "incomplete": 0}
    for test, action in tests.items():
        if test not in parents:
            result[action or "incomplete"] += 1
    result["package_failures"] = sorted(package_failures)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("log", type=argparse.FileType(encoding="utf-8"))
    args = parser.parse_args()
    with args.log as log:
        result = count(log)
    print(json.dumps(result, sort_keys=True))
    return bool(result["fail"] or result["incomplete"] or result["package_failures"])


if __name__ == "__main__":
    sys.exit(main())
