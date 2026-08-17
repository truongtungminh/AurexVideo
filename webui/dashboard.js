const state = {
  projects: [],
  jobs: new Map(),
  selected: localStorage.getItem("tho-selected-project") || "",
  engine: "maziao",
  activeJob: null,
  activeJobs: [],
  poller: 0,
  characters: [],
  characterDraft: null,
  characterEdit: null,
};

const elements = Object.fromEntries([
  "projectGrid", "projectCount", "pageStatus", "dependencies", "refreshButton", "newProjectButton",
  "projectDialog", "projectForm", "duplicateDialog", "duplicateForm", "jobList", "toast", "selectedProject",
  "renderAudioFile", "renderAudioName", "maziaoApiKey", "maziaoVoiceId", "maziaoModelId", "saveMaziaoConfig",
  "maziaoConfigState", "elevenApiKey", "elevenVoiceId", "elevenModelId", "saveElevenConfig", "elevenConfigState",
  "aurexttsProvider", "aurexttsVoice", "aurexttsStyle", "aurexttsTtsSpeed", "aurexttsBaseUrl",
  "checkAurextts", "aurexttsConfigState",
  "edgeVoice", "renderSpeed", "renderSize", "startRender", "stopRender", "renderLive",
  "renderLiveTitle", "renderPercent", "renderProgress", "renderLogs", "renderOutput",
  "socialState", "uploadSettingsLink", "openUploadCenter", "renderUpload",
  "characterDialog", "characterForm", "createCharacterButton", "characterSelect", "characterCover", "characterMeta",
  "characterPrompt", "copyCharacterPrompt", "characterSheetFile", "characterSplitStatus", "poseDraftGrid",
  "characterName", "characterId", "saveCharacterButton",
  "editCharacterButton", "characterEditDialog", "characterEditForm", "characterEditGrid",
  "characterEditStatus", "saveCharacterEditButton",
].map((id) => [id, document.querySelector(`#${id}`)]));

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>'"]/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" }[char]));
}

function showToast(message, error = false) {
  elements.toast.textContent = message;
  elements.toast.className = `toast visible${error ? " error" : ""}`;
  clearTimeout(showToast.timer);
  showToast.timer = setTimeout(() => { elements.toast.className = "toast"; }, 3000);
}

async function api(url, options = {}) {
  const response = await fetch(url, { cache: "no-store", headers: { "Content-Type": "application/json", ...(options.headers || {}) }, ...options });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.error || `HTTP ${response.status}`);
  return payload;
}

function formatDuration(seconds) {
  const value = Math.max(0, Math.round(Number(seconds) || 0));
  return `${String(Math.floor(value / 60)).padStart(2, "0")}:${String(value % 60).padStart(2, "0")}`;
}

function relativeTime(value) {
  const minutes = Math.floor(Math.max(0, Date.now() - new Date(value).getTime()) / 60000);
  if (minutes < 1) return "vừa cập nhật";
  if (minutes < 60) return `${minutes} phút trước`;
  const hours = Math.floor(minutes / 60);
  return hours < 24 ? `${hours} giờ trước` : `${Math.floor(hours / 24)} ngày trước`;
}

function renderDependencies(dependencies = {}) {
  elements.dependencies.innerHTML = Object.entries(dependencies).map(([name, ok]) => `<span class="dependency ${ok ? "ok" : ""}">${escapeHtml(name)}</span>`).join("");
}

