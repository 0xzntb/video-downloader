/**
 * Viddl — background service worker.
 *
 * Its whole job is to turn "the user wants this video" into a `viddl://` URL and
 * let the operating system open the terminal app. There is no server, no port
 * and no account involved.
 *
 * The hand-off happens by clicking a hidden link inside the page the user is
 * already looking at. That is the gentlest way to ask Chrome to launch a
 * registered app: it shows the usual "Open Viddl?" prompt once, and never
 * navigates the page away.
 */

const SCHEME = 'viddl';
const MENU_VIDEO = 'viddl-send-video';
const MENU_AUDIO = 'viddl-send-audio';

/** Build the custom-scheme link the terminal app understands. */
function buildTargetLink(videoUrl, audio) {
  const suffix = audio ? '&audio=1' : '';
  return `${SCHEME}://download?url=${encodeURIComponent(videoUrl)}${suffix}`;
}

/** Only real web pages can be downloaded; ignore browser-internal URLs. */
function isDownloadable(url) {
  return typeof url === 'string' && /^https?:\/\//i.test(url);
}

/**
 * Runs inside the page (it gets serialised, so it must not close over anything).
 * Clicking an anchor is how a page hands a custom scheme to the OS without
 * leaving the current page.
 */
function clickHandOff(targetLink) {
  const link = document.createElement('a');
  link.href = targetLink;
  link.rel = 'nofollow';
  link.style.position = 'fixed';
  link.style.left = '-9999px';
  link.style.top = '0';
  document.documentElement.appendChild(link);
  link.click();
  window.setTimeout(() => link.remove(), 1000);
  return true;
}

/** Ask the current tab to perform the hand-off, with a tab fallback. */
async function sendToViddl(tabId, videoUrl, audio) {
  const targetLink = buildTargetLink(videoUrl, audio);

  if (typeof tabId === 'number') {
    try {
      await chrome.scripting.executeScript({
        target: { tabId, allFrames: false },
        func: clickHandOff,
        args: [targetLink],
      });
      return { ok: true, url: videoUrl };
    } catch (error) {
      // Pages like the Chrome Web Store refuse injection; fall through.
    }
  }

  try {
    await chrome.tabs.create({ url: targetLink, active: false });
    return { ok: true, url: videoUrl, openedTab: true };
  } catch (error) {
    return { ok: false, error: String(error), url: videoUrl };
  }
}

/** Which URL should be downloaded for a right-click? */
function pickUrl(info, tab) {
  const candidates = [info.linkUrl, info.pageUrl, tab && tab.url];
  for (const candidate of candidates) {
    if (isDownloadable(candidate)) {
      return candidate;
    }
  }
  return null;
}

function createMenus() {
  chrome.contextMenus.removeAll(() => {
    chrome.contextMenus.create({
      id: MENU_VIDEO,
      title: 'Download with Viddl (choose quality)',
      contexts: ['page', 'link', 'video', 'audio'],
    });
    chrome.contextMenus.create({
      id: MENU_AUDIO,
      title: 'Download audio as MP3 with Viddl',
      contexts: ['page', 'link', 'video', 'audio'],
    });
  });
}

chrome.runtime.onInstalled.addListener(createMenus);
chrome.runtime.onStartup.addListener(createMenus);

chrome.contextMenus.onClicked.addListener((info, tab) => {
  const videoUrl = pickUrl(info, tab);
  const audio = info.menuItemId === MENU_AUDIO;
  if (!videoUrl) {
    return;
  }
  void sendToViddl(tab && tab.id, videoUrl, audio);
});

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (!message || message.type !== 'viddl-send') {
    return undefined;
  }
  if (!isDownloadable(message.url)) {
    sendResponse({ ok: false, error: 'That does not look like a web page.' });
    return undefined;
  }
  const tabId =
    typeof message.tabId === 'number' ? message.tabId : sender.tab ? sender.tab.id : undefined;
  sendToViddl(tabId, message.url, Boolean(message.audio)).then(sendResponse);
  return true; // keep the message channel open for the async reply
});

// Exported for the test harness in tests/extension.test.js.
if (typeof module !== 'undefined' && module.exports) {
  module.exports = { buildTargetLink, isDownloadable, pickUrl };
}
