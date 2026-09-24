/* Admin > Products > Add: the category decides which fields show and which stores can be chosen;
   the chosen store is shown on the map with its address. */
(function () {
  "use strict";

  var form = document.getElementById("product-form");
  var dataNode = document.getElementById("product-stores");
  if (!form || !dataNode) return;

  var stores = JSON.parse(dataNode.textContent || "[]");
  var byId = {};
  stores.forEach(function (store) { byId[store.id] = store; });
  var select = form.querySelector("select[name='StoreId']");
  var details = document.getElementById("store-details");
  var mapEl = document.getElementById("product-store-map");
  var DURBAN = [-29.8587, 31.0218];

  function category() {
    var checked = form.querySelector("input[name='Category']:checked");
    return checked ? checked.value : "Grocery";
  }

  function sells(store, cat) {
    return cat === "Clothing" ? (store.type === "clothing" || store.type === "both")
                              : (store.type === "grocery" || store.type === "both");
  }

  /* ---- map ------------------------------------------------------------------------------- */
  var map = null, markers = {}, chosen = null;
  if (mapEl && typeof L !== "undefined") {
    var cfg = (window.App && window.App.config && window.App.config.map) || {};
    map = L.map(mapEl, { scrollWheelZoom: false }).setView(DURBAN, 11);
    L.tileLayer(cfg.tileUrl || "https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 19, attribution: cfg.attribution || "&copy; OpenStreetMap contributors"
    }).addTo(map);
  }

  function popup(store) {
    var box = document.createElement("div");
    var title = document.createElement("strong");
    title.textContent = store.name;
    var address = document.createElement("div");
    address.textContent = store.address;
    var pick = document.createElement("button");
    pick.type = "button";
    pick.className = "btn btn-sm btn-brand mt-2";
    pick.textContent = "Choose this store";
    pick.addEventListener("click", function () { select.value = store.id; showStore(); });
    box.append(title, address, pick);
    return box;
  }

  function drawMarkers(cat) {
    if (!map) return;
    Object.keys(markers).forEach(function (id) { map.removeLayer(markers[id]); });
    markers = {};
    var bounds = [];
    stores.forEach(function (store) {
      if (!sells(store, cat)) return;
      markers[store.id] = L.circleMarker([store.lat, store.lng], {
        radius: 7, color: "#2C666E", weight: 2, fillColor: "#ffffff", fillOpacity: 1
      }).addTo(map).bindPopup(popup(store));
      bounds.push([store.lat, store.lng]);
    });
    if (bounds.length && !select.value) map.fitBounds(bounds, { padding: [20, 20], maxZoom: 13 });
  }

  function showStore() {
    var store = byId[select.value];
    if (details) details.hidden = !store;
    if (!store) return;
    details.querySelector("[data-store='name']").textContent = store.name;
    details.querySelector("[data-store='brand']").textContent = store.brand + (store.suburb ? " · " + store.suburb : "");
    details.querySelector("[data-store='address']").textContent = store.address;
    if (!map) return;
    if (chosen) map.removeLayer(chosen);
    chosen = L.marker([store.lat, store.lng], { title: store.name, keyboard: true }).addTo(map);
    chosen.bindPopup(popup(store)).openPopup();
    map.setView([store.lat, store.lng], 15);
  }

  /* ---- category ----------------------------------------------------------------------------- */
  function applyCategory() {
    var cat = category();
    form.querySelectorAll("[data-show-for]").forEach(function (el) {
      var show = el.getAttribute("data-show-for").split(" ").indexOf(cat) !== -1;
      el.hidden = !show;
      el.querySelectorAll("input, select, textarea").forEach(function (input) { input.disabled = !show; });
    });
    // Mark what this category needs (the server checks the same rules).
    var required = cat === "Clothing" ? ["Brand", "Sku", "Size", "Colour", "StockQuantity"] : ["Barcode"];
    ["Brand", "Sku", "Size", "Colour", "StockQuantity", "Barcode"].forEach(function (name) {
      var label = form.querySelector("label[for='" + name + "']");
      if (label) label.classList.toggle("required-now", required.indexOf(name) !== -1);
    });

    Array.prototype.forEach.call(select.options, function (option) {
      if (!option.value) return;
      var store = byId[option.value];
      var ok = store && sells(store, cat);
      option.hidden = !ok;
      option.disabled = !ok;
    });
    if (select.value && !sells(byId[select.value] || {}, cat)) select.value = "";
    drawMarkers(cat);
    showStore();
  }

  form.addEventListener("change", function (event) {
    if (event.target.name === "Category") applyCategory();
    if (event.target === select) showStore();
  });
  applyCategory();
  if (map) setTimeout(function () { map.invalidateSize(); }, 0);
})();
