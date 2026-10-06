(() => {
  const tg = window.Telegram?.WebApp;
  tg?.ready?.();
  tg?.expand?.();

  const back = document.getElementById("rules-back");
  const filters = [...document.querySelectorAll("[data-rule-filter]")];
  const sections = [...document.querySelectorAll("[data-rule-section]")];
  const intro = document.querySelector(".rules-intro");
  const mobileSelect = document.getElementById("rules-mobile-select");

  function goBack() {
    if (document.referrer && document.referrer.includes("/miniapp/")) {
      history.back();
      return;
    }
    window.location.href = "./index.html";
  }

  function setActive(name) {
    for (const button of filters) {
      const active = button.dataset.ruleFilter === name;
      button.classList.toggle("active", active);
      button.setAttribute("aria-pressed", active ? "true" : "false");
    }
  }

  function jumpTo(name) {
    if (name === "all") {
      setActive("all");
      intro?.scrollIntoView({ behavior: "smooth", block: "start" });
      tg?.HapticFeedback?.selectionChanged?.();
      return;
    }

    const section = sections.find((item) => item.dataset.ruleSection === name);
    if (!section) return;

    setActive(name);
    section.scrollIntoView({ behavior: "smooth", block: "start" });
    tg?.HapticFeedback?.selectionChanged?.();
  }

  back?.addEventListener("click", goBack);

  filters.forEach((button) => {
    button.addEventListener("click", () => jumpTo(button.dataset.ruleFilter || "all"));
  });

  mobileSelect?.addEventListener("change", () => {
    jumpTo(mobileSelect.value || "all");
  });

  const observer = new IntersectionObserver((entries) => {
    const visible = entries
      .filter((entry) => entry.isIntersecting)
      .sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top)[0];

    if (visible?.target?.dataset?.ruleSection) {
      const name = visible.target.dataset.ruleSection;
      setActive(name);
      if (mobileSelect && mobileSelect.value !== name) mobileSelect.value = name;
    }
  }, {
    root: null,
    rootMargin: "-12% 0px -72% 0px",
    threshold: 0
  });

  sections.forEach((section) => {
    section.hidden = false;
    observer.observe(section);
  });
})();
