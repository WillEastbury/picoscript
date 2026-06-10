#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Generate deterministic PicoStore seed decks for the demo card store.

Deck/schema convention:
  * A deck is one PicoStore pack persisted as a PSCDECK1 binary envelope.
  * The envelope contains the pack name and an ordered list of PSC1 PicoBinarySerializer
    card payloads generated through PicoStore.card_bytes_hex(). Loading replays the
    records into a fresh PicoStore pack, preserving deterministic card order/ids.
  * PicoStore cards are self-describing, but there is not yet an explicit schema-card
    primitive in the store. Until Storage.GetSchemaForPack/SetSchemaForPack are backed
    by a runtime schema store, schema is modeled as its own "schema" pack: one schema
    card per field definition with pack_id, field_name, field_type, required, ordinal.
  * field_type values are the PicoBinarySerializer primitive names: "int" and "str".
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Dict, Iterable, List, Tuple

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from picostore import PicoStore  # noqa: E402
from picoserializer import deserialize_card, from_hex  # noqa: E402

MAGIC = b"PSCDECK1"
DECK_DIR = ROOT / "examples" / "decks"

SCHEMA_PACK = "schema"
SCHEMA_FIELDS: List[Tuple[str, str]] = [
    ("pack_id", "str"),
    ("field_name", "str"),
    ("field_type", "str"),
    ("required", "int"),
    ("ordinal", "int"),
]

PACK_SCHEMAS: Dict[str, List[Tuple[str, str]]] = {
    SCHEMA_PACK: SCHEMA_FIELDS,
    "users": [
        ("id", "int"),
        ("name", "str"),
        ("email", "str"),
        ("role", "str"),
        ("active", "int"),
    ],
    "customers": [
        ("id", "int"),
        ("name", "str"),
        ("country", "str"),
        ("currency", "str"),
        ("tier", "str"),
    ],
    "products": [
        ("id", "int"),
        ("sku", "str"),
        ("name", "str"),
        ("category", "str"),
        ("price", "int"),
        ("stock", "int"),
    ],
    "orders": [
        ("id", "int"),
        ("customer_id", "int"),
        ("product_id", "int"),
        ("user_id", "int"),
        ("qty", "int"),
        ("total", "int"),
        ("status", "str"),
    ],
    "days_of_week": [
        ("id", "int"),
        ("name", "str"),
        ("short", "str"),
    ],
    "currencies": [
        ("code", "str"),
        ("symbol", "str"),
        ("name", "str"),
    ],
    "countries": [
        ("code", "str"),
        ("name", "str"),
        ("currency", "str"),
    ],
    "order_statuses": [
        ("code", "str"),
        ("name", "str"),
        ("sort_order", "int"),
    ],
}

USERS = [
    {"id": 1, "name": "Ada Lovelace", "email": "ada@example.com", "role": "admin", "active": 1},
    {"id": 2, "name": "Grace Hopper", "email": "grace@example.com", "role": "manager", "active": 1},
    {"id": 3, "name": "Katherine Johnson", "email": "katherine@example.com", "role": "seller", "active": 1},
    {"id": 4, "name": "Mary Jackson", "email": "mary@example.com", "role": "seller", "active": 1},
    {"id": 5, "name": "Dorothy Vaughan", "email": "dorothy@example.com", "role": "support", "active": 1},
    {"id": 6, "name": "Alan Turing", "email": "alan@example.com", "role": "seller", "active": 1},
    {"id": 7, "name": "Hedy Lamarr", "email": "hedy@example.com", "role": "support", "active": 0},
    {"id": 8, "name": "Tim Berners-Lee", "email": "tim@example.com", "role": "seller", "active": 1},
]

