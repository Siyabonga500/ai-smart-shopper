/* Profile > Preferences: the Value box suggests real values for the chosen type (stores, brands, categories, diets). */
(function () {
  "use strict";

  var type = document.getElementById("PreferenceType");
  var list = document.getElementById("pref-suggestions");
  var data = document.getElementById("pref-suggestions-data");
  if (!type || !list || !data) return;
  var suggestions = JSON.parse(data.textContent || "{}");

  function fill() {
    list.replaceChildren();
    (suggestions[type.value] || []).forEach(function (value) {
      var option = document.createElement("option");
      option.value = value;
      list.appendChild(option);
    });
  }
  type.addEventListener("change", function () {
    fill();
    var input = document.getElementById("PreferenceValue");
    if (input) { input.value = ""; input.focus(); }
  });
  fill();
})();
