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

  function csrf() {
    var m = document.querySelector('meta[name="csrf-token"]');
    return m ? m.getAttribute('content') : '';
  }
  function storeGet() { try { return sessionStorage.getItem(STORE_KEY); } catch (e) { return null; } }
  function storeSet(v) { try { if (v == null) sessionStorage.removeItem(STORE_KEY); else sessionStorage.setItem(STORE_KEY, String(v)); } catch (e) {} }

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

  var fab = el('button', 'aiw-fab', '💬');
  fab.type = 'button';
  fab.setAttribute('aria-label', 'Open AI assistant');
  fab.setAttribute('aria-expanded', 'false');

  var panel = el('div', 'aiw-panel');
  panel.setAttribute('role', 'dialog');
  panel.setAttribute('aria-label', 'AI assistant');
  var head = el('div', 'aiw-head');
  head.appendChild(el('b', null, 'AI Assistant'));
  var newBtn = el('button', null, 'New chat'); newBtn.type = 'button';
  var closeBtn = el('button', null, '✕'); closeBtn.type = 'button'; closeBtn.setAttribute('aria-label', 'Close');
  head.appendChild(newBtn); head.appendChild(closeBtn);
  var note = el('div', 'aiw-note', 'Your teacher can review these chats. Don\u2019t share passwords or private details.');
  var testBar = el('div', 'aiw-test', 'A test is open: I can explain concepts but I won\u2019t solve its questions.');
  var log = el('div', 'aiw-log');
  log.setAttribute('aria-live', 'polite');
  var form = el('form', 'aiw-form');
  var input = el('textarea'); input.rows = 1; input.maxLength = 2000; input.placeholder = 'Ask a maths question…';
  input.setAttribute('aria-label', 'Message');
  var send = el('button', null, 'Send'); send.type = 'submit';
  form.appendChild(input); form.appendChild(send);
  panel.appendChild(head); panel.appendChild(note); panel.appendChild(testBar); panel.appendChild(log); panel.appendChild(form);
  document.body.appendChild(panel);
  document.body.appendChild(fab);

  function scrollDown() { log.scrollTop = log.scrollHeight; }
  function addMsg(role, text) {
    var m = el('div', 'aiw-msg ' + (role === 'user' ? 'aiw-user' : 'aiw-bot'));
    fillRich(m, text);
    log.appendChild(m); scrollDown();
    return m;
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
  function setBusy(v) { busy = v; send.disabled = v; input.disabled = v; }

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
        res.body.data.forEach(function (m) { addMsg(m.role, m.content); });
        if (!res.body.data.length) greet();
      } else { storeSet(null); greet(); }
    }).catch(function () { greet(); });
  }
  function greet() { addMsg('assistant', 'Hi! Ask me about any maths concept, a mistake you made, or what to revise.'); }

  function refreshStatus() {
    api('/status').then(function (res) {
      if (res.status !== 200 || !res.body || !res.body.ok) return;
      testBar.classList.toggle('on', !!res.body.data.test_mode);
      if (!res.body.data.enabled || res.body.data.viewing_as_teacher) { fab.style.display = 'none'; panel.classList.remove('open'); }
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
    if (conversationId) payload.conversation_id = conversationId;
    else { var h = testHint(); if (h) payload.test_id = h; }
    api('/chat', { method: 'POST', body: JSON.stringify(payload) }).then(function (res) {
      typing.remove(); setBusy(false);
      if (res.status === 200 && res.body && res.body.ok) {
        conversationId = res.body.data.conversation_id; storeSet(conversationId);
        testBar.classList.toggle('on', !!res.body.data.test_mode);
        addMsg('assistant', res.body.data.message.content);
        input.focus();
      } else if (res.status === 404 && conversationId) {
        conversationId = null; storeSet(null); showErr('That chat is no longer available. Tap Retry to start a new one.', true);
      } else {
        showErr(errText(res), res.status !== 403 && res.status !== 429 && res.status !== 413);
      }
    }).catch(function () {
      typing.remove(); setBusy(false);
      showErr('No connection. Check your internet and retry.', true);
    });
  }

  var loaded = false;
  function open() {
    panel.classList.add('open'); fab.setAttribute('aria-expanded', 'true');
    if (!loaded) { loaded = true; loadHistory(); }
    refreshStatus();
    setTimeout(function () { input.focus(); }, 50);
  }
  function close() { panel.classList.remove('open'); fab.setAttribute('aria-expanded', 'false'); fab.focus(); }
  fab.addEventListener('click', function () { panel.classList.contains('open') ? close() : open(); });
  closeBtn.addEventListener('click', close);
  newBtn.addEventListener('click', function () {
    if (busy) return;
    conversationId = null; storeSet(null); log.textContent = ''; greet();
  });
  form.addEventListener('submit', function (e) { e.preventDefault(); submit(input.value, false); });
  input.addEventListener('keydown', function (e) {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); submit(input.value, false); }
    e.stopPropagation(); // never leak keystrokes to the test form's shortcuts
  });
  document.addEventListener('keydown', function (e) { if (e.key === 'Escape' && panel.classList.contains('open')) close(); });
})();
