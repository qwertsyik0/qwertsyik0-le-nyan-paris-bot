const tg = window.Telegram?.WebApp;
const initData = tg?.initData || "";

const links = {
  chat: "https://t.me/+gB1sMZBd5Lo4YjQy",
  citySheet: "https://qwertsyik0.github.io/le-nyan-paris/",
};

let currentUser = null;
let currentApplication = null;
let isAdmin = false;

const form = document.getElementById("application-form");
const submitButton = document.getElementById("submit-button");
const formMessage = document.getElementById("form-message");
const connectionPill = document.getElementById("connection-pill");
const formStatus = document.getElementById("form-status");
const homeStatus = document.getElementById("home-status");
const homeRole = document.getElementById("home-role");
const statusText = document.getElementById("status-text");
const statusDetails = document.getElementById("status-details");
const statusComment = document.getElementById("status-comment");
const adminTabButton = document.getElementById("admin-tab-button");
const refreshAdminButton = document.getElementById("refresh-admin");
const adminMessage = document.getElementById("admin-message");
const adminPendingCount = document.getElementById("admin-pending-count");
const adminAcceptedCount = document.getElementById("admin-accepted-count");
const adminPendingList = document.getElementById("admin-pending-list");
const adminAcceptedList = document.getElementById("admin-accepted-list");

tg?.ready();
tg?.expand();

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function setPill(element, text, type = "muted") {
  element.textContent = text;
  element.className = `pill ${type}`.trim();
}

function setFormMessage(text, type = "") {
  formMessage.textContent = text || "";
  formMessage.className = `message ${type}`.trim();
}

function setAdminMessage(text, type = "") {
  adminMessage.textContent = text || "";
  adminMessage.className = `message ${type}`.trim();
}

function statusLabel(status) {
  const labels = {
    pending: "на рассмотрении",
    accepted: "принята",
    rejected: "отклонена",
    needs_changes: "нужны правки",
  };
  return labels[status] || status || "нет анкеты";
}

function statusType(status) {
  if (status === "accepted") return "ok";
  if (status === "needs_changes") return "warn";
  if (status === "rejected") return "bad";
  if (status === "pending") return "warn";
  return "muted";
}

function characterName(application) {
  if (!application) return "—";
  return `${application.character_first_name || ""} ${application.character_last_name || ""}`.trim() || "—";
}

function assignedRole(application) {
  if (!application) return "роль появится после принятия";
  return application.assigned_role || application.owner_comment || application.role_preference || "роль пока не назначена";
}

function openTab(name) {
  if (name === "guide") {
    window.location.href = "./rules.html?v=20261006-rules-1";
    return;
  }
  document.querySelectorAll(".tab").forEach((button) => {
    button.classList.toggle("active", button.dataset.tab === name);
  });
  document.querySelectorAll(".screen").forEach((screen) => {
    screen.classList.toggle("active", screen.id === `tab-${name}`);
  });
  window.scrollTo({ top: 0, behavior: "smooth" });
  if (name === "admin" && isAdmin) loadAdminOverview();
}

function openLink(name) {
  const url = links[name];
  if (!url) return;
  if (url.startsWith("https://t.me/")) {
    tg?.openTelegramLink?.(url);
    return;
  }
  tg?.openLink?.(url);
}

function fillForm(application) {
  if (!application) return;
  for (const element of form.elements) {
    if (!element.name || !(element.name in application)) continue;
    element.value = application[element.name] ?? "";
  }
}

function renderStatus(application) {
  currentApplication = application;
  const label = statusLabel(application?.status);
  const type = statusType(application?.status);

  homeStatus.textContent = label;
  homeRole.textContent = assignedRole(application);
  statusText.textContent = label;
  setPill(formStatus, label, type);

  if (!application) {
    statusDetails.innerHTML = `
      <div><b>анкета</b><span>пока не отправлена</span></div>
      <div><b>действие</b><span>перейдите в раздел «анкета» и заполните форму</span></div>
    `;
    statusComment.textContent = "";
    submitButton.disabled = false;
    return;
  }

  statusDetails.innerHTML = `
    <div><b>персонаж</b><span>${escapeHtml(characterName(application))}</span></div>
    <div><b>раздел</b><span>${escapeHtml(application.affiliation || "—")}</span></div>
    <div><b>желаемая роль</b><span>${escapeHtml(application.role_preference || "—")}</span></div>
    <div><b>назначенная роль</b><span>${escapeHtml(assignedRole(application))}</span></div>
  `;

  const commentParts = [];
  if (application.owner_comment) commentParts.push(application.owner_comment);
  if (application.status === "pending") commentParts.push("анкета уже ушла администрации. дождитесь решения.");
  if (application.status === "accepted") commentParts.push("вы приняты. переходите в чат события и начинайте игру с подходящей локации.");
  if (application.status === "needs_changes") commentParts.push("откройте раздел анкеты, исправьте данные и отправьте заново.");
  if (application.status === "rejected") commentParts.push("анкета отклонена. при необходимости уточните причину у администрации.");
  statusComment.textContent = commentParts.filter(Boolean).join("\n\n");

  submitButton.disabled = application.status === "accepted";
  if (application.status === "accepted") {
    setFormMessage("анкета уже принята. если нужно что-то изменить, напишите администрации.", "ok");
  }
}

