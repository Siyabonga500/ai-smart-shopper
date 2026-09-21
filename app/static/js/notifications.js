/* The bell (Step 17). The page arrives with the notifications already drawn; this file
   - marks a notification as read when it is opened,
   - handles "Mark all as read",
   - refreshes the bell every minute and whenever the tab becomes visible again.
   Every text goes in with textContent (never innerHTML): a product name is not markup. */
(function () {
  "use strict";

  var bells = document.querySelectorAll("[data-bell]");
  if (!bells.length || !window.App) return;
  var REFRESH_MS = 60000;
  var LIMIT = 8;                       // rows in the dropdown (NOTIFICATION_BELL_LIMIT)

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text != null) node.textContent = text;
    return node;
  }

  function itemNode(n) {
    var li = el("li");
    var a = el("a", "bell-item" + (n.is_read ? "" : " is-unread"));
    a.href = App.safeUrl(n.url) || "#";
    a.title = n.message;
    a.setAttribute("data-notification-id", n.id);
    a.setAttribute("data-read", n.is_read ? "true" : "false");
    var icon = el("i", "bi bi-" + n.icon + " bell-icon level-" + n.level);
    icon.setAttribute("aria-hidden", "true");
    var text = el("span", "bell-text");
    text.appendChild(el("span", "bell-title", n.title));
    text.appendChild(el("span", "bell-message", n.message));
    var ago = el("span", "bell-ago", n.ago);
    if (!n.is_read) ago.appendChild(el("span", "visually-hidden", " (unread)"));
    text.appendChild(ago);
    a.appendChild(icon);
    a.appendChild(text);
    li.appendChild(a);
    return li;
  }

  function render(bell, data) {
    var list = bell.querySelector("[data-bell-list]");
    list.replaceChildren.apply(list, data.notifications.map(itemNode));
    bell.querySelector("[data-bell-empty]").hidden = data.notifications.length > 0;
    setUnread(bell, data.unread);
  }

  function setUnread(bell, unread) {
    var badge = bell.querySelector("[data-bell-badge]");
    badge.hidden = !unread;
    badge.textContent = unread > 9 ? "9+" : String(unread);
    bell.querySelector("[data-bell-read-all]").hidden = !unread;
    bell.querySelector("[data-bell-button]").setAttribute("aria-label",
      "Notifications" + (unread ? " (" + unread + " unread)" : ""));
  }

  function refresh(bell) {
    App.api(bell.getAttribute("data-endpoint") + "?limit=" + LIMIT)
      .then(function (data) { render(bell, data); })
      .catch(function () { /* offline or signed out: keep what is drawn */ });
  }

  bells.forEach(function (bell) {
    // Opening a notification marks it read. keepalive lets the request finish while the browser navigates away.
    bell.addEventListener("click", function (event) {
      var link = event.target.closest("[data-notification-id]");
      if (link && link.getAttribute("data-read") === "false") {
        link.setAttribute("data-read", "true");
        link.classList.remove("is-unread");
        var badge = bell.querySelector("[data-bell-badge]");
        var left = Math.max(0, (parseInt(badge.textContent, 10) || 1) - 1);
        setUnread(bell, left);
        fetch("/api/notifications/" + encodeURIComponent(link.getAttribute("data-notification-id")) + "/read", {
          method: "POST", keepalive: true, credentials: "same-origin",
          headers: { "X-CSRFToken": App.csrfToken(), Accept: "application/json" }
        }).catch(function () {});
      }
      if (link && link.getAttribute("href") === "#") event.preventDefault();

      if (event.target.closest("[data-bell-read-all]")) {
        App.api(bell.getAttribute("data-read-all-endpoint"), { method: "POST" })
          .then(function () {
            bell.querySelectorAll("[data-notification-id]").forEach(function (node) {
              node.classList.remove("is-unread");
              node.setAttribute("data-read", "true");
            });
            setUnread(bell, 0);
          })
          .catch(function () {});
      }
    });

    setInterval(function () { if (!document.hidden) refresh(bell); }, REFRESH_MS);
    document.addEventListener("visibilitychange", function () { if (!document.hidden) refresh(bell); });
  });

  // Other scripts (adding to the list, removing an item) can ask for a refresh straight away.
  document.addEventListener("app:notifications-changed", function () { bells.forEach(refresh); });
})();
