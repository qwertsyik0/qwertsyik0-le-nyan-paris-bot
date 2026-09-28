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
  }

  function injectStyle() {
    const style = document.createElement("style");
    style.textContent = `
      .feature-grid{display:grid;gap:12px}.feature-card{border:1px solid rgba(88,62,33,.15);border-radius:18px;padding:14px;background:rgba(255,255,255,.58)}
      .feature-kv{display:grid;gap:8px}.feature-kv div{display:grid;grid-template-columns:120px 1fr;gap:8px;border-bottom:1px solid rgba(88,62,33,.08);padding-bottom:8px}.feature-kv b{font-size:12px;text-transform:uppercase;opacity:.68}.feature-kv span{white-space:pre-wrap}
      .feature-list{display:grid;gap:10px}.feature-player{border:1px solid rgba(88,62,33,.12);border-radius:16px;padding:12px;background:rgba(255,255,255,.52)}
      .feature-player header{display:flex;justify-content:space-between;gap:10px;align-items:flex-start}.feature-player code{font-size:11px;opacity:.7}.feature-player .row{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-top:8px}.feature-player input,.feature-player select,.feature-panel input,.feature-panel select,.feature-panel textarea{width:100%;box-sizing:border-box;border-radius:12px;border:1px solid rgba(88,62,33,.18);padding:9px;background:rgba(255,255,255,.72);color:inherit}.feature-panel textarea{min-height:120px}.feature-actions{display:flex;flex-wrap:wrap;gap:8px;margin-top:10px}.feature-note{font-size:12px;opacity:.72;white-space:pre-wrap}.hidden-feature{display:none!important}
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
    `);
    document.getElementById("players-refresh")?.addEventListener("click", loadPlayers);
    document.getElementById("players-search")?.addEventListener("input", () => setTimeout(loadPlayers, 150));
    document.getElementById("players-filter")?.addEventListener("change", loadPlayers);
    document.getElementById("group-letter-send")?.addEventListener("click", sendGroupLetter);
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

  document.addEventListener("click", async (event) => {
    const save = event.target.closest("[data-save-player]");
    if (save) savePlayer(save.closest(".feature-player"));
    const open = event.target.closest("[data-open-card]");
    if (open) alert("карточка открывается через кнопку в основной админке или команду /app. быстрый просмотр уже виден в списке.");
  });

  injectStyle();
  injectProfileTab();
  injectAdminTools();
  setTimeout(loadProfile, 700);
  setTimeout(loadPlayers, 1200);
})();
