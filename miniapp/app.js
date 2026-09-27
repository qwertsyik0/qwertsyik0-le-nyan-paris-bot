const tg = window.Telegram?.WebApp;
const initData = tg?.initData || "";

const form = document.getElementById("application-form");
const submitButton = document.getElementById("submit-button");
const formMessage = document.getElementById("form-message");
const statusCard = document.getElementById("status-card");
const statusText = document.getElementById("status-text");
const statusComment = document.getElementById("status-comment");

tg?.ready();
tg?.expand();

function setMessage(text, type = "") {
  formMessage.textContent = text || "";
  formMessage.className = `form-message ${type}`.trim();
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

function fillForm(application) {
  if (!application) return;
  for (const element of form.elements) {
    if (!element.name || !(element.name in application)) continue;
    element.value = application[element.name] ?? "";
  }
}

function renderStatus(application) {
  if (!application) {
    statusCard.classList.add("hidden");
    return;
  }
  statusCard.classList.remove("hidden");
  statusText.textContent = statusLabel(application.status);
  const chunks = [];
  if (application.assigned_role) chunks.push(`назначенная роль: ${application.assigned_role}`);
  if (application.owner_comment) chunks.push(application.owner_comment);
  statusComment.textContent = chunks.join("\n\n");
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
    setMessage("откройте анкету через Telegram-бота, иначе Telegram не передаст данные входа", "error");
    submitButton.disabled = true;
    return;
  }
  try {
    const data = await api("/api/me", { initData });
    renderStatus(data.application);
    fillForm(data.application);
    if (data.application?.status === "accepted") {
      submitButton.disabled = true;
      setMessage("анкета уже принята. если нужно что-то изменить, напишите администрации.", "ok");
    }
  } catch (error) {
    setMessage(error.message, "error");
  }
}

function collectForm() {
  const formData = new FormData(form);
  return Object.fromEntries(formData.entries());
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!initData) {
    setMessage("нет данных Telegram. откройте Mini App через бота", "error");
    return;
  }
  submitButton.disabled = true;
  setMessage("отправляем анкету...");
  try {
    const data = await api("/api/applications", {
      initData,
      application: collectForm(),
    });
    renderStatus(data.application);
    setMessage("анкета отправлена. администрация получила ее в боте.", "ok");
    tg?.HapticFeedback?.notificationOccurred?.("success");
  } catch (error) {
    setMessage(error.message, "error");
    tg?.HapticFeedback?.notificationOccurred?.("error");
  } finally {
    submitButton.disabled = false;
  }
});

loadMe();
