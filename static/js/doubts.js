/* Doubts: quick-actions menu, Ask-a-Doubt form (inline validation, safe retry), My Doubts filters, image zoom,
   copy answer, teacher answer save, "Ask AI" bridge. No inline handlers (CSP). */
(function () {
    "use strict";
    if (window.__doubtsJs) return;
    window.__doubtsJs = true;
    var doc = document;
    function $(id) { return doc.getElementById(id); }
    function toast(t, m) { if (window.AMC && window.AMC.toast) window.AMC.toast(t, m); }

    // ---- quick actions menu ----
    var btn = $("qaBtn"), menu = $("qaMenu");
    if (btn && menu) {
        var setOpen = function (v) { menu.hidden = !v; btn.setAttribute("aria-expanded", v ? "true" : "false"); };
        btn.addEventListener("click", function (e) { e.stopPropagation(); setOpen(menu.hidden); });
        doc.addEventListener("pointerdown", function (e) { if (!menu.hidden && !menu.contains(e.target) && e.target !== btn && !btn.contains(e.target)) setOpen(false); });
        doc.addEventListener("keydown", function (e) { if (e.key === "Escape" && !menu.hidden) { setOpen(false); btn.focus(); } });
    }

    // ---- filters: selects submit their form (inline onchange is blocked by the CSP) ----
    Array.prototype.forEach.call(doc.querySelectorAll("select[data-autosubmit]"), function (sel) {
        sel.addEventListener("change", function () {
            var list = doc.querySelector(".dbt-list"); if (list) list.classList.add("is-loading");
            sel.form.submit();
        });
    });

    // ---- image zoom (lightbox) ----
    var lb;
    function openLb(src) {
        if (!lb) {
            lb = doc.createElement("div"); lb.className = "dbt-lb"; lb.hidden = true; lb.setAttribute("role", "dialog"); lb.setAttribute("aria-label", "Picture");
            lb.innerHTML = '<button type="button" class="x" aria-label="Close picture">&times;</button><img alt="Question picture">';
            doc.body.appendChild(lb);
            lb.addEventListener("click", function (e) { if (e.target.tagName === "IMG") lb.classList.toggle("z"); else closeLb(); });
            doc.addEventListener("keydown", function (e) { if (e.key === "Escape" && !lb.hidden) closeLb(); });
        }
        lb.classList.remove("z"); lb.querySelector("img").src = src; lb.hidden = false; lb.querySelector(".x").focus();
    }
    function closeLb() { if (lb) lb.hidden = true; }
    doc.addEventListener("click", function (e) {
        var z = e.target.closest && e.target.closest("[data-zoom]");
        if (z) { e.preventDefault(); openLb(z.getAttribute("data-zoom")); }
    });

    // ---- copy answer ----
    doc.addEventListener("click", function (e) {
        var b = e.target.closest && e.target.closest("[data-copy]");
        if (!b) return;
        var src = $(b.getAttribute("data-copy")); if (!src) return;
        var text = src.getAttribute("data-raw") || src.textContent;
        var done = function () { toast("success", "Answer copied."); };
        if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(text).then(done, function () { toast("error", "Could not copy. Select the text and copy it manually."); });
        else { var t = doc.createElement("textarea"); t.value = text; doc.body.appendChild(t); t.select(); try { doc.execCommand("copy"); done(); } catch (x) { toast("error", "Could not copy."); } t.remove(); }
    });

    // ---- Ask-a-Doubt form ----
    var form = $("doubtForm");
    if (form) (function () {
        var MAX = parseInt(form.getAttribute("data-max-bytes"), 10) || 5 * 1024 * 1024, MAXMB = Math.round(MAX / 1048576);
        var sub = $("dSubmit"), busy = $("dBusy"), errBox = $("dErr"), q = form.elements.question, subj = form.elements.subject;
        var file = $("dImage"), pv = $("dPv"), cnt = $("dCount"), sending = false, DRAFT = "amc-doubt-draft";
        function fieldErr(id, msg, input) {
            var e = $(id); if (!e) return;
            e.textContent = msg || ""; e.hidden = !msg;
            if (input) { input.classList.toggle("invalid", !!msg); input.setAttribute("aria-invalid", msg ? "true" : "false"); }
        }
        function showErr(msg) { if (!errBox) return; errBox.textContent = msg || ""; errBox.hidden = !msg; if (msg) errBox.scrollIntoView({ block: "center", behavior: "smooth" }); }
        function count() { if (cnt) cnt.textContent = q.value.length + " / " + q.maxLength; }
        function validate() {
            var ok = true;
            if (!subj.value.trim()) { fieldErr("eSubject", "Please choose or type a subject.", subj); ok = false; } else fieldErr("eSubject", "", subj);
            if (q.value.trim().length < 5) { fieldErr("eQuestion", "Please write your question (at least a few words).", q); ok = false; } else fieldErr("eQuestion", "", q);
            return ok;
        }
        subj.addEventListener("blur", validate); q.addEventListener("blur", validate);
        q.addEventListener("input", function () { count(); if (q.classList.contains("invalid") && q.value.trim().length >= 5) fieldErr("eQuestion", "", q); save(); });
        subj.addEventListener("input", function () { if (subj.value.trim()) fieldErr("eSubject", "", subj); sync(); save(); });
        count();

        // subject chips
        var chips = Array.prototype.slice.call(doc.querySelectorAll("[data-subject-chip]"));
        function sync() { chips.forEach(function (c) { c.classList.toggle("on", c.getAttribute("data-subject-chip").toLowerCase() === subj.value.trim().toLowerCase()); }); }
        chips.forEach(function (c) { c.addEventListener("click", function () { subj.value = c.getAttribute("data-subject-chip"); sync(); fieldErr("eSubject", "", subj); save(); }); });
        sync();

        // draft survives refresh / accidental navigation until it is sent
        function save() { try { sessionStorage.setItem(DRAFT, JSON.stringify({ s: subj.value, c: form.elements.chapter.value, t: form.elements.topic.value, q: q.value })); } catch (e) { /* private mode */ } }
        function restore() {
            if (q.value || subj.value) return;
            try { var d = JSON.parse(sessionStorage.getItem(DRAFT) || "null"); if (d) { subj.value = d.s || ""; form.elements.chapter.value = d.c || ""; form.elements.topic.value = d.t || ""; q.value = d.q || ""; count(); sync(); } } catch (e) { /* ignore */ }
        }
        restore();
        form.elements.chapter.addEventListener("input", save); form.elements.topic.addEventListener("input", save);

        // picture: type (real header bytes) + size checks, preview, replace / remove
        function sniff(f) {
            return f.slice(0, 12).arrayBuffer().then(function (buf) {
                var b = new Uint8Array(buf), s = String.fromCharCode.apply(null, b);
                if (b[0] === 0xff && b[1] === 0xd8 && b[2] === 0xff) return true;
                if (s.indexOf("\x89PNG") === 0 || s.indexOf("GIF8") === 0) return true;
                if (s.slice(0, 4) === "RIFF" && s.slice(8, 12) === "WEBP") return true;
                return false;
            }).catch(function () { return true; });   // very old browser: the server checks again
        }
        function clearFile() { file.value = ""; pv.hidden = true; fieldErr("eImage", ""); var im = pv.querySelector("img"); if (im.src) { try { URL.revokeObjectURL(im.src); } catch (e) { /* ignore */ } im.removeAttribute("src"); } }
        function onFile(f) {
            fieldErr("eImage", "");
            if (!f) { clearFile(); return; }
            if (f.size > MAX) { clearFile(); fieldErr("eImage", "This picture is " + (f.size / 1048576).toFixed(1) + " MB. The limit is " + MAXMB + " MB - please choose a smaller one."); return; }
            sniff(f).then(function (ok) {
                if (!ok) { clearFile(); fieldErr("eImage", "This is not a JPG, PNG, WEBP or GIF picture."); return; }
                var im = pv.querySelector("img"); im.src = URL.createObjectURL(f);
                pv.querySelector(".nm").textContent = f.name;
                pv.querySelector(".sz").textContent = (f.size / 1048576 >= 0.1 ? (f.size / 1048576).toFixed(1) + " MB" : Math.max(1, Math.round(f.size / 1024)) + " KB");
                pv.hidden = false;
            });
        }
        file.addEventListener("change", function () { onFile(file.files && file.files[0]); });
        var drop = doc.querySelector(".dbt-drop");
        if (drop) {
            ["dragenter", "dragover"].forEach(function (n) { drop.addEventListener(n, function (e) { e.preventDefault(); drop.classList.add("drag"); }); });
            ["dragleave", "drop"].forEach(function (n) { drop.addEventListener(n, function (e) { e.preventDefault(); drop.classList.remove("drag"); }); });
            drop.addEventListener("drop", function (e) { var f = e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0]; if (f) { try { var dt = new DataTransfer(); dt.items.add(f); file.files = dt.files; } catch (x) { return; } onFile(f); } });
        }
        var rm = $("dRemove"), rp = $("dReplace");
        if (rm) rm.addEventListener("click", clearFile);
        if (rp) rp.addEventListener("click", function () { file.click(); });

        // send: fetch() with the SAME token on every retry -> the server never creates the doubt twice
        function setBusy(v) {
            sending = v; sub.disabled = v; sub.classList.toggle("is-busy", v); form.setAttribute("aria-busy", v ? "true" : "false");
            if (busy) busy.hidden = !v;
        }
        form.addEventListener("submit", function (e) {
            if (sending) { e.preventDefault(); return; }
            showErr("");
            if (!validate()) { e.preventDefault(); (subj.value.trim() ? q : subj).focus(); return; }
            if (!window.fetch || !window.FormData || !window.AbortController) { setBusy(true); return; }   // plain POST fallback
            e.preventDefault();
            setBusy(true);
            var ctl = new AbortController(), timer = setTimeout(function () { ctl.abort(); }, 120000);
            fetch(form.action, { method: "POST", body: new FormData(form), credentials: "same-origin", signal: ctl.signal,
                headers: { "X-Requested-With": "fetch", "Accept": "application/json" } })
                .then(function (r) {
                    if (r.redirected && !/json/.test(r.headers.get("content-type") || "")) { window.location.href = r.url; return null; }   // rate limit / login expired
                    return r.json().then(function (j) { return { r: r, j: j }; }, function () { return { r: r, j: null }; });
                })
                .then(function (x) {
                    if (!x) return;
                    clearTimeout(timer);
                    if (x.j && x.j.ok) { try { sessionStorage.removeItem(DRAFT); } catch (e2) { /* ignore */ } window.location.href = x.j.redirect; return; }
                    setBusy(false);
                    var j = x.j || {};
                    var msg = j.error || "Your doubt was not sent. Please try again.";
                    if (j.field === "subject") fieldErr("eSubject", msg, subj);
                    else if (j.field === "question") fieldErr("eQuestion", msg, q);
                    else if (j.field === "image") fieldErr("eImage", msg);
                    else showErr(msg);
                    if (j.field === "image" && j.retryable === false) showErr("Your doubt was not sent. Fix the picture (or remove it) and tap Send again.");
                })
                .catch(function (err) {
                    clearTimeout(timer); setBusy(false);
                    showErr((err && err.name === "AbortError" ? "This is taking too long. " : "Connection problem. ") +
                        "Your doubt may or may not have been sent - tap Send again. It is safe: the same doubt is never created twice, and everything you typed is still here.");
                });
        });
        window.addEventListener("pageshow", function (e) { if (e.persisted) setBusy(false); });
    })();

    // ---- teacher answer: save without leaving the page ----
    var tf = $("answerForm");
    if (tf) (function () {
        var ta = tf.elements.answer, b = $("aSave"), st = $("aStatus"), cnt = $("aCount"), dirty = false;
        function c() { if (cnt) cnt.textContent = ta.value.length + " / " + ta.maxLength; }
        ta.addEventListener("input", function () { dirty = true; c(); if (st) { st.textContent = ""; st.className = "dbt-saved"; } });
        c();
        window.addEventListener("beforeunload", function (e) { if (dirty) { e.preventDefault(); e.returnValue = ""; } });
        tf.addEventListener("submit", function (e) {
            if (!window.fetch) { dirty = false; return; }
            e.preventDefault();
            if (ta.value.trim().length < 2) { st.textContent = "Please write an answer first."; st.className = "dbt-saved err"; ta.focus(); return; }
            b.disabled = true; b.classList.add("is-busy"); st.textContent = "Saving…"; st.className = "dbt-saved";
            fetch(tf.action, { method: "POST", body: new FormData(tf), credentials: "same-origin", headers: { "X-Requested-With": "fetch", "Accept": "application/json" } })
                .then(function (r) { return r.json().then(function (j) { return { r: r, j: j }; }, function () { return { r: r, j: {} }; }); })
                .then(function (x) {
                    b.disabled = false; b.classList.remove("is-busy");
                    var j = x.j || {};
                    if (x.r.ok && j.ok) {
                        dirty = false; st.textContent = "✓ " + j.message; st.className = j.notified === false ? "dbt-saved err" : "dbt-saved";
                        var badge = $("aBadge"); if (badge) { badge.textContent = "Answered"; badge.className = "dbt-chip done"; }
                        b.textContent = "Save changes";
                    } else { st.textContent = j.message || j.error || "Not saved. Please try again."; st.className = "dbt-saved err"; }
                })
                .catch(function () { b.disabled = false; b.classList.remove("is-busy"); st.textContent = "Connection problem - your text is still here. Tap Save again."; st.className = "dbt-saved err"; });
        });
    })();

    // ---- profile picture: submit as soon as a file is chosen ----
    var av = doc.querySelector("input[data-autosubmit]");
    if (av) av.addEventListener("change", function () {
        var f = av.files && av.files[0];
        if (!f) return;
        if (f.size > 3 * 1024 * 1024) { toast("error", "The picture must be smaller than 3 MB."); av.value = ""; return; }
        av.form.submit();
    });

    // ---- "Ask AI" on a doubt page -> opens the existing AI chat with that doubt selected ----
    doc.addEventListener("click", function (e) {
        var b = e.target.closest && e.target.closest("[data-ask-ai]");
        if (!b) return;
        var id = parseInt(b.getAttribute("data-ask-ai"), 10);
        if (window.AMCAI && window.AMCAI.openDoubt) window.AMCAI.openDoubt(id);
        else toast("info", "The AI assistant is still loading. Please try again in a moment.");
    });
})();
