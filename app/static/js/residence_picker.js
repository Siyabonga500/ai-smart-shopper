/* Profile edit: picking a DUT residence fills in the address and moves the map pin (same as sign-up, register.js). */
(function () {
  "use strict";

  var residence = document.getElementById("Residence");
  var address = document.getElementById("ResidentialAddress");
  if (!residence || !address) return;
  var picker = residence.form && residence.form.querySelector("[data-map-picker]");

  residence.addEventListener("change", function () {
    var option = residence.options[residence.selectedIndex];
    var text = option && option.getAttribute("data-address");
    var latitude = option && parseFloat(option.getAttribute("data-lat"));
    var longitude = option && parseFloat(option.getAttribute("data-lng"));
    if (text) {
      if (picker && picker.mapPicker) {
        picker.mapPicker.setAddress(text);
        if (isFinite(latitude) && isFinite(longitude)) picker.mapPicker.setLocation(latitude, longitude);
        else picker.mapPicker.search(text);
      } else {
        address.value = text;
      }
      address.classList.remove("is-invalid");
    } else if (residence.value === "other") {
      address.focus();
    }
  });
})();
