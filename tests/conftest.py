"""Shared pytest configuration for PicoScript tests."""
import inspect

import pytest


def pytest_addoption(parser):
    parser.addoption("--runslow", action="store_true", default=False,
                     help="run slow tests that build the C VM")


def pytest_configure(config):
    config.addinivalue_line("markers", "slow: tests that build the C VM")


def pytest_collection_modifyitems(config, items):
    """Skip slow tests (those using ziglang/build_c_vm) unless --runslow."""
    if config.getoption("--runslow"):
        return
    skip_slow = pytest.mark.skip(reason="need --runslow option to run")
    for item in items:
        src = inspect.getfile(item.module)
        try:
            content = open(src, encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        if "ziglang" in content or "build_c_vm" in content:
            item.add_marker(skip_slow)
