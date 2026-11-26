// Records the README demo (offline demo mode) as lossless PNG frames.
//
//   python app.py                                   # with ANTHROPIC_API_KEY unset
//   node scripts/record_demo.js demo-frames         # needs the `playwright` npm package
//   python scripts/build_demo.py demo-frames        # -> examples/demo.gif + demo.mp4 (ffmpeg)
//
// Set INSTANT=1 for jump scrolls: far smaller GIF frames (the README GIF is recorded that way).
const { chromium } = require('playwright');
const OUT = process.argv[2] || 'demo-frames';
const BASE = process.env.DEMO_URL || 'http://127.0.0.1:5000/';
const SCROLL = process.env.INSTANT === '1' ? 'instant' : 'smooth';
const W = 1280, H = 800;
(async () => {
  const browser = await chromium.launch();
  const ctx = await browser.newContext({ viewport: { width: W, height: H }, deviceScaleFactor: 1,
    acceptDownloads: true });
  await ctx.grantPermissions(['clipboard-read', 'clipboard-write'], { origin: new URL(BASE).origin });
  const page = await ctx.newPage();
  const wait = (ms) => page.waitForTimeout(ms);
  const fs = require('fs'); fs.rmSync(OUT + '', { recursive: true, force: true }); fs.mkdirSync(OUT + '');
  const cdp = await ctx.newCDPSession(page); const meta = []; let n = 0;
  cdp.on('Page.screencastFrame', async (f) => {
    const name = `f${String(n++).padStart(5, '0')}.png`;
    fs.writeFileSync(OUT + '/' + name, Buffer.from(f.data, 'base64'));
    meta.push([name, f.metadata.timestamp]);
    try { await cdp.send('Page.screencastFrameAck', { sessionId: f.sessionId }); } catch (e) {}
  });
  await cdp.send('Page.startScreencast', { format: 'png', maxWidth: W, maxHeight: H, everyNthFrame: 1 });
  await page.goto(BASE);
  await page.addStyleTag({ content: `
    #capbar{position:fixed;left:50%;bottom:22px;transform:translateX(-50%);z-index:99999;background:#14213d;color:#fff;
      font:600 19px/1.35 -apple-system,Segoe UI,Helvetica,Arial,sans-serif;padding:12px 22px;border-radius:8px;
      box-shadow:0 6px 24px rgba(0,0,0,.35);border-bottom:3px solid #c9a227;max-width:1100px;text-align:center;transition:opacity .25s}
    #fakecur{position:fixed;z-index:100000;width:22px;height:22px;border-radius:50%;background:rgba(201,162,39,.45);
      border:2px solid #c9a227;pointer-events:none;transform:translate(-50%,-50%);transition:left .45s ease,top .45s ease}` });
  await page.evaluate(() => { const c = document.createElement('div'); c.id = 'capbar'; c.style.opacity = 0; document.body.appendChild(c);
    const m = document.createElement('div'); m.id = 'fakecur'; m.style.left = '640px'; m.style.top = '420px'; document.body.appendChild(m); });
  const cap = async (t) => { await page.evaluate((t) => { const c = document.getElementById('capbar'); c.textContent = t; c.style.opacity = t ? 1 : 0; }, t); };
  const scrollTo = async (sel, block = 'center') => {
    await page.evaluate(([s, b, m]) => document.querySelector(s).scrollIntoView({ behavior: m, block: b }), [sel, block, SCROLL]);
    await wait(900);
  };
  const click = async (sel, pause = 500) => {
    const el = page.locator(sel).first();
    await el.scrollIntoViewIfNeeded();
    const b = await el.boundingBox();
    await page.evaluate(([x, y]) => { const m = document.getElementById('fakecur'); m.style.left = x + 'px'; m.style.top = y + 'px'; }, [b.x + b.width / 2, b.y + b.height / 2]);
    await wait(pause);
    await el.click();
  };

  await wait(800);
  await cap('Offline demo: no API key needed');
  await wait(1600);
  await click('#loadSample');
  await cap('1. Load the sample requirements (R7 is deliberately vague)');
  await page.evaluate(() => { const t = document.querySelector('textarea'); t.scrollTop = t.scrollHeight; });
  await wait(2200);
  await click('#btnAnalyze');
  await cap('2. Check requirements: R7 is flagged before any test is written');
  await page.waitForSelector('#analysis button[data-rewrite]');
  await wait(400);
  await scrollTo('#analysis', 'start');
  await wait(2000);
  await scrollTo('#analysis button[data-rewrite]');
  await wait(600);
  await click('#analysis button[data-rewrite]');
  await cap('Use this wording rewrites R7 so it can be tested');
  await wait(1800);
  await scrollTo('#btnCopyQs');
  await click('#btnCopyQs');
  await cap('…or copy the clarifying questions for the team as Markdown');
  await wait(2200);
  await click('#btnGenerate');
  await cap('3. Generate tests: positive, negative, edge case');
  await page.waitForSelector('[data-fix], #btnRun', { state: 'attached' });
  await wait(600);
  await page.evaluate((m) => window.scrollBy({ top: 600, behavior: m }), SCROLL); await wait(1400);
  await page.evaluate((m) => window.scrollBy({ top: 700, behavior: m }), SCROLL); await wait(1200);
  await click('#btnRun');
  await cap('4. Run the tests with coverage');
  await page.waitForSelector('button[data-fix]');
  await wait(500);
  await scrollTo('button[data-fix]');
  await cap('One test has a syntax error, so it gets a Fix with AI button');
  await wait(2200);
  await click('button[data-fix]');
  await page.waitForSelector('button[data-apply]');
  await cap('The fix is shown as a diff. Nothing is saved until you apply it');
  await scrollTo('button[data-apply]');
  await wait(2600);
  await click('button[data-apply]');
  await cap('Apply and rerun: the test now passes (Undo AI fix is one click away)');
  await page.waitForSelector('button[data-undo]');
  await wait(500);
  await scrollTo('button[data-undo]');
  await wait(2600);
  await scrollTo('button[data-bug]');
  await cap('5. A real failure: the 64-character edge case caught the planted bug');
  await wait(2200);
  await click('button[data-bug]');
  await page.waitForSelector('#bugTitle');
  await cap('Create bug report: steps, expected vs actual, ready for JIRA or GitHub');
  await wait(500);
  await scrollTo('#bugPanel', 'start');
  await wait(2000);
  await page.evaluate((m) => document.getElementById('bugBody').scrollTo({ top: 400, behavior: m }), SCROLL);
  await wait(2200);
  await scrollTo('#btnReport');
  await cap('6. Download the PDF report with charts and coverage');
  await wait(800);
  const dl = page.waitForEvent('download', { timeout: 20000 }).catch(() => null);
  await click('#btnReport');
  await dl;
  await wait(2200);
  await cap('');
  await wait(400);
  await cdp.send('Page.stopScreencast');
  meta.push(['END', Date.now() / 1000]);
  fs.writeFileSync(OUT + '/meta.json', JSON.stringify(meta));
  await ctx.close();
  await browser.close();
})();
