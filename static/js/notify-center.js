/* Arnab Math Class - student notification pop-ups.

   What it shows (one pop-up at a time, OK unlocks after a 5 second countdown):
     * a NEW TEST notification          - only while that test is still open for this student
     * a NOTICE from the teacher        - only while the notice is still active
     * "Result published"               - when a test the student already submitted gets its marks released

   What it never shows:
     * anything the student already acknowledged (OK is saved on the server through the existing
       mark-read API, so it also holds on another phone / browser)
     * a test notification for a test the student has already taken (or that was withdrawn)
     * anything while an exam is running, or while a teacher is "viewing as" a student

   Frontend only: it reads the existing /api/v1/tests, /api/v1/results and /api/v1/announcements
   endpoints and writes through the existing /api/notifications/mark-read endpoint. */
(function () {
    "use strict";

    var dataEl = document.getElementById("amcNotifData");
    if (!dataEl) return;
    var boot;
    try {
        boot = JSON.parse(dataEl.textContent) || {};
    } catch (e) {
        return;
    }
    if (boot.impersonating) return; // a teacher must not "see" notices for the student
    if (document.body.classList.contains("exam-page")) return; // never interrupt an exam

    var SID = String(boot.sid || "0");
    var COUNTDOWN = 5; // seconds before OK works
    var MAX_POPUPS = 5;
    var CSRF =
        (document.querySelector('meta[name="csrf-token"]') || {}).content || "";
    var KEY_SEEN = "amc:" + SID + ":seen"; // notification ids acknowledged here (fallback copy)
    var KEY_RES = "amc:" + SID + ":results"; // {init:1, t:{testId:"pending"|"released"}}
    var resultPageTest = document.body.getAttribute("data-result-test") || "";

    function lsGet(k) {
        try {
            return JSON.parse(localStorage.getItem(k) || "null");
        } catch (e) {
            return null;
        }
    }
    function lsSet(k, v) {
        try {
            localStorage.setItem(k, JSON.stringify(v));
        } catch (e) {
            /* private mode */
        }
    }

    function h(tag, cls, text) {
        var el = document.createElement(tag);
        if (cls) el.className = cls;
        if (text != null) el.textContent = text;
        return el;
    }
    function icon(name) {
        var i = h("i", "fa-solid " + name);
        i.setAttribute("aria-hidden", "true");
        return i;
    }
    function chip(ic, text) {
        var c = h("span", "chip");
        c.appendChild(icon(ic));
        c.appendChild(document.createTextNode(" " + text));
        return c;
    }

    function api(url) {
        return fetch(url, {
            credentials: "same-origin",
            cache: "no-store",
            headers: { Accept: "application/json" }
        })
            .then(function (r) {
                return r.ok ? r.json() : null;
            })
            .then(function (j) {
                return j && j.ok ? j.data : null;
            })
            .catch(function () {
                return null;
            });
    }
    function markRead(ids) {
        if (!ids.length) return Promise.resolve();
        return fetch("/api/notifications/mark-read", {
            method: "POST",
            credentials: "same-origin",
            keepalive: true,
            headers: {
                "Content-Type": "application/json",
                "X-CSRFToken": CSRF
            },
            body: JSON.stringify({ ids: ids })
        }).catch(function () {
            /* the local copy below still prevents a repeat on this device */
        });
    }

    /* ── bell (top bar) stays in sync with what was acknowledged ── */
    function syncBell(ids) {
        var list = document.getElementById("notifList");
        ids.forEach(function (id) {
            var n =
                list && list.querySelector('.notif-item[data-id="' + id + '"]');
            if (n) n.parentNode.removeChild(n);
        });
        var left = list ? list.querySelectorAll(".notif-item").length : 0;
        var badge = document.getElementById("notifBadge");
        if (badge) {
            if (left) badge.textContent = String(left);
            else badge.parentNode.removeChild(badge);
        }
        if (list && !left && !list.querySelector(".notif-empty")) {
            var e = h("div", "notif-empty");
            e.appendChild(icon("fa-bell-slash"));
            e.appendChild(document.createTextNode("No new notifications"));
            list.appendChild(e);
        }
    }

    var seen = lsGet(KEY_SEEN) || [];
    function rememberSeen(ids) {
        ids.forEach(function (id) {
            if (seen.indexOf(id) < 0) seen.push(id);
        });
        lsSet(KEY_SEEN, seen.slice(-200));
    }
    function setResultState(testId, state) {
        var s = lsGet(KEY_RES) || { init: 1, t: {} };
        s.t[String(testId)] = state;
        s.init = 1;
        lsSet(KEY_RES, s);
    }

    function when(iso) {
        if (!iso) return "";
        var d = new Date(String(iso).replace(" ", "T"));
        if (isNaN(d.getTime())) return "";
        return d.toLocaleString("en-GB", {
            day: "numeric",
            month: "short",
            hour: "numeric",
            minute: "2-digit",
            hour12: true
        });
    }

    /* ───────────── building the queue ───────────── */
    function buildQueue() {
        var raw = (boot.items || []).filter(function (n) {
            return (
                (n.type === "test" || n.type === "notice") &&
                seen.indexOf(n.id) < 0
            );
        });
        var wantTests = raw.some(function (n) {
            return n.type === "test";
        });
        var wantNotices = raw.some(function (n) {
            return n.type === "notice";
        });

        return Promise.all([
            wantTests
                ? api("/api/v1/tests?status=available&per_page=100")
                : Promise.resolve(null),
            wantNotices
                ? api("/api/v1/announcements?per_page=30")
                : Promise.resolve(null),
            api("/api/v1/tests?status=attempted&per_page=100"),
            api("/api/v1/results?per_page=100")
        ]).then(function (r) {
            var open = r[0],
                notices = r[1],
                attempted = r[2],
                released = r[3];
            var queue = [],
                silent = [];

            raw.forEach(function (n) {
                if (n.type === "test") {
                    if (open === null) return; // could not check -> stay quiet rather than risk a wrong pop-up
                    var title = String(n.title || "")
                        .replace(/^New test:\s*/i, "")
                        .trim();
                    var match = null;
                    open.forEach(function (t) {
                        if (!match && String(t.title).trim() === title)
                            match = t;
                    });
                    if (!match) {
                        silent.push(n.id);
                        return;
                    } // already taken, withdrawn or not for this student
                    queue.push({
                        kind: "test",
                        nid: n.id,
                        title: n.title,
                        message: n.message,
                        time: n.created_at,
                        chips: [
                            chip(
                                "fa-stopwatch",
                                match.duration_minutes + " min"
                            ),
                            chip(
                                "fa-circle-question",
                                match.question_count + " questions"
                            ),
                            chip(
                                "fa-bullseye",
                                fmt(match.total_marks) + " marks"
                            )
                        ],
                        action: {
                            label: "Start test",
                            href: "/test/" + match.id
                        }
                    });
                } else {
                    var nt = String(n.title || "")
                        .replace(/^Notice:\s*/i, "")
                        .trim();
                    var full = null;
                    if (notices !== null) {
                        notices.forEach(function (a) {
                            if (!full && String(a.title).trim() === nt)
                                full = a;
                        });
                        if (!full) {
                            silent.push(n.id);
                            return;
                        } // notice was removed / switched off
                    }
                    queue.push({
                        kind: "notice",
                        nid: n.id,
                        title: nt || n.title,
                        message: full ? full.content : n.message,
                        time: n.created_at,
                        chips: []
                    });
                }
            });

            // results: pending -> released since the student last looked
            if (attempted !== null && released !== null) {
                var rel = {};
                released.forEach(function (x) {
                    rel[String(x.test_id)] = x;
                });
                var st = lsGet(KEY_RES);
                var firstRun = !st || !st.init;
                var map = (st && st.t) || {};
                attempted.forEach(function (t) {
                    var id = String(t.id),
                        now = rel[id] ? "released" : "pending",
                        prev = map[id];
                    if (firstRun || !prev) {
                        map[id] = now;
                        return;
                    }
                    if (prev === "pending" && now === "released") {
                        if (resultPageTest === id) {
                            map[id] = "released";
                            return;
                        } // they are looking at it right now
                        var x = rel[id];
                        var pc = Math.round(x.percentage);
                        queue.push({
                            kind: "result",
                            rid: id,
                            title: "Your result is out",
                            message:
                                "The marks for “" +
                                t.title +
                                "” have been published.",
                            time: x.submitted_at,
                            chips: [
                                chip(
                                    "fa-star",
                                    fmt(x.score) + " / " + fmt(x.total_marks)
                                ),
                                chip(
                                    x.passed
                                        ? "fa-circle-check"
                                        : "fa-rotate-right",
                                    pc +
                                        "% · " +
                                        (x.passed
                                            ? "Passed"
                                            : "Keep practising")
                                )
                            ],
                            action: {
                                label: "View result",
                                href: "/test/" + id + "/result"
                            }
                        });
                    } else {
                        map[id] = now;
                    }
                });
                lsSet(KEY_RES, { init: 1, t: map });
            }

            if (silent.length) {
                rememberSeen(silent);
                markRead(silent).then(function () {
                    syncBell(silent);
                });
                syncBell(silent);
            }

            var order = { result: 0, test: 1, notice: 2 };
            queue.sort(function (a, b) {
                return order[a.kind] - order[b.kind];
            });
            return queue.slice(0, MAX_POPUPS);
        });
    }
    function fmt(v) {
        var n = Math.round(Number(v) * 100) / 100;
        return isNaN(n) ? String(v) : String(n);
    }

    /* ───────────── the pop-up ───────────── */
    var KIND = {
        test: { icon: "fa-file-pen", label: "New test" },
        notice: { icon: "fa-bullhorn", label: "Notice" },
        result: { icon: "fa-chart-column", label: "Result published" }
    };
    var open = false;

    function acknowledge(item) {
        if (item.kind === "result") {
            setResultState(item.rid, "released");
            return Promise.resolve();
        }
        rememberSeen([item.nid]);
        syncBell([item.nid]);
        return markRead([item.nid]);
    }

    function show(queue, index) {
        if (index >= queue.length) {
            open = false;
            return;
        }
        var item = queue[index],
            cfg = KIND[item.kind];
        open = true;
        var prevFocus = document.activeElement;

        var back = h("div", "amc-pop-back");
        var pop = h("div", "amc-pop " + item.kind);
        pop.setAttribute("role", "alertdialog");
        pop.setAttribute("aria-modal", "true");
        pop.setAttribute("aria-labelledby", "amcPopT");
        pop.setAttribute("aria-describedby", "amcPopM");
        pop.tabIndex = -1;

        var body = h("div", "amc-pop-body");
        var ic = h("div", "amc-pop-ic");
        ic.appendChild(icon(cfg.icon));
        var kind = h("div", "amc-pop-kind");
        kind.appendChild(icon(cfg.icon));
        kind.appendChild(document.createTextNode(" " + cfg.label));
        var title = h("h2", "amc-pop-title", item.title);
        title.id = "amcPopT";
        var msg = h("div", "amc-pop-msg", item.message || "");
        msg.id = "amcPopM";
        body.appendChild(ic);
        body.appendChild(kind);
        body.appendChild(title);
        body.appendChild(msg);
        var meta = h("div", "amc-pop-meta");
        item.chips.forEach(function (c) {
            meta.appendChild(c);
        });
        var t = when(item.time);
        if (t) meta.appendChild(chip("fa-clock", t));
        if (meta.childNodes.length) body.appendChild(meta);

        var foot = h("div", "amc-pop-foot");
        if (queue.length > 1) {
            var cnt = h("div", "amc-pop-count");
            cnt.appendChild(h("span", null, index + 1 + " of " + queue.length));
            var dots = h("div", "amc-pop-dots");
            queue.forEach(function (_, i) {
                dots.appendChild(h("i", i === index ? "on" : ""));
            });
            cnt.appendChild(dots);
            foot.appendChild(cnt);
        }
        var actions = h("div", "amc-pop-actions");
        var go = null;
        if (item.action) {
            go = h("button", "btn btn-outline", item.action.label);
            go.type = "button";
            go.setAttribute("aria-disabled", "true");
            go.disabled = true;
            actions.appendChild(go);
        }
        var ok = h("button", "btn btn-primary amc-ok locked");
        ok.type = "button";
        ok.setAttribute("aria-disabled", "true");
        var ring = document.createElementNS(
            "http://www.w3.org/2000/svg",
            "svg"
        );
        ring.setAttribute("class", "ring-svg");
        ring.setAttribute("viewBox", "0 0 24 24");
        ring.setAttribute("aria-hidden", "true");
        ["trk", "prg"].forEach(function (c) {
            var ci = document.createElementNS(
                "http://www.w3.org/2000/svg",
                "circle"
            );
            ci.setAttribute("class", c);
            ci.setAttribute("cx", "12");
            ci.setAttribute("cy", "12");
            ci.setAttribute("r", "9");
            ci.setAttribute("pathLength", "100");
            ring.appendChild(ci);
        });
        ring.style.setProperty("--cd", COUNTDOWN + "s");
        var label = h("span", "amc-ok-label", "OK (" + COUNTDOWN + ")");
        ok.appendChild(ring);
        ok.appendChild(label);
        actions.appendChild(ok);
        foot.appendChild(actions);

        pop.appendChild(h("div", "amc-pop-top"));
        pop.appendChild(body);
        pop.appendChild(foot);
        back.appendChild(pop);
        document.body.appendChild(back);
        document.body.classList.add("amc-lock");
        pop.focus();

        var left = COUNTDOWN,
            unlocked = false,
            finished = false,
            timer;
        timer = setInterval(function () {
            if (document.hidden) {
                ring.style.setProperty("--cd-state", "paused");
                return;
            } // only count while the student can see it
            ring.style.setProperty("--cd-state", "running");
            left -= 1;
            if (left > 0) {
                label.textContent = "OK (" + left + ")";
                return;
            }
            clearInterval(timer);
            unlocked = true;
            ok.classList.remove("locked");
            ok.removeAttribute("aria-disabled");
            if (ring.parentNode) ring.parentNode.removeChild(ring);
            ok.insertBefore(icon("fa-check"), label);
            label.textContent = "OK";
            if (go) {
                go.disabled = false;
                go.removeAttribute("aria-disabled");
            }
            ok.focus();
        }, 1000);
        document.addEventListener("visibilitychange", function vis() {
            if (finished) {
                document.removeEventListener("visibilitychange", vis);
                return;
            }
            ring.style.setProperty(
                "--cd-state",
                document.hidden ? "paused" : "running"
            );
        });

        function close(after) {
            if (finished) return;
            finished = true;
            clearInterval(timer);
            document.removeEventListener("keydown", onKey, true);
            acknowledge(item).then(function () {
                /* saved */
            });
            back.style.transition = "opacity .2s";
            back.style.opacity = "0";
            setTimeout(function () {
                if (back.parentNode) back.parentNode.removeChild(back);
                document.body.classList.remove("amc-lock");
                if (after) {
                    after();
                    return;
                }
                if (index + 1 < queue.length)
                    setTimeout(function () {
                        show(queue, index + 1);
                    }, 160);
                else {
                    open = false;
                    if (prevFocus && prevFocus.focus) {
                        try {
                            prevFocus.focus();
                        } catch (e) {
                            /* gone */
                        }
                    }
                }
            }, 190);
        }
        function nudge() {
            // pressed too early: gently shake the dialog
            pop.animate(
                [
                    { transform: "translateX(0)" },
                    { transform: "translateX(-6px)" },
                    { transform: "translateX(6px)" },
                    { transform: "translateX(0)" }
                ],
                { duration: 240 }
            );
        }
        ok.addEventListener("click", function () {
            if (!unlocked) {
                nudge();
                return;
            }
            close();
        });
        if (go)
            go.addEventListener("click", function () {
                if (!unlocked) {
                    nudge();
                    return;
                }
                close(function () {
                    window.location.href = item.action.href;
                });
            });
        function onKey(e) {
            if (e.key === "Escape") {
                e.preventDefault();
                e.stopPropagation();
                if (unlocked) close();
                else nudge();
                return;
            }
            if (e.key === "Tab") {
                var f = [go, ok].filter(function (x) {
                    return x && !x.disabled;
                });
                if (!f.length) {
                    e.preventDefault();
                    return;
                }
                var i = f.indexOf(document.activeElement);
                e.preventDefault();
                f[(i + (e.shiftKey ? f.length - 1 : 1)) % f.length].focus();
            }
        }
        document.addEventListener("keydown", onKey, true);
    }

    function start() {
        if (open) return;
        buildQueue().then(function (queue) {
            if (queue.length) show(queue, 0);
        });
    }
    if (document.readyState === "complete") setTimeout(start, 400);
    else
        window.addEventListener("load", function () {
            setTimeout(start, 400);
        });
})();
