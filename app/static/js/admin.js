/* Admin portal behaviour: dashboard charts, confirm-before-delete, copy button, busy buttons.
   No inline scripts: the chart data comes from <script type="application/json" id="admin-chart-data">. */
(function () {
  "use strict";

  // Ask before a destructive form is sent: <form data-confirm="Delete this?">
  document.addEventListener("submit", function (event) {
    var message = event.target.getAttribute && event.target.getAttribute("data-confirm");
    if (message && !window.confirm(message)) event.preventDefault();
  });

  // Show that a slow action (Sync now) is running, and stop a second click.
  document.addEventListener("submit", function (event) {
    var button = event.target.querySelector && event.target.querySelector("[data-busy-text]");
    if (!button || event.defaultPrevented) return;
    setTimeout(function () {
      button.disabled = true;
      button.textContent = button.getAttribute("data-busy-text");
    }, 0);
  });

  // <button data-copy-from="input-id">
  document.addEventListener("click", function (event) {
    var button = event.target.closest("[data-copy-from]");
    if (!button) return;
    var input = document.getElementById(button.getAttribute("data-copy-from"));
    if (!input) return;
    var done = function () { button.textContent = "Copied"; };
    if (navigator.clipboard && window.isSecureContext) navigator.clipboard.writeText(input.value).then(done, function () { input.select(); });
    else { input.select(); try { document.execCommand("copy"); done(); } catch (e) { /* the text is selected: Ctrl+C works */ } }
  });

  /* ---- dashboard charts ------------------------------------------------------------------- */
  var dataNode = document.getElementById("admin-chart-data");
  if (!dataNode || typeof Chart === "undefined") return;
  var data = JSON.parse(dataNode.textContent);
  var css = getComputedStyle(document.documentElement);
  // Series colours are the validated chart teal (#2C666E is too grey to read as a data colour) and the danger red.
  var brand = (css.getPropertyValue("--chart-allocated") || "#0094A0").trim();
  var danger = (css.getPropertyValue("--danger") || "#D9534F").trim();   // failed calls: the reserved "bad" colour
  var ink = (css.getPropertyValue("--ink-muted") || "#5D6B6E").trim();
  var grid = "rgba(93, 107, 110, .18)";
  var reduce = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  var common = {
    responsive: true, maintainAspectRatio: false, animation: reduce ? false : undefined,
    scales: {
      x: { grid: { display: false }, ticks: { color: ink }, stacked: false },
      y: { beginAtZero: true, grid: { color: grid }, border: { display: false }, ticks: { color: ink, precision: 0 } }
    }
  };

  var reg = document.getElementById("chart-registrations");
  if (reg) {
    new Chart(reg, {
      type: "bar",
      data: { labels: data.registrations.labels, datasets: [{ label: "New students", data: data.registrations.values,
        backgroundColor: brand, borderRadius: { topLeft: 4, topRight: 4 }, maxBarThickness: 36 }] },
      options: Object.assign({}, common, { plugins: { legend: { display: false }, tooltip: { intersect: false } } })
    });
  }

  var api = document.getElementById("chart-api");
  if (api) {
    var stacked = JSON.parse(JSON.stringify(common));
    stacked.scales.x.stacked = true; stacked.scales.y.stacked = true;
    new Chart(api, {
      type: "bar",
      data: { labels: data.apiCalls.labels, datasets: [
        { label: "Successful", data: data.apiCalls.ok, backgroundColor: brand, maxBarThickness: 36 },
        { label: "Failed", data: data.apiCalls.error, backgroundColor: danger, maxBarThickness: 36, borderRadius: { topLeft: 4, topRight: 4 } }
      ] },
      options: Object.assign({}, stacked, { plugins: { legend: { position: "bottom", labels: { color: ink, boxWidth: 12 } }, tooltip: { mode: "index", intersect: false } } })
    });
  }
})();
