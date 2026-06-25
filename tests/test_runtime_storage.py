import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from picoscript_runtime import PicoStoreHostStorageApi


def main():
    api = PicoStoreHostStorageApi()
    fails = 0

    def chk(name, got, want):
        nonlocal fails
        ok = got == want
        print(("[PASS] " if ok else "[FAIL] ") + name + ("" if ok else f"  got={got!r} want={want!r}"))
        if not ok:
            fails += 1

    i1 = api.add_card(7, {"qty": 42, "sku": "ABC", "status": 1})
    i2 = api.add_card(7, {"qty": 7, "sku": "XYZ", "status": 0})
    i3 = api.add_card(7, {"qty": 99, "sku": "ABC", "status": 1})
    chk("ids assigned", [i1, i2, i3], [1, 2, 3])
    chk("read #1", api.read_card(7, 1), {"qty": 42, "sku": "ABC", "status": 1})
    chk("schema set/get", (api.set_schema_for_pack(7, {"v": 1}), api.get_schema_for_pack(7))[1], {"v": 1})
    chk("query qty>40 AND status=1", [e[0] for e in api.query_card(7, "qty > 40 AND status = 1")], [1, 3])
    chk("query sku ~ AB", [e[0] for e in api.query_card(7, "sku ~ AB")], [1, 3])
    chk("patch", api.patch_card(7, 2, {"status": 5}), True)
    chk("patched read", api.read_card(7, 2)["status"], 5)
    chk("delete", api.delete_card(7, 2), True)
    chk("read deleted -> None", api.read_card(7, 2), None)
    chk("pack isolation (pack 9 empty)", api.query_card(9, "qty > 0"), [])

    print(f"\n{'RUNTIME STORAGE OK' if fails == 0 else 'RUNTIME STORAGE FAILED ' + str(fails)}")
    assert fails == 0, f"{fails} test(s) failed"


def test_main():
    main()


if __name__ == "__main__":
    main()
