/* Search / Browse (Step 11): search bar, filters, sorting, product cards, details modal, add to list,
   "Recommended for you". Talks to /api/search, /api/product, /api/recommendations, /api/stores/near and
   POST /api/list/items. Everything the server sends is put on the page with textContent, never innerHTML. */
(function () {
  "use strict";

  var root = document.getElementById("search-app");
  if (!root || !window.App) return;
  var App = window.App;
  var cfg = root.dataset;
  var $ = function (id) { return document.getElementById(id); };

  var RADIUS_DEFAULT = parseInt(cfg.radiusDefault, 10) || 5;
  var RADIUS_MAX = parseInt(cfg.radiusMax, 10) || 15;
  var EMPTY_PROMPT = "Search for a product or pick a category to compare prices near you.";

  var state = { q: "", category: "", min: "", max: "", radius: RADIUS_DEFAULT, stores: new Set(), sort: "lowest",
                page: 1, pages: 1, total: 0 };
  var stores = [];
  var searchSeq = 0, modalSeq = 0, controller = null, timers = {};

  // ------------------------------------------------------------------------------------------- tiny helpers
  function h(tag, attrs, kids) {
    var el = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (key) {
      var value = attrs[key];
      if (value == null || value === false) return;
      if (key === "class") el.className = value;
      else if (key === "text") el.textContent = value;
      else if (key.slice(0, 2) === "on") el.addEventListener(key.slice(2), value);
      else el.setAttribute(key, value === true ? "" : value);
    });
    (kids || []).forEach(function (kid) {
      if (kid == null || kid === false) return;
      el.appendChild(typeof kid === "string" ? document.createTextNode(kid) : kid);
    });
    return el;
  }
  function icon(name) { return h("i", { "class": "bi bi-" + name, "aria-hidden": "true" }); }
  function debounce(name, fn, wait) { clearTimeout(timers[name]); timers[name] = setTimeout(fn, wait); }
  function km(value) { return value == null ? "" : String(value).replace(/\.0$/, "") + " km"; }
  function keyMatches(el, key) { return el.getAttribute("data-key") === key; }

  // ------------------------------------------------------------------------------------------- URL <-> state
  function readUrl() {
    var p = new URLSearchParams(location.search);
    state.q = (p.get("q") || "").trim().slice(0, 100);
    var chips = Array.prototype.map.call(document.querySelectorAll("#category-chips [data-category]"), function (c) { return c.dataset.category; });
    var category = p.get("category") || "";
    state.category = chips.indexOf(category) >= 0 ? category : "";
    state.min = p.get("min_price") || "";
    state.max = p.get("max_price") || "";
    var radius = parseFloat(p.get("radius"));
    if (isFinite(radius) && radius > 0) state.radius = Math.min(Math.round(radius), RADIUS_MAX);
    var sorts = Array.prototype.map.call($("sort").options, function (o) { return o.value; });
    state.sort = sorts.indexOf(p.get("sort")) >= 0 ? p.get("sort") : "lowest";
    (p.get("stores") || "").split(",").forEach(function (id) { if (id.trim()) state.stores.add(id.trim()); });
  }

  function queryParams(page) {
    var p = new URLSearchParams();
    if (state.q) p.set("q", state.q);
    if (state.category) p.set("category", state.category);
    if (state.min) p.set("min_price", state.min);
    if (state.max) p.set("max_price", state.max);
    if (state.radius !== RADIUS_DEFAULT) p.set("radius", state.radius);
    if (state.sort !== "lowest") p.set("sort", state.sort);
    if (state.stores.size) p.set("stores", Array.from(state.stores).join(","));
    if (page && page > 1) p.set("page", page);
    return p;
  }

  function writeUrl() {
    var query = queryParams(1).toString();
    try { history.replaceState(null, "", location.pathname + (query ? "?" + query : "")); } catch (e) { /* not essential */ }
  }

  function applyToControls() {
    $("q").value = state.q;
    $("min-price").value = state.min;
    $("max-price").value = state.max;
    $("radius").value = state.radius;
    $("radius-out").textContent = state.radius;
    $("sort").value = state.sort;
    Array.prototype.forEach.call(document.querySelectorAll("#category-chips [data-category]"), function (chip) {
      var on = chip.dataset.category === state.category;
      chip.classList.toggle("active", on);
      chip.setAttribute("aria-pressed", on ? "true" : "false");
    });
    updateFilterCount();
  }

  function updateFilterCount() {
    var n = (state.min ? 1 : 0) + (state.max ? 1 : 0) + (state.radius !== RADIUS_DEFAULT ? 1 : 0) + (state.stores.size ? 1 : 0);
    var badge = $("filters-count");
    badge.textContent = n;
    badge.hidden = n === 0;
  }

  // ------------------------------------------------------------------------------------------- messages
  function status(text, isError) {
    var el = $("status");
    el.textContent = text;
    el.classList.toggle("text-danger", !!isError);
  }

  function clearAlert() { $("alert-area").replaceChildren(); }

  function showAlert(text, level, link) {
    var box = h("div", { "class": "alert alert-" + (level || "warning") + " alert-dismissible fade show search-alert", role: "alert" }, [
      icon(level === "danger" ? "x-octagon" : "exclamation-triangle"), " ", text,
      link ? h("a", { "class": "alert-link ms-2", href: link.href, text: link.text }) : null,
      h("button", { type: "button", "class": "btn-close", "data-bs-dismiss": "alert", "aria-label": "Close" })
    ]);
    $("alert-area").replaceChildren(box);
  }

  var toast = App.toast;

  function showEmpty(text, retry) {
    $("results").replaceChildren();
    $("empty-text").textContent = text;
    var old = $("empty").querySelector("button");
    if (old) old.remove();
    if (retry) $("empty").appendChild(h("button", { type: "button", "class": "btn btn-outline-brand btn-sm mt-3", text: "Try again", onclick: retry }));
    $("empty").hidden = false;
    $("load-more").hidden = true;
  }

  // ------------------------------------------------------------------------------------------- budget strip
  function updateBudget(b) {
    var strip = $("budget-strip");
    if (!b || !strip) return;
    $("strip-in-list").textContent = App.formatZAR(b.in_list);
    $("strip-remaining-label").textContent = b.is_over ? "Over by" : "Remaining";
    var remaining = $("strip-remaining");
    remaining.textContent = App.formatZAR(b.is_over ? b.over_by : b.remaining);
    remaining.classList.toggle("text-over", !!b.is_over);
    var meter = strip.querySelector(".meter");
    var pct = Math.max(0, Math.min(100, Number(b.percent_used) || 0));
    meter.firstElementChild.style.width = pct + "%";
    meter.classList.toggle("warn", Number(b.percent_used) >= 85);
    meter.setAttribute("aria-valuenow", Math.round(pct));
  }

  // ------------------------------------------------------------------------------------------- product cards
  function badgeEl(text) {
    var kind = text === "CHEAPEST" ? "cheapest" : text === "CLOSEST" ? "closest" : "recommended";
    return h("span", { "class": "badge-tag " + kind, text: text });
  }

  function thumbEl(card, cls) {
    var thumb = h("div", { "class": cls || "product-thumb" });
    var src = App.safeUrl(card.image_url);
    if (src && /^https:\/\//i.test(src)) src = "/api/product-image?url=" + encodeURIComponent(src);
    var fallback = icon("image");
    if (src) {
      var img = h("img", { src: src, alt: "", loading: "lazy", decoding: "async", referrerpolicy: "no-referrer" });
      img.addEventListener("error", function () { img.remove(); thumb.appendChild(fallback); });
      thumb.appendChild(img);
    } else {
      thumb.appendChild(fallback);
    }
    return thumb;
  }

  function inListPill(qty) {
    return h("span", { "class": "in-list-pill", "data-in-list": "", hidden: qty > 0 ? null : true, text: qty > 0 ? "In list ×" + qty : "" });
  }

  function addButton(card, small) {
    var button = h("button", { type: "button", "class": "btn btn-brand" + (small === false ? "" : " btn-sm"), disabled: card.in_stock ? null : true,
      "aria-label": "Add " + card.name + " from " + (card.store_name || "this store") + " to your shopping list" }, [
      icon("cart-plus"), h("span", { "class": "lbl ms-1", text: card.in_stock ? "Add" : "Out of stock" })
    ]);
    button.addEventListener("click", function () { addToList(card, button); });
    return button;
  }

  var STOCK_TEXT = { in_stock: "In stock", low_stock: "Low stock", out_of_stock: "Out of stock" };
  function stockText(card) {
    return STOCK_TEXT[card.stock_status] || (card.in_stock ? "In stock" : "Out of stock");
  }
  function stockClass(card) {
    if (!card.in_stock) return "out-of-stock";
    return card.stock_status === "low_stock" ? "low-stock" : "in-stock";
  }

  function cardEl(card, compact) {
    var article = h("article", { "class": "product-card" + (compact ? " compact" : "") + (card.in_stock ? "" : " out"), "data-key": card.key });
    var thumb = thumbEl(card);
    thumb.setAttribute("role", "button");
    thumb.tabIndex = -1;
    thumb.addEventListener("click", function () { openProduct(card); });

    var badges = h("div", { "class": "product-badges" }, (card.badges || []).map(badgeEl).concat(
      card.tag ? [h("span", { "class": "badge-tag tag", text: card.tag })] : []));
    var name = h("h3", { "class": "product-name" }, [
      h("button", { type: "button", "class": "link-name", text: card.name, onclick: function () { openProduct(card); } })
    ]);
    var meta = h("div", { "class": "product-meta" }, [
      h("span", { "class": "text-truncate", text: card.store_name || card.retailer || "Live retailer" }),
      card.distance_km != null ? h("span", { "class": "flex-shrink-0", text: "· " + km(card.distance_km) + " away" }) : null,
      h("span", { "class": "product-stock " + stockClass(card), text: stockText(card) })
    ]);
    var source = h("div", { "class": "product-source", text: [card.retailer, card.brand, card.barcode ? "Barcode " + card.barcode : ""].filter(Boolean).join(" · ") || "Live price" });
    var why = (card.reasons && card.reasons.length) ? h("div", { "class": "product-why", text: card.reasons[0], title: card.reasons.join(". ") }) : null;
    var priceRow = h("div", { "class": "product-price-row" }, [App.priceEl(card.price, "product-price"), inListPill(card.in_list_qty || 0)]);
    // One button: Compare opens every store's price and distance from the student, and adding happens there.
    var actions = h("div", { "class": "product-actions" }, [
      h("button", { type: "button", "class": "btn btn-brand btn-sm w-100", "aria-label": "Compare prices and distances for " + card.name,
                    onclick: function () { openProduct(card); } }, [icon("arrow-left-right"), h("span", { "class": "ms-1", text: "Compare" })])
    ]);
    article.appendChild(thumb);
    article.appendChild(h("div", { "class": "product-body" }, [badges, name, meta, source, why, priceRow, actions]));
    return article;
  }

  function skeletons() {
    var frag = document.createDocumentFragment();
    for (var i = 0; i < 6; i++) {
      frag.appendChild(h("div", { "class": "product-card skeleton", "aria-hidden": "true" }, [
        h("div", { "class": "product-thumb" }), h("div", { "class": "product-body" }, [h("div", { "class": "sk-line" }), h("div", { "class": "sk-line short" })])
      ]));
    }
    return frag;
  }

  /** Every card, modal row and carousel item for one product-at-one-store shows the same "In list ×n". */
  function syncInList(key, qty) {
    Array.prototype.forEach.call(document.querySelectorAll("[data-key]"), function (el) {
      if (!keyMatches(el, key)) return;
      var pill = el.querySelector("[data-in-list]");
      if (!pill) return;
      pill.hidden = !(qty > 0);
      pill.textContent = qty > 0 ? "In list ×" + qty : "";
    });
  }

  // ------------------------------------------------------------------------------------------- add to list
  function addToList(card, button) {
    if (button.disabled || button.getAttribute("aria-disabled") === "true") return;
    var label = button.querySelector(".lbl");
    var original = label.textContent;
    button.setAttribute("aria-disabled", "true");          // not `disabled`: that would drop keyboard focus (and Esc in the modal)
    button.classList.add("disabled");
    label.textContent = "Adding…";
    App.api(cfg.addUrl, { method: "POST", body: { barcode: card.barcode, store_id: card.store_id, store_name: card.store_name, name: card.name } })
      .then(function (data) {
        syncInList(card.key, data.quantity);
        updateBudget(data.budget);
        if (data.alert) {
          showAlert(data.alert, "warning", { href: cfg.listUrl, text: "Review your list" });
        } else {
          clearAlert();
        }
        toast(data.created ? "Added to your list." : "Quantity is now " + data.quantity + ".", "View list", cfg.listUrl);
      })
      .catch(function (error) {
        if (error && error.response) {
          error.response.json().then(function (body) {
            showAlert((body && body.error) || "That item could not be added.", "danger",
                      body && body.create_budget_url ? { href: body.create_budget_url, text: "Create a budget" } : null);
          }, function () { showAlert("That item could not be added.", "danger"); });
        } else {
          showAlert("We could not reach the server. Check your connection and try again.", "danger");
        }
      })
      .finally(function () { button.removeAttribute("aria-disabled"); button.classList.remove("disabled"); label.textContent = original; });
  }

  // ------------------------------------------------------------------------------------------- search
  function runSearch(append) {
    var hasQuery = !!(state.q || state.category);
    if (!append) state.page = 1;
    writeUrl();
    updateFilterCount();
    if (controller) controller.abort();
    if (!hasQuery) {
      searchSeq++;
      status("");
      showEmpty(EMPTY_PROMPT);
      return;
    }
    var seq = ++searchSeq;
    controller = new AbortController();
    var results = $("results");
    results.setAttribute("aria-busy", "true");
    $("empty").hidden = true;
    if (!append) { results.replaceChildren(skeletons()); $("load-more").hidden = true; status("Searching…"); }

    var params = queryParams(append ? state.page + 1 : 1);
    params.set("page", append ? state.page + 1 : 1);
    App.api(cfg.searchUrl + "?" + params.toString(), { signal: controller.signal })
      .then(function (data) {
        if (seq !== searchSeq) return;
        results.setAttribute("aria-busy", "false");
        state.page = data.page; state.pages = data.pages; state.total = data.total;
        if (!append) results.replaceChildren();
        if (!data.total) {
          showEmpty("No products found" + (state.q ? " for “" + state.q + "”" : "") + " within " + km(data.radius_km) +
                    ". Try another word, a wider distance or fewer filters.");
          status("0 products");
          return;
        }
        data.results.forEach(function (card) { results.appendChild(cardEl(card)); });
        status(data.total + (data.total === 1 ? " product" : " products") + " within " + km(data.radius_km));
        $("load-more").hidden = state.page >= state.pages;
      })
      .catch(function (error) {
        if ((error && error.name === "AbortError") || seq !== searchSeq) return;
        results.setAttribute("aria-busy", "false");
        App.errorMessage(error, "The search failed. Please try again.").then(function (message) {
          if (seq !== searchSeq) return;
          if (append) { showAlert(message, "danger"); return; }      // keep the products already on screen
          status(message, true);
          showEmpty(message, function () { runSearch(false); });
        });
      });
  }

  // ------------------------------------------------------------------------------------------- stores filter
  function renderStores() {
    var box = $("store-filter");
    box.replaceChildren();
    if (!stores.length) {
      box.appendChild(h("span", { "class": "small text-muted-ink", text: "No stores within " + state.radius + " km." }));
      return;
    }
    stores.forEach(function (store) {
      var id = "store-" + store.id;
      var input = h("input", { type: "checkbox", "class": "form-check-input", id: id, value: store.id, checked: state.stores.has(store.id) ? true : null });
      input.addEventListener("change", function () {
        if (input.checked) state.stores.add(store.id); else state.stores.delete(store.id);
        runSearch(false);
      });
      box.appendChild(h("div", { "class": "form-check" }, [
        input,
        h("label", { "class": "form-check-label d-flex justify-content-between gap-2", "for": id }, [
          h("span", { "class": "text-truncate", text: store.name }),
          h("span", { "class": "text-muted-ink flex-shrink-0", text: km(store.distance_km) })
        ])
      ]));
    });
  }

  function loadStores() {
    return App.api(cfg.storesUrl + "?radius=" + encodeURIComponent(state.radius))
      .then(function (data) {
        stores = data.stores || [];
        var known = new Set(stores.map(function (s) { return s.id; }));
        var pruned = false;
        Array.from(state.stores).forEach(function (id) { if (!known.has(id)) { state.stores.delete(id); pruned = true; } });
        renderStores();
        return pruned;
      })
      .catch(function () {
        $("store-filter").replaceChildren(h("span", { "class": "small text-danger", text: "Stores could not be loaded." }));
        return false;
      });
  }

  // ------------------------------------------------------------------------------------------- product details modal
  var modalEl = $("product-modal");
  var modal = window.bootstrap ? new bootstrap.Modal(modalEl) : null;

  function fact(label, value) {
    return value ? [h("dt", { text: label }), h("dd", { text: value })] : [];
  }

  function imageFact(url) {
    var safe = App.safeUrl(url);
    if (!safe) return [];
    return [h("dt", { text: "Image URL" }), h("dd", { "class": "text-break" }, [
      h("a", { href: safe, target: "_blank", rel: "noopener noreferrer", text: safe })
    ])];
  }

  function renderProduct(data) {
    var p = data.product;
    var body = $("product-modal-body");
    var facts = [].concat(
      fact("Store", p.store_name), fact("Address", p.store_address),
      fact("Distance", p.distance_km != null ? km(p.distance_km) + " from you" : ""),
      fact("Retailer", p.retailer), fact("Barcode", p.barcode), fact("SKU", p.sku !== p.barcode ? p.sku : ""),
      fact("Category", p.category), fact("Brand", p.brand), fact("Size", p.size), fact("Colour", p.colour),
      fact("Stock", stockText(p)), imageFact(p.image_url));

    var main = h("div", { "class": "row g-3", "data-key": p.key }, [
      h("div", { "class": "col-md-5" }, [thumbEl(p, "product-hero")]),
      h("div", { "class": "col-md-7" }, [
        h("div", { "class": "d-flex align-items-baseline justify-content-between gap-2" }, [App.priceEl(p.price, "product-price fs-2"), inListPill(p.in_list_qty || 0)]),
        h("dl", { "class": "facts" }, facts)
      ])
    ]);

    var comparison = data.comparison || {};
    var others = h("div", { "class": "mt-4" });
    others.appendChild(h("h3", { "class": "h6 fw-bold", text: "Compare prices and distance from you" }));
    if (comparison.text) {
      others.appendChild(h("p", { "class": "compare-note " + (comparison.far ? "far" : "ok"), role: "status" }, [
        icon(comparison.far ? "exclamation-triangle" : "check-circle"), " ", comparison.text]));
    }
    if (!data.home_known) {
      others.appendChild(h("p", { "class": "small text-muted-ink", text: "Distances are from central Durban because your address is not saved. Add it in your profile for exact distances." }));
    }
    var list = h("ul", { "class": "offer-list list-unstyled mb-0" });
    data.offers.forEach(function (offer) {
      var cheapest = offer.key === data.cheapest_key;
      var distance = offer.distance_km != null ? km(offer.distance_km) + " away from you" : "Distance unknown";
      var detail = [];
      if (!offer.in_stock) detail.push("Out of stock");
      else if (!cheapest && offer.more_than_cheapest && offer.more_than_cheapest !== "0.00") {
        detail.push("R" + offer.more_than_cheapest + " more than the cheapest");
        if (offer.closer_than_cheapest_km != null && offer.closer_than_cheapest_km > 0) detail.push(km(offer.closer_than_cheapest_km) + " closer");
      }
      list.appendChild(h("li", { "class": "offer-row" + (cheapest ? " cheapest" : ""), "data-key": offer.key }, [
        h("div", { "class": "min-w-0" }, [
          h("div", { "class": "fw-600 text-truncate", text: offer.store_name || offer.retailer }),
          offer.store_address ? h("div", { "class": "small text-muted-ink text-truncate", text: offer.store_address }) : null,
          h("div", { "class": "small offer-distance" + (offer.far ? " far" : ""), title: offer.far ? "The cheapest store is further away than another store that has it" : null }, [
            icon("geo-alt"), " ", distance]),
          detail.length ? h("div", { "class": "small text-muted-ink", text: detail.join(" · ") }) : null
        ]),
        h("div", { "class": "d-flex flex-column align-items-end gap-1 flex-shrink-0" }, [
          h("div", { "class": "d-flex gap-1" }, (offer.badges || []).map(badgeEl)),
          App.priceEl(offer.price, "fw-bold"),
          inListPill(offer.in_list_qty || 0),
          addButton(offer)
        ])
      ]));
    });
    others.appendChild(list);
    others.classList.remove("mt-4");
    main.classList.add("mt-4");
    body.replaceChildren(others, main);   // the comparison first: that is what the student opened this for
  }

  function openProduct(card) {
    if (!modal) return;
    $("product-modal-title").textContent = "Compare: " + card.name;
    $("product-modal-body").replaceChildren(h("div", { "class": "text-center text-muted-ink py-5" }, [
      h("div", { "class": "spinner-border spinner-border-sm me-2", role: "status" }), "Loading prices…"]));
    modal.show();
    var params = new URLSearchParams();
    if (card.barcode) params.set("barcode", card.barcode); else params.set("name", card.name);
    if (card.store_id) params.set("store_id", card.store_id);
    if (card.store_name) params.set("store_name", card.store_name);
    var seq = ++modalSeq;
    App.api(cfg.productUrl + "?" + params.toString())
      .then(function (data) { if (seq === modalSeq) renderProduct(data); })
      .catch(function (error) {
        if (seq !== modalSeq) return;
        App.errorMessage(error, "Could not load this product.").then(function (message) {
          $("product-modal-body").replaceChildren(h("p", { "class": "text-danger mb-0", role: "alert", text: message }));
        });
      });
  }

  // ------------------------------------------------------------------------------------------- recommendations
  function loadRecommendations() {
    var row = $("recs-row");
    var section = $("recs");
    var bones = [];
    for (var i = 0; i < 3; i++) {
      bones.push(h("div", { "class": "product-card compact skeleton", "aria-hidden": "true" }, [
        h("div", { "class": "product-thumb" }), h("div", { "class": "product-body" }, [h("div", { "class": "sk-line" }), h("div", { "class": "sk-line short" })])
      ]));
    }
    row.replaceChildren.apply(row, bones);
    row.setAttribute("aria-busy", "true");
    section.hidden = false;                                    // shimmer cards while the picks load
    App.api(cfg.recsUrl).then(function (data) {
      row.setAttribute("aria-busy", "false");
      if (!data.results || !data.results.length) {
        row.replaceChildren(h("p", { "class": "text-muted-ink small mb-0 py-3", text: "Recommendations will appear as we learn what you like." }));
        return;
      }
      row.replaceChildren.apply(row, data.results.map(function (card) { return cardEl(card, true); }));
    }).catch(function () {
      row.setAttribute("aria-busy", "false");
      row.replaceChildren(h("p", { "class": "text-muted-ink small mb-0 py-3", text: "Recommendations are temporarily unavailable. Please try again later." }));
    });
  }

  // ------------------------------------------------------------------------------------------- wiring
  function validatePrices() {
    var min = $("min-price").value.trim(), max = $("max-price").value.trim();
    var minVal = min ? App.parseAmount(min) : null, maxVal = max ? App.parseAmount(max) : null;
    var error = "";
    if (min && (minVal === null || minVal < 0)) error = "Enter the lowest price in rand, e.g. 20.";
    else if (max && (maxVal === null || maxVal < 0)) error = "Enter the highest price in rand, e.g. 100.";
    else if (minVal !== null && maxVal !== null && minVal > maxVal) error = "The highest price must not be below the lowest price.";
    $("price-error").textContent = error;
    $("min-price").classList.toggle("is-invalid", !!error && !!min);
    $("max-price").classList.toggle("is-invalid", !!error && !!max);
    if (error) return false;
    state.min = minVal === null ? "" : String(minVal);
    state.max = maxVal === null ? "" : String(maxVal);
    return true;
  }

  $("search-form").addEventListener("submit", function (event) {
    event.preventDefault();
    state.q = $("q").value.replace(/\s+/g, " ").trim();
    runSearch(false);
  });

  $("category-chips").addEventListener("click", function (event) {
    var chip = event.target.closest("[data-category]");
    if (!chip) return;
    state.category = chip.dataset.category;
    state.q = $("q").value.replace(/\s+/g, " ").trim();
    applyToControls();
    runSearch(false);
  });

  $("sort").addEventListener("change", function () { state.sort = this.value; runSearch(false); });

  ["min-price", "max-price"].forEach(function (id) {
    $(id).addEventListener("input", function () { debounce("price", function () { if (validatePrices()) runSearch(false); }, 450); });
  });

  $("radius").addEventListener("input", function () {
    state.radius = parseInt(this.value, 10) || RADIUS_DEFAULT;
    $("radius-out").textContent = state.radius;
    debounce("radius", function () {
      loadStores().then(function () { runSearch(false); });
    }, 300);
  });

  $("clear-stores").addEventListener("click", function () {
    state.stores.clear();
    renderStores();
    runSearch(false);
  });

  $("reset-filters").addEventListener("click", function () {
    state.min = ""; state.max = ""; state.radius = RADIUS_DEFAULT; state.stores.clear();
    $("price-error").textContent = "";
    applyToControls();
    loadStores().then(function () { runSearch(false); });
  });

  $("load-more").addEventListener("click", function () { runSearch(true); });

  Array.prototype.forEach.call(document.querySelectorAll("[data-scroll]"), function (button) {
    button.addEventListener("click", function () {
      var row = $("recs-row");
      row.scrollBy({ left: Number(button.dataset.scroll) * row.clientWidth * 0.8, behavior: App.reduceMotion ? "auto" : "smooth" });
    });
  });

  readUrl();
  applyToControls();
  loadStores().then(function (pruned) { if (pruned) runSearch(false); });
  runSearch(false);
  loadRecommendations();
})();
