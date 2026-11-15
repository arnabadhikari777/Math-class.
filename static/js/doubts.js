/* Doubts: quick-actions menu (all student pages), doubt form protection, "Ask AI" bridge to the AI widget. */
(function () {
    "use strict";
    if (window.__doubtsJs) return;
    window.__doubtsJs = true;

    // ---- quick actions menu ----
    var btn = document.getElementById("qaBtn"), menu = document.getElementById("qaMenu");
    if (btn && menu) {
        function setOpen(v) {
            menu.hidden = !v;
            btn.setAttribute("aria-expanded", v ? "true" : "false");
        }
        btn.addEventListener("click", function (e) { e.stopPropagation(); setOpen(menu.hidden); });
        document.addEventListener("click", function (e) { if (!menu.hidden && !menu.contains(e.target)) setOpen(false); });
        document.addEventListener("keydown", function (e) { if (e.key === "Escape" && !menu.hidden) { setOpen(false); btn.focus(); } });
    }

    // ---- doubt form: no double submit, picture preview ----
    var form = document.getElementById("doubtForm");
    if (form) {
        var sub = document.getElementById("dSubmit"), busy = document.getElementById("dBusy");
        form.addEventListener("submit", function (e) {
            if (form.dataset.sent === "1") { e.preventDefault(); return; }
            var s = form.elements.subject.value.trim(), q = form.elements.question.value.trim();
            if (!s || q.length < 5) { e.preventDefault(); (s ? form.elements.question : form.elements.subject).focus(); return; }
            form.dataset.sent = "1";
            sub.disabled = true;
            if (busy) busy.hidden = false;
        });
        var file = document.getElementById("dImage"), prev = document.getElementById("dPreview");
        if (file && prev) file.addEventListener("change", function () {
            var f = file.files && file.files[0];
            if (!f) { prev.hidden = true; return; }
            if (f.size > 5 * 1024 * 1024) { alert("This picture is bigger than 5 MB. Please choose a smaller one."); file.value = ""; prev.hidden = true; return; }
            prev.src = URL.createObjectURL(f); prev.hidden = false;
        });
    }

    // ---- profile picture: submit as soon as a file is chosen ----
    var av = document.querySelector("input[data-autosubmit]");
    if (av) av.addEventListener("change", function () {
        var f = av.files && av.files[0];
        if (!f) return;
        if (f.size > 3 * 1024 * 1024) { alert("The picture must be smaller than 3 MB."); av.value = ""; return; }
        av.form.submit();
    });

    // ---- "Ask AI" on a doubt page -> opens the existing AI chat with that doubt selected ----
    document.addEventListener("click", function (e) {
        var b = e.target.closest && e.target.closest("[data-ask-ai]");
        if (!b) return;
        var id = parseInt(b.getAttribute("data-ask-ai"), 10);
        if (window.AMCAI && window.AMCAI.openDoubt) window.AMCAI.openDoubt(id);
        else alert("The AI assistant is still loading. Please try again in a moment.");
    });
})();
