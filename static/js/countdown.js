/* JEE Main countdowns.
   Every element with [data-countdown="<ISO datetime with offset>"] is kept live.
   Modes (data-cd-mode):
     chip  -> compact text, e.g. "83d 04h 12m"
     full  -> fills child [data-unit="days|hours|minutes|seconds"] cells
   Optional: data-cd-window-days="10" (how long the exam window stays "live"). */
(function () {
  'use strict';

  var DAY = 86400000, HOUR = 3600000, MIN = 60000;
  var nodes = Array.prototype.slice.call(document.querySelectorAll('[data-countdown]'));
  if (!nodes.length) return;

  function pad(n) { return n < 10 ? '0' + n : String(n); }

  function stateOf(node, now) {
    var target = Date.parse(node.getAttribute('data-countdown'));
    var win = parseFloat(node.getAttribute('data-cd-window-days') || '10') * DAY;
    if (isNaN(target)) return { kind: 'invalid' };
    var diff = target - now;
    if (diff > 0) return { kind: 'before', diff: diff };
    if (-diff <= win) return { kind: 'live' };
    return { kind: 'done' };
  }

  function render(node, now) {
    var s = stateOf(node, now);
    var mode = node.getAttribute('data-cd-mode') || 'chip';
    var card = node.closest('.cd-card') || node;
    card.setAttribute('data-state', s.kind);

    if (mode === 'chip') {
      var out = node.querySelector('.cd-text') || node;
      if (s.kind === 'before') {
        var d = Math.floor(s.diff / DAY);
        var h = Math.floor((s.diff % DAY) / HOUR);
        var m = Math.floor((s.diff % HOUR) / MIN);
        out.textContent = d > 0 ? d + 'd ' + pad(h) + 'h ' + pad(m) + 'm' : pad(h) + 'h ' + pad(m) + 'm';
      } else if (s.kind === 'live') {
        out.textContent = 'Live now';
      } else {
        out.textContent = 'Done';
        node.hidden = true;
      }
      return;
    }

    // full mode
    var cells = {
      days: node.querySelector('[data-unit="days"]'),
      hours: node.querySelector('[data-unit="hours"]'),
      minutes: node.querySelector('[data-unit="minutes"]'),
      seconds: node.querySelector('[data-unit="seconds"]')
    };
    var msg = card.querySelector('.cd-msg');
    var weeks = card.querySelector('.cd-weeks');
    if (s.kind === 'before') {
      var dd = Math.floor(s.diff / DAY);
      var hh = Math.floor((s.diff % DAY) / HOUR);
      var mm = Math.floor((s.diff % HOUR) / MIN);
      var ss = Math.floor((s.diff % MIN) / 1000);
      if (cells.days) cells.days.textContent = dd;
      if (cells.hours) cells.hours.textContent = pad(hh);
      if (cells.minutes) cells.minutes.textContent = pad(mm);
      if (cells.seconds) cells.seconds.textContent = pad(ss);
      if (weeks) weeks.textContent = '≈ ' + (dd / 7).toFixed(1) + ' weeks to go';
      if (msg) msg.textContent = dd <= 7 ? 'Final week: revise, rest well, stay calm.'
        : dd <= 30 ? 'Last month: full-length mock tests + error log revision.'
        : dd <= 90 ? 'Strengthen weak chapters and practise timed sets daily.'
        : 'Plenty of time: build concepts, then speed.';
    } else if (s.kind === 'live') {
      ['days', 'hours', 'minutes', 'seconds'].forEach(function (k) { if (cells[k]) cells[k].textContent = '00'; });
      if (weeks) weeks.textContent = '';
      if (msg) msg.textContent = 'The exam window is open. All the best!';
    } else {
      ['days', 'hours', 'minutes', 'seconds'].forEach(function (k) { if (cells[k]) cells[k].textContent = '–'; });
      if (weeks) weeks.textContent = '';
      if (msg) msg.textContent = 'This session is over.';
    }
  }

  function tick() {
    var now = Date.now();
    nodes.forEach(function (n) { render(n, now); });
  }

  tick();
  setInterval(tick, 1000);
  document.addEventListener('visibilitychange', function () { if (!document.hidden) tick(); });
})();
