// Executes the REAL loadDictionary() from the built file, using the same
// browser APIs it uses in the page (atob, Blob, DecompressionStream, Response).
//
// This test exists because a data:-URL optimisation shipped without it and
// broke the page while every other check stayed green: the parity harness
// hand-rolls its own payload parsing and never calls the loader.
const fs = require('fs');
const html = fs.readFileSync('anagrind.html', 'utf8');
let js = html.split('<script>')[1].split('</script>')[0];
js = js.slice(0, js.indexOf(' * Tile tray'));
js = js.slice(0, js.lastIndexOf('/*')).replace('"use strict";', '');

const scope = new Function(js + `
  return {loadDictionary, WORD_GROUPS, PHRASE_GROUPS, FREQ, SYN, solve, parseEnum};
`)();

(async () => {
  const t0 = Date.now();
  let stats;
  try {
    stats = await scope.loadDictionary();
  } catch (err) {
    console.log('FAIL  loadDictionary threw:', err && err.name, '-', err && err.message);
    process.exit(1);
  }
  const ms = Date.now() - t0;
  const checks = [
    ['loadDictionary resolves', !!stats],
    ['all words counted', stats.words === 237658],
    ['length groups present', stats.groups === 33],
    ['phrase groups present', scope.PHRASE_GROUPS.size === 64],
    ['frequencies loaded', scope.FREQ.get('point') > 5],
    ['synonyms loaded', !!scope.SYN.get('want') && scope.SYN.get('want').has('need')],
    ['solves after load',
      scope.solve('on a train, up to its', scope.parseEnum('10,5'), false)[0].text
        === 'saturation point'],
    ['lazy group available', scope.WORD_GROUPS.has(15)],
  ];
  let fail = 0;
  for (const [name, ok] of checks) { if (!ok) fail++; console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}`); }
  console.log(`\n  real load path: ${ms} ms`);
  process.exit(fail ? 1 : 0);
})();
