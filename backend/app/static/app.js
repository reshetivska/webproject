"use strict";

let token = localStorage.getItem("token");
let limits = null;
let currentView = "tasks";
let pickedImage = null;
let registerMode = false;
let busy = false;

const $ = (id) => document.getElementById(id);

function h(tag, props, ...children) {
  const el = document.createElement(tag);
  for (const [key, value] of Object.entries(props || {})) {
    if (key === "class") el.className = value;
    else if (key.startsWith("on")) el.addEventListener(key.slice(2), value);
    else el.setAttribute(key, value);
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    el.append(child.nodeType ? child : document.createTextNode(String(child)));
  }
  return el;
}

async function api(path, options = {}) {
  options.headers = options.headers || {};
  if (token) options.headers["Authorization"] = "Bearer " + token;
  const resp = await fetch("/api" + path, options);

  const server = resp.headers.get("X-Served-By");
  if (server) $("served-by").textContent = server;

  if (resp.status === 401 && token) {
    doLogout();
    throw new Error("Сесія закінчилась, увійдіть знову");
  }
  if (!resp.ok) {
    let message = "Помилка " + resp.status;
    try {
      const body = await resp.json();
      message = typeof body.detail === "string" ? body.detail : "Некоректні дані (перевірте логін і пароль)";
    } catch (e) {  }
    throw new Error(message);
  }
  return resp;
}


function setRegisterMode(on) {
  registerMode = on;
  $("tab-login").classList.toggle("active", !on);
  $("tab-register").classList.toggle("active", on);
  $("auth-submit").textContent = on ? "Зареєструватись" : "Увійти";
  $("auth-password").autocomplete = on ? "new-password" : "current-password";
  $("auth-error").textContent = "";
}
$("tab-login").onclick = () => setRegisterMode(false);
$("tab-register").onclick = () => setRegisterMode(true);

$("auth-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  $("auth-error").textContent = "";
  try {
    const resp = await api(registerMode ? "/register" : "/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username: $("auth-username").value.trim(), password: $("auth-password").value }),
    });
    token = (await resp.json()).token;
    localStorage.setItem("token", token);
    $("auth-password").value = "";
    await startApp();
  } catch (err) {
    $("auth-error").textContent = err.message;
  }
});

async function doLogout() {
  const oldToken = token;
  token = null;
  localStorage.removeItem("token");
  if (oldToken) fetch("/api/logout", { method: "POST", headers: { Authorization: "Bearer " + oldToken } });
  $("app-section").hidden = true;
  $("userbox").hidden = true;
  $("auth-section").hidden = false;
}
$("logout-btn").onclick = doLogout;

async function startApp() {
  const me = await (await api("/me")).json();
  limits = await (await api("/limits")).json();
  $("username").textContent = me.username;
  $("admin-tab").hidden = !me.is_admin;
  $("radius").max = limits.max_radius;
  $("limits-info").textContent =
    "Ліміти: до " + limits.max_active + " одночасних задач, до " + limits.max_tasks + " задач в історії; " +
    "зображення до " + limits.max_side + "×" + limits.max_side + " px і " + limits.max_file_mb + " МБ.";
  $("auth-section").hidden = true;
  $("app-section").hidden = false;
  $("userbox").hidden = false;
  switchView("tasks");
  tick();
}


document.querySelectorAll(".main-tabs button").forEach((btn) => {
  btn.onclick = () => switchView(btn.dataset.view);
});

function switchView(view) {
  currentView = view;
  document.querySelectorAll(".main-tabs button").forEach((b) => b.classList.toggle("active", b.dataset.view === view));
  $("view-tasks").hidden = view !== "tasks";
  $("view-admin").hidden = view !== "admin";
  tick();
}


function complexityOf(w, hgt, r) { return w * hgt * (2 * r + 1) ** 2; }

