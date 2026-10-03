(() => {
  const tg = window.Telegram?.WebApp;
  const initData = tg?.initData || "";

  let cachedWarnings = [];
  let warningsLoaded = false;
  let adminInjected = false;

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

  function formatDate(value) {
    if (!value) return "дата не указана";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return String(value);
    return date.toLocaleString("ru-RU", { dateStyle: "short", timeStyle: "short" });
  }

  function injectStyle() {
    if (document.getElementById("warnings-style")) return;
    const style = document.createElement("style");
    style.id = "warnings-style";
    style.textContent = `
      .warnings-banner{border:1px solid rgba(120,43,36,.24);border-radius:18px;padding:14px;background:rgba(255,238,231,.75);display:grid;gap:12px}.warnings-banner header{display:flex;justify-content:space-between;gap:12px;align-items:flex-start}.warnings-banner .warning-title{font-size:12px;letter-spacing:.1em;text-transform:uppercase;color:#8b2f26;font-weight:800}.warnings-banner strong{display:block;margin-top:4px}.warnings-list{display:grid;gap:10px;margin-top:4px}.warnings-list.hidden{display:none}.warning-item{border:1px solid rgba(120,43,36,.18);border-radius:14px;padding:12px;background:rgba(255,255,255,.58)}.warning-item header{display:flex;justify-content:space-between;gap:10px;align-items:flex-start}.warning-item b{text-transform:lowercase}.warning-item p{white-space:pre-wrap}.warning-meta{font-size:12px;opacity:.72}.admin-warning-form{display:grid;gap:10px}.admin-warning-form input,.admin-warning-form select,.admin-warning-form textarea{width:100%;box-sizing:border-box;border-radius:12px;border:1px solid rgba(88,62,33,.18);padding:10px;background:rgba(255,255,255,.72);color:inherit}.admin-warning-form textarea{min-height:110px}.warning-admin-row{display:grid;grid-template-columns:1fr 1fr;gap:10px}@media(max-width:620px){.warning-admin-row{grid-template-columns:1fr}.warnings-banner header{display:grid}}
    `;
    document.head.appendChild(style);
  }

  function renderWarningsCard() {
    const box = document.getElementById("profile-content");
    if (!box) return;

    let card = document.getElementById("profile-warnings-card");
    if (!card) {
      card = document.createElement("div");
      card.id = "profile-warnings-card";
      card.className = "warnings-banner";
      box.appendChild(card);
    }

    const count = cachedWarnings.length;
    const listHtml = count
      ? cachedWarnings.map((item) => `
          <div class="warning-item">
            <header>
              <b>${esc(item.type_label || item.type || "предупреждение")}</b>
              <span class="warning-meta">${esc(formatDate(item.created_at))}</span>
            </header>
            <p>${esc(item.reason || "без причины")}</p>
            <div class="warning-meta">выдал: ${esc(item.admin || "администрация")}</div>
          </div>
        `).join("")
      : `<div class="warning-item">предупреждений нет.</div>`;

    card.innerHTML = `
      <header>
        <div>
          <span class="warning-title">ПРЕДУПРЕЖДЕНИЯ</span>
          <strong>${count ? `активных записей: ${count}` : "активных записей нет"}</strong>
        </div>
        <button id="warnings-toggle" class="small" type="button">посмотреть</button>
      </header>
      <div id="warnings-list" class="warnings-list hidden">${listHtml}</div>
    `;

    document.getElementById("warnings-toggle")?.addEventListener("click", () => {
      document.getElementById("warnings-list")?.classList.toggle("hidden");
    });
  }

  async function loadWarnings() {
    if (!initData) return;
    try {
      const data = await post("/api/warnings");
      cachedWarnings = Array.isArray(data.warnings) ? data.warnings : [];
      warningsLoaded = true;
      renderWarningsCard();
    } catch (error) {
      cachedWarnings = [];
      warningsLoaded = true;
      renderWarningsCard();
      console.warn("warnings load skipped", error);
    }
  }

  function injectAdminWarnings() {
    const admin = document.getElementById("tab-admin");
    if (!admin || adminInjected || document.getElementById("admin-warning-card")) return;
    adminInjected = true;
    admin.insertAdjacentHTML("beforeend", `
      <article class="card" id="admin-warning-card" data-admin-pane="warnings">
        <p class="eyebrow">дисциплина</p>
        <h2>предупреждения игрокам</h2>
        <div class="admin-warning-form">
          <div class="warning-admin-row">
            <input id="warning-target" placeholder="@username или Telegram ID">
            <select id="warning-type">
              <option value="oral">устное замечание</option>
              <option value="remark">замечание</option>
              <option value="warning">предупреждение</option>
              <option value="reprimand">выговор</option>
            </select>
          </div>
          <textarea id="warning-reason" placeholder="причина: что нарушил, когда, коротко и по делу"></textarea>
          <button id="warning-send" class="primary wide" type="button">выдать предупреждение</button>
          <div id="warning-admin-message" class="message"></div>
        </div>
      </article>
    `);
    document.getElementById("warning-send")?.addEventListener("click", sendWarning);
  }

  async function sendWarning() {
    const msg = document.getElementById("warning-admin-message");
    const target = document.getElementById("warning-target")?.value.trim() || "";
    const warningType = document.getElementById("warning-type")?.value || "warning";
    const reason = document.getElementById("warning-reason")?.value.trim() || "";
    if (!msg) return;
    if (!target || !reason) {
      msg.textContent = "укажите игрока и причину";
      msg.className = "message error";
      return;
    }
    msg.textContent = "выдаю...";
    msg.className = "message";
    try {
      const data = await post("/api/admin/warnings/create", {
        target,
        warning_type: warningType,
        reason,
      });
      msg.textContent = `готово: ${data.warning?.type_label || "предупреждение"} для ${data.target || target}${data.notify_ok ? "" : " | уведомление не отправилось"}`;
      msg.className = "message ok";
      const reasonField = document.getElementById("warning-reason");
      if (reasonField) reasonField.value = "";
    } catch (error) {
      msg.textContent = error.message;
      msg.className = "message error";
    }
  }

  function init() {
    injectStyle();
    injectAdminWarnings();
    renderWarningsCard();
    if (!warningsLoaded) loadWarnings();

    const profile = document.getElementById("profile-content");
    if (profile && !profile.dataset.warningsObserved) {
      profile.dataset.warningsObserved = "1";
      const observer = new MutationObserver(() => {
        if (!document.getElementById("profile-warnings-card")) renderWarningsCard();
      });
      observer.observe(profile, { childList: true });
    }
  }

  document.addEventListener("click", (event) => {
    const tab = event.target.closest?.('[data-tab="profile"], [data-tab="admin"]');
    if (tab) setTimeout(init, 350);
  });

  setTimeout(init, 600);
  setTimeout(init, 1400);
})();
