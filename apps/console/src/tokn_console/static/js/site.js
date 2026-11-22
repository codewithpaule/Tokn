/* The mirror's React header never hydrates reliably, so the More menu is ours.
   It is one button, one menu, no dependency on the vendor bundle. */
(function () {
  "use strict";

  function closeAll(except) {
    document.querySelectorAll("[data-more-menu]").forEach(function (menu) {
      if (menu !== except) menu.hidden = true;
    });
    document.querySelectorAll("[data-more]").forEach(function (button) {
      if (button !== except) button.setAttribute("aria-expanded", "false");
    });
  }

  document.addEventListener("click", function (event) {
    var button = event.target.closest("[data-more]");
    if (button) {
      event.preventDefault();
      var menu = button.nextElementSibling;
      if (!menu || !menu.matches("[data-more-menu]")) return;
      var opening = menu.hidden;
      closeAll(opening ? button : null);
      menu.hidden = !opening;
      button.setAttribute("aria-expanded", opening ? "true" : "false");
      return;
    }
    if (!event.target.closest("[data-more-menu]")) closeAll(null);
  });

  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape") closeAll(null);
  });
})();
