import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from picoscript_lang import HOST_HOOK_CODES
from tools.provider_conformance import build_report


def test_provider_manifest_covers_every_hook_and_target():
    report = build_report()
    targets = set(report["targets"])
    assert targets == {"null", "python", "javascript", "native-posix", "native-windows", "pios"}
    assert len(report["entries"]) == len(HOST_HOOK_CODES) * len(targets)
    assert all(entry["status"] for entry in report["entries"])
    assert all(entry["provider"] for entry in report["entries"])


def test_null_target_never_silently_claims_provider_support():
    report = build_report()
    null_entries = [entry for entry in report["entries"] if entry["target"] == "null"]
    assert all(entry["status"] != "provider-backed" for entry in null_entries)
