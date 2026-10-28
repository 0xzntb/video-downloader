/**
 * Viddl — background script (Chrome/Edge/Brave service worker, Firefox event page).
 *
 * This file never reads a page. It has no `tabs.query` for content, no
 * `scripting.executeScript` that returns anything from the document, and no
 * storage: the only thing it learns is the URL the user pointed at, and the
 * only thing it does with it is hand that to the Viddl app. That promise is why
 * the extension asks for so little — `activeTab` and `scripting` cover the
 * click the hand-off needs, and nothing else is listed (S7).
 *
 * Its whole job is to turn "the user wants this video" into a `viddl://` URL and
 * let the operating system open the terminal app. There is no server, no port
 * and no account involved.
 *
 * The hand-off happens by clicking a hidden link inside the page the user is
 * already looking at. That is the gentlest way to ask the browser to launch a
 * registered app: it shows the usual "Open Viddl?" prompt once, and never
 * navigates the page away.
 *
 * The same file runs in both browsers, from the same folder: `manifest.json`
 * gives Chromium a service worker, `manifest.firefox.json` gives Firefox an
 * event page, and the only difference either of them sees here is the name of
 * the API object — `browser.*` in Firefox, `chrome.*` in Chromium. Both return
 * promises for the calls below, which is why they are awaited rather than
 * given callbacks: a callback handed to Firefox's `browser.*` would simply be
 * ignored, and the hand-off would never happen.
 */

const SCHEME = 'viddl';
const MENU_VIDEO = 'viddl-send-video';
const MENU_AUDIO = 'viddl-send-audio';

const isFirefox = typeof browser !== 'undefined';

/** The API object of whichever browser is running this. */
const api = isFirefox ? browser : chrome;

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
      await api.scripting.executeScript({
        target: { tabId, allFrames: false },
        func: clickHandOff,
        args: [targetLink],
      });
      return { ok: true, url: videoUrl };
    } catch (error) {
      // Pages like the browser's own add-on store refuse injection; fall through.
    }
  }

  try {
    await api.tabs.create({ url: targetLink, active: false });
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

async function createMenus() {
  // Removing first keeps a second install or a browser restart from creating
  // the same two items twice, which is an error in both browsers. Firefox
  // answers with a promise; Chromium has always taken a callback here, promise
  // support or not, and waiting for the removal is the point.
  try {
    await (isFirefox
      ? api.contextMenus.removeAll()
      : new Promise((resolve) => api.contextMenus.removeAll(resolve)));
  } catch (error) {
    // Nothing to remove, or menus are unavailable — either way, carry on.
  }
  try {
    api.contextMenus.create({
      id: MENU_VIDEO,
      title: 'Download with Viddl (choose quality)',
      contexts: ['page', 'link', 'video', 'audio'],
    });
    api.contextMenus.create({
      id: MENU_AUDIO,
      title: 'Download just the audio with Viddl',
      contexts: ['page', 'link', 'video', 'audio'],
    });
  } catch (error) {
    // A duplicate id: the menus are already there, which is the outcome wanted.
  }
}

api.runtime.onInstalled.addListener(() => {
  void createMenus();
});
api.runtime.onStartup.addListener(() => {
  void createMenus();
});

api.contextMenus.onClicked.addListener((info, tab) => {
  const videoUrl = pickUrl(info, tab);
  const audio = info.menuItemId === MENU_AUDIO;
  if (!videoUrl) {
    return;
  }
  void sendToViddl(tab && tab.id, videoUrl, audio);
});

// Kept as "return true, then sendResponse" rather than a promise: that is the
// one shape every Chromium version understands, and Firefox supports it too.
api.runtime.onMessage.addListener((message, sender, sendResponse) => {
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
  module.exports = { buildTargetLink, isDownloadable, pickUrl, sendToViddl };
}
