const state = { projects: [], selected: new URLSearchParams(location.search).get("project") || "", status: null };
const elements = Object.fromEntries([
  "uploadProject", "uploadVideo", "videoState", "uploadTitle", "youtubeDescription", "facebookCaption",
  "uploadYoutubeChannel", "youtubePrivacy", "youtubeScheduleToggle", "youtubeScheduleRow", "youtubeScheduleTime", "uploadYoutubeButton", "uploadFacebookPage", "facebookScheduleToggle", "facebookScheduleRow", "facebookScheduleTime", "uploadFacebookButton", "tiktokCaption", "tiktokScheduleToggle", "tiktokScheduleRow", "tiktokScheduleTime", "uploadTiktokButton", "configureTiktokButton", "tiktokConfigModal", "tiktokConfigClose", "tiktokConfigState", "zernioApiKey", "zernioAccountId", "tiktokSaveButton", "tiktokDisconnectButton", "uploadBinanceButton", "binanceDuration", "binanceCaption",
  "configureBinanceButton", "binanceConfigModal", "binanceConfigClose", "binanceConfigState", "binanceApiKey", "binanceSaveConfigButton", "binanceDisconnectButton",
  "uploadResult", "toast",
].map((id) => [id, document.querySelector(`#${id}`)]));

function showToast(message, error = false) {
  elements.toast.textContent = message;
  elements.toast.className = `toast visible${error ? " error" : ""}`;
  clearTimeout(showToast.timer);
  showToast.timer = setTimeout(() => { elements.toast.className = "toast"; }, 3200);
}

async function api(url, options = {}) {
  const response = await fetch(url, { cache: "no-store", headers: { "Content-Type": "application/json", ...(options.headers || {}) }, ...options });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.error || `HTTP ${response.status}`);
  return payload;
}

function option(value, label, selected = false) {
  const node = document.createElement("option");
  node.value = value;
  node.textContent = label;
  node.selected = selected;
  return node;
}

