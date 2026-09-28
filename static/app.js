// Topic filter chips + search box. The pages work without it, too.
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
})();
