/* Two behaviours, both optional. Every page still works with JavaScript off. */
(function () {
  "use strict";

  document.addEventListener("click", function (event) {
    var button = event.target.closest("[data-copy]");
    if (button) {
      var selector = button.getAttribute("data-copy");
      var source = selector ? document.querySelector(selector) : null;
      var text = source ? source.textContent : "";
      if (!text) return;
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(function () {
          var old = button.textContent;
          button.textContent = "Copied";
          window.setTimeout(function () { button.textContent = old; }, 1600);
        });
      }
      return;
    }

    var dangerous = event.target.closest("[data-confirm]");
    if (dangerous && !window.confirm(dangerous.getAttribute("data-confirm"))) {
      event.preventDefault();
    }
  });

  document.addEventListener("submit", function (event) {
    var form = event.target;
    if (form && form.matches("[data-busy]")) {
      var submit = form.querySelector("button[type=submit]");
      if (submit) {
        submit.disabled = true;
        submit.textContent = submit.getAttribute("data-busy-label") || "Working";
      }
    }
  });
})();
