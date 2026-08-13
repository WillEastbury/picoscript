import json
import os
import subprocess

from picostore import PicoStore

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def python_fixture():
    store = PicoStore()
    store.create_pack("orders", max_card_bytes=128)
    store.register_schema("orders", {"fields": [
        {"id": 0, "name": "id", "type": "INT32", "required": True},
        {"id": 1, "name": "qty", "type": "INT32", "required": True},
    ]})
    first = store.create("orders", {"id": 1, "qty": 4})
    second = store.create("orders", {"id": 2, "qty": 8})
    store.update("orders", first, {"id": 1, "qty": 5})
    return {
        "ids": [first, second],
        "query": [[cid, rec] for cid, rec in store.query("orders", "qty >= 5")],
        "read": store.read("orders", first),
        "delete": store.delete("orders", second),
        "remaining": [cid for cid, _ in store.all("orders")],
    }


def test_python_javascript_storage_fixture_matches():
    script = """
const S = require('./vm/picostore.js');
const s = new S.PicoStore();
s.createPack('orders', 'orders', 128);
s.registerSchema('orders', {fields:[
  {id:0,name:'id',type:'INT32',required:true},
  {id:1,name:'qty',type:'INT32',required:true}
]});
const first=s.create('orders',{id:1,qty:4}), second=s.create('orders',{id:2,qty:8});
s.update('orders',first,{id:1,qty:5});
process.stdout.write(JSON.stringify({
 ids:[first,second],
 query:s.query('orders','qty >= 5'),
 read:s.read('orders',first),
 delete:s.delete('orders',second),
 remaining:s.all('orders').map(x=>x[0])
}));
"""
    result = subprocess.run(["node", "-e", script], cwd=ROOT,
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == python_fixture()
