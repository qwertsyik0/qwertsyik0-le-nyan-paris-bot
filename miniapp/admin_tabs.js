(() => {
  const admin = document.getElementById("tab-admin");
  if (!admin || document.getElementById("admin-section-tabs")) return;

  const labels = {
    overview: "обзор",
    applications: "анкеты",
    players: "игроки",
    notes: "заметки",
    letters: "письма",
    templates: "шаблоны",
    warnings: "предупреждения",
    logs: "журнал",
  };
  const order = ["overview", "applications", "players", "notes", "letters", "templates", "warnings", "logs"];
  let current = "overview";
  let refreshTimer = null;

  const nav = document.createElement("div");
  nav.id = "admin-section-tabs";
  nav.className = "admin-section-tabs";
  nav.innerHTML = '<div class="admin-section-tabs-title"><span>разделы админки</span></div><div class="admin-section-tabs-grid"></div>';
  admin.insertBefore(nav, admin.firstChild);

  function textOf(element) {
    return String(element?.textContent || "").toLowerCase();
  }

  function inferPane(element) {
    if (!element) return null;
    const id = String(element.id || "").toLowerCase();
    const text = textOf(element);

    if (
      id.includes("warning") ||
      element.querySelector?.("#admin-warning-target, #warning-target, #hard-warning-target, #admin-warning-list") ||
      text.includes("предупрежден")
    ) return "warnings";

    if (
      element.querySelector?.("#admin-logs-list") ||
      text.includes("журнал действий")
    ) return "logs";

    if (
      element.querySelector?.("#admin-templates-list, #admin-template-name") ||
      text.includes("шаблоны писем")
    ) return "templates";

    if (
      element.querySelector?.("#admin-note-body") ||
      text.includes("внутренние заметки")
    ) return "notes";

    if (
      id.includes("player") ||
      element.querySelector?.("#players-list, #players-search, #admin-player-query, #admin-player-card") ||
      text.includes("карточка игрока") ||
      text.includes("принятые участники")
    ) return "players";

    if (
      element.querySelector?.("#admin-letters-list, #group-letter-target, #hard-group-target, #admin-letter-target") ||
      text.includes("управление письмами") ||
      text.includes("рассылка по разделам") ||
      text.includes("отправка письма") ||
      text.includes("быстрые команды")
    ) return "letters";

    if (
      element.querySelector?.("#admin-pending-list, #admin-accepted-list, #admin-app-id") ||
      text.includes("новые анкеты") ||
      text.includes("последние принятые") ||
      text.includes("решение по анкете")
    ) return "applications";

    if (
      element.querySelector?.("#admin-message") ||
      text.includes("доступ владельца")
    ) return "overview";

    return null;
  }

  function extBoxes() {
    return [...admin.querySelectorAll("#admin-ext-panel .admin-ext-box")];
  }

  function directArticles() {
    return [...admin.children].filter((element) => element.tagName === "ARTICLE");
  }

  function availableKeys() {
    const found = new Set(["overview"]);
    for (const article of directArticles()) {
      if (article.id === "admin-ext-panel") continue;
      const key = inferPane(article);
      if (key) found.add(key);
    }
    for (const box of extBoxes()) {
      const key = inferPane(box);
      if (key) found.add(key);
    }
    return order.filter((key) => found.has(key));
  }

  function applyVisibility() {
    const extPanel = admin.querySelector("#admin-ext-panel");
    let extHasCurrent = false;

    for (const box of extBoxes()) {
      const key = inferPane(box);
      const show = key === current;
      box.classList.toggle("admin-pane-hidden", !show);
      if (show) extHasCurrent = true;
    }

    if (extPanel) {
      extPanel.classList.toggle("admin-pane-hidden", !extHasCurrent);
    }

    for (const article of directArticles()) {
      if (article.id === "admin-ext-panel") continue;
      const key = inferPane(article) || "overview";
      article.classList.toggle("admin-pane-hidden", key !== current);
    }

    // Backend currently mounts two warning forms. Keep the richer player-aware one.
    const richWarnings = admin.querySelector("#feature-admin-warnings");
    const duplicateWarnings = admin.querySelector("#admin-warning-card");
    if (richWarnings && duplicateWarnings) duplicateWarnings.classList.add("admin-pane-hidden");

    nav.querySelectorAll("[data-admin-section]").forEach((button) => {
      button.classList.toggle("active", button.dataset.adminSection === current);
    });
  }

  function rebuild() {
    const keys = availableKeys();
    if (!keys.includes(current)) current = keys[0] || "overview";

    const grid = nav.querySelector(".admin-section-tabs-grid");
    grid.innerHTML = keys.map((key) =>
      `<button type="button" class="admin-section-tab${key === current ? " active" : ""}" data-admin-section="${key}">${labels[key] || key}</button>`
    ).join("");

    applyVisibility();
  }

  nav.addEventListener("click", (event) => {
    const button = event.target.closest?.("[data-admin-section]");
    if (!button) return;
    current = button.dataset.adminSection;
    applyVisibility();
    nav.scrollIntoView({ behavior: "smooth", block: "start" });
  });

  const observer = new MutationObserver(() => {
    clearTimeout(refreshTimer);
    refreshTimer = setTimeout(rebuild, 80);
  });
  observer.observe(admin, { childList: true });

  rebuild();
})();