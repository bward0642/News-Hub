// Topic filter chips + search box + "Add staff note" helper. Works without it, too.
(function () {
  var chips = document.querySelectorAll(".chip[data-filter]");
  var search = document.querySelector(".search");
  var cards = document.querySelectorAll(".topic .card, .week .card");
  var noResults = document.querySelector(".no-results");
  var active = "all";

  function apply() {
    var q = (search && search.value || "").trim().toLowerCase();
    var shown = 0;
    cards.forEach(function (c) {
      var okTopic = active === "all" || (" " + c.dataset.topics + " ").indexOf(" " + active + " ") !== -1;
      var okText = !q || c.dataset.text.indexOf(q) !== -1;
      c.hidden = !(okTopic && okText);
      if (!c.hidden) shown++;
    });
    // Open collapsed groups while searching so matches are visible.
    document.querySelectorAll("details.more, .week details").forEach(function (d) {
      if (q || active !== "all") {
        if (d.querySelector(".card:not([hidden])")) d.open = true;
      }
    });
    document.querySelectorAll("[data-topic-section], .week").forEach(function (s) {
      s.hidden = (q || active !== "all") && !s.querySelector(".card:not([hidden])");
    });
    if (noResults) noResults.hidden = shown > 0 || !cards.length;
  }

  chips.forEach(function (chip) {
    chip.addEventListener("click", function () {
      active = chip.dataset.filter;
      chips.forEach(function (c) { c.setAttribute("aria-pressed", c === chip ? "true" : "false"); });
      apply();
    });
  });
  if (search) search.addEventListener("input", apply);

  function toast(msg) {
    var t = document.createElement("div");
    t.className = "toast"; t.setAttribute("role", "status"); t.textContent = msg;
    document.body.appendChild(t);
    setTimeout(function () { t.remove(); }, 3500);
  }

  document.addEventListener("click", function (e) {
    var btn = e.target.closest("[data-copy-note]");
    if (!btn) return;
    var snippet =
      "  - url: " + btn.dataset.copyNote + "\n" +
      "    # " + btn.dataset.title.replace(/\n/g, " ") + "\n" +
      "    note: >\n" +
      "      What this means for our clients: \n" +
      "    author: \n" +
      "    pin: true\n";
    var done = function () { toast("Copied! Paste it under “notes:” in config/staff_notes.yaml"); };
    if (navigator.clipboard && window.isSecureContext) {
      navigator.clipboard.writeText(snippet).then(done, function () { window.prompt("Copy this snippet:", snippet); });
    } else {
      window.prompt("Copy this snippet:", snippet);
    }
  });
})();
