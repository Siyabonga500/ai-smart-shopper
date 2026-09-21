/* Address + map picker (Leaflet + OpenStreetMap). Works on any element with [data-map-picker]
   (see templates/partials/map_picker.html).

   Three ways to set the address:
     1. type it and press "Find on map"  -> /api/geocode  (Nominatim)  -> pin
     2. click (or drag the pin on) the map -> /api/reverse-geocode      -> address text
     3. "use my current location"         -> navigator.geolocation      -> pin + address text

   The pin position lives in two hidden inputs named Latitude / Longitude. Editing the address
   text by hand clears them (the old pin no longer matches); the server then looks the new address
   up itself if the user does not press "Find on map".

   Each picker element gets a small API:  el.mapPicker = { setAddress(text, {search}), getLocation(),
   setLocation(lat, lng, {reverse}), search(query) }  and fires a "mappicker:change" event.        */
(function () {
  "use strict";

  var cfg = (window.App && window.App.config && window.App.config.map) || {};
  var DURBAN = cfg.center || { lat: -29.8587, lng: 31.0218 };
  var DEFAULT_ZOOM = cfg.zoom || 12;
  var PIN_ZOOM = 17;

  function init(root) {
    if (root.mapPicker || typeof L === "undefined") return;

    var addressInput = root.querySelector("input[type=text], textarea");
    var latInput = root.querySelector("input[name=Latitude]");
    var lngInput = root.querySelector("input[name=Longitude]");
    var searchButton = root.querySelector('[data-role="search"]');
    var locateButton = root.querySelector('[data-role="locate"]');
    var statusEl = root.querySelector('[data-role="status"]');
    var resultsEl = root.querySelector('[data-role="results"]');
    var mapEl = root.querySelector('[data-role="map"]');
    if (!addressInput || !latInput || !lngInput || !mapEl) return;

    var hasPin = isFinite(parseFloat(latInput.value)) && isFinite(parseFloat(lngInput.value));
    var start = hasPin ? { lat: parseFloat(latInput.value), lng: parseFloat(lngInput.value) } : DURBAN;
    var map = L.map(mapEl, { scrollWheelZoom: false }).setView([start.lat, start.lng], hasPin ? PIN_ZOOM : DEFAULT_ZOOM);
    L.tileLayer(cfg.tileUrl || "https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 19,
      attribution: cfg.attribution || "&copy; OpenStreetMap contributors"
    }).addTo(map);

    var marker = null;
    var lookupToken = 0;             // ignore answers that arrive after a newer request
    var originalStatus = statusEl ? statusEl.innerHTML : "";

    function say(message, kind) {
      if (!statusEl) return;
      statusEl.textContent = message;
      statusEl.classList.toggle("text-danger", kind === "error");
      statusEl.classList.toggle("text-brand", kind === "ok");
    }

    function clearResults() {
      if (!resultsEl) return;
      resultsEl.innerHTML = "";
      resultsEl.classList.add("d-none");
    }

    function writeLocation(lat, lng) {
      latInput.value = Number(lat).toFixed(6);
      lngInput.value = Number(lng).toFixed(6);
    }

    function clearLocation() {
      latInput.value = "";
      lngInput.value = "";
      if (marker) { map.removeLayer(marker); marker = null; }
    }

    function placeMarker(lat, lng) {
      if (!marker) {
        marker = L.marker([lat, lng], { draggable: true, title: "Drag to adjust" }).addTo(map);
        marker.on("dragend", function () {
          var p = marker.getLatLng();
          setLocation(p.lat, p.lng, { reverse: true });
        });
      } else {
        marker.setLatLng([lat, lng]);
      }
    }

    function fire() {
      root.dispatchEvent(new CustomEvent("mappicker:change", { bubbles: true, detail: getLocation() }));
    }

    function getLocation() {
      var lat = parseFloat(latInput.value), lng = parseFloat(lngInput.value);
      return isFinite(lat) && isFinite(lng) ? { lat: lat, lng: lng } : null;
    }

    function explain(error, what) {
      var status = error && error.response && error.response.status;
      if (status === 429) return "Too many lookups in a short time. Wait a moment and try again.";
      if (status === 401) return "Your session has expired. Sign in again to use the address lookup.";
      if (status === 502 || status === 503) return what + " is unavailable right now. You can still type your address.";
      return "Something went wrong looking that up. You can still type your address.";
    }

    function setLocation(lat, lng, options) {
      options = options || {};
      writeLocation(lat, lng);
      placeMarker(lat, lng);
      map.setView([lat, lng], Math.max(map.getZoom(), PIN_ZOOM));
      clearResults();
      if (options.reverse) {
        var token = ++lookupToken;
        say("Looking up the address…");
        fetchJson("/api/reverse-geocode?lat=" + encodeURIComponent(lat) + "&lng=" + encodeURIComponent(lng))
          .then(function (data) {
            if (token !== lookupToken) return;
            if (data && data.address) {
              addressInput.value = data.address;
              addressInput.classList.remove("is-invalid");
              say("Address filled in from the map. Drag the pin if it is slightly off.", "ok");
            } else {
              say("Pin set, but we could not find a street address there. Type it in below.");
            }
          })
          .catch(function (error) {
            if (token === lookupToken) say("Pin set. " + explain(error, "Address lookup"), "error");
          });
      } else {
        say("Pin set.", "ok");
      }
      fire();
    }

    function fetchJson(url) {
      return fetch(url, { headers: { Accept: "application/json" }, credentials: "same-origin" }).then(function (response) {
        if (!response.ok) {
          var error = new Error("HTTP " + response.status);
          error.response = response;
          throw error;
        }
        return response.json();
      });
    }

    function showResults(results) {
      resultsEl.innerHTML = "";
      results.forEach(function (result) {
        var item = document.createElement("button");
        item.type = "button";
        item.className = "list-group-item list-group-item-action small";
        item.textContent = result.display_name;
        item.addEventListener("click", function () {
          setLocation(result.lat, result.lng, { reverse: false });
          say("Pin set to “" + result.display_name + "”. Your typed address is kept as it is.", "ok");
        });
        resultsEl.appendChild(item);
      });
      resultsEl.classList.remove("d-none");
    }

    function search(query) {
      query = (query || addressInput.value || "").trim();
      if (query.length < 3) {
        say("Type at least the street and suburb first.", "error");
        return Promise.resolve([]);
      }
      var token = ++lookupToken;
      say("Searching…");
      searchButton && (searchButton.disabled = true);
      return fetchJson("/api/geocode?q=" + encodeURIComponent(query))
        .then(function (data) {
          if (token !== lookupToken) return [];
          var results = (data && data.results) || [];
          if (!results.length) {
            clearResults();
            say("We could not find that address. Try adding the suburb, or click the map instead.", "error");
          } else if (results.length === 1) {
            setLocation(results[0].lat, results[0].lng, { reverse: false });
            say("Found it. Drag the pin if it is slightly off.", "ok");
          } else {
            setLocation(results[0].lat, results[0].lng, { reverse: false });
            showResults(results);
            say("Pin set to the best match. Not right? Pick another below.", "ok");
          }
          return results;
        })
        .catch(function (error) {
          if (token === lookupToken) say(explain(error, "Address search"), "error");
          return [];
        })
        .then(function (results) {
          searchButton && (searchButton.disabled = false);
          return results;
        });
    }

    map.on("click", function (event) { setLocation(event.latlng.lat, event.latlng.lng, { reverse: true }); });

    if (searchButton) searchButton.addEventListener("click", function () { search(); });
    addressInput.addEventListener("keydown", function (event) {
      if (event.key === "Enter") { event.preventDefault(); search(); }
    });
    addressInput.addEventListener("input", function () {
      // The text no longer describes the pin, so forget the pin until the user re-finds it.
      if (getLocation()) {
        clearLocation();
        clearResults();
        say("Address changed. Press “Find on map” to update the pin.");
        fire();
      }
    });

    if (locateButton) {
      locateButton.addEventListener("click", function () {
        if (!navigator.geolocation) {
          say("Your browser cannot share your location. Type your address or click the map.", "error");
          return;
        }
        say("Waiting for your location…");
        navigator.geolocation.getCurrentPosition(
          function (position) { setLocation(position.coords.latitude, position.coords.longitude, { reverse: true }); },
          function (error) {
            var messages = {
              1: "Location permission was denied. Allow it in your browser, or type your address.",
              2: "Your position is not available right now. Type your address or click the map.",
              3: "Finding your location took too long. Try again, or click the map."
            };
            say(messages[error.code] || "Could not get your location.", "error");
          },
          { enableHighAccuracy: true, timeout: 10000, maximumAge: 60000 }
        );
      });
    }

    if (hasPin) placeMarker(start.lat, start.lng);
    setTimeout(function () { map.invalidateSize(); }, 0);

    root.mapPicker = {
      map: map,
      getLocation: getLocation,
      setLocation: setLocation,
      search: search,
      /** Put text in the address box. With {search: true} also look the pin up. */
      setAddress: function (text, options) {
        addressInput.value = text || "";
        addressInput.classList.remove("is-invalid");
        clearLocation();
        clearResults();
        if (options && options.search && text) return search(text);
        if (statusEl) {
          statusEl.innerHTML = originalStatus;
          statusEl.classList.remove("text-danger", "text-brand");
        }
        fire();
        return Promise.resolve([]);
      }
    };
  }

  function initAll() { document.querySelectorAll("[data-map-picker]").forEach(init); }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", initAll);
  else initAll();

  window.MapPicker = { init: init, initAll: initAll };
})();
