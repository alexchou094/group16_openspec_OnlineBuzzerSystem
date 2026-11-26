const page = document.querySelector("[data-export-url]");
const base = page.dataset.exportUrl;
const csrf = document.querySelector('meta[name="csrf-token"]').content;
const confirmWrite = document.querySelector("#confirm-write");
const authorize = document.querySelector("#authorize-export");
const verify = document.querySelector("#verify-write");
const retry = document.querySelector("#retry-write");
const progress = document.querySelector("#export-progress");
const error = document.querySelector("#export-error");
let attempt = page.dataset.attempt;
let status = page.dataset.status;
let busy = false;

const labels = {RUNNING: "處理中", SUCCESS: "成功", FAILED: "失敗", UNCERTAIN: "待確認"};

function controls() {
  if (confirmWrite) confirmWrite.disabled = busy;
  if (authorize) authorize.disabled = busy;
  verify.hidden = status !== "UNCERTAIN";
  retry.hidden = !["FAILED", "UNCERTAIN"].includes(status);
  verify.disabled = retry.disabled = busy;
}

function render(result) {
  attempt = result.id;
  status = result.status;
  const older = result.older_snapshot ? " 此次作業仍使用先前確認的快照。" : "";
  progress.textContent = `${labels[status]}（定案版本 ${result.revision}，${result.cell_count} 格）。${result.message}${older}`;
}

async function request(path, body) {
  const options = body === undefined ? {} : {
    method: "POST",
    headers: {"Content-Type": "application/json", "X-CSRF-Token": csrf},
    body: JSON.stringify(body),
  };
  let response;
  try {
    response = await fetch(`${base}/${path}`, options);
  } catch {
    throw new Error("連線中斷，請更新預覽以查看伺服器保存的寫回進度。");
  }
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || "匯出作業失敗，請重試。");
  return data;
}

function showError(problem) {
  error.textContent = problem.message;
  error.hidden = false;
}

async function operate(path, body, openingMessage, handle = render) {
  busy = true;
  controls();
  error.hidden = true;
  const previous = progress.textContent;
  progress.textContent = openingMessage;
  try {
    handle(await request(path, body));
  } catch (problem) {
    progress.textContent = previous;
    showError(problem);
  } finally {
    busy = false;
    controls();
  }
}

confirmWrite?.addEventListener("click", () =>
  operate("write", {preview_id: confirmWrite.dataset.preview}, "正在檢查結構並寫回…"));
authorize?.addEventListener("click", () =>
  operate("oauth", {}, "正在連線至 Google…", ({url}) => window.location.assign(url)));
verify.addEventListener("click", () =>
  operate(`attempts/${attempt}/verify`, {}, "正在讀回核對…"));
retry.addEventListener("click", () =>
  operate(`attempts/${attempt}/retry`, {}, "正在重試先前確認的答案…"));

async function refreshAttempt() {
  busy = true;
  controls();
  progress.textContent = "正在查詢寫回進度…";
  try {
    render(await request(`attempts/${attempt}`));
    if (status === "RUNNING") {
      window.setTimeout(refreshAttempt, 1000);
      return;
    }
  } catch (problem) {
    showError(problem);
  }
  busy = false;
  controls();
}

if (attempt) refreshAttempt();
else controls();
