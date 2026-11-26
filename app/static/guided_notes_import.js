import { chooseSpreadsheet } from "./google_picker.js";

const importUrl = document.querySelector("main").dataset.importUrl;
const csrf = document.querySelector('meta[name="csrf-token"]').content;
const authorize = document.querySelector("#authorize");
const pick = document.querySelector("#pick");
const statusText = document.querySelector("#import-status");
const errorText = document.querySelector("#import-error");
const form = document.querySelector("#sheet-form");
const sheet = form.elements.sheet;
const submit = form.querySelector('button[type="submit"]');
const cancel = document.querySelector("#cancel-selection");
let spreadsheetId;
let pickingEnabled = pick.dataset.authorized === "yes";
let busy = false;

async function post(action, data) {
  const response = await fetch(`${importUrl}/${action}`, {
    method: "POST",
    headers: { "X-CSRF-Token": csrf, "Content-Type": "application/json" },
    body: JSON.stringify(data),
    signal: AbortSignal.timeout(35000),
  });
  if (response.status === 401) pickingEnabled = false;
  const result = await response.json().catch(() => ({}));
  if (response.ok && !Object.keys(result).length) throw new Error("伺服器回應異常，請稍後再試。");
  if (!response.ok) throw new Error(result.detail || "操作失敗，請稍後重試。");
  return result;
}

async function run(operation) {
  if (busy) return;
  busy = true;
  authorize.disabled = pick.disabled = submit.disabled = cancel.disabled = true;
  errorText.hidden = true;
  statusText.textContent = "處理中…";
  try {
    await operation();
  } catch (error) {
    statusText.textContent = "";
    errorText.textContent = error instanceof TypeError || error.name === "TimeoutError"
      ? "連線失敗或逾時，請確認目前結果後再試。" : error.message;
    errorText.hidden = false;
  } finally {
    busy = false;
    authorize.disabled = submit.disabled = cancel.disabled = false;
    pick.disabled = !pickingEnabled;
  }
}

async function select(data) {
  const result = await post("select", data);
  if (result.imported) {
    location.reload();
    return;
  }
  sheet.replaceChildren(new Option("請選擇一張工作表", ""),
    ...result.sheets.map(item => new Option(item.title, String(item.id))));
  form.hidden = false;
  statusText.textContent = "這份試算表包含多張工作表。";
  sheet.focus();
}

authorize.onclick = () => run(async () => {
  const { url } = await post("oauth");
  location.assign(url);
});

pick.onclick = () => run(async () => {
  form.hidden = true;
  spreadsheetId = await chooseSpreadsheet(() => post("picker"));
  if (spreadsheetId === null) {
    statusText.textContent = "已取消選檔。";
    return;
  }
  await select({ spreadsheet_id: spreadsheetId });
});

form.onsubmit = event => {
  event.preventDefault();
  void run(() => select({ spreadsheet_id: spreadsheetId, sheet_id: Number(sheet.value) }));
};

cancel.onclick = () => {
  form.hidden = true;
  spreadsheetId = undefined;
  errorText.hidden = true;
  statusText.textContent = "已取消選擇工作表。";
  pick.focus();
};

authorize.disabled = false;
pick.disabled = !pickingEnabled;
