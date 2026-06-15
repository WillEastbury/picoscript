// picovm_run.js -- Node harness for picovm.js (parity testing vs Python/C VM).
// Reads: first line word count N, then N hex words. Prints STEPS/FAULT/STATUS/REGS/OUT.
const PicoVM = require("./picovm.js");

let data = "";
process.stdin.on("data", (d) => (data += d));
process.stdin.on("end", () => {
  const toks = data.split(/\s+/).filter((s) => s.length);
  const n = parseInt(toks[0], 10);
  const words = [];
  for (let i = 0; i < n; i++) words.push(parseInt(toks[1 + i], 16) >>> 0);
  const ms = process.env.PICOVM_MAX_STEPS;   // let tests drive the step budget
  const cps = process.env.PICOVM_CAPS;        // let tests restrict binding capabilities
  const seed = process.env.PICOVM_SEED;        // let tests pin Random.U32 nondeterminism
  const noalloc = process.env.PICOVM_NOALLOC;   // let tests enable hot-path no-alloc mode
  var opts = {};
  if (ms) opts.maxSteps = parseInt(ms, 10);
  if (cps) opts.caps = parseInt(cps, 0) >>> 0;
  if (seed) opts.seed = parseInt(seed, 0) >>> 0;
  if (noalloc && noalloc[0] !== "0") opts.noAlloc = true;
  const vm = new PicoVM(opts);
  let fault = 0;
  let faultPc = 0;
  let faultDetail = 0;
  try {
    vm.run(words);
  } catch (e) {
    // Map the VM trap to a typed fault code, mirroring the C runtime's ctx.fault.
    faultPc = (e && e.pc) || 0;
    faultDetail = (e && e.detail) || 0;
    var m = String(e && e.message || e);
    if (e && e.fault !== undefined) fault = e.fault;
    else if (m.indexOf("step budget") >= 0) fault = 1;
    else if (m.indexOf("bad opcode") >= 0) fault = 2;
    else if (m.indexOf("bad jump") >= 0 || m.indexOf("bad branch") >= 0 || m.indexOf("bad call") >= 0) fault = 3;
    else if (m.indexOf("template depth") >= 0) fault = 7;
    else if (m.indexOf("capability denied") >= 0) fault = 8;
    else fault = 99;
  }
  console.log("STEPS " + vm.steps);
  console.log("FAULT " + fault + " " + faultPc + " " + faultDetail);
  console.log("STATUS " + vm.httpStatus);
  console.log("ASSERT " + ((vm._asTotal || 0) >>> 0) + " " + ((vm._asFailed || 0) >>> 0));
  console.log("REGS " + Array.from(vm.regs).join(" "));
  console.log("OUT " + vm.output.map((b) => b.toString(16).padStart(2, "0")).join(" "));
});
