import { ESPLoader, Transport } from '/vendor/esptool.js';

const $ = (id) => document.getElementById(id);
const PAPER = [0xf4, 0xf1, 0xe8], INK = [0x10, 0x10, 0x10], RED = [0xd0, 0x10, 0x10];
const ESPRESSIF_VID = 0x303a;
const LOG_MAX_NODES = 4000;

// ------------------------------------------------------------------ helpers
async function post(path, body = {}) {
  const r = await fetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  if (!r.ok) throw new Error(`${path}: ${r.status} ${await r.text()}`);
  return r.json();
}

function appendLog(pre, text) {
  const stick = pre.scrollTop + pre.clientHeight >= pre.scrollHeight - 20;
  pre.appendChild(document.createTextNode(text));
  while (pre.childNodes.length > LOG_MAX_NODES) pre.removeChild(pre.firstChild);
  if (stick) pre.scrollTop = pre.scrollHeight;
}
const logs = { build: $('log-build'), emu: $('log-emu-pre'), dev: $('log-dev-pre') };

function pill(id, text, cls) {
  const el = $(id);
  el.textContent = text;
  el.className = 'pill ' + (cls || '');
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// ------------------------------------------------------------------ tabs
document.querySelectorAll('.tabs').forEach((tabs) => {
  tabs.querySelectorAll('.tab').forEach((t) => t.addEventListener('click', () => {
    tabs.querySelectorAll('.tab').forEach((x) => x.classList.toggle('active', x === t));
    const pane = $(t.dataset.tab);
    pane.parentElement.querySelectorAll(':scope > .tabpane').forEach((p) => p.classList.toggle('active', p === pane));
    if (tabs.dataset.group === 'screen') $('btn-target').textContent = t.dataset.tab === 'screen-dev' ? 'device' : 'emulator';
  }));
});
$('log-clear').onclick = () => {
  const active = document.querySelector('.logs .tabpane.active');
  active.querySelectorAll('pre').forEach((p) => (p.textContent = ''));
  if (active.tagName === 'PRE') active.textContent = '';
};

// ------------------------------------------------------------------ e-paper frames
async function frameBytes(frame) {
  const bin = Uint8Array.from(atob(frame.data), (c) => c.charCodeAt(0));
  if (frame.enc === 'raw') return bin;
  if (frame.enc !== 'z') throw new Error(`unknown frame encoding ${frame.enc}`);
  const stream = new Blob([bin]).stream().pipeThrough(new DecompressionStream('deflate'));
  return new Uint8Array(await new Response(stream).arrayBuffer());
}

async function drawFrame(canvas, meta, frame) {
  const { w, h } = frame;
  const bytes = await frameBytes(frame);
  const plane = (w / 8) * h;
  if (bytes.length < plane * 2) throw new Error(`frame too short: ${bytes.length} < ${plane * 2}`);
  canvas.width = w;
  canvas.height = h;
  const ctx = canvas.getContext('2d');
  const img = ctx.createImageData(w, h);
  const px = img.data;
  for (let y = 0, o = 0; y < h; y++) {
    for (let x = 0; x < w; x++, o += 4) {
      const i = y * (w / 8) + (x >> 3), bit = 0x80 >> (x & 7);
      const c = bytes[plane + i] & bit ? RED : bytes[i] & bit ? INK : PAPER;
      px[o] = c[0]; px[o + 1] = c[1]; px[o + 2] = c[2]; px[o + 3] = 255;
    }
  }
  ctx.putImageData(img, 0, 0);
  meta.textContent = `frame ${frame.n ?? ''} · ${frame.t ?? new Date().toLocaleTimeString()} · via ${frame.source}`;
}

// ------------------------------------------------------------------ server events
let buildWaiters = [];
let changedPending = false;
function waitForBuild(target) {
  return new Promise((resolve) => buildWaiters.push({ target, resolve }));
}

function connectEvents() {
  const es = new EventSource('/api/events');
  es.onopen = () => pill('pill-server', 'server: connected', 'ok');
  es.onerror = () => pill('pill-server', 'server: reconnecting…', 'bad');
  es.addEventListener('log', (e) => {
    const { stream, text } = JSON.parse(e.data);
    appendLog(logs[stream] || logs.build, text);
  });
  es.addEventListener('status', (e) => {
    const s = JSON.parse(e.data);
    $('project').textContent = s.project;
    pill('pill-build', `build: ${s.build}`, s.build.startsWith('building') ? 'busy' : s.build === 'failed' ? 'bad' : s.build === 'ok' ? 'ok' : '');
    pill('pill-emu', `emulator: ${s.emu}`, s.emu === 'running' ? 'ok' : s.emu.startsWith('exited') ? 'bad' : '');
    $('watch').checked = s.watch;
    $('hw-flash').disabled = !s.hw_ready;
  });
  es.addEventListener('frame', (e) => {
    drawFrame($('canvas-emu'), $('meta-emu'), JSON.parse(e.data)).catch((err) => appendLog(logs.emu, `[screen] ${err}\n`));
  });
  es.addEventListener('changed', () => { changedPending = true; });
  es.addEventListener('built', (e) => {
    const { target, ok } = JSON.parse(e.data);
    buildWaiters = buildWaiters.filter((w) => (w.target === target ? (w.resolve(ok), false) : true));
    if (target === 'emu' && changedPending) {
      changedPending = false;
      if (ok && $('hw-autopush').checked && port) buildAndFlash().catch(reportFlashError);
    }
  });
}
connectEvents();

// ------------------------------------------------------------------ emulator controls
$('emu-build').onclick = () => post('/api/build', { target: 'emu' });
$('emu-start').onclick = () => post('/api/emu/start');
$('emu-stop').onclick = () => post('/api/emu/stop');
$('emu-snap').onclick = () => post('/api/emu/snapshot');
$('watch').onchange = (e) => post('/api/watch', { on: e.target.checked });

function pressButton(name) {
  if ($('btn-target').textContent === 'device') return deviceSend(`@btn ${name}\r`);
  return post('/api/emu/button', { name });
}
document.querySelectorAll('[data-btn]').forEach((b) => (b.onclick = () => pressButton(b.dataset.btn)));
$('canvas-emu').addEventListener('keydown', (e) => {
  const map = { ArrowLeft: 'menu_minus', ArrowRight: 'menu_plus', a: 'a', b: 'b' };
  if (map[e.key]) { e.preventDefault(); post('/api/emu/button', { name: map[e.key] }); }
});

document.querySelectorAll('form.term').forEach((f) => f.addEventListener('submit', (e) => {
  e.preventDefault();
  const input = f.querySelector('input');
  const text = input.value + '\r';
  input.value = '';
  if (f.dataset.target === 'emu') post('/api/emu/send', { text });
  else deviceSend(text);
}));

// ------------------------------------------------------------------ device: Web Serial monitor
let port = null;
let monitor = null; // { reader, done: Promise }
let devLine = '';
let reconnecting = false;

if (!('serial' in navigator)) {
  $('webserial-note').textContent = 'Web Serial is unavailable here. Use desktop Chrome or Edge on http://127.0.0.1.';
  ['dev-connect', 'hw-flash', 'hw-buildflash'].forEach((id) => ($(id).disabled = true));
} else {
  $('webserial-note').textContent = 'Plug the badge in over USB-C. Flashing works from the running app or from bootloader mode.';
  navigator.serial.addEventListener('connect', (e) => {
    if (reconnecting) { port = e.target; openMonitor().catch(() => {}); }
  });
  navigator.serial.addEventListener('disconnect', (e) => {
    if (e.target === port) {
      monitor = null;
      reconnecting = true; // USB-Serial/JTAG re-enumerates on every reset
      pill('pill-dev', 'device: reconnecting…', 'busy');
    }
  });
}

let devElfSha = null;

// Panic/backtrace addresses -> function + file:line using the local hardware ELF (like idf.py monitor).
async function decodeDeviceLine(line) {
  try {
    const res = await post('/api/decode', { target: 'hw', text: line, sha: devElfSha });
    let out = (res.lines || []).map((l) => `    ${l}\n`).join('');
    if (res.warning) out += `    [decode] ${res.warning}\n`;
    if (out) appendLog(logs.dev, out);
  } catch (err) {
    appendLog(logs.dev, `    [decode] ${err}\n`);
  }
}

function onDeviceText(text) {
  devLine += text;
  let out = '';
  let nl;
  while ((nl = devLine.indexOf('\n')) >= 0) {
    const line = devLine.slice(0, nl).replace(/\r$/, '');
    devLine = devLine.slice(nl + 1);
    if (line.startsWith('EMU_FB ')) {
      const [, w, h, enc, data] = line.split(' ');
      drawFrame($('canvas-dev'), $('meta-dev'), { w: +w, h: +h, enc, data, source: 'device serial' })
        .catch((err) => appendLog(logs.dev, `[screen] ${err}\n`));
      out += '[EMU_FB frame]\n';
      continue;
    }
    out += line + '\n';
    const sha = line.match(/ELF file SHA256:\s*([0-9a-fA-F]+)/);
    if (sha) devElfSha = sha[1];
    if (/Backtrace:|^PC\s*:|Guru Meditation/.test(line)) decodeDeviceLine(line);
    if (/EMU_EPD_REFRESH \d+/.test(line) && $('dev-automirror').checked) deviceSend('@fb\r');
  }
  if (devLine && !'EMU_FB '.startsWith(devLine.slice(0, 7))) { out += devLine; devLine = ''; }
  if (out) appendLog(logs.dev, out);
}

async function openMonitor() {
  for (let i = 0; i < 20; i++) {
    try { await port.open({ baudRate: 115200 }); break; } catch (e) {
      if (/already open/i.test(e.message)) break;
      if (i === 19) throw e;
      await sleep(250);
    }
  }
  reconnecting = false;
  pill('pill-dev', 'device: connected', 'ok');
  const decoder = new TextDecoder();
  const reader = port.readable.getReader();
  const done = (async () => {
    try {
      for (;;) {
        const { value, done: end } = await reader.read();
        if (end) break;
        onDeviceText(decoder.decode(value, { stream: true }));
      }
    } catch (e) {
      appendLog(logs.dev, `\n[serial] ${e.message}\n`);
    } finally {
      reader.releaseLock();
    }
  })();
  monitor = { reader, done };
}

async function closeMonitor() {
  if (!monitor) return;
  const m = monitor;
  monitor = null;
  await m.reader.cancel().catch(() => {});
  await m.done;
  await port.close().catch(() => {});
}

async function deviceSend(text) {
  if (!port?.writable) return appendLog(logs.dev, '[serial] not connected\n');
  const w = port.writable.getWriter();
  try { await w.write(new TextEncoder().encode(text)); } finally { w.releaseLock(); }
}

$('dev-connect').onclick = async () => {
  try {
    port = await navigator.serial.requestPort({
      filters: [{ usbVendorId: ESPRESSIF_VID }, { usbVendorId: 0x10c4 }, { usbVendorId: 0x1a86 }, { usbVendorId: 0x0403 }],
    });
    await openMonitor();
    document.querySelector('[data-tab="log-dev"]').click();
  } catch (e) {
    if (e.name !== 'NotFoundError') appendLog(logs.dev, `[serial] ${e.message}\n`);
  }
};
$('dev-disconnect').onclick = async () => {
  reconnecting = false;
  await closeMonitor();
  port = null;
  pill('pill-dev', 'device: not connected');
};
$('dev-reset').onclick = async () => {
  if (!port) return;
  reconnecting = true;
  await port.setSignals({ dataTerminalReady: false, requestToSend: true });
  await sleep(100);
  await port.setSignals({ requestToSend: false });
};
$('dev-mirror').onclick = () => {
  document.querySelector('[data-tab="screen-dev"]').click();
  deviceSend('@fb\r');
};

// ------------------------------------------------------------------ device: compile + flash (esptool-js)
let skipConfirm = false;
const reportFlashError = (e) => {
  $('flash-status').textContent = `flash failed: ${e.message || e}`;
  appendLog(logs.dev, `\n[flash] ${e.stack || e}\n`);
  $('flash-progress').hidden = true;
};

async function confirmFlash(manifest) {
  if (skipConfirm) return true;
  const list = $('flash-files');
  list.innerHTML = '';
  for (const f of manifest.files) {
    const li = document.createElement('li');
    li.textContent = `0x${f.address.toString(16)}  ${f.path}  (${(f.size / 1024).toFixed(0)} KB)`;
    list.appendChild(li);
  }
  const dlg = $('flash-confirm');
  dlg.showModal();
  const answer = await new Promise((r) => dlg.addEventListener('close', () => r(dlg.returnValue), { once: true }));
  if (answer === 'ok' && $('flash-noask').checked) skipConfirm = true;
  return answer === 'ok';
}

async function flashDevice() {
  if (!port) throw new Error('connect the device first');
  const fs = $('hw-fs').checked ? 1 : 0;
  const r = await fetch(`/api/manifest?fs=${fs}`);
  const manifest = await r.json();
  if (!r.ok) throw new Error(manifest.error);
  if (!(await confirmFlash(manifest))) return;

  $('flash-progress').hidden = false;
  $('flash-progress').value = 0;
  $('flash-status').textContent = 'downloading images…';
  const fileArray = [];
  for (const f of manifest.files) {
    const data = new Uint8Array(await (await fetch(`/api/fw/${f.path}`)).arrayBuffer());
    fileArray.push({ address: f.address, data });
  }
  const total = fileArray.reduce((n, f) => n + f.data.length, 0);
  const before = fileArray.map((_, i) => fileArray.slice(0, i).reduce((n, f) => n + f.data.length, 0));

  await closeMonitor();
  const terminal = {
    clean() {},
    writeLine(s) { appendLog(logs.dev, `[esptool] ${s}\n`); },
    write(s) { appendLog(logs.dev, s); },
  };
  const transport = new Transport(port, false);
  try {
    $('flash-status').textContent = 'connecting to bootloader…';
    const loader = new ESPLoader({ transport, baudrate: 921600, terminal, debugLogging: false });
    const chip = await loader.main();
    if (!/ESP32-S3/i.test(chip)) throw new Error(`expected ESP32-S3, found ${chip}; not flashing`);
    $('flash-status').textContent = `flashing ${chip} (${(total / 1024).toFixed(0)} KB)…`;
    await loader.writeFlash({
      fileArray, flashSize: 'keep', flashMode: 'keep', flashFreq: 'keep', eraseAll: false, compress: true,
      reportProgress: (i, written) => { $('flash-progress').value = ((before[i] + written) / total) * 100; },
    });
    $('flash-status').textContent = 'resetting…';
    reconnecting = true;
    await loader.after('hard_reset');
  } finally {
    await transport.disconnect().catch(() => {});
  }
  $('flash-progress').value = 100;
  $('flash-status').textContent = `flashed build from ${manifest.built} at ${new Date().toLocaleTimeString()}`;
  await sleep(500);
  if (!monitor) await openMonitor().catch(() => appendLog(logs.dev, '[serial] waiting for the badge to re-enumerate…\n'));
}

async function buildHw() {
  $('flash-status').textContent = 'compiling for the badge…';
  document.querySelector('[data-tab="log-build"]').click();
  const done = waitForBuild('hw');
  await post('/api/build', { target: 'hw', fs: $('hw-fs').checked });
  const ok = await done;
  $('flash-status').textContent = ok ? 'badge build OK' : 'badge build FAILED (see Build log)';
  return ok;
}

async function buildAndFlash() {
  if (!port) throw new Error('connect the device first');
  if (await buildHw()) await flashDevice();
}

$('hw-build').onclick = () => buildHw().catch(reportFlashError);
$('hw-flash').onclick = () => flashDevice().catch(reportFlashError);
$('hw-buildflash').onclick = () => buildAndFlash().catch(reportFlashError);