function cardHtml(project) {
  const selected = project.id === state.selected;
  const cover = project.previewUrl
    ? `<img src="${escapeHtml(project.previewUrl)}?v=${encodeURIComponent(project.updatedAt)}" alt="Preview ${escapeHtml(project.id)}" />`
    : `<div class="cover-placeholder"><strong>${escapeHtml(project.leftLabel)}<br />vs<br />${escapeHtml(project.rightLabel)}</strong></div>`;
  return `<article class="project-card ${selected ? "selected" : ""}" data-project="${escapeHtml(project.id)}">
    <div class="project-cover">${cover}<span class="duration">${formatDuration(project.duration)}</span></div>
    <div class="project-body">
      <h3>${escapeHtml(project.leftLabel)} · ${escapeHtml(project.rightLabel)}</h3>
      <p>${escapeHtml(project.id)}</p>
      <div class="project-meta"><span>${project.segmentCount} nhịp thoại</span><span>${relativeTime(project.updatedAt)}</span></div>
      <div class="project-actions project-actions-wide">
        <button class="button ${selected ? "secondary" : "primary"}" data-action="select">${selected ? "Đang chọn" : "Chọn render"}</button>
        <a class="button secondary" href="/editor?project=${encodeURIComponent(project.id)}">Editor</a>
        ${project.videoUrl ? `<a class="button secondary icon-action" href="${escapeHtml(project.videoUrl)}" target="_blank" title="Mở video">▶</a>` : ""}
        <button class="button secondary icon-action" data-action="duplicate" title="Nhân bản">⧉</button>
        <button class="button danger icon-action" data-action="delete" title="Xoá">×</button>
      </div>
    </div>
  </article>`;
}

function ensureSelection() {
  if (!state.projects.some((project) => project.id === state.selected)) state.selected = state.projects[0]?.id || "";
  localStorage.setItem("tho-selected-project", state.selected);
  elements.selectedProject.textContent = state.selected || "Chưa có dự án";
  elements.openUploadCenter.href = state.selected ? `/upload?project=${encodeURIComponent(state.selected)}` : "/upload";
  elements.uploadSettingsLink.href = elements.openUploadCenter.href;
  const selectedBusy = [...state.jobs.values()].some((job) => job.project === state.selected && ["queued", "running", "cancelling"].includes(job.status));
  elements.startRender.disabled = !state.selected || selectedBusy;
}

function renderProjects() {
  ensureSelection();
  elements.projectCount.textContent = state.projects.length;
  elements.pageStatus.textContent = state.projects.length ? `${state.projects.length} dự án sẵn sàng` : "Chưa có dự án";
  elements.projectGrid.innerHTML = state.projects.length ? state.projects.map(cardHtml).join("") : `<div class="empty-card">Chưa có dự án. Bấm “Dự án mới” để bắt đầu.</div>`;
}

function jobUpdatedAt(job) {
  return Number(job.updated_at || job.updatedAt || job.created_at || job.createdAt || 0) || 0;
}

function jobStatusLabel(status) {
  switch (status) {
    case "queued": return "Đang chờ";
    case "running": return "Đang chạy";
    case "cancelling": return "Đang dừng";
    case "done": return "Hoàn tất";
    case "failed": return "Thất bại";
    case "cancelled": return "Đã dừng";
    default: return status || "unknown";
  }
}

function jobStatusClass(status) {
  switch (status) {
    case "queued": return "warn";
    case "running": return "active";
    case "cancelling": return "warn";
    case "done": return "good";
    case "failed": return "error";
    case "cancelled": return "muted";
    default: return "muted";
  }
}

function jobSourceLabel(job) {
  return job.engine || job.options?.source || job.source || "project";
}

function jobActionHtml(job) {
  if (["queued", "running", "cancelling"].includes(job.status)) {
    return `<button class="button tiny danger" data-job-action="cancel" data-job-id="${escapeHtml(job.id)}">Dừng</button>`;
  }
  if (job.outputUrl) {
    return `<a class="button tiny secondary" href="${escapeHtml(job.outputUrl)}" target="_blank">Mở</a>`;
  }
  return `<span>${Number(job.progress) || 0}%</span>`;
}

function renderJobRow(job, active = false) {
  const progress = Math.max(0, Math.min(100, Number(job.progress) || 0));
  return `<div class="job-row ${active ? "job-row-live" : ""}" data-job-id="${escapeHtml(job.id)}">
    <div>
      <strong>${escapeHtml(job.project)} · ${escapeHtml(jobSourceLabel(job))}</strong>
      <small>
        <span class="job-status-badge job-status-${jobStatusClass(job.status)}">${escapeHtml(jobStatusLabel(job.status))}</span>
        <span>${escapeHtml(relativeTime(new Date(jobUpdatedAt(job) || Date.now()).toISOString()))}</span>
      </small>
    </div>
    <div class="mini-progress"><span style="width:${progress}%"></span></div>
    <div class="job-actions">${jobActionHtml(job)}</div>
  </div>`;
}

