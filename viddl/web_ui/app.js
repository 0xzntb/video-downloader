/**
 * Viddl — the page served from 127.0.0.1.
 *
 * Every address here is relative, so it resolves inside the token's folder and
 * the token never has to be written into this file. The page only ever talks to
 * its own origin: no fonts, no images, no analytics, nothing fetched from
 * anywhere else.
 */
'use strict';

const byId = (id) => document.getElementById(id);

const form = byId('lookup');
const link = byId('link');
const lookUpButton = byId('look-up');
const statusLine = byId('status');
const video = byId('video');
const title = byId('title');
const meta = byId('meta');
const note = byId('note');
const options = byId('options');
const downloadButton = byId('download');
const progressBox = byId('progress');
const progressBar = byId('bar');
const downloadTitle = byId('download-title');
const numbers = byId('numbers');
const stopButton = byId('stop');
const doneBox = byId('done');
const saved = byId('saved');
const savedNote = byId('saved-note');
const again = byId('again');

let current = null;
let jobId = null;
let timer = null;

/* ------------------------------------------------------------------ */
/* talking to the server                                              */
/* ------------------------------------------------------------------ */

async function read(response) {
  let answer = {};
  try {
    answer = await response.json();
  } catch (error) {
    answer = {};
  }
  if (!response.ok) {
    const problem = answer.error || {};
    const message = [problem.message, problem.hint].filter(Boolean).join(' ');
    throw new Error(message || 'Viddl could not answer that.');
  }
  return answer;
}

function post(path, body) {
  return fetch(path, {
    method: 'POST',
    credentials: 'omit',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  }).then(read);
}

function load(path) {
  return fetch(path, { credentials: 'omit' }).then(read);
}

/* ------------------------------------------------------------------ */
/* small pieces of text                                               */
/* ------------------------------------------------------------------ */

function sizes(bytes) {
  if (typeof bytes !== 'number' || !isFinite(bytes) || bytes < 0) {
    return '';
  }
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let value = bytes;
  let unit = 0;
  while (value >= 1000 && unit < units.length - 1) {
    value /= 1000;
    unit += 1;
  }
  const digits = value >= 100 || unit === 0 ? 0 : 1;
  return value.toFixed(digits) + ' ' + units[unit];
}

function clock(seconds) {
  if (typeof seconds !== 'number' || !isFinite(seconds) || seconds <= 0) {
    return '';
  }
  const total = Math.round(seconds);
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const rest = total % 60;
  const pad = (value) => String(value).padStart(2, '0');
  return hours ? hours + ':' + pad(minutes) + ':' + pad(rest) : minutes + ':' + pad(rest);
}

function showStatus(text, isError) {
  statusLine.textContent = text || '';
  statusLine.classList.toggle('error', Boolean(isError));
}

function clearStatus() {
  showStatus('', false);
}

/* ------------------------------------------------------------------ */
/* the three screens                                                  */
/* ------------------------------------------------------------------ */

function reset() {
  stopPolling();
  jobId = null;
  current = null;
  options.replaceChildren();
  title.textContent = '';
  meta.textContent = '';
  note.textContent = '';
  note.hidden = true;
  video.hidden = true;
  progressBox.hidden = true;
  doneBox.hidden = true;
  progressBar.value = 0;
  numbers.textContent = '';
  downloadButton.disabled = false;
  stopButton.disabled = false;
  clearStatus();
}

function showVideo(found) {
  current = found;
  title.textContent = found.title || 'Untitled video';

  const bits = [];
  if (found.uploader) {
    bits.push(found.uploader);
  }
  const length = clock(found.duration);
  if (length) {
    bits.push(length);
  }
  if (found.live) {
    bits.push('live now');
  }
  if (found.playlist) {
    bits.push('one video of a playlist of ' + found.playlist);
  }
  meta.textContent = bits.join(' · ');

  note.textContent = found.note || '';
  note.hidden = !found.note;

  const legend = document.createElement('legend');
  legend.textContent = 'Which quality?';
  options.replaceChildren(legend);

  found.options.forEach((option, index) => {
    const row = document.createElement('label');
    row.className = 'option';

    const radio = document.createElement('input');
    radio.type = 'radio';
    radio.name = 'option';
    radio.value = option.key;
    radio.checked = index === 0;

    const name = document.createElement('span');
    name.className = 'label';
    name.textContent = option.label;

    row.append(radio, name);

    const detail = [option.sizeText, option.detail].filter(Boolean).join(' · ');
    if (detail) {
      const extra = document.createElement('span');
      extra.className = 'detail';
      extra.textContent = detail;
      row.append(extra);
    }

    options.append(row);
  });

  const none = found.options.length === 0;
  if (none) {
    const empty = document.createElement('p');
    empty.className = 'meta';
    empty.textContent =
      'This link offers nothing Viddl can download. It may be a live stream, a page rather than a video, or a site that needs a sign-in.';
    options.append(empty);
  }

  video.hidden = false;
  downloadButton.disabled = none;
  if (!none) {
    downloadButton.focus();
  }
}

