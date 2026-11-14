/* Arnab Math Class - app shell behaviour (loaded on every page).

   1. Scroll lock       one shared lock used by the teacher drawer and the notification panel
   2. Teacher drawer    ONE drawer, opened by the top-left menu button AND the bottom "More" button
   3. Student nav       bottom navigation hides when scrolling down, returns when scrolling up
   4. Notification bell panel (students): list, bell = mark all read, All Clean, push on/off

   No libraries, no polling. Everything is a small, passive listener. */
(function () {
    "use strict";

    var doc = document;
    var root = doc.documentElement;
    var body = doc.body;
    function $(id) { return doc.getElementById(id); }
    function mq(q) { return window.matchMedia ? window.matchMedia(q) : { matches: false, addEventListener: function () {}, addListener: function () {} }; }
    function onMq(m, fn) { if (m.addEventListener) m.addEventListener("change", fn); else if (m.addListener) m.addListener(fn); }
    var reduceMotion = mq("(prefers-reduced-motion: reduce)");
    var CSRF = (doc.querySelector('meta[name="csrf-token"]') || {}).content || "";

    function toast(type, msg) { if (window.AMC && window.AMC.toast) window.AMC.toast(type, msg); }
    function postJSON(url, data) {
        return fetch(url, {
            method: "POST",
            credentials: "same-origin",
            keepalive: true,
            headers: { "Content-Type": "application/json", "X-CSRFToken": CSRF },
            body: JSON.stringify(data || {})
        }).then(function (r) {
            return r.json().catch(function () { return {}; }).then(function (j) {
                if (!r.ok || j.ok === false) throw new Error(j.error || "Request failed");
                return j;
            });
        });
    }

    /* ───────────── 1. shared scroll lock ───────────── */
    // overflow:hidden on <html>/<body> (see .amc-scroll-lock in app.css) keeps the page exactly where it is, so the
    // sticky top bar does not jump. The touchmove guard covers browsers that ignore overflow:hidden on touch.
    var locks = 0;
    var lockY = 0;
    function guardTouch(e) {
        var t = e.target;
        if (t && t.closest && t.closest("[data-scroll-ok]")) return; // the drawer / notification list may scroll
        if (e.cancelable) e.preventDefault();
    }
    function lockScroll() {
        if (locks++ > 0) return;
        lockY = window.pageYOffset || root.scrollTop || 0;
        root.classList.add("amc-scroll-lock");
        doc.addEventListener("touchmove", guardTouch, { passive: false });
    }
    function unlockScroll() {
        if (locks === 0 || --locks > 0) return;
        doc.removeEventListener("touchmove", guardTouch, { passive: false });
        root.classList.remove("amc-scroll-lock");
        if (Math.abs((window.pageYOffset || 0) - lockY) > 1) { // put the page back exactly where it was
            var prev = root.style.scrollBehavior;
            root.style.scrollBehavior = "auto";
            window.scrollTo(0, lockY);
            root.style.scrollBehavior = prev;
        }
    }

    /* ───────────── 1b. action menus (<details class="act-menu">): one open at a time, close on outside tap / Escape ───────────── */
    doc.addEventListener("click", function (e) {
        var inside = e.target.closest ? e.target.closest(".act-menu") : null;
        doc.querySelectorAll("details.act-menu[open]").forEach(function (d) { if (d !== inside) d.removeAttribute("open"); });
        if (inside && e.target.closest(".act-pop a, .act-pop button")) inside.removeAttribute("open");
    });
    doc.addEventListener("keydown", function (e) {
        if (e.key !== "Escape") return;
        doc.querySelectorAll("details.act-menu[open]").forEach(function (d) { d.removeAttribute("open"); var s = d.querySelector("summary"); if (s) s.focus(); });
    });

    /* ───────────── 2. teacher drawer (one implementation, two buttons) ───────────── */
    (function drawer() {
        var side = $("sidebar");
        var scrim = $("scrim");
        if (!side) return;
        var buttons = [$("menuBtn"), $("moreBtn")].filter(Boolean);
        var narrow = mq("(max-width: 960px)");
        var behind = ["header.topbar", ".main", ".bottom-nav"]
            .map(function (s) { return doc.querySelector(s); })
            .filter(Boolean);
        var lastTrigger = null;

        function isOpen() { return body.classList.contains("drawer-open"); }
        function setState(open) {
            body.classList.toggle("drawer-open", open);
            buttons.forEach(function (b) { b.setAttribute("aria-expanded", open ? "true" : "false"); });
            side.setAttribute("aria-hidden", narrow.matches && !open ? "true" : "false");
            behind.forEach(function (el) {
                if (open) el.setAttribute("inert", ""); else el.removeAttribute("inert");
            });
        }
        function open(from) {
            if (isOpen() || !narrow.matches) return;
            lastTrigger = from || doc.activeElement;
            setState(true);
            lockScroll();
            side.scrollTop = 0;
            setTimeout(function () { try { side.focus({ preventScroll: true }); } catch (e) { /* old browser */ } }, reduceMotion.matches ? 0 : 60);
        }
        function close(restoreFocus) {
            if (!isOpen()) return;
            setState(false);
            unlockScroll();
            if (restoreFocus !== false && lastTrigger && lastTrigger.focus) {
                try { lastTrigger.focus({ preventScroll: true }); } catch (e) { /* old browser */ }
            }
            lastTrigger = null;
        }
        side.setAttribute("tabindex", "-1");
        side.setAttribute("data-scroll-ok", "");
        buttons.forEach(function (b) {
            b.setAttribute("aria-controls", "sidebar");
            b.addEventListener("click", function () { if (isOpen()) close(); else open(b); });
        });
        if (scrim) scrim.addEventListener("click", function () { close(); });
        var x = $("drawerClose");
        if (x) x.addEventListener("click", function () { close(); });
        side.addEventListener("click", function (e) {
            var a = e.target.closest ? e.target.closest("a[href]") : null;
            if (a) close(false); // navigating away: no need to move focus
        });
        doc.addEventListener("keydown", function (e) {
            if (e.key === "Escape" && isOpen()) { e.preventDefault(); close(); }
        });
        onMq(narrow, function () { if (!narrow.matches) close(false); setState(isOpen()); });
        window.addEventListener("pageshow", function (e) { if (e.persisted && isOpen()) close(false); }); // back button / bfcache only
        setState(false);
    })();

    /* ───────────── 3. student bottom navigation: hide on scroll down, show on scroll up ───────────── */
    (function smartNav() {
        if (!body.classList.contains("student-shell")) return; // teacher navigation never hides
        var nav = doc.querySelector(".bottom-nav");
        if (!nav) return;
        var phone = mq("(max-width: 960px)");
        var THRESHOLD = 10;     // ignore tiny movements (prevents flicker)
        var HIDE_AFTER = 72;    // never hide while this close to the top
        var lastY = window.pageYOffset || 0;
        var ticking = false;
        var hidden = false;

        function set(hide) {
            if (hide === hidden) return;
            hidden = hide;
            body.classList.toggle("nav-hidden", hide);
        }
        function busy() { // typing, an open dialog, or the exam screen: leave the bar alone
            if (body.classList.contains("exam-page")) return true;
            var a = doc.activeElement;
            if (a && /^(INPUT|TEXTAREA|SELECT)$/.test(a.tagName)) return true;
            return !!doc.querySelector(".amc-modal, .amc-pop, [role='dialog']:not([hidden])") || body.classList.contains("amc-lock") || root.classList.contains("amc-scroll-lock");
        }
        function update() {
            ticking = false;
            if (!phone.matches) { set(false); return; }
            var y = Math.max(0, window.pageYOffset || 0);
            var max = Math.max(0, root.scrollHeight - window.innerHeight);
            if (y <= HIDE_AFTER) { set(false); lastY = y; return; }
            if (busy()) { set(false); lastY = y; return; }
            var dy = y - lastY;
            if (Math.abs(dy) < THRESHOLD) return;       // small movement: keep the current state
            if (y >= max - 2) { lastY = y; return; }    // rubber-band at the very bottom: do nothing
            set(dy > 0);
            lastY = y;
        }
        window.addEventListener("scroll", function () {
            if (ticking) return;
            ticking = true;
            window.requestAnimationFrame(update);
        }, { passive: true });
        // keyboard users tabbing into the bar, or any focus inside a field, must always see the bar
        nav.addEventListener("focusin", function () { set(false); });
        doc.addEventListener("focusin", function (e) {
            if (e.target && /^(INPUT|TEXTAREA|SELECT)$/.test(e.target.tagName)) set(false);
        });
        onMq(phone, update);
        window.addEventListener("pageshow", function (e) { if (e.persisted) { set(false); lastY = window.pageYOffset || 0; } });

        // "Tests" shares a page with "Home": highlight the right one from the URL hash.
        var home = nav.querySelector("a[data-nav='home']");
        var tests = nav.querySelector("a[data-nav='tests']");
        var onDashboard = !!(home && home.classList.contains("active"));
        function mark(link, on) {
            link.classList.toggle("active", on);
            if (on) link.setAttribute("aria-current", "page"); else link.removeAttribute("aria-current");
        }
        function syncHash() {
            if (!home || !tests || !onDashboard) return;
            var t = location.hash === "#tests";
            mark(home, !t);
            mark(tests, t);
        }
        window.addEventListener("hashchange", syncHash);
        syncHash();
    })();

    /* ───────────── 4. student notification bell ───────────── */
    (function bell() {
        var btn = $("notifBtn");
        var panel = $("notifPanel");
        if (!btn || !panel) return;
        var backdrop = $("notifBackdrop");
        var list = $("notifList");
        var clearBtn = $("clearNotifBtn");
        var phone = mq("(max-width: 640px)");
        var boot = {};
        try { boot = JSON.parse(($("amcNotifData") || {}).textContent || "{}") || {}; } catch (e) { boot = {}; }
        var readOnly = !!boot.impersonating; // a teacher "viewing as" a student must not change anything
        var isOpen = false;
        var loaded = false;
        var loading = false;
        var markInFlight = false;
        var clearing = false;

        function h(tag, cls, text) {
            var el = doc.createElement(tag);
            if (cls) el.className = cls;
            if (text != null) el.textContent = text;
            return el;
        }
        function icon(name) {
            var i = h("i", "fa-solid " + name);
            i.setAttribute("aria-hidden", "true");
            return i;
        }

        /* badge */
        function badgeCount() {
            var b = $("notifBadge");
            return b ? parseInt(b.textContent, 10) || 0 : 0;
        }
        function setBadge(n) {
            var b = $("notifBadge");
            if (n > 0) {
                if (!b) {
                    b = h("span", "notif-badge");
                    b.id = "notifBadge";
                    btn.appendChild(b);
                }
                b.textContent = n > 99 ? "99+" : String(n);
            } else if (b) {
                b.parentNode.removeChild(b);
            }
            btn.setAttribute("aria-label", n > 0 ? "Notifications, " + n + " unread" : "Notifications");
        }

        /* list */
        var ICONS = { test: "fa-file-pen", notice: "fa-bullhorn", note: "fa-book-open", chat: "fa-comments" };
        function stateBox(ic, text, retry) {
            var box = h("div", "notif-empty");
            box.appendChild(icon(ic));
            box.appendChild(doc.createTextNode(text));
            if (retry) {
                var b = h("button", "btn btn-sm btn-outline", "Try again");
                b.type = "button";
                b.addEventListener("click", function () { load(false); });
                box.appendChild(b);
            }
            return box;
        }
        function render(items) {
            list.textContent = "";
            if (!items.length) {
                list.appendChild(stateBox("fa-bell-slash", "You have no notifications"));
            } else {
                items.forEach(function (n) {
                    var type = ICONS[n.type] ? n.type : "info";
                    var a = h("a", "notif-item" + (n.is_read ? "" : " is-new"));
                    a.href = n.link || "/dashboard";
                    a.setAttribute("data-id", String(n.id));
                    var ic = h("span", "notif-item-ic " + type);
                    ic.appendChild(icon(ICONS[type] || "fa-circle-info"));
                    var bodyEl = h("span", "notif-item-body");
                    bodyEl.appendChild(h("span", "notif-item-title", n.title));
                    bodyEl.appendChild(h("span", "notif-item-msg", n.message));
                    bodyEl.appendChild(h("span", "notif-item-time", String(n.created_at || "").slice(0, 16).replace("T", " ")));
                    a.appendChild(ic);
                    a.appendChild(bodyEl);
                    list.appendChild(a);
                });
            }
            if (clearBtn) clearBtn.disabled = !items.length || readOnly;
        }
        function load(markAfter) {
            if (loading) return;
            loading = true;
            list.setAttribute("aria-busy", "true");
            if (!loaded) { list.textContent = ""; list.appendChild(stateBox("fa-spinner fa-spin", "Loading…")); }
            fetch("/api/notifications", { credentials: "same-origin", cache: "no-store", headers: { Accept: "application/json" } })
                .then(function (r) { if (!r.ok) throw new Error("load failed"); return r.json(); })
                .then(function (data) {
                    loaded = true;
                    render(data.notifications || []);
                    place();
                    if (markAfter && (data.unread_count || 0) > 0) markAllRead(data.unread_count);
                    else if (!(data.unread_count || 0)) setBadge(0);
                })
                .catch(function () {
                    list.textContent = "";
                    list.appendChild(stateBox("fa-triangle-exclamation", "Could not load notifications.", true));
                })
                .then(function () { loading = false; list.removeAttribute("aria-busy"); });
        }

        /* bell click = mark everything read (one request per opening, never repeated while in flight) */
        function markAllRead(before) {
            if (readOnly || markInFlight) return;
            markInFlight = true;
            var prev = before != null ? before : badgeCount();
            setBadge(0); // instant feedback
            postJSON("/api/notifications/mark-read", {})
                .catch(function () {
                    setBadge(prev);
                    toast("error", "Could not update notifications. Please try again.");
                })
                .then(function () { markInFlight = false; });
        }

        /* All Clean */
        function clearAll() {
            if (readOnly || clearing) return;
            var ask = window.AMC && window.AMC.confirm
                ? window.AMC.confirm({
                    title: "Clear all notifications?",
                    message: "Your notification history will be cleared. Notices posted by your teacher stay on your dashboard, and other students are not affected.",
                    confirmText: "Clear all",
                    cancelText: "Cancel",
                    danger: true
                })
                : Promise.resolve(window.confirm("Clear all your notifications?"));
            ask.then(function (ok) {
                if (!ok) return;
                clearing = true;
                clearBtn.disabled = true;
                postJSON("/api/notifications/clear", {})
                    .then(function () {
                        render([]);
                        setBadge(0);
                        toast("success", "Notifications cleared.");
                    })
                    .catch(function () {
                        clearBtn.disabled = false;
                        toast("error", "Could not clear notifications. Please try again.");
                    })
                    .then(function () { clearing = false; });
            });
        }

        /* placement: always inside the viewport, list scrolls on its own */
        function place() {
            if (!isOpen) return;
            if (phone.matches) { // phones: full width sheet below the top bar (see .notif-panel in app.css)
                panel.style.left = panel.style.right = panel.style.top = panel.style.width = panel.style.maxHeight = "";
                return;
            }
            var vw = root.clientWidth;
            var vh = (window.visualViewport && window.visualViewport.height) || window.innerHeight;
            var r = btn.getBoundingClientRect();
            var width = Math.min(380, vw - 16);
            var right = Math.max(8, vw - r.right);
            if (vw - right - width < 8) right = Math.max(8, vw - 8 - width);
            var top = Math.max(8, r.bottom + 8);
            panel.style.left = "auto";
            panel.style.right = right + "px";
            panel.style.top = top + "px";
            panel.style.width = width + "px";
            panel.style.maxHeight = Math.max(180, vh - top - 12) + "px";
        }

        function setOpen(open) {
            if (open === isOpen) return;
            isOpen = open;
            if (open) {
                panel.removeAttribute("hidden");
                if (backdrop) backdrop.removeAttribute("hidden");
            } else {
                panel.setAttribute("hidden", "");
                if (backdrop) backdrop.setAttribute("hidden", "");
            }
            btn.setAttribute("aria-expanded", open ? "true" : "false");
            if (phone.matches) { if (open) lockScroll(); else unlockScroll(); }
            if (open) {
                place();
                load(!readOnly);
                try { panel.focus({ preventScroll: true }); } catch (e) { /* old browser */ }
                if (window.AMC && window.AMC.push && window.AMC.push.refresh) window.AMC.push.refresh();
            }
        }
        panel.setAttribute("tabindex", "-1");
        list.setAttribute("data-scroll-ok", "");
        btn.addEventListener("click", function (e) { e.stopPropagation(); setOpen(!isOpen); });
        if (backdrop) backdrop.addEventListener("click", function () { setOpen(false); });
        doc.addEventListener("click", function (e) {
            if (isOpen && !panel.contains(e.target) && !btn.contains(e.target)) setOpen(false);
        });
        doc.addEventListener("keydown", function (e) {
            if (e.key === "Escape" && isOpen) { setOpen(false); btn.focus(); }
        });
        list.addEventListener("click", function (e) {
            if (e.target.closest && e.target.closest("a.notif-item")) setOpen(false);
        });
        if (clearBtn) clearBtn.addEventListener("click", function (e) { e.preventDefault(); clearAll(); });
        window.addEventListener("resize", place);
        if (window.visualViewport) window.visualViewport.addEventListener("resize", place);
        onMq(phone, function () {
            if (isOpen) { // switching between phone and desktop layout while open: re-balance the scroll lock
                if (phone.matches) lockScroll(); else unlockScroll();
                place();
            }
        });
        window.addEventListener("pageshow", function (e) { if (e.persisted && isOpen) setOpen(false); });

        // used by notify-center.js when a pop-up is acknowledged
        window.AMC = window.AMC || {};
        window.AMC.bell = {
            acknowledged: function (ids) {
                (ids || []).forEach(function (id) {
                    var el = list.querySelector('.notif-item[data-id="' + id + '"]');
                    if (el) el.classList.remove("is-new");
                });
                setBadge(Math.max(0, badgeCount() - (ids || []).length));
            }
        };
    })();

    /* ───────────── 5. phone / browser alerts (Web Push) on this device ───────────── */
    (function push() {
        var box = $("pushBox");
        if (!box) return; // push not configured on the server, or a teacher is viewing as a student
        var status = $("pushStatus");
        var help = $("pushHelp");
        var enableBtn = $("pushEnableBtn");
        var disableBtn = $("pushDisableBtn");
        var PENDING = "amc-push-unsub";
        var supported = "serviceWorker" in navigator && "PushManager" in window && "Notification" in window;
        var busy = false;

        function b64ToBytes(s) {
            var pad = "=".repeat((4 - (s.length % 4)) % 4);
            var raw = atob((s + pad).replace(/-/g, "+").replace(/_/g, "/"));
            var out = new Uint8Array(raw.length);
            for (var i = 0; i < raw.length; i++) out[i] = raw.charCodeAt(i);
            return out;
        }
        function view(text, helpText, canEnable, canDisable) {
            box.removeAttribute("hidden");
            status.textContent = text;
            if (helpText) { help.textContent = helpText; help.removeAttribute("hidden"); } else { help.setAttribute("hidden", ""); }
            enableBtn.hidden = !canEnable;
            disableBtn.hidden = !canDisable;
            enableBtn.disabled = busy;
            disableBtn.disabled = busy;
        }
        function registration() { return navigator.serviceWorker.ready; }

        // the real state comes from the browser (permission + this device's subscription), never from a guess
        function refresh() {
            if (!supported) { view("This browser cannot show alerts.", "", false, false); return Promise.resolve(); }
            if (Notification.permission === "denied") {
                view("Notifications are blocked for this site.",
                    "To turn them on again, allow notifications for this site in your browser settings.", false, false);
                return Promise.resolve();
            }
            return registration().then(function (reg) { return reg.pushManager.getSubscription(); }).then(function (sub) {
                if (sub && Notification.permission === "granted") {
                    view("Notifications are ON for this device.",
                        "", false, true);
                } else if (Notification.permission === "granted") {
                    view("Notifications are OFF for this device.",
                        "Your browser still allows this site to send notifications. To block them completely, change the site permission in your browser settings.",
                        true, false);
                } else {
                    view("Notifications are OFF for this device.", "", true, false);
                }
            }).catch(function () { view("Could not check notification status.", "", false, false); });
        }

        function enable() {
            if (busy || !supported) return;
            busy = true;
            refresh();
            Notification.requestPermission().then(function (perm) {
                if (perm !== "granted") throw new Error(perm === "denied" ? "blocked" : "dismissed");
                return Promise.all([registration(), fetch("/api/push/public-key", { credentials: "same-origin" }).then(function (r) { return r.json(); })]);
            }).then(function (pair) {
                var reg = pair[0];
                var key = pair[1].publicKey;
                if (!key) throw new Error("no key");
                return reg.pushManager.getSubscription().then(function (sub) {
                    return sub || reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: b64ToBytes(key) });
                });
            }).then(function (sub) {
                return postJSON("/api/push/subscribe", sub.toJSON());
            }).then(function () {
                try { localStorage.removeItem(PENDING); } catch (e) { /* private mode */ }
                toast("success", "Notifications are on for this device.");
            }).catch(function (err) {
                if (err && err.message === "dismissed") toast("info", "Notifications were not turned on.");
                else if (err && err.message === "blocked") toast("warning", "Notifications are blocked. Allow them in your browser settings.");
                else toast("error", "Could not turn on notifications. Please try again.");
            }).then(function () { busy = false; refresh(); });
        }

        function tellServer(endpoint) {
            return postJSON("/api/push/unsubscribe", { endpoint: endpoint }).then(function () {
                try { localStorage.removeItem(PENDING); } catch (e) { /* private mode */ }
            });
        }
        function disable() {
            if (busy || !supported) return;
            busy = true;
            refresh();
            var endpoint = "";
            registration().then(function (reg) { return reg.pushManager.getSubscription(); }).then(function (sub) {
                if (!sub) return null;
                endpoint = sub.endpoint;
                return sub.unsubscribe();
            }).then(function () {
                if (!endpoint) return null;
                try { localStorage.setItem(PENDING, endpoint); } catch (e) { /* private mode */ }
                return tellServer(endpoint); // removes ONLY this device's row
            }).then(function () {
                toast("success", "Notifications are off for this device.");
            }).catch(function () {
                toast("error", "Could not turn off notifications. Please try again.");
            }).then(function () { busy = false; refresh(); });
        }

        if (enableBtn) enableBtn.addEventListener("click", function (e) { e.preventDefault(); e.stopPropagation(); enable(); });
        if (disableBtn) disableBtn.addEventListener("click", function (e) { e.preventDefault(); e.stopPropagation(); disable(); });

        // on every page load: finish an interrupted "turn off", or quietly keep an existing subscription saved
        if (supported) {
            var pending = null;
            try { pending = localStorage.getItem(PENDING); } catch (e) { pending = null; }
            var start = pending ? tellServer(pending).catch(function () { /* try again next visit */ }) : Promise.resolve();
            start.then(function () {
                if (Notification.permission !== "granted") return null;
                return registration().then(function (reg) { return reg.pushManager.getSubscription(); }).then(function (sub) {
                    if (sub) return postJSON("/api/push/subscribe", sub.toJSON()).catch(function () { /* next visit */ });
                    return null;
                });
            }).then(refresh);
        } else {
            refresh();
        }
        window.AMC = window.AMC || {};
        window.AMC.push = { refresh: refresh };
    })();
})();
