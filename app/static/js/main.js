/* AI Smart Shopper - shared front-end helpers (vanilla JS). */
(function () {
  "use strict";

  var config = window.APP_CONFIG || {};

  /** Format a number as ZAR the same way the server does: R1 750, R29.99, -R72.88 */
  function formatZAR(value, cents) {
    var n = Number(value) || 0;
    var negative = n < 0;
    var abs = Math.abs(n);
    var isWhole = Math.round(abs * 100) % 100 === 0;
    var showCents = cents === true || ((cents === undefined || cents === "auto") && !isWhole);
    var parts = abs.toFixed(showCents ? 2 : 0).split(".");
    parts[0] = parts[0].replace(/\B(?=(\d{3})+(?!\d))/g, " ");
    return (negative ? "-" : "") + (config.currencySymbol || "R") + parts.join(".");
  }

  /** CSRF token from <meta name="csrf-token"> (Flask-WTF). */
  function csrfToken() {
    var meta = document.querySelector('meta[name="csrf-token"]');
    return meta ? meta.content : "";
  }

  /** fetch() wrapper that sends the CSRF header and JSON, and throws on non-2xx. */
  function api(url, options) {
    options = options || {};
    var headers = Object.assign(
      { "X-CSRFToken": csrfToken(), Accept: "application/json" },
      options.body && !(options.body instanceof FormData) ? { "Content-Type": "application/json" } : {},
      options.headers || {}
    );
    var body = options.body;
    if (body && !(body instanceof FormData) && typeof body !== "string") body = JSON.stringify(body);
    return fetch(url, Object.assign({}, options, { headers: headers, body: body, credentials: "same-origin" }))
      .then(function (response) {
        if (!response.ok) {
          var error = new Error("Request failed: " + response.status);
          error.response = response;
          throw error;
        }
        return response.status === 204 ? null : response.json();
      });
  }

  /** Turn a failed App.api() call into a message for the student (uses the server's JSON "error" when there is one). */
  function errorMessage(error, fallback) {
    fallback = fallback || "Something went wrong. Please try again.";
    if (error && error.response) {
      return error.response.json().then(function (body) { return (body && body.error) || fallback; }, function () { return fallback; });
    }
    return Promise.resolve("We could not reach the server. Check your connection and try again.");
  }

  /** Only let http(s) URLs and site-relative paths into href/src attributes. */
  function safeUrl(url) {
    if (!url || typeof url !== "string") return null;
    if (url.charAt(0) === "/" && url.charAt(1) !== "/") return url;
    try {
      var parsed = new URL(url);
      return parsed.protocol === "https:" || parsed.protocol === "http:" ? parsed.href : null;
    } catch (e) { return null; }
  }

  /** A price like R29⁹⁹: <span class="price"><span class="price-cur">R</span>29<sup>99</sup></span>. */
  function priceEl(value, className) {
    var n = Number(value) || 0;
    var span = document.createElement("span");
    span.className = "price" + (className ? " " + className : "");
    var cur = document.createElement("span");
    cur.className = "price-cur"; cur.textContent = config.currencySymbol || "R";
    var abs = Math.abs(n), cents = Math.round(abs * 100) % 100, whole = Math.floor(Math.round(abs * 100) / 100);
    span.appendChild(cur);
    span.appendChild(document.createTextNode((n < 0 ? "-" : "") + String(whole).replace(/\B(?=(\d{3})+(?!\d))/g, "\u00a0")));
    if (cents) {
      var sup = document.createElement("sup");
      sup.textContent = (cents < 10 ? "0" : "") + cents;
      span.appendChild(sup);
    }
    return span;
  }

  /** Show / hide password buttons: <button data-toggle-password="<input id>">. */
  document.addEventListener("click", function (event) {
    var button = event.target.closest("[data-toggle-password]");
    if (!button) return;
    var input = document.getElementById(button.getAttribute("data-toggle-password"));
    if (!input) return;
    var show = input.type === "password";
    input.type = show ? "text" : "password";
    button.setAttribute("aria-pressed", show ? "true" : "false");
    button.setAttribute("aria-label", show ? "Hide password" : "Show password");
    var icon = button.querySelector("i");
    if (icon) icon.className = show ? "bi bi-eye-slash" : "bi bi-eye";
  });

  /** Parse what a student types into an amount box. Same rules as app/utils/money.py parse_amount().
   *  Returns a number of rands, or null when it is not a number. */
  function parseAmount(text) {
    if (text == null) return null;
    var cleaned = String(text).replace(/[\s\u00a0]/g, "");
    if (/^[Rr]/.test(cleaned)) cleaned = cleaned.slice(1);
    if (/^\d{1,3}(,\d{3})+(\.\d{1,2})?$/.test(cleaned)) cleaned = cleaned.replace(/,/g, "");
    else if (/^\d+([.,]\d{1,2})?$/.test(cleaned)) cleaned = cleaned.replace(",", ".");
    else return null;
    return Math.round(parseFloat(cleaned) * 100) / 100;
  }

  /** Read a CSS custom property (chart colours live in app.css). */
  function cssVar(name, fallback) {
    var value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    return value || fallback;
  }

  var reduceMotion = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  /** A ring meter for "% used" (Chart.js doughnut). The arc is capped at a full ring; the number in the middle is
   *  drawn in HTML by the page. options: {fill, warnFill, track}. Turns to warnFill from 85%. */
  function drawRing(canvas, percent, options) {
    if (!canvas || typeof Chart === "undefined") return null;
    options = options || {};
    var shown = Math.max(0, Math.min(100, Number(percent) || 0));
    var fill = (Number(percent) >= 85 ? options.warnFill : options.fill) || options.fill || cssVar("--brand", "#2C666E");
    return new Chart(canvas, {
      type: "doughnut",
      data: { datasets: [{ data: [shown, 100 - shown], backgroundColor: [fill, options.track || cssVar("--chart-track", "#DCE7E8")],
                           borderWidth: 0, hoverOffset: 0 }] },
      options: { cutout: "74%", responsive: true, maintainAspectRatio: true, animation: reduceMotion ? false : { duration: 500 },
                 events: [], plugins: { legend: { display: false }, tooltip: { enabled: false } } }
    });
  }

  /** A small message that fades away (Bootstrap toast). Optional link, e.g. App.toast("Added.", "View list", "/list"). */
  function toast(text, linkText, linkHref) {
    if (typeof bootstrap === "undefined") return;
    var holder = document.getElementById("toast-holder");
    if (!holder) {
      var stack = document.createElement("div");
      stack.className = "toast-stack";
      stack.setAttribute("aria-live", "polite");
      stack.setAttribute("aria-atomic", "true");
      holder = document.createElement("div");
      holder.id = "toast-holder";
      stack.appendChild(holder);
      document.body.appendChild(stack);
    }
    var el = document.createElement("div");
    el.className = "toast align-items-center text-bg-dark border-0";
    el.setAttribute("role", "status");
    var flex = document.createElement("div");
    flex.className = "d-flex";
    var body = document.createElement("div");
    body.className = "toast-body";
    body.appendChild(document.createTextNode(text));
    var href = linkText ? safeUrl(linkHref) : null;
    if (href) {
      var link = document.createElement("a");
      link.className = "ms-2 text-white fw-600";
      link.href = href;
      link.textContent = linkText;
      body.appendChild(link);
    }
    var close = document.createElement("button");
    close.type = "button";
    close.className = "btn-close btn-close-white me-2 m-auto";
    close.setAttribute("data-bs-dismiss", "toast");
    close.setAttribute("aria-label", "Close");
    flex.appendChild(body);
    flex.appendChild(close);
    el.appendChild(flex);
    holder.appendChild(el);
    el.addEventListener("hidden.bs.toast", function () { el.remove(); });
    new bootstrap.Toast(el, { delay: 3500 }).show();
  }

  // A product picture that will not load is swapped for an icon (mark the <img> with data-fallback="<bootstrap icon>").
  document.addEventListener("error", function (event) {
    var img = event.target;
    if (!img || img.tagName !== "IMG" || !img.hasAttribute("data-fallback")) return;
    var icon = document.createElement("i");
    icon.className = "bi bi-" + (img.getAttribute("data-fallback") || "image");
    icon.setAttribute("aria-hidden", "true");
    img.replaceWith(icon);
  }, true);

  window.App = { config: config, formatZAR: formatZAR, csrfToken: csrfToken, api: api, parseAmount: parseAmount,
                 cssVar: cssVar, drawRing: drawRing, reduceMotion: reduceMotion, errorMessage: errorMessage,
                 safeUrl: safeUrl, priceEl: priceEl, toast: toast };
})();
