const activityId = document.body.dataset.activity;
const demo = document.body.dataset.demo === "yes";
const notice = document.querySelector("#notice");
const phases = { PREPARING: "準備中", OPEN: "搶答中", RESULTS: "本輪結果", CANCELLED: "已取消", FINISHED: "已結束" };
let csrfToken;
let current;
let latestRequest = 0;
let deadline = null;
let controlsKey;

class RequestError extends Error {
  constructor(status, detail) {
    super(detail);
    this.status = status;
  }
}

async function request(url, method = "GET", body) {
  const headers = {};
  if (method !== "GET") headers["X-CSRF-Token"] = csrfToken;
  if (body !== undefined) headers["Content-Type"] = "application/json";
  const response = await fetch(url, { method, headers, body: JSON.stringify(body), signal: AbortSignal.timeout(2500) });
  if (response.status === 204) return;
  const data = await response.json();
  if (!response.ok) throw new RequestError(response.status, data.detail ?? "操作失敗。");
  return data;
}

function notify(message = "") {
  notice.textContent = message;
  notice.hidden = !message;
}

function element(tag, text = "") {
  const node = document.createElement(tag);
  node.textContent = text;
  return node;
}

function integer(raw) {
  const value = Number(raw);
  return /^\d+$/.test(raw) && Number.isSafeInteger(value) ? value : null;
}

function activityUrl(id) {
  return `/activities/${encodeURIComponent(id)}`;
}

function attachForm(form, submit) {
  const button = form.querySelector('button[type="submit"]');
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    button.disabled = true;
    notify();
    try {
      await submit(new FormData(form));
    } catch (error) {
      notify(error instanceof RequestError ? error.message : "連線中斷，請確認目前畫面後再操作。");
    } finally {
      button.disabled = false;
    }
  });
  button.disabled = false;
}

async function home(session) {
  const resume = document.querySelector("#resume");
  if (session.active_activity) {
    resume.href = activityUrl(session.active_activity);
    resume.hidden = false;
  }
  const studentResume = document.querySelector("#student-resume");
  const studentActivities = session.student_activities;
  for (const activity of studentActivities) {
    const label = studentActivities.length === 1
      ? "回到進行中的活動"
      : `回到進行中的活動（${activity.code}）`;
    const link = element("a", label);
    link.href = activityUrl(activity.id);
    const row = element("p");
    row.append(link);
    studentResume.append(row);
  }
  studentResume.hidden = studentActivities.length === 0;
  attachForm(document.querySelector("#create"), async (data) => {
    const { id } = await request("/activities", "POST", { group_limit: integer(data.get("limit")) });
    location.assign(activityUrl(id));
  });
  attachForm(document.querySelector("#join"), async (data) => {
    const { id } = await request("/activities/join", "POST", {
      code: data.get("code"), group_number: integer(data.get("number")), credential: data.get("credential") || null,
    });
    location.assign(activityUrl(id));
  });
}

function actionUrl(path) {
  return `${activityUrl(activityId)}/${path}`;
}

function button(label, path, { method = "POST", body, leave = false, confirmation } = {}) {
  const node = element("button", label);
  node.type = "button";
  node.addEventListener("click", async () => {
    if (confirmation && !window.confirm(confirmation)) return;
    node.disabled = true;
    notify();
    try {
      await request(actionUrl(path), method, body);
      if (leave) location.assign("/");
      else await sync();
    } catch (error) {
      notify(error instanceof RequestError ? error.message : "連線中斷，請確認最新狀態後再操作。");
      await sync();
    } finally {
      node.disabled = false;
    }
  });
  return node;
}

