// Full GUI roundtrip test: for each of 30 guide constructs, for each source language
// that has a pre-built example, translate to all 7 languages, compile each, run each,
// and verify all produce identical output. Tests in the actual generated index.html.

const { chromium } = require('playwright');
const path = require('path');

const LANGS = ['c', 'basic', 'python', 'english', 'cobol', 'report', 'functional'];

(async () => {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage();
  const url = 'file:///' + path.resolve('docs/index.html').replace(/\\/g, '/');
  await page.goto(url, { waitUntil: 'networkidle', timeout: 30000 });

  const cardCount = await page.evaluate(() => DATA.length);
  console.log('Testing ' + cardCount + ' constructs x 7 languages = ' + (cardCount * 7) + ' cells\n');

  let pass = 0, fail = 0, skip = 0;
  const failures = [];

  for (let i = 0; i < cardCount; i++) {
    const title = await page.evaluate((idx) => DATA[idx].title, i);
    const row = [];

    // Get reference output from a language that has a pre-built example
    const refResult = await page.evaluate((idx) => {
      var d = DATA[idx];
      var refLang = d.c ? 'c' : (d.basic ? 'basic' : (d.python ? 'python' : null));
      if (!refLang || !d[refLang]) return { refLang: null };
      try {
        var vm = new PicoVM();
        vm.run(d[refLang].words.map(function(w) { return parseInt(w, 16) >>> 0; }));
        return { refLang: refLang, refOut: JSON.stringify(vm.outputInts()) };
      } catch (e) {
        return { refLang: refLang, err: e.message };
      }
    }, i);

    if (!refResult.refLang) {
      for (let j = 0; j < LANGS.length; j++) row.push('SKIP');
      skip += LANGS.length;
      console.log(String(i + 1).padStart(3) + '. ' + title.padEnd(42) + row.join(' '));
      continue;
    }

    for (const lang of LANGS) {
      const result = await page.evaluate(({ idx, lang, refLang, refOut }) => {
        var d = DATA[idx];
        var src;
        // Get source: pre-built or translated
        if (d[lang]) {
          src = d[lang].src;
        } else {
          try {
            src = PicoCompile.translate(d[refLang].src, refLang, lang);
          } catch (e) {
            return { status: 'XLATE', err: e.message.substring(0, 40) };
          }
        }
        if (!src || !src.trim()) return { status: 'EMPTY' };

        // Compile in target language
        var words;
        try {
          var r = PicoCompile.compile(src, lang);
          words = r.words;
        } catch (e) {
          return { status: 'COMP', err: e.message.substring(0, 40), src: src.substring(0, 60) };
        }

        // Run and compare output
        try {
          var vm = new PicoVM();
          vm.run(words);
          var out = JSON.stringify(vm.outputInts());
          if (out === refOut) return { status: 'PASS' };
          return { status: 'OUT', expected: refOut, got: out };
        } catch (e) {
          return { status: 'RUN', err: e.message.substring(0, 40) };
        }
      }, { idx: i, lang, refLang: refResult.refLang, refOut: refResult.refOut });

      if (result.status === 'PASS') {
        row.push('✓');
        pass++;
      } else {
        row.push(result.status);
        fail++;
        failures.push({
          card: i + 1,
          title: title,
          lang: lang,
          ...result
        });
      }
    }

    const line = String(i + 1).padStart(3) + '. ' + title.padEnd(42) +
      row.map((r, j) => (r === '✓' ? r : r).padEnd(5)).join(' ');
    console.log(line);
  }

  console.log('\n' + '='.repeat(80));
  console.log('HEADER: ' + LANGS.map(l => l.substring(0, 4).padEnd(5)).join(' '));
  console.log('TOTAL: ' + pass + ' pass, ' + fail + ' fail, ' + skip + ' skip');

  if (failures.length) {
    console.log('\nFAILURES (' + failures.length + '):');
    failures.forEach(f => {
      console.log('  #' + f.card + ' ' + f.title + ' [' + f.lang + ']: ' + f.status +
        (f.err ? ' — ' + f.err : '') +
        (f.got ? ' expected=' + f.expected + ' got=' + f.got : ''));
      if (f.src) console.log('    src: ' + f.src);
    });
  }

  await browser.close();
  process.exit(failures.length > 0 ? 1 : 0);
})();
