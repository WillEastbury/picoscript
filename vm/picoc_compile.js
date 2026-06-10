// picoc_compile.js -- Node harness: compile source via picoc.js, print hex words.
// Usage: node picoc_compile.js <c|basic>   (source on stdin)
const PicoCompile = require("./picoc.js");
const lang = process.argv[2] || "c";
let src = "";
process.stdin.on("data", (d) => (src += d));
process.stdin.on("end", () => {
  try {
    const r = PicoCompile.compile(src, lang);
    console.log(r.words.map((w) => (w >>> 0).toString(16).padStart(8, "0")).join("\n"));
  } catch (e) {
    console.error("COMPILE_ERROR " + e.message);
    process.exit(1);
  }
});