function localDatetimeValue(date) {
  const pad = (n) => String(n).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

function setupScheduleToggle(toggleId, rowId, timeId, onEnable) {
  const toggle = elements[toggleId];
  const row = elements[rowId];
  const time = elements[timeId];
  toggle.addEventListener("change", () => {
    if (toggle.checked) {
      time.min = localDatetimeValue(new Date(Date.now() + 15 * 60 * 1000));
      time.value = "";
      row.hidden = false;
      if (onEnable) onEnable(true);
    } else {
      row.hidden = true;
      time.value = "";
      if (onEnable) onEnable(false);
    }
  });
}

function readScheduleTime(input, label) {
  if (!input.value) throw new Error(`Chưa chọn thời gian hẹn đăng cho ${label}.`);
  const date = new Date(input.value);
  if (Number.isNaN(date.getTime())) throw new Error(`Thời gian hẹn đăng ${label} không hợp lệ.`);
  if (date.getTime() <= Date.now()) throw new Error(`Thời gian hẹn đăng ${label} phải ở tương lai.`);
  return date.toISOString();
}

setupScheduleToggle("tiktokScheduleToggle", "tiktokScheduleRow", "tiktokScheduleTime");
setupScheduleToggle("youtubeScheduleToggle", "youtubeScheduleRow", "youtubeScheduleTime", (enabled) => {
  if (enabled) {
    elements.youtubePrivacy.value = "private";
    elements.youtubePrivacy.disabled = true;
  } else {
    elements.youtubePrivacy.disabled = false;
  }
});
setupScheduleToggle("facebookScheduleToggle", "facebookScheduleRow", "facebookScheduleTime");

async function loadProject() {
  if (!state.selected) return;
  const project = state.projects.find((item) => item.id === state.selected);
  if (project?.videoUrl) {
    elements.uploadVideo.src = project.videoUrl;
    elements.videoState.textContent = "Video đã sẵn sàng để upload.";
  } else {
    elements.uploadVideo.removeAttribute("src");
    elements.videoState.textContent = "Dự án này chưa có video render.";
  }
  const metadata = await api(`/api/social/metadata?project=${encodeURIComponent(state.selected)}`);
  elements.uploadTitle.value = metadata.title;
  elements.youtubeDescription.value = metadata.youtubeDescription;
  elements.facebookCaption.value = metadata.facebookCaption;
  elements.youtubePrivacy.value = metadata.privacyStatus || "public";
  elements.tiktokCaption.value = metadata.tiktokCaption || metadata.instagramCaption || metadata.facebookCaption || "";
}

async function load() {
  try {
    const [projects, status] = await Promise.all([api("/api/projects"), api("/api/social/status")]);
    state.projects = projects.projects;
    state.status = status;
    if (!state.projects.some((project) => project.id === state.selected)) state.selected = state.projects.find((project) => project.videoUrl)?.id || state.projects[0]?.id || "";
    elements.uploadProject.replaceChildren(...state.projects.map((project) => option(project.id, `${project.leftLabel} · ${project.rightLabel}`, project.id === state.selected)));
    const youtube = status.platforms?.youtube || {};
    const facebook = status.platforms?.facebook || {};
    const tiktok = status.platforms?.tiktok || {};
    elements.uploadYoutubeChannel.replaceChildren(...(youtube.channels || []).map((channel) => option(channel.id, channel.title || channel.id, channel.active)));
    elements.uploadFacebookPage.replaceChildren(...(facebook.pages || []).map((page) => option(page.id, page.name || page.id, page.active)));
    elements.uploadYoutubeButton.disabled = !youtube.connected;
    elements.uploadFacebookButton.disabled = !facebook.connected;
    elements.uploadTiktokButton.disabled = !tiktok.connected;
    elements.configureTiktokButton.textContent = tiktok.connected ? `Đổi Zernio (${tiktok.masked_api_key || "đã cấu hình"})` : "Cấu hình Zernio";
    elements.uploadBinanceButton.disabled = !(status.platforms?.binance?.connected);
    elements.configureBinanceButton.textContent = status.platforms?.binance?.connected ? "Đổi OpenAPI key" : "Cấu hình OpenAPI key";
    await loadProject();
  } catch (error) { showToast(error.message, true); }
}

async function setActive(platform, value) {
  const key = platform === "youtube" ? "channelId" : "pageId";
  await api(`/api/social/${platform}/active`, { method: "POST", body: JSON.stringify({ [key]: value }) });
}

async function upload(platform) {
  if (!state.selected) return showToast("Chưa chọn dự án.", true);
  const button = platform === "youtube" ? elements.uploadYoutubeButton : platform === "facebook" ? elements.uploadFacebookButton : platform === "tiktok" ? elements.uploadTiktokButton : elements.uploadBinanceButton;
  button.disabled = true;
  elements.uploadResult.textContent = `Đang upload ${platform}... Giữ tab này mở.`;
  try {
    if (platform === "youtube") await setActive("youtube", elements.uploadYoutubeChannel.value);
    else if (platform === "facebook") await setActive("facebook", elements.uploadFacebookPage.value);
    const scheduledPublishAt =
      platform === "youtube"
        ? (elements.youtubeScheduleToggle.checked ? readScheduleTime(elements.youtubeScheduleTime, "YouTube") : "")
        : platform === "facebook"
          ? (elements.facebookScheduleToggle.checked ? readScheduleTime(elements.facebookScheduleTime, "Facebook") : "")
          : platform === "tiktok"
            ? (elements.tiktokScheduleToggle.checked ? readScheduleTime(elements.tiktokScheduleTime, "TikTok") : "")
            : "";
    const payload =
      platform === "youtube"
        ? { project: state.selected, title: elements.uploadTitle.value, description: elements.youtubeDescription.value, privacyStatus: elements.youtubePrivacy.value, ...(scheduledPublishAt ? { scheduledPublishAt } : {}) }
        : platform === "facebook"
          ? { project: state.selected, facebookCaption: elements.facebookCaption.value, facebookVideoState: "PUBLISHED", ...(scheduledPublishAt ? { scheduledPublishAt } : {}) }
          : platform === "tiktok"
            ? { project: state.selected, tiktokCaption: elements.tiktokCaption.value, ...(scheduledPublishAt ? { scheduledPublishAt, scheduleTimezone: Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC' } : {}) }
            : { project: state.selected, duration: Number(elements.binanceDuration.value), text: elements.binanceCaption.value };
    const result = await api(`/api/social/${platform}/upload`, { method: "POST", body: JSON.stringify(payload) });
    elements.uploadResult.replaceChildren(document.createTextNode("Upload hoàn tất. "));
    if (result.url) {
      const link = document.createElement("a"); link.href = result.url; link.target = "_blank"; link.textContent = "Mở bài đã đăng ↗";
      elements.uploadResult.append(link);
    }
    showToast(`Đã upload ${platform}.`);
  } catch (error) {
    elements.uploadResult.textContent = error.message;
    showToast(error.message, true);
  } finally { button.disabled = false; }
}

async function refreshBinanceStatus() {
  try {
    const status = await api("/api/social/status");
    state.status = status;
    elements.uploadBinanceButton.disabled = !(status.platforms?.binance?.connected);
    elements.configureBinanceButton.textContent = status.platforms?.binance?.connected ? "Đổi OpenAPI key" : "Cấu hình OpenAPI key";
  } catch (error) {
    showToast(error.message, true);
  }
}

function openBinanceConfig() {
  elements.binanceConfigState.textContent = state.status?.platforms?.binance?.configured
    ? `Đã cấu hình: ${state.status.platforms?.binance?.masked || ""}`
    : "Chưa có OpenAPI key. Dán key vào ô dưới rồi Lưu.";
  elements.binanceApiKey.value = "";
  elements.binanceConfigModal.hidden = false;
  elements.binanceApiKey.focus();
}

function closeBinanceConfig() {
  elements.binanceConfigModal.hidden = true;
}

elements.configureBinanceButton.addEventListener("click", openBinanceConfig);
elements.binanceConfigClose.addEventListener("click", closeBinanceConfig);
elements.binanceConfigModal.addEventListener("click", (event) => { if (event.target === elements.binanceConfigModal) closeBinanceConfig(); });
elements.binanceSaveConfigButton.addEventListener("click", async () => {
  const apiKey = elements.binanceApiKey.value.trim();
  if (!apiKey) return showToast("Nhập OpenAPI key trước khi lưu.", true);
  elements.binanceSaveConfigButton.disabled = true;
  try {
    const result = await api("/api/social/binance/config", { method: "POST", body: JSON.stringify({ apiKey }) });
    showToast("Đã lưu cấu hình Binance Square.");
    closeBinanceConfig();
    await refreshBinanceStatus();
  } catch (error) {
    showToast(error.message, true);
  } finally {
    elements.binanceSaveConfigButton.disabled = false;
  }
});
elements.binanceDisconnectButton.addEventListener("click", async () => {
  elements.binanceDisconnectButton.disabled = true;
  try {
    await api("/api/social/binance/disconnect", { method: "POST" });
    showToast("Đã gỡ cấu hình Binance Square.");
    closeBinanceConfig();
    await refreshBinanceStatus();
  } catch (error) {
    showToast(error.message, true);
  } finally {
    elements.binanceDisconnectButton.disabled = false;
  }
});

elements.uploadProject.addEventListener("change", () => { state.selected = elements.uploadProject.value; history.replaceState({}, "", `/upload?project=${encodeURIComponent(state.selected)}`); loadProject().catch((error) => showToast(error.message, true)); });
elements.uploadYoutubeChannel.addEventListener("change", () => setActive("youtube", elements.uploadYoutubeChannel.value).catch((error) => showToast(error.message, true)));
elements.uploadFacebookPage.addEventListener("change", () => setActive("facebook", elements.uploadFacebookPage.value).catch((error) => showToast(error.message, true)));
elements.uploadYoutubeButton.addEventListener("click", () => upload("youtube"));
elements.uploadFacebookButton.addEventListener("click", () => upload("facebook"));
elements.uploadTiktokButton.addEventListener("click", () => upload("tiktok"));
function openTiktokConfig() {
  const current = state.status?.platforms?.tiktok || {};
  elements.tiktokConfigState.textContent = current.connected ? `Đã cấu hình Zernio: ${current.masked_api_key || "đã lưu"}` : "Nhập API key và TikTok account ID từ Zernio.";
  elements.zernioApiKey.value = "";
  elements.zernioAccountId.value = current.account_id || "";
  elements.tiktokConfigModal.hidden = false;
  elements.zernioApiKey.focus();
}
function closeTiktokConfig() { elements.tiktokConfigModal.hidden = true; }
elements.configureTiktokButton.addEventListener("click", openTiktokConfig);
elements.tiktokConfigClose.addEventListener("click", closeTiktokConfig);
elements.tiktokConfigModal.addEventListener("click", (event) => { if (event.target === elements.tiktokConfigModal) closeTiktokConfig(); });
elements.tiktokSaveButton.addEventListener("click", async () => {
  const apiKey = elements.zernioApiKey.value.trim(); const accountId = elements.zernioAccountId.value.trim();
  if (!apiKey || !accountId) return showToast("Nhập đủ Zernio API key và TikTok account ID.", true);
  elements.tiktokSaveButton.disabled = true;
  try { await api("/api/social/tiktok/config", { method: "POST", body: JSON.stringify({ apiKey, accountId }) }); closeTiktokConfig(); showToast("Đã lưu cấu hình Zernio TikTok."); await load(); } catch (error) { showToast(error.message, true); } finally { elements.tiktokSaveButton.disabled = false; }
});
elements.tiktokDisconnectButton.addEventListener("click", async () => {
  elements.tiktokDisconnectButton.disabled = true;
  try { await api("/api/social/tiktok/disconnect", { method: "POST" }); closeTiktokConfig(); showToast("Đã gỡ cấu hình Zernio."); await load(); } catch (error) { showToast(error.message, true); } finally { elements.tiktokDisconnectButton.disabled = false; }
});
elements.uploadBinanceButton.addEventListener("click", () => upload("binance"));
load();