function renderJobs() {
  if (!elements.activeJobList || !elements.activeJobCount || !elements.jobHistoryList || !elements.historyJobCount) return;
  const jobs = [...state.jobs.values()].sort((a, b) => jobUpdatedAt(b) - jobUpdatedAt(a));
  const activeJobs = jobs.filter((job) => ["queued", "running", "cancelling"].includes(job.status));
  const historyJobs = jobs.filter((job) => !["queued", "running", "cancelling"].includes(job.status));
  elements.activeJobCount.innerHTML = `<strong>${activeJobs.length}</strong>`;
  elements.historyJobCount.innerHTML = `<strong>${historyJobs.length}</strong>`;
  elements.activeJobList.innerHTML = activeJobs.length ? activeJobs.map((job) => renderJobRow(job, true)).join("") : `<p class="empty">Chưa có job đang chạy.</p>`;
  elements.jobHistoryList.innerHTML = historyJobs.length ? historyJobs.map((job) => renderJobRow(job)).join("") : `<p class="empty">Chưa có tác vụ trong phiên này.</p>`;
}

function syncActiveJobs(jobs = [...state.jobs.values()]) {
  const active = jobs.filter((job) => ["queued", "running", "cancelling"].includes(job.status));
  state.activeJobs = active.map((job) => job.id);
  const preferred = active.find((job) => job.project === state.selected) || active[0] || jobs[0] || null;
  state.activeJob = preferred?.id || null;
  return preferred;
}

async function loadElevenConfig() {
  try {
    const config = await api("/api/tts/elevenlabs/config");
    elements.elevenVoiceId.value = config.voiceId;
    elements.elevenModelId.value = config.modelId;
    elements.elevenConfigState.textContent = config.apiKeyConfigured
      ? "API key đã được cấu hình."
      : "Chưa có API key.";
  } catch (error) { elements.elevenConfigState.textContent = error.message; }
}

async function loadAurexttsConfig() {
  try {
    const config = await api("/api/tts/aurextts/config");
    elements.aurexttsBaseUrl.value = config.baseUrl;
    elements.aurexttsProvider.value = config.provider;
    elements.aurexttsVoice.value = config.voice;
    elements.aurexttsStyle.value = config.style;
    elements.aurexttsTtsSpeed.value = String(config.speed);
    elements.aurexttsConfigState.textContent = "Cấu hình AurexTTS đã sẵn sàng.";
  } catch (error) { elements.aurexttsConfigState.textContent = error.message; }
}

async function saveAurexttsConfig(showMessage = true) {
  const payload = {
    baseUrl: elements.aurexttsBaseUrl.value.trim(),
    provider: elements.aurexttsProvider.value,
    voice: elements.aurexttsVoice.value.trim(),
    style: elements.aurexttsStyle.value,
    speed: Number(elements.aurexttsTtsSpeed.value),
  };
  const config = await api("/api/tts/aurextts/config", { method: "POST", body: JSON.stringify(payload) });
  elements.aurexttsConfigState.textContent = "Đã lưu cấu hình AurexTTS.";
  if (showMessage) showToast("Đã lưu cấu hình AurexTTS.");
  return config;
}

async function checkAurexttsServer() {
  try {
    const health = await api("/api/tts/aurextts/health");
    if (!health.ok) {
      elements.aurexttsConfigState.textContent = health.error || "Server không khả dụng.";
      return health;
    }
    const voiceIds = (health.voices || []).map((voice) => String(voice?.id ?? voice ?? ""));
    const known = ["omnivoice-auto", "chau-tinh-tri", "Phạm Tuyên"].filter((voice) => voiceIds.includes(voice));
    elements.aurexttsConfigState.textContent = 
      `Server OK · provider ${health.provider} · ${health.voices?.length || 0} giọng` +
      (known.length ? ` · có sẵn: ${known.join(", ")}` : "");
    return health;
  } catch (error) {
    elements.aurexttsConfigState.textContent = error.message;
    return null;
  }
}

