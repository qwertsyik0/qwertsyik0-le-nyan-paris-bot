(() => {
  const tabbar = document.querySelector(".tabbar");
  if (!tabbar || document.getElementById("mobile-bottom-nav")) return;

  const labels = {
    home: "главная",
    application: "анкета",
    profile: "персонаж",
    game: "игра",
    status: "статус",
    letters: "письма",
    guide: "правила",
    locations: "локации",
    registry: "реестр",
    warnings: "предупреждения",
    admin: "админка",
  };

  const icons = {
    home: "⌂",
    game: "◇",
    profile: "◉",
    letters: "✉",
    more: "•••",
  };

  const nav = document.createElement("nav");
  nav.id = "mobile-bottom-nav";
  nav.className = "mobile-bottom-nav";
  nav.setAttribute("aria-label", "основная навигация");
  nav.innerHTML = `
    <button type="button" class="mobile-nav-item is-active" data-mobile-nav="home"><span>${icons.home}</span><b>главная</b></button>
    <button type="button" class="mobile-nav-item" data-mobile-nav="game"><span>${icons.game}</span><b>игра</b></button>
    <button type="button" class="mobile-nav-item" data-mobile-nav="profile"><span>${icons.profile}</span><b>персонаж</b></button>
    <button type="button" class="mobile-nav-item" data-mobile-nav="letters"><span>${icons.letters}</span><b>письма</b></button>
    <button type="button" class="mobile-nav-item" data-mobile-nav="more"><span>${icons.more}</span><b>ещё</b></button>
  `;

  const backdrop = document.createElement("div");
  backdrop.id = "section-menu-backdrop";
  backdrop.className = "section-menu-backdrop hidden";
  backdrop.innerHTML = `
    <div class="section-menu-sheet" role="dialog" aria-modal="true" aria-labelledby="section-menu-title">
      <div class="section-menu-head">
        <div>
          <p class="eyebrow">l’empire des ombres</p>
          <h3 id="section-menu-title">разделы</h3>
        </div>
        <button type="button" class="section-menu-close" aria-label="закрыть">×</button>
      </div>
      <div id="section-menu-grid" class="section-menu-grid"></div>
    </div>
  `;

  const currentTitle = document.createElement("div");
  currentTitle.id = "mobile-current-title";
  currentTitle.className = "mobile-current-title";
  currentTitle.innerHTML = '<small>l’empire des ombres</small><strong>главная</strong>';

  document.body.append(nav, backdrop);
  document.querySelector(".topbar > div")?.appendChild(currentTitle);

  const grid = backdrop.querySelector("#section-menu-grid");
  const closeButton = backdrop.querySelector(".section-menu-close");

  function existingTab(name) {
    const tab = tabbar.querySelector(`.tab[data-tab="${name}"]`);
    if (!tab || tab.classList.contains("hidden")) return null;
    const screen = document.getElementById(`tab-${name}`);
    return screen ? tab : null;
  }

  function activeName() {
    return tabbar.querySelector(".tab.active[data-tab]")?.dataset.tab || "home";
  }

  function availableTabs() {
    return [...tabbar.querySelectorAll(".tab[data-tab]")].filter((tab) => {
      if (tab.classList.contains("hidden")) return false;
      return Boolean(document.getElementById(`tab-${tab.dataset.tab}`));
    });
  }

  function setHeader(name) {
    const title = currentTitle.querySelector("strong");
    if (title) title.textContent = labels[name] || name || "главная";
  }

  function setBottomActive(name) {
    nav.querySelectorAll("[data-mobile-nav]").forEach((button) => {
      const target = button.dataset.mobileNav;
      let active = target === name;
      if (target === "profile" && ["profile","application","status"].includes(name)) active = true;
      if (target === "letters" && name === "warnings") active = false;
      button.classList.toggle("is-active", active);
    });
    setHeader(name);
  }

  function openTab(name) {
    const tab = existingTab(name);
    if (!tab) return false;
    tab.click();
    setBottomActive(name);
    requestAnimationFrame(() => window.scrollTo({top:0, behavior:"smooth"}));
    return true;
  }

  function openCharacter() {
    if (openTab("profile")) return;
    if (openTab("application")) return;
    openTab("status");
  }

  function openMail() {
    if (openTab("letters")) return;
    if (openTab("profile")) {
      setTimeout(() => {
        const cards = [...document.querySelectorAll("#tab-profile .feature-card, #tab-profile .card")];
        const mail = cards.find((card) => card.querySelector("h3")?.textContent?.trim().toLowerCase() === "письма");
        mail?.scrollIntoView({behavior:"smooth", block:"start"});
      }, 180);
    }
  }

  function rebuildMore() {
    if (!grid) return;
    const primary = new Set(["home","game","profile","letters"]);
    const tabs = availableTabs().filter((tab) => !primary.has(tab.dataset.tab));
    grid.innerHTML = tabs.map((tab) => {
      const name = tab.dataset.tab;
      const active = tab.classList.contains("active") ? " is-active" : "";
      return `<button type="button" class="section-menu-item${active}" data-section-target="${name}"><span>${String(tabs.indexOf(tab)+1).padStart(2,"0")}</span><b>${labels[name] || tab.textContent.trim() || name}</b></button>`;
    }).join("");
  }

  function openMenu() {
    rebuildMore();
    backdrop.classList.remove("hidden");
    document.body.classList.add("section-menu-open");
    nav.querySelector('[data-mobile-nav="more"]')?.classList.add("is-active");
  }

  function closeMenu() {
    backdrop.classList.add("hidden");
    document.body.classList.remove("section-menu-open");
    setBottomActive(activeName());
  }

  nav.addEventListener("click", (event) => {
    const button = event.target.closest?.("[data-mobile-nav]");
    if (!button) return;
    const target = button.dataset.mobileNav;
    if (target === "home") openTab("home");
    else if (target === "game") openTab("game");
    else if (target === "profile") openCharacter();
    else if (target === "letters") openMail();
    else if (target === "more") openMenu();
  });

  closeButton?.addEventListener("click", closeMenu);
  backdrop.addEventListener("click", (event) => {
    if (event.target === backdrop) {
      closeMenu();
      return;
    }
    const item = event.target.closest?.("[data-section-target]");
    if (!item) return;
    const target = item.dataset.sectionTarget;
    closeMenu();
    if (target === "guide") {
      const tab = existingTab("guide");
      tab?.click();
      return;
    }
    openTab(target);
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") closeMenu();
  });

  tabbar.addEventListener("click", () => {
    setTimeout(() => setBottomActive(activeName()), 0);
  });

  const observer = new MutationObserver(() => {
    rebuildMore();
    setBottomActive(activeName());
  });
  observer.observe(tabbar, { childList:true, subtree:true, attributes:true, attributeFilter:["class"] });

  setBottomActive(activeName());
})();
