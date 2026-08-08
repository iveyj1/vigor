#!/usr/bin/env python3
"""Intentional runtime errors for testing vigor quickfix diagnostics."""

import traceback


def divide_by_zero():
    return 10 / 0


def use_missing_name():
    return undefined_value + 1


def bad_conversion():
    return int("not a number")


for example in (divide_by_zero, use_missing_name, bad_conversion):
    try:
        example()
    except Exception:
        traceback.print_exc()

raise SystemExit(1)
