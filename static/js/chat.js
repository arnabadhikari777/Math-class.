/* Arnab Math Class - student chat.
   Loaded on every student page:
     * everywhere   -> a small heartbeat/badge poll (keeps you "online" and marks incoming messages "delivered")
     * on /chat      -> the full chat screen (chat list, people, requests, room with sent/delivered/seen).
   All data comes from /api/chat/*; the server re-checks every request. No inline handlers (CSP). */
(function () {
    "use strict";
    var CSRF = (document.querySelector('meta[name="csrf-token"]') || {}).content || "";
    var app = document.getElementById("chatApp");
    var READONLY = app && app.getAttribute("data-readonly") === "1";

    function api(url, opts) {
        opts = opts || {};
        var init = { credentials: "same-origin", cache: "no-store", headers: { Accept: "application/json" } };
        if (opts.body !== undefined) {
            init.method = "POST";
            init.headers["Content-Type"] = "application/json";
            init.headers["X-CSRFToken"] = CSRF;
            init.body = JSON.stringify(opts.body);
        } else if (opts.post) {
            init.method = "POST";
            init.headers["X-CSRFToken"] = CSRF;
        }
        return fetch(url, init)
            .then(function (r) { return r.json().catch(function () { return { ok: false, error: { message: "Server error" } }; }); })
            .catch(function () { return { ok: false, error: { message: "No connection" }, offline: true }; });
    }
    function el(tag, cls, text) {
        var e = document.createElement(tag);
        if (cls) e.className = cls;
        if (text != null) e.textContent = text;
        return e;
    }
    function ic(name) { var i = el("i", "fa-solid " + name); i.setAttribute("aria-hidden", "true"); return i; }
    function toast(kind, msg) { if (window.AMC && window.AMC.toast) window.AMC.toast(kind, msg); }
    function visible() { return document.visibilityState !== "hidden"; }

    /* ---------- time helpers ---------- */
    function pad(n) { return (n < 10 ? "0" : "") + n; }
    function clock(iso) { var d = new Date(iso); return isNaN(d) ? "" : pad(d.getHours()) + ":" + pad(d.getMinutes()); }
    function dayKey(iso) { var d = new Date(iso); return d.getFullYear() + "-" + d.getMonth() + "-" + d.getDate(); }
    function dayLabel(iso) {
        var d = new Date(iso), t = new Date(), y = new Date(Date.now() - 864e5);
        if (dayKey(iso) === dayKey(t.toISOString())) return "Today";
        if (dayKey(iso) === dayKey(y.toISOString())) return "Yesterday";
        return d.toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
    }
    function ago(iso) {
        var s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
        if (s < 60) return "just now";
        if (s < 3600) return Math.floor(s / 60) + " min ago";
        if (dayKey(iso) === dayKey(new Date().toISOString())) return "today at " + clock(iso);
        if (dayKey(iso) === dayKey(new Date(Date.now() - 864e5).toISOString())) return "yesterday at " + clock(iso);
        return new Date(iso).toLocaleDateString(undefined, { day: "numeric", month: "short" }) + " at " + clock(iso);
    }
    function initials(name) {
        var p = String(name || "?").trim().split(/\s+/);
        return ((p[0] || "?")[0] + (p.length > 1 ? p[p.length - 1][0] : "")).toUpperCase();
    }
    function tick(status) {            // sent = one grey tick, delivered = two grey, seen = two blue
        var s = el("span", "tick " + status);
        s.title = status;
        s.appendChild(ic(status === "sent" ? "fa-check" : "fa-check-double"));
        return s;
    }
    function uuid() {
        if (window.crypto && crypto.randomUUID) return crypto.randomUUID();
        return "m" + Date.now().toString(36) + Math.random().toString(36).slice(2, 12);
    }

    /* ---------- nav badge + heartbeat (every student page) ---------- */
    var navBadges = [];
    function paintBadges(n) {
        document.querySelectorAll('a[data-chat-link]').forEach(function (a) {
            var b = a.querySelector(".chat-nav-badge");
            if (n > 0) {
                if (!b) { b = el("span", "chat-nav-badge"); a.appendChild(b); }
                b.textContent = n > 99 ? "99+" : String(n);
            } else if (b) { b.remove(); }
        });
    }
    function summaryTick() {
        if (!visible()) return;
        api("/api/chat/summary").then(function (r) {
            if (r && r.ok) paintBadges((r.data.messages || 0) + (r.data.requests || 0));
        });
    }
    if (!app) {
        summaryTick();
        setInterval(summaryTick, 20000);
        document.addEventListener("visibilitychange", function () { if (visible()) summaryTick(); });
        return;
    }

    /* ======================= full chat screen ======================= */
    var list = document.getElementById("chatList"), side = document.getElementById("chatSide");
    var roomBox = document.getElementById("chatRoom"), emptyBox = document.getElementById("chatEmpty");
    var searchBox = document.getElementById("chatSearch"), searchInput = document.getElementById("chatSearchInput");
    var moreBox = document.getElementById("chatMore");
    var state = { tab: app.getAttribute("data-tab") || "chats", page: 1, q: "", conv: null, chats: [], requests: [] };
    var listTimer = null;

    function setView(roomOpen) {
        app.classList.toggle("room-open", roomOpen);
        roomBox.hidden = !roomOpen;
        emptyBox.hidden = roomOpen;
    }
    function setTab(tab) {
        state.tab = tab;
        state.page = 1;
        side.querySelectorAll(".chat-tabs button").forEach(function (b) { b.classList.toggle("on", b.getAttribute("data-tab") === tab); });
        searchBox.hidden = tab !== "people";
        loadList();
    }
    side.querySelectorAll(".chat-tabs button").forEach(function (b) {
        b.addEventListener("click", function () { setTab(b.getAttribute("data-tab")); });
    });
    var qTimer = null;
    searchInput.addEventListener("input", function () {
        clearTimeout(qTimer);
        qTimer = setTimeout(function () { state.q = searchInput.value; state.page = 1; loadList(); }, 300);
    });

    function badge(id, n) {
        var b = document.getElementById(id);
        if (!b) return;
        b.hidden = !n;
        b.textContent = n > 99 ? "99+" : String(n);
    }
    function emptyNote(icon, text) {
        var d = el("div", "chat-note");
        d.appendChild(ic(icon));
        d.appendChild(el("p", null, text));
        return d;
    }
    function avatar(name, online) {
        var a = el("div", "avatar", initials(name));
        if (online) a.appendChild(el("span", "dot"));
        return a;
    }

    function renderChats() {
        list.textContent = "";
        if (!state.chats.length) {
            list.appendChild(emptyNote("fa-comments", "No chats yet. Open People and say hello."));
            return;
        }
        state.chats.forEach(function (c) {
            var row = el("button", "chat-row" + (c.id === state.conv ? " active" : ""));
            row.type = "button";
            row.appendChild(avatar(c.peer.name, c.online));
            var mid = el("div", "mid");
            var top = el("div", "top");
            top.appendChild(el("strong", null, c.peer.name));
            top.appendChild(el("span", "when", c.last_at ? clock(c.last_at) : ""));
            mid.appendChild(top);
            var bot = el("div", "bot");
            if (c.last_mine && c.last_status) bot.appendChild(tick(c.last_status));
            bot.appendChild(el("span", "txt", c.last_text || "Say hello"));
            if (c.unread) bot.appendChild(el("span", "cbadge", String(c.unread)));
            mid.appendChild(bot);
            row.appendChild(mid);
            row.addEventListener("click", function () { openRoom(c.id); });
            list.appendChild(row);
        });
    }

    function renderRequests() {
        list.textContent = "";
        if (!state.requests.length) {
            list.appendChild(emptyNote("fa-user-clock", "No chat requests waiting."));
            return;
        }
        state.requests.forEach(function (r) {
            var row = el("div", "chat-row static");
            row.appendChild(avatar(r.name, false));
            var mid = el("div", "mid");
            mid.appendChild(el("strong", null, r.name));
            mid.appendChild(el("div", "bot", "wants to chat with you"));
            row.appendChild(mid);
            var acts = el("div", "acts");
            [["accept", "Accept", "btn btn-sm btn-primary"], ["reject", "Decline", "btn btn-sm btn-outline"]].forEach(function (a) {
                var b = el("button", a[2], a[1]);
                b.type = "button";
                b.disabled = READONLY;
                b.addEventListener("click", function () {
                    b.disabled = true;
                    api("/api/chat/requests/" + r.id + "/" + a[0], { post: true }).then(function (res) {
                        if (res.ok) {
                            if (a[0] === "accept" && res.data.conversation_id) openRoom(res.data.conversation_id);
                            loadList();
                        } else { toast("error", (res.error || {}).message || "Could not answer"); b.disabled = false; }
                    });
                });
                acts.appendChild(b);
            });
            row.appendChild(acts);
            list.appendChild(row);
        });
    }

    function renderPeople(people, hasNext) {
        if (state.page === 1) list.textContent = "";
        if (!people.length && state.page === 1) {
            list.appendChild(emptyNote("fa-user-group", state.q ? "No student found." : "No other students yet."));
        }
        people.forEach(function (p) {
            var row = el("div", "chat-row static");
            row.appendChild(avatar(p.name, false));
            var mid = el("div", "mid");
            var top = el("div", "top");
            top.appendChild(el("strong", null, p.name));
            if (!p.is_public) { var lock = ic("fa-lock"); lock.classList.add("lock"); lock.title = "Private account"; top.appendChild(lock); }
            mid.appendChild(top);
            mid.appendChild(el("div", "bot", p.class_name ? "Class " + p.class_name : ""));
            row.appendChild(mid);
            var b = el("button", "btn btn-sm btn-primary");
            b.type = "button";
            if (p.conversation_id) { b.textContent = "Open"; b.addEventListener("click", function () { openRoom(p.conversation_id); }); }
            else if (p.request_received) { b.textContent = "See request"; b.addEventListener("click", function () { setTab("requests"); }); }
            else if (p.request_sent) { b.textContent = "Requested"; b.disabled = true; b.className = "btn btn-sm btn-outline"; }
            else if (!p.is_public) {
                b.textContent = "Request";
                b.className = "btn btn-sm btn-outline";
                b.disabled = READONLY;
                b.addEventListener("click", function () {
                    b.disabled = true;
                    api("/api/chat/requests", { body: { student_id: p.id } }).then(function (r) {
                        if (r.ok) { b.textContent = "Requested"; toast("success", "Request sent."); }
                        else { b.disabled = false; toast("error", (r.error || {}).message || "Could not send"); }
                    });
                });
            } else {
                b.textContent = "Message";
                b.disabled = READONLY;
                b.addEventListener("click", function () {
                    b.disabled = true;
                    api("/api/chat/start", { body: { student_id: p.id } }).then(function (r) {
                        if (r.ok) openRoom(r.data.conversation_id);
                        else { b.disabled = false; toast("error", (r.error || {}).message || "Could not open"); }
                    });
                });
            }
            row.appendChild(b);
            list.appendChild(row);
        });
        moreBox.hidden = !hasNext;
    }

    function loadList() {
        moreBox.hidden = true;
        if (state.tab === "people") {
            api("/api/chat/people?page=" + state.page + "&q=" + encodeURIComponent(state.q)).then(function (r) {
                if (r.ok) renderPeople(r.data.people, r.data.has_next);
            });
            return;
        }
        api("/api/chat/chats").then(function (r) {
            if (!r.ok) { if (!r.offline) toast("error", (r.error || {}).message || "Could not load chats"); return; }
            state.chats = r.data.chats;
            state.requests = r.data.requests;
            badge("tabBadgeChats", state.chats.reduce(function (n, c) { return n + c.unread; }, 0));
            badge("tabBadgeReq", state.requests.length);
            if (state.tab === "requests") renderRequests(); else renderChats();
        });
    }
    document.getElementById("chatMoreBtn").addEventListener("click", function () { state.page += 1; loadList(); });
    function startListTimer() {
        clearInterval(listTimer);
        listTimer = setInterval(function () { if (visible() && state.tab !== "people") loadList(); }, 8000);
    }

    /* ---------- room ---------- */
    var msgsBox = document.getElementById("roomMsgs"), items = document.getElementById("roomItems");
    var form = document.getElementById("roomForm"), text = document.getElementById("roomText");
    var typingBox = document.getElementById("roomTyping"), older = document.getElementById("roomOlder");
    var room = null, pollTimer = null, typingSent = 0, typingStop = null;

    function resetRoom(cid) {
        room = { id: cid, byId: {}, after: 0, firstId: 0, since: null, lastDay: "", peerName: "", hasMore: false, busy: false, seenSent: 0 };
        items.textContent = "";
    }
    function nearBottom() { return msgsBox.scrollHeight - msgsBox.scrollTop - msgsBox.clientHeight < 120; }
    function toBottom() { msgsBox.scrollTop = msgsBox.scrollHeight; }

    function bubble(m) {
        var b = el("div", "msg " + (m.mine ? "mine" : "theirs") + (m.deleted ? " deleted" : ""));
        b.setAttribute("data-id", m.id);
        var body = el("div", "body");
        body.textContent = m.deleted ? "This message was deleted" : m.body;
        b.appendChild(body);
        var meta = el("div", "meta");
        meta.appendChild(el("span", null, clock(m.created_at)));
        if (m.mine && !m.deleted) meta.appendChild(tick(m.status));
        b.appendChild(meta);
        if (m.mine && !m.deleted && !READONLY && typeof m.id === "number") {
            var del = el("button", "msg-del");
            del.type = "button";
            del.title = "Delete for everyone";
            del.setAttribute("aria-label", "Delete for everyone");
            del.appendChild(ic("fa-trash"));
            del.addEventListener("click", function () {
                if (!window.confirm("Delete this message for everyone?")) return;
                api("/api/chat/messages/" + m.id + "/delete", { post: true }).then(function (r) {
                    if (r.ok) { m.deleted = true; m.body = ""; paint(m); }
                    else toast("error", (r.error || {}).message || "Could not delete");
                });
            });
            b.appendChild(del);
        }
        return b;
    }
    function paint(m) {
        var old = items.querySelector('.msg[data-id="' + m.id + '"]');
        var nb = bubble(m);
        if (old) old.parentNode.replaceChild(nb, old);
        room.byId[m.id] = m;
    }
    function addMessage(m, prepend) {
        if (room.byId[m.id] && !prepend) { paint(m); return; }
        room.byId[m.id] = m;
        var node = bubble(m);
        if (prepend) {
            items.insertBefore(node, items.firstChild);
            return;
        }
        var k = dayKey(m.created_at);
        if (k !== room.lastDay) { items.appendChild(el("div", "day", dayLabel(m.created_at))); room.lastDay = k; }
        items.appendChild(node);
        if (typeof m.id === "number" && m.id > room.after) room.after = m.id;
    }
    function renderPeer(p) {
        if (!p) return;
        var st = document.getElementById("roomStatus");
        if (p.name) { document.getElementById("roomName").textContent = p.name; room.peerName = p.name; }
        if (p.name) { var av = document.getElementById("roomAvatar"); av.textContent = initials(p.name); }
        st.classList.toggle("online", !!p.online);
        st.textContent = p.online ? "online" : (p.last_seen ? "last seen " + ago(p.last_seen) : "offline");
        typingBox.hidden = !p.typing;
        typingBox.textContent = (room.peerName || "") + " is typing…";
        if (p.typing && nearBottom()) toBottom();
    }
    function markSeenIfNeeded() {
        if (READONLY || !room || !visible()) return;
        var maxIn = 0;
        Object.keys(room.byId).forEach(function (k) {
            var m = room.byId[k];
            if (!m.mine && typeof m.id === "number" && m.status !== "seen" && m.id > maxIn) maxIn = m.id;
        });
        if (maxIn && maxIn > room.seenSent) {
            room.seenSent = maxIn;
            api("/api/chat/c/" + room.id + "/seen", { body: { upto: maxIn } }).then(function (r) {
                if (r.ok) {
                    Object.keys(room.byId).forEach(function (k) { var m = room.byId[k]; if (!m.mine && m.id <= maxIn) m.status = "seen"; });
                    loadList();
                }
            });
        }
    }

    function openRoom(cid, push) {
        if (room && room.id === cid) { setView(true); return; }
        clearInterval(pollTimer);
        resetRoom(cid);
        state.conv = cid;
        setView(true);
        if (push !== false && window.history && history.pushState) history.pushState({ c: cid }, "", "/chat/" + cid);
        api("/api/chat/c/" + cid + "/messages").then(function (r) {
            if (!r.ok) { toast("error", (r.error || {}).message || "Could not open chat"); closeRoom(); return; }
            var d = r.data;
            room.hasMore = d.has_more;
            older.hidden = !d.has_more;
            d.messages.forEach(function (m) { addMessage(m); });
            room.firstId = d.messages.length ? d.messages[0].id : 0;
            room.since = d.server_time;
            renderPeer(d.peer);
            toBottom();
            markSeenIfNeeded();
            pollTimer = setInterval(pollRoom, 2500);
            text.focus({ preventScroll: true });
        });
        renderChats();
    }
    function closeRoom(skipHistory) {
        clearInterval(pollTimer);
        room = null;
        state.conv = null;
        setView(false);
        if (!skipHistory && window.history && history.pushState && /^\/chat\/\d+/.test(location.pathname)) history.pushState({}, "", "/chat");
        if (state.tab === "chats") renderChats();
    }
    document.getElementById("roomBack").addEventListener("click", function () { closeRoom(); loadList(); });
    window.addEventListener("popstate", function () {
        var m = /^\/chat\/(\d+)/.exec(location.pathname);
        if (m) openRoom(parseInt(m[1], 10), false); else closeRoom(true);
    });

    function pollRoom() {
        if (!room || !visible() || room.busy) return;
        var cid = room.id;
        room.busy = true;
        api("/api/chat/c/" + cid + "/poll?after=" + room.after + (room.since ? "&since=" + encodeURIComponent(room.since) : "")).then(function (r) {
            if (!room || room.id !== cid) return;
            room.busy = false;
            if (!r.ok) return;
            var stick = nearBottom(), d = r.data, gotNew = false;
            d.messages.forEach(function (m) {
                if (!room.byId[m.id]) { addMessage(m); gotNew = true; }
            });
            d.updates.forEach(function (m) { if (room.byId[m.id]) paint(m); });
            room.since = d.server_time;
            renderPeer(d.peer);
            if (gotNew && stick) toBottom();
            markSeenIfNeeded();
        });
    }
    document.addEventListener("visibilitychange", function () { if (visible()) { pollRoom(); markSeenIfNeeded(); } });

    document.getElementById("roomOlderBtn").addEventListener("click", function () {
        if (!room || !room.firstId) return;
        var cid = room.id, h = msgsBox.scrollHeight;
        api("/api/chat/c/" + cid + "/messages?before=" + room.firstId).then(function (r) {
            if (!r.ok || !room || room.id !== cid) return;
            r.data.messages.slice().reverse().forEach(function (m) { room.byId[m.id] = m; addMessage(m, true); });
            if (r.data.messages.length) room.firstId = r.data.messages[0].id;
            older.hidden = !r.data.has_more;
            msgsBox.scrollTop = msgsBox.scrollHeight - h;
        });
    });

    /* ---------- sending (instant, retry-safe through the uuid) ---------- */
    function send(body) {
        var cid = room.id, id = uuid();
        var temp = { id: "t-" + id, mine: true, body: body, deleted: false, status: "pending", created_at: new Date().toISOString() };
        var node = el("div", "msg mine pending");
        node.setAttribute("data-uuid", id);
        var bd = el("div", "body", body);
        node.appendChild(bd);
        var meta = el("div", "meta");
        meta.appendChild(el("span", null, clock(temp.created_at)));
        var clk = el("span", "tick pending"); clk.appendChild(ic("fa-clock")); meta.appendChild(clk);
        node.appendChild(meta);
        items.appendChild(node);
        toBottom();
        function attempt(n) {
            api("/api/chat/c/" + cid + "/messages", { body: { uuid: id, body: body } }).then(function (r) {
                if (!room || room.id !== cid) return;
                if (r.ok) {
                    var m = r.data.message;
                    var polled = room.byId[m.id];
                    node.parentNode && node.parentNode.removeChild(node);
                    if (!polled) addMessage(m); else paint(m);
                    toBottom();
                    loadList();
                } else if (r.offline && n < 3) {
                    setTimeout(function () { attempt(n + 1); }, 1500 * (n + 1));
                } else {
                    node.classList.add("failed");
                    var retry = el("button", "btn btn-sm btn-outline", "Not sent - tap to retry");
                    retry.type = "button";
                    retry.addEventListener("click", function () { retry.remove(); node.classList.remove("failed"); attempt(0); });
                    node.appendChild(retry);
                    if (!r.offline) toast("error", (r.error || {}).message || "Could not send");
                }
            });
        }
        attempt(0);
    }
    function grow() { text.style.height = "auto"; text.style.height = Math.min(text.scrollHeight, 120) + "px"; }
    text.addEventListener("input", function () {
        grow();
        if (READONLY || !room) return;
        var t = Date.now();
        if (t - typingSent > 3000) {
            typingSent = t;
            api("/api/chat/c/" + room.id + "/typing", { body: { typing: true } });
        }
        clearTimeout(typingStop);
        typingStop = setTimeout(function () { typingSent = 0; if (room) api("/api/chat/c/" + room.id + "/typing", { body: { typing: false } }); }, 4000);
    });
    text.addEventListener("keydown", function (e) {
        if (e.key === "Enter" && !e.shiftKey && !e.isComposing && window.matchMedia("(hover: hover)").matches) {
            e.preventDefault();
            form.requestSubmit();
        }
    });
    form.addEventListener("submit", function (e) {
        e.preventDefault();
        var body = text.value.trim();
        if (!body || !room) return;
        if (READONLY) { toast("error", "Read-only while viewing as a student."); return; }
        text.value = "";
        grow();
        send(body);
    });
    if (READONLY) { text.disabled = true; text.placeholder = "Read-only (viewing as a student)"; document.getElementById("roomSend").disabled = true; }

    /* ---------- start ---------- */
    setTab(state.tab);
    startListTimer();
    setInterval(summaryTick, 20000);
    var start = parseInt(app.getAttribute("data-conv") || "0", 10);
    if (start) openRoom(start, false); else setView(false);
})();