function openForm(questions) {
  const form = element("form");
  const choices = element("fieldset");
  choices.append(element("legend", "本輪題目"));
  for (const question of questions) {
    const label = element("label");
    const checkbox = element("input");
    Object.assign(checkbox, { type: "checkbox", name: "questions", value: question.id, checked: true });
    label.append(checkbox, ` 第 ${question.number} 題`);
    choices.append(label);
  }
  const mode = element("label", "排序模式");
  const select = element("select");
  select.name = "mode";
  select.add(new Option("速度", "speed"));
  select.add(new Option("公平", "fair"));
  mode.append(select);
  const countdown = element("label", "倒數秒數（留空表示無倒數）");
  const seconds = element("input");
  Object.assign(seconds, { name: "seconds", type: "number", min: "1", max: "3600", step: "1" });
  countdown.append(seconds);
  const submit = element("button", "開輪");
  submit.type = "submit";
  form.append(choices, mode, countdown, submit);
  attachForm(form, async (data) => {
    const raw = data.get("seconds");
    await request(actionUrl("rounds"), "POST", {
      question_ids: data.getAll("questions"), fairness_enabled: data.get("mode") === "fair",
      seconds: raw ? integer(raw) : null,
    });
    await sync();
  });
  return form;
}

function resultsPanel(state) {
  const section = element("section");
  const rounds = new Map(state.history.map((round) => [round.id, round]));
  if (state.round?.review_version) rounds.set(state.round.id, state.round);
  section.append(element("h2", "輪次結果"));
  for (const round of rounds.values()) {
    section.append(element("h3", `第 ${round.number} 輪 · ${round.finalized ? "已定案" : round.review_complete ? "暫定，待定案" : "待審閱"}`));
    const list = element("ul");
    if (round.affected_questions.length) section.append(element("p", `結果更新：第 ${round.affected_questions.join('、')} 題`));
    for (const result of round.results) {
      const summary = result.status === "WAITING" ? "等待前題審閱" : result.status === "PENDING" ? "待審閱"
        : result.winner === null ? "未成功" : `第 ${result.winner} 組勝出`;
      const row = element("li", `第 ${result.question} 題 · ${summary} · ${result.reason}`);
      row.dataset.question = result.question;
      const candidates = element("ul");
      for (const candidate of result.candidates) {
        const outcome = candidate.rejected ? "判錯 · 搶答失敗" : candidate.review_status === "CORRECT" ? "正確 · 搶答成功"
          : result.status === "COMPLETE" ? "搶答失敗" : "待審閱";
        const position = candidate.rank === null ? "" : ` · 順位 ${candidate.rank}`;
        const successes = candidate.successes_before === null ? "" : ` · 處理時成功 ${candidate.successes_before} 題`;
        const item = element("li", `第 ${candidate.group} 組 · ${outcome}${position}${successes}`);
        item.dataset.submission = candidate.id;
        appendAnswers(item, candidate.answers);
        if (state.role === "teacher" && !round.finalized && state.phase === "RESULTS" && round.id === state.round.id) {
          const path = `rounds/${round.id}/reviews/${candidate.id}`;
          const options = status => ({method: "PATCH", body: {review_status: status, expected_review_version: round.review_version}});
          if (candidate.id === result.current_submission_id) {
            item.append(button("確認正確", path, options("CORRECT")), button("判錯", path, options("INCORRECT")));
          } else if (candidate.review_status !== "UNREVIEWED") {
            const affected = round.results.slice(round.results.indexOf(result)).map(r => r.question).join("、");
            item.append(button("撤銷審閱", path, {...options("UNREVIEWED"),
              confirmation: `撤銷後，第 ${affected} 題須從受影響處重新審閱，原暫定勝者可能失效。確定撤銷？`}));
          }
        }
        candidates.append(item);
      }
      row.append(candidates);
      list.append(row);
    }
    section.append(list);
  }
  if (state.phase === "FINISHED") {
    const remaining = state.unsuccessful_questions.map((q) => `第 ${q} 題`).join("、") || "無";
    section.append(element("p", `未成功題目：${remaining}`));
  }
  return section;
}

function appendAnswers(parent, answers) {
  answers.forEach((answer, index) => {
    parent.append(element("p", `空格 ${index + 1}`));
    const text = element("div", answer);
    text.className = "answer-text";
    parent.append(text);
  });
}

