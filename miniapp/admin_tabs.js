(() => {
  const admin = document.getElementById("tab-admin");
  if (!admin || document.getElementById("admin-section-tabs")) return;

  const labels = {
    overview: "обзор",
    players: "игроки",
    applications: "анкеты",
    letters: "письма",
    warnings: "предупр.",
    npc: "NPC",
    notes: "заметки",
    templates: "шаблоны",
    logs: "журнал",
  };
  const order = ["overview", "players", "applications", "npc", "letters", "warnings", "notes", "templates", "logs"];
  let current = "overview";

  const nav = document.createElement("div");
  nav.id = "admin-section-tabs";
  nav.className = "admin-section-tabs";
  nav.innerHTML = '<div class="admin-section-tabs-grid"></div>';
  admin.insertBefore(nav, admin.firstChild);

  function panes() {
    return [...admin.querySelectorAll("[data-admin-pane]")];
  }

  function availableKeys() {
    const found = new Set(panes().map((el) => el.dataset.adminPane).filter(Boolean));
    return order.filter((key) => found.has(key));
  }

  function applyVisibility() {
    for (const pane of panes()) {
      pane.classList.toggle("admin-pane-hidden", pane.dataset.adminPane !== current);
    }

    const extPanel = admin.querySelector("#admin-ext-panel");
    if (extPanel) {
      const hasVisibleNestedPane = [...extPanel.querySelectorAll(":scope > [data-admin-pane]")]
        .some((pane) => pane.dataset.adminPane === current && !pane.classList.contains("power-replaced"));
      extPanel.classList.toggle("admin-pane-hidden", !hasVisibleNestedPane);
    }

    const richWarnings = admin.querySelector("#feature-admin-warnings");
    const duplicateWarnings = admin.querySelector("#admin-warning-card");
    if (richWarnings && duplicateWarnings) duplicateWarnings.classList.add("admin-pane-hidden");

    nav.querySelectorAll("[data-admin-section]").forEach((button) => {
      button.classList.toggle("active", button.dataset.adminSection === current);
    });

    admin.dataset.currentAdminPane = current;
    document.dispatchEvent(new CustomEvent("adminpanechange", { detail: { pane: current } }));
  }

  function rebuild() {
    const keys = availableKeys();
    if (!keys.length) return;
    if (!keys.includes(current)) current = keys[0];

    const grid = nav.querySelector(".admin-section-tabs-grid");
    grid.innerHTML = keys.map((key) =>
      `<button type="button" class="admin-section-tab${key === current ? " active" : ""}" data-admin-section="${key}"><span class="admin-section-label">${labels[key] || key}</span></button>`
    ).join("");

    applyVisibility();
  }

  nav.addEventListener("click", (event) => {
    const button = event.target.closest?.("[data-admin-section]");
    if (!button) return;
    current = button.dataset.adminSection;
    applyVisibility();
    window.scrollTo({ top: Math.max(0, admin.offsetTop - 8), behavior: "smooth" });
  });

  rebuild();
  // Поздние модули Mini App монтируются после основного HTML.
  // Обновляем список вкладок несколько раз только при старте,
  // а не следим за DOM бесконечно.
  setTimeout(rebuild, 450);
  setTimeout(rebuild, 1200);
  setTimeout(rebuild, 2600);
})();