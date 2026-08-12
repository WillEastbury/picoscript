// picostore_check.js -- Node harness: serialize records + run queries, emit JSON.
// Reads {records:[...], queries:[...]} from stdin; prints {hexes, results}.
const SER = require("./picoserializer.js");
const STORE = require("./picostore.js");

let data = "";
process.stdin.on("data", (d) => (data += d));
process.stdin.on("end", () => {
  const inp = JSON.parse(data);
  const hexes = inp.records.map((r) => SER.toHex(SER.serializeCard(r)));
  const store = new STORE.PicoStore();
  const ids = inp.records.map((r) => store.create("p", r));
  // mutate: update id[1], patch id[2], delete none (keep deterministic)
  const results = inp.queries.map((q) =>
    store.query("p", q).map((e) => [e[0], e[1]])
  );
  const roundtrip = ids.map((id) => store.read("p", id));
  const contract = new STORE.PicoStore();
  const schema = { fields: [
    { id: 1, name: "qty", type: "INT32", required: true },
    { id: 2, name: "sku", type: "TEXT" }
  ] };
  const schemaInfo = contract.registerSchema(7, schema);
  const contractId = contract.create(7, { qty: 3, sku: "A" });
  process.stdout.write(JSON.stringify({
    hexes, results, roundtrip, ids,
    contract: { schema: schemaInfo, id: contractId, card: contract.read(7, contractId) }
  }));
});