function updateComplexityInfo() {
  const r = Number($("radius").value);
  $("radius-value").textContent = r;
  const info = $("complexity-info");
  if (!pickedImage || !limits) { info.textContent = "Оберіть файл, щоб побачити трудомісткість."; return null; }
  const cost = complexityOf(pickedImage.w, pickedImage.h, r);
  const text = "Розмір: " + pickedImage.w + "×" + pickedImage.h + ". Трудомісткість: " +
    cost.toLocaleString("uk-UA") + " із " + limits.max_complexity.toLocaleString("uk-UA") + ".";
  if (cost > limits.max_complexity) {
    info.textContent = text + " Забагато — зменшіть радіус.";
    info.style.color = "#c0392b";
    return "Задача занадто трудомістка. Зменшіть радіус або розмір зображення.";
  }
  info.textContent = text;
  info.style.color = "";
  return null;
}

$("radius").addEventListener("input", updateComplexityInfo);

$("file").addEventListener("change", () => {
  pickedImage = null;
  const file = $("file").files[0];
  if (!file) return updateComplexityInfo();
  const url = URL.createObjectURL(file);
  const img = new Image();
  img.onload = () => {
    pickedImage = { w: img.naturalWidth, h: img.naturalHeight, size: file.size };
    URL.revokeObjectURL(url);
    updateComplexityInfo();
  };
  img.onerror = () => { URL.revokeObjectURL(url); $("complexity-info").textContent = "Не вдалося прочитати зображення."; };
  img.src = url;
});

$("task-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const err = $("task-error");
  err.textContent = "";
  const file = $("file").files[0];
  if (!file || !pickedImage) { err.textContent = "Оберіть зображення."; return; }

  if (file.size > limits.max_file_mb * 1024 * 1024) { err.textContent = "Файл завеликий (максимум " + limits.max_file_mb + " МБ)."; return; }
  if (Math.max(pickedImage.w, pickedImage.h) > limits.max_side) { err.textContent = "Зображення завелике (максимум " + limits.max_side + " px по стороні)."; return; }
  const complexityError = updateComplexityInfo();
  if (complexityError) { err.textContent = complexityError; return; }

  const form = new FormData();
  form.append("file", file);
  form.append("filter", $("filter").value);
  form.append("radius", $("radius").value);
  $("submit-task").disabled = true;
  try {
    await api("/tasks", { method: "POST", body: form });
    $("task-form").reset();
    pickedImage = null;
    updateComplexityInfo();
    await refreshTasks();
  } catch (ex) {
    err.textContent = ex.message;
  } finally {
    $("submit-task").disabled = false;
  }
});


const STATUS_TEXT = { queued: "У черзі", running: "Виконується", done: "Готово", failed: "Помилка", cancelled: "Скасовано" };

function workersText(workers) {
  const names = Object.entries(workers || {});
  if (!names.length) return "—";
  return names.map(([name, count]) => name + " (" + count + ")").join(", ");
}

function formatTime(iso) { return new Date(iso).toLocaleString("uk-UA"); }

function taskRow(t) {
  const filterName = t.filter === "blur" ? "Розмиття" : "Різкість";
  const isActive = t.status === "queued" || t.status === "running";
  const actions = h("div", { class: "actions" });
  if (isActive) {
    actions.append(h("button", { class: "small danger", onclick: () => cancelTask(t.id) }, "Скасувати"));
  } else {
    if (t.status === "done") actions.append(h("button", { class: "small", onclick: () => showResult(t.id) }, "Переглянути"));
    actions.append(h("button", { class: "small secondary", onclick: () => deleteTask(t.id) }, "Видалити"));
  }

  const timeCell = [h("div", {}, formatTime(t.created_at))];
  if (t.duration_sec != null) timeCell.push(h("div", { class: "state-text" }, "Тривалість: " + t.duration_sec.toFixed(1) + " с"));

  return h("tr", {},
    h("td", {}, h("div", {}, filterName + ", радіус " + t.radius), h("div", { class: "state-text" }, t.width + "×" + t.height + " px")),
    h("td", {},
      h("span", { class: "badge " + t.status }, STATUS_TEXT[t.status]),
      h("div", { class: "bar " + t.status }, h("div", { style: "width:" + t.progress + "%" })),
      h("div", { class: "state-text" }, (isActive ? t.progress + "%. " : "") + (t.status === "done" ? "" : t.message))),
    h("td", {}, workersText(t.workers)),
    h("td", {}, timeCell),
    h("td", {}, actions));
}

