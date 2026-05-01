"""
Admin web panel for Sigmonions question management.

Set ADMIN_TOKEN env var to a secret string. Access the panel at /admin.
"""
import asyncio
import json
import logging
import os

from aiohttp import web

log = logging.getLogger("sigmonions.admin")

ADMIN_TOKEN: str = os.environ.get("ADMIN_TOKEN", "")


def _auth(request: web.Request) -> bool:
    if not ADMIN_TOKEN:
        return False
    header = request.headers.get("Authorization", "")
    if header.startswith("Bearer ") and header[7:] == ADMIN_TOKEN:
        return True
    return False


# ── HTML SPA ──────────────────────────────────────────────────────────────────

_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Sigmonions Admin</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&display=swap" rel="stylesheet">
<style>
  :root {
    --bg:       #0d0d1a;
    --surface:  #13132a;
    --card:     #1a1a35;
    --card-h:   #202040;
    --border:   #2a2a4a;
    --accent:   #7c3aed;
    --accent2:  #a855f7;
    --success:  #10b981;
    --warning:  #f59e0b;
    --danger:   #ef4444;
    --info:     #3b82f6;
    --txt:      #e2e8f0;
    --txt2:     #94a3b8;
    --txt3:     #64748b;
    --radius:   12px;
    --shadow:   0 4px 24px rgba(0,0,0,.4);
    --glow:     0 0 0 2px rgba(124,58,237,.4);
  }
  *,*::before,*::after { box-sizing:border-box; margin:0; padding:0 }
  html,body { height:100%; font-family:'Inter',sans-serif; background:var(--bg); color:var(--txt); font-size:14px }
  ::-webkit-scrollbar { width:6px }
  ::-webkit-scrollbar-track { background:var(--bg) }
  ::-webkit-scrollbar-thumb { background:var(--border); border-radius:3px }

  /* ── Layout ── */
  #app { min-height:100vh; display:flex; flex-direction:column }

  /* ── Top bar ── */
  header {
    background:var(--surface);
    border-bottom:1px solid var(--border);
    padding:0 24px;
    height:60px;
    display:flex;
    align-items:center;
    gap:16px;
    position:sticky;
    top:0;
    z-index:100;
    backdrop-filter:blur(8px);
  }
  .logo { font-size:1.2rem; font-weight:800; background:linear-gradient(135deg,var(--accent),var(--accent2)); -webkit-background-clip:text; -webkit-text-fill-color:transparent; white-space:nowrap }
  .header-stats { display:flex; gap:12px; margin-left:8px }
  .stat-pill { background:var(--card); border:1px solid var(--border); border-radius:20px; padding:4px 12px; font-size:.78rem; color:var(--txt2); display:flex; align-items:center; gap:5px }
  .stat-pill b { color:var(--accent2) }
  .spacer { flex:1 }
  #search { background:var(--card); border:1px solid var(--border); border-radius:8px; padding:7px 14px; color:var(--txt); font-size:.9rem; width:240px; outline:none; transition:border-color .2s,box-shadow .2s }
  #search:focus { border-color:var(--accent); box-shadow:var(--glow) }
  #search::placeholder { color:var(--txt3) }
  .btn { display:inline-flex; align-items:center; gap:6px; padding:8px 16px; border-radius:8px; font-weight:600; font-size:.85rem; cursor:pointer; border:none; transition:all .15s; white-space:nowrap }
  .btn-primary { background:var(--accent); color:#fff }
  .btn-primary:hover { background:var(--accent2); transform:translateY(-1px); box-shadow:0 4px 12px rgba(124,58,237,.4) }
  .btn-ghost { background:transparent; color:var(--txt2); border:1px solid var(--border) }
  .btn-ghost:hover { background:var(--card); color:var(--txt); border-color:var(--accent) }
  .btn-danger { background:transparent; color:var(--danger); border:1px solid rgba(239,68,68,.3) }
  .btn-danger:hover { background:rgba(239,68,68,.1) }
  .btn-sm { padding:5px 10px; font-size:.78rem }

  /* ── Main content ── */
  main { flex:1; padding:28px 24px; max-width:1400px; width:100%; margin:0 auto }
  .section-header { display:flex; align-items:center; gap:12px; margin-bottom:20px }
  .section-title { font-size:1.05rem; font-weight:700; color:var(--txt) }
  .count-badge { background:var(--accent); color:#fff; border-radius:20px; padding:2px 10px; font-size:.75rem; font-weight:700 }

  /* ── Cards grid ── */
  #grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(320px,1fr)); gap:16px }

  .cat-card {
    background:var(--card);
    border:1px solid var(--border);
    border-radius:var(--radius);
    overflow:hidden;
    transition:transform .2s, box-shadow .2s, border-color .2s;
  }
  .cat-card:hover { transform:translateY(-2px); box-shadow:var(--shadow); border-color:rgba(124,58,237,.3) }

  .card-head {
    padding:14px 16px;
    display:flex;
    align-items:center;
    gap:10px;
    background:var(--card-h);
    border-bottom:1px solid var(--border);
  }
  .cat-name-wrap { flex:1; min-width:0 }
  .cat-name {
    font-weight:700;
    font-size:.95rem;
    cursor:pointer;
    display:flex;
    align-items:center;
    gap:6px;
    border-radius:6px;
    padding:3px 6px;
    margin:-3px -6px;
    transition:background .15s;
  }
  .cat-name:hover { background:rgba(124,58,237,.15) }
  .cat-name .edit-icon { opacity:0; font-size:.7rem; color:var(--accent2); transition:opacity .15s }
  .cat-name:hover .edit-icon { opacity:1 }
  .word-count { background:rgba(124,58,237,.2); color:var(--accent2); border-radius:20px; padding:2px 9px; font-size:.72rem; font-weight:700; white-space:nowrap }

  .card-body { padding:14px 16px }

  /* ── Word chips ── */
  .chips { display:flex; flex-wrap:wrap; gap:6px; margin-bottom:12px }
  .chip {
    background:rgba(255,255,255,.05);
    border:1px solid var(--border);
    border-radius:6px;
    padding:3px 9px;
    font-size:.78rem;
    cursor:pointer;
    display:inline-flex;
    align-items:center;
    gap:5px;
    transition:all .15s;
    user-select:none;
  }
  .chip:hover { background:rgba(124,58,237,.15); border-color:rgba(124,58,237,.4); color:var(--accent2) }
  .chip-input {
    background:rgba(124,58,237,.15);
    border:1px solid var(--accent);
    border-radius:6px;
    padding:3px 9px;
    font-size:.78rem;
    color:var(--txt);
    outline:none;
    min-width:80px;
    max-width:160px;
    font-family:inherit;
    box-shadow:var(--glow);
  }

  /* ── Add words row ── */
  .add-words-row { display:flex; gap:8px; margin-top:4px }
  .add-words-input {
    flex:1;
    background:rgba(255,255,255,.05);
    border:1px solid var(--border);
    border-radius:8px;
    padding:6px 10px;
    color:var(--txt);
    font-size:.82rem;
    outline:none;
    font-family:inherit;
    transition:border-color .2s, box-shadow .2s;
  }
  .add-words-input:focus { border-color:var(--accent); box-shadow:var(--glow) }
  .add-words-input::placeholder { color:var(--txt3) }

  /* ── Inline name edit ── */
  .name-edit-input {
    background:rgba(124,58,237,.15);
    border:1px solid var(--accent);
    border-radius:6px;
    padding:3px 8px;
    color:var(--txt);
    font-size:.95rem;
    font-weight:700;
    font-family:inherit;
    outline:none;
    width:100%;
    box-shadow:var(--glow);
  }

  /* ── Login screen ── */
  #login-screen {
    min-height:100vh;
    display:flex;
    align-items:center;
    justify-content:center;
    background:radial-gradient(ellipse at 50% 0%, rgba(124,58,237,.15) 0%, transparent 60%), var(--bg);
  }
  .login-card {
    background:var(--surface);
    border:1px solid var(--border);
    border-radius:16px;
    padding:40px 48px;
    width:380px;
    box-shadow:0 20px 60px rgba(0,0,0,.5);
    text-align:center;
  }
  .login-logo { font-size:2.5rem; font-weight:800; background:linear-gradient(135deg,var(--accent),var(--accent2)); -webkit-background-clip:text; -webkit-text-fill-color:transparent; margin-bottom:6px }
  .login-sub { color:var(--txt2); margin-bottom:32px; font-size:.9rem }
  .login-field { text-align:left; margin-bottom:20px }
  .login-label { display:block; font-size:.82rem; font-weight:600; color:var(--txt2); margin-bottom:6px; letter-spacing:.04em; text-transform:uppercase }
  .login-input {
    width:100%;
    background:var(--card);
    border:1px solid var(--border);
    border-radius:8px;
    padding:10px 14px;
    color:var(--txt);
    font-size:.95rem;
    font-family:inherit;
    outline:none;
    transition:border-color .2s, box-shadow .2s;
  }
  .login-input:focus { border-color:var(--accent); box-shadow:var(--glow) }
  .login-btn { width:100%; padding:11px; font-size:.95rem }
  .login-error { background:rgba(239,68,68,.15); border:1px solid rgba(239,68,68,.3); border-radius:8px; padding:10px 14px; color:var(--danger); font-size:.85rem; margin-top:14px; display:none }

  /* ── Modal ── */
  .modal-overlay {
    position:fixed; inset:0; background:rgba(0,0,0,.7); z-index:200;
    display:flex; align-items:center; justify-content:center;
    backdrop-filter:blur(4px);
    animation:fadeIn .15s ease;
  }
  .modal {
    background:var(--surface);
    border:1px solid var(--border);
    border-radius:16px;
    padding:32px;
    width:480px;
    max-width:calc(100vw - 32px);
    box-shadow:0 24px 80px rgba(0,0,0,.6);
    animation:slideUp .2s ease;
  }
  .modal-title { font-size:1.1rem; font-weight:700; margin-bottom:20px }
  .form-group { margin-bottom:16px }
  .form-label { display:block; font-size:.82rem; font-weight:600; color:var(--txt2); margin-bottom:6px; letter-spacing:.04em; text-transform:uppercase }
  .form-input, .form-textarea {
    width:100%;
    background:var(--card);
    border:1px solid var(--border);
    border-radius:8px;
    padding:9px 12px;
    color:var(--txt);
    font-size:.9rem;
    font-family:inherit;
    outline:none;
    transition:border-color .2s, box-shadow .2s;
  }
  .form-input:focus, .form-textarea:focus { border-color:var(--accent); box-shadow:var(--glow) }
  .form-textarea { resize:vertical; min-height:100px; line-height:1.5 }
  .form-hint { font-size:.75rem; color:var(--txt3); margin-top:5px }
  .modal-actions { display:flex; gap:10px; justify-content:flex-end; margin-top:24px }

  /* ── Toast ── */
  #toasts { position:fixed; top:72px; right:20px; z-index:300; display:flex; flex-direction:column; gap:8px; pointer-events:none }
  .toast {
    background:var(--surface);
    border:1px solid var(--border);
    border-radius:10px;
    padding:12px 16px;
    min-width:260px;
    max-width:360px;
    display:flex;
    align-items:center;
    gap:10px;
    font-size:.85rem;
    box-shadow:var(--shadow);
    pointer-events:auto;
    animation:slideInRight .25s ease, fadeOut .3s ease 3.7s forwards;
  }
  .toast-icon { font-size:1.1rem; flex-shrink:0 }
  .toast-success { border-left:3px solid var(--success) }
  .toast-error   { border-left:3px solid var(--danger) }
  .toast-info    { border-left:3px solid var(--info) }

  /* ── Empty / Loading ── */
  .empty-state { text-align:center; padding:60px 20px; color:var(--txt2) }
  .empty-state .icon { font-size:3rem; margin-bottom:12px }
  .spinner { display:inline-block; width:20px; height:20px; border:2px solid var(--border); border-top-color:var(--accent); border-radius:50%; animation:spin .6s linear infinite }

  /* ── Animations ── */
  @keyframes fadeIn    { from { opacity:0 }                    to { opacity:1 } }
  @keyframes fadeOut   { from { opacity:1 }                    to { opacity:0; pointer-events:none } }
  @keyframes slideUp   { from { transform:translateY(16px); opacity:0 } to { transform:translateY(0); opacity:1 } }
  @keyframes slideInRight { from { transform:translateX(40px); opacity:0 } to { transform:translateX(0); opacity:1 } }
  @keyframes spin      { to { transform:rotate(360deg) } }

  /* ── No categories message ── */
  .no-match { grid-column:1/-1; text-align:center; padding:40px; color:var(--txt2) }
</style>
</head>
<body>

<!-- LOGIN SCREEN -->
<div id="login-screen">
  <div class="login-card">
    <div class="login-logo">Sigmonions</div>
    <div class="login-sub">Admin Question Manager</div>
    <div class="login-field">
      <label class="login-label" for="token-input">Admin Token</label>
      <input class="login-input" id="token-input" type="password" placeholder="Enter your admin token…" autocomplete="current-password">
    </div>
    <button class="btn btn-primary login-btn" id="login-btn">Sign In</button>
    <div class="login-error" id="login-error">Invalid token. Please try again.</div>
  </div>
</div>

<!-- MAIN APP (hidden until logged in) -->
<div id="app" style="display:none">
  <header>
    <span class="logo">⚡ Sigmonions Admin</span>
    <div class="header-stats">
      <div class="stat-pill">📚 <b id="hdr-cats">—</b> categories</div>
      <div class="stat-pill">💬 <b id="hdr-words">—</b> words</div>
    </div>
    <div class="spacer"></div>
    <input type="search" id="search" placeholder="Search categories…" autocomplete="off">
    <button class="btn btn-primary" id="add-cat-btn">+ New Category</button>
    <button class="btn btn-ghost btn-sm" id="logout-btn">Logout</button>
  </header>

  <main>
    <div class="section-header">
      <span class="section-title">Categories</span>
      <span class="count-badge" id="visible-count">0</span>
    </div>
    <div id="grid">
      <div class="empty-state"><div><span class="spinner"></span></div><div style="margin-top:14px">Loading categories…</div></div>
    </div>
  </main>
</div>

<!-- ADD CATEGORY MODAL -->
<div class="modal-overlay" id="modal-add" style="display:none">
  <div class="modal">
    <div class="modal-title">➕ New Category</div>
    <div class="form-group">
      <label class="form-label">Category Name</label>
      <input class="form-input" id="new-cat-name" placeholder="e.g. Famous Scientists" maxlength="80">
    </div>
    <div class="form-group">
      <label class="form-label">Words / Phrases</label>
      <textarea class="form-textarea" id="new-cat-words" placeholder="Enter words separated by commas or newlines…&#10;e.g. Newton, Einstein, Curie, Darwin"></textarea>
      <div class="form-hint">Minimum 4 words required. Separate with commas or line breaks.</div>
    </div>
    <div class="modal-actions">
      <button class="btn btn-ghost" id="modal-cancel-btn">Cancel</button>
      <button class="btn btn-primary" id="modal-save-btn">Create Category</button>
    </div>
  </div>
</div>

<!-- TOAST CONTAINER -->
<div id="toasts"></div>

<script>
'use strict';
const API = '/admin/api';
let TOKEN = '';
let categories = [];  // [{id, name, word_count, words:[{id,word}], updated_at}]

// ── Auth ──────────────────────────────────────────────────────────────────────
function loadToken() {
  TOKEN = sessionStorage.getItem('adminToken') || '';
  if (TOKEN) showApp();
}

document.getElementById('login-btn').addEventListener('click', tryLogin);
document.getElementById('token-input').addEventListener('keydown', e => { if (e.key === 'Enter') tryLogin() });

async function tryLogin() {
  const t = document.getElementById('token-input').value.trim();
  if (!t) return;
  // Validate by pinging the API
  const res = await fetch(`${API}/categories`, { headers: { Authorization: `Bearer ${t}` } });
  if (res.status === 401) {
    document.getElementById('login-error').style.display = 'block';
    return;
  }
  TOKEN = t;
  sessionStorage.setItem('adminToken', t);
  document.getElementById('login-error').style.display = 'none';
  showApp();
  const data = await res.json();
  renderAll(data);
}

document.getElementById('logout-btn').addEventListener('click', () => {
  sessionStorage.removeItem('adminToken');
  TOKEN = '';
  document.getElementById('app').style.display = 'none';
  document.getElementById('login-screen').style.display = 'flex';
  document.getElementById('token-input').value = '';
});

function showApp() {
  document.getElementById('login-screen').style.display = 'none';
  document.getElementById('app').style.display = 'flex';
  if (!categories.length) loadCategories();
}

// ── API helpers ───────────────────────────────────────────────────────────────
async function api(method, path, body) {
  const opts = {
    method,
    headers: { Authorization: `Bearer ${TOKEN}`, 'Content-Type': 'application/json' },
  };
  if (body !== undefined) opts.body = JSON.stringify(body);
  const res = await fetch(`${API}${path}`, opts);
  if (res.status === 401) { toast('Session expired — please log in again.', 'error'); return null; }
  const json = await res.json().catch(() => null);
  if (!res.ok) { toast(json?.error || `Error ${res.status}`, 'error'); return null; }
  return json;
}

// ── Data loading ──────────────────────────────────────────────────────────────
async function loadCategories() {
  const data = await api('GET', '/categories');
  if (data) renderAll(data);
}

function renderAll(data) {
  categories = data;
  updateHeaderStats();
  renderGrid(getFilteredCats());
}

function getFilteredCats() {
  const q = document.getElementById('search').value.toLowerCase();
  if (!q) return categories;
  return categories.filter(c =>
    c.name.toLowerCase().includes(q) ||
    c.words.some(w => w.word.toLowerCase().includes(q))
  );
}

function updateHeaderStats() {
  document.getElementById('hdr-cats').textContent = categories.length;
  document.getElementById('hdr-words').textContent = categories.reduce((s, c) => s + c.word_count, 0);
}

// ── Grid rendering ────────────────────────────────────────────────────────────
function renderGrid(cats) {
  const grid = document.getElementById('grid');
  document.getElementById('visible-count').textContent = cats.length;
  if (!cats.length) {
    grid.innerHTML = '<div class="no-match">No categories match your search.</div>';
    return;
  }
  grid.innerHTML = '';
  cats.forEach(cat => grid.appendChild(buildCard(cat)));
}

function buildCard(cat) {
  const card = document.createElement('div');
  card.className = 'cat-card';
  card.dataset.id = cat.id;

  // ── Card header ──
  const head = document.createElement('div');
  head.className = 'card-head';

  const nameWrap = document.createElement('div');
  nameWrap.className = 'cat-name-wrap';
  const nameEl = document.createElement('div');
  nameEl.className = 'cat-name';
  nameEl.innerHTML = `<span class="name-text">${esc(cat.name)}</span><span class="edit-icon">✏️</span>`;
  nameEl.title = 'Click to rename';
  nameEl.addEventListener('click', () => startNameEdit(cat, nameEl));
  nameWrap.appendChild(nameEl);

  const badge = document.createElement('span');
  badge.className = 'word-count';
  badge.textContent = `${cat.word_count} words`;

  head.appendChild(nameWrap);
  head.appendChild(badge);

  // ── Card body ──
  const body = document.createElement('div');
  body.className = 'card-body';

  const chips = document.createElement('div');
  chips.className = 'chips';
  cat.words.forEach(w => chips.appendChild(buildChip(cat, w)));

  const addRow = document.createElement('div');
  addRow.className = 'add-words-row';
  const addInput = document.createElement('input');
  addInput.type = 'text';
  addInput.className = 'add-words-input';
  addInput.placeholder = 'Add words (comma-separated)…';
  const addBtn = document.createElement('button');
  addBtn.className = 'btn btn-ghost btn-sm';
  addBtn.textContent = '+ Add';
  addBtn.addEventListener('click', () => submitAddWords(cat, addInput, chips, badge));
  addInput.addEventListener('keydown', e => { if (e.key === 'Enter') submitAddWords(cat, addInput, chips, badge) });

  addRow.appendChild(addInput);
  addRow.appendChild(addBtn);

  body.appendChild(chips);
  body.appendChild(addRow);
  card.appendChild(head);
  card.appendChild(body);
  return card;
}

function buildChip(cat, wordObj) {
  const chip = document.createElement('span');
  chip.className = 'chip';
  chip.textContent = wordObj.word;
  chip.dataset.wordId = wordObj.id;
  chip.title = 'Click to edit';
  chip.addEventListener('click', () => startWordEdit(chip, cat, wordObj));
  return chip;
}

// ── Inline name editing ───────────────────────────────────────────────────────
function startNameEdit(cat, nameEl) {
  if (nameEl.querySelector('input')) return;
  const text = cat.name;
  nameEl.innerHTML = '';
  const input = document.createElement('input');
  input.className = 'name-edit-input';
  input.value = text;
  nameEl.appendChild(input);
  input.focus();
  input.select();

  async function commit() {
    const newName = input.value.trim();
    if (!newName || newName === text) { cancel(); return; }
    const res = await api('PUT', `/categories/${cat.id}`, { name: newName });
    if (!res) { cancel(); return; }
    cat.name = newName;
    nameEl.innerHTML = `<span class="name-text">${esc(newName)}</span><span class="edit-icon">✏️</span>`;
    nameEl.addEventListener('click', () => startNameEdit(cat, nameEl));
    toast(`Renamed to "${newName}"`, 'success');
  }
  function cancel() {
    nameEl.innerHTML = `<span class="name-text">${esc(text)}</span><span class="edit-icon">✏️</span>`;
    nameEl.addEventListener('click', () => startNameEdit(cat, nameEl), { once: true });
  }

  input.addEventListener('keydown', e => {
    if (e.key === 'Enter') commit();
    if (e.key === 'Escape') cancel();
  });
  input.addEventListener('blur', () => setTimeout(commit, 120));
}

// ── Inline word editing ───────────────────────────────────────────────────────
function startWordEdit(chip, cat, wordObj) {
  if (chip.querySelector('input')) return;
  const orig = wordObj.word;
  chip.innerHTML = '';
  const input = document.createElement('input');
  input.type = 'text';
  input.className = 'chip-input';
  input.value = orig;
  chip.appendChild(input);
  input.focus();
  input.select();

  async function commit() {
    const nw = input.value.trim();
    if (!nw || nw === orig) { cancel(); return; }
    const res = await api('PUT', `/words/${wordObj.id}`, { word: nw });
    if (!res) { cancel(); return; }
    wordObj.word = nw;
    chip.innerHTML = '';
    chip.textContent = nw;
    chip.addEventListener('click', () => startWordEdit(chip, cat, wordObj));
    toast(`Updated "${orig}" → "${nw}"`, 'success');
  }
  function cancel() {
    chip.innerHTML = '';
    chip.textContent = orig;
    chip.addEventListener('click', () => startWordEdit(chip, cat, wordObj));
  }

  input.addEventListener('keydown', e => {
    if (e.key === 'Enter') commit();
    if (e.key === 'Escape') cancel();
  });
  input.addEventListener('blur', () => setTimeout(commit, 120));
}

// ── Add words ─────────────────────────────────────────────────────────────────
async function submitAddWords(cat, input, chipsEl, badge) {
  const raw = input.value.trim();
  if (!raw) return;
  const words = raw.split(/[,\n]+/).map(w => w.trim()).filter(Boolean);
  if (!words.length) return;

  input.disabled = true;
  const res = await api('POST', `/categories/${cat.id}/words`, { words });
  input.disabled = false;
  if (!res) return;

  // Reload this category's words
  const full = await api('GET', `/categories`);
  if (!full) return;
  const updated = full.find(c => c.id === cat.id);
  if (!updated) return;

  // Update local data
  Object.assign(cat, updated);
  updateHeaderStats();

  // Re-render chips
  chipsEl.innerHTML = '';
  cat.words.forEach(w => chipsEl.appendChild(buildChip(cat, w)));

  badge.textContent = `${cat.word_count} words`;
  input.value = '';
  toast(`Added ${res.added} new word${res.added !== 1 ? 's' : ''} to "${cat.name}"`, 'success');
}

// ── Add category modal ────────────────────────────────────────────────────────
document.getElementById('add-cat-btn').addEventListener('click', () => {
  document.getElementById('new-cat-name').value = '';
  document.getElementById('new-cat-words').value = '';
  document.getElementById('modal-add').style.display = 'flex';
  setTimeout(() => document.getElementById('new-cat-name').focus(), 50);
});

function closeModal() {
  document.getElementById('modal-add').style.display = 'none';
}
document.getElementById('modal-cancel-btn').addEventListener('click', closeModal);
document.getElementById('modal-add').addEventListener('click', e => { if (e.target === e.currentTarget) closeModal(); });

document.getElementById('modal-save-btn').addEventListener('click', async () => {
  const name = document.getElementById('new-cat-name').value.trim();
  const rawWords = document.getElementById('new-cat-words').value;
  const words = rawWords.split(/[,\n]+/).map(w => w.trim()).filter(Boolean);

  if (!name) { toast('Category name is required.', 'error'); return; }
  if (words.length < 4) { toast('Please provide at least 4 words.', 'error'); return; }

  const btn = document.getElementById('modal-save-btn');
  btn.disabled = true;
  btn.textContent = 'Creating…';

  const res = await api('POST', '/categories', { name, words });
  btn.disabled = false;
  btn.textContent = 'Create Category';

  if (!res) return;
  closeModal();
  toast(`Category "${name}" created with ${words.length} words!`, 'success');
  await loadCategories();
});

// ── Search ────────────────────────────────────────────────────────────────────
document.getElementById('search').addEventListener('input', () => {
  renderGrid(getFilteredCats());
});

// ── Toast notifications ───────────────────────────────────────────────────────
function toast(msg, type = 'info') {
  const icons = { success: '✅', error: '❌', info: 'ℹ️' };
  const el = document.createElement('div');
  el.className = `toast toast-${type}`;
  el.innerHTML = `<span class="toast-icon">${icons[type]}</span><span>${msg}</span>`;
  document.getElementById('toasts').appendChild(el);
  setTimeout(() => el.remove(), 4200);
}

function esc(str) {
  return str.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

// ── Init ──────────────────────────────────────────────────────────────────────
loadToken();
</script>
</body>
</html>"""


# ── Route handlers ────────────────────────────────────────────────────────────

async def handle_admin_ui(request: web.Request) -> web.Response:
    if not ADMIN_TOKEN:
        return web.Response(
            text="<h2>Admin panel disabled — set the ADMIN_TOKEN environment variable.</h2>",
            content_type="text/html",
            status=503,
        )
    return web.Response(text=_HTML, content_type="text/html")


async def handle_get_categories(request: web.Request) -> web.Response:
    if not _auth(request):
        return web.json_response({"error": "Unauthorized"}, status=401)
    from utils.database import get_categories_for_admin
    data = await get_categories_for_admin()
    return web.json_response(data)


async def handle_create_category(request: web.Request) -> web.Response:
    if not _auth(request):
        return web.json_response({"error": "Unauthorized"}, status=401)
    body = await request.json()
    name  = (body.get("name") or "").strip()
    words = [w.strip() for w in body.get("words", []) if w.strip()]
    if not name:
        return web.json_response({"error": "Category name is required."}, status=400)
    if len(words) < 4:
        return web.json_response({"error": "At least 4 words are required."}, status=400)
    from utils.database import create_category
    try:
        cat = await create_category(name, words)
    except Exception as exc:
        if "unique" in str(exc).lower():
            return web.json_response({"error": f'Category "{name}" already exists.'}, status=409)
        raise
    _schedule_reload(request)
    return web.json_response(cat, status=201)


async def handle_update_category(request: web.Request) -> web.Response:
    if not _auth(request):
        return web.json_response({"error": "Unauthorized"}, status=401)
    cat_id = int(request.match_info["id"])
    body   = await request.json()
    name   = (body.get("name") or "").strip()
    if not name:
        return web.json_response({"error": "Name is required."}, status=400)
    from utils.database import update_category_name
    ok = await update_category_name(cat_id, name)
    if not ok:
        return web.json_response({"error": "Category not found."}, status=404)
    _schedule_reload(request)
    return web.json_response({"ok": True})


async def handle_add_words(request: web.Request) -> web.Response:
    if not _auth(request):
        return web.json_response({"error": "Unauthorized"}, status=401)
    cat_id = int(request.match_info["id"])
    body   = await request.json()
    words  = [w.strip() for w in body.get("words", []) if w.strip()]
    if not words:
        return web.json_response({"error": "No words provided."}, status=400)
    from utils.database import add_words_to_category
    added = await add_words_to_category(cat_id, words)
    _schedule_reload(request)
    return web.json_response({"added": added})


async def handle_update_word(request: web.Request) -> web.Response:
    if not _auth(request):
        return web.json_response({"error": "Unauthorized"}, status=401)
    word_id  = int(request.match_info["id"])
    body     = await request.json()
    new_word = (body.get("word") or "").strip()
    if not new_word:
        return web.json_response({"error": "Word is required."}, status=400)
    from utils.database import update_word
    ok = await update_word(word_id, new_word)
    if not ok:
        return web.json_response({"error": "Word not found."}, status=404)
    _schedule_reload(request)
    return web.json_response({"ok": True})


# ── Bot category reload ───────────────────────────────────────────────────────

def _schedule_reload(request: web.Request) -> None:
    bot = request.app.get("bot")
    if bot is None:
        return
    asyncio.create_task(_do_reload(bot))


async def _do_reload(bot) -> None:
    try:
        from utils.database import get_all_categories
        cog = bot.cogs.get("SigmonionCog")
        if cog:
            cats = await get_all_categories()
            cog.engine.set_categories(cats)
            log.info("Game engine categories reloaded live (%d categories).", len(cats))
    except Exception as exc:
        log.error("Live category reload failed: %s", exc)


# ── Setup ─────────────────────────────────────────────────────────────────────

def setup_admin_routes(app: web.Application, bot=None) -> None:
    """Register all admin routes onto an existing aiohttp Application."""
    if bot is not None:
        app["bot"] = bot
    app.router.add_get ("/admin",                       handle_admin_ui)
    app.router.add_get ("/admin/api/categories",        handle_get_categories)
    app.router.add_post("/admin/api/categories",        handle_create_category)
    app.router.add_put ("/admin/api/categories/{id}",   handle_update_category)
    app.router.add_post("/admin/api/categories/{id}/words", handle_add_words)
    app.router.add_put ("/admin/api/words/{id}",        handle_update_word)
    log.info("Admin panel registered at /admin")
