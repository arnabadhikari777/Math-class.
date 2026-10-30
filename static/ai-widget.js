/* Student AI assistant widget. Identity comes from the server session; this file
   never sends a student id, name, email or result - only the message text, the
   conversation id it got back from the server and (optionally) a test id hint. */
(function () {
  'use strict';
  if (window.__aiWidgetLoaded) return;
  window.__aiWidgetLoaded = true;

  var API = '/api/v1/ai';
  var STORE_KEY = 'aiw_conv';
  var conversationId = null;
  var lastText = '';
  var busy = false;
  var AI_KEY = 'aiw_ai';
  var choices = [];
  var chosen = null;

  function csrf() {
    var m = document.querySelector('meta[name="csrf-token"]');
    return m ? m.getAttribute('content') : '';
  }
  function storeGet() { try { return sessionStorage.getItem(STORE_KEY); } catch (e) { return null; } }
  function storeSet(v) { try { if (v == null) sessionStorage.removeItem(STORE_KEY); else sessionStorage.setItem(STORE_KEY, String(v)); } catch (e) {} }

  function icon(name) {
    var i = document.createElement('i');
    i.className = 'fa-solid fa-' + name;
    i.setAttribute('aria-hidden', 'true');
    return i;
  }

  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }

  // Safe mini-formatter: only textContent / createElement (no innerHTML => no XSS).
  function fillRich(node, text) {
    String(text).split(/\n{2,}/).forEach(function (block) {
      var p = el('p');
      block.split('\n').forEach(function (line, i) {
        if (i) p.appendChild(document.createElement('br'));
        line.split(/(\*\*[^*]+\*\*|`[^`]+`)/).forEach(function (part) {
          if (!part) return;
          if (part.length > 4 && part.slice(0, 2) === '**' && part.slice(-2) === '**') {
            p.appendChild(el('strong', null, part.slice(2, -2)));
          } else if (part.length > 2 && part[0] === '`' && part.slice(-1) === '`') {
            p.appendChild(el('code', null, part.slice(1, -1)));
          } else {
            p.appendChild(document.createTextNode(part));
          }
        });
      });
      node.appendChild(p);
    });
  }

  var fab = el('button', 'aiw-fab'); fab.appendChild(icon('comment-dots'));
  fab.type = 'button';
  fab.setAttribute('aria-label', 'Open AI assistant');
  fab.setAttribute('aria-expanded', 'false');

  var panel = el('div', 'aiw-panel');
  panel.setAttribute('role', 'dialog');
  panel.setAttribute('aria-label', 'AI assistant');
  var head = el('div', 'aiw-head');
  head.appendChild(el('b', null, 'AI Assistant'));
  var histBtn = el('button', null, 'History'); histBtn.type = 'button';
  var newBtn = el('button', null, 'New chat'); newBtn.type = 'button';
  var closeBtn = el('button', null); closeBtn.appendChild(icon('xmark')); closeBtn.type = 'button'; closeBtn.setAttribute('aria-label', 'Close');
  head.appendChild(histBtn); head.appendChild(newBtn); head.appendChild(closeBtn);
  var note = el('div', 'aiw-note', 'Your teacher can review these chats. Don\u2019t share passwords or private details.');
  var testBar = el('div', 'aiw-test', 'A test is open: I can explain concepts but I won\u2019t solve its questions.');
  var picker = el('div', 'aiw-pick');
  picker.style.display = 'none';
  var offBar = el('div', 'aiw-test', 'AI assistant is not set up yet. Please tell your teacher.');
  var log = el('div', 'aiw-log');
  log.setAttribute('aria-live', 'polite');
  var hist = el('div', 'aiw-hist');
  hist.style.display = 'none';
  var form = el('form', 'aiw-form');
  var input = el('textarea'); input.rows = 1; input.maxLength = 2000; input.placeholder = 'Ask a maths question…';
  input.setAttribute('aria-label', 'Message');
  var send = el('button', null, 'Send'); send.type = 'submit';
  form.appendChild(input); form.appendChild(send);
  panel.appendChild(head); panel.appendChild(picker); panel.appendChild(note); panel.appendChild(testBar); panel.appendChild(offBar); panel.appendChild(log); panel.appendChild(hist); panel.appendChild(form);
  document.body.appendChild(panel);
  document.body.appendChild(fab);

  function scrollDown() { log.scrollTop = log.scrollHeight; }
  function addMsg(role, text, by, note) {
    var m = el('div', 'aiw-msg ' + (role === 'user' ? 'aiw-user' : 'aiw-bot'));
    fillRich(m, text);
    if (role !== 'user' && by && by.label) m.appendChild(el('div', 'aiw-by', by.label));
    if (note) m.appendChild(el('div', 'aiw-by', note));
    log.appendChild(m); scrollDown();
    return m;
  }
  function labelOf(id) {
    for (var i = 0; i < choices.length; i++) if (choices[i].id === id) return choices[i].label;
    return '';
  }
  function renderPicker() {
    picker.textContent = '';
    if (choices.length < 2) { picker.style.display = 'none'; return; }
    picker.style.display = 'flex';
    picker.appendChild(el('span', 'aiw-pick-t', 'Answer by:'));
    choices.forEach(function (c) {
      var b = el('button', 'aiw-chip' + (c.id === chosen ? ' on' : ''), c.label);
      b.type = 'button';
      b.setAttribute('aria-pressed', c.id === chosen ? 'true' : 'false');
      b.addEventListener('click', function () {
        chosen = c.id;
        try { sessionStorage.setItem(AI_KEY, chosen); } catch (e) {}
        renderPicker();
      });
      picker.appendChild(b);
    });
  }
  function setChoices(list) {
    choices = list || [];
    var saved = null;
    try { saved = sessionStorage.getItem(AI_KEY); } catch (e) {}
    var ids = choices.map(function (c) { return c.id; });
    chosen = ids.indexOf(chosen) !== -1 ? chosen : (ids.indexOf(saved) !== -1 ? saved : (ids[0] || null));
    renderPicker();
  }
  function clearErr() { var e = log.querySelector('.aiw-err'); if (e) e.remove(); }
  function showErr(text, retry) {
    clearErr();
    var e = el('div', 'aiw-msg aiw-err', text);
    if (retry) {
      var b = el('button', null, 'Retry'); b.type = 'button';
      b.addEventListener('click', function () { clearErr(); submit(lastText, true); });
      e.appendChild(b);
    }
    log.appendChild(e); scrollDown();
  }
  function setBusy(v) { busy = v; send.disabled = v || aiOff; input.disabled = v || aiOff; }

  function api(path, opts) {
    opts = opts || {};
    opts.credentials = 'same-origin';
    opts.headers = Object.assign({ 'Content-Type': 'application/json', 'X-CSRFToken': csrf() }, opts.headers || {});
    return fetch(API + path, opts).then(function (r) {
      return r.json().catch(function () { return null; }).then(function (j) { return { status: r.status, body: j }; });
    });
  }
  function errText(res) {
    var e = res.body && res.body.error;
    return (e && e.message) ? e.message : (res.status === 400 ? 'Please refresh the page and try again.' : 'Something went wrong. Please try again.');
  }

  function loadHistory() {
    var saved = storeGet();
    if (!saved) { greet(); return; }
    api('/conversations/' + encodeURIComponent(saved) + '/messages').then(function (res) {
      if (res.status === 200 && res.body && res.body.ok) {
        conversationId = parseInt(saved, 10);
        res.body.data.forEach(function (m) { addMsg(m.role, m.content, m.answered_by); });
        if (!res.body.data.length) greet();
      } else { storeSet(null); greet(); }
    }).catch(function () { greet(); });
  }
  function greet() { addMsg('assistant', 'Hi! Ask me about any maths concept, a mistake you made, or what to revise.'); }

  var aiOff = false;
  function setOff(v) {
    aiOff = v;
    offBar.classList.toggle('on', v);
    send.disabled = v || busy;
    input.disabled = v || busy;
    input.placeholder = v ? 'AI is not available right now' : 'Ask a maths question…';
  }
  function refreshStatus() {
    api('/status').then(function (res) {
      if (res.status !== 200 || !res.body || !res.body.ok) return;
      testBar.classList.toggle('on', !!res.body.data.test_mode);
      setChoices(res.body.data.choices);
      setOff(!res.body.data.enabled || !!res.body.data.viewing_as_teacher);
    }).catch(function () {});
  }

  function testHint() {
    var m = location.pathname.match(/\/test\/(\d+)/);
    return m ? parseInt(m[1], 10) : null;
  }

  function submit(text, isRetry) {
    if (busy) return;
    text = (text || '').trim();
    if (!text) return;
    lastText = text;
    if (!isRetry) { addMsg('user', text); input.value = ''; }
    setBusy(true);
    var typing = el('div', 'aiw-typing', 'Thinking…'); log.appendChild(typing); scrollDown();
    var payload = { message: text };
    var requested = chosen;
    if (chosen) payload.ai = chosen;
    if (conversationId) payload.conversation_id = conversationId;
    else { var h = testHint(); if (h) payload.test_id = h; }
    api('/chat', { method: 'POST', body: JSON.stringify(payload) }).then(function (res) {
      typing.remove(); setBusy(false);
      if (res.status === 200 && res.body && res.body.ok) {
        conversationId = res.body.data.conversation_id; storeSet(conversationId);
        testBar.classList.toggle('on', !!res.body.data.test_mode);
        var note = (res.body.data.fallback && requested && labelOf(requested))
          ? labelOf(requested) + ' could not answer, so this answer is from ' + (res.body.data.message.answered_by ? res.body.data.message.answered_by.label : 'another AI') + '.'
          : null;
        addMsg('assistant', res.body.data.message.content, res.body.data.message.answered_by, note);
        input.focus();
      } else if (res.status === 404 && conversationId) {
        conversationId = null; storeSet(null); showErr('That chat is no longer available. Tap Retry to start a new one.', true);
      } else {
        var code = res.body && res.body.error && res.body.error.code;
        if (code === 'ai_not_configured') setOff(true);
        var noRetry = ['forbidden', 'rate_limited', 'too_large', 'ai_not_configured', 'conversation_full', 'invalid_param'];
        showErr(errText(res), noRetry.indexOf(code) === -1);
      }
    }).catch(function () {
      typing.remove(); setBusy(false);
      showErr('No connection. Check your internet and retry.', true);
    });
  }

  // ---- My chat history: every past chat of THIS student (server checks ownership) ----
  var histOpen = false;
  function showHistory(v) {
    histOpen = v;
    hist.style.display = v ? 'block' : 'none';
    log.style.display = v ? 'none' : '';
    form.style.display = v ? 'none' : '';
    histBtn.textContent = v ? 'Back' : 'History';
  }
  function openConversation(id) {
    api('/conversations/' + encodeURIComponent(id) + '/messages').then(function (res) {
      if (res.status === 200 && res.body && res.body.ok) {
        conversationId = id; storeSet(id);
        log.textContent = '';
        res.body.data.forEach(function (m) { addMsg(m.role, m.content, m.answered_by); });
        showHistory(false); scrollDown();
      } else {
        hist.appendChild(el('div', 'aiw-hist-empty', 'Could not open that chat. Please try again.'));
      }
    }).catch(function () { hist.appendChild(el('div', 'aiw-hist-empty', 'No connection. Check your internet.')); });
  }
  function loadHistoryList(page) {
    if (page === 1) { hist.textContent = ''; hist.appendChild(el('div', 'aiw-hist-t', 'My previous chats')); }
    var old = hist.querySelector('.aiw-hist-more'); if (old) old.remove();
    api('/conversations?page=' + page + '&per_page=20').then(function (res) {
      if (!(res.status === 200 && res.body && res.body.ok)) {
        hist.appendChild(el('div', 'aiw-hist-empty', 'Could not load your chats.')); return;
      }
      var list = res.body.data || [], meta = res.body.meta || {};
      if (page === 1 && !list.length) { hist.appendChild(el('div', 'aiw-hist-empty', 'No chats yet. Ask your first question!')); return; }
      list.forEach(function (c) {
        var b = el('button', 'aiw-hist-item'); b.type = 'button';
        b.appendChild(el('span', 'aiw-hist-title', c.title || '(untitled)'));
        b.appendChild(el('small', null, (c.updated_at || c.created_at || '').slice(0, 16)));
        if (c.id === conversationId) b.classList.add('on');
        b.addEventListener('click', function () { if (!busy) openConversation(c.id); });
        hist.appendChild(b);
      });
      if (meta.page && meta.pages && meta.page < meta.pages) {
        var more = el('button', 'aiw-hist-more', 'Show older chats'); more.type = 'button';
        more.addEventListener('click', function () { loadHistoryList(meta.page + 1); });
        hist.appendChild(more);
      }
    }).catch(function () { hist.appendChild(el('div', 'aiw-hist-empty', 'No connection. Check your internet.')); });
  }
  histBtn.addEventListener('click', function () {
    if (histOpen) { showHistory(false); return; }
    showHistory(true); loadHistoryList(1);
  });

  var loaded = false;
  function open() {
    panel.classList.add('open'); fab.setAttribute('aria-expanded', 'true');
    document.body.style.overflow = 'hidden';
    if (!loaded) { loaded = true; loadHistory(); }
    refreshStatus();
    setTimeout(function () { input.focus(); }, 50);
  }
  function close() {
    panel.classList.remove('open'); fab.setAttribute('aria-expanded', 'false');
    document.body.style.overflow = '';
    fab.focus();
  }
  fab.addEventListener('click', function () { panel.classList.contains('open') ? close() : open(); });
  closeBtn.addEventListener('click', close);
  newBtn.addEventListener('click', function () {
    if (busy) return;
    conversationId = null; storeSet(null); log.textContent = ''; greet(); showHistory(false);
  });
  form.addEventListener('submit', function (e) { e.preventDefault(); submit(input.value, false); });
  input.addEventListener('keydown', function (e) {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); submit(input.value, false); }
    if (e.key !== 'Escape') e.stopPropagation(); // keep keystrokes away from the page, but let Esc close the chat
  });
  document.addEventListener('keydown', function (e) { if (e.key === 'Escape' && panel.classList.contains('open')) close(); });
})();