function submissionPanel(state) {
  const section = element("section");
  section.id = "submissions";
  section.append(element("h2", state.role === "teacher" ? "即時提交（依後端接收先後）" : "本組作答"));
  const round = state.round;
  for (const q of round.question_details) {
    const block = element("div");
    block.append(element("h3", `第 ${q.number} 題`));
    const sent = round.submissions.filter(s => s.question_id === q.id);
    sent.forEach((s, index) => {
      const row = element("div");
      row.append(element("p", `第 ${s.group} 組 · ${state.role === "teacher" ? `提交先後 ${index + 1}` : "本組已送出"}`));
      appendAnswers(row, s.answers);
      block.append(row);
    });
    if (state.role === "teacher") {
      for (const group of state.groups.filter(g => !sent.some(s => s.group === g.number))) {
        block.append(element("p", `第 ${group.number} 組 · 未送出`));
      }
    } else if (!sent.length) {
      const form = element("form");
      form.dataset.question = q.id;
      for (const label of q.blanks) {
        const wrapper = element("label", `第 ${q.number} 題・空格 ${label}`);
        const input = element("textarea");
        input.name = "answer";
        input.required = true;
        const count = element("small", "0 / 500 字（空白與換行也計入）");
        input.addEventListener("input", () => {
          const length = Array.from(input.value).length;
          count.textContent = `${length} / 500 字（空白與換行也計入）`;
          input.setCustomValidity(length > 500 ? "每格最多 500 字。" : "");
        });
        wrapper.append(input);
        form.append(wrapper, count);
      }
      const submit = element("button", `送出第 ${q.number} 題`);
      submit.type = "submit";
      form.append(submit);
      attachForm(form, async data => {
        try {
          const response = await request(actionUrl(`rounds/${round.id}/submissions`), "POST", { question_id: q.id, answers: data.getAll("answer") });
          notify(response.message);
        } catch (error) {
          notify(error instanceof RequestError ? error.message : "提交回應未收到，結果待確認；請等待同步或重送，本組只會保留第一筆有效答案。");
        }
        await sync();
      });
      block.append(form);
    }
    section.append(block);
  }
  return section;
}

function renderPanels(state) {
  const panels = document.querySelector("#panels");
  const phase = state.phase;
  const round = state.round;
  const teacher = state.role === "teacher";
  const controls = element("section");
  controls.append(element("h2", teacher ? "活動操作" : "組別操作"));
  if (teacher) {
    if (phase === "PREPARING") {
      const importLink = element("a", state.import_summary ? "重新匯入學習單" : "匯入學習單");
      importLink.href = actionUrl("import");
      const importRow = element("p");
      importRow.append(importLink);
      controls.append(importRow);
      if (demo && !state.available_questions.length) controls.append(button("載入示範題目", "demo-questions"));
      if (!state.available_questions.length) controls.append(element("p", "開輪前請先匯入題目。"));
      controls.append(button("取消活動", "cancel"));
    }
    if (phase === "PREPARING" || (phase === "RESULTS" && round.finalized)) {
      if (state.available_questions.length) controls.append(openForm(state.available_questions));
    }
    if (phase === "OPEN") controls.append(button("關輪", `rounds/${round.id}/close`));
    if (phase === "RESULTS") {
      if (round.finalized) controls.append(button("結束活動", "finish"));
      else if (round.review_complete) controls.append(button("確認定案", `rounds/${round.id}/finalize`, { body: { expected_review_version: round.review_version } }));
      else controls.append(element("p", "請完成逐題審閱後再定案。"));
    }
  } else if (["PREPARING", "RESULTS"].includes(phase)) {
    controls.append(button("離開組別", "leave", { leave: true }));
  }
  if (phase === "OPEN") controls.append(element("p", `開放題目：${round.questions.map((n) => `第 ${n} 題`).join("、")}`));

  const sections = controls.children.length > 1 ? [controls] : [];
  if (phase === "OPEN") sections.push(submissionPanel(state));
  if (teacher) {
    if (state.import_summary) {
      const summary = state.import_summary;
      const section = element("section");
      section.append(element("h2", "目前匯入結果"), element("p", summary.title),
        element("p", `${summary.question_count} 題、${summary.blank_count} 格`));
      const questions = element("ul");
      questions.className = "import-questions";
      for (const question of summary.questions) {
        questions.append(element("li", `第 ${question.number} 題 · ${question.blank_count} 格`));
      }
      section.append(questions);
      sections.push(section);
    }
    const groups = element("section");
    groups.append(element("h2", "組別與加入憑證"));
    if (!state.groups.length) groups.append(element("p", "目前沒有組別。"));
    const list = element("ul");
    for (const group of state.groups) {
      const row = element("li", `第 ${group.number} 組 · 憑證 ${group.credential} · 成功 ${group.successes} 題 `);
      if (!["FINISHED", "CANCELLED"].includes(phase) && group.can_delete) {
        row.append(button(`刪除第 ${group.number} 組`, `groups/${group.number}`, { method: "DELETE" }));
      }
      list.append(row);
    }
    groups.append(list);
    sections.push(groups);
  }
  if (round?.review_version || state.history.length) sections.push(resultsPanel(state));

  const nextKey = JSON.stringify([phase, round?.id, round?.review_version, round?.finalized, state.available_questions, state.import_summary]);
  if (nextKey === controlsKey && panels.firstElementChild?.querySelector("form") && sections[0] === controls) {
    // 組別更新時保留老師正在編輯的開輪設定。
    for (const child of Array.from(panels.children).slice(1)) child.remove();
    panels.append(...sections.slice(1));
  } else {
    // 同組其他題更新時，保留尚未提交題目的輸入。
    if (current?.round?.id === round?.id && phase === "OPEN") {
      for (const form of panels.querySelectorAll('form[data-question]')) {
        const replacement = sections.flatMap(s => Array.from(s.querySelectorAll('form[data-question]'))).find(f => f.dataset.question === form.dataset.question);
        if (replacement) replacement.replaceWith(form);
      }
    }
    panels.replaceChildren(...sections);
  }
  controlsKey = nextKey;
}

