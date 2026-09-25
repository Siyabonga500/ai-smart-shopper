/* Shopping List (Step 12). The server works out every total and every enabled/disabled button, so after each
   change this file asks it for the fresh HTML (/list/fragment) and swaps it in. Changes go through the JSON API. */
(function () {
  "use strict";

  var root = document.getElementById("list-app");
  if (!root || !window.App) return;
  var App = window.App;
  var cfg = root.dataset;
  var body = document.getElementById("list-body");
  var showAlternatives = cfg.alternatives === "1";
  var busy = false;

  function say(message) { App.toast(message); }

  function fragmentUrl() {
    return cfg.fragmentUrl + (showAlternatives ? "?alternatives=1" : "");
  }

  /** Fetch the fresh HTML and put it on the page, keeping keyboard focus on the same control. */
  function refresh(focusKey) {
    return fetch(fragmentUrl(), { credentials: "same-origin", headers: { "X-Requested-With": "fetch" } })
      .then(function (response) {
        if (!response.ok || response.headers.get("X-List-Fragment") !== "1") { location.reload(); return null; }
        return response.text();
      })
      .then(function (html) {
        if (html === null) return;
        body.innerHTML = html;                       // server-rendered and escaped by Jinja
        var state = document.getElementById("list-state");
        if (state) {
          var n = parseInt(state.dataset.count, 10) || 0;
          document.getElementById("list-count").textContent = n + (n === 1 ? " item" : " items");
        }
        var target = focusKey && body.querySelector('[data-focus="' + focusKey + '"]:not([disabled])');
        if (target) target.focus();
      });
  }

  function run(promise, focusKey, successMessage) {
    if (busy) return;
    busy = true;
    body.classList.add("is-busy");
    body.setAttribute("aria-busy", "true");
    promise
      .then(function () { if (successMessage) say(successMessage); })
      .catch(function (error) {
        App.errorMessage(error).then(function (message) { say(message); });
      })
      .then(function () { return refresh(focusKey); })
      .catch(function () { say("The list could not be refreshed. Reload the page."); })
      .then(function () {
        busy = false;
        body.classList.remove("is-busy");
        body.setAttribute("aria-busy", "false");
      });
  }

  function itemUrl(id, suffix) { return cfg.itemsUrl + "/" + encodeURIComponent(id) + (suffix || ""); }

  body.addEventListener("click", function (event) {
    var button = event.target.closest("[data-action]");
    if (!button || busy) return;
    var action = button.dataset.action;
    if (action === "toggle-alternatives") {
      showAlternatives = !showAlternatives;
      run(Promise.resolve(), null);
      return;
    }
    var row = button.closest("[data-item-id]");
    if (!row) return;
    var id = row.dataset.itemId;
    var name = (row.querySelector(".li-info .fw-600") || {}).textContent || "Item";
    var qty = parseInt(row.querySelector(".qty").textContent, 10) || 1;
    var focusKey = button.getAttribute("data-focus");

    if (action === "inc") run(App.api(itemUrl(id), { method: "PATCH", body: { quantity: qty + 1 } }), focusKey);
    else if (action === "dec") run(App.api(itemUrl(id), { method: "PATCH", body: { quantity: qty - 1 } }), focusKey);
    else if (action === "remove") run(App.api(itemUrl(id), { method: "DELETE" }), null, "Removed " + name + ".");
    else if (action === "purchased") {
      var done = row.dataset.collected === "1";
      run(App.api(itemUrl(id, "/purchased"), { method: "POST", body: { purchased: !done } }), focusKey,
          done ? name + " is no longer marked as purchased." : name + " marked as purchased.");
    }
    else if (action === "replace") run(App.api(itemUrl(id, "/replace"), { method: "POST" }), null, "Replaced with the cheaper alternative.");
  });

  // A product image that will not load: leave the grey square with an icon instead of a broken picture.
  body.addEventListener("error", function (event) {
    var img = event.target;
    if (!img || img.tagName !== "IMG") return;
    var icon = document.createElement("i");
    icon.className = "bi bi-image";
    icon.setAttribute("aria-hidden", "true");
    img.replaceWith(icon);
  }, true);

  // ------------------------------------------------------------------------------------------- title, edited in place
  var title = document.getElementById("list-title");
  var input = document.getElementById("list-title-input");
  var editButton = document.getElementById("list-title-edit");
  var errorBox = document.getElementById("list-title-error");
  var editing = false;

  function startEdit() {
    editing = true;
    errorBox.textContent = "";
    input.value = title.textContent.trim();
    title.hidden = true;
    input.hidden = false;
    editButton.hidden = true;
    input.focus();
    input.select();
  }

  function stopEdit() {
    editing = false;
    input.hidden = true;
    title.hidden = false;
    editButton.hidden = false;
  }

  function commit() {
    if (!editing) return;
    var value = input.value.replace(/\s+/g, " ").trim();
    if (value === title.textContent.trim()) { stopEdit(); editButton.focus(); return; }
    if (!value) { errorBox.textContent = "The list needs a title."; input.focus(); return; }
    if (value.length > 100) { errorBox.textContent = "Keep the title to 100 characters or fewer."; input.focus(); return; }
    input.disabled = true;
    App.api(cfg.listUrl, { method: "PATCH", body: { title: value } })
      .then(function (data) {
        title.textContent = data.list.title;
        stopEdit();
        editButton.focus();
        say("Title saved.");
      })
      .catch(function (error) {
        App.errorMessage(error).then(function (message) { errorBox.textContent = message; });
      })
      .then(function () { input.disabled = false; if (editing) input.focus(); });
  }

  editButton.addEventListener("click", startEdit);
  title.addEventListener("dblclick", startEdit);
  input.addEventListener("keydown", function (event) {
    if (event.key === "Enter") { event.preventDefault(); commit(); }
    else if (event.key === "Escape") { event.preventDefault(); errorBox.textContent = ""; stopEdit(); editButton.focus(); }
  });
  input.addEventListener("blur", function () { if (editing && !input.disabled) commit(); });
})();