async function api(path, payload) {
  const response = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.detail || "ошибка запроса");
  }
  return data;
}

async function loadMe() {
  if (!initData) {
    setPill(connectionPill, "не Telegram", "bad");
    setFormMessage("откройте Mini App через Telegram-бота, иначе Telegram не передаст данные входа", "error");
    submitButton.disabled = true;
    return;
  }

  try {
    const data = await api("/api/me", { initData });
    currentUser = data.user;
    isAdmin = Boolean(data.is_admin);
    if (data.links) Object.assign(links, data.links);

    setPill(connectionPill, currentUser?.username ? `@${currentUser.username}` : `ID ${currentUser?.id}`, "ok");
    adminTabButton.classList.toggle("hidden", !isAdmin);

    renderStatus(data.application);
    fillForm(data.application);
    if (isAdmin) loadAdminOverview();
  } catch (error) {
    setPill(connectionPill, "ошибка", "bad");
    setFormMessage(error.message, "error");
  }
}

function collectForm() {
  const formData = new FormData(form);
  return Object.fromEntries(formData.entries());
}

function renderAdminList(element, rows, emptyText) {
  if (!rows?.length) {
    element.className = "admin-list empty";
    element.textContent = emptyText;
    return;
  }
  element.className = "admin-list";
  element.innerHTML = rows.map((row) => `
    <div class="admin-item">
      <b>#${escapeHtml(row.id)} — ${escapeHtml(row.character_name || "без имени")}</b>
      <p>игрок: ${escapeHtml(row.username || "без username")}</p>
      <p>роль: ${escapeHtml(row.assigned_role || row.role_preference || "—")}</p>
      <p>раздел: ${escapeHtml(row.affiliation || "—")}</p>
      <p><code>/app ${escapeHtml(row.id)}</code></p>
    </div>
  `).join("");
}

async function loadAdminOverview() {
  if (!isAdmin || !initData) return;
  setAdminMessage("обновляю...");
  try {
    const data = await api("/api/admin/overview", { initData });
    adminPendingCount.textContent = String(data.pending_count ?? data.pending?.length ?? 0);
    adminAcceptedCount.textContent = String(data.accepted_count ?? data.accepted?.length ?? 0);
    renderAdminList(adminPendingList, data.pending, "новых анкет нет");
    renderAdminList(adminAcceptedList, data.accepted, "принятых анкет пока нет");
    setAdminMessage("данные обновлены", "ok");
  } catch (error) {
    setAdminMessage(error.message, "error");
  }
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!initData) {
    setFormMessage("нет данных Telegram. откройте Mini App через бота", "error");
    return;
  }
  submitButton.disabled = true;
  setFormMessage("отправляем анкету...");
  try {
    const data = await api("/api/applications", {
      initData,
      application: collectForm(),
    });
    renderStatus(data.application);
    setFormMessage("анкета отправлена. администрация получила ее в боте.", "ok");
    tg?.HapticFeedback?.notificationOccurred?.("success");
    openTab("status");
  } catch (error) {
    setFormMessage(error.message, "error");
    tg?.HapticFeedback?.notificationOccurred?.("error");
  } finally {
    if (currentApplication?.status !== "accepted") submitButton.disabled = false;
  }
});

document.querySelectorAll(".tab").forEach((button) => {
  button.addEventListener("click", () => openTab(button.dataset.tab));
});

document.querySelectorAll("[data-open-tab]").forEach((button) => {
  button.addEventListener("click", () => openTab(button.dataset.openTab));
});

document.querySelectorAll("[data-open-link]").forEach((button) => {
  button.addEventListener("click", () => openLink(button.dataset.openLink));
});

refreshAdminButton?.addEventListener("click", loadAdminOverview);

loadMe();
