/* Phone, password and email rules. Mirrors app/utils/validators.py rule for rule
   (tests/test_js_validator_parity.py runs both implementations over the same cases).
   The server re-checks everything; this file only gives quicker feedback. */
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.Validators = factory();
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  var PHONE_MESSAGE = "Enter your cellphone number as 10 digits, for example 0821234567.";
  var EMAIL_MESSAGE = "Enter a valid email address, for example name@example.com.";

  /** "082 123 4567" -> "0821234567" (spaces, dashes and brackets are ignored). */
  function cleanPhone(value) {
    return String(value == null ? "" : value).replace(/[\s\-()]/g, "");
  }

  /** {valid, value, error}: 10 digits, starting 0 and then 6, 7 or 8. */
  function validatePhone(value) {
    var digits = cleanPhone(value);
    if (!/^0[678][0-9]{8}$/.test(digits)) return { valid: false, error: PHONE_MESSAGE };
    return { valid: true, value: digits };
  }

  /** What a password is missing: at least 8 characters, upper, lower, digit, symbol, at most 72 bytes. */
  function passwordProblems(value) {
    value = String(value == null ? "" : value);
    var problems = [];
    if (Array.from(value).length < 8) problems.push("at least 8 characters");
    if (!/\p{Uppercase}/u.test(value)) problems.push("one uppercase letter");
    if (!/\p{Lowercase}/u.test(value)) problems.push("one lowercase letter");
    if (!/[0-9]/.test(value)) problems.push("one number");
    if (!/[\p{P}\p{S}]/u.test(value)) problems.push("one symbol (for example ! @ # $ %)");
    if (new TextEncoder().encode(value).length > 72) problems.push("no more than 72 bytes");
    return problems;
  }

  function validatePassword(value) {
    var problems = passwordProblems(value);
    if (problems.length) return { valid: false, error: "Your password needs " + problems.join(", ") + "." };
    return { valid: true, value: String(value) };
  }

  var LOCAL_RE = /^[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+(?:\.[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+)*$/;
  var LABEL_RE = /^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$/;
  var TLD_RE = /^(?:[A-Za-z]{2,63}|xn--[A-Za-z0-9-]{2,59})$/;

  /** {valid, value, error}: one @, a sane local part, a dotted domain, a letters-only top-level domain. */
  function validateEmail(value) {
    var text = String(value == null ? "" : value).trim();
    var fail = { valid: false, error: EMAIL_MESSAGE };
    if (!text || text.length > 254 || text.split("@").length !== 2) return fail;
    var parts = text.split("@");
    var local = parts[0];
    var domain = parts[1].replace(/\.$/, "");
    if (/[^\x00-\x7f]/.test(domain)) {                  // internationalised domain -> punycode, as the server does
      try { domain = new URL("http://" + domain).hostname; } catch (e) { return fail; }
    }
    var labels = domain.split(".");
    if (local.length < 1 || local.length > 64 || !LOCAL_RE.test(local) || labels.length < 2 || domain.length > 253) return fail;
    for (var i = 0; i < labels.length; i++) if (!LABEL_RE.test(labels[i])) return fail;
    if (!TLD_RE.test(labels[labels.length - 1])) return fail;
    return { valid: true, value: (local + "@" + domain).toLowerCase() };
  }

  return {
    cleanPhone: cleanPhone, validatePhone: validatePhone,
    passwordProblems: passwordProblems, validatePassword: validatePassword,
    validateEmail: validateEmail
  };
});
