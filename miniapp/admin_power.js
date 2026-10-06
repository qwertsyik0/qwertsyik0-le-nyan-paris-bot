(() => {
  const API_BASE = "https://le-nyan-paris-bot.onrender.com";
  const tg = window.Telegram?.WebApp;
  const initData = tg?.initData || "";
  const admin = document.getElementById("tab-admin");
  if (!admin || document.getElementById("power-player-console")) return;

  let searchResults = [];
  let currentPlayerId = null;
  let currentPlayer = null;
  let selectedIds = new Set();
  let dashboardCounts = {};

  const statusLabels = {
    active: "активен",
    low_activity: "малоактив",
    frozen: "заморожен",
    left: "выбыл",
    watch: "под наблюдением",
  };
  const affiliations = ["двор","суд","полиция","армия","пресса","медицина","церковь","город","подполье","рынок"];
  const actionLabels = {
    player_profile_updated: "изменены данные игрока",
    player_status_left: "игрок выбыл",
    player_status_frozen: "игрок заморожен",
    player_status_restore: "игрок восстановлен",
    player_status_active: "статус: активен",
    player_status_watch: "игрок под наблюдением",
    player_status_low_activity: "статус: малоактив",
    admin_note_added: "добавлена заметка",
    admin_note_deleted: "удалена заметка",
    warning_created: "выдано предупреждение",
    targeted_bulk_letter_sent: "выборочная рассылка",
    letter_sent_from_template: "письмо по шаблону",
  };

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
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return String(value);
    return date.toLocaleString("ru-RU", {day:"2-digit",month:"2-digit",year:"numeric",hour:"2-digit",minute:"2-digit"});
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

  function setMsg(id, text, type = "") {
    const el = document.getElementById(id);
    if (!el) return;
    el.textContent = text || "";
    el.className = "message " + type;
  }

  function switchAdminSection(name) {
    const button = document.querySelector(`#admin-section-tabs [data-admin-section="${name}"]`);
    if (button) {
      button.click();
      return;
    }
    setTimeout(() => document.querySelector(`#admin-section-tabs [data-admin-section="${name}"]`)?.click(), 120);
  }

  function injectPanels() {
    const overview = document.createElement("article");
    overview.id = "power-global-search";
    overview.className = "card power-card";
    overview.dataset.adminPane = "overview";
    overview.innerHTML = `
      <p class="eyebrow">быстрый доступ</p>
      <h2>поиск по админке</h2>
      <div class="power-search-row">
        <input id="power-global-query" placeholder="@username, имя, роль или Telegram ID">
        <button id="power-global-search-btn" class="primary" type="button">найти</button>
      </div>
      <div id="power-global-results" class="power-results"></div>
    `;
    admin.appendChild(overview);

    const players = document.createElement("article");
    players.id = "power-player-console";
    players.className = "card power-card";
    players.dataset.adminPane = "players";
    players.innerHTML = `
      <div class="section-head">
        <div><p class="eyebrow">управление участниками</p><h2>игроки</h2></div>
        <button id="power-players-refresh" class="small" type="button">обновить</button>
      </div>
      <div class="power-filter-grid">
        <input id="power-player-search" placeholder="@username, имя, роль или ID">
        <select id="power-player-affiliation"><option value="">все разделы</option>${affiliations.map(x=>`<option>${x}</option>`).join("")}</select>
        <select id="power-player-status">
          <option value="">все статусы</option>
          ${Object.entries(statusLabels).map(([v,l])=>`<option value="${v}">${l}</option>`).join("")}
        </select>
      </div>
      <div class="power-selection-bar">
        <b>выбрано: <span id="power-selected-count">0</span></b>
        <button id="power-selection-clear" class="small" type="button">сбросить</button>
        <button id="power-selection-letter" class="small" type="button">написать выбранным</button>
      </div>
      <div id="power-players-message" class="message"></div>
      <div id="power-player-list" class="power-player-list"></div>
      <div id="power-player-detail" class="power-detail hidden"></div>
    `;
    admin.appendChild(players);

    const notes = document.createElement("article");
    notes.id = "power-notes-console";
    notes.className = "card power-card";
    notes.dataset.adminPane = "notes";
    notes.innerHTML = `
      <p class="eyebrow">внутренние записи</p><h2>заметки игрока</h2>
      <div class="power-search-row">
        <input id="power-note-target" placeholder="@username или Telegram ID">
        <button id="power-note-load" class="small" type="button">открыть</button>
      </div>
      <div id="power-note-player" class="power-mini-player"></div>
      <textarea id="power-note-body" placeholder="видно только администрации"></textarea>
      <button id="power-note-add" class="primary wide" type="button">добавить заметку</button>
      <div id="power-note-message" class="message"></div>
      <div id="power-note-list" class="power-note-list"></div>
    `;
    admin.appendChild(notes);

    const letters = document.createElement("article");
    letters.id = "power-targeted-mail";
    letters.className = "card power-card";
    letters.dataset.adminPane = "letters";
    letters.innerHTML = `
      <p class="eyebrow">точечная рассылка</p><h2>выбранным игрокам</h2>
      <p>получателей: <b id="power-mail-count">0</b></p>
      <div id="power-mail-recipients" class="power-chips"></div>
      <div class="grid two">
        <select id="power-mail-type">
          <option value="letter">письмо</option><option value="summons">повестка</option>
          <option value="task">задание</option><option value="rumor">слух</option><option value="warning">предупреждение</option>
        </select>
        <input id="power-mail-title" placeholder="заголовок">
      </div>
      <textarea id="power-mail-body" placeholder="текст сообщения"></textarea>
      <button id="power-mail-send" class="primary wide" type="button">отправить выбранным</button>
      <div id="power-mail-message" class="message"></div>
    `;
    admin.appendChild(letters);

    const templates = document.createElement("article");
    templates.id = "power-template-favorites";
    templates.className = "card power-card";
    templates.dataset.adminPane = "templates";
    templates.innerHTML = `
      <div class="section-head"><div><p class="eyebrow">быстрые шаблоны</p><h2>избранное</h2></div><button id="power-template-refresh" class="small" type="button">обновить</button></div>
      <p>звездой можно закрепить часто используемые шаблоны наверху.</p>
      <div id="power-template-message" class="message"></div>
      <div id="power-template-list" class="power-template-list"></div>
    `;
    admin.appendChild(templates);

    hideLegacy();
  }

  function hideLegacy() {
    document.getElementById("feature-admin-players")?.classList.add("power-replaced");
    document.querySelector("#admin-ext-panel > [data-admin-pane='players']")?.classList.add("power-replaced");
    document.querySelector("#admin-ext-panel > [data-admin-pane='notes']")?.classList.add("power-replaced");
  }

  function injectConfirm() {
    if (document.getElementById("power-confirm")) return;
    const modal = document.createElement("div");
    modal.id = "power-confirm";
    modal.className = "power-confirm hidden";
    modal.innerHTML = `
      <div class="power-confirm-box">
        <p class="eyebrow">подтверждение</p>
        <h3 id="power-confirm-title">подтвердить действие?</h3>
        <p id="power-confirm-text"></p>
        <div class="feature-actions">
          <button id="power-confirm-cancel" class="ghost" type="button">отмена</button>
          <button id="power-confirm-ok" class="primary" type="button">подтвердить</button>
        </div>
      </div>
    `;
    document.body.appendChild(modal);
  }

  function askConfirm(title, text) {
    injectConfirm();
    return new Promise((resolve) => {
      const modal = document.getElementById("power-confirm");
      modal.querySelector("#power-confirm-title").textContent = title;
      modal.querySelector("#power-confirm-text").textContent = text;
      modal.classList.remove("hidden");
      const finish = (value) => {
        modal.classList.add("hidden");
        modal.querySelector("#power-confirm-ok").onclick = null;
        modal.querySelector("#power-confirm-cancel").onclick = null;
        resolve(value);
      };
      modal.querySelector("#power-confirm-ok").onclick = () => finish(true);
      modal.querySelector("#power-confirm-cancel").onclick = () => finish(false);
    });
  }

  async function loadDashboard() {
    if (!initData) return;
    try {
      const data = await post("/api/admin/dashboard");
      dashboardCounts = data.counts || {};
      decorateTabCounts();
    } catch (_) {}
  }

  function decorateTabCounts() {
    const map = {
      applications: "pending",
      players: "players",
      notes: "notes",
      letters: "letters",
      templates: "templates",
      warnings: "warnings",
      logs: "logs",
    };
    document.querySelectorAll("#admin-section-tabs [data-admin-section]").forEach((btn) => {
      const key = map[btn.dataset.adminSection];
      btn.querySelector(".power-count")?.remove();
      if (!key) return;
      const badge = document.createElement("span");
      badge.className = "power-count";
      badge.textContent = String(dashboardCounts[key] ?? 0);
      btn.appendChild(badge);
    });
  }

  function resultRow(p, compact = false) {
    const canSelect = p.application_status === "accepted" && p.player_status !== "left";
    return `
      <div class="power-player-row" data-power-player="${esc(p.telegram_id)}">
        ${compact ? "" : `<label class="power-check"><input type="checkbox" data-power-select="${esc(p.telegram_id)}" ${selectedIds.has(Number(p.telegram_id)) ? "checked" : ""} ${canSelect ? "" : "disabled"}><span></span></label>`}
        <button class="power-player-open" type="button" data-power-open="${esc(p.telegram_id)}">
          <b>${esc(p.character_name)}</b>
          <span>${esc(p.username || "без username")} · ${esc(p.assigned_role || "без роли")}</span>
          <small>${esc(p.affiliation || "—")} · ${esc(p.player_status_label || p.player_status)}${p.application_status !== "accepted" ? " · анкета: " + esc(p.application_status) : ""}</small>
        </button>
        <div class="power-row-counts"><span>✉ ${esc(p.letters_count || 0)}</span><span>📝 ${esc(p.notes_count || 0)}</span><span>⚠ ${esc(p.warnings_count || 0)}</span></div>
      </div>
    `;
  }

  async function searchPlayers({global = false} = {}) {
    if (!initData) return;
    const search = global ? document.getElementById("power-global-query")?.value || "" : document.getElementById("power-player-search")?.value || "";
    const affiliation = global ? "" : document.getElementById("power-player-affiliation")?.value || "";
    const player_status = global ? "" : document.getElementById("power-player-status")?.value || "";
    const target = global ? document.getElementById("power-global-results") : document.getElementById("power-player-list");
    if (!target) return;
    if (!global) setMsg("power-players-message", "загрузка...");
    try {
      const data = await post("/api/admin/search", {search, affiliation, player_status});
      if (!global) searchResults = data.players || [];
      const rows = global ? (data.players || []).slice(0, 12) : (data.players || []);
      target.innerHTML = rows.length ? rows.map(p=>resultRow(p, global)).join("") : '<div class="empty">ничего не найдено</div>';
      if (!global) setMsg("power-players-message", `найдено: ${rows.length}`);
    } catch (error) {
      target.innerHTML = "";
      if (!global) setMsg("power-players-message", error.message, "error");
      else target.textContent = error.message;
    }
  }

  function updateSelectionUI() {
    const ids = [...selectedIds];
    document.getElementById("power-selected-count").textContent = String(ids.length);
    document.getElementById("power-mail-count").textContent = String(ids.length);
    const names = ids.map((id) => searchResults.find(p=>Number(p.telegram_id)===id)).filter(Boolean);
    document.getElementById("power-mail-recipients").innerHTML = names.length
      ? names.map(p=>`<span>${esc(p.character_name)}</span>`).join("")
      : '<span class="muted">никто не выбран</span>';
  }

  async function openPlayer(identifier) {
    if (!identifier) return;
    setMsg("power-players-message", "открываю карточку...");
    try {
      const data = await post("/api/admin/player/power", {identifier:String(identifier)});
      currentPlayer = data;
      currentPlayerId = Number(data.player.telegram_id);
      renderPlayerDetail(data);
      syncNotes(data);
      setMsg("power-players-message", "");
    } catch (error) {
      setMsg("power-players-message", error.message, "error");
    }
  }

  function renderPlayerDetail(data) {
    const p = data.player;
    const detail = document.getElementById("power-player-detail");
    if (!detail) return;
    const index = searchResults.findIndex(x=>Number(x.telegram_id)===Number(p.telegram_id));
    detail.classList.remove("hidden");
    detail.innerHTML = `
      <div class="power-detail-head">
        <div>
          <p class="eyebrow">карточка игрока</p>
          <h2>${esc(p.character_name)}</h2>
          <p>${esc(p.username || "без username")} · <code>${esc(p.telegram_id)}</code></p>
        </div>
        <button class="small" type="button" data-power-close-card>закрыть</button>
      </div>

      <div class="power-prevnext">
        <button class="small" type="button" data-power-prev ${index <= 0 ? "disabled" : ""}>← предыдущий</button>
        <span>${index >= 0 ? index + 1 : "—"} / ${searchResults.length || "—"}</span>
        <button class="small" type="button" data-power-next ${index < 0 || index >= searchResults.length - 1 ? "disabled" : ""}>следующий →</button>
      </div>

      <div class="power-kv">
        <div><b>анкета</b><span>${esc(p.application_status)}</span></div>
        <div><b>статус</b><span>${esc(p.player_status_label)}</span></div>
        <div><b>причина</b><span>${esc(p.status_reason || "—")}</span></div>
      </div>

      <div class="power-edit-grid">
        <label><span>роль</span><input id="power-edit-role" value="${esc(p.assigned_role || "")}"></label>
        <label><span>раздел</span><select id="power-edit-affiliation">${affiliations.map(x=>`<option ${x===p.affiliation?"selected":""}>${x}</option>`).join("")}</select></label>
        <label class="power-wide"><span>сюжетные метки</span><input id="power-edit-tags" value="${esc((p.story_tags || []).join(", "))}" placeholder="через запятую"></label>
      </div>
      <button class="primary wide" type="button" data-power-save-player>сохранить роль и раздел</button>

      <div class="power-status-actions">
        <button class="small" data-power-status="active" type="button">активен</button>
        <button class="small" data-power-status="low_activity" type="button">малоактив</button>
        <button class="small" data-power-status="watch" type="button">под наблюдением</button>
        <button class="small" data-power-status="frozen" type="button">заморозить</button>
        <button class="small danger" data-power-status="left" type="button">вышел по с/ж</button>
        <button class="small" data-power-status="restore" type="button">восстановить</button>
      </div>

      <div class="power-quick-actions">
        <button class="small" type="button" data-power-quick="letter">✉ письмо</button>
        <button class="small" type="button" data-power-quick="warning">⚠ предупреждение</button>
        <button class="small" type="button" data-power-quick="note">📝 заметка</button>
      </div>

      <details class="power-history" open>
        <summary>история игрока · ${(data.timeline || []).length}</summary>
        <div class="power-timeline">
          ${(data.timeline || []).slice(0,80).map(item=>`
            <div class="power-timeline-item">
              <b>${esc(actionLabels[item.title] || item.title)}</b>
              ${item.text ? `<p>${esc(item.text)}</p>` : ""}
              <small>${esc(fmt(item.created_at))}</small>
            </div>
          `).join("") || '<div class="empty">история пока пустая</div>'}
        </div>
      </details>
    `;
    detail.scrollIntoView({behavior:"smooth",block:"start"});
  }

  async function savePlayerDetail() {
    if (!currentPlayerId) return;
    try {
      await post("/api/admin/player/edit", {
        identifier:String(currentPlayerId),
        assigned_role:document.getElementById("power-edit-role")?.value || "",
        affiliation:document.getElementById("power-edit-affiliation")?.value || "",
        story_tags:document.getElementById("power-edit-tags")?.value || "",
      });
      tg?.HapticFeedback?.notificationOccurred?.("success");
      await searchPlayers();
      await openPlayer(currentPlayerId);
      await loadDashboard();
    } catch (error) {
      setMsg("power-players-message", error.message, "error");
    }
  }

  async function changeStatus(action) {
    if (!currentPlayerId) return;
    const dangerous = action === "left" || action === "restore" || action === "frozen";
    if (dangerous) {
      const label = action === "left" ? "отметить игрока как вышедшего по с/ж?" : action === "restore" ? "восстановить игрока?" : "заморозить игрока?";
      const ok = await askConfirm("изменение статуса", label + " действие сразу изменит его состояние в реестре.");
      if (!ok) return;
    }
    let reason = "";
    if (action === "left") reason = "вышел по с/ж";
    try {
      await post("/api/admin/player/status", {identifier:String(currentPlayerId), action, reason});
      tg?.HapticFeedback?.notificationOccurred?.("success");
      if (action === "left") selectedIds.delete(currentPlayerId);
      await searchPlayers();
      await openPlayer(currentPlayerId);
      updateSelectionUI();
      await loadDashboard();
    } catch (error) {
      setMsg("power-players-message", error.message, "error");
    }
  }

  function quickAction(kind) {
    if (!currentPlayer) return;
    const p = currentPlayer.player;
    if (kind === "letter") {
      selectedIds.add(Number(p.telegram_id));
      updateSelectionUI();
      switchAdminSection("letters");
      document.getElementById("power-mail-body")?.focus();
    } else if (kind === "warning") {
      switchAdminSection("warnings");
      setTimeout(() => {
        const input = document.querySelector("#admin-warning-target, #warning-target, #hard-warning-target");
        if (input) input.value = p.username || String(p.telegram_id);
      }, 100);
    } else if (kind === "note") {
      document.getElementById("power-note-target").value = p.username || String(p.telegram_id);
      syncNotes(currentPlayer);
      switchAdminSection("notes");
      setTimeout(()=>document.getElementById("power-note-body")?.focus(),100);
    }
  }

  function syncNotes(data) {
    if (!data?.player) return;
    const p = data.player;
    const target = document.getElementById("power-note-target");
    if (target) target.value = p.username || String(p.telegram_id);
    document.getElementById("power-note-player").innerHTML = `<b>${esc(p.character_name)}</b><span>${esc(p.username || p.telegram_id)}</span>`;
    document.getElementById("power-note-list").innerHTML = (data.notes || []).length
      ? data.notes.map(n=>`<div class="power-note"><p>${esc(n.body)}</p><small>${esc(fmt(n.created_at))}</small></div>`).join("")
      : '<div class="empty">заметок нет</div>';
  }

  async function loadNotesTarget() {
    const id = document.getElementById("power-note-target")?.value.trim();
    if (!id) return setMsg("power-note-message","укажи игрока","error");
    try {
      const data = await post("/api/admin/player/power",{identifier:id});
      currentPlayer = data;
      currentPlayerId = Number(data.player.telegram_id);
      syncNotes(data);
      setMsg("power-note-message","");
    } catch(error){setMsg("power-note-message",error.message,"error");}
  }

  async function addNote() {
    const target = document.getElementById("power-note-target")?.value.trim();
    const body = document.getElementById("power-note-body")?.value.trim();
    if (!target || !body) return setMsg("power-note-message","укажи игрока и текст заметки","error");
    try {
      await post("/api/admin/notes/add",{identifier:target,body});
      document.getElementById("power-note-body").value="";
      await loadNotesTarget();
      await loadDashboard();
      setMsg("power-note-message","заметка добавлена","success");
    } catch(error){setMsg("power-note-message",error.message,"error");}
  }

  async function sendSelected() {
    const ids=[...selectedIds];
    if (!ids.length) return setMsg("power-mail-message","сначала выбери игроков","error");
    const body=document.getElementById("power-mail-body")?.value.trim();
    if (!body) return setMsg("power-mail-message","введи текст сообщения","error");
    const ok=await askConfirm("массовая отправка",`отправить сообщение ${ids.length} выбранным игрокам? отменить отправленные сообщения уже нельзя.`);
    if(!ok)return;
    setMsg("power-mail-message","отправляю...");
    try{
      const data=await post("/api/admin/letters/targets",{
        target_ids:ids,
        letter_type:document.getElementById("power-mail-type")?.value||"letter",
        title:document.getElementById("power-mail-title")?.value||"",
        body,
      });
      setMsg("power-mail-message",`готово: создано ${data.letters_created}, уведомлено ${data.notified}, ошибок ${data.notify_failed}`,"success");
      await loadDashboard();
    }catch(error){setMsg("power-mail-message",error.message,"error");}
  }

  async function loadTemplates() {
    const root=document.getElementById("power-template-list");
    if(!root)return;
    setMsg("power-template-message","загрузка...");
    try{
      const data=await post("/api/admin/templates/power");
      const templates=data.templates||[];
      root.innerHTML=templates.length?templates.map(t=>`
        <div class="power-template ${t.is_favorite?"favorite":""}">
          <button class="power-star" type="button" data-power-template-star="${esc(t.id)}" aria-label="избранное">${t.is_favorite?"★":"☆"}</button>
          <div><b>${esc(t.name)}</b><small>${esc(t.letter_type)} · ${esc(t.title)}</small><p>${esc(t.body)}</p></div>
          <button class="small" type="button" data-power-template-use="${esc(t.id)}"
            data-title="${esc(t.title)}" data-type="${esc(t.letter_type)}" data-body="${esc(t.body)}">использовать</button>
        </div>
      `).join(""):'<div class="empty">шаблонов нет</div>';
      setMsg("power-template-message",`избранных: ${templates.filter(t=>t.is_favorite).length}`);
    }catch(error){setMsg("power-template-message",error.message,"error");}
  }

  async function toggleTemplate(id){
    try{await post("/api/admin/templates/favorite",{template_id:Number(id)});await loadTemplates();await loadDashboard();}
    catch(error){setMsg("power-template-message",error.message,"error");}
  }

  function useTemplate(button){
    document.getElementById("power-mail-type").value=button.dataset.type||"letter";
    document.getElementById("power-mail-title").value=button.dataset.title||"";
    document.getElementById("power-mail-body").value=button.dataset.body||"";
    switchAdminSection("letters");
  }

  function wire() {
    document.getElementById("power-global-search-btn")?.addEventListener("click",()=>searchPlayers({global:true}));
    document.getElementById("power-global-query")?.addEventListener("keydown",(e)=>{if(e.key==="Enter")searchPlayers({global:true});});
    document.getElementById("power-players-refresh")?.addEventListener("click",()=>searchPlayers());
    document.getElementById("power-player-search")?.addEventListener("input",()=>{clearTimeout(window.__powerSearchTimer);window.__powerSearchTimer=setTimeout(()=>searchPlayers(),180);});
    document.getElementById("power-player-affiliation")?.addEventListener("change",()=>searchPlayers());
    document.getElementById("power-player-status")?.addEventListener("change",()=>searchPlayers());
    document.getElementById("power-selection-clear")?.addEventListener("click",()=>{selectedIds.clear();document.querySelectorAll("[data-power-select]").forEach(x=>x.checked=false);updateSelectionUI();});
    document.getElementById("power-selection-letter")?.addEventListener("click",()=>switchAdminSection("letters"));
    document.getElementById("power-note-load")?.addEventListener("click",loadNotesTarget);
    document.getElementById("power-note-add")?.addEventListener("click",addNote);
    document.getElementById("power-mail-send")?.addEventListener("click",sendSelected);
    document.getElementById("power-template-refresh")?.addEventListener("click",loadTemplates);

    document.addEventListener("change",(event)=>{
      const box=event.target.closest?.("[data-power-select]");
      if(!box)return;
      const id=Number(box.dataset.powerSelect);
      if(box.checked)selectedIds.add(id);else selectedIds.delete(id);
      updateSelectionUI();
    });

    document.addEventListener("click",async(event)=>{
      const open=event.target.closest?.("[data-power-open]");
      if(open){switchAdminSection("players");await openPlayer(open.dataset.powerOpen);return;}
      if(event.target.closest?.("[data-power-close-card]")){document.getElementById("power-player-detail")?.classList.add("hidden");return;}
      if(event.target.closest?.("[data-power-save-player]")){await savePlayerDetail();return;}
      const status=event.target.closest?.("[data-power-status]");
      if(status){await changeStatus(status.dataset.powerStatus);return;}
      const quick=event.target.closest?.("[data-power-quick]");
      if(quick){quickAction(quick.dataset.powerQuick);return;}
      if(event.target.closest?.("[data-power-prev]")){
        const i=searchResults.findIndex(x=>Number(x.telegram_id)===currentPlayerId);if(i>0)await openPlayer(searchResults[i-1].telegram_id);return;
      }
      if(event.target.closest?.("[data-power-next]")){
        const i=searchResults.findIndex(x=>Number(x.telegram_id)===currentPlayerId);if(i>=0&&i<searchResults.length-1)await openPlayer(searchResults[i+1].telegram_id);return;
      }
      const star=event.target.closest?.("[data-power-template-star]");
      if(star){await toggleTemplate(star.dataset.powerTemplateStar);return;}
      const use=event.target.closest?.("[data-power-template-use]");
      if(use){useTemplate(use);return;}
    });
  }

  function boot() {
    injectPanels();
    wire();
    updateSelectionUI();
    setTimeout(()=>searchPlayers(),350);
    setTimeout(()=>searchPlayers({global:true}),500);
    setTimeout(()=>loadTemplates(),650);
    setTimeout(()=>loadDashboard(),750);
    setTimeout(()=>loadDashboard(),2800);
    setTimeout(hideLegacy, 500);
    setTimeout(hideLegacy, 1400);
    setTimeout(hideLegacy, 3000);
    // Счётчики обновляются только после реальной загрузки данных.
    // Постоянный interval здесь ломал фокус у select и вызывал мерцание.
  }

  boot();
})();