CUSTOMERS = [
    {"id": 1, "name": "Northwind Traders", "country": "US", "currency": "USD", "tier": "enterprise"},
    {"id": 2, "name": "Contoso Retail", "country": "GB", "currency": "GBP", "tier": "enterprise"},
    {"id": 3, "name": "Fabrikam Labs", "country": "DE", "currency": "EUR", "tier": "growth"},
    {"id": 4, "name": "Adventure Works", "country": "CA", "currency": "CAD", "tier": "growth"},
    {"id": 5, "name": "Tailspin Toys", "country": "AU", "currency": "AUD", "tier": "starter"},
    {"id": 6, "name": "Coho Winery", "country": "FR", "currency": "EUR", "tier": "growth"},
    {"id": 7, "name": "Wingtip Coffee", "country": "JP", "currency": "JPY", "tier": "starter"},
    {"id": 8, "name": "Blue Yonder", "country": "US", "currency": "USD", "tier": "enterprise"},
    {"id": 9, "name": "Litware Studio", "country": "IN", "currency": "USD", "tier": "growth"},
    {"id": 10, "name": "A. Datum Market", "country": "NL", "currency": "EUR", "tier": "starter"},
]

PRODUCTS = [
    {"id": 1, "sku": "PSC-START", "name": "Starter Script Pack", "category": "software", "price": 1900, "stock": 120},
    {"id": 2, "sku": "PSC-PRO", "name": "Pro Script Pack", "category": "software", "price": 4900, "stock": 80},
    {"id": 3, "sku": "CARD-BLANK", "name": "Blank Card Bundle", "category": "supplies", "price": 700, "stock": 400},
    {"id": 4, "sku": "CARD-ART", "name": "Illustrated Card Bundle", "category": "supplies", "price": 1200, "stock": 180},
    {"id": 5, "sku": "DECK-BOX", "name": "Walnut Deck Box", "category": "accessory", "price": 2500, "stock": 45},
    {"id": 6, "sku": "SLEEVE-CLR", "name": "Clear Sleeve Set", "category": "accessory", "price": 900, "stock": 260},
    {"id": 7, "sku": "SLEEVE-MAT", "name": "Matte Sleeve Set", "category": "accessory", "price": 1100, "stock": 210},
    {"id": 8, "sku": "TOKEN-MET", "name": "Metal Token Set", "category": "accessory", "price": 1800, "stock": 70},
    {"id": 9, "sku": "PLAYMAT", "name": "PicoScript Playmat", "category": "accessory", "price": 3200, "stock": 35},
    {"id": 10, "sku": "GUIDE", "name": "Pocket Strategy Guide", "category": "book", "price": 1500, "stock": 95},
    {"id": 11, "sku": "BOOSTER-A", "name": "Algorithm Booster", "category": "cards", "price": 600, "stock": 500},
    {"id": 12, "sku": "BOOSTER-B", "name": "Storage Booster", "category": "cards", "price": 600, "stock": 480},
]

DAYS_OF_WEEK = [
    {"id": 1, "name": "Monday", "short": "Mon"},
    {"id": 2, "name": "Tuesday", "short": "Tue"},
    {"id": 3, "name": "Wednesday", "short": "Wed"},
    {"id": 4, "name": "Thursday", "short": "Thu"},
    {"id": 5, "name": "Friday", "short": "Fri"},
    {"id": 6, "name": "Saturday", "short": "Sat"},
    {"id": 7, "name": "Sunday", "short": "Sun"},
]

CURRENCIES = [
    {"code": "USD", "symbol": "$", "name": "US Dollar"},
    {"code": "GBP", "symbol": "£", "name": "Pound Sterling"},
    {"code": "EUR", "symbol": "€", "name": "Euro"},
    {"code": "CAD", "symbol": "$", "name": "Canadian Dollar"},
    {"code": "AUD", "symbol": "$", "name": "Australian Dollar"},
    {"code": "JPY", "symbol": "¥", "name": "Japanese Yen"},
]

COUNTRIES = [
    {"code": "US", "name": "United States", "currency": "USD"},
    {"code": "GB", "name": "United Kingdom", "currency": "GBP"},
    {"code": "DE", "name": "Germany", "currency": "EUR"},
    {"code": "CA", "name": "Canada", "currency": "CAD"},
    {"code": "AU", "name": "Australia", "currency": "AUD"},
    {"code": "FR", "name": "France", "currency": "EUR"},
    {"code": "JP", "name": "Japan", "currency": "JPY"},
    {"code": "IN", "name": "India", "currency": "USD"},
    {"code": "NL", "name": "Netherlands", "currency": "EUR"},
    {"code": "IE", "name": "Ireland", "currency": "EUR"},
]

