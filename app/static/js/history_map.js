(function () {
  "use strict";

  var root = document.querySelector("[data-history-map]");
  if (!root || typeof L === "undefined") return;

  var points = JSON.parse(root.getAttribute("data-points") || "[]");
  if (!points.length) return;

  var cfg = (window.App && window.App.config && window.App.config.map) || {};
  var map = L.map(root, { scrollWheelZoom: false });
  L.tileLayer(cfg.tileUrl || "https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19,
    attribution: cfg.attribution || "&copy; OpenStreetMap contributors"
  }).addTo(map);

  var bounds = [];
  points.forEach(function (point) {
    var marker = L.marker([point.lat, point.lng]).addTo(map);
    marker.bindPopup("<strong>" + escapeHtml(point.name) + "</strong><br>" +
      point.purchases + " purchase" + (point.purchases === 1 ? "" : "s") +
      (point.address ? "<br>" + escapeHtml(point.address) : ""));
    bounds.push([point.lat, point.lng]);
  });
  map.fitBounds(bounds, { padding: [24, 24], maxZoom: 14 });
  setTimeout(function () { map.invalidateSize(); }, 0);

  function escapeHtml(value) {
    return String(value || "").replace(/[&<>\"']/g, function (character) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;", "'": "&#39;" }[character];
    });
  }
})();
