/* South African ID number helpers. Mirrors app/utils/sa_id.py rule for rule
   (tests/test_js_parity.py runs both implementations over the same cases).

   ID layout: YYMMDD SSSS C A Z
     digits 0-5  date of birth      digits 6-9  gender (0000-4999 female, 5000-9999 male)
     digit 10    0 citizen / 1 PR   digit 12    Luhn check digit                              */
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.SAId = factory();
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  function clean(value) {
    return String(value == null ? "" : value).replace(/[\s\-]/g, "");
  }

  function luhnValid(digits) {
    if (!/^\d+$/.test(digits)) return false;
    var total = 0;
    for (var i = 0; i < digits.length; i++) {
      var value = Number(digits.charAt(digits.length - 1 - i));
      if (i % 2 === 1) {
        value *= 2;
        if (value > 9) value -= 9;
      }
      total += value;
    }
    return total % 10 === 0;
  }

  function pad(n) { return (n < 10 ? "0" : "") + n; }

  /** Date for y/m/d, or null when the calendar date does not exist (e.g. 31 February). */
  function realDate(year, month, day) {
    var d = new Date(Date.UTC(year, month - 1, day));
    return d.getUTCFullYear() === year && d.getUTCMonth() === month - 1 && d.getUTCDate() === day ? d : null;
  }

  /** Returns {valid, error} or {valid, dateOfBirth: "YYYY-MM-DD", gender, isCitizen}. */
  function parse(value, today) {
    var id = clean(value);
    if (!/^\d{13}$/.test(id)) return { valid: false, error: "ID number must be exactly 13 digits." };

    var now = today || new Date();
    var todayUtc = Date.UTC(now.getFullYear(), now.getMonth(), now.getDate());
    var yy = Number(id.slice(0, 2)), month = Number(id.slice(2, 4)), day = Number(id.slice(4, 6));
    var birth = null, years = [2000 + yy, 1900 + yy];
    for (var i = 0; i < years.length; i++) {
      var candidate = realDate(years[i], month, day);
      if (candidate && candidate.getTime() <= todayUtc) { birth = candidate; break; }
    }
    if (!birth) return { valid: false, error: "The date of birth in this ID number is not a valid date." };
    if (id.charAt(10) !== "0" && id.charAt(10) !== "1") return { valid: false, error: "The citizenship digit of this ID number (the 11th digit) must be 0 or 1." };
    if (!luhnValid(id)) return { valid: false, error: "This ID number is not valid. Please check for a typing mistake." };

    return {
      valid: true,
      dateOfBirth: birth.getUTCFullYear() + "-" + pad(birth.getUTCMonth() + 1) + "-" + pad(birth.getUTCDate()),
      gender: Number(id.slice(6, 10)) >= 5000 ? "Male" : "Female",
      isCitizen: id.charAt(10) === "0"
    };
  }

  return { clean: clean, luhnValid: luhnValid, parse: parse };
});
