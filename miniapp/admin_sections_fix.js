(() => {
  const admin = document.getElementById("tab-admin");
  if (!admin || document.getElementById("admin-sections-fix-marker")) return;

  const marker = document.createElement("span");
  marker.id = "admin-sections-fix-marker";
  marker.hidden = true;
  admin.appendChild(marker);

  const tg = window.Telegram?.WebApp;
  const initData = tg?.initData || "";
  const API_BASE = "https://le-nyan-paris-bot.onrender.com";

  function esc(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");
  }

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

  function currentPane() {
    return admin.dataset.currentAdminPane
      || admin.querySelector("#admin-section-tabs .admin-section-tab.active")?.dataset.adminSection
      || "overview";
  }

  function chooseWarningPane() {
    const preferred =
      document.getElementById("feature-admin-warnings")
      || document.getElementById("hard-warning-admin-card")
      || document.getElementById("admin-warning-card");

    if (preferred) {
      preferred.dataset.adminPane = "warnings";
      preferred.dataset.adminEssential = "warnings";
      preferred.classList.remove("admin-layout-hidden", "power-replaced");
      return preferred;
    }

    const pane = document.createElement("article");
    pane.id = "unified-admin-warnings";
    pane.className = "card admin-primary-card";
    pane.dataset.adminPane = "warnings";
    pane.dataset.adminEssential = "warnings";
    pane.innerHTML = `
      <p class="eyebrow">дисциплина</p>
      <h2>предупреждения</h2>
      <div class="grid two">
        <input id="unified-warning-target" placeholder="@username или Telegram ID">
        <select id="unified-warning-type">
          <option value="oral">устное замечание</option>
          <option value="remark">замечание</option>
          <option value="warning">предупреждение</option>
          <option value="reprimand">выговор</option>
        </select>
      </div>
      <textarea id="unified-warning-reason" placeholder="причина"></textarea>
      <div class="feature-actions">
        <button id="unified-warning-send" class="primary" type="button">выдать</button>
        <button id="unified-warning-load" class="small" type="button">показать записи</button>
      </div>
      <div id="unified-warning-message" class="message"></div>
      <div id="unified-warning-list" class="feature-list"></div>
    `;
    admin.appendChild(pane);

    document.getElementById("unified-warning-send")?.addEventListener("click", async () => {
      const msg = document.getElementById("unified-warning-message");
      const target = document.getElementById("unified-warning-target")?.value.trim() || "";
      const warning_type = document.getElementById("unified-warning-type")?.value || "warning";
      const reason = document.getElementById("unified-warning-reason")?.value.trim() || "";
      if (!target || !reason) {
        msg.textContent = "укажи игрока и причину";
        return;
      }
      msg.textContent = "выдаю...";
      try {
        const data = await post("/api/admin/warnings/create", { target, warning_type, reason });
        msg.textContent = data.notify_ok
          ? `готово: ${data.target || target}`
          : `запись создана для ${data.target || target}, но уведомление не отправилось`;
        document.getElementById("unified-warning-reason").value = "";
      } catch (error) {
        msg.textContent = error.message;
      }
    });

    document.getElementById("unified-warning-load")?.addEventListener("click", async () => {
      const msg = document.getElementById("unified-warning-message");
      const list = document.getElementById("unified-warning-list");
      const target = document.getElementById("unified-warning-target")?.value.trim() || "";
      if (!target) {
        msg.textContent = "укажи игрока";
        return;
      }
      msg.textContent = "загрузка...";
      try {
        const data = await post("/api/admin/warnings/list", { target });
        list.innerHTML = (data.warnings || []).length
          ? data.warnings.map((w) => `
              <div class="warning-item">
                <b>${esc(w.type_label || w.type || "предупреждение")}</b>
                <p>${esc(w.reason || "без причины")}</p>
                <small>${esc(w.created_at || "")}</small>
              </div>
            `).join("")
          : '<div class="empty">записей нет</div>';
        msg.textContent = `записей: ${(data.warnings || []).length}`;
      } catch (error) {
        msg.textContent = error.message;
      }
    });

    return pane;
  }

  function chooseGroupMailPane() {
    const backendPane = document.getElementById("group-letter-target")?.closest('[data-admin-pane="letters"]');
    const sitePane = document.getElementById("hard-admin-panels");

    const preferred = backendPane || sitePane;
    if (preferred) {
      preferred.dataset.adminPane = "letters";
      preferred.dataset.adminEssential = "group-mail";
      preferred.classList.remove("admin-layout-hidden", "power-replaced");
      return preferred;
    }

    const pane = document.createElement("article");
    pane.id = "unified-group-mail";
    pane.className = "card admin-primary-card";
    pane.dataset.adminPane = "letters";
    pane.dataset.adminEssential = "group-mail";
    pane.innerHTML = `
      <p class="eyebrow">массовая рассылка</p>
      <h2>по разделам</h2>
      <div class="grid two">
        <select id="unified-group-target">
          <option value="all">всем</option>
          <option>двор</option><option>суд</option><option>полиция</option>
          <option>армия</option><option>пресса</option><option>медицина</option>
          <option>церковь</option><option>город</option><option>подполье</option><option>рынок</option>
        </select>
        <select id="unified-group-type">
          <option value="letter">письмо</option>
          <option value="summons">повестка</option>
          <option value="task">задание</option>
          <option value="rumor">слух</option>
          <option value="warning">предупреждение</option>
        </select>
      </div>
      <input id="unified-group-title" placeholder="заголовок">
      <textarea id="unified-group-body" placeholder="текст рассылки"></textarea>
      <button id="unified-group-send" class="primary wide" type="button">отправить по разделу</button>
      <div id="unified-group-message" class="message"></div>
    `;
    admin.appendChild(pane);

    document.getElementById("unified-group-send")?.addEventListener("click", async () => {
      const msg = document.getElementById("unified-group-message");
      const body = document.getElementById("unified-group-body")?.value.trim() || "";
      if (!body) {
        msg.textContent = "введи текст рассылки";
        return;
      }
      msg.textContent = "отправляю...";
      try {
        const data = await post("/api/admin/letters/group", {
          target_group: document.getElementById("unified-group-target")?.value || "all",
          letter_type: document.getElementById("unified-group-type")?.value || "letter",
          title: document.getElementById("unified-group-title")?.value.trim() || "письмо из канцелярии",
          body,
        });
        msg.textContent = `готово: создано ${data.letters_created || 0}, уведомлено ${data.notified || 0}, ошибок ${data.notify_failed || 0}`;
        document.getElementById("unified-group-body").value = "";
      } catch (error) {
        msg.textContent = error.message;
      }
    });

    return pane;
  }

  function hideDuplicateCriticalPanes(warningPane, groupPane) {
    admin.querySelectorAll('[data-admin-pane="warnings"]').forEach((pane) => {
      if (pane === warningPane) return;
      pane.classList.add("admin-layout-hidden");
      pane.dataset.adminDuplicate = "true";
    });

    const groupCandidates = [
      document.getElementById("hard-admin-panels"),
      document.getElementById("group-letter-target")?.closest('[data-admin-pane="letters"]'),
      document.getElementById("unified-group-mail"),
    ].filter(Boolean);

    groupCandidates.forEach((pane) => {
      if (pane === groupPane) return;
      pane.classList.add("admin-layout-hidden");
      pane.dataset.adminDuplicate = "true";
    });
  }

  function syncPaneVisibility() {
    const pane = currentPane();

    admin.querySelectorAll("[data-admin-pane]").forEach((node) => {
      const permanentlyHidden =
        node.dataset.adminDuplicate === "true"
        || node.classList.contains("power-replaced");

      if (node.dataset.adminEssential) {
        node.classList.remove("admin-layout-hidden");
      }

      const show = !permanentlyHidden
        && !node.classList.contains("admin-layout-hidden")
        && node.dataset.adminPane === pane;

      node.classList.toggle("admin-pane-hidden", !show);
      node.setAttribute("aria-hidden", show ? "false" : "true");
    });

    const extPanel = document.getElementById("admin-ext-panel");
    if (extPanel) {
      const visibleChild = [...extPanel.querySelectorAll(":scope > [data-admin-pane]")]
        .some((node) => !node.classList.contains("admin-pane-hidden") && !node.classList.contains("admin-layout-hidden"));
      extPanel.classList.toggle("admin-pane-hidden", !visibleChild);
    }
  }

  function setupLettersCollapse() {
    const list = document.getElementById("admin-letters-list");
    if (!list || document.getElementById("admin-letters-collapse-toggle")) return;

    const panel = list.closest('[data-admin-pane="letters"]') || list.parentElement;
    if (!panel) return;

    const button = document.createElement("button");
    button.id = "admin-letters-collapse-toggle";
    button.className = "small letters-collapse-toggle";
    button.type = "button";

    const key = "empire-admin-letters-collapsed";
    let collapsed = localStorage.getItem(key) === "1";

    const apply = () => {
      list.classList.toggle("letters-list-collapsed", collapsed);
      button.textContent = collapsed ? "показать письма" : "скрыть письма";
      button.setAttribute("aria-expanded", collapsed ? "false" : "true");
      localStorage.setItem(key, collapsed ? "1" : "0");
    };

    button.addEventListener("click", () => {
      collapsed = !collapsed;
      apply();
    });

    const head = panel.querySelector(".section-head");
    if (head) {
      const existingButton = head.querySelector("button");
      if (existingButton?.parentElement === head) {
        const actions = document.createElement("div");
        actions.className = "letters-head-actions";
        head.replaceChild(actions, existingButton);
        actions.append(existingButton, button);
      } else {
        head.appendChild(button);
      }
    } else {
      panel.insertBefore(button, list);
    }

    apply();
  }

  function moveCriticalPanesUp(warningPane, groupPane) {
    const warningFirst = [...admin.querySelectorAll('[data-admin-pane="warnings"]')]
      .find((node) => node !== warningPane && !node.classList.contains("admin-layout-hidden"));
    if (warningFirst) admin.insertBefore(warningPane, warningFirst);

    const targeted = document.getElementById("power-targeted-mail");
    if (groupPane && targeted && groupPane !== targeted && targeted.nextElementSibling !== groupPane) {
      targeted.after(groupPane);
    }
  }

  function ensure() {
    const warningPane = chooseWarningPane();
    const groupPane = chooseGroupMailPane();
    hideDuplicateCriticalPanes(warningPane, groupPane);
    moveCriticalPanesUp(warningPane, groupPane);
    setupLettersCollapse();
    syncPaneVisibility();
  }

  document.addEventListener("adminpanechange", () => setTimeout(syncPaneVisibility, 0));

  function adminFieldIsActive() {
    const element = document.activeElement;
    return Boolean(
      element
      && admin.contains(element)
      && ["INPUT", "SELECT", "TEXTAREA"].includes(element.tagName)
    );
  }

  function safeEnsure() {
    if (adminFieldIsActive()) {
      // Никогда не перестраиваем админку, пока пользователь выбирает
      // пункт select или печатает в поле.
      setTimeout(safeEnsure, 400);
      return;
    }
    ensure();
  }

  // Нужные старые модули догружаются чуть позже. Проверяем их только
  // несколько раз на старте, без бесконечного MutationObserver.
  ensure();
  setTimeout(safeEnsure, 550);
  setTimeout(safeEnsure, 1500);
  setTimeout(safeEnsure, 3000);
})();