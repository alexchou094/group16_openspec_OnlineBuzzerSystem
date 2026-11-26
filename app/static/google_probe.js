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
    if (!window.gapi) throw new Error("無法載入 Google Picker。");
    await new Promise((resolve, reject) => {
        gapi.load("picker", {
            callback: resolve,
            onerror: () => reject(new Error("無法載入 Google Picker。")),
            timeout: 15000,
            ontimeout: () => reject(new Error("載入 Google Picker 逾時。")),
        });
    });
    const {token, key, project} = await post("/picker");
    const api = google.picker;
    let picker = new api.PickerBuilder()
        .addView(api.ViewId.SPREADSHEETS)
        .setSelectableMimeTypes("application/vnd.google-apps.spreadsheet")
        .setOAuthToken(token)
        .setDeveloperKey(key)
        .setAppId(project)
        .setOrigin(location.origin)
        .setCallback(data => {
            if (![api.Action.PICKED, api.Action.CANCEL].includes(data.action)) return;
            picker.dispose();
            picker = null;
            if (data.action === api.Action.CANCEL) {
                statusText.textContent = "已取消選檔。";
                return;
            }
            readButton.disabled = true;
            writeButton.disabled = true;
            void show(async () => {
                const result = await post("/select", {spreadsheet_id: data.docs[0].id});
                readButton.disabled = false;
                writeButton.disabled = false;
                return result;
            });
        })
        .build();
    statusText.textContent = "請在 Google Picker 選取測試複本。";
    picker.setVisible(true);
});

readButton.onclick = () => show(() => post("/read"));
writeButton.onclick = () => show(() => post("/write"));
