(() => {
  const admin = document.getElementById("tab-admin");
  if (!admin || document.getElementById("admin-redesign-marker")) return;

  const tg = window.Telegram?.WebApp;
  const initData = tg?.initData || "";
  const API_BASE = "https://le-nyan-paris-bot.onrender.com";

  const marker = document.createElement("span");
  marker.id = "admin-redesign-marker";
  marker.hidden = true;
  admin.appendChild(marker);
  admin.classList.add("admin-redesigned");

  async function post(path, payload = {}) {
    const response = await fetch(API_BASE + path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ initData, ...payload }),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || "ошибка запроса");
    return data;
  }

  function markMode() {
    document.body.classList.toggle("admin-mode", admin.classList.contains("active"));
  }

  function hideLegacyBlocks() {
    // Старый обзор больше не нужен: вместо него компактный дашборд.
    [...admin.querySelectorAll(':scope > article[data-admin-pane="overview"]')].forEach((node) => {
      if (node.id !== "power-global-search" && node.id !== "admin-overview-dashboard") node.classList.add("admin-layout-hidden");
    });

    // Старые версии игрока/заметок заменены power-модулем.
    document.getElementById("feature-admin-players")?.classList.add("admin-layout-hidden");
    document.querySelector('#admin-ext-panel > [data-admin-pane="players"]')?.classList.add("admin-layout-hidden");
    document.querySelector('#admin-ext-panel > [data-admin-pane="notes"]')?.classList.add("admin-layout-hidden");

    // Дубли одиночной отправки и старой hard-admin рассылки убираем:
    // индивидуальное письмо теперь запускается из карточки игрока,
    // массовые письма остаются отдельным нормальным блоком.
    [...admin.querySelectorAll('#admin-ext-panel > [data-admin-pane="letters"]')].forEach((node) => {
      const text = String(node.textContent || "").toLowerCase();
      if (text.includes("отправка письма")) node.classList.add("admin-layout-hidden");
    });

    // Один рабочий блок предупреждений вместо нескольких одинаковых.
    if (document.getElementById("feature-admin-warnings")) {
      document.getElementById("admin-warning-card")?.classList.add("admin-layout-hidden");
      document.getElementById("hard-warning-admin-card")?.classList.add("admin-layout-hidden");
    }
  }

  function buildDashboard() {
    const old = document.getElementById("power-global-search");
    if (!old || document.getElementById("admin-overview-dashboard")) return;

    const dashboard = document.createElement("article");
    dashboard.id = "admin-overview-dashboard";
    dashboard.className = "card admin-dashboard";
    dashboard.dataset.adminPane = "overview";
    dashboard.innerHTML = `
      <div class="admin-dashboard-head">
        <div>
          <p class="eyebrow">панель управления</p>
          <h2>админка</h2>
        </div>
        <button id="admin-dashboard-refresh" class="small" type="button">обновить</button>
      </div>

      <div class="admin-dashboard-stats">
        <button type="button" data-admin-jump="applications"><span>анкеты</span><b id="dash-pending">—</b></button>
        <button type="button" data-admin-jump="players"><span>игроки</span><b id="dash-players">—</b></button>
        <button type="button" data-admin-jump="warnings"><span>предупр.</span><b id="dash-warnings">—</b></button>
        <button type="button" data-admin-jump="letters"><span>письма</span><b id="dash-letters">—</b></button>
      </div>

      <div class="admin-dashboard-search">
        <input id="admin-dashboard-query" placeholder="@username, имя, роль или ID">
        <button id="admin-dashboard-search-btn" class="primary" type="button">найти</button>
      </div>
      <div id="admin-dashboard-results" class="power-results"></div>

      <div class="admin-dashboard-actions">
        <button type="button" data-admin-jump="players">управление игроками</button>
        <button type="button" data-admin-jump="letters">написать</button>
        <button type="button" data-admin-jump="warnings">предупреждение</button>
        <button type="button" data-admin-jump="templates">шаблоны</button>
      </div>
    `;

    admin.insertBefore(dashboard, old);
    old.classList.add("admin-layout-hidden");
  }

  function switchPane(name) {
    const button = admin.querySelector(`#admin-section-tabs [data-admin-section="${name}"]`);
    button?.click();
  }

  function esc(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");
  }

  async function loadDashboard() {
    if (!initData) return;
    try {
      const data = await post("/api/admin/dashboard");
      const c = data.counts || {};
      const map = {
        "dash-pending": c.pending,
        "dash-players": c.players,
        "dash-warnings": c.warnings,
        "dash-letters": c.letters,
      };
      Object.entries(map).forEach(([id, value]) => {
        const el = document.getElementById(id);
        if (el) el.textContent = String(value ?? 0);
      });
    } catch (_) {}
  }

  async function dashboardSearch() {
    const query = document.getElementById("admin-dashboard-query")?.value.trim() || "";
    const root = document.getElementById("admin-dashboard-results");
    if (!root) return;
    if (!query) {
      root.innerHTML = "";
      return;
    }
    root.innerHTML = '<div class="admin-loading">поиск...</div>';
    try {
      const data = await post("/api/admin/search", { search: query, affiliation: "", player_status: "" });
      const rows = (data.players || []).slice(0, 8);
      root.innerHTML = rows.length ? rows.map((p) => `
        <button class="admin-search-result" type="button" data-redesign-open-player="${esc(p.telegram_id)}">
          <span><b>${esc(p.character_name)}</b><small>${esc(p.username || "без username")}</small></span>
          <span><small>${esc(p.assigned_role || "без роли")}</small><em>${esc(p.player_status_label || p.player_status)}</em></span>
        </button>
      `).join("") : '<div class="empty">ничего не найдено</div>';
    } catch (error) {
      root.textContent = error.message;
    }
  }

  function moveUsefulBlocks() {
    const targeted = document.getElementById("power-targeted-mail");
    if (targeted) {
      const letterPanes = [...admin.querySelectorAll('[data-admin-pane="letters"]')].filter((x) => x !== targeted && !x.classList.contains("admin-layout-hidden"));
      const first = letterPanes[0];
      if (first && targeted.compareDocumentPosition(first) & Node.DOCUMENT_POSITION_PRECEDING) {
        admin.insertBefore(targeted, first);
      }
      targeted.classList.add("admin-primary-card");
    }

    const warning = document.getElementById("feature-admin-warnings")
      || document.getElementById("hard-warning-admin-card")
      || document.getElementById("admin-warning-card");
    if (warning) {
      warning.classList.add("admin-primary-card");
      const warningPanes = [...admin.querySelectorAll('[data-admin-pane="warnings"]')].filter((x) => x !== warning && !x.classList.contains("admin-layout-hidden"));
      const first = warningPanes[0];
      if (first) admin.insertBefore(warning, first);
    }

    document.getElementById("power-notes-console")?.classList.add("admin-primary-card");
    document.getElementById("power-template-favorites")?.classList.add("admin-primary-card");

    const pending = document.getElementById("admin-pending-list")?.closest("article");
    const accepted = document.getElementById("admin-accepted-list")?.closest("article");
    if (pending) pending.classList.add("admin-primary-card");
    if (accepted) accepted.classList.add("admin-secondary-card");

    const decision = [...admin.querySelectorAll('#admin-ext-panel > [data-admin-pane="applications"]')]
      .find((node) => String(node.textContent || "").toLowerCase().includes("решение по анкете"));
    if (decision && pending) {
      pending.after(decision);
      decision.classList.add("admin-secondary-card");
    }
  }

  function setupPlayerSheet() {
    const detail = document.getElementById("power-player-detail");
    if (!detail) return;

    const sync = () => {
      const open = !detail.classList.contains("hidden");
      document.body.classList.toggle("admin-player-sheet-open", open);
    };
    if (!detail.dataset.sheetObserverReady) {
      detail.dataset.sheetObserverReady = "1";
      new MutationObserver(sync).observe(detail, { attributes: true, attributeFilter: ["class"] });
    }
    sync();
  }

  function compactLegacyStyles() {
    admin.querySelectorAll(".feature-panel, .admin-ext-box").forEach((node) => node.classList.add("admin-compact-card"));
  }

  function refreshLayout() {
    hideLegacyBlocks();
    buildDashboard();
    moveUsefulBlocks();
    compactLegacyStyles();
    setupPlayerSheet();
    markMode();
  }

  document.addEventListener("click", async (event) => {
    const jump = event.target.closest?.("[data-admin-jump]");
    if (jump) {
      switchPane(jump.dataset.adminJump);
      return;
    }

    const found = event.target.closest?.("[data-redesign-open-player]");
    if (found) {
      switchPane("players");
      setTimeout(() => {
        const button = document.querySelector(`[data-power-open="${found.dataset.redesignOpenPlayer}"]`);
        if (button) button.click();
        else {
          const search = document.getElementById("power-player-search");
          if (search) {
            search.value = found.dataset.redesignOpenPlayer;
            search.dispatchEvent(new Event("input", { bubbles: true }));
            setTimeout(() => document.querySelector(`[data-power-open="${found.dataset.redesignOpenPlayer}"]`)?.click(), 350);
          }
        }
      }, 100);
      return;
    }

    if (event.target.closest?.("#admin-dashboard-refresh")) {
      await loadDashboard();
      return;
    }
    if (event.target.closest?.("#admin-dashboard-search-btn")) {
      await dashboardSearch();
      return;
    }
  });

  document.addEventListener("keydown", (event) => {
    if (event.target?.id === "admin-dashboard-query" && event.key === "Enter") {
      event.preventDefault();
      dashboardSearch();
    }
  });

  const screenObserver = new MutationObserver(markMode);
  screenObserver.observe(admin, { attributes: true, attributeFilter: ["class"] });

  refreshLayout();
  setTimeout(refreshLayout, 600);
  setTimeout(refreshLayout, 1600);
  setTimeout(refreshLayout, 3200);
  setTimeout(loadDashboard, 700);
})();