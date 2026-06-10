// picovm_run.js -- Node harness for picovm.js (parity testing vs Python/C VM).
// Reads: first line word count N, then N hex words. Prints STEPS/STATUS/REGS/OUT.
const PicoVM = require("./picovm.js");

let data = "";
process.stdin.on("data", (d) => (data += d));
process.stdin.on("end", () => {
  const toks = data.split(/\s+/).filter((s) => s.length);
  const n = parseInt(toks[0], 10);
  const words = [];
  for (let i = 0; i < n; i++) words.push(parseInt(toks[1 + i], 16) >>> 0);
  const vm = new PicoVM();
  vm.run(words);
  const out = [];
  console.log("STEPS " + vm.steps);
  console.log("STATUS " + vm.httpStatus);
  console.log("REGS " + Array.from(vm.regs).join(" "));
  console.log("OUT " + vm.output.map((b) => b.toString(16).padStart(2, "0")).join(" "));
});