async function loadSocialStatus() {
  try {
    const status = await api("/api/social/status");
    const youtube = status.platforms?.youtube || {};
    const facebook = status.platforms?.facebook || {};
    elements.socialState.textContent = `YouTube: ${youtube.connected ? `${(youtube.channels || []).length} kênh` : "chưa kết nối"} · Facebook: ${facebook.connected ? `${(facebook.pages || []).length} Page` : "chưa kết nối"}`;
  } catch (error) {
    elements.socialState.textContent = `Không đọc được cấu hình upload: ${error.message}`;
  }
}

async function loadPage() {
  elements.pageStatus.textContent = "Đang tải...";
  try {
    const [health, projects, jobs] = await Promise.all([api("/api/health"), api("/api/projects"), api("/api/jobs")]);
    renderDependencies(health.dependencies);
    state.projects = projects.projects;
    state.jobs = new Map(jobs.jobs.map((job) => [job.id, job]));
    syncActiveJobs(jobs.jobs);
    renderProjects();
    renderJobs();
    if (state.activeJob) showActiveJob(state.jobs.get(state.activeJob));
    scheduleJobPoll();
  } catch (error) {
    elements.pageStatus.textContent = "Không tải được dữ liệu";
    showToast(error.message, true);
  }
}

function renderCharacterSelect(selected = "") {
  const characters = state.characters;
  elements.characterSelect.innerHTML = characters.map((item) => `<option value="${escapeHtml(item.id)}" ${item.id === selected ? "selected" : ""}>${escapeHtml(item.name)}</option>`).join("");
  const active = characters.find((item) => item.id === elements.characterSelect.value) || characters[0];
  elements.characterCover.src = active?.coverUrl || "";
  elements.characterCover.hidden = !active;
  const usedBy = active?.usedBy || [];
  elements.characterMeta.textContent = active
    ? `${active.poseCount} dáng pose · ${usedBy.length ? `đang dùng ở ${usedBy.length} project (khoá sửa)` : "dùng lại cho mọi dự án"}`
    : "Chưa có nhân vật trong thư viện";
  if (elements.editCharacterButton) elements.editCharacterButton.disabled = !active || usedBy.length > 0;
}

async function loadCharacters(selected = "") {
  const payload = await api("/api/characters");
  state.characters = payload.characters || [];
  renderCharacterSelect(selected || elements.characterSelect.value || payload.defaultCharacterId || "");
}

function slugify(value) {
  return String(value || "").normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase().replace(/đ/g, "d").replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 48);
}

function renderPoseDraft() {
  const poses = state.characterDraft?.poses || [];
  elements.poseDraftGrid.innerHTML = poses.map((pose, index) => `<label class="pose-draft-card"><img src="${escapeHtml(pose.url)}" alt="Pose ${index + 1}" /><span>Dáng ${index + 1}</span><input data-pose-name required value="Dáng ${index + 1}" placeholder="Tên dáng pose" /></label>`).join("");
  elements.saveCharacterButton.disabled = !poses.length;
}

async function splitCharacterSheet(file) {
  elements.characterSplitStatus.textContent = "Đang nhận diện và tách các dáng...";
  elements.saveCharacterButton.disabled = true;
  const result = await api("/api/characters/split", { method: "POST", body: JSON.stringify({ name: file.name, data: await fileToDataUrl(file) }) });
  state.characterDraft = result;
  renderPoseDraft();
  elements.characterSplitStatus.textContent = `Đã tách ${result.poses.length} dáng trên cùng khung ${result.width} × ${result.height}px.`;
}

async function saveCharacter(event) {
  event.preventDefault();
  if (!state.characterDraft) return;
  const poseNames = [...elements.poseDraftGrid.querySelectorAll("[data-pose-name]")].map((input) => input.value.trim());
  elements.saveCharacterButton.disabled = true;
  try {
    const result = await api("/api/characters", { method: "POST", body: JSON.stringify({
      token: state.characterDraft.token,
      id: elements.characterId.value.trim(),
      name: elements.characterName.value.trim(),
      poseNames,
    }) });
    state.characterDraft = null;
    await loadCharacters(result.character.id);
    elements.characterDialog.close();
    elements.projectDialog.showModal();
    showToast(`Đã lưu nhân vật “${result.character.name}”.`);
  } catch (error) {
    showToast(error.message, true);
    elements.saveCharacterButton.disabled = false;
  }
}

