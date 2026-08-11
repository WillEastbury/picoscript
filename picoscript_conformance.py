"""Small machine-readable conformance runner for shared ABI fixtures."""

from dataclasses import dataclass
import json


@dataclass(frozen=True)
class Result:
    target: str
    status: str
    value: object = None


def run_fixture(name, fixture, targets):
    results = []
    for target, runner in targets.items():
        try:
            value = runner(fixture)
            results.append(Result(target, "ok", value))
        except NotImplementedError:
            results.append(Result(target, "unsupported"))
        except Exception as exc:  # keep failures machine-readable
            results.append(Result(target, "error", type(exc).__name__))
    supported = [r.value for r in results if r.status == "ok"]
    parity = bool(supported) and all(value == supported[0] for value in supported)
    return {"fixture": name, "parity": parity, "results": [r.__dict__ for r in results]}


def encode_report(report):
    return json.dumps(report, sort_keys=True, separators=(",", ":"))
