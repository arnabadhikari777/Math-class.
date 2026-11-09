/* Arnab Math Class - student chat (v2).
   Loaded on every student page:
     * everywhere   -> a small heartbeat/badge poll (keeps you "online" and marks incoming messages "delivered")
     * on /chat      -> the full chat screen (chat list, people, requests, room with sent/delivered/seen).
   All data comes from /api/chat/*; the server re-checks every request. No inline handlers (CSP). */
(function () {
    "use strict";
    var CSRF = (document.querySelector('meta[name="csrf-token"]') || {}).content || "";
    var app = document.getElementById("chatApp");
    var READONLY = app && app.getAttribute("data-readonly") === "1";
    var phone = window.matchMedia("(max-width: 768px)");
    var hoverDevice = window.matchMedia("(hover: hover)");

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
    function errMsg(r, fallback) { return (r && r.error && r.error.message) || fallback; }

    /* ---------- time helpers ---------- */
    function pad(n) { return (n < 10 ? "0" : "") + n; }
    function clock(iso) { var d = new Date(iso); return isNaN(d) ? "" : pad(d.getHours()) + ":" + pad(d.getMinutes()); }
    function dayKey(iso) { var d = new Date(iso); return d.getFullYear() + "-" + d.getMonth() + "-" + d.getDate(); }
    function isToday(iso) { return dayKey(iso) === dayKey(new Date().toISOString()); }
    function isYesterday(iso) { return dayKey(iso) === dayKey(new Date(Date.now() - 864e5).toISOString()); }
    function dayLabel(iso) {
        if (isToday(iso)) return "Today";
        if (isYesterday(iso)) return "Yesterday";
        return new Date(iso).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
    }
    function listTime(iso) {            // chat list: clock today, "Yesterday", else a short date
        if (!iso) return "";
        if (isToday(iso)) return clock(iso);
        if (isYesterday(iso)) return "Yesterday";
        return new Date(iso).toLocaleDateString(undefined, { day: "numeric", month: "short" });
    }
    function ago(iso) {
        var s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
        if (s < 60) return "just now";
        if (s < 3600) return Math.floor(s / 60) + " min ago";
        if (isToday(iso)) return "today at " + clock(iso);
        if (isYesterday(iso)) return "yesterday at " + clock(iso);
        return new Date(iso).toLocaleDateString(undefined, { day: "numeric", month: "short" }) + " at " + clock(iso);
    }
    function initials(name) {
        var p = String(name || "?").trim().split(/\s+/);
        return ((p[0] || "?")[0] + (p.length > 1 ? p[p.length - 1][0] : "")).toUpperCase();
    }
    function hue(name) {                // same name -> same avatar colour
        var h = 0, s = String(name || "");
        for (var i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) % 360;
        return h;
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
    function fillText(node, text) {     // plain text, but http(s) links become safe clickable links
        var re = /https?:\/\/[^\s<]+/gi, last = 0, m;
        while ((m = re.exec(text))) {
            var url = m[0], trail = "";
            var t = /[.,;:!?)\]]+$/.exec(url);
            if (t) { trail = t[0]; url = url.slice(0, -trail.length); }
            try {
                var u = new URL(url);
                if (u.protocol !== "http:" && u.protocol !== "https:") throw new Error("scheme");
            } catch (e) { continue; }
            if (m.index > last) node.appendChild(document.createTextNode(text.slice(last, m.index)));
            var a = el("a", null, url);
            a.href = url; a.target = "_blank"; a.rel = "noopener noreferrer nofollow";
            node.appendChild(a);
            last = m.index + url.length;
            re.lastIndex = last;
        }
        if (last < text.length) node.appendChild(document.createTextNode(text.slice(last)));
    }

    /* ---------- nav badge + heartbeat (every student page) ---------- */
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
    var state = { tab: app.getAttribute("data-tab") || "chats", page: 1, q: "", conv: null, chats: [], requests: [], req: 0 };
    var listTimer = null;
    if (["chats", "people", "requests"].indexOf(state.tab) < 0) state.tab = "chats";

    /* ---------- phone keyboard: the room always fits the visible area ---------- */
    var root = document.documentElement;
    function syncViewport() {
        var vv = window.visualViewport;
        if (!vv || !phone.matches || !app.classList.contains("room-open")) {
            root.style.removeProperty("--vv-h");
            root.style.removeProperty("--vv-top");
            app.classList.remove("kbd");
            return;
        }
        root.style.setProperty("--vv-h", Math.round(vv.height) + "px");
        root.style.setProperty("--vv-top", Math.round(vv.offsetTop) + "px");
        app.classList.toggle("kbd", window.innerHeight - vv.height > 120);
        if (room && room.stick) toBottom();
    }
    if (window.visualViewport) {
        window.visualViewport.addEventListener("resize", syncViewport);
        window.visualViewport.addEventListener("scroll", syncViewport);
    }
    if (phone.addEventListener) phone.addEventListener("change", syncViewport);

    function setView(roomOpen) {
        app.classList.toggle("room-open", roomOpen);
        roomBox.hidden = !roomOpen;
        emptyBox.hidden = roomOpen;
        syncViewport();
    }
    function setTab(tab) {
        state.tab = tab;
        state.page = 1;
        side.querySelectorAll(".chat-tabs button").forEach(function (b) {
            var on = b.getAttribute("data-tab") === tab;
            b.classList.toggle("on", on);
            b.setAttribute("aria-selected", on ? "true" : "false");
        });
        searchBox.hidden = tab !== "people";
        loadList(true);
    }
    side.querySelectorAll(".chat-tabs button").forEach(function (b) {
        b.addEventListener("click", function () { setTab(b.getAttribute("data-tab")); });
    });
    var qTimer = null;
    searchInput.addEventListener("input", function () {
        clearTimeout(qTimer);
        qTimer = setTimeout(function () { state.q = searchInput.value; state.page = 1; loadList(true); }, 300);
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
        a.style.setProperty("--h", hue(name));
        if (online) a.appendChild(el("span", "dot"));
        return a;
    }
    function skeletonRows(n) {
        list.textContent = "";
        for (var i = 0; i < n; i++) {
            var row = el("div", "chat-row sk");
            row.appendChild(el("div", "sk-av"));
            var mid = el("div", "mid");
            var a = el("div", "sk-l"), b = el("div", "sk-l");
            a.style.width = (45 + (i * 13) % 30) + "%";
            b.style.width = (60 + (i * 17) % 30) + "%";
            mid.appendChild(a); mid.appendChild(b);
            row.appendChild(mid);
            list.appendChild(row);
        }
    }
    function repaintList(fn) {          // redraw but keep the scroll position
        var st = list.scrollTop;
        list.textContent = "";
        fn();
        list.scrollTop = st;
    }

    function renderChats() {
        repaintList(function () {
            if (!state.chats.length) {
                list.appendChild(emptyNote("fa-comments", "No chats yet. Open People and say hello."));
                return;
            }
            state.chats.forEach(function (c) {
                var row = el("button", "chat-row" + (c.id === state.conv ? " active" : "") + (c.unread ? " unread" : ""));
                row.type = "button";
                row.appendChild(avatar(c.peer.name, c.online));
                var mid = el("div", "mid");
                var top = el("div", "top");
                top.appendChild(el("strong", null, c.peer.name));
                top.appendChild(el("span", "when", listTime(c.last_at)));
                mid.appendChild(top);
                var bot = el("div", "bot");
                if (c.last_mine && c.last_status) bot.appendChild(tick(c.last_status));
                bot.appendChild(el("span", "txt", c.last_text || "Say hello"));
                if (c.unread) bot.appendChild(el("span", "cbadge", c.unread > 99 ? "99+" : String(c.unread)));
                mid.appendChild(bot);
                row.appendChild(mid);
                row.addEventListener("click", function () { openRoom(c.id); });
                list.appendChild(row);
            });
        });
    }

    function renderRequests() {
        repaintList(function () {
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
                        acts.querySelectorAll("button").forEach(function (x) { x.disabled = true; });
                        api("/api/chat/requests/" + r.id + "/" + a[0], { post: true }).then(function (res) {
                            if (res.ok) {
                                if (a[0] === "accept" && res.data.conversation_id) openRoom(res.data.conversation_id);
                                loadList();
                            } else {
                                toast("error", errMsg(res, "Could not answer"));
                                acts.querySelectorAll("button").forEach(function (x) { x.disabled = READONLY; });
                            }
                        });
                    });
                    acts.appendChild(b);
                });
                row.appendChild(acts);
                list.appendChild(row);
            });
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
                        else { b.disabled = false; toast("error", errMsg(r, "Could not send")); }
                    });
                });
            } else {
                b.textContent = "Message";
                b.disabled = READONLY;
                b.addEventListener("click", function () {
                    b.disabled = true;
                    api("/api/chat/start", { body: { student_id: p.id } }).then(function (r) {
                        if (r.ok) openRoom(r.data.conversation_id);
                        else { b.disabled = false; toast("error", errMsg(r, "Could not open")); }
                    });
                });
            }
            row.appendChild(b);
            list.appendChild(row);
        });
        moreBox.hidden = !hasNext;
    }

    function loadList(showSkeleton) {
        moreBox.hidden = true;
        var token = ++state.req, tab = state.tab;
        if (showSkeleton && state.page === 1) skeletonRows(6);
        if (tab === "people") {
            api("/api/chat/people?page=" + state.page + "&q=" + encodeURIComponent(state.q)).then(function (r) {
                if (token !== state.req || state.tab !== "people") return;      // a newer request / tab won
                if (r.ok) renderPeople(r.data.people, r.data.has_next);
                else { list.textContent = ""; list.appendChild(emptyNote("fa-wifi", errMsg(r, "Could not load"))); }
            });
            return;
        }
        api("/api/chat/chats").then(function (r) {
            if (token !== state.req || state.tab !== tab) return;
            if (!r.ok) {
                if (showSkeleton) { list.textContent = ""; list.appendChild(emptyNote("fa-wifi", errMsg(r, "Could not load chats"))); }
                return;
            }
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
    var sendBtn = document.getElementById("roomSend"), jump = document.getElementById("roomJump");
    var typingBox = document.getElementById("roomTyping"), older = document.getElementById("roomOlder");
    var room = null, pollTimer = null, typingSent = 0, typingStop = null;

    function resetRoom(cid) {
        room = { id: cid, byId: {}, pending: [], fresh: {}, after: 0, firstId: 0, since: null, peerName: "", hasMore: false,
                 busy: false, seenSent: 0, selId: null, stick: true, unseenBelow: 0, loaded: false };
        items.textContent = "";
        typingBox.hidden = true;
        jump.hidden = true;
        older.hidden = true;
    }
    function nearBottom() { return msgsBox.scrollHeight - msgsBox.scrollTop - msgsBox.clientHeight < 120; }
    function toBottom() {
        msgsBox.scrollTop = msgsBox.scrollHeight;
        if (room) { room.stick = true; room.unseenBelow = 0; }
        updateJump();
    }
    function updateJump() {
        if (!room) return;
        var away = !nearBottom();
        jump.hidden = !away;
        var c = document.getElementById("roomJumpCount");
        c.hidden = !(away && room.unseenBelow);
        c.textContent = String(room.unseenBelow);
    }
    msgsBox.addEventListener("scroll", function () {
        if (!room) return;
        room.stick = nearBottom();
        if (room.stick) room.unseenBelow = 0;
        updateJump();
    }, { passive: true });
    jump.addEventListener("click", toBottom);

    function allMessages() {
        var arr = Object.keys(room.byId).map(function (k) { return room.byId[k]; });
        arr.sort(function (a, b) { return a.id - b.id; });
        return arr.concat(room.pending);
    }
    function bubble(m, pos) {
        var cls = "msg " + (m.mine ? "mine" : "theirs") + " g-" + pos + (m.deleted ? " deleted" : "");
        if (m.pending) cls += m.failed ? " failed" : " pending";
        if (room.fresh[m.id]) cls += " fresh";
        if (room.selId === m.id) cls += " sel";
        var b = el("div", cls);
        b.setAttribute("data-id", m.id);
        var body = el("div", "body");
        if (m.deleted) body.textContent = "This message was deleted"; else fillText(body, m.body);
        b.appendChild(body);
        var meta = el("div", "meta");
        meta.appendChild(el("span", null, clock(m.created_at)));
        if (m.pending) {
            var clk = el("span", "tick pending"); clk.appendChild(ic(m.failed ? "fa-circle-exclamation" : "fa-clock")); meta.appendChild(clk);
        } else if (m.mine && !m.deleted) {
            meta.appendChild(tick(m.status));
        }
        b.appendChild(meta);
        if (m.pending && m.failed) {
            var retry = el("button", "btn btn-sm btn-outline retry", "Not sent - tap to retry");
            retry.type = "button";
            retry.addEventListener("click", function (e) { e.stopPropagation(); m.failed = false; render(); attempt(room.id, m, 0); });
            b.appendChild(retry);
        }
        if (m.mine && !m.deleted && !m.pending) {
            if (!READONLY && typeof m.id === "number") {
                var del = el("button", "msg-del");
                del.type = "button";
                del.title = "Delete for everyone";
                del.setAttribute("aria-label", "Delete for everyone");
                del.appendChild(ic("fa-trash"));
                del.addEventListener("click", function (e) { e.stopPropagation(); confirmDelete(m); });
                b.appendChild(del);
            }
            b.addEventListener("click", function (e) {         // tap a message of yours to reveal Delete (touch screens)
                if (e.target.closest("a, button")) return;
                room.selId = room.selId === m.id ? null : m.id;
                items.querySelectorAll(".msg.sel").forEach(function (n) { n.classList.remove("sel"); });
                if (room.selId === m.id) b.classList.add("sel");
            });
        }
        return b;
    }
    function confirmDelete(m) {
        var ask = window.AMC && window.AMC.confirm
            ? window.AMC.confirm({ title: "Delete message?", message: "It will be deleted for everyone in this chat.", confirmText: "Delete", danger: true })
            : Promise.resolve(window.confirm("Delete this message for everyone?"));
        ask.then(function (yes) {
            if (!yes || !room) return;
            api("/api/chat/messages/" + m.id + "/delete", { post: true }).then(function (r) {
                if (r.ok) { m.deleted = true; m.body = ""; room.selId = null; render(); loadList(); }
                else toast("error", errMsg(r, "Could not delete"));
            });
        });
    }
    /* Rebuild the whole message list (grouping + day chips are computed here, so older/new/updated all look right).
       mode: "bottom" = jump to latest, "anchor" = keep what the reader is looking at (older messages loaded above). */
    function render(mode) {
        if (!room) return;
        var stick = mode === "bottom" || room.stick, prevH = msgsBox.scrollHeight, prevTop = msgsBox.scrollTop;
        var msgs = allMessages(), frag = document.createDocumentFragment(), lastDay = "";
        if (!msgs.length && room.loaded) {
            var hi = el("div", "room-hello");
            hi.appendChild(ic("fa-hand"));
            hi.appendChild(el("div", null, "No messages yet. Say hello" + (room.peerName ? " to " + room.peerName : "") + "."));
            frag.appendChild(hi);
        }
        msgs.forEach(function (m, i) {
            var prev = msgs[i - 1], next = msgs[i + 1];
            if (dayKey(m.created_at) !== lastDay) {
                var d = el("div", "day"); d.appendChild(el("span", null, dayLabel(m.created_at)));
                frag.appendChild(d);
                lastDay = dayKey(m.created_at);
                prev = null;
            }
            function joins(a, b) {
                return a && b && a.mine === b.mine && dayKey(a.created_at) === dayKey(b.created_at) &&
                    Math.abs(new Date(b.created_at) - new Date(a.created_at)) < 5 * 60 * 1000;
            }
            var withPrev = joins(prev, m), withNext = joins(m, next);
            var pos = withPrev ? (withNext ? "mid" : "last") : (withNext ? "first" : "single");
            frag.appendChild(bubble(m, pos));
        });
        items.textContent = "";
        items.appendChild(frag);
        room.fresh = {};
        if (mode === "anchor") msgsBox.scrollTop = prevTop + (msgsBox.scrollHeight - prevH);
        else if (stick) toBottom();
        else msgsBox.scrollTop = prevTop;
        updateJump();
    }
    function setPeerAvatar(name) {
        var av = document.getElementById("roomAvatar");
        av.textContent = initials(name);
        av.style.setProperty("--h", hue(name));
    }
    function renderPeer(p) {
        if (!p || !room) return;
        var st = document.getElementById("roomStatus");
        if (p.name) {
            document.getElementById("roomName").textContent = p.name;
            room.peerName = p.name;
            setPeerAvatar(p.name);
        }
        var typing = !!p.typing;
        st.classList.toggle("online", !!p.online && !typing);
        st.classList.toggle("typing", typing);
        st.textContent = typing ? "typing…" : (p.online ? "online" : (p.last_seen ? "last seen " + ago(p.last_seen) : "offline"));
        var was = typingBox.hidden;
        typingBox.hidden = !typing;
        if (typing && was && room.stick) toBottom();
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
                if (r.ok && room) {
                    Object.keys(room.byId).forEach(function (k) { var m = room.byId[k]; if (!m.mine && m.id <= maxIn) m.status = "seen"; });
                    loadList();
                }
            });
        }
    }
    function roomSkeleton() {
        items.textContent = "";
        [["theirs", 58], ["theirs", 38], ["mine", 46], ["theirs", 64], ["mine", 34]].forEach(function (s) {
            var d = el("div", "msg " + s[0] + " g-single sk-bubble");
            d.appendChild(el("div", "sk-l")).style.width = s[1] * 2 + "px";
            items.appendChild(d);
        });
    }

    function openRoom(cid, push) {
        if (room && room.id === cid) { setView(true); return; }
        clearInterval(pollTimer);
        resetRoom(cid);
        state.conv = cid;
        setView(true);
        roomSkeleton();
        document.getElementById("roomName").textContent = "";
        document.getElementById("roomStatus").textContent = "";
        var known = state.chats.filter(function (c) { return c.id === cid; })[0];      // show the name instantly if the list knows it
        if (known) { document.getElementById("roomName").textContent = known.peer.name; setPeerAvatar(known.peer.name); room.peerName = known.peer.name; }
        if (push !== false && window.history && history.pushState) history.pushState({ c: cid }, "", "/chat/" + cid);
        api("/api/chat/c/" + cid + "/messages").then(function (r) {
            if (!room || room.id !== cid) return;
            if (!r.ok) { toast("error", errMsg(r, "Could not open chat")); closeRoom(); return; }
            var d = r.data;
            room.loaded = true;
            room.hasMore = d.has_more;
            older.hidden = !d.has_more;
            d.messages.forEach(function (m) {
                room.byId[m.id] = m;
                if (typeof m.id === "number" && m.id > room.after) room.after = m.id;
            });
            room.firstId = d.messages.length ? d.messages[0].id : 0;
            room.since = d.server_time;
            renderPeer(d.peer);
            render("bottom");
            markSeenIfNeeded();
            pollTimer = setInterval(pollRoom, 2500);
            if (hoverDevice.matches && !READONLY) text.focus({ preventScroll: true });   // on phones don't pop the keyboard up by itself
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
        if (!room || !room.loaded || !visible() || room.busy) return;
        var cid = room.id;
        room.busy = true;
        api("/api/chat/c/" + cid + "/poll?after=" + room.after + (room.since ? "&since=" + encodeURIComponent(room.since) : "")).then(function (r) {
            if (!room || room.id !== cid) return;
            room.busy = false;
            if (!r.ok) return;
            var d = r.data, changed = false, incoming = 0;
            d.messages.forEach(function (m) {
                if (room.byId[m.id]) return;
                room.byId[m.id] = m;
                room.fresh[m.id] = true;
                if (typeof m.id === "number" && m.id > room.after) room.after = m.id;
                changed = true;
                if (!m.mine) incoming++;
            });
            d.updates.forEach(function (m) { if (room.byId[m.id]) { room.byId[m.id] = m; changed = true; } });
            room.since = d.server_time;
            if (incoming && !room.stick) room.unseenBelow += incoming;
            renderPeer(d.peer);
            if (changed) render();
            markSeenIfNeeded();
        });
    }
    document.addEventListener("visibilitychange", function () { if (visible()) { pollRoom(); markSeenIfNeeded(); } });

    document.getElementById("roomOlderBtn").addEventListener("click", function () {
        if (!room || !room.firstId) return;
        var cid = room.id, btn = this;
        btn.disabled = true;
        api("/api/chat/c/" + cid + "/messages?before=" + room.firstId).then(function (r) {
            btn.disabled = false;
            if (!r.ok || !room || room.id !== cid) return;
            r.data.messages.forEach(function (m) { room.byId[m.id] = m; });
            if (r.data.messages.length) room.firstId = r.data.messages[0].id;
            older.hidden = !r.data.has_more;
            render("anchor");
        });
    });

    /* ---------- sending (instant, retry-safe through the uuid) ---------- */
    function attempt(cid, p, n) {
        api("/api/chat/c/" + cid + "/messages", { body: { uuid: p.uuid, body: p.body } }).then(function (r) {
            if (!room || room.id !== cid) return;
            if (r.ok) {
                var m = r.data.message;
                room.pending = room.pending.filter(function (x) { return x !== p; });
                room.byId[m.id] = m;
                if (typeof m.id === "number" && m.id > room.after) room.after = m.id;
                render("bottom");
                loadList();
            } else if (r.offline && n < 3) {
                setTimeout(function () { attempt(cid, p, n + 1); }, 1500 * (n + 1));
            } else {
                p.failed = true;
                render();
                if (!r.offline) toast("error", errMsg(r, "Could not send"));
            }
        });
    }
    function send(body) {
        var p = { id: "t-" + uuid(), uuid: null, mine: true, body: body, deleted: false, pending: true, failed: false, status: "pending", created_at: new Date().toISOString() };
        p.uuid = p.id.slice(2);
        room.fresh[p.id] = true;
        room.pending.push(p);
        render("bottom");
        attempt(room.id, p, 0);
    }
    function grow() { text.style.height = "auto"; text.style.height = Math.min(text.scrollHeight, 132) + "px"; }
    function syncSend() { sendBtn.disabled = READONLY || !text.value.trim(); }
    text.addEventListener("input", function () {
        grow();
        syncSend();
        if (READONLY || !room) return;
        var t = Date.now();
        if (t - typingSent > 3000) {
            typingSent = t;
            api("/api/chat/c/" + room.id + "/typing", { body: { typing: true } });
        }
        clearTimeout(typingStop);
        typingStop = setTimeout(function () { typingSent = 0; if (room) api("/api/chat/c/" + room.id + "/typing", { body: { typing: false } }); }, 4000);
    });
    text.addEventListener("focus", function () { if (room && room.stick) setTimeout(toBottom, 250); });
    text.addEventListener("keydown", function (e) {
        if (e.key === "Enter" && !e.shiftKey && !e.isComposing && hoverDevice.matches) {
            e.preventDefault();
            form.requestSubmit();
        }
    });
    sendBtn.addEventListener("pointerdown", function (e) { e.preventDefault(); });   // keep the keyboard open when Send is tapped
    form.addEventListener("submit", function (e) {
        e.preventDefault();
        var body = text.value.trim();
        if (!body || !room) return;
        if (READONLY) { toast("error", "Read-only while viewing as a student."); return; }
        text.value = "";
        grow();
        syncSend();
        send(body);
        if (!hoverDevice.matches) text.focus({ preventScroll: true });
    });
    if (READONLY) { text.disabled = true; text.placeholder = "Read-only (viewing as a student)"; sendBtn.disabled = true; }

    /* ---------- start ---------- */
    setTab(state.tab);
    startListTimer();
    setInterval(summaryTick, 20000);
    var start = parseInt(app.getAttribute("data-conv") || "0", 10);
    if (start) openRoom(start, false); else setView(false);
})();
