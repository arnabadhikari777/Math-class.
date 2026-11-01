/* Renders LaTeX in questions, options and answers (exam, results, review, question lists).
   KaTeX is loaded from cdnjs only on pages that really contain math, so other pages pay nothing.
   Supported: $$...$$  \[...\]  \(...\)  $...$   If KaTeX cannot load (offline) the plain text stays readable. */
(function () {
    "use strict";
    var BASE = "https://cdnjs.cloudflare.com/ajax/libs/KaTeX/0.16.9/";
    var SELECTOR = ".q-card, .item-box, .math-render";
    var HAS_MATH = /\$\$|\\\(|\\\[|\$[^$\n]+\$/;
    var loading = null;

    function addCss() {
        if (document.querySelector('link[href*="katex"]')) return;
        var l = document.createElement("link");
        l.rel = "stylesheet";
        l.href = BASE + "katex.min.css";
        document.head.appendChild(l);
    }
    function addScript(src) {
        return new Promise(function (resolve, reject) {
            var s = document.createElement("script");
            s.src = src;
            s.async = false;
            s.onload = resolve;
            s.onerror = function () { reject(new Error("failed: " + src)); };
            document.head.appendChild(s);
        });
    }
    function load() {
        if (window.renderMathInElement) return Promise.resolve();
        if (loading) return loading;
        addCss();
        loading = addScript(BASE + "katex.min.js")
            .then(function () { return addScript(BASE + "contrib/auto-render.min.js"); });
        loading.catch(function () { loading = null; });
        return loading;
    }
    function render(root) {
        var nodes = (root || document).querySelectorAll(SELECTOR);
        var todo = [];
        Array.prototype.forEach.call(nodes, function (n) {
            if (!n.hasAttribute("data-math-done") && HAS_MATH.test(n.textContent)) todo.push(n);
        });
        if (!todo.length) return Promise.resolve();
        return load().then(function () {
            todo.forEach(function (n) {
                try {
                    window.renderMathInElement(n, {
                        delimiters: [
                            { left: "$$", right: "$$", display: true },
                            { left: "\\[", right: "\\]", display: true },
                            { left: "\\(", right: "\\)", display: false },
                            { left: "$", right: "$", display: false }
                        ],
                        throwOnError: false,
                        strict: "ignore",
                        trust: false,
                        ignoredTags: ["script", "style", "textarea", "pre", "code", "option"]
                    });
                } catch (e) { /* leave the text as it is */ }
                n.setAttribute("data-math-done", "1");
            });
        }).catch(function () { /* offline or blocked: raw text stays */ });
    }
    window.renderMathIn = render;
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", function () { render(); });
    else render();
})();