function renderCharacterEdit() {
  const edit = state.characterEdit;
  if (!edit) return;
  const focusOptions = (value) => ["left", "center", "right"]
    .map((side) => `<option value="${side}" ${value === side ? "selected" : ""}>${side === "left" ? "Trái" : side === "right" ? "Phải" : "Giữa"}</option>`)
    .join("");
  elements.characterEditGrid.innerHTML = edit.poses.map((pose, index) => `
    <div class="pose-draft-card" data-pose-index="${index}">
      <img src="/assets/characters/${encodeURIComponent(edit.id)}/${encodeURIComponent(pose.file)}" alt="Pose ${index + 1}" />
      <input data-edit-pose-name required value="${escapeHtml(pose.label || `Dáng ${index + 1}`)}" placeholder="Tên dáng pose" />
      <label class="pose-focus-field">Hướng<select data-edit-pose-focus>${focusOptions(pose.focusSide || "center")}</select></label>
      <button class="button tiny danger" type="button" data-remove-pose="${index}">Xoá pose</button>
    </div>`).join("");
  elements.saveCharacterEditButton.disabled = edit.poses.length === 0;
}

async function openCharacterEditor() {
  const characterId = elements.characterSelect.value;
  const character = state.characters.find((item) => item.id === characterId);
  if (!character) { showToast("Chưa chọn nhân vật.", true); return; }
  if ((character.usedBy || []).length) {
    showToast(`Nhân vật đang được dùng trong project: ${character.usedBy.join(", ")}. Đổi nhân vật của project trước khi sửa.`, true);
    return;
  }
  state.characterEdit = {
    id: character.id,
    originalCount: (character.poses || []).length,
    poses: (character.poses || []).map((pose, index) => ({
      id: pose.id,
      index,
      label: pose.label || pose.id,
      file: pose.file,
      focusSide: ["left", "right", "center"].includes(pose.focusSide) ? pose.focusSide : "center",
    })),
  };
  elements.characterEditStatus.textContent = `${character.name || character.id} · ${state.characterEdit.poses.length} pose`;
  renderCharacterEdit();
  elements.projectDialog.close();
  elements.characterEditDialog.showModal();
}

async function saveCharacterEdit(event) {
  event.preventDefault();
  const edit = state.characterEdit;
  if (!edit) return;
  const cards = [...elements.characterEditGrid.querySelectorAll("[data-pose-index]")];
  const kept = cards.map((card) => ({
    index: edit.poses[Number(card.dataset.poseIndex)].index,
    name: card.querySelector("[data-edit-pose-name]").value.trim(),
    focusSide: card.querySelector("[data-edit-pose-focus]").value,
  }));
  // The backend indexes against the ORIGINAL manifest order, so rebuild
  // full-length arrays and list removed original indexes explicitly.
  const poseNames = [];
  const poseFocusSides = [];
  const removedPoses = [];
  for (let index = 0; index < edit.originalCount; index += 1) {
    const match = kept.find((item) => item.index === index);
    poseNames.push(match ? match.name : `pose-${index + 1}`);
    poseFocusSides.push(match ? match.focusSide : "center");
    if (!match) removedPoses.push(index);
  }
  elements.saveCharacterEditButton.disabled = true;
  try {
    const result = await api(`/api/characters/${encodeURIComponent(edit.id)}`, {
      method: "PUT",
      body: JSON.stringify({ id: edit.id, poseNames, poseFocusSides, removedPoses }),
    });
    state.characterEdit = null;
    await loadCharacters(result.character.id);
    elements.characterEditDialog.close();
    showToast(`Đã cập nhật nhân vật “${result.character.name || result.character.id}”.`);
  } catch (error) {
    showToast(error.message, true);
  } finally {
    elements.saveCharacterEditButton.disabled = false;
  }
}

function scheduleJobPoll() {
  clearTimeout(state.poller);
  if (!state.activeJobs.length) return;
  state.poller = setTimeout(pollJobs, 1000);
}