ORDER_STATUSES = [
    {"code": "draft", "name": "Draft", "sort_order": 1},
    {"code": "paid", "name": "Paid", "sort_order": 2},
    {"code": "packed", "name": "Packed", "sort_order": 3},
    {"code": "shipped", "name": "Shipped", "sort_order": 4},
    {"code": "cancelled", "name": "Cancelled", "sort_order": 5},
]

ORDERS = [
    {"id": 1001, "customer_id": 1, "product_id": 2, "user_id": 3, "qty": 2, "total": 9800, "status": "paid"},
    {"id": 1002, "customer_id": 2, "product_id": 5, "user_id": 4, "qty": 1, "total": 2500, "status": "packed"},
    {"id": 1003, "customer_id": 3, "product_id": 11, "user_id": 6, "qty": 6, "total": 3600, "status": "shipped"},
    {"id": 1004, "customer_id": 4, "product_id": 9, "user_id": 8, "qty": 1, "total": 3200, "status": "paid"},
    {"id": 1005, "customer_id": 5, "product_id": 3, "user_id": 3, "qty": 5, "total": 3500, "status": "draft"},
    {"id": 1006, "customer_id": 6, "product_id": 10, "user_id": 4, "qty": 2, "total": 3000, "status": "shipped"},
    {"id": 1007, "customer_id": 7, "product_id": 8, "user_id": 6, "qty": 3, "total": 5400, "status": "packed"},
    {"id": 1008, "customer_id": 8, "product_id": 1, "user_id": 8, "qty": 4, "total": 7600, "status": "paid"},
    {"id": 1009, "customer_id": 1, "product_id": 6, "user_id": 3, "qty": 10, "total": 9000, "status": "shipped"},
    {"id": 1010, "customer_id": 2, "product_id": 7, "user_id": 4, "qty": 8, "total": 8800, "status": "paid"},
    {"id": 1011, "customer_id": 3, "product_id": 12, "user_id": 6, "qty": 12, "total": 7200, "status": "packed"},
    {"id": 1012, "customer_id": 4, "product_id": 4, "user_id": 8, "qty": 2, "total": 2400, "status": "cancelled"},
    {"id": 1013, "customer_id": 5, "product_id": 2, "user_id": 3, "qty": 1, "total": 4900, "status": "draft"},
    {"id": 1014, "customer_id": 6, "product_id": 11, "user_id": 4, "qty": 7, "total": 4200, "status": "shipped"},
    {"id": 1015, "customer_id": 7, "product_id": 3, "user_id": 6, "qty": 9, "total": 6300, "status": "paid"},
    {"id": 1016, "customer_id": 8, "product_id": 9, "user_id": 8, "qty": 1, "total": 3200, "status": "packed"},
]

DATA_PACKS: Dict[str, List[dict]] = {
    "users": USERS,
    "customers": CUSTOMERS,
    "products": PRODUCTS,
    "orders": ORDERS,
    "days_of_week": DAYS_OF_WEEK,
    "currencies": CURRENCIES,
    "countries": COUNTRIES,
    "order_statuses": ORDER_STATUSES,
}

REFERENCES = [
    {"from": "orders.customer_id", "to": "customers.id"},
    {"from": "orders.product_id", "to": "products.id"},
    {"from": "orders.user_id", "to": "users.id"},
    {"from": "orders.status", "to": "order_statuses.code"},
    {"from": "customers.country", "to": "countries.code"},
    {"from": "customers.currency", "to": "currencies.code"},
    {"from": "countries.currency", "to": "currencies.code"},
]


