from picoscript_conformance import encode_report, run_fixture


def test_conformance_reports_parity_and_explicit_unsupported():
    report = run_fixture("span", b"abc", {
        "python": lambda value: value.hex(),
        "js": lambda value: value.hex(),
        "c": lambda value: value.hex(),
        "pios": lambda value: (_ for _ in ()).throw(NotImplementedError()),
    })
    assert report["parity"]
    assert [r["status"] for r in report["results"]][-1] == "unsupported"
    assert "\"parity\":true" in encode_report(report)


def test_conformance_detects_divergence():
    report = run_fixture("bad", 1, {"a": lambda x: x, "b": lambda x: x + 1})
    assert not report["parity"]
