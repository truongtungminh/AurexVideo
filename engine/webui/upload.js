const state = { projects: [], selected: new URLSearchParams(location.search).get("project") || "", status: null };
const elements = Object.fromEntries([
  "uploadProject", "uploadVideo", "videoState", "uploadTitle", "youtubeDescription", "facebookCaption",
  "uploadYoutubeChannel", "youtubePrivacy", "uploadYoutubeButton", "uploadFacebookPage", "uploadFacebookButton",
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
    elements.uploadYoutubeChannel.replaceChildren(...(youtube.channels || []).map((channel) => option(channel.id, channel.title || channel.id, channel.active)));
    elements.uploadFacebookPage.replaceChildren(...(facebook.pages || []).map((page) => option(page.id, page.name || page.id, page.active)));
    elements.uploadYoutubeButton.disabled = !youtube.connected;
    elements.uploadFacebookButton.disabled = !facebook.connected;
    await loadProject();
  } catch (error) { showToast(error.message, true); }
}

async function setActive(platform, value) {
  const key = platform === "youtube" ? "channelId" : "pageId";
  await api(`/api/social/${platform}/active`, { method: "POST", body: JSON.stringify({ [key]: value }) });
}

async function upload(platform) {
  if (!state.selected) return showToast("Chưa chọn dự án.", true);
  const button = platform === "youtube" ? elements.uploadYoutubeButton : elements.uploadFacebookButton;
  button.disabled = true;
  elements.uploadResult.textContent = `Đang upload ${platform}... Giữ tab này mở.`;
  try {
    if (platform === "youtube") await setActive("youtube", elements.uploadYoutubeChannel.value);
    else await setActive("facebook", elements.uploadFacebookPage.value);
    const payload = platform === "youtube"
      ? { project: state.selected, title: elements.uploadTitle.value, description: elements.youtubeDescription.value, privacyStatus: elements.youtubePrivacy.value }
      : { project: state.selected, facebookCaption: elements.facebookCaption.value, facebookVideoState: "PUBLISHED" };
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

elements.uploadProject.addEventListener("change", () => { state.selected = elements.uploadProject.value; history.replaceState({}, "", `/upload?project=${encodeURIComponent(state.selected)}`); loadProject().catch((error) => showToast(error.message, true)); });
elements.uploadYoutubeChannel.addEventListener("change", () => setActive("youtube", elements.uploadYoutubeChannel.value).catch((error) => showToast(error.message, true)));
elements.uploadFacebookPage.addEventListener("change", () => setActive("facebook", elements.uploadFacebookPage.value).catch((error) => showToast(error.message, true)));
elements.uploadYoutubeButton.addEventListener("click", () => upload("youtube"));
elements.uploadFacebookButton.addEventListener("click", () => upload("facebook"));
load();
