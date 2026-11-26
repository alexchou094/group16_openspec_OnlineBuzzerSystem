import { chooseSpreadsheet } from "./google_picker.js";

const csrf = document.querySelector('meta[name="csrf-token"]').content;
const statusText = document.querySelector("#status");
const resultText = document.querySelector("#result");
const pickButton = document.querySelector("#pick");
const readButton = document.querySelector("#read");
const writeButton = document.querySelector("#write");

async function post(path, data) {
    const response = await fetch(path, {
        method: "POST",
        headers: {"X-CSRF-Token": csrf, "Content-Type": "application/json"},
        body: JSON.stringify(data),
    });
    const result = await response.json();
    if (!response.ok) {
        if (response.status === 401) {
            pickButton.dataset.expired = "true";
            pickButton.disabled = true;
            readButton.disabled = true;
            writeButton.disabled = true;
        }
        throw new Error(typeof result.detail === "string" ? result.detail : "請求失敗。");
    }
    return result;
}

async function show(operation) {
    statusText.textContent = "處理中…";
    resultText.textContent = "";
    try {
        const result = await operation();
        if (result) {
            statusText.textContent = "完成。";
            resultText.textContent = JSON.stringify(result, null, 2);
        }
    } catch (error) {
        statusText.textContent = error instanceof TypeError ? "連線失敗，請再試一次。" : error.message;
    }
}

document.querySelector("#authorize").onclick = () => show(async () => {
    const {url} = await post("/oauth/start");
    location.assign(url);
});

pickButton.onclick = () => show(async () => {
    pickButton.disabled = true;
    statusText.textContent = "請在 Google Picker 選取測試複本。";
    try {
        const spreadsheetId = await chooseSpreadsheet(() => post("/picker"));
        if (spreadsheetId === null) {
            statusText.textContent = "已取消選檔。";
            return;
        }
        readButton.disabled = true;
        writeButton.disabled = true;
        const result = await post("/select", {spreadsheet_id: spreadsheetId});
        readButton.disabled = false;
        writeButton.disabled = false;
        return result;
    } finally {
        if (!pickButton.dataset.expired) pickButton.disabled = false;
    }
});

readButton.onclick = () => show(() => post("/read"));
writeButton.onclick = () => show(() => post("/write"));
