/**
 * The extension in a real browser (E8).
 *
 * `extension.test.js` covers the logic with the browser stubbed out, and
 * `test_extension.py` checks that every file the manifests name exists. None of
 * that can tell you whether clicking the button actually produces a `viddl://`
 * link — the popup may not render, the content script may never find the player,
 * and the service worker may not be running at all. So this file loads the real
 * extension into a real Chromium and clicks things.
 *
 * Run with:
 *
 *     npm install                                  # playwright, once
 *     npx playwright install chromium              # the browser, once
 *     node --test tests/extension.browser.test.js  # pytest runs this for you
 *
 * The pages are never fetched from YouTube: every request to `youtube.com` is
 * answered from a two-line document written here, so the run is offline and the
 * same everywhere. What is exercised is the extension, not YouTube.
 *
 * One test loads a *copy* of the extension with one line added — a host
 * permission. That needs saying plainly: the shipped extension asks for
 * `activeTab`, not for any host, which means the background script can only
 * inject the hand-off click after the browser considers the extension invoked
 * (a click on the toolbar button, which a test cannot perform). Loading the copy
 * makes the browser grant what an invocation would, so the click actually lands
 * in the page and the link can be read. Everything else is the shipped code,
 * copied byte for byte.
 */

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

const { chromium } = require('playwright');

const EXTENSION = path.join(__dirname, '..', 'viddl', 'browser_extension');
const WATCH = 'https://www.youtube.com/watch?v=abc123';

//: Enough of a YouTube page for the content script: it looks for the player and
//: for the action row, in that order, and floats over the player when the row is
//: not there.
const PAGE = `<!DOCTYPE html>
<html><body>
  <div id="movie_player" style="width: 400px; height: 200px"></div>
</body></html>`;

/** Start Chromium with `folder` loaded as an unpacked extension. */
async function launch(folder) {
  const userDataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'viddl-profile-'));
  const context = await chromium.launchPersistentContext(userDataDir, {
    // Extensions need the full Chromium build in its new headless mode; the
    // default headless shell cannot load one.
    channel: 'chromium',
    headless: !process.env.VIDDL_BROWSER_HEADED,
    args: [`--disable-extensions-except=${folder}`, `--load-extension=${folder}`],
  });
  const worker =
    context.serviceWorkers()[0] || (await context.waitForEvent('serviceworker', { timeout: 30000 }));
  return {
    context,
    id: new URL(worker.url()).host,
    worker,
    async close() {
      await context.close();
      fs.rmSync(userDataDir, { recursive: true, force: true });
    },
  };
}

/** Answer every youtube.com request with PAGE, so nothing leaves the machine. */
function offlineYouTube(page) {
  return page.route('https://www.youtube.com/**', (route) =>
    route.fulfill({ contentType: 'text/html', body: PAGE })
  );
}

/**
 * Record every anchor the page is asked to click.
 *
 * The background script hands the `viddl://` link over by appending a hidden
 * anchor and clicking it, so this is where the link the operating system would
 * receive becomes visible. It has to be installed before the page is loaded.
 */
function recordHandOffs(page) {
  return page.addInitScript(() => {
    window.__viddlHandOffs = [];
    document.addEventListener(
      'click',
      (event) => {
        const anchor =
          event.target && event.target.closest ? event.target.closest('a') : null;
        if (anchor) {
          window.__viddlHandOffs.push(anchor.getAttribute('href'));
        }
      },
      true
    );
  });
}

/** Wait for the `count`-th hand-off and return its href. */
async function handOff(page, count = 1) {
  await page.waitForFunction(
    (expected) => (window.__viddlHandOffs || []).length >= expected,
    count,
    { timeout: 20000 }
  );
  const links = await page.evaluate(() => window.__viddlHandOffs);
  return links[count - 1];
}

/** Open a watch page and wait for the button the content script adds. */
async function openWatchPage(page, url = WATCH) {
  await offlineYouTube(page);
  await page.goto(url);
  await page.locator('#viddl-button').waitFor({ state: 'visible', timeout: 20000 });
}

/** The shipped extension with the host permission an invocation would grant. */
function permittedCopy() {
  const folder = fs.mkdtempSync(path.join(os.tmpdir(), 'viddl-extension-'));
  fs.cpSync(EXTENSION, folder, { recursive: true });
  const manifestPath = path.join(folder, 'manifest.json');
  const manifest = JSON.parse(fs.readFileSync(manifestPath, 'utf8'));
  manifest.host_permissions = ['*://*.youtube.com/*'];
  fs.writeFileSync(manifestPath, JSON.stringify(manifest, null, 2));
  return folder;
}