function showActiveJob(job) {
  if (!job) return;
  elements.renderLive.hidden = false;
  elements.renderLiveTitle.textContent = job.status === "done" ? "Render hoàn tất" : job.status === "failed" ? "Render thất bại" : job.status === "cancelled" ? "Đã dừng render" : "Đang render...";
  elements.renderPercent.textContent = `${job.progress || 0}%`;
  elements.renderProgress.style.width = `${job.progress || 0}%`;
  elements.renderLogs.textContent = job.logs || "Đang chuẩn bị...";
  elements.renderLogs.scrollTop = elements.renderLogs.scrollHeight;
  elements.renderOutput.hidden = !job.outputUrl;
  elements.renderUpload.hidden = !job.outputUrl;
  if (job.outputUrl) elements.renderOutput.href = job.outputUrl;
  if (job.outputUrl) elements.renderUpload.href = `/upload?project=${encodeURIComponent(job.project)}`;
  const active = ["queued", "running", "cancelling"].includes(job.status);
  elements.startRender.hidden = active;
  elements.stopRender.hidden = !active;
}

async function pollJobs() {
  try {
    const payload = await api("/api/jobs");
    const jobs = payload.jobs || [];
    state.jobs = new Map(jobs.map((job) => [job.id, job]));
    const liveJob = syncActiveJobs(jobs);
    if (liveJob) showActiveJob(liveJob);
    renderJobs();
    elements.startRender.disabled = !state.selected || [...state.jobs.values()].some((job) => job.project === state.selected && ["queued", "running", "cancelling"].includes(job.status));
    elements.stopRender.hidden = !state.activeJobs.length;
    scheduleJobPoll();
  } catch (error) { showToast(error.message, true); }
}

async function loadProjectsOnly() {
  const payload = await api("/api/projects");
  state.projects = payload.projects;
  renderProjects();
}

async function saveElevenConfig(showMessage = true) {
  const payload = {
    apiKey: elements.elevenApiKey.value.trim(),
    voiceId: elements.elevenVoiceId.value.trim(),
    modelId: elements.elevenModelId.value.trim(),
  };
  const config = await api("/api/tts/elevenlabs/config", { method: "POST", body: JSON.stringify(payload) });
  elements.elevenApiKey.value = "";
  elements.elevenConfigState.textContent = config.apiKeyConfigured ? "API key và Voice ID đã sẵn sàng." : "Chưa có API key.";
  if (showMessage) showToast("Đã lưu cấu hình ElevenLabs.");
  return config;
}

function fileToDataUrl(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result);
    reader.onerror = reject;
    reader.readAsDataURL(file);
  });
}

async function startRender() {
  if (!state.selected) return showToast("Hãy chọn một dự án.", true);
  elements.startRender.disabled = true;
  try {
    let source;
    const payload = {
      speed: Number(elements.renderSpeed.value),
      volume: Number(elements.renderVolume.value),
      size: elements.renderSize.value,
      uploadYoutube: false,
      uploadFacebook: false,
    };
    if (state.engine === "edge" || state.engine === "edgetts") {
      source = "edge";
      payload.voice = elements.edgeVoice.value.trim();
    } else if (state.engine === "aurextts") {
      await saveAurexttsConfig(false);
      source = "aurextts";
      payload.voice = elements.aurexttsVoice.value.trim();
      payload.provider = elements.aurexttsProvider.value;
      payload.style = elements.aurexttsStyle.value;
      payload.ttsSpeed = Number(elements.aurexttsTtsSpeed.value);
    } else {
      const mode = document.querySelector('input[name="elevenMode"]:checked').value;
      if (mode === "api") {
        await saveElevenConfig(false);
        source = "elevenlabs";
        payload.voiceId = elements.elevenVoiceId.value.trim();
        payload.modelId = elements.elevenModelId.value.trim();
      } else if (elements.renderAudioFile.files[0]) {
        source = "upload";
        const file = elements.renderAudioFile.files[0];
        const uploaded = await api(`/api/projects/${encodeURIComponent(state.selected)}/upload`, {
          method: "POST",
          body: JSON.stringify({ kind: "voiceover", name: file.name, data: await fileToDataUrl(file) }),
        });
        payload.audioPath = uploaded.path;
      } else source = "project";
    }
    payload.source = source;
    const result = await api(`/api/projects/${encodeURIComponent(state.selected)}/render`, { method: "POST", body: JSON.stringify(payload) });
    await loadProjectsOnly();
    state.jobs.set(result.job.id, result.job);
    syncActiveJobs([...state.jobs.values()]);
    state.activeJob = result.job.id;
    state.jobs.set(result.job.id, result.job);
    showActiveJob(result.job);
    renderJobs();
    scheduleJobPoll();
  } catch (error) {
    elements.startRender.disabled = false;
    showToast(error.message, true);
  }
}

