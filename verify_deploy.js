// Serves dist/ over HTTP in headless Chromium and checks the two things that
// only break in production:
//
//   1. it works with no signal once installed  (the point of the app)
//   2. a redeploy actually reaches an installed user
//
// (2) is the one that bites. A cache-first service worker will happily serve
// the old app forever, and you cannot fix it by deploying again — the fix is
// in the file the worker refuses to fetch. The cache name carries a hash of
// the built page, the page asks for an update on every launch, and it reloads
// once when a new worker takes over.
const {spawn} = require('child_process');
const fs = require('fs'), crypto = require('crypto'), path = require('path');
const puppeteer = require('/home/claude/.npm-global/lib/node_modules/'
                        + '@mermaid-js/mermaid-cli/node_modules/puppeteer');

const PORT = 8137;
const STAGE = path.join(require('os').tmpdir(), 'anagrind-deploy');
const wait = ms => new Promise(r => setTimeout(r, ms));

function redeploy(){
  // Exactly what build_dist.py does: change the page, restamp the cache name.
  const page = path.join(STAGE, 'index.html');
  fs.writeFileSync(page, fs.readFileSync(page, 'utf8')
    .replace('<div class="mark">anagrind</div>', '<div class="mark">anagrind v2</div>'));
  const hash = crypto.createHash('sha256').update(fs.readFileSync(page)).digest('hex').slice(0, 12);
  const sw = path.join(STAGE, 'sw.js');
  fs.writeFileSync(sw, fs.readFileSync(sw, 'utf8').replace(/anagrind-[a-f0-9]+/g, 'anagrind-' + hash));
  return hash;
}

(async () => {
  fs.rmSync(STAGE, {recursive: true, force: true});
  fs.cpSync(path.join(__dirname, 'dist'), STAGE, {recursive: true});
  const server = spawn('python3', ['-m', 'http.server', String(PORT)],
                       {cwd: STAGE, stdio: 'ignore'});
  await wait(1500);

  const browser = await puppeteer.launch({
    executablePath: process.env.CHROME || '/opt/pw-browsers/chromium-1194/chrome-linux/chrome',
    args: ['--no-sandbox', '--disable-dev-shm-usage'],
  });
  const page = await browser.newPage();
  const mark = () => page.evaluate(() => document.querySelector('.mark').textContent);
  const results = [];

  try {
    await page.goto(`http://127.0.0.1:${PORT}/`, {waitUntil: 'load'});
    await page.waitForSelector('.answer .txt', {timeout: 30000});
    await page.evaluate(() => navigator.serviceWorker.ready);
    const first = await mark();
    const cacheV1 = await page.evaluate(async () => (await caches.keys())[0]);
    const precached = await page.evaluate(async () =>
      (await (await caches.open((await caches.keys())[0])).keys()).length);

    const manifest = await page.evaluate(() =>
      fetch('manifest.webmanifest').then(r => r.json()));
    const iconStatus = await page.evaluate(() => fetch('icon-180.png').then(r => r.status));

    // 1. the train test
    await page.setOfflineMode(true);
    await page.reload({waitUntil: 'load'});
    let offline = false;
    try {
      await page.waitForSelector('.answer .txt', {timeout: 20000});
      offline = (await page.$eval('.answer .txt', e => e.textContent)) === 'saturation point';
    } catch { /* offline check failed */ }
    await page.setOfflineMode(false);

    // 2. the redeploy test
    const hash = redeploy();
    await page.reload({waitUntil: 'load'});
    for (let i = 0; i < 25; i++) {
      const done = await page.evaluate(async h =>
        (await caches.keys()).includes('anagrind-' + h)
        && document.querySelector('.mark').textContent === 'anagrind v2', hash).catch(() => false);
      if (done) break;
      await wait(400);
    }
    await page.waitForSelector('.answer .txt', {timeout: 30000});
    const updated = await mark();
    const cachesAfter = await page.evaluate(async () => await caches.keys());

    results.push(
      ['manifest is installable', manifest.name === 'anagrind' && manifest.display === 'standalone'],
      ['apple-touch-icon served', iconStatus === 200],
      [`assets precached (${precached})`, precached >= 6],
      ['first load serves the current build', first === 'anagrind'],
      ['WORKS OFFLINE once installed', offline],
      ['a redeploy reaches an installed user', updated === 'anagrind v2'],
      ['the superseded cache is deleted',
        cachesAfter.length === 1 && cachesAfter[0] !== cacheV1],
    );
  } finally {
    await browser.close();
    server.kill();
  }

  let fail = 0;
  for (const [name, ok] of results) { if (!ok) fail++; console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}`); }
  console.log(fail ? '\n  FAILED' : '\n  dist/ is deployable');
  process.exit(fail ? 1 : 0);
})();
