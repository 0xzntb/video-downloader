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
