/**
 * Behaviour tests for the pieces of the extension that are pure logic.
 *
 * Run with: node --test tests/extension.test.js
 * (pytest runs this for you via tests/test_extension.py.)
 */

const test = require('node:test');
const assert = require('node:assert');
const path = require('node:path');

// background.js registers listeners at import time, so give it a stub to talk to.
globalThis.chrome = {
  runtime: {
    onInstalled: { addListener() {} },
    onStartup: { addListener() {} },
    onMessage: { addListener() {} },
  },
  contextMenus: {
    removeAll() {},
    create() {},
    onClicked: { addListener() {} },
  },
};

const extension = path.join(__dirname, '..', 'viddl', 'browser_extension');
const background = require(path.join(extension, 'background.js'));
const youtube = require(path.join(extension, 'content-youtube.js'));

test('buildTargetLink encodes the page and the audio flag', () => {
  assert.strictEqual(
    background.buildTargetLink('https://www.youtube.com/watch?v=a b&x=1', false),
    'viddl://download?url=https%3A%2F%2Fwww.youtube.com%2Fwatch%3Fv%3Da%20b%26x%3D1'
  );
  assert.strictEqual(
    background.buildTargetLink('https://x.com/u/status/1', true),
    'viddl://download?url=https%3A%2F%2Fx.com%2Fu%2Fstatus%2F1&audio=1'
  );
});

test('only real web pages are considered downloadable', () => {
  assert.ok(background.isDownloadable('https://vimeo.com/1'));
  assert.ok(background.isDownloadable('http://example.com'));
  assert.ok(!background.isDownloadable('chrome://extensions'));
  assert.ok(!background.isDownloadable('about:blank'));
  assert.ok(!background.isDownloadable(undefined));
  assert.ok(!background.isDownloadable(''));
});

test('a right-click prefers the link, then the page', () => {
  const tab = { url: 'https://www.youtube.com/watch?v=page' };
  assert.strictEqual(
    background.pickUrl({ linkUrl: 'https://youtu.be/link', pageUrl: 'https://a.test/' }, tab),
    'https://youtu.be/link'
  );
  assert.strictEqual(
    background.pickUrl({ pageUrl: 'https://a.test/watch' }, tab),
    'https://a.test/watch'
  );
  assert.strictEqual(background.pickUrl({}, tab), tab.url);
  assert.strictEqual(
    background.pickUrl({ linkUrl: 'chrome://settings', pageUrl: 'about:blank' }, { url: 'chrome://newtab' }),
    null
  );
});

test('the YouTube button knows which video is open', () => {
  const { videoUrlFrom } = youtube;
  assert.strictEqual(
    videoUrlFrom('https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=PL1'),
    'https://www.youtube.com/watch?v=dQw4w9WgXcQ'
  );
  assert.strictEqual(
    videoUrlFrom('https://m.youtube.com/watch?v=abc123'),
    'https://www.youtube.com/watch?v=abc123'
  );
  assert.strictEqual(
    videoUrlFrom('https://youtu.be/abc123?t=30'),
    'https://www.youtube.com/watch?v=abc123'
  );
  assert.strictEqual(
    videoUrlFrom('https://www.youtube.com/shorts/abc123'),
    'https://www.youtube.com/shorts/abc123'
  );
  assert.strictEqual(
    videoUrlFrom('https://www.youtube.com/live/abc123?feature=share'),
    'https://www.youtube.com/watch?v=abc123'
  );
});

test('the button stays away from pages that hold no video', () => {
  const { videoUrlFrom } = youtube;
  assert.strictEqual(videoUrlFrom('https://www.youtube.com/@somechannel'), null);
  assert.strictEqual(videoUrlFrom('https://www.youtube.com/results?search_query=hi'), null);
  assert.strictEqual(videoUrlFrom('https://www.youtube.com/watch'), null);
  assert.strictEqual(videoUrlFrom('https://example.com/watch?v=abc'), null);
  assert.strictEqual(videoUrlFrom('not a url'), null);
});

// ---------------------------------------------------------------------------
// The same background script on both browsers (F15)
//
// Firefox calls its API object `browser` and answers with promises; Chromium
// calls it `chrome` and answers with promises too in Manifest V3. Both are
// exercised here, because a callback handed to Firefox's `browser.*` is
// silently ignored — which looks exactly like "nothing happened".
// ---------------------------------------------------------------------------

const backgroundPath = require.resolve(path.join(extension, 'background.js'));

