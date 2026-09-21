/* Set Budget page (Step 10).

   Rules enforced here (and again on the server, app/services/budgets.py parse_budget_form):
   - Combined Budget and individual categories never coexist:
       * choosing Combined turns every individual category off (the rows are removed and cannot be added),
       * while any individual category is listed, Combined is disabled - remove them all first to switch.
   - One row per category. The total updates on every change.
   - The NSFAS R1 750 bar only warns; it never stops the student saving.
   - "Save Budget" opens a summary; only "Confirm & Save" submits.
   Without JavaScript the form still posts normally and the server applies the same rules. */
(function () {
  "use strict";

  var form = document.getElementById("budget-form");
  if (!form) return;

  var allowance = parseFloat(form.getAttribute("data-allowance")) || 1750;
  var maxAmount = parseFloat(form.getAttribute("data-max")) || 1000000;
  var categories = JSON.parse(form.getAttribute("data-categories") || "[]");
  var combinedKey = form.getAttribute("data-combined-key") || "Combined";
  var icons = { Grocery: "basket2", Toiletries: "droplet", Clothes: "handbag", Electronics: "tv" };

  var $ = function (id) { return document.getElementById(id); };
  var rowsEl = $("category-rows"), combinedRow = $("combined-row"), combinedInput = $("amount_" + combinedKey);
  var radioIndividual = form.querySelector('input[name="budget_type"][value="individual"]');
  var radioCombined = form.querySelector('input[name="budget_type"][value="combined"]');
  var cardCombined = $("type-combined-card"), cardIndividual = $("type-individual-card");
  var addBtn = $("add-category"), addMenu = $("add-category-menu"), clearBtn = $("clear-categories");
  var typeHint = $("type-hint"), addHint = $("add-hint"), totalEl = $("total-amount");
  var titleInput = $("title"), titleError = $("title-error");
  var template = $("row-template");
  var confirmed = false;

  function currentType() { return radioCombined.checked ? "combined" : "individual"; }
  function rows() { return Array.prototype.slice.call(rowsEl.querySelectorAll(".cat-row")); }
  function presentCategories() { return rows().map(function (row) { return row.getAttribute("data-category"); }); }
  function rowInput(row) { return row.querySelector("input"); }

  // ---- total + NSFAS bar --------------------------------------------------------------------------------
  function total() {
    var sum = 0;
    var inputs = currentType() === "combined" ? [combinedInput] : rows().map(rowInput);
    inputs.forEach(function (input) {
      var amount = App.parseAmount(input.value);
      if (amount != null && amount > 0) sum += amount;
    });
    return Math.round(sum * 100) / 100;
  }

  function nsfasWarning(amount) {
    var over = Math.round((amount - allowance) * 100) / 100;
    if (over <= 0) return "";
    return "Your budget is " + App.formatZAR(amount) + ", which is " + App.formatZAR(over) + " above the " +
      App.formatZAR(allowance) + " NSFAS meal allowance reference.";
  }

  function updateTotals() {
    var amount = total();
    totalEl.textContent = App.formatZAR(amount);
    $("nsfas-allocated").textContent = App.formatZAR(amount);
    var percent = allowance > 0 ? amount / allowance * 100 : 0;
    $("nsfas-percent").textContent = Math.round(percent) + "%";
    var meter = $("nsfas-meter");
    meter.firstElementChild.style.width = Math.min(percent, 100) + "%";
    meter.classList.toggle("warn", percent >= 100);
    meter.setAttribute("aria-valuenow", String(Math.round(Math.min(percent, 100))));
    var warning = nsfasWarning(amount);
    $("nsfas-warning").hidden = !warning;
    $("nsfas-warning-text").textContent = warning;
  }

  // ---- the Combined / individual rule ---------------------------------------------------------------------
  function refresh() {
    var type = currentType();
    var hasRows = rows().length > 0;

    // Combined is off the table while any individual category is listed.
    radioCombined.disabled = hasRows;
    cardCombined.classList.toggle("disabled", hasRows);
    typeHint.textContent = hasRows
      ? "Combined Budget is available once every category below is removed."
      : (type === "combined" ? "One amount covers everything you buy." : "");

    rowsEl.hidden = type === "combined";
    combinedRow.hidden = type !== "combined";
    combinedInput.disabled = type !== "combined";

    var remaining = categories.filter(function (c) { return presentCategories().indexOf(c) === -1; });
    addBtn.disabled = type === "combined" || remaining.length === 0;
    clearBtn.hidden = type === "combined" || !hasRows;
    addHint.textContent = type === "combined"
      ? "Individual categories are turned off while a Combined Budget is selected."
      : (remaining.length === 0 ? "All four categories are added." : "");

    addMenu.innerHTML = "";
    remaining.forEach(function (category) {
      var li = document.createElement("li");
      var button = document.createElement("button");
      button.type = "button"; button.className = "dropdown-item"; button.textContent = category;
      button.addEventListener("click", function () { addRow(category, true); });
      li.appendChild(button); addMenu.appendChild(li);
    });
    updateTotals();
  }

  function addRow(category, focus) {
    if (presentCategories().indexOf(category) !== -1) return;              // one row per category
    var row = template.content.firstElementChild.cloneNode(true);
    row.setAttribute("data-category", category);
    var input = row.querySelector("input"), label = row.querySelector("label");
    input.id = "amount_" + category; input.name = "amount_" + category;
    input.setAttribute("aria-label", category + " amount in rand");
    label.setAttribute("for", input.id); label.textContent = category;
    row.querySelector(".cat-icon i").className = "bi bi-" + (icons[category] || "tag");
    row.querySelector('[data-action="remove"]').setAttribute("aria-label", "Remove " + category);
    // keep the rows in the same order as the categories list
    var later = rows().filter(function (r) { return categories.indexOf(r.getAttribute("data-category")) > categories.indexOf(category); })[0];
    rowsEl.insertBefore(row, later || null);
    refresh();
    if (focus) input.focus();
  }

  function setType(type) {
    if (type === "combined" && rows().length) return;                        // the rule: remove the rows first
    (type === "combined" ? radioCombined : radioIndividual).checked = true;
    if (type === "individual" && rows().length === 0) categories.forEach(function (c) { addRow(c, false); });
    refresh();
  }

  radioIndividual.addEventListener("change", function () { setType("individual"); });
  radioCombined.addEventListener("change", function () { setType("combined"); });
  cardCombined.addEventListener("click", function (event) {
    if (radioCombined.disabled) {                                            // explain instead of doing nothing
      event.preventDefault();
      typeHint.classList.add("text-over");
      setTimeout(function () { typeHint.classList.remove("text-over"); }, 2500);
    }
  });

  rowsEl.addEventListener("click", function (event) {
    var button = event.target.closest('[data-action="remove"]');
    if (!button) return;
    button.closest(".cat-row").remove();
    refresh();
  });
  clearBtn.addEventListener("click", function () {
    rows().forEach(function (row) { row.remove(); });
    refresh();
  });

  form.addEventListener("input", function (event) {
    if (event.target.matches(".cat-row input")) {
      event.target.classList.remove("is-invalid");
      var error = event.target.closest(".cat-row").querySelector(".cat-error");
      if (error) error.textContent = "";
    }
    if (event.target === titleInput) { titleInput.classList.remove("is-invalid"); titleError.textContent = ""; }
    updateTotals();
  });

  // ---- validation + confirmation ----------------------------------------------------------------------------
  function fail(input, message, errorEl) {
    input.classList.add("is-invalid");
    if (errorEl) errorEl.textContent = message;
    return input;
  }

  function validate() {
    var firstBad = null;
    var title = titleInput.value.replace(/\s+/g, " ").trim();
    if (!title) firstBad = fail(titleInput, "Give your budget a title.", titleError);

    var type = currentType();
    var lines = [];
    var inputs = type === "combined"
      ? [{ category: "Combined Budget", input: combinedInput, error: combinedRow.querySelector(".cat-error") }]
      : rows().map(function (row) { return { category: row.getAttribute("data-category"), input: rowInput(row), error: row.querySelector(".cat-error") }; });

    if (type === "individual" && inputs.length === 0) {
      typeHint.textContent = "Add at least one category, or choose a Combined Budget.";
      typeHint.classList.add("text-over");
      firstBad = firstBad || addBtn;
    }
    inputs.forEach(function (entry) {
      var amount = App.parseAmount(entry.input.value);
      var message = amount == null ? "Enter an amount, e.g. 700."
        : amount <= 0 ? "The amount must be more than R0."
        : amount > maxAmount ? "The amount cannot be more than " + App.formatZAR(maxAmount) + "." : "";
      if (message) firstBad = firstBad || fail(entry.input, message, entry.error);
      else lines.push({ label: entry.category, amount: amount });
    });
    if (!firstBad && total() > maxAmount) {
      typeHint.textContent = "The total budget cannot be more than " + App.formatZAR(maxAmount) + ".";
      firstBad = addBtn;
    }
    if (firstBad) { firstBad.focus(); return null; }
    return { title: title, lines: lines, total: total() };
  }

  function showSummary(summary) {
    var list = $("confirm-list");
    list.innerHTML = "";
    function row(label, value, cls) {
      var li = document.createElement("li");
      if (cls) li.className = cls;
      var a = document.createElement("span"), b = document.createElement("strong");
      a.textContent = label; b.textContent = value;
      li.appendChild(a); li.appendChild(b); list.appendChild(li);
    }
    row("Title", summary.title);
    summary.lines.forEach(function (line) { row(line.label, App.formatZAR(line.amount)); });
    row("Total", App.formatZAR(summary.total), "total");
    var warning = nsfasWarning(summary.total);
    var warnEl = $("confirm-warning");
    warnEl.hidden = !warning;
    warnEl.textContent = warning ? warning + " You can still save it." : "";
    var button = $("confirm-save");
    button.disabled = false;
    bootstrap.Modal.getOrCreateInstance($("confirm-modal")).show();
  }

  form.addEventListener("submit", function (event) {
    if (confirmed) return;                                                    // the confirmed native submit
    event.preventDefault();
    var summary = validate();
    if (summary) showSummary(summary);
  });

  $("confirm-save").addEventListener("click", function () {
    this.disabled = true;                                                     // no double submit
    confirmed = true;
    form.submit();
  });

  refresh();
})();
