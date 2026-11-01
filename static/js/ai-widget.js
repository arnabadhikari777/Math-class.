/* Student AI assistant widget. Identity comes from the server session; this file
   never sends a student id, name, email or result - only the message text, the
   conversation id it got back from the server and (optionally) a test id hint. */
(function () {
    "use strict";
    if (window.__aiWidgetLoaded) return;
    window.__aiWidgetLoaded = true;

    var API = "/api/v1/ai";
    var STORE_KEY = "aiw_conv";
    var conversationId = null;
    var lastText = "";
    var busy = false;
    var AI_KEY = "aiw_ai";
    var choices = [];
    var chosen = null;

    function csrf() {
        var m = document.querySelector('meta[name="csrf-token"]');
        return m ? m.getAttribute("content") : "";
    }
    function storeGet() {
        try {
            return sessionStorage.getItem(STORE_KEY);
        } catch (e) {
            return null;
        }
    }
    function storeSet(v) {
        try {
            if (v == null) sessionStorage.removeItem(STORE_KEY);
            else sessionStorage.setItem(STORE_KEY, String(v));
        } catch (e) {}
    }

    function icon(name) {
        var i = document.createElement("i");
        i.className = "fa-solid fa-" + name;
        i.setAttribute("aria-hidden", "true");
        return i;
    }

    function el(tag, cls, text) {
        var n = document.createElement(tag);
        if (cls) n.className = cls;
        if (text != null) n.textContent = text;
        return n;
    }

    // Safe mini-formatter: only textContent / createElement (no innerHTML => no XSS).
    // Supports: **bold**, `code`, LaTeX ($..$, $$..$$, \(..\), \[..\]) and | tables |.
    var INLINE_SRC = /\$\$([\s\S]+?)\$\$|\\\[([\s\S]+?)\\\]|\\\(([\s\S]+?)\\\)|\$(?!\s)([^$\n]*?[^\s$])\$(?!\d)|\*\*(?=\S)([\s\S]+?)\*\*|`([^`\n]+)`/
        .source;

    function mathSpan(tex, display) {
        var s = el("span", "aiw-math" + (display ? " aiw-math-d" : ""), tex);
        s.setAttribute("data-tex", tex);
        return s;
    }

    function fillInline(node, text) {
        var re = new RegExp(INLINE_SRC, "g");
        var last = 0,
            m;
        while ((m = re.exec(text))) {
            if (m.index > last)
                node.appendChild(
                    document.createTextNode(text.slice(last, m.index))
                );
            if (m[1] != null) node.appendChild(mathSpan(m[1].trim(), true));
            else if (m[2] != null)
                node.appendChild(mathSpan(m[2].trim(), true));
            else if (m[3] != null)
                node.appendChild(mathSpan(m[3].trim(), false));
            else if (m[4] != null)
                node.appendChild(mathSpan(m[4].trim(), false));
            else if (m[5] != null) {
                var b = el("strong");
                fillInline(b, m[5]);
                node.appendChild(b);
            } else if (m[6] != null) node.appendChild(el("code", null, m[6]));
            last = re.lastIndex;
        }
        if (last < text.length)
            node.appendChild(document.createTextNode(text.slice(last)));
    }

    function isSep(line) {
        return (
            line.indexOf("|") !== -1 &&
            /^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?\s*$/.test(line)
        );
    }
    // split a table row on "|" but never inside math / code
    function splitRow(line) {
        var keep = [];
        var s = line
            .trim()
            .replace(
                /\$\$[\s\S]+?\$\$|\\\([\s\S]+?\\\)|\$(?!\s)[^$\n]*?[^\s$]\$(?!\d)|`[^`\n]+`|\\\|/g,
                function (m) {
                    keep.push(m);
                    return "\u0001" + (keep.length - 1) + "\u0001";
                }
            );
        if (s.charAt(0) === "|") s = s.slice(1);
        if (s.charAt(s.length - 1) === "|") s = s.slice(0, -1);
        return s.split("|").map(function (c) {
            return c
                .replace(/\u0001(\d+)\u0001/g, function (_, n) {
                    return keep[+n] === "\\|" ? "|" : keep[+n];
                })
                .trim();
        });
    }

    function parseBlocks(text) {
        var lines = String(text).replace(/\r\n?/g, "\n").split("\n");
        var blocks = [],
            para = [],
            i = 0;
        function flush() {
            if (para.length) {
                blocks.push({ t: "p", lines: para });
                para = [];
            }
        }
        while (i < lines.length) {
            var line = lines[i],
                tr = line.trim();
            if (!tr) {
                flush();
                i++;
                continue;
            }
            // display math block: $$ ... $$  or  \[ ... \]  (one or many lines)
            var om = tr.match(/^(\$\$|\\\[)/);
            if (om) {
                var close = om[1] === "$$" ? "$$" : "\\]";
                var rest = tr.slice(om[1].length),
                    at = rest.indexOf(close);
                if (at !== -1) {
                    if (!rest.slice(at + close.length).trim()) {
                        flush();
                        blocks.push({
                            t: "math",
                            tex: rest.slice(0, at).trim()
                        });
                        i++;
                        continue;
                    }
                } else {
                    var buf = [rest],
                        j = i + 1,
                        found = false;
                    while (j < lines.length) {
                        var k = lines[j].indexOf(close);
                        if (k !== -1) {
                            buf.push(lines[j].slice(0, k));
                            found = true;
                            break;
                        }
                        buf.push(lines[j]);
                        j++;
                    }
                    if (found) {
                        flush();
                        blocks.push({
                            t: "math",
                            tex: buf.join("\n").trim()
                        });
                        i = j + 1;
                        continue;
                    }
                }
            }
            // table: header row + |---|---| separator row
            if (
                tr.indexOf("|") !== -1 &&
                i + 1 < lines.length &&
                isSep(lines[i + 1])
            ) {
                flush();
                var head = splitRow(line);
                var align = splitRow(lines[i + 1]).map(function (c) {
                    var l = c.charAt(0) === ":",
                        r = c.charAt(c.length - 1) === ":";
                    return l && r ? "center" : r ? "right" : l ? "left" : "";
                });
                var rows = [];
                i += 2;
                while (
                    i < lines.length &&
                    lines[i].trim() &&
                    lines[i].indexOf("|") !== -1
                ) {
                    rows.push(splitRow(lines[i]));
                    i++;
                }
                blocks.push({ t: "table", head: head, align: align, rows: rows });
                continue;
            }
            para.push(line);
            i++;
        }
        flush();
        return blocks;
    }

    function buildTable(b) {
        var wrap = el("div", "aiw-tablewrap aiw-tw");
        var table = document.createElement("table");
        var thead = document.createElement("thead");
        var htr = document.createElement("tr");
        b.head.forEach(function (c, i) {
            var th = el("th");
            if (b.align[i]) th.style.textAlign = b.align[i];
            fillInline(th, c);
            htr.appendChild(th);
        });
        thead.appendChild(htr);
        table.appendChild(thead);
        var tbody = document.createElement("tbody");
        b.rows.forEach(function (r) {
            var row = document.createElement("tr");
            for (var i = 0; i < b.head.length; i++) {
                var td = el("td");
                if (b.align[i]) td.style.textAlign = b.align[i];
                fillInline(td, r[i] || "");
                row.appendChild(td);
            }
            tbody.appendChild(row);
        });
        table.appendChild(tbody);
        wrap.appendChild(table);
        return wrap;
    }

    function fillRich(node, text) {
        parseBlocks(text).forEach(function (b) {
            if (b.t === "math") {
                var w = el("div", "aiw-mathblock aiw-tw");
                w.appendChild(mathSpan(b.tex, true));
                node.appendChild(w);
            } else if (b.t === "table") {
                node.appendChild(buildTable(b));
            } else {
                var p = el("p");
                b.lines.forEach(function (line, i) {
                    if (i) p.appendChild(document.createElement("br"));
                    fillInline(p, line);
                });
                node.appendChild(p);
            }
        });
    }

    // ---- KaTeX: loaded lazily (only when a message really contains math) ----
    var KATEX_BASE = "https://cdnjs.cloudflare.com/ajax/libs/KaTeX/0.16.9/";
    var katexP = null;
    function loadCss(href) {
        return new Promise(function (resolve) {
            if (document.querySelector('link[href*="katex"]')) {
                resolve();
                return;
            }
            var l = document.createElement("link");
            l.rel = "stylesheet";
            l.href = href;
            l.onload = resolve;
            l.onerror = resolve;
            document.head.appendChild(l);
        });
    }
    function ensureKatex() {
        if (window.katex) return Promise.resolve(window.katex);
        if (katexP) return katexP;
        katexP = Promise.all([
            loadCss(KATEX_BASE + "katex.min.css"),
            new Promise(function (resolve, reject) {
                var s = document.createElement("script");
                s.src = KATEX_BASE + "katex.min.js";
                s.async = true;
                s.onload = function () {
                    window.katex
                        ? resolve(window.katex)
                        : reject(new Error("katex missing"));
                };
                s.onerror = function () {
                    reject(new Error("katex load failed"));
                };
                document.head.appendChild(s);
            })
        ]).then(function (r) {
            return r[1];
        });
        katexP.catch(function () {
            katexP = null;
        });
        return katexP;
    }
    function renderMath(node) {
        var list = node.querySelectorAll(".aiw-math:not([data-r])");
        if (!list.length) return Promise.resolve();
        return ensureKatex()
            .then(function (k) {
                Array.prototype.forEach.call(list, function (s) {
                    var tex = s.getAttribute("data-tex") || "";
                    try {
                        s.textContent = "";
                        k.render(tex, s, {
                            displayMode: s.classList.contains("aiw-math-d"),
                            throwOnError: false,
                            strict: "ignore",
                            trust: false
                        });
                    } catch (e) {
                        s.textContent = tex;
                        s.classList.add("aiw-math-raw");
                    }
                    s.setAttribute("data-r", "1");
                });
            })
            .catch(function () {
                // offline / blocked: keep the raw LaTeX text, styled as code
                Array.prototype.forEach.call(list, function (s) {
                    s.classList.add("aiw-math-raw");
                    s.setAttribute("data-r", "1");
                });
            });
    }

    var fab = el("button", "aiw-fab");
    fab.appendChild(icon("comment-dots"));
    fab.type = "button";
    fab.setAttribute("aria-label", "Open AI assistant");
    fab.setAttribute("aria-expanded", "false");

    var panel = el("div", "aiw-panel");
    panel.setAttribute("role", "dialog");
    panel.setAttribute("aria-label", "AI assistant");
    var head = el("div", "aiw-head");
    head.appendChild(el("b", null, "AI Assistant"));
    var histBtn = el("button", null, "History");
    histBtn.type = "button";
    var newBtn = el("button", null, "New chat");
    newBtn.type = "button";
    var closeBtn = el("button", null);
    closeBtn.appendChild(icon("xmark"));
    closeBtn.type = "button";
    closeBtn.setAttribute("aria-label", "Close");
    head.appendChild(histBtn);
    head.appendChild(newBtn);
    head.appendChild(closeBtn);
    var note = el(
        "div",
        "aiw-note",
        "Your teacher can review these chats. Don\u2019t share passwords or private details."
    );
    var testBar = el(
        "div",
        "aiw-test",
        "A test is open: I can explain concepts but I won\u2019t solve its questions."
    );
    var picker = el("div", "aiw-pick");
    picker.style.display = "none";
    var offBar = el(
        "div",
        "aiw-test",
        "AI assistant is not set up yet. Please tell your teacher."
    );
    var log = el("div", "aiw-log");
    log.setAttribute("aria-live", "polite");
    var hist = el("div", "aiw-hist");
    hist.style.display = "none";
    var form = el("form", "aiw-form");
    var input = el("textarea");
    input.rows = 1;
    input.maxLength = 2000;
    input.placeholder = "Ask a maths question…";
    input.setAttribute("aria-label", "Message");
    var send = el("button", null, "Send");
    send.type = "submit";
    form.appendChild(input);
    form.appendChild(send);
    panel.appendChild(head);
    panel.appendChild(picker);
    panel.appendChild(note);
    panel.appendChild(testBar);
    panel.appendChild(offBar);
    panel.appendChild(log);
    panel.appendChild(hist);
    panel.appendChild(form);
    document.body.appendChild(panel);
    document.body.appendChild(fab);

    function scrollDown() {
        log.scrollTop = log.scrollHeight;
    }
    // ---- typewriter: reveals an already-rendered message grapheme by grapheme ----
    var activeTw = null;
    function finishTyping() {
        if (activeTw) activeTw.finish();
    }
    function typewrite(root, msgEl) {
        finishTyping();
        if (
            window.matchMedia &&
            window.matchMedia("(prefers-reduced-motion: reduce)").matches
        )
            return null;
        var seg = null;
        try {
            if (window.Intl && Intl.Segmenter)
                seg = new Intl.Segmenter(undefined, { granularity: "grapheme" });
        } catch (e) {}
        function pieces(str) {
            // grapheme-safe split, so Bengali conjuncts never appear half-drawn
            if (seg)
                return Array.from(seg.segment(str), function (x) {
                    return x.segment;
                });
            return Array.from(str);
        }
        function chainOf(n) {
            var c = [];
            for (var p = n.parentNode; p && p !== root; p = p.parentNode) {
                if (
                    p.tagName === "P" ||
                    p.tagName === "TR" ||
                    p.tagName === "TABLE" ||
                    (p.classList && p.classList.contains("aiw-tw"))
                )
                    c.push(p);
            }
            return c;
        }
        var steps = [],
            total = 0;
        (function walk(n) {
            Array.prototype.forEach.call(n.childNodes, function (c) {
                if (c.nodeType === 3) {
                    if (!c.nodeValue) return;
                    var g = pieces(c.nodeValue);
                    steps.push({ n: c, g: g, p: 0, ch: chainOf(c) });
                    total += g.length;
                } else if (c.nodeType === 1) {
                    if (c.classList.contains("aiw-math") || c.tagName === "BR") {
                        var w = c.tagName === "BR" ? 1 : 4;
                        steps.push({ u: c, w: w, ch: chainOf(c) });
                        total += w;
                    } else walk(c);
                }
            });
        })(root);
        if (!steps.length) return null;

        // hide everything, then uncover it as the "typing" reaches it
        var hideSel = "p, tr, table, .aiw-tw";
        Array.prototype.forEach.call(root.querySelectorAll(hideSel), function (e) {
            e.style.display = "none";
        });
        steps.forEach(function (s) {
            if (s.u) s.u.style.display = "none";
            else s.n.nodeValue = "";
        });

        var caret = el("span", "aiw-caret");
        var cps = Math.max(80, Math.min(420, total / 3.2));
        var idx = 0,
            consumed = 0,
            t0 = null,
            raf = 0,
            finished = false,
            lastStep = null;
        var api = { finish: finish };

        function uncover(s) {
            for (var i = s.ch.length - 1; i >= 0; i--) {
                if (s.ch[i].style.display === "none") {
                    s.ch[i].style.display = "";
                    s.ch[i].classList.add("aiw-in");
                }
            }
        }
        function finish() {
            if (finished) return;
            finished = true;
            cancelAnimationFrame(raf);
            steps.forEach(function (s) {
                if (s.u) s.u.style.display = "";
                else s.n.nodeValue = s.g.join("");
            });
            Array.prototype.forEach.call(
                root.querySelectorAll(hideSel),
                function (e) {
                    e.style.display = "";
                }
            );
            if (caret.parentNode) caret.parentNode.removeChild(caret);
            if (msgEl) msgEl.classList.remove("aiw-streaming");
            if (activeTw === api) activeTw = null;
            scrollDown();
        }
        function frame(ts) {
            if (finished) return;
            if (t0 === null) t0 = ts;
            var stick = log.scrollHeight - log.scrollTop - log.clientHeight < 70;
            var target = Math.floor(((ts - t0) / 1000) * cps) + 1;
            while (consumed < target && idx < steps.length) {
                var s = steps[idx];
                uncover(s);
                if (s.u) {
                    s.u.style.display = "";
                    if (s.u.classList.contains("aiw-math"))
                        s.u.classList.add("aiw-pop");
                    consumed += s.w;
                    idx++;
                } else {
                    var take = Math.min(target - consumed, s.g.length - s.p);
                    s.n.nodeValue += s.g.slice(s.p, s.p + take).join("");
                    s.p += take;
                    consumed += take;
                    if (s.p >= s.g.length) idx++;
                }
                lastStep = s;
            }
            var ref = lastStep && (lastStep.u || lastStep.n);
            if (ref && ref.parentNode)
                ref.parentNode.insertBefore(caret, ref.nextSibling);
            if (stick) scrollDown();
            if (idx >= steps.length) {
                finish();
                return;
            }
            raf = requestAnimationFrame(frame);
        }
        activeTw = api;
        raf = requestAnimationFrame(frame);
        return api;
    }

    function addMsg(role, text, by, note, animate) {
        var isUser = role === "user";
        var m = el("div", "aiw-msg " + (isUser ? "aiw-user" : "aiw-bot"));
        var body = el("div", "aiw-body");
        fillRich(body, text);
        m.appendChild(body);
        if (!isUser && by && by.label)
            m.appendChild(el("div", "aiw-by", by.label));
        if (note) m.appendChild(el("div", "aiw-by", note));
        var typed = !!animate && !isUser;
        if (typed) m.classList.add("aiw-pending");
        log.appendChild(m);
        scrollDown();
        renderMath(body).then(function () {
            if (typed) {
                m.classList.remove("aiw-pending");
                if (m.parentNode) {
                    m.classList.add("aiw-streaming");
                    var tw = typewrite(body, m);
                    if (tw) m.addEventListener("click", tw.finish);
                    else m.classList.remove("aiw-streaming");
                }
            }
            scrollDown();
        });
        return m;
    }
    function labelOf(id) {
        for (var i = 0; i < choices.length; i++)
            if (choices[i].id === id) return choices[i].label;
        return "";
    }
    function renderPicker() {
        picker.textContent = "";
        if (choices.length < 2) {
            picker.style.display = "none";
            return;
        }
        picker.style.display = "flex";
        picker.appendChild(el("span", "aiw-pick-t", "Answer by:"));
        choices.forEach(function (c) {
            var b = el(
                "button",
                "aiw-chip" + (c.id === chosen ? " on" : ""),
                c.label
            );
            b.type = "button";
            b.setAttribute("aria-pressed", c.id === chosen ? "true" : "false");
            b.addEventListener("click", function () {
                chosen = c.id;
                try {
                    sessionStorage.setItem(AI_KEY, chosen);
                } catch (e) {}
                renderPicker();
            });
            picker.appendChild(b);
        });
    }
    function setChoices(list) {
        choices = list || [];
        var saved = null;
        try {
            saved = sessionStorage.getItem(AI_KEY);
        } catch (e) {}
        var ids = choices.map(function (c) {
            return c.id;
        });
        chosen =
            ids.indexOf(chosen) !== -1
                ? chosen
                : ids.indexOf(saved) !== -1
                  ? saved
                  : ids[0] || null;
        renderPicker();
    }
    function clearErr() {
        var e = log.querySelector(".aiw-err");
        if (e) e.remove();
    }
    function showErr(text, retry) {
        clearErr();
        var e = el("div", "aiw-msg aiw-err", text);
        if (retry) {
            var b = el("button", null, "Retry");
            b.type = "button";
            b.addEventListener("click", function () {
                clearErr();
                submit(lastText, true);
            });
            e.appendChild(b);
        }
        log.appendChild(e);
        scrollDown();
    }
    function setBusy(v) {
        busy = v;
        send.disabled = v || aiOff;
        input.disabled = v || aiOff;
    }

    function api(path, opts) {
        opts = opts || {};
        opts.credentials = "same-origin";
        opts.headers = Object.assign(
            { "Content-Type": "application/json", "X-CSRFToken": csrf() },
            opts.headers || {}
        );
        return fetch(API + path, opts).then(function (r) {
            return r
                .json()
                .catch(function () {
                    return null;
                })
                .then(function (j) {
                    return { status: r.status, body: j };
                });
        });
    }
    function errText(res) {
        var e = res.body && res.body.error;
        return e && e.message
            ? e.message
            : res.status === 400
              ? "Please refresh the page and try again."
              : "Something went wrong. Please try again.";
    }

    function loadHistory() {
        var saved = storeGet();
        if (!saved) {
            greet();
            return;
        }
        api("/conversations/" + encodeURIComponent(saved) + "/messages")
            .then(function (res) {
                if (res.status === 200 && res.body && res.body.ok) {
                    conversationId = parseInt(saved, 10);
                    res.body.data.forEach(function (m) {
                        addMsg(m.role, m.content, m.answered_by);
                    });
                    if (!res.body.data.length) greet();
                } else {
                    storeSet(null);
                    greet();
                }
            })
            .catch(function () {
                greet();
            });
    }
    function greet() {
        addMsg(
            "assistant",
            "Hi! Ask me about any maths concept, a mistake you made, or what to revise.",
            null,
            null,
            true
        );
    }

    var aiOff = false;
    function setOff(v) {
        aiOff = v;
        offBar.classList.toggle("on", v);
        send.disabled = v || busy;
        input.disabled = v || busy;
        input.placeholder = v
            ? "AI is not available right now"
            : "Ask a maths question…";
    }
    function refreshStatus() {
        api("/status")
            .then(function (res) {
                if (res.status !== 200 || !res.body || !res.body.ok) return;
                testBar.classList.toggle("on", !!res.body.data.test_mode);
                setChoices(res.body.data.choices);
                setOff(
                    !res.body.data.enabled || !!res.body.data.viewing_as_teacher
                );
            })
            .catch(function () {});
    }

    function testHint() {
        var m = location.pathname.match(/\/test\/(\d+)/);
        return m ? parseInt(m[1], 10) : null;
    }

    function submit(text, isRetry) {
        if (busy) return;
        text = (text || "").trim();
        if (!text) return;
        lastText = text;
        finishTyping();
        if (!isRetry) {
            addMsg("user", text);
            input.value = "";
        }
        setBusy(true);
        var typing = el("div", "aiw-typing", "Thinking…");
        log.appendChild(typing);
        scrollDown();
        var payload = { message: text };
        var requested = chosen;
        if (chosen) payload.ai = chosen;
        if (conversationId) payload.conversation_id = conversationId;
        else {
            var h = testHint();
            if (h) payload.test_id = h;
        }
        api("/chat", { method: "POST", body: JSON.stringify(payload) })
            .then(function (res) {
                typing.remove();
                setBusy(false);
                if (res.status === 200 && res.body && res.body.ok) {
                    conversationId = res.body.data.conversation_id;
                    storeSet(conversationId);
                    testBar.classList.toggle("on", !!res.body.data.test_mode);
                    var note =
                        res.body.data.fallback &&
                        requested &&
                        labelOf(requested)
                            ? labelOf(requested) +
                              " could not answer, so this answer is from " +
                              (res.body.data.message.answered_by
                                  ? res.body.data.message.answered_by.label
                                  : "another AI") +
                              "."
                            : null;
                    addMsg(
                        "assistant",
                        res.body.data.message.content,
                        res.body.data.message.answered_by,
                        note,
                        true
                    );
                    input.focus();
                } else if (res.status === 404 && conversationId) {
                    conversationId = null;
                    storeSet(null);
                    showErr(
                        "That chat is no longer available. Tap Retry to start a new one.",
                        true
                    );
                } else {
                    var code =
                        res.body && res.body.error && res.body.error.code;
                    if (code === "ai_not_configured") setOff(true);
                    var noRetry = [
                        "forbidden",
                        "rate_limited",
                        "too_large",
                        "ai_not_configured",
                        "conversation_full",
                        "invalid_param"
                    ];
                    showErr(errText(res), noRetry.indexOf(code) === -1);
                }
            })
            .catch(function () {
                typing.remove();
                setBusy(false);
                showErr("No connection. Check your internet and retry.", true);
            });
    }

    // ---- My chat history: every past chat of THIS student (server checks ownership) ----
    var histOpen = false;
    function showHistory(v) {
        histOpen = v;
        hist.style.display = v ? "block" : "none";
        log.style.display = v ? "none" : "";
        form.style.display = v ? "none" : "";
        histBtn.textContent = v ? "Back" : "History";
    }
    function openConversation(id) {
        api("/conversations/" + encodeURIComponent(id) + "/messages")
            .then(function (res) {
                if (res.status === 200 && res.body && res.body.ok) {
                    finishTyping();
                    conversationId = id;
                    storeSet(id);
                    log.textContent = "";
                    res.body.data.forEach(function (m) {
                        addMsg(m.role, m.content, m.answered_by);
                    });
                    showHistory(false);
                    scrollDown();
                } else {
                    hist.appendChild(
                        el(
                            "div",
                            "aiw-hist-empty",
                            "Could not open that chat. Please try again."
                        )
                    );
                }
            })
            .catch(function () {
                hist.appendChild(
                    el(
                        "div",
                        "aiw-hist-empty",
                        "No connection. Check your internet."
                    )
                );
            });
    }
    function loadHistoryList(page) {
        if (page === 1) {
            hist.textContent = "";
            hist.appendChild(el("div", "aiw-hist-t", "My previous chats"));
        }
        var old = hist.querySelector(".aiw-hist-more");
        if (old) old.remove();
        api("/conversations?page=" + page + "&per_page=20")
            .then(function (res) {
                if (!(res.status === 200 && res.body && res.body.ok)) {
                    hist.appendChild(
                        el(
                            "div",
                            "aiw-hist-empty",
                            "Could not load your chats."
                        )
                    );
                    return;
                }
                var list = res.body.data || [],
                    meta = res.body.meta || {};
                if (page === 1 && !list.length) {
                    hist.appendChild(
                        el(
                            "div",
                            "aiw-hist-empty",
                            "No chats yet. Ask your first question!"
                        )
                    );
                    return;
                }
                list.forEach(function (c) {
                    var b = el("button", "aiw-hist-item");
                    b.type = "button";
                    b.appendChild(
                        el("span", "aiw-hist-title", c.title || "(untitled)")
                    );
                    b.appendChild(
                        el(
                            "small",
                            null,
                            (c.updated_at || c.created_at || "").slice(0, 16)
                        )
                    );
                    if (c.id === conversationId) b.classList.add("on");
                    b.addEventListener("click", function () {
                        if (!busy) openConversation(c.id);
                    });
                    hist.appendChild(b);
                });
                if (meta.page && meta.pages && meta.page < meta.pages) {
                    var more = el(
                        "button",
                        "aiw-hist-more",
                        "Show older chats"
                    );
                    more.type = "button";
                    more.addEventListener("click", function () {
                        loadHistoryList(meta.page + 1);
                    });
                    hist.appendChild(more);
                }
            })
            .catch(function () {
                hist.appendChild(
                    el(
                        "div",
                        "aiw-hist-empty",
                        "No connection. Check your internet."
                    )
                );
            });
    }
    histBtn.addEventListener("click", function () {
        if (histOpen) {
            showHistory(false);
            return;
        }
        showHistory(true);
        loadHistoryList(1);
    });

    var loaded = false;
    function open() {
        panel.classList.add("open");
        fab.setAttribute("aria-expanded", "true");
        document.body.style.overflow = "hidden";
        if (!loaded) {
            loaded = true;
            loadHistory();
        }
        refreshStatus();
        setTimeout(function () {
            input.focus();
        }, 50);
    }
    function close() {
        finishTyping();
        panel.classList.remove("open");
        fab.setAttribute("aria-expanded", "false");
        document.body.style.overflow = "";
        fab.focus();
    }
    fab.addEventListener("click", function () {
        panel.classList.contains("open") ? close() : open();
    });
    closeBtn.addEventListener("click", close);
    newBtn.addEventListener("click", function () {
        if (busy) return;
        finishTyping();
        conversationId = null;
        storeSet(null);
        log.textContent = "";
        greet();
        showHistory(false);
    });
    form.addEventListener("submit", function (e) {
        e.preventDefault();
        submit(input.value, false);
    });
    input.addEventListener("keydown", function (e) {
        if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault();
            submit(input.value, false);
        }
        if (e.key !== "Escape") e.stopPropagation(); // keep keystrokes away from the page, but let Esc close the chat
    });
    document.addEventListener("keydown", function (e) {
        if (e.key === "Escape" && panel.classList.contains("open")) close();
    });
})();
