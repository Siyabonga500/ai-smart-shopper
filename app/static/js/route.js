/* Shopping route map (Step 13): asks /api/route for the trip and draws it with Leaflet.
   Start (home), numbered store pins, and the road line. Distances and times come from the server. */
(function () {
  "use strict";

  var app = document.getElementById("route-app");
  var mapEl = document.getElementById("route-map");
  if (!app || !mapEl || typeof L === "undefined" || !window.App) return;
  var App = window.App;
  var stats = document.getElementById("route-stats");
  var note = document.getElementById("route-note");
  var mapConfig = (App.config && App.config.map) || {};

  // Stop the confirm button being pressed twice.
  var form = document.getElementById("done-form");
  if (form) form.addEventListener("submit", function () {
    var button = document.getElementById("done-confirm");
    button.disabled = true;
    button.textContent = "Saving…";
  });

  var map = L.map(mapEl, { scrollWheelZoom: false });
  L.tileLayer(mapConfig.tileUrl || "https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19, attribution: mapConfig.attribution || "&copy; OpenStreetMap contributors"
  }).addTo(map);

  function pin(latlng, label, cls, title) {
    var safe = /^[0-9S]{1,3}$/.test(label) ? label : "";            // the label is only ever a stop number or "S"
    var marker = L.marker(latlng, {
      icon: L.divIcon({ className: "route-pin-wrap", html: '<span class="route-pin ' + cls + '">' + safe + "</span>", iconSize: [30, 30], iconAnchor: [15, 15] }),
      title: title, keyboard: true
    });
    return marker.addTo(map);
  }

  function popup(title, lines) {
    var box = document.createElement("div");
    var strong = document.createElement("strong");
    strong.textContent = title;
    box.appendChild(strong);
    (lines || []).forEach(function (line) {
      if (!line) return;
      var div = document.createElement("div");
      div.textContent = line;
      box.appendChild(div);
    });
    return box;
  }

  function show(data) {
    var home = [data.home.lat, data.home.lng];
    map.setView(home, 13);
    var bounds = L.latLngBounds([home]);

    if (data.geometry && data.geometry.length > 1) {
      var line = L.polyline(data.geometry, { color: "#2C666E", weight: 5, opacity: 0.85 }).addTo(map);
      bounds.extend(line.getBounds());
    }
    pin(home, "S", "start", "Start").bindPopup(popup(data.return_home ? "Start and finish: home" : "Start: home"));
    data.stops.forEach(function (stop) {
      var latlng = [stop.lat, stop.lng];
      bounds.extend(latlng);
      pin(latlng, String(stop.order), "stop", "Stop " + stop.order + ": " + stop.name)
        .bindPopup(popup("Stop " + stop.order + ": " + stop.name, [stop.address, stop.item_count + (stop.item_count === 1 ? " item" : " items")]));
    });
    map.fitBounds(bounds, { padding: [30, 30], maxZoom: 15 });

    if (!data.stops.length) {
      stats.textContent = "None of these stores has a map position, so there is no route to show.";
      return;
    }
    stats.textContent = "";
    var distance = document.createElement("strong");
    distance.textContent = String(data.distance_km).replace(/\.0$/, "") + " km";
    var time = document.createElement("strong");
    time.textContent = data.duration_min + " min";
    stats.append("Total distance: ", distance, " • Est. travel time: ", time);
    var parts = [];
    if (data.source === "estimate") parts.push("These figures are estimated from straight-line distances because road routing was not available.");
    if (data.return_home) parts.push("The trip ends back at home.");
    if (data.unlocated && data.unlocated.length) parts.push("Not on the map (no position): " + data.unlocated.join(", ") + ".");
    note.textContent = parts.join(" ");
  }

  App.api(app.dataset.endpoint)
    .then(show)
    .catch(function (error) {
      App.errorMessage(error, "The route could not be loaded.").then(function (message) {
        stats.textContent = message + " The stores to visit are listed below.";
        stats.classList.add("text-danger");
      });
      map.setView([-29.8587, 31.0218], 11);
    });
})();