function paint(state) {
  if (typeof state.percent === 'number') {
    progressBar.value = Math.max(0, Math.min(100, state.percent));
  } else {
    progressBar.removeAttribute('value');
  }

  const bits = [];
  if (state.status && state.phase !== 'download') {
    bits.push(state.status);
  }
  if (typeof state.percent === 'number') {
    bits.push(Math.round(state.percent) + '%');
  }
  if (state.downloaded) {
    bits.push(
      state.total
        ? sizes(state.downloaded) + ' of ' + sizes(state.total)
        : sizes(state.downloaded)
    );
  }
  if (state.speed) {
    bits.push(state.speed);
  }
  if (state.eta) {
    bits.push(state.eta + ' left');
  }
  numbers.textContent = bits.length ? bits.join(' · ') : state.status || 'Working…';
}

function finish(state) {
  stopPolling();
  jobId = null;

  const kept = state.path;
  if (state.state === 'done' || (state.state === 'stopped' && kept)) {
    progressBox.hidden = true;
    doneBox.hidden = false;
    saved.textContent = (state.alreadyHad ? 'Already had it: ' : 'Saved to ') + kept;
    const bits = [];
    if (state.size) {
      bits.push(sizes(state.size));
    }
    if (state.state === 'stopped') {
      bits.push('stopped early; what had been recorded was kept');
    }
    if (state.attachments.length) {
      bits.push('also saved: ' + state.attachments.join(', '));
    }
    savedNote.textContent = bits.join(' · ');
    savedNote.hidden = bits.length === 0;
    again.focus();
    return;
  }

  progressBox.hidden = true;
  downloadButton.disabled = false;

  if (state.state === 'stopped') {
    showStatus(
      'Stopped. Nothing was saved yet; starting it again carries on from the part that arrived.',
      false
    );
    return;
  }

  const problem = state.error || {};
  showStatus(
    [problem.message, problem.hint].filter(Boolean).join(' ') || 'The download failed.',
    true
  );
}

/* ------------------------------------------------------------------ */
/* following a download                                               */
/* ------------------------------------------------------------------ */

function stopPolling() {
  if (timer !== null) {
    window.clearTimeout(timer);
    timer = null;
  }
}

function poll() {
  stopPolling();
  const id = jobId;
  if (!id) {
    return;
  }
  timer = window.setTimeout(async () => {
    timer = null;
    try {
      const state = await load('api/jobs/' + encodeURIComponent(id));
      if (jobId !== id) {
        return;
      }
      paint(state);
      if (state.state === 'running' || state.state === 'stopping') {
        poll();
        return;
      }
      finish(state);
    } catch (error) {
      showStatus(error.message, true);
      stopPolling();
    }
  }, 400);
}

/* ------------------------------------------------------------------ */
/* the buttons                                                        */
/* ------------------------------------------------------------------ */

form.addEventListener('submit', async (event) => {
  event.preventDefault();
  const text = link.value.trim();
  if (!text) {
    showStatus('Paste a link, or type a name to search for.', true);
    return;
  }
  reset();
  showStatus('Looking up…', false);
  lookUpButton.disabled = true;
  try {
    const found = await post('api/lookup', { url: text });
    showVideo(found);
    if (found.options.length === 0) {
      showStatus('That link has nothing to download.', true);
    }
  } catch (error) {
    showStatus(error.message, true);
  } finally {
    lookUpButton.disabled = false;
  }
});

downloadButton.addEventListener('click', async () => {
  const picked = options.querySelector('input[name="option"]:checked');
  if (!picked || !current) {
    return;
  }
  downloadButton.disabled = true;
  doneBox.hidden = true;
  showStatus('Starting…', false);
  try {
    const started = await post('api/download', { url: current.url, option: picked.value });
    jobId = started.id;
    downloadTitle.textContent =
      started.title + ' — ' + started.option + (started.live ? ' (live)' : '');
    progressBox.hidden = false;
    progressBar.value = 0;
    numbers.textContent = 'Starting…';
    stopButton.disabled = false;
    clearStatus();
    poll();
  } catch (error) {
    downloadButton.disabled = false;
    showStatus(error.message, true);
  }
});

stopButton.addEventListener('click', async () => {
  if (!jobId) {
    return;
  }
  stopButton.disabled = true;
  showStatus('Stopping…', false);
  try {
    await post('api/jobs/' + encodeURIComponent(jobId) + '/stop', {});
  } catch (error) {
    showStatus(error.message, true);
  }
});

again.addEventListener('click', () => {
  reset();
  link.value = '';
  link.focus();
});
