(() => {
  const tg = window.Telegram?.WebApp;
  tg?.ready?.();
  tg?.expand?.();

  const back = document.getElementById("rules-back");
  const filters = [...document.querySelectorAll("[data-rule-filter]")];
  const sections = [...document.querySelectorAll("[data-rule-section]")];
  const empty = document.getElementById("rules-empty");

  function goBack() {
    if (document.referrer && document.referrer.includes("/miniapp/")) {
      history.back();
      return;
    }
    window.location.href = "./index.html";
  }

  function selectFilter(name) {
    let visible = 0;
    for (const section of sections) {
      const show = name === "all" || section.dataset.ruleSection === name;
      section.hidden = !show;
      if (show) visible += 1;
    }

    for (const button of filters) {
      const active = button.dataset.ruleFilter === name;
      button.classList.toggle("active", active);
      button.setAttribute("aria-pressed", active ? "true" : "false");
    }

    empty?.classList.toggle("hidden", visible > 0);

    const activeButton = filters.find((button) => button.dataset.ruleFilter === name);
    activeButton?.scrollIntoView({ behavior: "smooth", block: "nearest", inline: "center" });
    window.scrollTo({ top: 0, behavior: "smooth" });
    tg?.HapticFeedback?.selectionChanged?.();
  }

  back?.addEventListener("click", goBack);
  filters.forEach((button) => {
    button.addEventListener("click", () => selectFilter(button.dataset.ruleFilter || "all"));
  });
})();
