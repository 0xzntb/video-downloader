/**
 * Viddl — YouTube content script.
 *
 * Puts a "Viddl" button on the page: next to YouTube's own buttons when that
 * row exists, otherwise floating on the player. Clicking it offers the two
 * things people actually want — pick a quality, or just grab the audio.
 *
 * The link is handed to the extension, which opens the viddl:// URL and lets
 * the operating system start the terminal app.
 */

const api = typeof browser !== 'undefined' ? browser : chrome;

const BUTTON_ID = 'viddl-button';
const PANEL_ID = 'viddl-panel';
const TOAST_ID = 'viddl-toast';
const CHECK_INTERVAL_MS = 1200;

/** Work out which video the current address points at. */
function videoUrlFrom(href) {
  let url;
  try {
    url = new URL(href, 'https://www.youtube.com');
  } catch (error) {
    return null;
  }

  const host = url.hostname.replace(/^www\./, '');
  if (host === 'youtu.be') {
    const id = url.pathname.slice(1).split('/')[0];
    return id ? `https://www.youtube.com/watch?v=${id}` : null;
  }
  if (host !== 'youtube.com' && host !== 'm.youtube.com' && host !== 'music.youtube.com') {
    return null;
  }

  if (url.pathname === '/watch') {
    const id = url.searchParams.get('v');
    return id ? `https://www.youtube.com/watch?v=${id}` : null;
  }
  const shorts = url.pathname.match(/^\/shorts\/([A-Za-z0-9_-]+)/);
  if (shorts) {
    return `https://www.youtube.com/shorts/${shorts[1]}`;
  }
  const live = url.pathname.match(/^\/live\/([A-Za-z0-9_-]+)/);
  if (live) {
    return `https://www.youtube.com/watch?v=${live[1]}`;
  }
  return null;
}

function showToast(text, isError) {
  document.getElementById(TOAST_ID)?.remove();
  const toast = document.createElement('div');
  toast.id = TOAST_ID;
  toast.className = isError ? 'viddl-toast viddl-toast-error' : 'viddl-toast';
  toast.textContent = text;
  document.body.appendChild(toast);
  window.setTimeout(() => toast.remove(), 5000);
}

function sendToViddl(url, audio) {
  const failed = () => showToast('Viddl could not start. Reload this page and try again.', true);
  let reply;
  try {
    reply = api.runtime.sendMessage({ type: 'viddl-send', url, audio });
  } catch (error) {
    failed();
    return;
  }
  // Firefox answers with a promise here and Chromium's Manifest V3 does too,
  // so a callback would be ignored in one of them.
  Promise.resolve(reply).then((response) => {
    if (!response || !response.ok) {
      failed();
      return;
    }
    showToast(
      audio
        ? 'Sent to Viddl as audio — look for the terminal window.'
        : 'Sent to Viddl — pick a quality in the terminal window.'
    );
  }, failed);
}

function closePanel() {
  document.getElementById(PANEL_ID)?.remove();
}

function togglePanel(anchor, url) {
  if (document.getElementById(PANEL_ID)) {
    closePanel();
    return;
  }
  const panel = document.createElement('div');
  panel.id = PANEL_ID;

  const video = document.createElement('button');
  video.type = 'button';
  video.className = 'viddl-choice';
  video.innerHTML = '<strong>Video</strong><span>choose a quality with sizes</span>';
  video.addEventListener('click', (event) => {
    event.preventDefault();
    event.stopPropagation();
    closePanel();
    sendToViddl(url, false);
  });

  const audio = document.createElement('button');
  audio.type = 'button';
  audio.className = 'viddl-choice';
  audio.innerHTML = '<strong>Audio only</strong><span>saved as audio</span>';
  audio.addEventListener('click', (event) => {
    event.preventDefault();
    event.stopPropagation();
    closePanel();
    sendToViddl(url, true);
  });

  panel.append(video, audio);
  const rect = anchor.getBoundingClientRect();
  panel.style.top = `${Math.round(rect.bottom + 8)}px`;
  panel.style.left = `${Math.round(Math.min(rect.left, window.innerWidth - 300))}px`;
  document.body.appendChild(panel);

  const dismiss = (event) => {
    if (!panel.contains(event.target)) {
      closePanel();
      document.removeEventListener('click', dismiss, true);
    }
  };
  window.setTimeout(() => document.addEventListener('click', dismiss, true), 0);
}

function findHost() {
  const actionRow = document.querySelector('#top-level-buttons-computed');
  if (actionRow) {
    return { element: actionRow, floating: false };
  }
  const player = document.querySelector('#movie_player');
  if (player) {
    return { element: player, floating: true };
  }
  return null;
}

function ensureButton() {
  const url = videoUrlFrom(window.location.href);
  const existing = document.getElementById(BUTTON_ID);

  if (!url) {
    existing?.remove();
    closePanel();
    return;
  }
  if (existing && existing.isConnected && existing.dataset.viddlUrl === url) {
    return;
  }
  existing?.remove();

  const host = findHost();
  if (!host) {
    return;
  }

  const button = document.createElement('button');
  button.id = BUTTON_ID;
  button.type = 'button';
  button.dataset.viddlUrl = url;
  button.title = 'Send this video to the Viddl app';
  button.textContent = 'Viddl';
  button.setAttribute('aria-label', 'Send this video to the Viddl app');
  if (host.floating) {
    button.classList.add('viddl-floating');
  }
  button.addEventListener('click', (event) => {
    event.preventDefault();
    event.stopPropagation();
    togglePanel(button, url);
  });
  host.element.appendChild(button);
}

function start() {
  const tick = () => {
    try {
      ensureButton();
    } catch (error) {
      /* YouTube re-renders constantly; nothing here is worth breaking on. */
    }
  };
  window.setInterval(tick, CHECK_INTERVAL_MS);
  document.addEventListener('yt-navigate-finish', tick);
  document.addEventListener('yt-page-data-updated', tick);
  window.addEventListener('resize', closePanel);
  tick();
}

if (typeof module !== 'undefined' && module.exports) {
  module.exports = { videoUrlFrom };
} else {
  start();
}
