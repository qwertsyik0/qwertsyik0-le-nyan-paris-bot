(() => {
  if (document.getElementById("player-social-marker")) return;

  const marker = document.createElement("span");
  marker.id = "player-social-marker";
  marker.hidden = true;
  document.body.appendChild(marker);

  const tg = window.Telegram?.WebApp;
  const initData = tg?.initData || "";
  const API_BASE = location.hostname.includes("github.io")
    ? "https://le-nyan-paris-bot.onrender.com"
    : "";

  const locations = [
    "дворец","суд и канцелярия","полиция","тюрьма","газета и слухи","рынок",
    "кафе и салон","больница","театр","армия и гарнизон","подполье и катакомбы",
    "улицы Парижа","другое"
  ];

  const sceneTypes = {
    any: "любая сцена",
    meeting: "знакомство",
    calm: "спокойная сцена",
    plot: "сюжетная сцена",
    conflict: "конфликт",
    dialogue: "разговор",
  };

  const npcTypes = [
    "любой NPC","врач","солдат","жандарм","слуга","чиновник",
    "торговец","священник","горожанин","другой"
  ];

  const npcStatuses = {
    new: "новая",
    in_work: "в работе",
    done: "выполнена",
    rejected: "отклонена",
    cancelled: "отменена",
  };

  let isAdmin = false;

  function esc(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");
  }

  function fmt(value) {
    if (!value) return "—";
    const d = new Date(value);
    if (Number.isNaN(d.getTime())) return String(value);
    return d.toLocaleString("ru-RU", {
      day:"2-digit", month:"2-digit", hour:"2-digit", minute:"2-digit"
    });
  }

  async function post(path, payload = {}) {
    const response = await fetch(API_BASE + path, {
      method: "POST",
      headers: {"Content-Type":"application/json"},
      body: JSON.stringify({initData, ...payload}),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || "ошибка запроса");
    return data;
  }

  function msg(id, text, type = "") {
    const el = document.getElementById(id);
    if (!el) return;
    el.textContent = text || "";
    el.className = "message " + type;
  }

  function showTab(name) {
    document.querySelectorAll(".tab").forEach((button) => {
      button.classList.toggle("active", button.dataset.tab === name);
    });
    document.querySelectorAll(".screen").forEach((screen) => {
      screen.classList.toggle("active", screen.id === `tab-${name}`);
    });
    if (name === "game") {
      loadCoplay();
      loadNpcMine();
    }
    window.scrollTo({top:0, behavior:"smooth"});
  }

  function optionList(items) {
    return items.map((x) => `<option value="${esc(x)}">${esc(x)}</option>`).join("");
  }

  function injectGameTab() {
    const tabbar = document.querySelector(".tabbar");
    const main = document.querySelector("main.content");
    if (!tabbar || !main || document.querySelector('[data-tab="game"]')) return;

    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "tab";
    btn.dataset.tab = "game";
    btn.textContent = "игра";

    const profile = tabbar.querySelector('[data-tab="profile"]');
    const status = tabbar.querySelector('[data-tab="status"]');
    if (profile) profile.after(btn);
    else if (status) tabbar.insertBefore(btn, status);
    else tabbar.appendChild(btn);

    btn.addEventListener("click", () => showTab("game"));

    const section = document.createElement("section");
    section.id = "tab-game";
    section.className = "screen";
    section.innerHTML = `
      <article class="card social-card">
        <div class="section-head">
          <div>
            <p class="eyebrow">игра с участниками</p>
            <h2>найти соигрока</h2>
          </div>
          <button id="coplay-refresh" type="button" class="small">обновить</button>
        </div>

        <div class="social-form">
          <div class="grid two">
            <label>
              <span>локация</span>
              <select id="coplay-location">${optionList(locations)}</select>
            </label>
            <label>
              <span>что хочется отыграть</span>
              <select id="coplay-scene-type">
                ${Object.entries(sceneTypes).map(([v,l])=>`<option value="${v}">${l}</option>`).join("")}
              </select>
            </label>
          </div>
          <label>
            <span>коротко о сцене <small>необязательно</small></span>
            <textarea id="coplay-note" maxlength="800" placeholder="например: ищу кого-нибудь для спокойного знакомства в кафе"></textarea>
          </label>
          <button id="coplay-create" type="button" class="primary wide">начать поиск</button>
          <div id="coplay-message" class="message"></div>
        </div>

        <div class="social-subhead">
          <h3>кто сейчас ищет игру</h3>
          <span id="coplay-count" class="pill muted">—</span>
        </div>
        <div id="coplay-list" class="social-list"></div>
      </article>

      <article class="card social-card">
        <div class="section-head">
          <div>
            <p class="eyebrow">помощь со сценой</p>
            <h2>запросить NPC</h2>
          </div>
          <button id="npc-mine-refresh" type="button" class="small">обновить</button>
        </div>

        <div class="social-form">
          <div class="grid two">
            <label>
              <span>кто нужен</span>
              <select id="npc-type">${optionList(npcTypes)}</select>
            </label>
            <label>
              <span>локация</span>
              <select id="npc-location">${optionList(locations)}</select>
            </label>
          </div>
          <label>
            <span>когда нужен <small>необязательно</small></span>
            <input id="npc-when" maxlength="160" placeholder="сейчас / вечером / во время конкретной сцены">
          </label>
          <label>
            <span>что должен сделать NPC</span>
            <textarea id="npc-description" maxlength="1400" placeholder="опиши сцену и зачем в ней нужен NPC"></textarea>
          </label>
          <button id="npc-create" type="button" class="primary wide">отправить заявку</button>
          <div id="npc-message" class="message"></div>
        </div>

        <div class="social-subhead"><h3>мои NPC-заявки</h3></div>
        <div id="npc-my-list" class="social-list"></div>
      </article>
    `;

    const adminScreen = document.getElementById("tab-admin");
    if (adminScreen) main.insertBefore(section, adminScreen);
    else main.appendChild(section);

    document.getElementById("coplay-refresh")?.addEventListener("click", loadCoplay);
    document.getElementById("coplay-create")?.addEventListener("click", createCoplay);
    document.getElementById("npc-mine-refresh")?.addEventListener("click", loadNpcMine);
    document.getElementById("npc-create")?.addEventListener("click", createNpc);
  }

  async function loadCoplay() {
    const root = document.getElementById("coplay-list");
    if (!root || !initData) return;
    msg("coplay-message", "загрузка...");
    try {
      const data = await post("/api/game/coplay/list");
      const rows = data.requests || [];
      document.getElementById("coplay-count").textContent = String(rows.length);
      root.innerHTML = rows.length ? rows.map((r) => `
        <div class="social-item ${r.own ? "own" : ""}">
          <div class="social-item-head">
            <div>
              <b>${esc(r.character_name)}</b>
              <span>${esc(r.username || "без username")} · ${esc(r.assigned_role || "без роли")}</span>
            </div>
            <span class="social-status">${esc(r.scene_type_label)}</span>
          </div>
          <div class="social-meta">
            <span>📍 ${esc(r.location)}</span>
            <span>откликов: ${esc(r.responses_count || 0)}</span>
            <span>${esc(fmt(r.created_at))}</span>
          </div>
          ${r.note ? `<p>${esc(r.note)}</p>` : ""}
          <div class="social-actions">
            ${r.own
              ? `<button class="small danger" type="button" data-coplay-close="${r.id}">закрыть поиск</button>`
              : `<button class="small ${r.responded ? "disabled-action" : ""}" type="button"
                    data-coplay-respond="${r.id}" ${r.responded ? "disabled" : ""}>
                    ${r.responded ? "отклик отправлен" : "откликнуться"}
                 </button>`
            }
          </div>
        </div>
      `).join("") : '<div class="empty">сейчас никто не ищет соигрока. можно стать первым.</div>';
      msg("coplay-message", "");
    } catch (error) {
      root.innerHTML = "";
      msg("coplay-message", error.message, "error");
    }
  }

  async function createCoplay() {
    const note = document.getElementById("coplay-note")?.value.trim() || "";
    msg("coplay-message", "создаю поиск...");
    try {
      await post("/api/game/coplay/create", {
        location: document.getElementById("coplay-location")?.value || "",
        scene_type: document.getElementById("coplay-scene-type")?.value || "any",
        note,
      });
      document.getElementById("coplay-note").value = "";
      tg?.HapticFeedback?.notificationOccurred?.("success");
      msg("coplay-message", "поиск опубликован", "success");
      await loadCoplay();
    } catch (error) {
      msg("coplay-message", error.message, "error");
    }
  }

  async function respondCoplay(id) {
    try {
      await post("/api/game/coplay/respond", {request_id:Number(id)});
      tg?.HapticFeedback?.notificationOccurred?.("success");
      await loadCoplay();
    } catch (error) {
      msg("coplay-message", error.message, "error");
    }
  }

  async function closeCoplay(id) {
    try {
      await post("/api/game/coplay/close", {request_id:Number(id)});
      tg?.HapticFeedback?.notificationOccurred?.("success");
      msg("coplay-message", "поиск закрыт", "success");
      await loadCoplay();
    } catch (error) {
      msg("coplay-message", error.message, "error");
    }
  }

  async function loadNpcMine() {
    const root = document.getElementById("npc-my-list");
    if (!root || !initData) return;
    try {
      const data = await post("/api/game/npc/list");
      const rows = data.requests || [];
      root.innerHTML = rows.length ? rows.map((r) => `
        <div class="social-item">
          <div class="social-item-head">
            <div><b>#${r.id} · ${esc(r.npc_type)}</b><span>${esc(r.location)}</span></div>
            <span class="social-status status-${esc(r.status)}">${esc(r.status_label)}</span>
          </div>
          ${r.when_text ? `<div class="social-meta"><span>когда: ${esc(r.when_text)}</span></div>` : ""}
          <p>${esc(r.description)}</p>
          ${r.admin_comment ? `<div class="social-admin-comment"><b>администрация:</b> ${esc(r.admin_comment)}</div>` : ""}
          <div class="social-meta"><span>${esc(fmt(r.created_at))}</span></div>
          ${["new","in_work"].includes(r.status)
            ? `<div class="social-actions"><button class="small danger" type="button" data-npc-cancel="${r.id}">отменить заявку</button></div>`
            : ""
          }
        </div>
      `).join("") : '<div class="empty">NPC-заявок пока нет.</div>';
    } catch (error) {
      root.innerHTML = `<div class="empty">${esc(error.message)}</div>`;
    }
  }

  async function createNpc() {
    const description = document.getElementById("npc-description")?.value.trim() || "";
    if (!description) return msg("npc-message", "опиши, зачем нужен NPC", "error");
    msg("npc-message", "отправляю...");
    try {
      const data = await post("/api/game/npc/create", {
        npc_type: document.getElementById("npc-type")?.value || "",
        location: document.getElementById("npc-location")?.value || "",
        when_text: document.getElementById("npc-when")?.value.trim() || "",
        description,
      });
      document.getElementById("npc-description").value = "";
      document.getElementById("npc-when").value = "";
      tg?.HapticFeedback?.notificationOccurred?.("success");
      msg("npc-message", `заявка #${data.request_id} отправлена администрации`, "success");
      await loadNpcMine();
    } catch (error) {
      msg("npc-message", error.message, "error");
    }
  }

  async function cancelNpc(id) {
    try {
      await post("/api/game/npc/cancel", {request_id:Number(id)});
      tg?.HapticFeedback?.notificationOccurred?.("success");
      await loadNpcMine();
    } catch (error) {
      msg("npc-message", error.message, "error");
    }
  }

  function injectAdminNpc() {
    const admin = document.getElementById("tab-admin");
    if (!admin || document.getElementById("admin-npc-panel")) return;

    const panel = document.createElement("article");
    panel.id = "admin-npc-panel";
    panel.className = "card admin-primary-card";
    panel.dataset.adminPane = "npc";
    panel.innerHTML = `
      <div class="section-head">
        <div><p class="eyebrow">заявки участников</p><h2>NPC</h2></div>
        <button id="admin-npc-refresh" type="button" class="small">обновить</button>
      </div>
      <label class="admin-npc-filter">
        <span>показывать</span>
        <select id="admin-npc-status-filter">
          <option value="active">активные</option>
          <option value="new">новые</option>
          <option value="in_work">в работе</option>
          <option value="done">выполненные</option>
          <option value="rejected">отклонённые</option>
          <option value="cancelled">отменённые</option>
          <option value="all">все</option>
        </select>
      </label>
      <div id="admin-npc-message" class="message"></div>
      <div id="admin-npc-list" class="social-list"></div>
    `;
    admin.appendChild(panel);

    document.getElementById("admin-npc-refresh")?.addEventListener("click", loadAdminNpc);
    document.getElementById("admin-npc-status-filter")?.addEventListener("change", loadAdminNpc);
  }

  async function loadAdminNpc() {
    const root = document.getElementById("admin-npc-list");
    if (!root || !initData) return;
    msg("admin-npc-message", "загрузка...");
    try {
      const data = await post("/api/admin/npc/list", {
        status: document.getElementById("admin-npc-status-filter")?.value || "active",
      });
      const rows = data.requests || [];
      root.innerHTML = rows.length ? rows.map((r) => `
        <div class="social-item admin-npc-item" data-admin-npc-id="${r.id}">
          <div class="social-item-head">
            <div>
              <b>#${r.id} · ${esc(r.npc_type)}</b>
              <span>${esc(r.character_name)} · ${esc(r.username || "без username")} · ${esc(r.affiliation || "—")}</span>
            </div>
            <span class="social-status status-${esc(r.status)}">${esc(r.status_label)}</span>
          </div>
          <div class="social-meta">
            <span>📍 ${esc(r.location)}</span>
            ${r.when_text ? `<span>когда: ${esc(r.when_text)}</span>` : ""}
            <span>${esc(fmt(r.created_at))}</span>
          </div>
          <p>${esc(r.description)}</p>
          <textarea data-admin-npc-comment maxlength="800" placeholder="комментарий игроку">${esc(r.admin_comment || "")}</textarea>
          <div class="social-actions admin-npc-actions">
            <button class="small" type="button" data-admin-npc-status="in_work">в работу</button>
            <button class="small" type="button" data-admin-npc-status="done">выполнено</button>
            <button class="small danger" type="button" data-admin-npc-status="rejected">отклонить</button>
          </div>
        </div>
      `).join("") : '<div class="empty">заявок нет.</div>';
      msg("admin-npc-message", `заявок: ${rows.length}`);
    } catch (error) {
      root.innerHTML = "";
      msg("admin-npc-message", error.message, "error");
    }
  }

  async function updateAdminNpc(button) {
    const card = button.closest("[data-admin-npc-id]");
    if (!card) return;
    button.disabled = true;
    try {
      await post("/api/admin/npc/status", {
        request_id:Number(card.dataset.adminNpcId),
        status:button.dataset.adminNpcStatus,
        admin_comment:card.querySelector("[data-admin-npc-comment]")?.value.trim() || "",
      });
      tg?.HapticFeedback?.notificationOccurred?.("success");
      await loadAdminNpc();
    } catch (error) {
      msg("admin-npc-message", error.message, "error");
      button.disabled = false;
    }
  }

  async function detectAdmin() {
    if (!initData) return;
    try {
      const data = await post("/api/profile");
      isAdmin = Boolean(data.is_admin);
      if (isAdmin) {
        injectAdminNpc();
        setTimeout(() => {
          // admin_tabs rebuilds a few times during startup and picks up this pane.
          document.dispatchEvent(new CustomEvent("socialadminready"));
        }, 50);
      }
    } catch (_) {}
  }

  document.addEventListener("click", (event) => {
    const respond = event.target.closest?.("[data-coplay-respond]");
    if (respond) {
      respond.disabled = true;
      respondCoplay(respond.dataset.coplayRespond);
      return;
    }
    const close = event.target.closest?.("[data-coplay-close]");
    if (close) {
      close.disabled = true;
      closeCoplay(close.dataset.coplayClose);
      return;
    }
    const cancel = event.target.closest?.("[data-npc-cancel]");
    if (cancel) {
      cancel.disabled = true;
      cancelNpc(cancel.dataset.npcCancel);
      return;
    }
    const adminStatus = event.target.closest?.("[data-admin-npc-status]");
    if (adminStatus) {
      updateAdminNpc(adminStatus);
    }
  });

  injectGameTab();
  detectAdmin();

  // If profile/features are injected slightly later, place the tab correctly once.
  setTimeout(() => {
    const btn = document.querySelector('[data-tab="game"]');
    const profile = document.querySelector('[data-tab="profile"]');
    if (btn && profile && profile.nextElementSibling !== btn) profile.after(btn);
  }, 700);
})();