def schema_rows() -> List[dict]:
    rows: List[dict] = []
    for pack, fields in PACK_SCHEMAS.items():
        for ordinal, (field_name, field_type) in enumerate(fields, start=1):
            rows.append({
                "pack_id": pack,
                "field_name": field_name,
                "field_type": field_type,
                "required": 1,
                "ordinal": ordinal,
            })
    return rows


def all_decks() -> Dict[str, List[dict]]:
    return {SCHEMA_PACK: schema_rows(), **DATA_PACKS}


def build_store(pack: str, rows: Iterable[dict]) -> PicoStore:
    store = PicoStore()
    for row in rows:
        store.create(pack, row)
    return store


def deck_path(pack: str, out_dir: Path = DECK_DIR) -> Path:
    return out_dir / f"{pack}.pscdeck"


def write_deck(pack: str, rows: List[dict], out_dir: Path = DECK_DIR) -> Path:
    store = build_store(pack, rows)
    pack_bytes = pack.encode("utf-8")
    payload = bytearray(MAGIC)
    payload += len(pack_bytes).to_bytes(2, "big")
    payload += pack_bytes
    payload += len(rows).to_bytes(4, "big")
    for card_id, _record in store.all(pack):
        card_bytes = from_hex(store.card_bytes_hex(pack, card_id))
        payload += len(card_bytes).to_bytes(4, "big")
        payload += card_bytes
    path = deck_path(pack, out_dir)
    path.write_bytes(bytes(payload))
    return path


def load_deck(path: os.PathLike[str] | str) -> Tuple[str, PicoStore]:
    data = Path(path).read_bytes()
    if not data.startswith(MAGIC):
        raise ValueError(f"{path}: bad deck magic")
    pos = len(MAGIC)
    pack_len = int.from_bytes(data[pos:pos + 2], "big"); pos += 2
    pack = data[pos:pos + pack_len].decode("utf-8"); pos += pack_len
    count = int.from_bytes(data[pos:pos + 4], "big"); pos += 4
    store = PicoStore()
    for _ in range(count):
        card_len = int.from_bytes(data[pos:pos + 4], "big"); pos += 4
        card = data[pos:pos + card_len]; pos += card_len
        store.create(pack, deserialize_card(card))
    if pos != len(data):
        raise ValueError(f"{path}: trailing bytes")
    return pack, store


def schema_for_manifest(pack: str) -> List[dict]:
    return [
        {"name": field_name, "type": field_type, "required": True, "ordinal": ordinal}
        for ordinal, (field_name, field_type) in enumerate(PACK_SCHEMAS[pack], start=1)
    ]


def manifest_for(decks: Dict[str, List[dict]]) -> dict:
    return {
        "format_version": 1,
        "schema_convention": (
            "PicoStore has self-describing PSC1 cards but no persisted schema-card primitive yet. "
            "The schema deck is a PicoStore pack named 'schema' with one card per field definition: "
            "pack_id, field_name, field_type, required, ordinal. field_type is 'int' or 'str'."
        ),
        "deck_file_format": {
            "magic": MAGIC.decode("ascii"),
            "layout": "magic[8], pack_name_len:u16, pack_name:utf8, card_count:u32, repeated(card_len:u32, PSC1 card bytes)",
            "card_encoding": "PicoBinarySerializer PSC1; fields sorted by UTF-8 name bytes for deterministic output",
        },
        "references": REFERENCES,
        "decks": [
            {
                "name": pack,
                "file": f"{pack}.pscdeck",
                "row_count": len(rows),
                "schema": schema_for_manifest(pack),
            }
            for pack, rows in decks.items()
        ],
    }


def generate(out_dir: Path = DECK_DIR) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    decks = all_decks()
    for pack, rows in decks.items():
        write_deck(pack, rows, out_dir)
    manifest = manifest_for(decks)
    (out_dir / "index.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=False, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return manifest


def main() -> None:
    manifest = generate()
    print(f"Wrote {len(manifest['decks'])} decks to {DECK_DIR}")
    for deck in manifest["decks"]:
        print(f"  {deck['name']}: {deck['row_count']} rows -> {deck['file']}")


if __name__ == "__main__":
    main()
