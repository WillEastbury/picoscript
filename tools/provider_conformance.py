"""Generate the target-by-target host provider conformance report."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from picoscript_lang import HOST_HOOK_CODES

MANIFEST = ROOT / "docs" / "PROVIDER_MANIFEST.json"
STATUSES = {"implemented", "provider-backed", "unsupported", "intentionally-unavailable"}


def load_manifest():
    with MANIFEST.open(encoding="utf-8") as stream:
        value = json.load(stream)
    if value.get("version") != 1:
        raise ValueError("unsupported provider manifest version")
    return value


def build_report():
    manifest = load_manifest()
    overrides = manifest["method_overrides"]
    families = manifest["families"]
    entries = []
    for namespace, method in sorted(HOST_HOOK_CODES):
        name = f"{namespace}.{method}"
        family_name = overrides.get(name, manifest["namespace_family"].get(namespace, "pure"))
        family = families[family_name]
        for target, target_info in sorted(manifest["targets"].items()):
            status = family["status"]
            if target == "null" and status == "provider-backed":
                status = "unsupported"
            if target == "pios" and status == "provider-backed":
                status = "provider-backed"
            entries.append({
                "target": target,
                "namespace": namespace,
                "method": method,
                "hook": HOST_HOOK_CODES[(namespace, method)],
                "status": status,
                "provider": target_info["provider"],
                "ownership": family["ownership"],
                "limits": family["limits"],
                "security": family["security"],
            })
    invalid = [entry for entry in entries if entry["status"] not in STATUSES]
    if invalid:
        raise ValueError(f"manifest contains invalid statuses: {invalid[:3]}")
    return {
        "manifest_version": manifest["version"],
        "targets": manifest["targets"],
        "entries": entries,
        "summary": {
            target: {
                status: sum(1 for entry in entries
                            if entry["target"] == target and entry["status"] == status)
                for status in sorted(STATUSES)
            }
            for target in sorted(manifest["targets"])
        },
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    args = parser.parse_args()
    report = build_report()
    if args.json:
        print(json.dumps(report, sort_keys=True, separators=(",", ":")))
    else:
        for target, counts in report["summary"].items():
            print(target + ": " + ", ".join(f"{key}={value}" for key, value in counts.items()))


if __name__ == "__main__":
    main()
