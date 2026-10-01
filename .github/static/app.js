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

// Market Watch: hover a sparkline to read the value on any date.
(function () {
  document.querySelectorAll(".spark[data-points]").forEach(function (box) {
    var pts; try { pts = JSON.parse(box.dataset.points); } catch (e) { return; }
    var svg = box.querySelector("svg"), tip = box.querySelector(".spark-tip");
    var focus = svg && svg.querySelector(".spark-focus");
    if (!svg || !tip || !focus || !pts.length) return;
    var vbW = svg.viewBox.baseVal.width;
    function show(clientX) {
      var r = svg.getBoundingClientRect();
      var x = (clientX - r.left) / r.width * vbW, best = pts[0];
      pts.forEach(function (p) { if (Math.abs(p[0] - x) < Math.abs(best[0] - x)) best = p; });
      focus.setAttribute("cx", best[0]); focus.setAttribute("cy", best[1]); focus.removeAttribute("hidden");
      tip.textContent = best[3] + " · " + best[2];
      tip.style.left = (best[0] / vbW * r.width) + "px";
      tip.hidden = false;
    }
    function hide() { tip.hidden = true; focus.setAttribute("hidden", ""); }
    box.addEventListener("mousemove", function (e) { show(e.clientX); });
    box.addEventListener("mouseleave", hide);
    box.addEventListener("touchstart", function (e) { show(e.touches[0].clientX); }, { passive: true });
    box.addEventListener("touchend", function () { setTimeout(hide, 1500); });
  });
})();

// Policy: jurisdiction dropdown (remembered per browser), status chips, search.
(function () {
  var select = document.getElementById("jurisdiction");
  if (!select) return;
  var KEY = "newsHubPolicyJurisdiction";
  var cards = document.querySelectorAll(".policy-card");
  var chips = document.querySelectorAll(".chip[data-status]");
  var search = document.querySelector(".policy-search");
  var count = document.querySelector(".policy-count");
  var status = "all";

  var saved = null;
  try { saved = localStorage.getItem(KEY); } catch (e) {}
  var initial = saved || select.dataset.default || "federal";
  if (select.querySelector('option[value="' + initial + '"]')) select.value = initial;

  function apply() {
    var jur = select.value, q = (search && search.value || "").trim().toLowerCase(), shown = 0;
    cards.forEach(function (c) {
      var j = c.dataset.jur;
      var okJur = jur === "all" || j === "US" || (jur !== "federal" && j === jur);
      var okStatus = status === "all" || c.dataset.status === status;
      var okText = !q || c.dataset.text.indexOf(q) !== -1;
      c.hidden = !(okJur && okStatus && okText);
      if (!c.hidden) shown++;
    });
    document.querySelectorAll(".policy-group").forEach(function (g) {
      var n = g.querySelectorAll(".policy-card:not([hidden])").length;
      g.hidden = status !== "all" && g.dataset.group !== status;
      var badge = g.querySelector("[data-count]"); if (badge) badge.textContent = n;
      var empty = g.querySelector(".group-empty"); if (empty) empty.hidden = n > 0;
    });
    // State news: hidden for "Federal only"; otherwise filtered like the federal cards.
    var news = document.querySelector(".policy-news");
    if (news) {
      news.hidden = jur === "federal";
      var n = news.querySelectorAll(".policy-card:not([hidden])").length;
      var badge = news.querySelector("[data-count]"); if (badge) badge.textContent = n;
      var empty = news.querySelector(".group-empty"); if (empty) empty.hidden = n > 0;
      var link = news.querySelector(".state-link"), links = {};
      if (link) {
        try { links = JSON.parse(link.dataset.links); } catch (e) {}
        link.hidden = !links[jur];
        if (links[jur]) {
          link.href = links[jur];
          link.textContent = select.options[select.selectedIndex].text + " government links on USA.gov →";
        }
      }
    }
    var label = select.options[select.selectedIndex].text;
    if (count) count.textContent = "Showing " + shown + " item" + (shown === 1 ? "" : "s") +
      (jur === "federal" ? " · federal only" : jur === "all" ? " · all states and federal" : " · " + label + " and federal");
  }

  select.addEventListener("change", function () {
    try { localStorage.setItem(KEY, select.value); } catch (e) {}
    apply();
  });
  chips.forEach(function (chip) {
    chip.addEventListener("click", function () {
      status = chip.dataset.status;
      chips.forEach(function (c) { c.setAttribute("aria-pressed", c === chip ? "true" : "false"); });
      apply();
    });
  });
  if (search) search.addEventListener("input", apply);
  apply();
})();
