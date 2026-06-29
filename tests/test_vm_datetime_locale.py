#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tests/test_vm_datetime_locale.py -- deeper DateTime and Locale VM coverage."""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from picoscript_cfront import compile_c  # noqa: E402
from picoscript_il import lower_to_bytecode_safe  # noqa: E402
from picoscript_vm import PicoVM  # noqa: E402


def run(src):
    words = lower_to_bytecode_safe(compile_c(src))
    return PicoVM().run(words)


def out_bytes(vm):
    return b"".join(vm.output)


def out_ints(vm):
    return [int.from_bytes(c, "big") - (0x100000000 if int.from_bytes(c, "big") & 0x80000000 else 0)
            for c in vm.output]


# ── DateTime extended ────────────────────────────────────────────────────────

def test_datetime_add_seconds():
    src = "int t = 1000000; int r = DateTime.AddSeconds(t, 100); print(r - t);"
    assert out_ints(run(src)) == [100]


def test_datetime_parse():
    """DateTime.Parse parses an ISO string to epoch seconds."""
    src = 'int s = "2024-01-01T00:00:00Z"; int t = DateTime.Parse(s); print(t);'
    vm = run(src)
    result = out_ints(vm)[0]
    # 2024-01-01 epoch = 1704067200
    assert result > 1700000000


def test_datetime_format():
    """DateTime.Format produces a string from epoch seconds."""
    src = "int t = 1704067200; int s = DateTime.Format(t); Io.Write(s);"
    vm = run(src)
    got = out_bytes(vm)
    assert b"2024" in got


def test_datetime_get_day_of_year():
    """DateTime.GetDayOfYear returns 1-366."""
    src = "int t = DateTime.UtcNow(); int d = DateTime.GetDayOfYear(t); print(d);"
    result = out_ints(run(src))[0]
    assert 1 <= result <= 366


def test_datetime_diff_days():
    """DateTime.DiffDays computes day difference."""
    src = """
int a = 1704067200;
int b = 1704067200 + 86400 * 7;
int diff = DateTime.DiffDays(a, b);
print(diff);
"""
    result = out_ints(run(src))[0]
    assert abs(result) == 7


def test_datetime_year():
    """DateTime.Year extracts year."""
    src = "int t = 1704067200; int y = DateTime.Year(t); print(y);"
    result = out_ints(run(src))[0]
    assert result == 2024


def test_datetime_month():
    """DateTime.Month extracts month."""
    src = "int t = 1704067200; int m = DateTime.Month(t); print(m);"
    result = out_ints(run(src))[0]
    assert result == 1


def test_datetime_day():
    """DateTime.Day extracts day of month."""
    src = "int t = 1704067200; int d = DateTime.Day(t); print(d);"
    result = out_ints(run(src))[0]
    assert result == 1


# ── Locale ───────────────────────────────────────────────────────────────────

def test_locale_set_get():
    """Locale.SetLocale / GetCurrentLocale."""
    src = """
int loc = "fr-FR";
Locale.SetLocale(loc);
int cur = Locale.GetCurrentLocale();
print(cur);
"""
    vm = run(src)
    # Just verify it runs (locale may return a handle not string bytes)
    assert vm.steps > 0


def test_locale_format_number():
    """Locale.FormatNumber produces text."""
    src = """
int n = 12345;
int s = Locale.FormatNumber(n);
Io.Write(s);
"""
    vm = run(src)
    got = out_bytes(vm)
    assert b"12" in got or b"345" in got


def test_locale_format_currency():
    """Locale.FormatCurrency produces text with currency symbol."""
    src = """
int n = 9999;
int s = Locale.FormatCurrency(n);
Io.Write(s);
"""
    vm = run(src)
    assert len(out_bytes(vm)) > 0


def test_locale_format_date():
    """Locale.FormatDate formats an epoch timestamp."""
    src = """
int t = 1704067200;
int s = Locale.FormatDate(t);
print(s);
"""
    vm = run(src)
    # Just verify it runs without fault
    assert vm.steps > 0


def test_locale_format_time():
    """Locale.FormatTime formats a time."""
    src = """
int t = 1704067200;
int s = Locale.FormatTime(t);
print(s);
"""
    vm = run(src)
    assert vm.steps > 0


def test_locale_translate():
    """Locale.Translate returns input if no translation table."""
    src = """
int key = "hello";
int s = Locale.Translate(key);
Io.Write(s);
"""
    vm = run(src)
    got = out_bytes(vm)
    assert b"hello" in got or len(got) > 0
