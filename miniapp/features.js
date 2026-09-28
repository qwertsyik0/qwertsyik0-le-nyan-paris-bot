(() => {
  const tg = window.Telegram?.WebApp;
  const initData = tg?.initData || "";

  function esc(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");
  }

  function formatDate(value) {
    if (!value) return "—";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return String(value);
    return date.toLocaleString("ru-RU", { day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit" });
  }

  async function post(path, payload = {}) {
    const response = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ initData, ...payload }),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || "ошибка запроса");
    return data;
  }

  function showTab(name) {
    document.querySelectorAll(".tab").forEach((button) => button.classList.toggle("active", button.dataset.tab === name));
    document.querySelectorAll(".screen").forEach((screen) => screen.classList.toggle("active", screen.id === `tab-${name}`));
    window.scrollTo({ top: 0, behavior: "smooth" });
    if (name === "profile") loadProfile();
    if (name === "warnings") loadWarnings();
  }

  function injectStyle() {
    if (document.getElementById("feature-style")) return;
    const style = document.createElement("style");
    style.id = "feature-style";
    style.textContent = `
      .feature-grid{display:grid;gap:12px}.feature-card{border:1px solid rgba(88,62,33,.15);border-radius:18px;padding:14px;background:rgba(255,255,255,.58)}
      .feature-kv{display:grid;gap:8px}.feature-kv div{display:grid;grid-template-columns:120px 1fr;gap:8px;border-bottom:1px solid rgba(88,62,33,.08);padding-bottom:8px}.feature-kv b{font-size:12px;text-transform:uppercase;opacity:.68}.feature-kv span{white-space:pre-wrap}
      .feature-list{display:grid;gap:10px}.feature-player,.warning-item{border:1px solid rgba(88,62,33,.12);border-radius:16px;padding:12px;background:rgba(255,255,255,.52)}
      .feature-player header,.warning-item header{display:flex;justify-content:space-between;gap:10px;align-items:flex-start}.feature-player code,.warning-item code{font-size:11px;opacity:.7}.feature-player .row{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-top:8px}.feature-player input,.feature-player select,.feature-panel input,.feature-panel select,.feature-panel textarea{width:100%;box-sizing:border-box;border-radius:12px;border:1px solid rgba(88,62,33,.18);padding:9px;background:rgba(255,255,255,.72);color:inherit}.feature-panel textarea{min-height:120px}.feature-actions{display:flex;flex-wrap:wrap;gap:8px;margin-top:10px}.feature-note{font-size:12px;opacity:.72;white-space:pre-wrap}.hidden-feature{display:none!important}
      .warning-summary{border-color:rgba(140,40,40,.28);background:rgba(255,235,225,.72)}.warning-summary h3,.warning-title{margin:0 0 6px;text-transform:uppercase;letter-spacing:.06em}.warning-count{font-size:28px;line-height:1;font-weight:800}.warning-item b{display:block}.warning-item p{white-space:pre-wrap}.warning-type{display:inline-block;border:1px solid rgba(88,62,33,.18);border-radius:999px;padding:3px 9px;font-size:12px;background:rgba(255,255,255,.55)}
    `;
    document.head.appendChild(style);
  }

  function injectProfileTab() {
    const tabbar = document.querySelector(".tabbar");
    if (!tabbar || document.querySelector('[data-tab="profile"]')) return;
    const button = document.createElement("button");
    button.className = "tab";
    button.dataset.tab = "profile";
    button.type = "button";
    button.textContent = "профиль";
    tabbar.insertBefore(button, document.querySelector('[data-tab="status"]'));
    button.addEventListener("click", () => showTab("profile"));

    const main = document.querySelector("main.content");
    const screen = document.createElement("section");
    screen.id = "tab-profile";
    screen.className = "screen";
    screen.innerHTML = `
      <article class="card">
        <div class="section-head"><div><p class="eyebrow">личное дело</p><h2>мой профиль</h2></div><button id="profile-refresh" class="small" type="button">обновить</button></div>
        <div id="profile-message" class="message"></div>
        <div id="profile-content" class="feature-grid"></div>
      </article>
    `;
    main?.insertBefore(screen, document.getElementById("tab-status"));
    document.getElementById("profile-refresh")?.addEventListener("click", loadProfile);
  }

  function injectWarningsTab() {
    const tabbar = document.querySelector(".tabbar");
    if (!tabbar || document.querySelector('[data-tab="warnings"]')) return;
    const button = document.createElement("button");
    button.className = "tab";
    button.dataset.tab = "warnings";
    button.type = "button";
    button.textContent = "предупреждения";
    tabbar.insertBefore(button, document.getElementById("admin-tab-button") || null);
    button.addEventListener("click", () => showTab("warnings"));

    const main = document.querySelector("main.content");
    const screen = document.createElement("section");
    screen.id = "tab-warnings";
    screen.className = "screen";
    screen.innerHTML = `
      <article class="card">
        <div class="section-head"><div><p class="eyebrow">дисциплина</p><h2>предупреждения</h2></div><button id="warnings-refresh" class="small" type="button">обновить</button></div>
        <div id="warnings-message" class="message"></div>
        <div id="warnings-list" class="feature-list"></div>
      </article>
    `;
    main?.insertBefore(screen, document.getElementById("tab-admin"));
    document.getElementById("warnings-refresh")?.addEventListener("click", loadWarnings);
  }

  function renderWarningItems(rows) {
    if (!rows?.length) {
      return `<div class="feature-card">активных предупреждений нет.</div>`;
    }
    return rows.map((item) => `
      <div class="warning-item">
        <header>
          <div>
            <span class="warning-type">${esc(item.type_label || item.type)}</span>
            <b>${esc(item.reason || "без причины")}</b>
          </div>
          <code>#${esc(item.id)}</code>
        </header>
        <p class="feature-note">от кого: ${esc(item.admin || "—")}\nкогда: ${esc(formatDate(item.created_at))}</p>
      </div>
    `).join("");
  }

  async function loadWarnings() {
    const list = document.getElementById("warnings-list");
    const msg = document.getElementById("warnings-message");
    if (!list || !msg || !initData) return;
    msg.textContent = "загрузка...";
    try {
      const data = await post("/api/warnings");
      list.innerHTML = renderWarningItems(data.warnings || []);
      msg.textContent = data.count ? `активных предупреждений: ${data.count}` : "предупреждений нет";
      renderWarningsSummary(data.warnings || []);
    } catch (error) {
      msg.textContent = error.message;
    }
  }

  function renderWarningsSummary(rows) {
    const card = document.getElementById("profile-warning-card");
    const count = document.getElementById("profile-warning-count");
    const text = document.getElementById("profile-warning-text");
    if (!card || !count || !text) return;
    const activeCount = rows?.length || 0;
    count.textContent = String(activeCount);
    text.textContent = activeCount ? "есть активные записи. откройте список, чтобы посмотреть тип, причину, кто выдал и когда." : "активных записей нет.";
  }

  async function loadProfile() {
    const box = document.getElementById("profile-content");
    const msg = document.getElementById("profile-message");
    if (!box || !msg || !initData) return;
    msg.textContent = "загрузка...";
    try {
      const data = await post("/api/profile");
      const app = data.application;
      if (!app) {
        box.innerHTML = `<div class="feature-card">анкета пока не найдена. откройте раздел «анкета» и отправьте заявку.</div>`;
        msg.textContent = "";
        return;
      }
      box.innerHTML = `
        <div class="feature-card warning-summary" id="profile-warning-card">
          <h3>предупреждения</h3>
          <div class="warning-count" id="profile-warning-count">—</div>
          <p id="profile-warning-text">загрузка...</p>
          <button type="button" class="small" data-feature-open-tab="warnings">посмотреть</button>
        </div>
        <div class="feature-card feature-kv">
          <div><b>персонаж</b><span>${esc(app.character_name)}</span></div>
          <div><b>роль</b><span>${esc(app.assigned_role)}</span></div>
          <div><b>раздел</b><span>${esc(app.affiliation)}</span></div>
          <div><b>анкета</b><span>${esc(app.status)}</span></div>
          <div><b>активность</b><span>${esc(app.player_status_label)}</span></div>
          <div><b>возраст</b><span>${esc(app.character_age)}</span></div>
          <div><b>пол</b><span>${esc(app.character_gender)}</span></div>
          <div><b>ориентация</b><span>${esc(app.character_orientation)}</span></div>
          <div><b>метки</b><span>${esc((app.story_tags || []).join(", ") || "—")}</span></div>
        </div>
        <div class="feature-card"><h3>описание</h3><p>${esc(app.character_description || "—")}</p></div>
        <div class="feature-card"><h3>характер</h3><p>${esc(app.character_personality || "—")}</p></div>
        <div class="feature-card"><h3>письма</h3>${(data.letters || []).length ? (data.letters || []).map((l) => `<p><b>${esc(l.title || l.type_label)}</b><br>${esc(l.body || "")}</p>`).join("") : "писем пока нет"}</div>
      `;
      msg.textContent = `непрочитанные письма: ${data.unread_letters || 0}`;
      loadWarnings();
    } catch (error) {
      msg.textContent = error.message;
    }
  }

  function injectAdminTools() {
    const admin = document.getElementById("tab-admin");
    if (!admin || document.getElementById("feature-admin-players")) return;
    admin.insertAdjacentHTML("beforeend", `
      <article class="card feature-panel" id="feature-admin-players">
        <div class="section-head"><div><p class="eyebrow">реестр из базы</p><h2>принятые участники</h2></div><button id="players-refresh" class="small" type="button">обновить</button></div>
        <div class="grid two">
          <input id="players-search" placeholder="поиск по юзу, имени, роли">
          <select id="players-filter"><option value="">все разделы</option><option>двор</option><option>суд</option><option>полиция</option><option>армия</option><option>пресса</option><option>медицина</option><option>церковь</option><option>город</option><option>подполье</option><option>рынок</option></select>
        </div>
        <div id="players-message" class="message"></div>
        <div id="players-list" class="feature-list"></div>
      </article>
      <article class="card feature-panel">
        <p class="eyebrow">массовые письма</p><h2>рассылка по разделам</h2>
        <div class="grid two"><select id="group-letter-target"><option value="all">всем</option><option>двор</option><option>суд</option><option>полиция</option><option>армия</option><option>пресса</option><option>медицина</option><option>церковь</option><option>город</option><option>подполье</option><option>рынок</option></select><select id="group-letter-type"><option value="letter">письмо</option><option value="summons">повестка</option><option value="task">задание</option><option value="rumor">слух</option><option value="warning">предупреждение</option></select></div>
        <input id="group-letter-title" placeholder="заголовок">
        <textarea id="group-letter-body" placeholder="текст письма"></textarea>
        <button id="group-letter-send" class="primary wide" type="button">отправить</button>
        <div id="group-letter-message" class="message"></div>
      </article>
      <article class="card feature-panel" id="feature-admin-warnings">
        <p class="eyebrow">дисциплина</p><h2>выдать предупреждение</h2>
        <div class="grid two">
          <input id="admin-warning-target" placeholder="@username или Telegram ID">
          <select id="admin-warning-type"><option value="oral">устное замечание</option><option value="remark">замечание</option><option value="warning">предупреждение</option><option value="reprimand">выговор</option></select>
        </div>
        <textarea id="admin-warning-reason" placeholder="причина: за что выдается"></textarea>
        <div class="feature-actions">
          <button id="admin-warning-send" class="primary" type="button">выдать</button>
          <button id="admin-warning-load" class="small" type="button">посмотреть игрока</button>
        </div>
        <div id="admin-warning-message" class="message"></div>
        <div id="admin-warning-list" class="feature-list"></div>
      </article>
    `);
    document.getElementById("players-refresh")?.addEventListener("click", loadPlayers);
    document.getElementById("players-search")?.addEventListener("input", () => setTimeout(loadPlayers, 150));
    document.getElementById("players-filter")?.addEventListener("change", loadPlayers);
    document.getElementById("group-letter-send")?.addEventListener("click", sendGroupLetter);
    document.getElementById("admin-warning-send")?.addEventListener("click", sendAdminWarning);
    document.getElementById("admin-warning-load")?.addEventListener("click", loadAdminWarnings);
  }

  async function loadPlayers() {
    const list = document.getElementById("players-list");
    const msg = document.getElementById("players-message");
    if (!list || !msg || !initData) return;
    msg.textContent = "загрузка...";
    try {
      const data = await post("/api/admin/players", { search: document.getElementById("players-search")?.value || "", affiliation: document.getElementById("players-filter")?.value || "" });
      msg.textContent = `участников: ${data.players.length}`;
      list.innerHTML = data.players.map((p) => `
        <div class="feature-player" data-id="${esc(p.telegram_id)}">
          <header><div><b>${esc(p.character_name)}</b><br><span>${esc(p.username || "без username")}</span></div><code>${esc(p.telegram_id)}</code></header>
          <p>${esc(p.assigned_role)} · ${esc(p.affiliation)} · ${esc(p.player_status_label)}</p>
          <p class="feature-note">метки: ${esc((p.story_tags || []).join(", ") || "—")}</p>
          <div class="row"><input data-field="assigned_role" value="${esc(p.assigned_role)}"><select data-field="player_status"><option value="active">активен</option><option value="low_activity">малоактив</option><option value="frozen">заморожен</option><option value="left">выбыл</option><option value="watch">под наблюдением</option></select></div>
          <input data-field="story_tags" value="${esc((p.story_tags || []).join(", "))}" placeholder="сюжетные метки через запятую">
          <div class="feature-actions"><button class="small" data-save-player type="button">сохранить</button><button class="small" data-open-card type="button">карточка</button></div>
        </div>
      `).join("");
      for (const item of list.querySelectorAll(".feature-player")) {
        const status = data.players.find((p) => String(p.telegram_id) === item.dataset.id)?.player_status || "active";
        item.querySelector('[data-field="player_status"]').value = status;
      }
    } catch (error) {
      msg.textContent = error.message;
    }
  }

  async function savePlayer(card) {
    const id = card.dataset.id;
    const payload = { identifier: id };
    for (const field of card.querySelectorAll("[data-field]")) payload[field.dataset.field] = field.value;
    await post("/api/admin/player/update", payload);
    await loadPlayers();
  }

  async function sendGroupLetter() {
    const msg = document.getElementById("group-letter-message");
    msg.textContent = "отправляю...";
    try {
      const data = await post("/api/admin/letters/group", {
        target_group: document.getElementById("group-letter-target").value,
        letter_type: document.getElementById("group-letter-type").value,
        title: document.getElementById("group-letter-title").value,
        body: document.getElementById("group-letter-body").value,
      });
      msg.textContent = `готово: создано ${data.letters_created}, уведомлено ${data.notified}, ошибок ${data.notify_failed}`;
    } catch (error) {
      msg.textContent = error.message;
    }
  }

  async function sendAdminWarning() {
    const msg = document.getElementById("admin-warning-message");
    const list = document.getElementById("admin-warning-list");
    msg.textContent = "выдаю...";
    try {
      const data = await post("/api/admin/warnings/create", {
        target: document.getElementById("admin-warning-target")?.value || "",
        warning_type: document.getElementById("admin-warning-type")?.value || "warning",
        reason: document.getElementById("admin-warning-reason")?.value || "",
      });
      msg.textContent = data.notify_ok ? `готово: ${data.target}` : `запись создана для ${data.target}, но уведомление не отправилось`;
      await loadAdminWarnings();
    } catch (error) {
      msg.textContent = error.message;
      if (list) list.innerHTML = "";
    }
  }

  async function loadAdminWarnings() {
    const msg = document.getElementById("admin-warning-message");
    const list = document.getElementById("admin-warning-list");
    const target = document.getElementById("admin-warning-target")?.value || "";
    if (!msg || !list) return;
    msg.textContent = "загрузка предупреждений...";
    try {
      const data = await post("/api/admin/warnings/list", { target });
      list.innerHTML = renderWarningItems(data.warnings || []);
      msg.textContent = `записей у ${data.target}: ${(data.warnings || []).length}`;
    } catch (error) {
      msg.textContent = error.message;
      list.innerHTML = "";
    }
  }

  document.addEventListener("click", async (event) => {
    const featureTab = event.target.closest("[data-feature-open-tab]");
    if (featureTab) showTab(featureTab.dataset.featureOpenTab);
    const save = event.target.closest("[data-save-player]");
    if (save) savePlayer(save.closest(".feature-player"));
    const open = event.target.closest("[data-open-card]");
    if (open) alert("карточка открывается через кнопку в основной админке или команду /app. быстрый просмотр уже виден в списке.");
  });

  injectStyle();
  injectProfileTab();
  injectWarningsTab();
  injectAdminTools();
  setTimeout(loadProfile, 700);
  setTimeout(loadWarnings, 900);
  setTimeout(loadPlayers, 1200);
})();