function fakeApi() {
  const calls = { injected: [], opened: [], menus: [] };
  let onMessage;
  let onInstalled;
  const api = {
    runtime: {
      onInstalled: {
        addListener(listener) {
          onInstalled = listener;
        },
      },
      onStartup: { addListener() {} },
      onMessage: {
        addListener(listener) {
          onMessage = listener;
        },
      },
    },
    contextMenus: {
      // Chromium is given a callback here, Firefox a promise; answer both.
      removeAll(callback) {
        if (callback) callback();
        return Promise.resolve();
      },
      create(options) {
        calls.menus.push(options);
      },
      onClicked: { addListener() {} },
    },
    scripting: {
      executeScript: async (options) => {
        calls.injected.push(options);
      },
    },
    tabs: {
      create: async (options) => {
        calls.opened.push(options);
      },
    },
  };
  return { api, calls, listener: () => onMessage, installed: () => onInstalled };
}

/** Load a fresh copy of background.js on top of one browser's API object. */
function loadBackground(api, asFirefox) {
  const savedChrome = globalThis.chrome;
  const savedBrowser = globalThis.browser;
  delete require.cache[backgroundPath];
  globalThis.chrome = undefined;
  globalThis.browser = undefined;
  if (asFirefox) {
    globalThis.browser = api;
  } else {
    globalThis.chrome = api;
  }
  try {
    return require(backgroundPath);
  } finally {
    globalThis.chrome = savedChrome;
    globalThis.browser = savedBrowser;
  }
}

/** Hand a message to the loaded listener and wait for its asynchronous reply. */
function ask(listener, message) {
  return new Promise((resolve) => {
    const keepOpen = listener()(message, {}, resolve);
    assert.strictEqual(keepOpen, true, 'the reply is asynchronous');
  });
}

for (const namespace of ['browser', 'chrome']) {
  test(`the right-click menus are created on ${namespace}.*`, async () => {
    const { api, calls, installed } = fakeApi();
    loadBackground(api, namespace === 'browser');

    installed()();
    await new Promise((resolve) => setTimeout(resolve, 0));

    assert.deepStrictEqual(
      calls.menus.map((menu) => menu.id),
      ['viddl-send-video', 'viddl-send-audio'],
      'both items, created after the old ones were removed'
    );
  });

  test(`the hand-off runs through ${namespace}.*`, async () => {
    const { api, calls, listener } = fakeApi();
    loadBackground(api, namespace === 'browser');

    const response = await ask(listener, {
      type: 'viddl-send',
      url: 'https://vimeo.com/1',
      audio: true,
      tabId: 7,
    });

    assert.deepStrictEqual(response, { ok: true, url: 'https://vimeo.com/1' });
    assert.strictEqual(calls.injected.length, 1);
    assert.deepStrictEqual(calls.injected[0].target, { tabId: 7, allFrames: false });
    assert.strictEqual(typeof calls.injected[0].func, 'function');
    assert.deepStrictEqual(calls.injected[0].args, [
      'viddl://download?url=https%3A%2F%2Fvimeo.com%2F1&audio=1',
    ]);
    assert.deepStrictEqual(calls.opened, [], 'the page itself did the hand-off');
  });
}

test('a page that refuses injection gets the link in a tab instead', async () => {
  const { api, calls, listener } = fakeApi();
  api.scripting.executeScript = async () => {
    throw new Error('Missing host permission');
  };
  loadBackground(api, true);

  const response = await ask(listener, {
    type: 'viddl-send',
    url: 'https://x.com/u/status/1',
    audio: false,
    tabId: 3,
  });

  assert.deepStrictEqual(response, { ok: true, url: 'https://x.com/u/status/1', openedTab: true });
  assert.deepStrictEqual(calls.opened, [
    { url: 'viddl://download?url=https%3A%2F%2Fx.com%2Fu%2Fstatus%2F1', active: false },
  ]);
});

test('a hand-off that cannot happen at all says so', async () => {
  const { api, listener } = fakeApi();
  api.scripting.executeScript = async () => {
    throw new Error('no');
  };
  api.tabs.create = async () => {
    throw new Error('also no');
  };
  loadBackground(api, false);

  const response = await ask(listener, {
    type: 'viddl-send',
    url: 'https://vimeo.com/2',
    audio: false,
    tabId: 1,
  });

  assert.strictEqual(response.ok, false);
  assert.match(response.error, /also no/);
});

test('a message about no real web page is refused before anything opens', () => {
  const { api, calls, listener } = fakeApi();
  loadBackground(api, true);

  let answer;
  const keepOpen = listener()({ type: 'viddl-send', url: 'about:blank' }, {}, (value) => {
    answer = value;
  });
  assert.strictEqual(keepOpen, undefined, 'a refused message needs no channel');
  assert.deepStrictEqual(answer, { ok: false, error: 'That does not look like a web page.' });
  assert.deepStrictEqual(calls.injected, []);
  assert.deepStrictEqual(calls.opened, []);
});