async function refreshTasks() {
  const data = await (await api("/tasks")).json();
  const body = $("tasks-body");
  body.replaceChildren(...data.tasks.map(taskRow));
  $("empty-tasks").hidden = data.tasks.length > 0;
}

async function cancelTask(id) {
  try { await api("/tasks/" + id + "/cancel", { method: "POST" }); } catch (e) { $("task-error").textContent = e.message; }
  refreshTasks();
}

async function deleteTask(id) {
  try { await api("/tasks/" + id, { method: "DELETE" }); } catch (e) { $("task-error").textContent = e.message; }
  $("result-section").hidden = true;
  refreshTasks();
}

async function showResult(id) {
  try {
    const [orig, res] = await Promise.all([
      api("/tasks/" + id + "/original").then((r) => r.blob()),
      api("/tasks/" + id + "/result").then((r) => r.blob()),
    ]);
    const resultUrl = URL.createObjectURL(res);
    $("img-original").src = URL.createObjectURL(orig);
    $("img-result").src = resultUrl;
    $("download-link").href = resultUrl;
    $("result-section").hidden = false;
    $("result-section").scrollIntoView({ behavior: "smooth" });
  } catch (e) {
    $("task-error").textContent = e.message;
  }
}


function stat(value, label) { return h("div", { class: "stat" }, h("b", {}, value), h("span", {}, label)); }

async function refreshAdmin() {
  const d = await (await api("/admin/overview")).json();
  $("admin-stats").replaceChildren(
    stat(d.workers.length, "активних воркерів"),
    stat(d.slots_busy + " / " + d.slots_total, "слотів зайнято"),
    stat(d.queue_length, "у черзі celery-задач"),
    stat(d.task_counts.active, "активних задач"),
    stat(d.task_counts.done, "виконано"),
    stat(d.users, "користувачів"),
    stat(d.api_server, "цей API-сервер"));

  const a = d.autoscaler;
  $("autoscaler-off").hidden = !!a;
  $("autoscaler-stats").hidden = !a;
  if (a) {
    $("autoscaler-stats").replaceChildren(
      stat(a.workers + " / " + a.max, "воркерів зараз / максимум"),
      stat(a.min, "мінімум (базові)"),
      stat(a.auto_containers, "запущено автоматично"),
      stat(a.queue_len, "задач у черзі"));
    $("autoscaler-last").textContent = "Остання дія: " + a.last_action;
  } else {
    $("autoscaler-last").textContent = "";
  }

  $("admin-no-workers").hidden = d.workers.length > 0;
  $("admin-workers").replaceChildren(...d.workers.map((w) =>
    h("tr", {}, h("td", {}, w.name), h("td", {}, w.busy + " / " + w.concurrency), h("td", {}, w.processed))));

  $("admin-tasks").replaceChildren(...d.active_tasks.map((t) =>
    h("tr", {},
      h("td", {}, t.username),
      h("td", {}, (t.filter === "blur" ? "Розмиття" : "Різкість") + ", r=" + t.radius + ", " + t.width + "×" + t.height),
      h("td", {}, STATUS_TEXT[t.status] + " " + t.progress + "%"),
      h("td", {}, workersText(t.workers)))));
}


async function tick() {
  if (!token || busy) return;
  busy = true;
  try {
    if (currentView === "tasks") await refreshTasks(); else await refreshAdmin();
  } catch (e) {  }
  busy = false;
}
setInterval(tick, 1000);


if (token) startApp().catch(() => doLogout());
