/* Shopping History maps: every store used ([data-history-map]) and, for each trip, the way from the student's
   residence to the stores of that shopping list and back ([data-trip-map]). */
(function () {
  "use strict";

  if (typeof L === "undefined") return;
  var cfg = (window.App && window.App.config && window.App.config.map) || {};

  function tiles(map) {
    L.tileLayer(cfg.tileUrl || "https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 19,
      attribution: cfg.attribution || "&copy; OpenStreetMap contributors"
    }).addTo(map);
  }

  function escapeHtml(value) {
    return String(value || "").replace(/[&<>\"']/g, function (character) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;", "'": "&#39;" }[character];
    });
  }

  function pin(label, cls) {
    var safe = /^[0-9S]{1,3}$/.test(label) ? label : "";
    return L.divIcon({ className: "route-pin-wrap", html: '<span class="route-pin ' + cls + '">' + safe + "</span>", iconSize: [26, 26], iconAnchor: [13, 13] });
  }

  /* ---- all stores used ------------------------------------------------------------------- */
  var root = document.querySelector("[data-history-map]");
  if (root) {
    var points = JSON.parse(root.getAttribute("data-points") || "[]");
    if (points.length) {
      var map = L.map(root, { scrollWheelZoom: false });
      tiles(map);
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
    }
  }

  /* ---- one map per trip (drawn when it scrolls into view) ------------------------------------- */
  function drawTrip(el) {
    if (el.getAttribute("data-drawn")) return;
    el.setAttribute("data-drawn", "1");
    var trip = JSON.parse(el.getAttribute("data-trip-map") || "{}");
    if (!trip.home || !trip.stops || !trip.stops.length) return;
    var map = L.map(el, { scrollWheelZoom: false, dragging: false, zoomControl: false, doubleClickZoom: false, touchZoom: false, keyboard: false });
    tiles(map);
    var home = [trip.home.lat, trip.home.lng];
    var line = [home];
    L.marker(home, { icon: pin("S", "start"), title: "Your residence" }).addTo(map).bindPopup("<strong>Your residence</strong>");
    trip.stops.forEach(function (stop) {
      var latlng = [stop.lat, stop.lng];
      line.push(latlng);
      var items = (stop.items || []).slice(0, 8).map(escapeHtml).join("<br>");
      L.marker(latlng, { icon: pin(String(stop.order), "stop"), title: stop.order + ". " + stop.name }).addTo(map)
        .bindPopup("<strong>" + stop.order + ". " + escapeHtml(stop.name) + "</strong>" +
          (stop.address ? "<br><small>" + escapeHtml(stop.address) + "</small>" : "") +
          (items ? "<br>" + items + ((stop.items || []).length > 8 ? "<br>…" : "") : ""));
    });
    line.push(home);
    var path = L.polyline(line, { color: "#2C666E", weight: 4, opacity: 0.8, dashArray: "6 6" }).addTo(map);
    map.fitBounds(path.getBounds(), { padding: [22, 22], maxZoom: 14 });
    setTimeout(function () { map.invalidateSize(); }, 0);
  }

  var tripMaps = document.querySelectorAll("[data-trip-map]");
  if (!tripMaps.length) return;
  if ("IntersectionObserver" in window) {
    var observer = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (entry.isIntersecting) { drawTrip(entry.target); observer.unobserve(entry.target); }
      });
    }, { rootMargin: "200px" });
    tripMaps.forEach(function (el) { observer.observe(el); });
  } else {
    tripMaps.forEach(drawTrip);
  }
})();
