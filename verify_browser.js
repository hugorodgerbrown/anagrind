// Loads the built file in a real headless Chromium — bare, and again under a
// restrictive CSP that simulates a sandboxed viewer.
//
//   node verify_browser.js [file]
//
// This exists because a data:-URL loader shipped and broke the page while every
// Node check stayed green. Node's fetch happily loads data: URLs, and so does
// Chromium over file://; only a page with a CSP refuses them. That is the
// environment real users are in, so it is the environment that must be tested.
const fs = require('fs'), os = require('os'), path = require('path');
const PUPPETEER = '/home/claude/.npm-global/lib/node_modules/'
                + '@mermaid-js/mermaid-cli/node_modules/puppeteer';
const puppeteer = require(PUPPETEER);

const CSP = '<meta http-equiv="Content-Security-Policy" content="'
          + `default-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://fonts.gstatic.com">`;

// Font requests are blocked in this sandbox and are not the page's fault.
const IGNORABLE = /fonts\.(googleapis|gstatic)|Failed to load resource/;

async function check(browser, file, label){
  const page = await browser.newPage();
  const errors = [];
  page.on('pageerror', e => errors.push('pageerror: ' + e.message));
  page.on('console', m => {
    if (m.type() === 'error' && !IGNORABLE.test(m.text())) errors.push('console: ' + m.text());
  });

  const t0 = Date.now();
  await page.goto('file://' + file, {waitUntil: 'load'});
  let state;
  try {
    state = await page.waitForFunction(() => {
      const fatal = document.querySelector('.fatal');
      if (fatal) return {fatal: fatal.textContent.trim().slice(0, 120)};
      const first = document.querySelector('.answer .txt');
      return first ? {answer: first.textContent} : false;
    }, {timeout: 30000}).then(h => h.jsonValue());
  } catch { state = {timeout: true}; }
  const ms = Date.now() - t0;
  const tiles = await page.$$eval('.tile', els => els.length).catch(() => 0);

  const checks = [
    ['no JS errors', errors.length === 0],
    ['dictionary loads', !state.fatal && !state.timeout],
    ['solves the default clue', state.answer === 'saturation point'],
    ['tile tray rendered', tiles === 15],
  ];
  let fail = 0;
  console.log(`\n  ${label}`);
  for (const [name, ok] of checks) { if (!ok) fail++; console.log(`  ${ok ? 'PASS' : 'FAIL'}  ${name}`); }
  if (state.fatal) console.log('        page said: ' + state.fatal);
  if (state.timeout) console.log('        never became usable within 30s');
  for (const e of errors.slice(0, 2)) console.log('        ' + e.slice(0, 130));
  if (!fail) console.log(`        interactive in ${ms} ms`);
  await page.close();
  return fail;
}

(async () => {
  const source = path.resolve(process.argv[2] || 'anagrind.html');
  const sandboxed = path.join(os.tmpdir(), 'anagrind-csp.html');
  fs.writeFileSync(sandboxed,
    fs.readFileSync(source, 'utf8').replace('<meta charset="utf-8">', '<meta charset="utf-8">' + CSP));

  const browser = await puppeteer.launch({
    executablePath: process.env.CHROME || '/opt/pw-browsers/chromium-1194/chrome-linux/chrome',
    args: ['--no-sandbox', '--disable-dev-shm-usage'],
  });
  const fail = await check(browser, source, 'file:// (a downloaded copy)')
             + await check(browser, sandboxed, 'with CSP (a sandboxed viewer)');
  await browser.close();
  console.log(fail ? '\n  FAILED' : '\n  all browser checks passed');
  process.exit(fail ? 1 : 0);
})();