async function cancelJob(jobId, button = null) {
  if (!jobId) return null;
  if (button) button.disabled = true;
  try {
    const job = await api(`/api/jobs/${jobId}/cancel`, { method: "POST", body: "{}" });
    state.jobs.set(job.id, job);
    syncActiveJobs([...state.jobs.values()]);
    showActiveJob(state.jobs.get(state.activeJob) || job);
    renderJobs();
    scheduleJobPoll();
    return job;
  } finally {
    if (button) button.disabled = false;
  }
}

async function stopRender() {
  if (!state.activeJob) return;
  elements.stopRender.disabled = true;
  try {
    await cancelJob(state.activeJob);
  } catch (error) { showToast(error.message, true); }
  finally { elements.stopRender.disabled = false; }
}

async function submitProject(event) {
  event.preventDefault();
  try {
    const result = await api("/api/projects", { method: "POST", body: JSON.stringify(Object.fromEntries(new FormData(elements.projectForm))) });
    window.location.href = `/editor?project=${encodeURIComponent(result.project.id)}`;
  } catch (error) { showToast(error.message, true); }
}

async function submitDuplicate(event) {
  event.preventDefault();
  const data = Object.fromEntries(new FormData(elements.duplicateForm));
  try {
    const result = await api(`/api/projects/${encodeURIComponent(data.source)}/duplicate`, { method: "POST", body: JSON.stringify({ id: data.id }) });
    window.location.href = `/editor?project=${encodeURIComponent(result.project.id)}`;
  } catch (error) { showToast(error.message, true); }
}

async function projectAction(event) {
  const button = event.target.closest("button[data-action]");
  if (!button) return;
  const slug = button.closest("[data-project]").dataset.project;
  if (button.dataset.action === "select") {
    state.selected = slug;
    renderProjects();
    return;
  }
  if (button.dataset.action === "duplicate") {
    elements.duplicateForm.elements.source.value = slug;
    elements.duplicateForm.elements.id.value = `${slug}-copy`;
    elements.duplicateDialog.showModal();
    return;
  }
  if (!window.confirm(`Xoá dự án “${slug}” và toàn bộ output của nó?`)) return;
  try {
    await api(`/api/projects/${encodeURIComponent(slug)}`, { method: "DELETE" });
    if (state.selected === slug) state.selected = "";
    await loadProjectsOnly();
    showToast("Đã xoá dự án.");
  } catch (error) { showToast(error.message, true); }
}

