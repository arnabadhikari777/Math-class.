/* Arnab Math Class - small shared behaviours.
   These used to be inline onclick/onsubmit attributes; they live here so the Content-Security-Policy
   can forbid inline event handlers. Everything is a delegated listener: no per-page code needed. */
(function () {
    "use strict";

    var confirmed = typeof WeakSet === "function" ? new WeakSet() : null;

    // <form data-confirm="Are you sure?"> asks (in a styled dialog) before submitting.
    // Add data-confirm-title / data-confirm-ok / data-danger="0" to tune the dialog.
    document.addEventListener("submit", function (e) {
        var form = e.target;
        if (!form || !form.getAttribute) return;
        var msg = form.getAttribute("data-confirm");
        if (msg && !(confirmed && confirmed.has(form))) {
            e.preventDefault();
            e.stopImmediatePropagation();
            var submitter = e.submitter || null;
            var danger = form.getAttribute("data-danger");
            danger = danger == null
                ? /delete|remove|purge|erase|permanent|মুছ|চিরতরে|ডিলিট/i.test(msg)
                : danger !== "0";
            var ask = window.AMC && window.AMC.confirm
                ? window.AMC.confirm({
                    title: form.getAttribute("data-confirm-title") || "",
                    message: msg,
                    confirmText: form.getAttribute("data-confirm-ok") || "",
                    danger: danger
                })
                : Promise.resolve(window.confirm(msg));
            ask.then(function (ok) {
                if (!ok) return;
                if (confirmed) confirmed.add(form);
                try {
                    if (form.requestSubmit) form.requestSubmit(submitter && submitter.form === form ? submitter : undefined);
                    else form.submit();
                } catch (err) {
                    form.submit();
                } finally {
                    if (confirmed) confirmed.delete(form);
                }
            });
            return;
        }
        if (form.hasAttribute("data-disable-on-submit")) {
            var b = form.querySelector("button");
            if (b) setTimeout(function () { b.disabled = true; }, 0); // after the form data is read
        }
    }, true);

    // <button data-copy="text" data-copy-done="Copied."> copies text to the clipboard.
    document.addEventListener("click", function (e) {
        var b = e.target.closest ? e.target.closest("[data-copy]") : null;
        if (!b) return;
        var text = b.getAttribute("data-copy") || "";
        var done = b.getAttribute("data-copy-done") || "Copied.";
        function ok() { if (window.AMC && window.AMC.toast) window.AMC.toast("success", done); }
        function fail() { if (window.AMC && window.AMC.toast) window.AMC.toast("error", "Could not copy. Please select the text and copy it."); }
        function legacy() {
            var ta = document.createElement("textarea");
            ta.value = text; ta.setAttribute("readonly", ""); ta.style.position = "fixed"; ta.style.opacity = "0";
            document.body.appendChild(ta); ta.select();
            var good = false;
            try { good = document.execCommand("copy"); } catch (err) { good = false; }
            document.body.removeChild(ta);
            good ? ok() : fail();
        }
        if (navigator.clipboard && window.isSecureContext) navigator.clipboard.writeText(text).then(ok, legacy);
        else legacy();
    });

    document.addEventListener("click", function (e) {
        var t = e.target.closest ? e.target.closest("[data-print], [data-busy-text]") : null;
        if (!t) return;
        if (t.hasAttribute("data-print")) {
            window.print();
            return;
        }
        // upload buttons: show "uploading…" and block double clicks
        var form = t.form;
        if (!form || (form.checkValidity && !form.checkValidity())) return; // let the browser show what is missing
        e.preventDefault();
        t.disabled = true;
        t.textContent = t.getAttribute("data-busy-text");
        form.submit();
    });
})();