function render(state) {
  document.querySelector("#activity").hidden = false;
  document.querySelector("#title").textContent = state.role === "teacher" ? "老師看板" : "學生畫面";
  document.querySelector("#phase").textContent = phases[state.phase];
  document.querySelector("#identity").textContent = state.role === "teacher"
    ? `活動代碼 ${state.code} · 組數上限 ${state.group_limit}`
    : `第 ${state.group.number} 組 · 加入憑證 ${state.group.credential} · 成功 ${state.group.successes} 題`;
  const round = state.round;
  document.querySelector("#round").textContent = round
    ? `第 ${round.number} 輪 · ${round.fairness_enabled ? "公平" : "速度"}模式` : "";
  renderPanels(state);
}

function showTimer() {
  const timer = document.querySelector("#timer");
  timer.textContent = current?.phase !== "OPEN" ? "" : deadline === null ? "無倒數，由老師結束"
    : `剩餘 ${Math.max(0, Math.ceil((deadline - performance.now()) / 1000))} 秒`;
  for (const submit of document.querySelectorAll('#submissions button[type="submit"]')) {
    submit.disabled = deadline !== null && performance.now() >= deadline;
  }
}

async function sync() {
  const connection = document.querySelector("#connection");
  const requestId = ++latestRequest;
  try {
    const state = await request(actionUrl("state"));
    if (requestId !== latestRequest) return;
    if (current && state.state_version < current.state_version) return;
    connection.textContent = "已同步";
    connection.dataset.offline = "false";
    deadline = state.round?.deadline ? performance.now() + Date.parse(state.round.deadline) - Date.parse(state.server_time) : null;
    if (state.state_version !== current?.state_version) render(state);
    current = state;
    showTimer();
  } catch (error) {
    if (requestId !== latestRequest) return;
    connection.dataset.offline = "true";
    if (error instanceof RequestError && [401, 403, 404].includes(error.status)) {
      document.querySelector("#activity").hidden = true;
      current = undefined;
      connection.textContent = error.message;
    } else {
      connection.textContent = "同步中斷，正在重新連線。";
    }
  }
}

async function poll() {
  await sync();
  setTimeout(poll, 1000);
}

async function initialize() {
  try {
    const session = await request("/api/session");
    csrfToken = session.csrf_token;
    if (activityId) {
      poll();
      setInterval(showTimer, 200);
    } else {
      await home(session);
    }
  } catch {
    if (!activityId) notify("無法連線，請確認服務已啟動並重新整理。");
    else {
      const connection = document.querySelector("#connection");
      connection.textContent = "同步中斷，正在重新連線。";
      connection.dataset.offline = "true";
      setTimeout(initialize, 1000);
    }
  }
}

if (!activityId) {
  window.addEventListener("pageshow", (event) => {
    if (event.persisted) location.reload();
  });
}

initialize();