async function handleJobListAction(event) {
  const button = event.target.closest("button[data-job-action='cancel']");
  if (!button) return;
  cancelJob(button.dataset.jobId, button).catch((error) => showToast(error.message, true));
}
document.querySelectorAll("[data-close-dialog]").forEach((button) => button.addEventListener("click", () => button.closest("dialog").close()));
document.querySelectorAll("[data-engine]").forEach((button) => button.addEventListener("click", () => {
  state.engine = button.dataset.engine;
  document.querySelectorAll("[data-engine]").forEach((item) => item.classList.toggle("active", item === button));
  document.querySelectorAll("[data-pane]").forEach((pane) => {
    pane.hidden = pane.dataset.pane !== state.engine;
  });
  document.querySelectorAll("[data-advanced-engine]").forEach((pane) => {
    pane.hidden = pane.dataset.advancedEngine !== state.engine;
  });
}));
document.querySelectorAll('input[name="maziaoMode"]').forEach((radio) => radio.addEventListener("change", () => {
  document.querySelectorAll("[data-maziao-mode]").forEach((pane) => { pane.hidden = pane.dataset.maziaoMode !== radio.value; });
}));
document.querySelectorAll("[data-speed]").forEach((button) => button.addEventListener("click", () => {
  elements.renderSpeed.value = button.dataset.speed;
  document.querySelectorAll("[data-speed]").forEach((item) => item.classList.toggle("active", item === button));
}));
elements.renderAudioFile.addEventListener("change", () => { elements.renderAudioName.textContent = elements.renderAudioFile.files[0]?.name || "Chưa chọn file — sẽ dùng audio trong project"; });
elements.newProjectButton.addEventListener("click", async () => {
  try { await loadCharacters(); } catch (error) { showToast(error.message, true); }
  elements.projectDialog.showModal();
});
elements.characterSelect.addEventListener("change", () => renderCharacterSelect(elements.characterSelect.value));
elements.createCharacterButton.addEventListener("click", () => {
  elements.projectDialog.close();
  state.characterDraft = null;
  elements.characterForm.reset();
  elements.poseDraftGrid.replaceChildren();
  elements.characterSplitStatus.textContent = "Các dáng phải nằm trên một hàng và không chạm nhau.";
  elements.saveCharacterButton.disabled = true;
  elements.characterDialog.showModal();
});
elements.copyCharacterPrompt.addEventListener("click", async () => {
  try { await navigator.clipboard.writeText(elements.characterPrompt.value); showToast("Đã copy prompt tạo nhân vật."); }
  catch (_) { elements.characterPrompt.select(); document.execCommand("copy"); showToast("Đã copy prompt tạo nhân vật."); }
});
elements.characterSheetFile.addEventListener("change", () => {
  const file = elements.characterSheetFile.files[0];
  if (file) splitCharacterSheet(file).catch((error) => { elements.characterSplitStatus.textContent = error.message; showToast(error.message, true); });
});
elements.characterName.addEventListener("input", () => {
  if (!elements.characterId.dataset.edited) elements.characterId.value = slugify(elements.characterName.value);
});
elements.characterId.addEventListener("input", () => { elements.characterId.dataset.edited = "1"; });
elements.characterForm.addEventListener("submit", saveCharacter);
if (elements.editCharacterButton) {
  elements.editCharacterButton.addEventListener("click", () => {
    openCharacterEditor().catch((error) => showToast(error.message, true));
  });
}
if (elements.characterEditForm) elements.characterEditForm.addEventListener("submit", saveCharacterEdit);
if (elements.characterEditGrid) {
  elements.characterEditGrid.addEventListener("click", (event) => {
    const button = event.target.closest("[data-remove-pose]");
    if (!button || !state.characterEdit) return;
    const index = Number(button.dataset.removePose);
    if (state.characterEdit.poses.length <= 1) {
      showToast("Nhân vật phải giữ lại ít nhất một pose.", true);
      return;
    }
    state.characterEdit.poses.splice(index, 1);
    renderCharacterEdit();
  });
}
elements.refreshButton.addEventListener("click", loadPage);
elements.projectForm.addEventListener("submit", submitProject);
elements.duplicateForm.addEventListener("submit", submitDuplicate);
elements.projectGrid.addEventListener("click", projectAction);
if (elements.activeJobList) elements.activeJobList.addEventListener("click", handleJobListAction);
if (elements.jobHistoryList) elements.jobHistoryList.addEventListener("click", handleJobListAction);
elements.saveElevenConfig.addEventListener("click", () => saveElevenConfig().catch((error) => showToast(error.message, true)));
elements.checkAurextts.addEventListener("click", () => checkAurexttsServer().catch((error) => showToast(error.message, true)));
elements.startRender.addEventListener("click", startRender);
elements.stopRender.addEventListener("click", stopRender);

// Open Settings inside a modal (same-origin iframe) so Tauri WebView does not
// block top-level navigation.
(function setupSettingsModal() {
  const dialog = document.getElementById("settingsDialog");
  const frame = document.getElementById("settingsFrame");
  if (!dialog || !frame) return;
  elements.openSettings.addEventListener("click", () => {
    frame.src = "/settings";
    dialog.showModal();
  });
  dialog.querySelectorAll("[data-close-settings]").forEach((btn) => {
    btn.addEventListener("click", () => dialog.close());
  });
})();

loadElevenConfig();
loadAurexttsConfig();
loadSocialStatus();
loadCharacters().catch(() => {});
loadPage();
