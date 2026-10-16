/** Viddl — popup. Sends whatever page you are looking at to the terminal app. */

const statusLine = document.getElementById('status');

function setStatus(text, isError) {
  statusLine.textContent = text;
  statusLine.classList.toggle('error', Boolean(isError));
}

async function activeTab() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  return tab;
}

async function send(audio) {
  const tab = await activeTab();
  if (!tab || !/^https?:\/\//i.test(tab.url || '')) {
    setStatus('This page cannot be downloaded — open a video page first.', true);
    return;
  }
  setStatus('Opening Viddl…');
  chrome.runtime.sendMessage(
    { type: 'viddl-send', url: tab.url, audio, tabId: tab.id },
    (response) => {
      if (chrome.runtime.lastError || !response || !response.ok) {
        setStatus('Viddl did not start. Is the app installed and registered?', true);
        return;
      }
      setStatus(
        audio
          ? 'Sent as MP3 — check the terminal window that just opened.'
          : 'Sent — pick a quality in the terminal window that just opened.'
      );
      window.setTimeout(() => window.close(), 1200);
    }
  );
}

document.getElementById('video').addEventListener('click', () => send(false));
document.getElementById('audio').addEventListener('click', () => send(true));
document.getElementById('help').addEventListener('click', (event) => {
  event.preventDefault();
  setStatus(
    'Run “viddl --install-browser-extension” then “viddl --register-protocol” in your terminal.',
    false
  );
});
