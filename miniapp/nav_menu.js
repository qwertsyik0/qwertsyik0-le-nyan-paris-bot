(() => {
  const tabbar = document.querySelector(".tabbar");
  if (!tabbar || document.getElementById("section-menu-button")) return;

  const labels = {
    home: "главная",
    application: "анкета",
    profile: "профиль",
    game: "игра",
    status: "статус",
    letters: "письма",
    guide: "правила",
    locations: "локации",
    registry: "реестр",
    warnings: "предупреждения",
    admin: "админка",
  };

  const button = document.createElement("button");
  button.id = "section-menu-button";
  button.className = "section-menu-button";
  button.type = "button";
  button.setAttribute("aria-label", "открыть все разделы");
  button.innerHTML = "<span>☰</span><b>разделы</b>";

  const backdrop = document.createElement("div");
  backdrop.id = "section-menu-backdrop";
  backdrop.className = "section-menu-backdrop hidden";
  backdrop.innerHTML = `
    <div class="section-menu-sheet" role="dialog" aria-modal="true" aria-labelledby="section-menu-title">
      <div class="section-menu-head">
        <div>
          <p class="eyebrow">навигация</p>
          <h3 id="section-menu-title">все разделы</h3>
        </div>
        <button type="button" class="section-menu-close" aria-label="закрыть">×</button>
      </div>
      <div id="section-menu-grid" class="section-menu-grid"></div>
    </div>
  `;

  document.body.append(button, backdrop);

  const grid = backdrop.querySelector("#section-menu-grid");
  const closeButton = backdrop.querySelector(".section-menu-close");

  function availableTabs() {
    return [...tabbar.querySelectorAll(".tab[data-tab]")].filter((tab) => {
      if (tab.classList.contains("hidden")) return false;
      const screen = document.getElementById(`tab-${tab.dataset.tab}`);
      return Boolean(screen);
    });
  }

  function rebuild() {
    if (!grid) return;
    const tabs = availableTabs();
    grid.innerHTML = tabs.map((tab) => {
      const name = tab.dataset.tab;
      const label = labels[name] || tab.textContent.trim() || name;
      const active = tab.classList.contains("active") ? " is-active" : "";
      return `<button type="button" class="section-menu-item${active}" data-section-target="${name}">${label}</button>`;
    }).join("");
  }

  function openMenu() {
    rebuild();
    backdrop.classList.remove("hidden");
    document.body.classList.add("section-menu-open");
  }

  function closeMenu() {
    backdrop.classList.add("hidden");
    document.body.classList.remove("section-menu-open");
  }

  button.addEventListener("click", openMenu);
  closeButton?.addEventListener("click", closeMenu);

  backdrop.addEventListener("click", (event) => {
    if (event.target === backdrop) closeMenu();
    const item = event.target.closest?.("[data-section-target]");
    if (!item) return;
    const target = item.dataset.sectionTarget;
    const tab = tabbar.querySelector(`.tab[data-tab="${target}"]`);
    if (!tab || tab.classList.contains("hidden")) return;
    closeMenu();
    tab.click();
    requestAnimationFrame(() => {
      tab.scrollIntoView({ behavior: "smooth", block: "nearest", inline: "center" });
    });
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") closeMenu();
  });

  tabbar.addEventListener("click", () => setTimeout(rebuild, 0));

  const observer = new MutationObserver(rebuild);
  observer.observe(tabbar, { childList: true, subtree: true, attributes: true, attributeFilter: ["class"] });

  rebuild();
})();