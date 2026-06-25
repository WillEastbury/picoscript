#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for deterministic PicoStore seed decks."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools import seed_decks  # noqa: E402

EXPECTED_COUNTS = {
    "schema": sum(len(fields) for fields in seed_decks.PACK_SCHEMAS.values()),
    "users": 8,
    "customers": 10,
    "products": 12,
    "orders": 16,
    "days_of_week": 7,
    "currencies": 6,
    "countries": 10,
    "order_statuses": 5,
}


def check(name: str, condition: bool) -> None:
    print(f"  [{'PASS' if condition else 'FAIL'}] {name}")
    if not condition:
        raise AssertionError(name)


def load_generated_decks():
    manifest = seed_decks.generate()
    index_path = seed_decks.DECK_DIR / "index.json"
    check("index.json written", index_path.exists())
    disk_manifest = json.loads(index_path.read_text(encoding="utf-8"))
    check("manifest round-trips", disk_manifest == manifest)

    stores = {}
    for deck in manifest["decks"]:
        path = seed_decks.DECK_DIR / deck["file"]
        check(f"{deck['file']} written", path.exists() and path.stat().st_size > 0)
        pack, store = seed_decks.load_deck(path)
        check(f"{pack} deck name", pack == deck["name"])
        stores[pack] = store
    return manifest, stores


def assert_counts(manifest, stores) -> None:
    manifest_counts = {deck["name"]: deck["row_count"] for deck in manifest["decks"]}
    check("expected deck list", manifest_counts == EXPECTED_COUNTS)
    for pack, expected in EXPECTED_COUNTS.items():
        check(f"{pack} row count", len(stores[pack].all(pack)) == expected)


def assert_schema_matches_data(manifest, stores) -> None:
    manifest_schemas = {
        deck["name"]: {field["name"]: field["type"] for field in deck["schema"]}
        for deck in manifest["decks"]
    }
    schema_records = [record for _id, record in stores["schema"].all("schema")]
    for pack, fields in manifest_schemas.items():
        records = stores[pack].all(pack)
        check(f"{pack} has records", len(records) > 0)
        expected_names = set(fields)
        for _id, record in records:
            check(f"{pack} card fields match schema", set(record) == expected_names)
            for name, field_type in fields.items():
                value = record[name]
                if field_type == "int":
                    check(f"{pack}.{name} is int", isinstance(value, int))
                elif field_type == "str":
                    check(f"{pack}.{name} is str", isinstance(value, str))
                else:
                    raise AssertionError(f"unknown field type {field_type}")

        deck_schema_records = [r for r in schema_records if r["pack_id"] == pack]
        check(f"{pack} schema card count", len(deck_schema_records) == len(fields))
        for ordinal, field_name in enumerate(fields, start=1):
            match = [r for r in deck_schema_records if r["field_name"] == field_name]
            check(f"{pack}.{field_name} schema card", len(match) == 1)
            check(f"{pack}.{field_name} schema type", match[0]["field_type"] == fields[field_name])
            check(f"{pack}.{field_name} schema ordinal", match[0]["ordinal"] == ordinal)


def assert_queries(stores) -> None:
    multi_qty_orders = stores["orders"].query("orders", "qty > 1")
    us_customers = stores["customers"].query("customers", "country = 'US'")
    check("orders query qty > 1", len(multi_qty_orders) == 12 and all(r["qty"] > 1 for _id, r in multi_qty_orders))
    check("customers query country = US", [r["name"] for _id, r in us_customers] == ["Northwind Traders", "Blue Yonder"])


def assert_references(stores) -> None:
    users = {r["id"] for _id, r in stores["users"].all("users")}
    customers = {r["id"] for _id, r in stores["customers"].all("customers")}
    products = {r["id"] for _id, r in stores["products"].all("products")}
    statuses = {r["code"] for _id, r in stores["order_statuses"].all("order_statuses")}
    currencies = {r["code"] for _id, r in stores["currencies"].all("currencies")}
    countries = {r["code"] for _id, r in stores["countries"].all("countries")}

    for _id, order in stores["orders"].all("orders"):
        check(f"order {order['id']} customer exists", order["customer_id"] in customers)
        check(f"order {order['id']} user exists", order["user_id"] in users)
        check(f"order {order['id']} product exists", order["product_id"] in products)
        check(f"order {order['id']} status exists", order["status"] in statuses)

    for _id, customer in stores["customers"].all("customers"):
        check(f"customer {customer['id']} country exists", customer["country"] in countries)
        check(f"customer {customer['id']} currency exists", customer["currency"] in currencies)

    for _id, country in stores["countries"].all("countries"):
        check(f"country {country['code']} currency exists", country["currency"] in currencies)


def main() -> None:
    os.chdir(ROOT)
    manifest, stores = load_generated_decks()
    assert_counts(manifest, stores)
    assert_schema_matches_data(manifest, stores)
    assert_queries(stores)
    assert_references(stores)
    print("\nseed deck tests passed")



def test_main():
    main()

if __name__ == "__main__":
    main()
