/* Arnab Math Class - UI layer.
   Toasts (replaces the old flash banners), confirm dialog (replaces window.confirm),
   light/dark theme switch and "busy" submit buttons.
   Public API:  AMC.toast(type, message, {title, duration, sticky})   type: success | error | warning | info
                AMC.confirm({title, message, confirmText, cancelText, danger}) -> Promise<boolean>
   No inline handlers anywhere (the Content-Security-Policy forbids them). */
(function () {
    "use strict";

    var doc = document;
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

    /* ───────────── Toasts ───────────── */
    var TYPES = {
        success: { icon: "fa-circle-check", title: "Done", ms: 4200 },
        error: {
            icon: "fa-circle-exclamation",
            title: "Something went wrong",
            ms: 7500
        },
        warning: {
            icon: "fa-triangle-exclamation",
            title: "Please note",
            ms: 6500
        },
        info: { icon: "fa-circle-info", title: "Info", ms: 5000 }
    };
    var ALIAS = {
        message: "success",
        danger: "error",
        warn: "warning",
        notice: "info"
    };
    var MAX_VISIBLE = 4;
    var stack = null;
    var recent = {};

    function getStack() {
        if (stack && doc.body.contains(stack)) return stack;
        stack = h("div", "amc-toasts");
        stack.setAttribute("role", "region");
        stack.setAttribute("aria-label", "Notifications");
        stack.setAttribute("aria-live", "polite");
        doc.body.appendChild(stack);
        return stack;
    }

    function toast(type, message, opts) {
        opts = opts || {};
        type = ALIAS[type] || type;
        if (!TYPES[type]) type = "info";
        message = message == null ? "" : String(message);
        if (!message && !opts.title) return null;

        var key = type + "|" + message; // same message twice within 1.2 s -> show once
        var now = Date.now();
        if (recent[key] && now - recent[key] < 1200) return null;
        recent[key] = now;

        var cfg = TYPES[type];
        var root = getStack();
        var el = h("div", "amc-toast " + type);
        el.setAttribute("role", type === "error" ? "alert" : "status");

        var ic = h("div", "amc-toast-ic");
        ic.appendChild(icon(cfg.icon));
        var body = h("div", "amc-toast-body");
        if (opts.title)
            body.appendChild(h("div", "amc-toast-title", opts.title));
        if (message) body.appendChild(h("div", "amc-toast-msg", message));
        var x = h("button", "amc-toast-x");
        x.type = "button";
        x.setAttribute("aria-label", "Dismiss");
        x.appendChild(icon("fa-xmark"));

        el.appendChild(ic);
        el.appendChild(body);
        el.appendChild(x);

        var total =
            opts.duration || cfg.ms + Math.max(0, message.length - 60) * 30;
        var bar = null;
        if (!opts.sticky) {
            bar = h("div", "amc-toast-time");
            bar.style.setProperty("--dur", total + "ms");
            el.appendChild(bar);
        }
        root.appendChild(el);

        // keep the stack short: drop the oldest ones
        var all = root.querySelectorAll(".amc-toast:not(.leaving)");
        for (var i = 0; i < all.length - MAX_VISIBLE; i++) dismiss(all[i]);

        var left = total,
            started = 0,
            timer = null,
            closed = false;
        function start() {
            if (opts.sticky || closed) return;
            started = Date.now();
            clearTimeout(timer);
            timer = setTimeout(function () {
                dismiss(el);
            }, left);
        }
        function pause() {
            if (opts.sticky || closed || !started) return;
            clearTimeout(timer);
            left -= Date.now() - started;
            started = 0;
            el.classList.add("paused");
        }
        function resume() {
            el.classList.remove("paused");
            if (!started) start();
        }

        el.addEventListener("mouseenter", pause);
        el.addEventListener("mouseleave", resume);
        el.addEventListener("focusin", pause);
        el.addEventListener("focusout", resume);
        x.addEventListener("click", function () {
            dismiss(el);
        });
        el._amcStop = function () {
            closed = true;
            clearTimeout(timer);
        };

        // swipe sideways to dismiss (phones)
        var sx = null,
            dx = 0;
        el.addEventListener("pointerdown", function (e) {
            if (e.target.closest("button")) return;
            sx = e.clientX;
            dx = 0;
            pause();
        });
        el.addEventListener("pointermove", function (e) {
            if (sx == null) return;
            dx = e.clientX - sx;
            el.style.transition = "none";
            el.style.transform = "translateX(" + dx + "px)";
            el.style.opacity = String(Math.max(0.2, 1 - Math.abs(dx) / 220));
        });
        function release() {
            if (sx == null) return;
            sx = null;
            el.style.transition = "";
            if (Math.abs(dx) > 70) {
                el.style.transform =
                    "translateX(" + (dx > 0 ? 130 : -130) + "%)";
                dismiss(el);
            } else {
                el.style.transform = "";
                el.style.opacity = "";
                resume();
            }
        }
        el.addEventListener("pointerup", release);
        el.addEventListener("pointercancel", release);

        start();
        return el;
    }

    function dismiss(el) {
        if (!el || el.classList.contains("leaving")) return;
        if (el._amcStop) el._amcStop();
        el.classList.add("leaving");
        setTimeout(function () {
            if (el.parentNode) el.parentNode.removeChild(el);
        }, 260);
    }

    doc.addEventListener("visibilitychange", function () {
        // timers keep running in a hidden tab; make sure the user still sees the toast when they come back
        if (doc.hidden || !stack) return;
        Array.prototype.forEach.call(
            stack.querySelectorAll(".amc-toast"),
            function (t) {
                t.classList.remove("paused");
            }
        );
    });

    /* ───────────── Confirm dialog ───────────── */
    var dialogOpen = false;
    function confirmDialog(o) {
        o = o || {};
        return new Promise(function (resolve) {
            if (dialogOpen) {
                resolve(false);
                return;
            }
            dialogOpen = true;
            var prevFocus = doc.activeElement;
            var back = h("div", "amc-dlg-back");
            var box = h("div", "amc-dlg" + (o.danger ? " danger" : ""));
            box.setAttribute("role", "alertdialog");
            box.setAttribute("aria-modal", "true");
            var tid = "amcDlgT" + Date.now(),
                mid = "amcDlgM" + Date.now();
            box.setAttribute("aria-labelledby", tid);
            box.setAttribute("aria-describedby", mid);

            var ic = h("div", "amc-dlg-ic");
            ic.appendChild(
                icon(o.danger ? "fa-trash-can" : "fa-circle-question")
            );
            var t = h(
                "h3",
                null,
                o.title || (o.danger ? "Are you sure?" : "Please confirm")
            );
            t.id = tid;
            var p = h("p", null, o.message || "");
            p.id = mid;
            var actions = h("div", "amc-dlg-actions");
            var no = h("button", "btn btn-outline", o.cancelText || "Cancel");
            no.type = "button";
            var yes = h(
                "button",
                "btn " + (o.danger ? "btn-danger" : "btn-primary"),
                o.confirmText || (o.danger ? "Yes, delete" : "Confirm")
            );
            yes.type = "button";
            actions.appendChild(no);
            actions.appendChild(yes);
            [ic, t, p, actions].forEach(function (n) {
                box.appendChild(n);
            });
            back.appendChild(box);
            doc.body.appendChild(back);
            doc.body.classList.add("amc-lock");

            function done(v) {
                doc.removeEventListener("keydown", onKey, true);
                if (back.parentNode) back.parentNode.removeChild(back);
                doc.body.classList.remove("amc-lock");
                dialogOpen = false;
                if (prevFocus && prevFocus.focus) {
                    try {
                        prevFocus.focus();
                    } catch (e) {
                        /* element gone */
                    }
                }
                resolve(v);
            }
            function onKey(e) {
                if (e.key === "Escape") {
                    e.preventDefault();
                    e.stopPropagation();
                    done(false);
                } else if (e.key === "Tab") {
                    // keep focus inside the dialog
                    var f = [no, yes],
                        i = f.indexOf(doc.activeElement);
                    e.preventDefault();
                    f[(i + (e.shiftKey ? f.length - 1 : 1)) % f.length].focus();
                }
            }
            doc.addEventListener("keydown", onKey, true);
            no.addEventListener("click", function () {
                done(false);
            });
            yes.addEventListener("click", function () {
                done(true);
            });
            back.addEventListener("mousedown", function (e) {
                if (e.target === back) done(false);
            });
            (o.danger ? no : yes).focus();
        });
    }

    /* ───────────── Theme (auto / light / dark) ───────────── */
    var THEME_KEY = "amc-theme";
    function storedTheme() {
        try {
            return localStorage.getItem(THEME_KEY);
        } catch (e) {
            return null;
        }
    }
    function isDark() {
        var t = doc.documentElement.getAttribute("data-theme");
        if (t) return t === "dark";
        return !!(
            window.matchMedia &&
            window.matchMedia("(prefers-color-scheme: dark)").matches
        );
    }
    function paintMeta() {
        var m = doc.querySelector('meta[name="theme-color"]');
        if (m) m.setAttribute("content", isDark() ? "#0b1020" : "#ffffff");
    }
    function setTheme(t) {
        doc.documentElement.setAttribute("data-theme", t);
        try {
            localStorage.setItem(THEME_KEY, t);
        } catch (e) {
            /* private mode */
        }
        paintMeta();
        syncThemeBtn();
    }
    function syncThemeBtn() {
        var b = doc.getElementById("themeBtn");
        if (!b) return;
        b.setAttribute(
            "aria-label",
            isDark() ? "Switch to light mode" : "Switch to dark mode"
        );
        b.setAttribute("title", isDark() ? "Light mode" : "Dark mode");
    }
    function initTheme() {
        var s = storedTheme();
        if (s === "light" || s === "dark")
            doc.documentElement.setAttribute("data-theme", s);
        paintMeta();
        syncThemeBtn();
        var b = doc.getElementById("themeBtn");
        if (b)
            b.addEventListener("click", function () {
                setTheme(isDark() ? "light" : "dark");
            });
        if (window.matchMedia) {
            var mq = window.matchMedia("(prefers-color-scheme: dark)");
            var onChange = function () {
                if (!storedTheme()) {
                    paintMeta();
                    syncThemeBtn();
                }
            };
            if (mq.addEventListener) mq.addEventListener("change", onChange);
        }
    }

    /* ───────────── Server flash messages -> toasts ───────────── */
    function showFlashes() {
        var el = doc.getElementById("amcFlash");
        if (!el) return;
        var list = [];
        try {
            list = JSON.parse(el.textContent) || [];
        } catch (e) {
            list = [];
        }
        list.slice(0, 5).forEach(function (f, i) {
            setTimeout(
                function () {
                    toast(f[0], f[1]);
                },
                120 + i * 160
            );
        });
    }

    /* ───────────── Busy submit buttons (stops double clicks) ───────────── */
    function clearBusy() {
        Array.prototype.forEach.call(
            doc.querySelectorAll(".btn.is-busy"),
            function (b) {
                b.classList.remove("is-busy");
            }
        );
    }
    doc.addEventListener("submit", function (e) {
        var form = e.target;
        if (e.defaultPrevented || !form || !form.getAttribute) return;
        if ((form.getAttribute("method") || "get").toLowerCase() !== "post")
            return;
        if (
            form.hasAttribute("data-no-busy") ||
            form.id === "testForm" ||
            form.target === "_blank"
        )
            return;
        var b =
            e.submitter ||
            form.querySelector('button[type="submit"], button:not([type])');
        if (!b || !b.classList || !b.classList.contains("btn")) return;
        setTimeout(function () {
            b.classList.add("is-busy");
        }, 0);
        setTimeout(function () {
            b.classList.remove("is-busy");
        }, 9000); // safety net (e.g. a download that never navigates)
    });
    window.addEventListener("pageshow", function (e) {
        if (e.persisted) clearBusy();
    });

    /* ───────────── Show / hide password ───────────── */
    doc.addEventListener("click", function (e) {
        var b = e.target.closest
            ? e.target.closest("[data-toggle-pass]")
            : null;
        if (!b) return;
        var input = doc.getElementById(b.getAttribute("data-toggle-pass"));
        if (!input) return;
        var show = input.type === "password";
        input.type = show ? "text" : "password";
        b.setAttribute("aria-label", show ? "Hide password" : "Show password");
        var i = b.querySelector("i");
        if (i)
            i.className = show
                ? "fa-regular fa-eye-slash"
                : "fa-regular fa-eye";
        input.focus();
    });

    /* ───────────── Show / hide password ───────────── */
    doc.addEventListener("click", function (e) {
        var b = e.target.closest ? e.target.closest("[data-toggle-pass]") : null;
        if (!b) return;
        var input = doc.getElementById(b.getAttribute("data-toggle-pass"));
        if (!input) return;
        var show = input.type === "password";
        input.type = show ? "text" : "password";
        b.setAttribute("aria-label", show ? "Hide password" : "Show password");
        var i = b.querySelector("i");
        if (i) i.className = show ? "fa-regular fa-eye-slash" : "fa-regular fa-eye";
        input.focus();
    });

    /* ───────────── Boot ───────────── */
    window.AMC = window.AMC || {};
    window.AMC.toast = toast;
    window.AMC.dismissToast = dismiss;
    window.AMC.confirm = confirmDialog;
    window.AMC.setTheme = setTheme;
    window.toast = {
        success: function (m, o) {
            return toast("success", m, o);
        },
        error: function (m, o) {
            return toast("error", m, o);
        },
        warning: function (m, o) {
            return toast("warning", m, o);
        },
        info: function (m, o) {
            return toast("info", m, o);
        }
    };

    function boot() {
        initTheme();
        showFlashes();
    }
    if (doc.readyState === "loading")
        doc.addEventListener("DOMContentLoaded", boot);
    else boot();
})();