test('the popup renders, and refuses a page that is not a web page', async () => {
  const browser = await launch(EXTENSION);
  try {
    const popup = await browser.context.newPage();
    await popup.goto(`chrome-extension://${browser.id}/popup.html`);

    assert.strictEqual(await popup.locator('h1').textContent(), 'Viddl');
    assert.deepStrictEqual(await popup.locator('button').allTextContents(), [
      'Choose a quality',
      'Audio only',
    ]);
    // The first thing a new user reads: what to do about the viddl:// prompt.
    assert.match(await popup.locator('#status').textContent(), /viddl:\/\//);

    // The popup is opened as a tab here, so the active page is the extension
    // itself — exactly the "not a web page" case the popup has to explain.
    await popup.click('#video');
    await popup.waitForFunction(
      () => document.getElementById('status').classList.contains('error'),
      undefined,
      { timeout: 10000 }
    );
    assert.match(
      await popup.locator('#status').textContent(),
      /This page cannot be downloaded/
    );
  } finally {
    await browser.close();
  }
});

test('the YouTube button appears on a watch page and offers both choices', async () => {
  const browser = await launch(EXTENSION);
  try {
    const page = await browser.context.newPage();
    await openWatchPage(page);

    const button = page.locator('#viddl-button');
    assert.strictEqual(await button.textContent(), 'Viddl');
    assert.strictEqual(
      await button.getAttribute('aria-label'),
      'Send this video to the Viddl app'
    );

    await button.click();
    const choices = page.locator('.viddl-choice');
    await choices.nth(1).waitFor({ state: 'visible', timeout: 10000 });
    assert.strictEqual(await choices.count(), 2);
    assert.match(await choices.nth(0).textContent(), /^Video/);
    assert.match(await choices.nth(1).textContent(), /^Audio only/);
  } finally {
    await browser.close();
  }
});

// One hand-off per browser, and that is not a detail: a click on a `viddl://`
// link raises the browser's "Open Viddl?" confirmation, and while it is up the
// page stops receiving real mouse input. A second click in the same session
// would look like a failing button. Each test therefore gets its own browser.
test('the hand-off link carries the page and the audio flag', async () => {
  const copy = permittedCopy();
  const browser = await launch(copy);
  try {
    const page = await browser.context.newPage();
    await recordHandOffs(page);
    await openWatchPage(page);

    await page.click('#viddl-button');
    await page.click('.viddl-choice >> nth=1');

    const audioLink = await handOff(page, 1);
    assert.strictEqual(
      audioLink,
      'viddl://download?url=https%3A%2F%2Fwww.youtube.com%2Fwatch%3Fv%3Dabc123&audio=1'
    );
    const parsed = new URL(audioLink);
    assert.strictEqual(parsed.protocol, 'viddl:');
    assert.strictEqual(parsed.host, 'download');
    assert.strictEqual(parsed.searchParams.get('url'), WATCH);
    assert.strictEqual(parsed.searchParams.get('audio'), '1');
    // And the page is told it worked, rather than left guessing.
    await page.locator('#viddl-toast').waitFor({ state: 'visible', timeout: 10000 });
    assert.match(await page.locator('#viddl-toast').textContent(), /audio/i);
  } finally {
    await browser.close();
    fs.rmSync(copy, { recursive: true, force: true });
  }
});

test('the video choice drops the flag, and an option-shaped address stays one value', async () => {
  // A page whose own address carries `&audio=1&--exec=calc.exe` in the video id.
  // The link is built from that address, and the app turns it into a command
  // line (S1) — so the payload must stay inside the `url` value, encoded, and
  // never arrive as a parameter of its own or as a bare word.
  const hostile = 'https://www.youtube.com/watch?v=abc%26audio%3D1%26--exec%3Dcalc.exe';
  const copy = permittedCopy();
  const browser = await launch(copy);
  try {
    const page = await browser.context.newPage();
    await recordHandOffs(page);
    await openWatchPage(page, hostile);

    await page.click('#viddl-button');
    await page.click('.viddl-choice >> nth=0');

    const link = await handOff(page, 1);
    const parsed = new URL(link);
    assert.strictEqual(
      parsed.searchParams.get('url'),
      'https://www.youtube.com/watch?v=abc&audio=1&--exec=calc.exe',
      'the address arrives whole, as one value'
    );
    // The video choice was clicked, so the link carries no audio flag of its
    // own — and the `&audio=1` inside the address did not become one.
    assert.strictEqual(parsed.searchParams.has('audio'), false, 'no second parameter');
    assert.ok(!link.includes('--exec=calc.exe'), 'the payload is never left unencoded');
  } finally {
    await browser.close();
    fs.rmSync(copy, { recursive: true, force: true });
  }
});

/** How many tabs the extension can see. Its URL is not needed, so neither is the
 * `tabs` permission: the count is all this asks for. */
function tabCount(worker) {
  return worker.evaluate(() => chrome.tabs.query({}).then((tabs) => tabs.length));
}

test('with no host permission the hand-off opens a tab instead', async () => {
  // The shipped extension, untouched: without the permission an invocation would
  // grant, the injected click is refused and the background script falls back to
  // opening the link in a tab. Chromium hands back no page object for a tab
  // holding a custom scheme, so the proof here is the tab the extension itself
  // can count.
  const browser = await launch(EXTENSION);
  try {
    const page = await browser.context.newPage();
    await openWatchPage(page);
    const before = await tabCount(browser.worker);

    await page.click('#viddl-button');
    await page.click('.viddl-choice >> nth=1');

    let after = before;
    const deadline = Date.now() + 20000;
    while (Date.now() < deadline) {
      after = await tabCount(browser.worker);
      if (after > before) {
        break;
      }
      await new Promise((resolve) => setTimeout(resolve, 250));
    }
    assert.ok(after > before, `a tab was opened for the link (${before} → ${after})`);
  } finally {
    await browser.close();
  }
});
