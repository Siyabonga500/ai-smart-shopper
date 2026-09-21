/* Registration page behaviour: instant validation that mirrors app/forms/auth.py, the password
   checklist, gender pre-fill from the SA ID, and the DUT residence -> address shortcut.
   The server re-checks everything, so this is only for quicker feedback. */
(function () {
  "use strict";

  var form = document.getElementById("register-form");
  if (!form) return;

  var microsoft = form.getAttribute("data-microsoft") === "true";
  var SAId = window.SAId;
  var Rules = window.Validators;

  function el(name) { return form.elements[name]; }
  function feedbackFor(name) { return form.querySelector('[data-error-for="' + name + '"]'); }

  /* ---- rules (same text as the server) ------------------------------------------------ */
  var NAME_RE = /^\p{L}[\p{L} '’.\-]*$/u;

  function nameRule(label) {
    return function (value) {
      value = value.normalize("NFC").trim();
      if (!value) return "Enter your " + label.toLowerCase() + ".";
      if (value.length < 2 || value.length > 50) return label + " must be between 2 and 50 characters.";
      if (!NAME_RE.test(value)) return label + " can only contain letters, spaces, hyphens and apostrophes.";
      return "";
    };
  }

  var passwordProblems = Rules.passwordProblems;

  var rules = {
    FirstName: nameRule("First name"),
    LastName: nameRule("Last name"),
    Email: function (v) {
      v = v.trim();
      if (!v) return "Enter your email address.";
      if (v.length > 100) return "Email address is too long.";
      var result = Rules.validateEmail(v);
      return result.valid ? "" : result.error;
    },
    CellphoneNumber: function (v) {
      if (!Rules.cleanPhone(v)) return "Enter your cellphone number.";
      var result = Rules.validatePhone(v);
      return result.valid ? "" : result.error;
    },
    SAIdNumber: function (v) {
      if (!SAId.clean(v)) return "Enter your 13-digit ID number.";
      var result = SAId.parse(v);
      return result.valid ? "" : result.error;
    },
    Race: function (v) { return v ? "" : "Select an option."; },
    ResidentialAddress: function (v) {
      v = v.trim();
      if (!v) return "Enter your residential address, or pick it on the map.";
      return v.length > 300 ? "Address is too long (300 characters max)." : "";
    },
    AcceptTerms: function () { return el("AcceptTerms").checked ? "" : "You must accept the Terms to create an account."; }
  };
  if (!microsoft) {
    rules.Password = function (v) {
      if (!v) return "Create a password.";
      var problems = passwordProblems(v);
      return problems.length ? "Your password needs " + problems.join(", ") + "." : "";
    };
    rules.ConfirmPassword = function (v) {
      if (!v) return "Confirm your password.";
      return v === el("Password").value ? "" : "Passwords do not match.";
    };
  }

  /* ---- showing errors --------------------------------------------------------------------- */
  function show(name, message) {
    var field = el(name);
    var box = feedbackFor(name);
    if (!field) return;
    if (box) box.textContent = message;
    field.classList.toggle("is-invalid", !!message);
    field.classList.toggle("is-valid", !message && field.dataset.touched === "1" && field.type !== "checkbox");
    field.setAttribute("aria-invalid", message ? "true" : "false");
  }

  function check(name) {
    var field = el(name);
    if (!field || !rules[name]) return "";
    var message = rules[name](field.value);
    show(name, message);
    return message;
  }

  function touch(name) { var f = el(name); if (f) f.dataset.touched = "1"; }

  Object.keys(rules).forEach(function (name) {
    var field = el(name);
    if (!field) return;
    var live = function () {
      touch(name);
      check(name);
      if (name === "Password" && el("ConfirmPassword") && el("ConfirmPassword").dataset.touched) check("ConfirmPassword");
    };
    field.addEventListener("blur", function () { if (field.value || field.type === "checkbox" || field.dataset.touched) live(); });
    field.addEventListener(field.type === "checkbox" || field.tagName === "SELECT" ? "change" : "input", function () {
      // Only start nagging after the person has left the field once, or if it already shows an error.
      if (field.dataset.touched === "1" || field.classList.contains("is-invalid")) live();
    });
  });

  /* ---- SA ID -> summary and gender ------------------------------------------------------ */
  var summary = document.getElementById("sa-id-summary");
  var genderSelect = el("Gender");
  var genderEdited = !!(genderSelect && genderSelect.value);   // respect a value the server sent back

  if (genderSelect) genderSelect.addEventListener("change", function () { genderEdited = true; });

  function describeId() {
    var idField = el("SAIdNumber");
    if (!idField || !summary) return;
    var digits = SAId.clean(idField.value);
    if (digits.length !== 13) { summary.classList.add("d-none"); return; }
    var result = SAId.parse(digits);
    if (!result.valid) { summary.classList.add("d-none"); return; }
    var months = ["January", "February", "March", "April", "May", "June", "July", "August", "September",
                  "October", "November", "December"];
    var parts = result.dateOfBirth.split("-");
    summary.textContent = "Born " + Number(parts[2]) + " " + months[Number(parts[1]) - 1] + " " + parts[0] +
      " · " + result.gender + " (from your ID number)";
    summary.classList.remove("d-none");
    if (genderSelect && !genderEdited) genderSelect.value = result.gender;
  }
  if (el("SAIdNumber")) {
    el("SAIdNumber").addEventListener("input", function () {
      // keep digits only (people paste "800101 5009 087"): the server ignores spaces as well
      describeId();
    });
    describeId();
  }

  /* ---- password checklist ---------------------------------------------------------------- */
  var checklist = document.getElementById("password-rules");
  if (checklist && el("Password")) {
    // one source of truth (validators.js): a rule is met when its message is not among the problems
    var missingText = {
      length: "at least 8 characters", upper: "one uppercase letter", lower: "one lowercase letter",
      number: "one number", symbol: "one symbol"
    };
    var tests = {};
    Object.keys(missingText).forEach(function (key) {
      tests[key] = function (v) {
        return !passwordProblems(v).some(function (text) { return text.indexOf(missingText[key]) === 0; });
      };
    });
    var update = function () {
      var value = el("Password").value;
      Object.keys(tests).forEach(function (key) {
        var item = checklist.querySelector('[data-rule="' + key + '"]');
        if (!item) return;
        var ok = tests[key](value);
        item.classList.toggle("text-success", ok);
        item.classList.toggle("text-muted", !ok);
        item.querySelector("i").className = "bi me-1 " + (ok ? "bi-check-circle-fill" : "bi-circle");
      });
    };
    el("Password").addEventListener("input", update);
    update();
  }

  /* ---- DUT residence -> address + pin ------------------------------------------------------ */
  var residence = el("Residence");
  var picker = form.querySelector("[data-map-picker]");
  if (residence) {
    residence.addEventListener("change", function () {
      var option = residence.options[residence.selectedIndex];
      var address = option && option.getAttribute("data-address");
      var latitude = option && parseFloat(option.getAttribute("data-lat"));
      var longitude = option && parseFloat(option.getAttribute("data-lng"));
      if (address) {
        if (picker && picker.mapPicker) {
          picker.mapPicker.setAddress(address);
          if (isFinite(latitude) && isFinite(longitude)) picker.mapPicker.setLocation(latitude, longitude);
          else picker.mapPicker.search(address);
        }
        else el("ResidentialAddress").value = address;
        el("ResidentialAddress").dataset.touched = "1";
        check("ResidentialAddress");
      } else if (residence.value === "other") {
        el("ResidentialAddress").focus();
      }
    });
  }

  /* ---- submit ---------------------------------------------------------------------------------- */
  form.addEventListener("submit", function (event) {
    var firstBad = null;
    Object.keys(rules).forEach(function (name) {
      touch(name);
      if (check(name) && !firstBad) firstBad = el(name);
    });
    if (firstBad) {
      event.preventDefault();
      firstBad.scrollIntoView({ behavior: "smooth", block: "center" });
      firstBad.focus({ preventScroll: true });
    }
  });
})();
