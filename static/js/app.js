/* Arnab Math Class - small shared behaviours.
   These used to be inline onclick/onsubmit attributes; they live here so the Content-Security-Policy
   can forbid inline event handlers. Everything is a delegated listener: no per-page code needed. */
(function () {
    "use strict";

    // <form data-confirm="Are you sure?"> asks before submitting.
    document.addEventListener("submit", function (e) {
        var form = e.target;
        if (!form || !form.getAttribute) return;
        var msg = form.getAttribute("data-confirm");
        if (msg && !window.confirm(msg)) {
            e.preventDefault();
            return;
        }
        if (form.hasAttribute("data-disable-on-submit")) {
            var b = form.querySelector("button");
            if (b) setTimeout(function () { b.disabled = true; }, 0); // after the form data is read
        }
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
