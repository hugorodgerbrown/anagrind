// Runs the browser solver's logic in Node against the real payload and
// checks it agrees with the Python suite.
const fs = require('fs'), zlib = require('zlib');
const html = fs.readFileSync('anagrind.html', 'utf8');
let js = html.split('<script>')[1].split('</script>')[0];
js = js.slice(0, js.indexOf(' * Tile tray'));
js = js.slice(0, js.lastIndexOf('/*'));
js = js.replace(/const PAYLOAD = "[^"]*";/, '').replace('"use strict";', '');

const test = `
const blob0 = ZLIB.gunzipSync(FS.readFileSync('payload.gz')).toString();
const [wordBlob, freqBlob, phraseBlob, synBlob] = blob0.split('\\x1e');
const A = "0123456789abcdefghijklmnopqrstuvwxyz";
const freqGroups = freqBlob.split('\\x1d');
let nWords = 0;
wordBlob.split('\\x1d').forEach((group, g) => {
  const cut = group.indexOf('\\n');
  WORD_GROUPS.set(+group.slice(0, cut), group.slice(cut + 1));
  const words = group.slice(cut + 1).split('\\n'), freqs = freqGroups[g];
  for (let i = 0; i < words.length; i++) FREQ.set(words[i], A.indexOf(freqs[i]) / 4);
  nWords += words.length;
});
for (const group of phraseBlob.split('\\x1d')) {
  const cut = group.indexOf('\\n');
  PHRASE_GROUPS.set(+group.slice(0, cut), group.slice(cut + 1));
}
for (const line of (synBlob || '').split('\\n')) {
  if (!line) continue;
  const [w, ...rest] = line.split(' ');
  SYN.set(w, new Set(rest));
}
console.log(\`loaded \${nWords.toLocaleString()} words in \${WORD_GROUPS.size} groups, \${PHRASE_GROUPS.size} phrase groups\`);

const cases = [
  ['on a train, up to its', '10,5', 'saturation point', 0, false],
  ['no more stars', '11', 'astronomers', 0, false],
  ['dirty room', '9', 'dormitory', 0, false],
  ['a rope ends it', '11', 'desperation', 0, false],
  ['voices rant on', '12', 'conversation', 0, false],
  ['out take', '4-3', 'take-out', 0, false],
  ['out take', '4,3', 'take out', 0, false],
  ['the eyes', '4,3', 'they see', 2, true],
  ['point of no return', '5,2,2,6', 'point of no return', 0, false],
];
let fail = 0;
for (const [f, e, want, wantBand, all] of cases) {
  const t0 = Date.now();
  const r = solve(f, parseEnum(e), all);
  const ok = r.length && r[0].text === want && r[0].band === wantBand;
  if (!ok) fail++;
  console.log(\`\${ok ? 'PASS' : 'FAIL'}  \${JSON.stringify(f)} (\${e}) -> \${r.length ? r[0].text : '(none)'} band \${r.length ? r[0].band : '-'}  \${Date.now() - t0}ms\`);
}
const bands = solve('a rope ends it', parseEnum('11'), false).map(a => a.band);
const mono = JSON.stringify(bands) === JSON.stringify([...bands].sort());
console.log(\`\${mono ? 'PASS' : 'FAIL'}  bands monotonic\`);
const esp = solve('a rope ends it', parseEnum('11'), false).find(a => a.text === 'esperantido');
console.log(\`\${esp && esp.band === 1 ? 'PASS' : 'FAIL'}  esperantido banded unranked\`);
if (!mono || !esp || esp.band !== 1) fail++;
// diagnostics parity
const sw = wordSwaps('want top line', parseEnum('11'));
const okSwap = sw.length && sw[0].confident && sw[0].answers[0].text === 'needlepoint';
console.log(\`\${okSwap ? 'PASS' : 'FAIL'}  wordSwaps -> \${sw.length ? sw[0].detail + ' = ' + sw[0].answers[0].text : '(none)'}\`);
const conf = wordSwaps('want top line', parseEnum('11'), 25).filter(s => s.confident);
console.log(\`\${conf.length === 1 ? 'PASS' : 'FAIL'}  exactly one confident swap (\${conf.length})\`);
const nm = letterNearMisses('no more star', parseEnum('11'));
const okNm = nm.some(s => s.answers[0].text === 'astronomers');
console.log(\`\${okNm ? 'PASS' : 'FAIL'}  letterNearMisses finds a dropped letter\`);
const sh = alternativeShapes('on a train, up to its');
const okSh = sh.some(s => s.enumeration === '10,5');
console.log(\`\${okSh ? 'PASS' : 'FAIL'}  alternativeShapes finds 10,5\`);
if (!okSwap || conf.length !== 1 || !okNm || !okSh) fail++;
PROC.exit(fail ? 1 : 0);
`;
eval(js + test.replace(/ZLIB/g, 'require("zlib")').replace(/FS/g, 'require("fs")').replace(/PROC/g, 'process'));
