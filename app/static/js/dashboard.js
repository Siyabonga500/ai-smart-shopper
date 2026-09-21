/* Dashboard (Step 8): the weather card (from /api/weather) and the small "% used" ring. */
(function () {
  "use strict";
  if (!window.App) return;
  var App = window.App;

  // Budget Status donut: brand teal below 85% used, orange from 85%.
  var ring = document.getElementById("status-ring");
  if (ring) {
    var percent = parseFloat(ring.dataset.percent) || 0;
    App.drawRing(ring.querySelector("canvas"), percent, { fill: App.cssVar("--chart-allocated", "#0094A0"), warnFill: App.cssVar("--chart-used", "#D9541E") });
  }

  var card = document.getElementById("weather-card");
  if (!card) return;
  var $ = function (id) { return document.getElementById(id); };
  var live = null;                                  // {lat, lng} once the student shares their location

  function setLoading() {                            // grey shimmer bars in place of the numbers
    $("wx-skeleton").hidden = false;
    $("wx-content").hidden = true;
    $("wx-error").hidden = true;
    $("wx-body").hidden = false;
  }

  function show(data) {
    $("wx-skeleton").hidden = true;
    $("wx-content").hidden = false;
    $("wx-body").hidden = false;
    $("wx-error").hidden = true;
    $("wx-place").textContent = data.place || "";
    $("wx-icon").className = "bi bi-" + String(data.icon || "cloud-sun").replace(/[^a-z-]/g, "") + " wx-icon";
    $("wx-temp").textContent = data.temperature_c + "°C";
    $("wx-condition").textContent = data.condition;
    $("wx-range").textContent = "H: " + data.high_c + "°  L: " + data.low_c + "°";
    $("wx-humidity").textContent = "Humidity " + data.humidity + "%";
  }

  function fail(message) {
    $("wx-body").hidden = true;
    $("wx-error-text").textContent = message;
    $("wx-error").hidden = false;
  }

  function load() {
    setLoading();
    var url = card.dataset.url;
    if (live) url += "?lat=" + encodeURIComponent(live.lat) + "&lng=" + encodeURIComponent(live.lng);
    App.api(url).then(show).catch(function (error) {
      App.errorMessage(error, "The weather is not available right now.").then(fail);
    });
  }

  $("wx-retry").addEventListener("click", load);

  // Without a saved home address the card shows Durban; the student can share their live location instead.
  var locate = $("wx-locate");
  if (card.dataset.canLocate === "1" && navigator.geolocation) {
    locate.hidden = false;
    locate.addEventListener("click", function () {
      locate.disabled = true;
      navigator.geolocation.getCurrentPosition(function (position) {
        live = { lat: position.coords.latitude.toFixed(4), lng: position.coords.longitude.toFixed(4) };
        locate.hidden = true;
        load();
      }, function () {
        locate.disabled = false;
        fail("We could not get your location. Showing Durban instead.");
        setTimeout(load, 2500);
      }, { timeout: 8000, maximumAge: 600000 });
    });
  }

  load();
})();
