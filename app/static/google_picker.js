async function loadGoogleApi() {
  if (window.gapi) return;
  await new Promise((resolve, reject) => {
    const script = document.createElement("script");
    const timeout = setTimeout(() => failed("載入 Google Picker 逾時。"), 15000);
    function failed(message) {
      clearTimeout(timeout);
      script.remove();
      reject(new Error(message));
    }
    script.src = "https://apis.google.com/js/api.js";
    script.onload = () => {
      clearTimeout(timeout);
      if (window.gapi) resolve();
      else failed("無法載入 Google Picker。");
    };
    script.onerror = () => failed("無法載入 Google Picker，請確認網路連線後再試。");
    document.head.append(script);
  });
}

export async function chooseSpreadsheet(getCredentials) {
  await loadGoogleApi();
  await new Promise((resolve, reject) => {
    gapi.load("picker", {
      callback: resolve,
      onerror: () => reject(new Error("無法載入 Google Picker。")),
      timeout: 15000,
      ontimeout: () => reject(new Error("載入 Google Picker 逾時。")),
    });
  });
  const { token, key, project } = await getCredentials();
  const api = google.picker;
  return new Promise((resolve, reject) => {
    let picker = new api.PickerBuilder()
      .addView(api.ViewId.SPREADSHEETS)
      .setSelectableMimeTypes("application/vnd.google-apps.spreadsheet")
      .setOAuthToken(token)
      .setDeveloperKey(key)
      .setAppId(project)
      .setOrigin(location.origin)
      .setCallback(data => {
        if (![api.Action.PICKED, api.Action.CANCEL].includes(data.action)) return;
        picker?.dispose();
        picker = null;
        if (data.action === api.Action.CANCEL) return resolve(null);
        const selected = data.docs?.[0];
        if (!selected?.id || (selected.mimeType && selected.mimeType !== "application/vnd.google-apps.spreadsheet")) {
          return reject(new Error("請選擇 Google 試算表。"));
        }
        resolve(selected.id);
      })
      .build();
    picker.setVisible(true);
  });
}
