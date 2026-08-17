const pathProject = window.location.pathname.match(/^\/project\/([^/]+)\/$/)?.[1] || "";
const project = new URLSearchParams(window.location.search).get("project") || decodeURIComponent(pathProject);
const state = { topic: null, characters: [], dirty: false, revision: 0, saving: false, previewTime: 0, previewSegmentIndex: -1, previewComparisonId: "", poseBySegment: [], speakerBySegment: [], renderedSegments: [], activeJob: null, pendingUploads: {}, imageDrag: null, cropTarget: null, cropOriginalSrc: null, cropZoom: 1, cropPanX: 0, cropPanY: 0, cropSelection: null, cropDisplay: null, cropDrag: null, cropRotateMode: false, cropAspectLocked: true, cropFrameAspect: 1, activePoseSfx: "neutral-left" };

function isEnglishUi() {
  return (window.__AUREX_LANGUAGE__ || document.documentElement.lang) === "en";
}

function tr(vi, en) {
  return isEnglishUi() ? en : vi;
}

const DEFAULT_LABEL_A = () => tr("Nội dung A", "Content A");
const DEFAULT_LABEL_B = () => tr("Nội dung B", "Content B");
const DEFAULT_SUBLABEL_COLOR = "#808080";
const DEFAULT_LABEL_FONT_FAMILY = '"Inter", -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif';
const DEFAULT_PASTE_IMAGE_MODE = "square";
const STARTER_SCRIPT = () => tr("Nhập nội dung đầu tiên tại đây.", "Enter your first line here.");

function hasSubLabelRow(source) {
  if (!source) return false;
  if (source.showSubLabels === true) return true;
  return Boolean(String(source.leftSubLabel || "").trim() || String(source.rightSubLabel || "").trim());
}

// Bundled Vietnamese-capable catalog. Kept in sync with
// m3_backend.LABEL_FONT_CATALOG so the editor, preview and render agree.
const LABEL_FONT_CATALOG = [
  { name: "Inter", stack: '"Inter", -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif' },
  { name: "Be Vietnam Pro", stack: '"Be Vietnam Pro", "Inter", sans-serif' },
  { name: "Manrope", stack: '"Manrope", "Inter", sans-serif' },
  { name: "Lexend", stack: '"Lexend", "Inter", sans-serif' },
  { name: "Nunito", stack: '"Nunito", "Inter", sans-serif' },
  { name: "Quicksand", stack: '"Quicksand", "Inter", sans-serif' },
  { name: "Saira", stack: '"Saira", "Inter", sans-serif' },
  { name: "Roboto", stack: '"Roboto", "Inter", sans-serif' },
  { name: "Literata", stack: '"Literata", Georgia, serif' },
  { name: "Playfair Display", stack: '"Playfair Display", Georgia, serif' },
];
// Legacy stacks render Vietnamese diacritics badly on Windows.
const LEGACY_LABEL_FONTS = {
  'arial, sans-serif': '"Be Vietnam Pro", "Inter", sans-serif',
  'georgia, serif': '"Literata", Georgia, serif',
  '"times new roman", times, serif': '"Literata", Georgia, serif',
};

function normalizeLabelFontFamily(value) {
  const next = String(value || "").trim();
  if (LABEL_FONT_CATALOG.some((item) => item.stack === next)) return next;
  return LEGACY_LABEL_FONTS[next.toLowerCase()] || DEFAULT_LABEL_FONT_FAMILY;
}

function labelFontOptionsHtml(selected) {
  const current = normalizeLabelFontFamily(selected);
  return LABEL_FONT_CATALOG
    .map((item) => `<option value='${item.stack.replace(/'/g, "&#39;")}' ${item.stack === current ? "selected" : ""}>${item.name}</option>`)
    .join("");
}

function populateLabelFontSelect(select, selected) {
  if (!select) return;
  select.innerHTML = labelFontOptionsHtml(selected);
  select.value = normalizeLabelFontFamily(selected);
}

async function rememberedLabelFontFor(characterId) {
  try {
    const response = await fetch(`/api/label-fonts?characterId=${encodeURIComponent(characterId || "")}`, { cache: "no-store" });
    if (!response.ok) return "";
    const data = await response.json();
    return normalizeLabelFontFamily(data?.default);
  } catch (error) {
    return "";
  }
}

async function rememberedLabelColorFor(characterId) {
  try {
    const response = await fetch(`/api/label-colors?characterId=${encodeURIComponent(characterId || "")}`, { cache: "no-store" });
    if (!response.ok) return "";
    const data = await response.json();
    return String(data?.default || "").trim();
  } catch (error) {
    return "";
  }
}

const POSE_LABEL_EN = {
  "Chỉ trái · không cười": "Point left · no smile",
  "Chỉ phải · không cười": "Point right · no smile",
  "Thắc mắc · dấu hỏi": "Curious · question mark",
  "Chỉ trái · cười": "Point left · smile",
  "Chỉ phải · cười": "Point right · smile",
  "neutral-left": "Point left · no smile",
  "neutral-right": "Point right · no smile",
  "question": "Curious · question mark",
  "smile-left": "Point left · smile",
  "smile-right": "Point right · smile",
};

function localizedPoseLabel(id, label) {
  const raw = String(label || id || "").trim();
  if (!isEnglishUi()) return raw || id;
  return POSE_LABEL_EN[raw] || POSE_LABEL_EN[id] || raw || id;
}

function normalizePasteImageMode(value) {
  return value === "original" ? "original" : DEFAULT_PASTE_IMAGE_MODE;
}

function currentPasteImageMode() {
  return normalizePasteImageMode(state.topic?.pasteImageMode);
}

function syncPasteImageModeControls() {
  const selected = currentPasteImageMode();
  document.querySelectorAll("[data-paste-image-mode]").forEach((button) => {
    const active = button.dataset.pasteImageMode === selected;
    button.classList.toggle("active", active);
    button.setAttribute("aria-pressed", active ? "true" : "false");
  });
}

function setPasteImageMode(value) {
  if (!state.topic) return;
  state.topic.pasteImageMode = normalizePasteImageMode(value);
  syncPasteImageModeControls();
  markDirty();
  scheduleAutoSave(120);
  showToast(state.topic.pasteImageMode === "square"
    ? tr("Ảnh dán mới sẽ tự căn khung vuông 1:1.", "New pasted images will use a 1:1 frame.")
    : tr("Ảnh dán mới sẽ giữ ảnh gốc.", "New pasted images will keep the original image."));
}

function localizeStarterContent(topic) {
  if (!topic || !isEnglishUi()) return false;
  let changed = false;
  if (topic.leftLabel === "Nội dung A") { topic.leftLabel = "Content A"; changed = true; }
  if (topic.rightLabel === "Nội dung B") { topic.rightLabel = "Content B"; changed = true; }
  const starterVi = "Nhập nội dung đầu tiên tại đây.";
  const starterEn = "Enter your first line here.";
  if (Array.isArray(topic.segments)) {
    topic.segments = topic.segments.map((segment) => {
      if (String(segment.text || "").trim() !== starterVi) return segment;
      changed = true;
      return { ...segment, text: starterEn };
    });
  }
  if (topic.poseLabels && typeof topic.poseLabels === "object") {
    const next = {};
    Object.entries(topic.poseLabels).forEach(([id, label]) => {
      const localized = localizedPoseLabel(id, label);
      next[id] = localized;
      if (localized !== label) changed = true;
    });
    topic.poseLabels = next;
  }
  return changed;
}

const elements = Object.fromEntries([
  "projectTitle", "saveState", "previewFrame", "phoneFrame", "previewTime", "leftLabelInput", "rightLabelInput",
  "leftThumb", "rightThumb", "leftViewport", "rightViewport", "leftImageFile", "rightImageFile", "deleteLeftImage", "deleteRightImage",
  "leftImageZoom", "rightImageZoom", "leftZoomText", "rightZoomText",
  "leftLabelColor", "rightLabelColor", "leftSubLabelInput", "rightSubLabelInput", "leftSubLabelColor", "rightSubLabelColor",
  "labelFontFamily",
  "primarySubLabelRow",
  "comparisonList", "addComparisonButton", "addSingleImageButton", "deleteBaseComparison",
  "backgroundType", "backgroundColor", "backgroundColorField", "backgroundImagePanel",
  "backgroundThumb", "backgroundViewport", "backgroundImageFile", "deleteBackgroundImage",
  "backgroundImageZoom", "backgroundZoomText",
  "backgroundMusicName", "backgroundMusicFile", "deleteBackgroundMusic",
  "backgroundMusicVolume", "backgroundMusicVolumeText",
  "karaokeActiveColor", "karaokeColor", "karaokeSize", "karaokeSizeText",
  "characterSelect", "characterPreview", "characterMeta", "characterPoseCount",
  "scriptInput", "poseList", "poseSfxMap", "autoPoseButton", "customSfxFile", "sfxVolumeInput", "sfxVolumeText", "editorForm",
  "saveButton", "renderButton", "minusButton", "plusButton", "jobDialog", "jobKicker", "jobTitle",
  "jobProgress", "jobMessage", "jobLogs", "jobOutput", "cropDialog", "cropStage", "cropImage", "cropSelection",
  "cropSizeText", "cropZoomInput", "cropZoomText", "closeCropDialog", "cropRotateLayer", "toggleCropRotate", "toggleCropAspect", "resetCrop", "applyCrop", "toast",
].map((id) => [id, document.querySelector(`#${id}`)]));

const IMAGE_SIDES = {
  left: { zoom: "leftImageZoom", x: "leftImageX", y: "leftImageY", thumb: "leftThumb", viewport: "leftViewport", zoomInput: "leftImageZoom", zoomText: "leftZoomText" },
  right: { zoom: "rightImageZoom", x: "rightImageX", y: "rightImageY", thumb: "rightThumb", viewport: "rightViewport", zoomInput: "rightImageZoom", zoomText: "rightZoomText" },
  background: {
    zoom: "backgroundImageZoom",
    x: "backgroundImageX",
    y: "backgroundImageY",
    thumb: "backgroundThumb",
    viewport: "backgroundViewport",
    zoomInput: "backgroundImageZoom",
    zoomText: "backgroundZoomText",
  },
};

const DEFAULT_BACKGROUND_COLOR = "#f5eee3";
const DEFAULT_BACKGROUND_MUSIC_VOLUME = 0.18;

function backgroundMusicFileLabel(path) {
  const raw = String(path || "").trim();
  if (!raw) return tr("Chưa có file nhạc", "No music file yet");
  const name = raw.split("/").pop() || raw;
  return name.replace(/^backgroundMusic-\d+-/, "").replace(/^background-music-/, "") || name;
}

function hasBackgroundMusic() {
  return Boolean(String(state.topic?.backgroundMusic || "").trim() || state.pendingUploads.backgroundMusic);
}

function syncBackgroundMusicControls() {
  if (!state.topic) return;
  const path = String(state.topic.backgroundMusic || "").trim();
  const hasPending = Boolean(state.pendingUploads.backgroundMusic);
  const hasMusic = hasBackgroundMusic();
  state.topic.backgroundMusicEnabled = hasMusic;
  if (elements.backgroundMusicName) {
    elements.backgroundMusicName.textContent = hasPending
      ? (state.pendingUploads.backgroundMusic.name || tr("Nhạc vừa chọn", "Selected music"))
      : backgroundMusicFileLabel(path);
  }
  const volume = clamp(Number(state.topic.backgroundMusicVolume ?? DEFAULT_BACKGROUND_MUSIC_VOLUME) || DEFAULT_BACKGROUND_MUSIC_VOLUME, 0.05, 0.5);
  if (elements.backgroundMusicVolume) elements.backgroundMusicVolume.value = String(volume);
  if (elements.backgroundMusicVolumeText) elements.backgroundMusicVolumeText.textContent = `${Math.round(volume * 100)}%`;
  if (elements.deleteBackgroundMusic) elements.deleteBackgroundMusic.disabled = !hasMusic;
}

function setBackgroundMusicFile(file) {
  if (!state.topic || !file) return;
  state.pendingUploads.backgroundMusic = file;
  if (elements.backgroundMusicFile) elements.backgroundMusicFile.value = "";
  state.topic.backgroundMusicEnabled = true;
  syncBackgroundMusicControls();
  markDirty();
  sendDraftToPreview();
  scheduleAutoSave(80);
}

function clearBackgroundMusic() {
  if (!state.topic) return;
  delete state.pendingUploads.backgroundMusic;
  state.topic.backgroundMusic = "";
  state.topic.backgroundMusicEnabled = false;
  if (elements.backgroundMusicFile) elements.backgroundMusicFile.value = "";
  stopStepPreviewBackgroundMusic();
  syncBackgroundMusicControls();
  markDirty();
  sendDraftToPreview();
  scheduleAutoSave(120);
}

function syncBackgroundControls() {
  if (!state.topic || !elements.backgroundType) return;
  const type = String(state.topic.backgroundType || "default");
  elements.backgroundType.value = ["default", "color", "image"].includes(type) ? type : "default";
  if (elements.backgroundColor) {
    elements.backgroundColor.value = state.topic.backgroundColor || DEFAULT_BACKGROUND_COLOR;
  }
  if (elements.backgroundColorField) {
    elements.backgroundColorField.hidden = false;
  }
  if (elements.backgroundImagePanel) {
    elements.backgroundImagePanel.hidden = elements.backgroundType.value !== "image";
  }
  if (elements.backgroundThumb) {
    const path = String(state.topic.backgroundImage || "").trim();
    const hasPending = Boolean(state.pendingUploads.backgroundImage);
    const nextSrc = path ? assetUrl(path) : "";
    if (path && !hasPending) {
      if (elements.backgroundThumb.getAttribute("src") !== nextSrc) {
        elements.backgroundThumb.src = nextSrc;
      }
      elements.backgroundThumb.hidden = false;
    } else if (hasPending) {
      elements.backgroundThumb.hidden = false;
    } else {
      elements.backgroundThumb.removeAttribute("src");
      elements.backgroundThumb.hidden = true;
      elements.backgroundThumb.style.width = "";
      elements.backgroundThumb.style.height = "";
      elements.backgroundThumb.style.transform = "";
    }
  }
  if (elements.backgroundType.value === "image" && !elements.backgroundThumb?.hidden) {
    applyImageFrameToThumb("background");
  }
}

function setBackgroundImageFile(file) {
  if (!state.topic || !file) return;
  state.pendingUploads.backgroundImage = file;
  if (elements.backgroundImageFile) elements.backgroundImageFile.value = "";
  state.topic.backgroundType = "image";
  if (elements.backgroundThumb) {
    elements.backgroundThumb.hidden = false;
    elements.backgroundThumb.src = URL.createObjectURL(file);
  }
  resetImageFrame("background");
  syncBackgroundControls();
  markDirty();
  sendDraftToPreview();
  scheduleAutoSave(80);
}

function clearBackgroundImage() {
  if (!state.topic) return;
  delete state.pendingUploads.backgroundImage;
  state.topic.backgroundImage = "";
  if (elements.backgroundImageFile) elements.backgroundImageFile.value = "";
  if (elements.backgroundThumb) {
    elements.backgroundThumb.removeAttribute("src");
    elements.backgroundThumb.hidden = true;
    elements.backgroundThumb.style.width = "";
    elements.backgroundThumb.style.height = "";
    elements.backgroundThumb.style.transform = "";
  }
  resetImageFrame("background");
  syncBackgroundControls();
  markDirty();
  sendDraftToPreview();
  scheduleAutoSave(120);
}

let POSE_OPTIONS = [
  { id: "neutral-left", label: localizedPoseLabel("neutral-left", "Chỉ trái · không cười"), defaultSfx: "pose-hard-pop-click" },
  { id: "neutral-right", label: localizedPoseLabel("neutral-right", "Chỉ phải · không cười"), defaultSfx: "pose-hard-pop-click" },
  { id: "question", label: localizedPoseLabel("question", "Thắc mắc · dấu hỏi"), defaultSfx: "pose-bubble-pop" },
  { id: "smile-left", label: localizedPoseLabel("smile-left", "Chỉ trái · cười"), defaultSfx: "pose-explainer-pop-whoosh" },
  { id: "smile-right", label: localizedPoseLabel("smile-right", "Chỉ phải · cười"), defaultSfx: "pose-explainer-pop-whoosh" },
];

const CUSTOM_POSE_SEQUENCES = {
  bietchichomet: ["pose-1", "pose-2", "pose-3", "pose-1", "pose-2", "pose-4", "pose-1", "pose-2", "pose-5", "pose-1", "pose-2"],
  july: ["pose-3", "pose-1", "pose-2", "pose-4", "pose-1", "pose-2", "pose-5"],
  popsy: ["pose-9", "pose-1", "pose-2", "pose-3", "pose-4", "pose-5", "pose-6"],
  engzy: ["pose-1", "pose-2", "pose-3", "pose-4", "pose-5", "pose-6"],
};

function poseDisplayOrder(_characterId, ids) {
  return [...ids];
}

function defaultPoseForCharacter(characterId, index, ids) {
  const sequence = CUSTOM_POSE_SEQUENCES[String(characterId || "")];
  if (sequence && sequence.length) {
    const preferred = sequence[index % sequence.length];
    if (preferred && ids.includes(preferred)) return preferred;
  }
  return ids[index % ids.length] || fallbackPose();
}

function configurePoseOptions(topic) {
  const assets = topic?.poseAssets && typeof topic.poseAssets === "object" ? topic.poseAssets : {};
  const labels = topic?.poseLabels && typeof topic.poseLabels === "object" ? topic.poseLabels : {};
  const ids = Object.keys(assets);
  if (!ids.length) return;
  POSE_OPTIONS = ids.map((id, index) => ({
    id,
    label: localizedPoseLabel(id, labels[id] || id.replace(/-/g, " ")),
    defaultSfx: topic.poseSfx?.[id] || ["pose-hard-pop-click", "pose-hard-pop-click", "pose-bubble-pop", "pose-explainer-pop-whoosh", "pose-explainer-pop-whoosh"][index] || "",
  }));
  if (!ids.includes(state.activePoseSfx)) state.activePoseSfx = POSE_OPTIONS[0].id;
}

function characterTopicData(character) {
  const poses = Array.isArray(character?.poses) ? character.poses.filter((pose) => pose?.id && pose?.file) : [];
  return {
    poseAssets: Object.fromEntries(poses.map((pose) => [pose.id, {
      closed: `../../assets/characters/${character.id}/${pose.closedFile || pose.file}`,
      speaking: `../../assets/characters/${character.id}/${pose.speakingFile || pose.file}`,
      focusSide: ["left", "right", "center"].includes(pose.focusSide) ? pose.focusSide : "center",
      syncMode: ["scene", "timeline", "freeze"].includes(pose.syncMode) ? pose.syncMode : "scene",
      loop: pose.loop !== false,
      loopStart: Math.max(0, Number(pose.loopStart) || 0),
      loopEnd: Math.max(0, Number(pose.loopEnd) || 0),
    }])),
    poseLabels: Object.fromEntries(poses.map((pose) => [pose.id, localizedPoseLabel(pose.id, (isEnglishUi() && pose.labelEn) || pose.label || pose.id)])),
  };
}

async function loadCharacters() {
  const result = await api("/api/characters");
  state.characters = Array.isArray(result.characters) ? result.characters : [];
}

function renderCharacterPicker() {
  if (!elements.characterSelect || !state.topic) return;
  const selectedId = String(state.topic.characterId || "");
  const characters = [...state.characters];
  if (selectedId && !characters.some((item) => item.id === selectedId)) {
    characters.unshift({ id: selectedId, name: selectedId, poseCount: Object.keys(state.topic.poseAssets || {}).length, poses: [] });
  }
  elements.characterSelect.innerHTML = characters.map((character) =>
    `<option value="${escapeHtml(character.id)}" ${character.id === selectedId ? "selected" : ""}>${escapeHtml(character.name || character.id)}</option>`
  ).join("");
  elements.characterSelect.disabled = !characters.length;
  const selected = characters.find((item) => item.id === selectedId) || characters[0];
  const poseCount = Number(selected?.poseCount) || Object.keys(state.topic.poseAssets || {}).length;
  elements.characterPreview.src = selected?.coverUrl || assetUrl(Object.values(state.topic.poseAssets || {})[0]?.closed || "");
  elements.characterPreview.alt = selected ? `Nhân vật ${selected.name || selected.id}` : "Chưa có nhân vật";
  elements.characterMeta.textContent = selected ? `Mã ${selected.id} · đổi xong sẽ tự lưu vào dự án` : "Chưa có nhân vật để chọn.";
  elements.characterPoseCount.textContent = poseCount ? tr(`${poseCount} dáng pose`, `${poseCount} poses`) : tr("Chưa có pose", "No poses yet");
}

function switchCharacter(characterId) {
  if (!state.topic || characterId === state.topic.characterId) return;
  const character = state.characters.find((item) => item.id === characterId);
  if (!character) { showToast("Không tìm thấy nhân vật đã chọn.", true); renderCharacterPicker(); return; }
  const poses = Array.isArray(character.poses) ? character.poses.filter((pose) => pose?.id && pose?.file) : [];
  if (!poses.length) { showToast("Nhân vật này chưa có pose hợp lệ.", true); renderCharacterPicker(); return; }

  const oldOptions = POSE_OPTIONS.map((item) => item.id);
  const oldSfx = normalizePoseSfx(state.topic.poseSfx);
  const next = characterTopicData(character);
  const nextIds = Object.keys(next.poseAssets);
  const nextSfx = Object.fromEntries(nextIds.map((id, index) => [
    id,
    oldSfx[oldOptions[index]] || ["pose-hard-pop-click", "pose-hard-pop-click", "pose-bubble-pop", "pose-explainer-pop-whoosh", "pose-explainer-pop-whoosh"][index] || "",
  ]));
  state.poseBySegment = state.poseBySegment.map((item) => {
    const oldIndex = Math.max(0, oldOptions.indexOf(item.pose));
    return { pose: nextIds[oldIndex % nextIds.length] || nextIds[0] };
  });
  Object.assign(state.topic, {
    characterId: character.id,
    poseAssets: next.poseAssets,
    poseLabels: next.poseLabels,
    poseSfx: nextSfx,
  });
  if (character.id === "engzy" || character.id === "july") {
    // engzy/july's pose videos already contain the full scene. Start these
    // characters without inheriting the shared background image from another project.
    delete state.pendingUploads.backgroundImage;
    state.topic.backgroundType = "default";
    state.topic.backgroundImage = "";
    if (elements.backgroundImageFile) elements.backgroundImageFile.value = "";
    resetImageFrame("background");
    syncBackgroundControls();
  }
  configurePoseOptions(state.topic);
  state.topic.poseTimeline = timelineFromPoses(segmentsFromEditor(false));
  renderCharacterPicker();
  renderPoseSfxMap();
  renderPoseList();
  // Apply the font remembered for this character (same behaviour as background
  // and subtitle colour presets).
  rememberedLabelFontFor(character.id).then((font) => {
    if (!font || !state.topic || state.topic.characterId !== character.id) return;
    state.topic.labelFontFamily = font;
    populateLabelFontSelect(elements.labelFontFamily, font);
    markDirty();
    sendDraftToPreview();
    scheduleAutoSave(120);
  });
  rememberedLabelColorFor(character.id).then((color) => {
    if (!color || !state.topic || state.topic.characterId !== character.id) return;
    state.topic.labelColor = color;
    state.topic.leftLabelColor = color;
    state.topic.rightLabelColor = color;
    elements.leftLabelColor.value = color;
    elements.rightLabelColor.value = color;
    markDirty();
    sendDraftToPreview();
    scheduleAutoSave(120);
  });
  markDirty();
  sendDraftToPreview();
  scheduleAutoSave(100);
  showToast(`Đã đổi sang nhân vật ${character.name || character.id}.`);
}

function fallbackPose() {
  return POSE_OPTIONS.find((item) => item.id === "question")?.id || POSE_OPTIONS[0]?.id || "question";
}
const SFX_NAMES = {};
const SFX_PINNED = ["pose-hard-pop-click", "pose-explainer-pop-whoosh", "pose-bubble-pop", "chi-tay", "mo-hai-tay"];

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[char]);
}

function sfxLabel(key) {
  if (SFX_NAMES[key]) return SFX_NAMES[key];
  if (key.startsWith("custom-")) return `Custom · ${key.replace(/^custom-[0-9]+-/, "").replace(/-/g, " ")}`;
  return key.replace(/-/g, " ");
}

function sortedSfxKeys(sfxMap) {
  const keys = Object.keys(sfxMap || {});
  const pinned = SFX_PINNED.filter((key) => keys.includes(key));
  const rest = keys.filter((key) => !SFX_PINNED.includes(key)).sort((a, b) => sfxLabel(a).localeCompare(sfxLabel(b), "vi"));
  return [...pinned, ...rest];
}

async function loadSfxLibrary() {
  try {
    const response = await fetch("/assets/sfx/library.json", { cache: "no-store" });
    if (!response.ok) return;
    const items = await response.json();
    if (!Array.isArray(items)) return;
    items.forEach((item) => {
      if (item?.key && item?.name) SFX_NAMES[item.key] = item.name;
    });
  } catch (_) {
    /* ignore */
  }
}

function showToast(message, error = false, durationMs = 2800) {
  elements.toast.textContent = message;
  elements.toast.className = `toast visible${error ? " error" : ""}`;
  clearTimeout(showToast.timer);
  const hold = Math.max(1200, Number(durationMs) || 2800);
  showToast.timer = setTimeout(() => { elements.toast.className = "toast"; }, hold);
}

function clamp(value, min, max) {
  return Math.min(max, Math.max(min, value));
}

function imageFrameLayout(naturalWidth, naturalHeight, viewWidth, viewHeight, zoom, panX, panY) {
  const nw = Math.max(1, naturalWidth || 1);
  const nh = Math.max(1, naturalHeight || 1);
  const vw = Math.max(1, viewWidth || 1);
  const vh = Math.max(1, viewHeight || 1);
  const safeZoom = clamp(Number(zoom) || 1, 1, 3);
  const base = Math.min(vw / nw, vh / nh);
  const width = nw * base * safeZoom;
  const height = nh * base * safeZoom;
  const maxOffsetX = Math.max(0, (width - vw) / 2);
  const maxOffsetY = Math.max(0, (height - vh) / 2);
  const x = clamp(Number(panX) || 0, -50, 50);
  const y = clamp(Number(panY) || 0, -50, 50);
  const left = (vw - width) / 2 + (maxOffsetX ? x / 50 * maxOffsetX : 0);
  const top = (vh - height) / 2 + (maxOffsetY ? y / 50 * maxOffsetY : 0);
  return { zoom: safeZoom, x, y, width, height, left, top, maxOffsetX, maxOffsetY };
}

function readImageFrame(side) {
  const keys = IMAGE_SIDES[side];
  return {
    zoom: clamp(Number(state.topic?.[keys.zoom] ?? 1) || 1, 1, 3),
    x: clamp(Number(state.topic?.[keys.x] ?? 0) || 0, -50, 50),
    y: clamp(Number(state.topic?.[keys.y] ?? 0) || 0, -50, 50),
  };
}

function writeImageFrame(side, frame) {
  const keys = IMAGE_SIDES[side];
  state.topic[keys.zoom] = Math.round(clamp(Number(frame.zoom) || 1, 1, 3) * 100) / 100;
  state.topic[keys.x] = Math.round(clamp(Number(frame.x) || 0, -50, 50) * 10) / 10;
  state.topic[keys.y] = Math.round(clamp(Number(frame.y) || 0, -50, 50) * 10) / 10;
}

function applyImageFrameToThumb(side) {
  const keys = IMAGE_SIDES[side];
  const frame = readImageFrame(side);
  const thumb = elements[keys.thumb];
  const viewport = elements[keys.viewport];
  const zoomInput = elements[keys.zoomInput];
  const zoomText = elements[keys.zoomText];
  const layout = imageFrameLayout(
    thumb.naturalWidth,
    thumb.naturalHeight,
    viewport.clientWidth,
    viewport.clientHeight,
    frame.zoom,
    frame.x,
    frame.y,
  );
  thumb.style.width = `${layout.width}px`;
  thumb.style.height = `${layout.height}px`;
  thumb.style.transform = `translate(${layout.left}px, ${layout.top}px)`;
  zoomInput.value = String(layout.zoom);
  zoomText.textContent = `${Math.round(layout.zoom * 100)}%`;
}

function resetImageFrame(side) {
  writeImageFrame(side, { zoom: 1, x: 0, y: 0 });
  applyImageFrameToThumb(side);
}

function bindImageViewport(side) {
  const keys = IMAGE_SIDES[side];
  const viewport = elements[keys.viewport];
  const thumb = elements[keys.thumb];
  thumb.addEventListener("load", () => applyImageFrameToThumb(side));
  new ResizeObserver(() => { if (state.topic) applyImageFrameToThumb(side); }).observe(viewport);
  viewport.addEventListener("pointerdown", (event) => {
    if (!state.topic || event.button !== 0) return;
    event.preventDefault();
    const frame = readImageFrame(side);
    const layout = imageFrameLayout(thumb.naturalWidth, thumb.naturalHeight, viewport.clientWidth, viewport.clientHeight, frame.zoom, frame.x, frame.y);
    state.imageDrag = {
      side,
      pointerId: event.pointerId,
      startX: event.clientX,
      startY: event.clientY,
      originLeft: layout.left,
      originTop: layout.top,
      width: layout.width,
      height: layout.height,
      viewWidth: viewport.clientWidth || 1,
      viewHeight: viewport.clientHeight || 1,
      zoom: frame.zoom,
    };
    viewport.classList.add("dragging");
    viewport.setPointerCapture(event.pointerId);
  });
  viewport.addEventListener("pointermove", (event) => {
    const drag = state.imageDrag;
    if (!drag || drag.side !== side || drag.pointerId !== event.pointerId) return;
    const left = drag.originLeft + (event.clientX - drag.startX);
    const top = drag.originTop + (event.clientY - drag.startY);
    const maxOffsetX = Math.max(0, (drag.width - drag.viewWidth) / 2);
    const maxOffsetY = Math.max(0, (drag.height - drag.viewHeight) / 2);
    const centerLeft = (drag.viewWidth - drag.width) / 2;
    const centerTop = (drag.viewHeight - drag.height) / 2;
    writeImageFrame(side, {
      zoom: drag.zoom,
      x: maxOffsetX ? clamp((left - centerLeft) / maxOffsetX * 50, -50, 50) : 0,
      y: maxOffsetY ? clamp((top - centerTop) / maxOffsetY * 50, -50, 50) : 0,
    });
    applyImageFrameToThumb(side);
    sendDraftToPreview();
  });
  const endDrag = (event) => {
    const drag = state.imageDrag;
    if (!drag || drag.side !== side || drag.pointerId !== event.pointerId) return;
    state.imageDrag = null;
    viewport.classList.remove("dragging");
    markDirty();
    scheduleAutoSave(200);
  };
  viewport.addEventListener("pointerup", endDrag);
  viewport.addEventListener("pointercancel", endDrag);
}

function comparisonById(id) {
  return (state.topic?.comparisons || []).find((item) => item.id === id);
}

function sentenceOptions(selected) {
  const lines = scriptLines();
  return lines.map((line, index) => {
    const sentence = index + 1;
    const preview = line.length > 42 ? `${line.slice(0, 42)}…` : line;
    return `<option value="${sentence}" ${sentence === Number(selected) ? "selected" : ""}>Câu ${sentence} · ${escapeHtml(preview)}</option>`;
  }).join("");
}

function comparisonLayout(comparison) {
  return comparison?.layout === "single" ? "single" : "pair";
}

function baseComparisonEnabled() {
  return state.topic?.baseComparisonEnabled !== false;
}

function syncBaseComparisonVisibility() {
  const block = document.querySelector(".comparison-block-primary");
  if (block) block.hidden = !baseComparisonEnabled();
}

function comparisonSides(comparison) {
  return comparisonLayout(comparison) === "single" ? ["left"] : ["left", "right"];
}

function comparisonFrameAspect(comparison) {
  return comparisonLayout(comparison) === "single" ? 16 / 9 : 1;
}

function comparisonUploadCardHtml(comparison, side) {
  const single = comparisonLayout(comparison) === "single";
  const slotLabel = single ? "Ảnh đơn" : `Ảnh bên ${side === "left" ? "trái" : "phải"}`;
  return `<div class="upload-card" data-side="${side}">
    <span class="sr-only">${slotLabel}</span>
    <div class="image-viewport" data-comparison-viewport data-side="${side}" title="Kéo để di chuyển vị trí hiển thị">
      <img data-comparison-thumb data-side="${side}" src="${comparisonThumbUrl(comparison, side)}" alt="" draggable="false" />
    </div>
    <label class="image-zoom"><span>Zoom <output data-zoom-text data-side="${side}">${Math.round(Number(comparison[`${side}ImageZoom`] || 1) * 100)}%</output></span><input data-field="${side}ImageZoom" data-side="${side}" type="range" min="1" max="3" step="0.01" value="${Number(comparison[`${side}ImageZoom`] || 1)}" /></label>
    <div class="image-actions"><label class="replace-image">Thay ảnh<input data-comparison-file data-side="${side}" type="file" accept="image/png,image/jpeg,image/webp" /></label><button class="crop-image" data-action="crop-comparison-image" data-side="${side}" type="button">Crop/xoay</button><button class="remove-bg-image" data-action="remove-bg-comparison-image" data-side="${side}" type="button">${tr("Xóa nền", "Remove BG")}</button><button class="delete-image" data-action="clear-comparison-image" data-side="${side}" type="button">Xoá ảnh</button></div>
  </div>`;
}

function comparisonCardHtml(comparison, index) {
  const layout = comparisonLayout(comparison);
  const earlierScenes = (state.topic?.comparisons || []).slice(0, index + 1);
  const number = layout === "single"
    ? earlierScenes.filter((item) => comparisonLayout(item) === "single").length
    : earlierScenes.filter((item) => comparisonLayout(item) === "pair").length + (baseComparisonEnabled() ? 1 : 0);
  const leftSub = escapeHtml(comparison.leftSubLabel || "");
  const rightSub = escapeHtml(comparison.rightSubLabel || "");
  const leftSubColor = escapeHtml(comparison.leftSubLabelColor || DEFAULT_SUBLABEL_COLOR);
  const rightSubColor = escapeHtml(comparison.rightSubLabelColor || DEFAULT_SUBLABEL_COLOR);
  const labelFontFamily = escapeHtml(comparison.labelFontFamily || state.topic.labelFontFamily || DEFAULT_LABEL_FONT_FAMILY);
  const heading = `<div class="comparison-block-heading">
      <div><strong>${layout === "single" ? `Ảnh đơn ${number}` : `So sánh ${number}`}</strong></div>
      <label class="comparison-start">Bắt đầu từ câu<select data-field="startSentence">${sentenceOptions(comparison.startSentence)}</select></label>
      <button class="button tiny danger comparison-remove" data-action="remove-comparison" type="button">Xoá</button>
    </div>`;
  if (layout === "single") {
    return `<section class="comparison-block comparison-block-single" data-layout="single" data-comparison-id="${escapeHtml(comparison.id)}">
      ${heading}
      <div class="single-image-label-grid">
        <div class="comparison-label-field"><label class="sr-only">Nhãn chính</label><div><input data-field="leftLabel" required value="${escapeHtml(comparison.leftLabel || "")}" aria-label="Nhãn chính" /><input class="label-color-input" data-field="leftLabelColor" type="color" value="${escapeHtml(comparison.leftLabelColor || comparison.labelColor || "#090909")}" aria-label="Màu nhãn chính" title="Màu nhãn chính" /></div></div>
        <div class="comparison-label-field comparison-sublabel-field"><label class="sr-only">Nhãn phụ</label><div><input data-field="leftSubLabel" maxlength="40" placeholder="Nhãn phụ" autocomplete="off" spellcheck="false" value="${leftSub}" aria-label="Nhãn phụ" /><input class="label-color-input" data-field="leftSubLabelColor" type="color" value="${leftSubColor}" aria-label="Màu nhãn phụ" title="Màu nhãn phụ" /></div></div>
      </div>
      <label class="comparison-font-field">Font nhãn
        <select data-field="labelFontFamily">${labelFontOptionsHtml(labelFontFamily)}</select>
      </label>
      <div class="upload-grid single-image-upload-grid">
        ${comparisonUploadCardHtml(comparison, "left")}
      </div>
    </section>`;
  }
  return `<section class="comparison-block" data-layout="pair" data-comparison-id="${escapeHtml(comparison.id)}">
    ${heading}
    <div class="form-grid comparison-label-grid">
      <div class="comparison-label-field"><label class="sr-only">Nhãn bên trái</label><div><input data-field="leftLabel" required value="${escapeHtml(comparison.leftLabel || "")}" aria-label="Nhãn bên trái" /><input class="label-color-input" data-field="leftLabelColor" type="color" value="${escapeHtml(comparison.leftLabelColor || comparison.labelColor || "#090909")}" aria-label="Màu nhãn bên trái" title="Màu nhãn bên trái" /></div></div>
      <div class="comparison-label-field"><label class="sr-only">Nhãn bên phải</label><div><input data-field="rightLabel" required value="${escapeHtml(comparison.rightLabel || "")}" aria-label="Nhãn bên phải" /><input class="label-color-input" data-field="rightLabelColor" type="color" value="${escapeHtml(comparison.rightLabelColor || comparison.labelColor || "#090909")}" aria-label="Màu nhãn bên phải" title="Màu nhãn bên phải" /></div></div>
    </div>
    <label class="comparison-font-field">Font nhãn
    <select data-field="labelFontFamily">${labelFontOptionsHtml(labelFontFamily)}</select>
    </label>
    <div class="form-grid comparison-label-grid comparison-sublabel-grid" data-sublabel-row>
      <div class="comparison-label-field comparison-sublabel-field"><label class="sr-only">Phiên âm bên trái</label><div><input data-field="leftSubLabel" maxlength="40" placeholder="/lʊk/" autocomplete="off" spellcheck="false" value="${leftSub}" aria-label="Phiên âm bên trái" /><input class="label-color-input" data-field="leftSubLabelColor" type="color" value="${leftSubColor}" aria-label="Màu phiên âm bên trái" title="Màu phiên âm bên trái" /></div></div>
      <div class="comparison-label-field comparison-sublabel-field"><label class="sr-only">Phiên âm bên phải</label><div><input data-field="rightSubLabel" maxlength="40" placeholder="/siː/" autocomplete="off" spellcheck="false" value="${rightSub}" aria-label="Phiên âm bên phải" /><input class="label-color-input" data-field="rightSubLabelColor" type="color" value="${rightSubColor}" aria-label="Màu phiên âm bên phải" title="Màu phiên âm bên phải" /></div></div>
    </div>
    <div class="upload-grid">
      ${["left", "right"].map((side) => `<div class="upload-card" data-side="${side}">
        <span class="sr-only">Ảnh bên ${side === "left" ? "trái" : "phải"}</span>
        <div class="image-viewport" data-comparison-viewport data-side="${side}" title="Kéo để di chuyển vị trí hiển thị">
          <img data-comparison-thumb data-side="${side}" src="${assetUrl(comparison[`${side}Image`])}" alt="" draggable="false" />
        </div>
        <label class="image-zoom"><span>Zoom <output data-zoom-text data-side="${side}">${Math.round(Number(comparison[`${side}ImageZoom`] || 1) * 100)}%</output></span><input data-field="${side}ImageZoom" data-side="${side}" type="range" min="1" max="3" step="0.01" value="${Number(comparison[`${side}ImageZoom`] || 1)}" /></label>
        <div class="image-actions"><label class="replace-image">Thay ảnh<input data-comparison-file data-side="${side}" type="file" accept="image/png,image/jpeg,image/webp" /></label><button class="crop-image" data-action="crop-comparison-image" data-side="${side}" type="button">Crop/xoay</button><button class="remove-bg-image" data-action="remove-bg-comparison-image" data-side="${side}" type="button">${tr("Xóa nền", "Remove BG")}</button><button class="delete-image" data-action="clear-comparison-image" data-side="${side}" type="button">Xoá ảnh</button></div>
      </div>`).join("")}
    </div>
  </section>`;
}

function renderComparisonList() {
  if (!elements.comparisonList || !state.topic) return;
  const scrollPane = elements.comparisonList.closest(".tab-pane");
  const previousScrollTop = scrollPane?.scrollTop || 0;
  const previousScrollLeft = scrollPane?.scrollLeft || 0;
  state.topic.comparisons = Array.isArray(state.topic.comparisons) ? state.topic.comparisons : [];
  syncBaseComparisonVisibility();
  elements.comparisonList.innerHTML = state.topic.comparisons.map(comparisonCardHtml).join("");
  elements.comparisonList.querySelectorAll("[data-comparison-thumb]").forEach((thumb) => {
    thumb.addEventListener("load", () => applyComparisonThumb(thumb.closest("[data-comparison-id]")?.dataset.comparisonId, thumb.dataset.side));
  });
  const restoreScroll = () => {
    if (!scrollPane) return;
    scrollPane.scrollTop = previousScrollTop;
    scrollPane.scrollLeft = previousScrollLeft;
  };
  restoreScroll();
  requestAnimationFrame(() => {
    state.topic.comparisons.forEach((item) => {
      applyComparisonThumb(item.id, "left");
      applyComparisonThumb(item.id, "right");
    });
    restoreScroll();
  });
}

function comparisonFrame(comparison, side) {
  return {
    zoom: clamp(Number(comparison?.[`${side}ImageZoom`]) || 1, 1, 3),
    x: clamp(Number(comparison?.[`${side}ImageX`]) || 0, -50, 50),
    y: clamp(Number(comparison?.[`${side}ImageY`]) || 0, -50, 50),
  };
}

function writeComparisonFrame(comparison, side, frame) {
  comparison[`${side}ImageZoom`] = Math.round(clamp(Number(frame.zoom) || 1, 1, 3) * 100) / 100;
  comparison[`${side}ImageX`] = Math.round(clamp(Number(frame.x) || 0, -50, 50) * 10) / 10;
  comparison[`${side}ImageY`] = Math.round(clamp(Number(frame.y) || 0, -50, 50) * 10) / 10;
}

function applyComparisonThumb(id, side) {
  const comparison = comparisonById(id);
  const card = elements.comparisonList?.querySelector(`[data-comparison-id="${CSS.escape(id || "")}"]`);
  const thumb = card?.querySelector(`[data-comparison-thumb][data-side="${side}"]`);
  const viewport = card?.querySelector(`[data-comparison-viewport][data-side="${side}"]`);
  if (!comparison || !thumb || !viewport) return;
  const frame = comparisonFrame(comparison, side);
  const layout = imageFrameLayout(thumb.naturalWidth, thumb.naturalHeight, viewport.clientWidth, viewport.clientHeight, frame.zoom, frame.x, frame.y);
  thumb.style.width = `${layout.width}px`;
  thumb.style.height = `${layout.height}px`;
  thumb.style.transform = `translate(${layout.left}px, ${layout.top}px)`;
  const zoom = card.querySelector(`[data-field="${side}ImageZoom"]`);
  const output = card.querySelector(`[data-zoom-text][data-side="${side}"]`);
  if (zoom) zoom.value = String(frame.zoom);
  if (output) output.textContent = `${Math.round(frame.zoom * 100)}%`;
}

function placeholderImage(side) {
  return `assets/placeholder-${side}.svg`;
}

function singleImagePlaceholderUrl() {
  const svg = '<svg xmlns="http://www.w3.org/2000/svg" width="1600" height="900" viewBox="0 0 1600 900"><rect width="1600" height="900" fill="#fffaf0"/><rect x="28" y="28" width="1544" height="844" rx="42" fill="none" stroke="#de370d" stroke-width="8" stroke-dasharray="18 16"/><circle cx="800" cy="350" r="92" fill="#de370d" opacity=".16"/><path d="M750 350h100M800 300v100" stroke="#de370d" stroke-width="20" stroke-linecap="round"/><text x="800" y="560" text-anchor="middle" fill="#4b3a29" font-family="Arial, sans-serif" font-size="42" font-weight="700">Ảnh đơn</text></svg>';
  return `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`;
}

function isPlaceholderImage(value, side) {
  return String(value || "").endsWith(`/placeholder-${side}.svg`) || String(value || "") === placeholderImage(side);
}

function comparisonThumbUrl(comparison, side) {
  if (comparisonLayout(comparison) === "single" && side === "left" && isPlaceholderImage(comparison.leftImage, "left")) {
    return singleImagePlaceholderUrl();
  }
  return assetUrl(comparison[`${side}Image`]);
}

function setBaseImageFile(side, file, frame = null) {
  const input = elements[side === "left" ? "leftImageFile" : "rightImageFile"];
  const thumb = elements[side === "left" ? "leftThumb" : "rightThumb"];
  state.pendingUploads[`${side}Image`] = file;
  if (input && input.files?.[0] !== file) input.value = "";
  thumb.src = URL.createObjectURL(file);
  resetImageFrame(side);
  jumpPreviewToBaseComparison();
  markDirty();
  sendDraftToPreview();
  scheduleAutoSave(80);
}

function clearBaseImage(side) {
  const input = elements[side === "left" ? "leftImageFile" : "rightImageFile"];
  const thumb = elements[side === "left" ? "leftThumb" : "rightThumb"];
  delete state.pendingUploads[`${side}Image`];
  state.topic[`${side}Image`] = placeholderImage(side);
  if (input) input.value = "";
  thumb.src = assetUrl(state.topic[`${side}Image`]);
  resetImageFrame(side);
  jumpPreviewToBaseComparison();
  markDirty();
  sendDraftToPreview();
  scheduleAutoSave(120);
}

function setComparisonImageFile(comparison, side, file, frame = null) {
  const key = `comparison:${comparison.id}:${side}`;
  state.pendingUploads[key] = file;
  writeComparisonFrame(comparison, side, frame || { zoom: 1, x: 0, y: 0 });
  const card = elements.comparisonList.querySelector(`[data-comparison-id="${CSS.escape(comparison.id)}"]`);
  const thumb = card?.querySelector(`[data-comparison-thumb][data-side="${side}"]`);
  const input = card?.querySelector(`[data-comparison-file][data-side="${side}"]`);
  if (input && input.files?.[0] !== file) input.value = "";
  if (thumb) thumb.src = URL.createObjectURL(file);
  jumpPreviewToComparison(comparison);
  markDirty();
  sendDraftToPreview();
  scheduleAutoSave(80);
}

function clearComparisonImage(comparison, side) {
  delete state.pendingUploads[`comparison:${comparison.id}:${side}`];
  comparison[`${side}Image`] = placeholderImage(side);
  writeComparisonFrame(comparison, side, { zoom: 1, x: 0, y: 0 });
  const card = elements.comparisonList.querySelector(`[data-comparison-id="${CSS.escape(comparison.id)}"]`);
  const input = card?.querySelector(`[data-comparison-file][data-side="${side}"]`);
  if (input) input.value = "";
  const thumb = card?.querySelector(`[data-comparison-thumb][data-side="${side}"]`);
  if (thumb) thumb.src = comparisonThumbUrl(comparison, side);
  applyComparisonThumb(comparison.id, side);
  jumpPreviewToComparison(comparison);
  markDirty();
  sendDraftToPreview();
  scheduleAutoSave(120);
}

function nextEmptyImageSlot() {
  const firstEmptyComparisonSlot = (comparison) => {
    if (!comparison) return null;
    for (const side of comparisonSides(comparison)) {
      const key = `comparison:${comparison.id}:${side}`;
      if (isPlaceholderImage(comparison[`${side}Image`], side) && !state.pendingUploads[key]) return { type: "comparison", comparison, side };
    }
    return null;
  };
  const firstEmptyBaseSlot = () => {
    if (!baseComparisonEnabled()) return null;
    for (const side of ["left", "right"]) {
      if (isPlaceholderImage(state.topic?.[`${side}Image`], side) && !state.pendingUploads[`${side}Image`]) return { type: "base", side };
    }
    return null;
  };
  const activeComparison = comparisonById(state.previewComparisonId);
  const activeSlot = firstEmptyComparisonSlot(activeComparison);
  if (activeSlot) return activeSlot;
  if (state.previewComparisonId === "base") {
    const baseSlot = firstEmptyBaseSlot();
    if (baseSlot) return baseSlot;
  }
  const baseSlot = firstEmptyBaseSlot();
  if (baseSlot) return baseSlot;
  for (const comparison of state.topic?.comparisons || []) {
    if (comparison.id === activeComparison?.id) continue;
    const comparisonSlot = firstEmptyComparisonSlot(comparison);
    if (comparisonSlot) return comparisonSlot;
  }
  return null;
}

function comparisonSceneName(comparison) {
  const scenes = state.topic?.comparisons || [];
  const index = scenes.findIndex((item) => item.id === comparison?.id);
  const earlierScenes = scenes.slice(0, index + 1);
  if (comparisonLayout(comparison) === "single") return `Ảnh đơn ${earlierScenes.filter((item) => comparisonLayout(item) === "single").length}`;
  return `So sánh ${earlierScenes.filter((item) => comparisonLayout(item) === "pair").length + (baseComparisonEnabled() ? 1 : 0)}`;
}

function normalizedClipboardImage(file, index) {
  const extensions = { "image/png": "png", "image/jpeg": "jpg", "image/webp": "webp" };
  const extension = extensions[file.type];
  if (!extension) return null;
  if (new RegExp(`\\.${extension === "jpg" ? "(?:jpg|jpeg)" : extension}$`, "i").test(file.name || "")) return file;
  return new File([file], `clipboard-${Date.now()}-${index + 1}.${extension}`, { type: file.type });
}

async function frameForClipboardImage(file, frameAspect = 1) {
  const sourceUrl = URL.createObjectURL(file);
  const source = document.createElement("img");
  try {
    await new Promise((resolve, reject) => {
      source.onload = resolve;
      source.onerror = () => reject(new Error(tr("Không thể đọc ảnh clipboard.", "Could not read the clipboard image.")));
      source.src = sourceUrl;
    });
    const aspect = source.naturalWidth / Math.max(1, source.naturalHeight);
    return {
      zoom: Math.round(clamp(Math.max(aspect / frameAspect, frameAspect / aspect), 1, 3) * 100) / 100,
      x: 0,
      y: 0,
    };
  } finally {
    URL.revokeObjectURL(sourceUrl);
  }
}

async function api(url, options = {}) {
  const response = await fetch(url, { cache: "no-store", headers: { "Content-Type": "application/json", ...(options.headers || {}) }, ...options });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.error || `HTTP ${response.status}`);
  return payload;
}

function assetUrl(value) {
  return new URL(value, `${window.location.origin}/project/${project}/topic.json`).href;
}

function formatTime(value) {
  const seconds = Math.max(0, Math.floor(Number(value) || 0));
  return `${String(Math.floor(seconds / 60)).padStart(2, "0")}:${String(seconds % 60).padStart(2, "0")}`;
}

function markDirty() {
  state.dirty = true;
  state.revision += 1;
  elements.saveState.textContent = "Đang chờ tự lưu...";
}

function scheduleAutoSave(delay = 800) {
  clearTimeout(scheduleAutoSave.timer);
  scheduleAutoSave.timer = setTimeout(() => saveEditor(null, true), delay);
}

function scriptLines() {
  return elements.scriptInput.value.split("\n").map((line) => line.trim()).filter(Boolean);
}

function defaultSpeakerDefinitions() {
  return [
    ["voice_a", { alias: tr("Voice A · Mở đầu + kết", "Voice A · Opening + ending"), voiceId: "oncoinx", speed: 1, pitch: 1 }],
    ["voice_b", { alias: tr("Voice B · Phần nội dung", "Voice B · Main content"), voiceId: "oncoinx", speed: 1, pitch: 1 }],
  ];
}

function normalizeSpeakerKey(value) {
  return String(value || "").trim().toLowerCase().replace(/\s+/g, "_").slice(0, 80);
}

function topicTtsConfig(topic = state.topic) {
  if (!topic || typeof topic !== "object") return {};
  if (topic.tts && typeof topic.tts === "object" && !Array.isArray(topic.tts)) return topic.tts;
  if (topic.ttsConfig && typeof topic.ttsConfig === "object" && !Array.isArray(topic.ttsConfig)) return topic.ttsConfig;
  return {};
}

function speakerReference(segment) {
  if (!segment || typeof segment !== "object") return "";
  return String(segment.speaker || segment.speakerId || segment.speaker_id || segment.role || "").trim();
}

function speakerEntries(topic = state.topic) {
  const byKey = new Map(defaultSpeakerDefinitions());
  const config = topicTtsConfig(topic);
  const raw = config.speakers || topic?.speakers;
  const add = (rawKey, rawValue) => {
    const key = normalizeSpeakerKey(rawKey) || ("speaker_" + (byKey.size + 1));
    const value = typeof rawValue === "string"
      ? { voiceId: rawValue }
      : (rawValue && typeof rawValue === "object" ? { ...rawValue } : {});
    const existing = byKey.get(key) || {};
    byKey.set(key, {
      ...existing,
      ...value,
      alias: String(value.alias || value.name || existing.alias || rawKey || key).trim().slice(0, 80),
    });
  };
  if (Array.isArray(raw)) {
    raw.forEach((value, index) => {
      if (!value || typeof value !== "object") return;
      add(value.id || value.key || value.alias || value.name || ("speaker_" + (index + 1)), value);
    });
  } else if (raw && typeof raw === "object") {
    Object.entries(raw).forEach(([key, value]) => add(key, value));
  }
  (Array.isArray(topic?.segments) ? topic.segments : []).forEach((segment) => {
    const reference = speakerReference(segment);
    if (!reference) return;
    const normalized = normalizeSpeakerKey(reference);
    const found = [...byKey.entries()].some(([key, value]) => (
      normalizeSpeakerKey(key) === normalized || normalizeSpeakerKey(value.alias) === normalized
    ));
    if (!found) add(reference, { alias: reference });
  });
  return [...byKey.entries()];
}

function defaultSpeakerForIndex(index, total) {
  return total <= 1 || index === 0 || index === total - 1 ? "voice_a" : "voice_b";
}

function resolveSpeakerKey(reference, topic = state.topic) {
  const normalized = normalizeSpeakerKey(reference);
  if (!normalized) return "";
  return speakerEntries(topic).find(([key, value]) => (
    normalizeSpeakerKey(key) === normalized || normalizeSpeakerKey(value.alias) === normalized
  ))?.[0] || normalizeSpeakerKey(reference);
}

function speakerForSegment(segment, index, total) {
  const reference = speakerReference(segment);
  return resolveSpeakerKey(reference, state.topic) || defaultSpeakerForIndex(index, total);
}

function ensureTopicSpeakerDefaults(topic = state.topic) {
  if (!topic || !Array.isArray(topic.segments)) return false;
  const before = JSON.stringify({
    tts: topic.tts,
    segments: topic.segments.map((segment) => speakerReference(segment)),
  });
  const config = topicTtsConfig(topic);
  const speakers = Object.fromEntries(speakerEntries(topic));
  const explicitMode = String(config.mode || config.ttsMode || config.tts_mode || "").trim();
  topic.tts = {
    ...config,
    mode: explicitMode || "multiSpeakers",
    speakers,
  };
  const total = topic.segments.length;
  topic.segments = topic.segments.map((segment, index) => ({
    ...segment,
    speaker: speakerForSegment(segment, index, total),
  }));
  return before !== JSON.stringify({
    tts: topic.tts,
    segments: topic.segments.map((segment) => speakerReference(segment)),
  });
}

function hydrateSpeakers() {
  if (!state.topic || !Array.isArray(state.topic.segments)) {
    state.speakerBySegment = [];
    return;
  }
  const total = state.topic.segments.length;
  state.speakerBySegment = state.topic.segments.map((segment, index) => ({
    speaker: speakerForSegment(segment, index, total),
  }));
}

function selectedSpeakerForSegment(segment, index, total) {
  const alignedSelection = state.speakerBySegment.length === total ? state.speakerBySegment[index]?.speaker : "";
  return resolveSpeakerKey(alignedSelection || speakerReference(segment), state.topic)
    || defaultSpeakerForIndex(index, total);
}

function speakerOptionsHtml(selected) {
  const selectedKey = resolveSpeakerKey(selected, state.topic);
  return speakerEntries(state.topic).map(([key, value]) => (
    '<option value="' + escapeHtml(key) + '"' + (key === selectedKey ? " selected" : "") + '>' + escapeHtml(value.alias || key) + '</option>'
  )).join("");
}

function defaultSpeakerSequence(segments) {
  if (!Array.isArray(segments) || !segments.length) return true;
  return segments.every((segment, index) => (
    resolveSpeakerKey(speakerReference(segment), state.topic) === defaultSpeakerForIndex(index, segments.length)
  ));
}

function hasPlaceholderVoiceover() {
  return /(?:^|\/)silence\.wav$/i.test(String(state.topic?.voiceover || ""));
}

function previewDurationFor(lines) {
  // Preview is deliberately independent of audio. A fresh project has a
  // silent 1-second placeholder, which must not squeeze multiple sentences
  // into one second before the user uploads/generates real voiceover.
  const suggested = Math.max(2.5, lines.length * 2.4);
  return hasPlaceholderVoiceover()
    ? Math.max(Number(state.topic?.duration) || 0, suggested)
    : Math.max(0.5, Number(state.topic?.duration) || 0.5);
}

function distributeSegments(lines, duration, previousSegments = []) {
  const safeDuration = Math.max(0.5, Number(duration) || 1);
  const weights = lines.map((line) => Math.max(5, line.replace(/\s/g, "").length));
  const total = weights.reduce((sum, value) => sum + value, 0) || 1;
  const gap = Math.min(0.32, safeDuration / Math.max(1, lines.length) * 0.08);
  let cursor = 0;
  const previous = Array.isArray(previousSegments) ? previousSegments : [];
  const resetDefaultSpeakers = defaultSpeakerSequence(previous);
  return lines.map((text, index) => {
    const rawEnd = index === lines.length - 1 ? safeDuration : cursor + safeDuration * weights[index] / total;
    const priorSpeaker = resetDefaultSpeakers ? "" : speakerReference(previous[index]);
    const segment = {
      start: Number(cursor.toFixed(3)),
      end: Number(Math.max(cursor + 0.12, rawEnd - (index === lines.length - 1 ? 0 : gap)).toFixed(3)),
      text,
      speaker: resolveSpeakerKey(priorSpeaker, state.topic) || defaultSpeakerForIndex(index, lines.length),
    };
    cursor = rawEnd;
    return segment;
  });
}

function segmentsFromEditor(forceRetime = false) {
  const lines = scriptLines();
  if (!lines.length) return [];
  if (!forceRetime && state.topic.segments.length === lines.length) {
    return state.topic.segments.map((segment, index) => ({
      ...segment,
      text: lines[index],
      speaker: selectedSpeakerForSegment(segment, index, lines.length),
    }));
  }
  return distributeSegments(lines, previewDurationFor(lines), state.topic.segments);
}

function inferPose(text, index) {
  const leftTokens = [state.topic.leftLabel, "bên trái", "loại đầu", "thứ nhất", "304"].filter(Boolean).map((value) => value.toLowerCase());
  const rightTokens = [state.topic.rightLabel, "bên phải", "loại sau", "thứ hai", "316"].filter(Boolean).map((value) => value.toLowerCase());
  const ids = POSE_OPTIONS.map((item) => item.id);
  const pick = (preferred, fallbackIndex) => ids.includes(preferred) ? preferred : ids[Math.min(fallbackIndex, ids.length - 1)] || fallbackPose();
  if (CUSTOM_POSE_SEQUENCES[String(state.topic?.characterId || "")]) return defaultPoseForCharacter(state.topic.characterId, index, ids);
  const normalized = text.toLowerCase();
  if (/[?？]|vậy|khác nhau|tại sao/.test(normalized)) return pick("question", 2);
  if (leftTokens.some((token) => normalized.includes(token))) return index <= 2 ? pick("neutral-left", 0) : pick("smile-left", 3);
  if (rightTokens.some((token) => normalized.includes(token))) return index <= 2 ? pick("neutral-right", 1) : pick("smile-right", 4);
  if (/nếu|cho nên|kết luận|chọn/.test(normalized)) return pick("question", 2);
  return ids[index % ids.length] || fallbackPose();
}

function autoSelectPoses() {
  const segments = segmentsFromEditor(false);
  state.poseBySegment = segments.map((segment, index) => ({ pose: inferPose(segment.text, index) }));
  renderPoseList();
  markDirty();
  sendDraftToPreview();
  scheduleAutoSave(150);
}

function poseForTime(time) {
  let current = state.topic.poseTimeline?.[0] || { pose: fallbackPose() };
  for (const event of state.topic.poseTimeline || []) {
    if (Number(event.time) <= Number(time)) current = event;
    else break;
  }
  return current.pose;
}

function defaultPoseSfx() {
  return Object.fromEntries(POSE_OPTIONS.map((item) => [item.id, item.defaultSfx]));
}

function normalizePoseSfx(value) {
  const defaults = defaultPoseSfx();
  const source = value && typeof value === "object" ? value : {};
  const result = { ...defaults };
  POSE_OPTIONS.forEach(({ id }) => {
    if (!(id in source)) return;
    const key = String(source[id] || "").trim();
    if (!key) {
      result[id] = "";
      return;
    }
    if (state.topic?.sfx?.[key] || key in SFX_NAMES || Object.values(defaults).includes(key)) result[id] = key;
  });
  return result;
}

function sfxForPose(pose) {
  const map = normalizePoseSfx(state.topic?.poseSfx);
  return map[pose] || "";
}

function hydratePoses() {
  state.topic.poseSfx = normalizePoseSfx(state.topic.poseSfx);
  state.poseBySegment = state.topic.segments.map((segment) => ({ pose: poseForTime(segment.start) }));
}

function poseOptionsHtml(selected) {
  const ids = poseDisplayOrder(state.topic?.characterId, POSE_OPTIONS.map((item) => item.id));
  const labelById = new Map(POSE_OPTIONS.map((item) => [item.id, item.label]));
  return ids.map((id) => `<option value="${id}" ${selected === id ? "selected" : ""}>${escapeHtml(labelById.get(id) || id)}</option>`).join("");
}

function renderPoseSfxMap() {
  if (!elements.poseSfxMap || !state.topic) return;
  state.topic.poseSfx = normalizePoseSfx(state.topic.poseSfx);
  const displayIds = poseDisplayOrder(state.topic.characterId, POSE_OPTIONS.map((item) => item.id));
  const activePose = POSE_OPTIONS.some((item) => item.id === state.activePoseSfx)
    ? state.activePoseSfx
    : displayIds[0] || POSE_OPTIONS[0].id;
  state.activePoseSfx = activePose;
  const selected = state.topic.poseSfx[activePose] || "";
  const sfxKeys = sortedSfxKeys(state.topic.sfx);
  const label = POSE_OPTIONS.find((item) => item.id === activePose)?.label || activePose;
  elements.poseSfxMap.innerHTML = `<div class="pose-sfx-row" data-pose="${activePose}">
    <select data-field="pose-map-pose" aria-label="Pose">${poseOptionsHtml(activePose)}</select>
    <select data-field="pose-map-sfx" aria-label="Âm thanh cho ${escapeHtml(label)}">
      <option value="" ${!selected ? "selected" : ""}>Không âm</option>
      ${sfxKeys.map((key) => `<option value="${escapeHtml(key)}" ${selected === key ? "selected" : ""}>${escapeHtml(sfxLabel(key))}</option>`).join("")}
    </select>
    <button class="sfx-preview-button" data-action="preview-pose-sfx" data-pose="${activePose}" type="button" title="Nghe thử">▶</button>
  </div>`;
}

function renderPoseList() {
  const segments = segmentsFromEditor(false);
  if (state.poseBySegment.length !== segments.length) {
    state.poseBySegment = segments.map((segment, index) => state.poseBySegment[index] || { pose: inferPose(segment.text, index) });
  }
  elements.poseList.innerHTML = segments.map((segment, index) => {
    const rendered = state.renderedSegments[index];
    const start = rendered && String(rendered.text || "").trim() === String(segment.text || "").trim()
      ? Number(rendered.start) : segment.start;
    return `<div class="pose-row" data-index="${index}">
    <span class="pose-row-time">${formatTime(start)}<strong>${index + 1}.</strong></span>
    <p title="${escapeHtml(segment.text)}">${escapeHtml(segment.text)}</p>
    <div class="pose-row-controls">
      <select data-field="pose" aria-label="Pose ${index + 1}">${poseOptionsHtml(state.poseBySegment[index].pose)}</select>
      <select class="speaker-select" data-field="speaker" aria-label="Voice/Speaker ${index + 1}">${speakerOptionsHtml(selectedSpeakerForSegment(segment, index, segments.length))}</select>
    </div>
  </div>`;
  }).join("");
}

async function loadRenderedTiming() {
  state.renderedSegments = [];
  try {
    const response = await fetch(`/project/${encodeURIComponent(project)}/topic.rendered.json`, { cache: "no-store" });
    if (!response.ok) return;
    const rendered = await response.json();
    const source = state.topic?.segments || [];
    const aligned = Array.isArray(rendered.segments) ? rendered.segments : [];
    if (aligned.length === source.length && aligned.every((item, index) => String(item.text || "").trim() === String(source[index]?.text || "").trim())) {
      state.renderedSegments = aligned;
    }
  } catch {
    // Render timing is optional until a video has been rendered.
  }
}

function timelineFromPoses(segments) {
  const result = [];
  let previous = "";
  segments.forEach((segment, index) => {
    const item = state.poseBySegment[index] || { pose: inferPose(segment.text, index) };
    if (index === 0 || item.pose !== previous) {
      const event = { time: segment.start, pose: item.pose };
      const sfx = sfxForPose(item.pose);
      if (sfx) event.sfx = sfx;
      result.push(event);
    }
    previous = item.pose;
  });
  return result.length ? result : [{ time: 0, pose: fallbackPose() }];
}

function draftTopic(forceRetime = false) {
  const segments = segmentsFromEditor(forceRetime);
  const ttsConfig = topicTtsConfig(state.topic);
  const speakerConfig = Object.fromEntries(speakerEntries({ ...state.topic, segments }));
  const duration = hasPlaceholderVoiceover()
    ? Math.max(previewDurationFor(scriptLines()), Number(segments[segments.length - 1]?.end) || 0)
    : Number(state.topic.duration);
  const left = readImageFrame("left");
  const right = readImageFrame("right");
  const background = readImageFrame("background");
  const backgroundType = elements.backgroundType?.value || state.topic.backgroundType || "default";
  return {
    ...state.topic,
    pasteImageMode: normalizePasteImageMode(state.topic.pasteImageMode),
    brand: state.topic.brand || "Aurex",
    leftLabel: elements.leftLabelInput.value.trim(),
    rightLabel: elements.rightLabelInput.value.trim(),
    leftLabelColor: elements.leftLabelColor.value,
    rightLabelColor: elements.rightLabelColor.value,
    labelColor: elements.leftLabelColor.value || state.topic.labelColor || "#090909",
    labelFontFamily: normalizeLabelFontFamily(elements.labelFontFamily?.value || state.topic.labelFontFamily),
    showSubLabels: hasSubLabelRow({
      showSubLabels: state.topic.showSubLabels,
      leftSubLabel: elements.leftSubLabelInput?.value,
      rightSubLabel: elements.rightSubLabelInput?.value,
    }),
    leftSubLabel: (elements.leftSubLabelInput?.value || "").trim().slice(0, 40),
    rightSubLabel: (elements.rightSubLabelInput?.value || "").trim().slice(0, 40),
    leftSubLabelColor: elements.leftSubLabelColor?.value || DEFAULT_SUBLABEL_COLOR,
    rightSubLabelColor: elements.rightSubLabelColor?.value || DEFAULT_SUBLABEL_COLOR,
    comparisons: (state.topic.comparisons || []).map((item) => ({ ...item })),
    duration,
    leftImageZoom: left.zoom,
    leftImageX: left.x,
    leftImageY: left.y,
    rightImageZoom: right.zoom,
    rightImageX: right.x,
    rightImageY: right.y,
    backgroundType,
    backgroundColor: elements.backgroundColor?.value || state.topic.backgroundColor || DEFAULT_BACKGROUND_COLOR,
    backgroundImage: String(state.topic.backgroundImage || "").trim(),
    backgroundImageZoom: background.zoom,
    backgroundImageX: background.x,
    backgroundImageY: background.y,
    karaokeActiveColor: elements.karaokeActiveColor.value,
    karaokeColor: elements.karaokeColor.value,
    karaokeSize: Number(elements.karaokeSize.value),
    backgroundMusic: String(state.topic.backgroundMusic || "").trim(),
    backgroundMusicEnabled: hasBackgroundMusic(),
    backgroundMusicVolume: clamp(Number(elements.backgroundMusicVolume?.value ?? state.topic.backgroundMusicVolume ?? DEFAULT_BACKGROUND_MUSIC_VOLUME) || DEFAULT_BACKGROUND_MUSIC_VOLUME, 0.05, 0.5),
    sfxVolume: Number(elements.sfxVolumeInput.value),
    poseSfx: normalizePoseSfx(state.topic.poseSfx),
    tts: {
      ...ttsConfig,
      mode: String(ttsConfig.mode || ttsConfig.ttsMode || ttsConfig.tts_mode || "").trim() || "multiSpeakers",
      speakers: speakerConfig,
    },
    segments,
    poseTimeline: timelineFromPoses(segments),
  };
}

function sendDraftToPreview() {
  if (!state.topic || !elements.previewFrame.contentWindow) return;
  if (state.previewSegmentIndex >= 0) {
    const segments = segmentsFromEditor(false);
    const index = clamp(state.previewSegmentIndex, 0, Math.max(0, segments.length - 1));
    const selected = segments[index];
    if (selected) {
      state.previewSegmentIndex = index;
      state.previewTime = Math.max(0, Number(selected.start) || 0);
      updatePreviewCounter(index);
    }
  }
  elements.previewFrame.contentWindow.postMessage({
    type: "tho-topic-update",
    topic: draftTopic(false),
    time: state.previewTime,
    comparisonId: state.previewComparisonId,
  }, window.location.origin);
  if (state.previewSegmentIndex < 0) {
    elements.previewFrame.contentWindow.postMessage({ type: "tho-preview-blank" }, window.location.origin);
  }
}

function reloadPreviewKeepingState(src) {
  // Reloading the iframe resets the preview to frame 0. Remember where the
  // user was and restore it once the new document has booted.
  const segmentIndex = state.previewSegmentIndex;
  const time = state.previewTime;
  const comparisonId = state.previewComparisonId;
  const frame = elements.previewFrame;
  const restore = () => {
    frame.removeEventListener("load", restore);
    state.previewSegmentIndex = segmentIndex;
    state.previewTime = time;
    state.previewComparisonId = comparisonId;
    updatePreviewCounter(segmentIndex);
    // The preview boots asynchronously, so give it a tick before seeking.
    window.setTimeout(() => sendDraftToPreview(), 120);
  };
  frame.addEventListener("load", restore);
  frame.src = src;
}

function seekPreview(delta = 0) {
  state.previewTime = Math.max(0, Math.min(Number(state.topic.duration) || 0, state.previewTime + delta));
  elements.previewFrame.contentWindow?.postMessage({ type: "tho-seek", time: state.previewTime }, window.location.origin);
}

function updatePreviewCounter(index = -1) {
  const total = scriptLines().length;
  elements.previewTime.textContent = `${index >= 0 ? index + 1 : "—"}/${total || "—"}`;
}

let stepPreviewSfxPlayer = null;
let stepPreviewBgmPlayer = null;

function playSegmentPoseSfx(segmentIndex) {
  if (!state.topic) return;
  const pose = state.poseBySegment[segmentIndex]?.pose;
  const key = pose ? String(state.topic.poseSfx?.[pose] || "").trim() : "";
  if (stepPreviewSfxPlayer) {
    stepPreviewSfxPlayer.pause();
    stepPreviewSfxPlayer = null;
  }
  if (!key) return;
  const path = state.topic.sfx?.[key];
  if (!path) return;
  stepPreviewSfxPlayer = new Audio(assetUrl(path));
  stepPreviewSfxPlayer.volume = Number(elements.sfxVolumeInput?.value ?? state.topic.sfxVolume ?? 0.3);
  stepPreviewSfxPlayer.play().catch(() => {});
}

function stopStepPreviewBackgroundMusic() {
  if (!stepPreviewBgmPlayer) return;
  stepPreviewBgmPlayer.pause();
  stepPreviewBgmPlayer.currentTime = 0;
}

function ensureStepPreviewBackgroundMusic() {
  const enabled = hasBackgroundMusic() && Boolean(String(state.topic?.backgroundMusic || "").trim());
  if (!enabled) {
    stopStepPreviewBackgroundMusic();
    return;
  }
  const src = assetUrl(state.topic.backgroundMusic);
  if (!stepPreviewBgmPlayer || stepPreviewBgmPlayer.dataset.src !== src) {
    stopStepPreviewBackgroundMusic();
    stepPreviewBgmPlayer = new Audio(src);
    stepPreviewBgmPlayer.loop = true;
    stepPreviewBgmPlayer.preload = "auto";
    stepPreviewBgmPlayer.dataset.src = src;
  }
  stepPreviewBgmPlayer.volume = clamp(
    Number(elements.backgroundMusicVolume?.value ?? state.topic.backgroundMusicVolume ?? DEFAULT_BACKGROUND_MUSIC_VOLUME) || DEFAULT_BACKGROUND_MUSIC_VOLUME,
    0.05,
    0.5,
  );
  // Start once when preview begins; later arrow steps keep the same playback position.
  if (!stepPreviewBgmPlayer.paused) return;
  stepPreviewBgmPlayer.play().catch(() => {});
}

function stepPreview(direction) {
  const segments = segmentsFromEditor(false);
  if (!segments.length) return;
  state.previewComparisonId = "";
  const current = Math.max(-1, Math.min(segments.length - 1, Number(state.previewSegmentIndex) || 0));
  state.previewSegmentIndex = direction > 0
    ? Math.min(segments.length - 1, current + 1)
    : Math.max(-1, current - 1);
  if (state.previewSegmentIndex < 0) {
    state.previewTime = 0;
    updatePreviewCounter(-1);
    if (stepPreviewSfxPlayer) {
      stepPreviewSfxPlayer.pause();
      stepPreviewSfxPlayer = null;
    }
    stopStepPreviewBackgroundMusic();
    elements.previewFrame.contentWindow?.postMessage({ type: "tho-preview-blank" }, window.location.origin);
    return;
  }
  const selected = segments[state.previewSegmentIndex];
  state.previewTime = Math.max(0, Number(selected.start));
  updatePreviewCounter(state.previewSegmentIndex);
  renderPoseList();
  elements.previewFrame.contentWindow?.postMessage({
    type: "tho-play-segment",
    topic: draftTopic(false),
    start: Number(selected.start),
    end: Number(selected.end),
    silent: true,
    parentPoseSfx: true,
    parentBgm: true,
  }, window.location.origin);
  playSegmentPoseSfx(state.previewSegmentIndex);
  ensureStepPreviewBackgroundMusic();
}

function jumpPreviewToSentence(sentenceNumber) {
  const segments = segmentsFromEditor(false);
  if (!segments.length) return;
  const index = clamp(Math.floor(Number(sentenceNumber) || 1) - 1, 0, segments.length - 1);
  const selected = segments[index];
  const changedSentence = state.previewSegmentIndex !== index;
  state.previewSegmentIndex = index;
  state.previewTime = Math.max(0, Number(selected.start) || 0);
  updatePreviewCounter(index);
  if (changedSentence) renderPoseList();
  elements.previewFrame.contentWindow?.postMessage({ type: "tho-seek", time: state.previewTime }, window.location.origin);
}

function jumpPreviewToComparison(comparison) {
  if (!comparison) return;
  state.previewComparisonId = comparison.id;
  jumpPreviewToSentence(comparison.startSentence);
  elements.previewFrame.contentWindow?.postMessage({
    type: "tho-preview-comparison-lock",
    comparisonId: comparison.id,
    time: state.previewTime,
  }, window.location.origin);
}

function jumpPreviewToBaseComparison() {
  if (!baseComparisonEnabled()) {
    jumpPreviewToComparison(state.topic?.comparisons?.[0]);
    return;
  }
  state.previewComparisonId = "base";
  jumpPreviewToSentence(1);
  elements.previewFrame.contentWindow?.postMessage({
    type: "tho-preview-comparison-lock",
    comparisonId: "base",
    time: state.previewTime,
  }, window.location.origin);
}

function fitPreviewFrame() {
  const stage = elements.phoneFrame.parentElement;
  if (!stage) return;
  const bounds = stage.getBoundingClientRect();
  const maxWidth = Math.max(1, bounds.width - 14);
  const maxHeight = Math.max(1, bounds.height - 14);
  const width = Math.floor(Math.min(maxWidth, maxHeight * 9 / 16));
  const height = Math.floor(width * 16 / 9);
  elements.phoneFrame.style.width = `${width}px`;
  elements.phoneFrame.style.height = `${height}px`;
  elements.previewFrame.style.width = "100%";
  elements.previewFrame.style.height = "100%";
}

function resetCropSelection() {
  clearCropLiveRotation();
  setCropRotateMode(false);
  setCropAspectLock(true, true);
  state.cropZoom = 1;
  state.cropPanX = 0;
  state.cropPanY = 0;
  syncCropZoomControls();
  const original = state.cropOriginalSrc;
  if (original && elements.cropImage && elements.cropImage.getAttribute("src") !== original) {
    state.cropSelection = centeredCropSelection(1);
    state.cropDisplay = null;
    elements.cropImage.src = original;
    return;
  }
  state.cropSelection = centeredCropSelection(1);
  layoutCropImage();
}

function clearCropLiveRotation() {
  if (!elements.cropImage) return;
  elements.cropImage.style.transform = "";
  elements.cropImage.style.transformOrigin = "";
  if (elements.cropRotateLayer) {
    elements.cropRotateLayer.style.transform = "";
    elements.cropRotateLayer.style.transformOrigin = "";
    elements.cropRotateLayer.querySelectorAll("[data-rotate-corner]").forEach((handle) => { handle.style.transform = ""; });
  }
}

function cropRotateToggleLabel(active) {
  if (active) return "OK";
  return document.documentElement.lang === "en" ? "Rotate" : "Xoay";
}

function fitCropSelectionToAspect(selection, aspect = 1) {
  const targetAspect = Math.max(0.01, Number(aspect) || 1);
  let width = clamp(Number(selection?.width) || 1, 0.001, 1);
  let height = clamp(Number(selection?.height) || 1, 0.001, 1);
  if (width / height > targetAspect) width = height * targetAspect;
  else height = width / targetAspect;
  const centerX = clamp((Number(selection?.x) || 0) + (Number(selection?.width) || 1) / 2, width / 2, 1 - width / 2);
  const centerY = clamp((Number(selection?.y) || 0) + (Number(selection?.height) || 1) / 2, height / 2, 1 - height / 2);
  return { x: centerX - width / 2, y: centerY - height / 2, width, height };
}

function centeredCropSelection(aspect = 1) {
  return fitCropSelectionToAspect({ x: 0, y: 0, width: 1, height: 1 }, aspect);
}

function setCropAspectLock(enabled, normalizeSelection = false) {
  state.cropAspectLocked = Boolean(enabled);
  if (elements.toggleCropAspect) {
    elements.toggleCropAspect.classList.toggle("active", state.cropAspectLocked);
    elements.toggleCropAspect.setAttribute("aria-pressed", state.cropAspectLocked ? "true" : "false");
    elements.toggleCropAspect.setAttribute("aria-label", tr("Khóa crop 1:1", "Lock 1:1 crop"));
    elements.toggleCropAspect.textContent = "1:1";
  }
  if (
    normalizeSelection
    && state.cropAspectLocked
    && state.cropSelection
    && elements.cropImage?.naturalWidth
  ) {
    state.cropSelection = fitCropSelectionToAspect(state.cropSelection, 1);
    renderCropSelection();
  }
}

function setCropRotateMode(enabled) {
  state.cropRotateMode = Boolean(enabled);
  if (state.cropRotateMode) {
    state.cropZoom = 1;
    state.cropPanX = 0;
    state.cropPanY = 0;
    syncCropZoomControls();
  }
  elements.cropStage?.classList.toggle("crop-rotate-mode", state.cropRotateMode);
  if (elements.toggleCropRotate) {
    elements.toggleCropRotate.classList.toggle("active", state.cropRotateMode);
    elements.toggleCropRotate.setAttribute("aria-pressed", state.cropRotateMode ? "true" : "false");
    elements.toggleCropRotate.textContent = cropRotateToggleLabel(state.cropRotateMode);
  }
  if (elements.cropZoomInput) elements.cropZoomInput.disabled = state.cropRotateMode;
  if (elements.applyCrop) elements.applyCrop.disabled = state.cropRotateMode;
  if (elements.cropRotateLayer) elements.cropRotateLayer.hidden = !state.cropRotateMode;
  if (!state.cropRotateMode) clearCropLiveRotation();
  if (state.cropRotateMode) layoutCropImage();
  else layoutCropRotateLayer();
}

function rotateCropImageByDegrees(degrees) {
  const image = elements.cropImage;
  if (!image?.naturalWidth) return;
  const angle = Number(degrees);
  if (!Number.isFinite(angle) || Math.abs(angle) < 0.05) return;
  const normalized = ((angle % 360) + 360) % 360;
  if (normalized < 0.05 || normalized > 359.95) return;
  const radians = (normalized * Math.PI) / 180;
  const sourceWidth = image.naturalWidth;
  const sourceHeight = image.naturalHeight;
  const cos = Math.abs(Math.cos(radians));
  const sin = Math.abs(Math.sin(radians));
  const canvas = document.createElement("canvas");
  canvas.width = Math.max(1, Math.ceil(sourceWidth * cos + sourceHeight * sin));
  canvas.height = Math.max(1, Math.ceil(sourceWidth * sin + sourceHeight * cos));
  const context = canvas.getContext("2d");
  context.imageSmoothingEnabled = true;
  context.imageSmoothingQuality = "high";
  context.translate(canvas.width / 2, canvas.height / 2);
  context.rotate(radians);
  context.drawImage(image, -sourceWidth / 2, -sourceHeight / 2);
  state.cropZoom = 1;
  state.cropPanX = 0;
  state.cropPanY = 0;
  state.cropSelection = { x: 0.08, y: 0.08, width: 0.84, height: 0.84 };
  syncCropZoomControls();
  clearCropLiveRotation();
  elements.cropImage.src = canvas.toDataURL("image/png");
}

function syncCropZoomControls() {
  const zoom = clamp(Number(state.cropZoom) || 1, 1, 3);
  if (elements.cropZoomInput) elements.cropZoomInput.value = String(zoom);
  if (elements.cropZoomText) elements.cropZoomText.textContent = `${Math.round(zoom * 100)}%`;
}

function setCropZoom(zoom) {
  const image = elements.cropImage;
  const stage = elements.cropStage;
  const display = state.cropDisplay;
  const selection = state.cropSelection;
  const nextZoom = clamp(Number(zoom) || 1, 1, 3);
  if (!image?.naturalWidth || !stage?.clientWidth || !display || !selection) {
    state.cropZoom = nextZoom;
    syncCropZoomControls();
    layoutCropImage();
    return;
  }
  const cropRect = {
    left: display.left + selection.x * display.width,
    top: display.top + selection.y * display.height,
    width: selection.width * display.width,
    height: selection.height * display.height,
  };
  const sourceCenter = {
    x: selection.x + selection.width / 2,
    y: selection.y + selection.height / 2,
  };
  const padding = 16;
  const fit = Math.min(
    (stage.clientWidth - padding * 2) / image.naturalWidth,
    (stage.clientHeight - padding * 2) / image.naturalHeight,
  );
  const width = image.naturalWidth * fit * nextZoom;
  const height = image.naturalHeight * fit * nextZoom;
  const centeredLeft = (stage.clientWidth - width) / 2;
  const centeredTop = (stage.clientHeight - height) / 2;
  const cropCenter = { x: cropRect.left + cropRect.width / 2, y: cropRect.top + cropRect.height / 2 };
  const nextSelection = {
    x: clamp(sourceCenter.x - cropRect.width / width / 2, 0, 1 - Math.min(1, cropRect.width / width)),
    y: clamp(sourceCenter.y - cropRect.height / height / 2, 0, 1 - Math.min(1, cropRect.height / height)),
    width: Math.min(1, cropRect.width / width),
    height: Math.min(1, cropRect.height / height),
  };
  const nextSourceCenter = {
    x: nextSelection.x + nextSelection.width / 2,
    y: nextSelection.y + nextSelection.height / 2,
  };
  state.cropZoom = nextZoom;
  state.cropPanX = cropCenter.x - (centeredLeft + nextSourceCenter.x * width);
  state.cropPanY = cropCenter.y - (centeredTop + nextSourceCenter.y * height);
  state.cropSelection = nextSelection;
  syncCropZoomControls();
  layoutCropImage();
}

function layoutCropRotateLayer() {
  const layer = elements.cropRotateLayer;
  const display = state.cropDisplay;
  if (!layer || !display) return;
  layer.style.left = `${display.left}px`;
  layer.style.top = `${display.top}px`;
  layer.style.width = `${display.width}px`;
  layer.style.height = `${display.height}px`;
}

function layoutCropImage() {
  const image = elements.cropImage;
  const stage = elements.cropStage;
  if (!image?.naturalWidth || !stage?.clientWidth) return;
  const padding = 16;
  const viewZoom = clamp(Number(state.cropZoom) || 1, 1, 3);
  const fit = Math.min(
    (stage.clientWidth - padding * 2) / image.naturalWidth,
    (stage.clientHeight - padding * 2) / image.naturalHeight,
  );
  const scale = fit * viewZoom;
  const width = image.naturalWidth * scale;
  const height = image.naturalHeight * scale;
  const left = (stage.clientWidth - width) / 2 + (Number(state.cropPanX) || 0);
  const top = (stage.clientHeight - height) / 2 + (Number(state.cropPanY) || 0);
  state.cropDisplay = { left, top, width, height };
  image.style.width = `${width}px`;
  image.style.height = `${height}px`;
  image.style.left = `${left}px`;
  image.style.top = `${top}px`;
  layoutCropRotateLayer();
  renderCropSelection();
}

function renderCropSelection() {
  const selection = state.cropSelection;
  const display = state.cropDisplay;
  if (!selection || !display || !elements.cropSelection) return;
  elements.cropSelection.style.left = `${display.left + selection.x * display.width}px`;
  elements.cropSelection.style.top = `${display.top + selection.y * display.height}px`;
  elements.cropSelection.style.width = `${selection.width * display.width}px`;
  elements.cropSelection.style.height = `${selection.height * display.height}px`;
  layoutCropRotateLayer();
  if (state.cropDrag?.type === "rotate" && Number.isFinite(state.cropDrag.currentDegrees)) {
    const degrees = state.cropDrag.currentDegrees;
    elements.cropSizeText.textContent = `Xoay ${degrees > 0 ? "+" : ""}${degrees.toFixed(1)}°`;
    return;
  }
  const outputWidth = Math.max(1, Math.round(elements.cropImage.naturalWidth * selection.width));
  const outputHeight = Math.max(1, Math.round(elements.cropImage.naturalHeight * selection.height));
  elements.cropSizeText.textContent = `${Math.round(state.cropZoom * 100)}% · Ảnh đầu ra: ${outputWidth} × ${outputHeight}px`;
}

function openCropDialog(target) {
  const thumb = target?.thumb;
  const source = thumb?.currentSrc || thumb?.src;
  if (!source) return;
  state.cropTarget = target;
  state.cropOriginalSrc = source;
  setCropAspectLock(true);
  state.cropZoom = 1;
  state.cropPanX = 0;
  state.cropPanY = 0;
  state.cropSelection = centeredCropSelection(1);
  state.cropDisplay = null;
  state.cropDrag = null;
  clearCropLiveRotation();
  setCropRotateMode(false);
  syncCropZoomControls();
  elements.cropImage.src = source;
  elements.cropDialog.showModal();
  if (elements.cropImage.complete) requestAnimationFrame(layoutCropImage);
}

function closeCropDialog() {
  state.cropTarget = null;
  state.cropOriginalSrc = null;
  state.cropDrag = null;
  clearCropLiveRotation();
  setCropRotateMode(false);
  elements.cropDialog.close();
}

function cropStagePoint(event) {
  const rect = elements.cropStage.getBoundingClientRect();
  return {
    x: event.clientX - rect.left,
    y: event.clientY - rect.top,
  };
}

function cropPoint(event) {
  const display = state.cropDisplay;
  const point = cropStagePoint(event);
  return {
    x: clamp((point.x - display.left) / display.width, 0, 1),
    y: clamp((point.y - display.top) / display.height, 0, 1),
  };
}

function cropDisplayCenter() {
  const display = state.cropDisplay;
  return {
    x: display.left + display.width / 2,
    y: display.top + display.height / 2,
  };
}

function angleFromCropCenter(point) {
  const center = cropDisplayCenter();
  return Math.atan2(point.y - center.y, point.x - center.x);
}

async function applyCrop() {
  const target = state.cropTarget;
  const selection = state.cropSelection;
  const image = elements.cropImage;
  if (!target || !selection || !image.naturalWidth) return;
  const sx = Math.round(selection.x * image.naturalWidth);
  const sy = Math.round(selection.y * image.naturalHeight);
  const sw = Math.max(1, Math.round(selection.width * image.naturalWidth));
  const sh = Math.max(1, Math.round(selection.height * image.naturalHeight));
  const preservedFrame = target.type === "base"
    ? readImageFrame(target.side)
    : comparisonFrame(comparisonById(target.comparisonId), target.side);
  const canvas = document.createElement("canvas");
  canvas.width = sw;
  canvas.height = sh;
  canvas.getContext("2d").drawImage(image, sx, sy, sw, sh, 0, 0, sw, sh);
  const blob = await new Promise((resolve, reject) => canvas.toBlob((value) => value ? resolve(value) : reject(new Error("Không tạo được ảnh crop.")), "image/png"));
  const file = new File([blob], `aurexvideo-crop-${Date.now()}.png`, { type: "image/png" });
  if (target.type === "base") {
    setBaseImageFile(target.side, file);
    writeImageFrame(target.side, preservedFrame);
    applyImageFrameToThumb(target.side);
  } else {
    const comparison = comparisonById(target.comparisonId);
    if (comparison) {
      setComparisonImageFile(comparison, target.side, file);
      writeComparisonFrame(comparison, target.side, preservedFrame);
      applyComparisonThumb(comparison.id, target.side);
    }
  }
  closeCropDialog();
  showToast(`Đã áp dụng vùng crop/xoay ${sw} × ${sh}px.`);
}

function dataUrlToFile(dataUrl, name = "image-nobg.png") {
  const [header, encoded = ""] = String(dataUrl || "").split(",", 2);
  const mime = /data:([^;]+)/.exec(header)?.[1] || "image/png";
  const binary = atob(encoded);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i);
  return new File([bytes], name, { type: mime });
}

async function fetchUrlAsFile(url, name = "image.png") {
  const response = await fetch(url);
  if (!response.ok) throw new Error(tr("Không tải được ảnh hiện tại.", "Could not load the current image."));
  const blob = await response.blob();
  const type = blob.type || "image/png";
  const safeName = name.endsWith(".png") || name.includes(".") ? name : `${name}.png`;
  return new File([blob], safeName, { type });
}

async function resolveSlotImageFile({ type, side, comparisonId }) {
  if (type === "base") {
    const pending = state.pendingUploads[`${side}Image`];
    if (pending) return pending;
    if (isPlaceholderImage(state.topic?.[`${side}Image`], side)) {
      throw new Error(tr("Ô ảnh đang trống.", "This image slot is empty."));
    }
    return fetchUrlAsFile(assetUrl(state.topic[`${side}Image`]), `${side}-image.png`);
  }
  const comparison = comparisonById(comparisonId);
  if (!comparison) throw new Error(tr("Không tìm thấy so sánh.", "Comparison not found."));
  const key = `comparison:${comparison.id}:${side}`;
  if (state.pendingUploads[key]) return state.pendingUploads[key];
  if (isPlaceholderImage(comparison[`${side}Image`], side)) {
    throw new Error(tr("Ô ảnh đang trống.", "This image slot is empty."));
  }
  return fetchUrlAsFile(assetUrl(comparison[`${side}Image`]), `${side}-image.png`);
}

async function removeBackgroundModelStatus() {
  try {
    const status = await api("/api/images/remove-background/status");
    return {
      ready: !!status?.ready,
      firstLoad: status?.firstLoad !== false && !status?.ready,
    };
  } catch (_) {
    return { ready: false, firstLoad: true };
  }
}

async function removeBackgroundForSlot(target, button) {
  const label = tr("Xóa nền", "Remove BG");
  if (button) {
    button.disabled = true;
    button.textContent = tr("Đang xóa…", "Removing…");
  }
  try {
    const modelStatus = await removeBackgroundModelStatus();
    if (modelStatus.firstLoad) {
      showToast(
        tr(
          "Lần đầu cần tải model xóa nền — có thể mất khoảng 3–4 phút. Vui lòng đợi, đừng tắt app.",
          "First use downloads the remove-bg model — this can take about 3–4 minutes. Please wait and keep the app open.",
        ),
        false,
        12000,
      );
      if (button) button.textContent = tr("Đang tải model…", "Loading model…");
    } else {
      showToast(tr("Đang xóa nền…", "Removing background…"));
    }
    const file = await resolveSlotImageFile(target);
    const result = await api("/api/images/remove-background", {
      method: "POST",
      body: JSON.stringify({ name: file.name, data: await fileToDataUrl(file) }),
    });
    const next = dataUrlToFile(result.data, result.name || `${fileStem(file.name)}-nobg.png`);
    if (target.type === "base") setBaseImageFile(target.side, next);
    else {
      const comparison = comparisonById(target.comparisonId);
      if (!comparison) throw new Error(tr("Không tìm thấy so sánh.", "Comparison not found."));
      setComparisonImageFile(comparison, target.side, next);
    }
    showToast(tr("Đã xóa nền ảnh.", "Background removed."));
  } finally {
    if (button) {
      button.disabled = false;
      button.textContent = label;
    }
  }
}

function fileStem(name) {
  const base = String(name || "image").split(/[\\/]/).pop() || "image";
  return base.replace(/\.[^.]+$/, "") || "image";
}

async function fileToDataUrl(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result);
    reader.onerror = reject;
    reader.readAsDataURL(file);
  });
}

async function uploadFile(kind, file, assignToTopic = true) {
  const result = await api(`/api/projects/${encodeURIComponent(project)}/upload`, { method: "POST", body: JSON.stringify({ kind, name: file.name, data: await fileToDataUrl(file) }) });
  if (assignToTopic) state.topic[kind] = result.path;
  if (kind === "voiceover" && result.duration) {
    state.topic.duration = result.duration;
  }
  return result;
}

async function uploadCustomSfx(file) {
  const result = await api(`/api/projects/${encodeURIComponent(project)}/upload`, {
    method: "POST",
    body: JSON.stringify({ kind: "customSfx", name: file.name, data: await fileToDataUrl(file) }),
  });
  state.topic.sfx = { ...(state.topic.sfx || {}), [result.sfxKey]: result.path };
  renderPoseSfxMap();
  markDirty();
  scheduleAutoSave(100);
  showToast(`Đã thêm âm custom: ${file.name}`);
}

function previewSfx(key) {
  const path = state.topic.sfx?.[key];
  if (!path) { showToast("Câu này đang chọn Không âm.", true); return; }
  const player = new Audio(assetUrl(path));
  player.volume = Number(elements.sfxVolumeInput.value || 0.3);
  player.play().catch((error) => showToast(`Không phát được âm: ${error.message}`, true));
}

async function saveEditor(event, quiet = false) {
  event?.preventDefault();
  if (state.saving) { scheduleAutoSave(350); return false; }
  if (!scriptLines().length) { showToast("Kịch bản cần ít nhất một dòng.", true); return false; }
  const savingRevision = state.revision;
  state.saving = true;
  if (elements.saveButton) elements.saveButton.disabled = true;
  elements.saveState.textContent = "Đang lưu...";
  try {
    let uploadedVoice = false;
    for (const [kind, file] of Object.entries(state.pendingUploads)) {
      if (kind.startsWith("comparison:")) {
        const [, comparisonId, side] = kind.split(":");
        const comparison = comparisonById(comparisonId);
        if (comparison && (side === "left" || side === "right")) {
          const result = await uploadFile("comparisonImage", file, false);
          comparison[`${side}Image`] = result.path;
        }
      } else {
        await uploadFile(kind, file);
        if (kind === "voiceover") uploadedVoice = true;
      }
      if (state.pendingUploads[kind] === file) delete state.pendingUploads[kind];
    }
    const result = await api(`/api/projects/${encodeURIComponent(project)}/topic`, { method: "PUT", body: JSON.stringify(draftTopic(false)) });
    state.topic = result.topic;
    state.topic.pasteImageMode = normalizePasteImageMode(state.topic.pasteImageMode);
    syncPasteImageModeControls();
    configurePoseOptions(state.topic);
    ensureTopicSpeakerDefaults(state.topic);
    hydrateSpeakers();
    state.dirty = savingRevision !== state.revision;
    elements.saveState.textContent = state.dirty ? "Có thay đổi mới, đang lưu tiếp..." : "Đã tự động lưu";
    if (uploadedVoice) reloadPreviewKeepingState(`/index.html?topic=${encodeURIComponent(`project/${project}/topic.json`)}&render=1&preview=1&v=${Date.now()}`);
    else sendDraftToPreview();
    elements.leftThumb.src = assetUrl(state.topic.leftImage);
    elements.rightThumb.src = assetUrl(state.topic.rightImage);
    renderCharacterPicker();
    renderPoseSfxMap();
    renderPoseList();
    renderComparisonList();
    applyImageFrameToThumb("left");
    applyImageFrameToThumb("right");
    syncBackgroundControls();
    syncBackgroundMusicControls();
    if (!quiet) showToast("Đã lưu dự án.");
    if (state.dirty) scheduleAutoSave(250);
    return true;
  } catch (error) {
    elements.saveState.textContent = "Lưu thất bại";
    showToast(error.message, true);
    return false;
  } finally { state.saving = false; if (elements.saveButton) elements.saveButton.disabled = false; }
}

async function startJob(kind) {
  if (state.dirty && !(await saveEditor())) return;
  try {
    const payload = await api(`/api/projects/${encodeURIComponent(project)}/${kind}`, { method: "POST", body: "{}" });
    state.activeJob = payload.job.id;
    elements.jobKicker.textContent = kind === "render" ? "Render MP4" : "Ảnh duyệt";
    elements.jobTitle.textContent = "Đang chuẩn bị...";
    elements.jobMessage.textContent = "Vui lòng giữ trang này mở.";
    elements.jobLogs.textContent = "";
    elements.jobOutput.hidden = true;
    elements.jobProgress.style.width = "0%";
    elements.jobDialog.showModal();
    pollJob();
  } catch (error) { showToast(error.message, true); }
}

async function pollJob() {
  if (!state.activeJob) return;
  try {
    const job = await api(`/api/jobs/${state.activeJob}`);
    elements.jobProgress.style.width = `${job.progress || 0}%`;
    elements.jobTitle.textContent = job.status === "queued" ? "Đang xếp hàng" : job.status === "running" ? "Đang xử lý..." : job.status === "done" ? "Hoàn tất" : "Có lỗi";
    elements.jobMessage.textContent = `${job.progress || 0}% · ${job.project}`;
    elements.jobLogs.textContent = job.logs || "Chưa có log...";
    elements.jobLogs.scrollTop = elements.jobLogs.scrollHeight;
    if (job.status === "done") {
      elements.jobOutput.href = job.outputUrl;
      elements.jobOutput.textContent = job.kind === "render" ? "Mở video MP4" : "Mở ảnh duyệt";
      elements.jobOutput.hidden = false;
      state.activeJob = null;
      return;
    }
    if (job.status === "failed") {
      elements.jobMessage.textContent = job.error || "Tác vụ thất bại.";
      state.activeJob = null;
      return;
    }
    setTimeout(pollJob, 1000);
  } catch (error) { elements.jobMessage.textContent = error.message; }
}

async function loadProject() {
  if (!project) { window.location.href = "/"; return; }
  try {
    await Promise.all([loadSfxLibrary(), loadCharacters()]);
    const result = await api(`/api/projects/${encodeURIComponent(project)}/topic`);
    state.topic = result.topic;
    await loadRenderedTiming();
    state.topic.pasteImageMode = normalizePasteImageMode(state.topic.pasteImageMode);
    syncPasteImageModeControls();
    if (localizeStarterContent(state.topic)) {
      markDirty();
      scheduleAutoSave(120);
    }
    configurePoseOptions(state.topic);
    const initialLines = state.topic.segments.map((segment) => String(segment.text || "").trim()).filter(Boolean);
    const suggestedPreviewDuration = previewDurationFor(initialLines);
    if (hasPlaceholderVoiceover() && Number(state.topic.duration) + 0.01 < suggestedPreviewDuration) {
      state.topic.duration = suggestedPreviewDuration;
      state.topic.segments = distributeSegments(initialLines, suggestedPreviewDuration);
      markDirty();
      scheduleAutoSave(80);
    }
    if (ensureTopicSpeakerDefaults(state.topic)) {
      markDirty();
      scheduleAutoSave(120);
    }
    hydrateSpeakers();
    elements.projectTitle.textContent = project;
    elements.leftLabelInput.value = state.topic.leftLabel;
    elements.rightLabelInput.value = state.topic.rightLabel;
    elements.leftLabelColor.value = state.topic.leftLabelColor || state.topic.labelColor || "#090909";
    elements.rightLabelColor.value = state.topic.rightLabelColor || state.topic.labelColor || "#090909";
    populateLabelFontSelect(elements.labelFontFamily, state.topic.labelFontFamily);
    if (state.topic.characterId && !state.topic.labelColor && elements.leftLabelColor.value === "#090909" && elements.rightLabelColor.value === "#090909") {
      const labelColor = await rememberedLabelColorFor(state.topic.characterId);
      if (labelColor) {
        state.topic.labelColor = labelColor;
        state.topic.leftLabelColor = labelColor;
        state.topic.rightLabelColor = labelColor;
        elements.leftLabelColor.value = labelColor;
        elements.rightLabelColor.value = labelColor;
      }
    }
    if (elements.leftSubLabelInput) elements.leftSubLabelInput.value = state.topic.leftSubLabel || "";
    if (elements.rightSubLabelInput) elements.rightSubLabelInput.value = state.topic.rightSubLabel || "";
    if (elements.leftSubLabelColor) elements.leftSubLabelColor.value = state.topic.leftSubLabelColor || DEFAULT_SUBLABEL_COLOR;
    if (elements.rightSubLabelColor) elements.rightSubLabelColor.value = state.topic.rightSubLabelColor || DEFAULT_SUBLABEL_COLOR;
    state.topic.comparisons = Array.isArray(state.topic.comparisons) ? state.topic.comparisons : [];
    syncBaseComparisonVisibility();
    elements.leftThumb.src = assetUrl(state.topic.leftImage);
    elements.rightThumb.src = assetUrl(state.topic.rightImage);
    applyImageFrameToThumb("left");
    applyImageFrameToThumb("right");
    elements.karaokeActiveColor.value = state.topic.karaokeActiveColor || "#de370d";
    elements.karaokeColor.value = state.topic.karaokeColor || "#271f11";
    elements.karaokeSize.value = String(state.topic.karaokeSize ?? 1.2);
    elements.karaokeSizeText.textContent = `${Math.round((state.topic.karaokeSize ?? 1.2) * 100)}%`;
    syncBackgroundControls();
    syncBackgroundMusicControls();
    elements.scriptInput.value = state.topic.segments.map((segment) => segment.text).join("\n");
    renderCharacterPicker();
    renderComparisonList();
    elements.sfxVolumeInput.value = state.topic.sfxVolume ?? 0.3;
    elements.sfxVolumeText.textContent = `${Math.round((state.topic.sfxVolume ?? 0.3) * 100)}%`;
    hydratePoses();
    renderPoseSfxMap();
    renderPoseList();
    const previewTopic = result.previewTopicUrl || `/project/${project}/topic.json`;
    elements.previewFrame.src = `/index.html?topic=${encodeURIComponent(previewTopic)}&render=1&preview=1&v=${Date.now()}`;
    state.previewSegmentIndex = -1;
    state.previewTime = 0;
    state.previewComparisonId = "";
    updatePreviewCounter(-1);
    elements.saveState.textContent = "Tự động lưu đã bật";
  } catch (error) {
    showToast(error.message, true);
    setTimeout(() => { window.location.href = "/"; }, 1800);
  }
}

document.querySelectorAll("[data-tab]").forEach((button) => button.addEventListener("click", () => {
  document.querySelectorAll("[data-tab]").forEach((item) => item.classList.toggle("active", item === button));
  document.querySelectorAll("[data-pane]").forEach((pane) => pane.classList.toggle("active", pane.dataset.pane === button.dataset.tab));
  if (button.dataset.tab === "pose") {
    renderPoseSfxMap();
    renderPoseList();
  }
  if (button.dataset.tab === "content") renderComparisonList();
}));
document.querySelectorAll("[data-close-dialog]").forEach((button) => button.addEventListener("click", () => button.closest("dialog").close()));
elements.cropImage.addEventListener("load", layoutCropImage);
new ResizeObserver(layoutCropImage).observe(elements.cropStage);
elements.closeCropDialog.addEventListener("click", closeCropDialog);
elements.toggleCropRotate.addEventListener("click", () => {
  setCropRotateMode(!state.cropRotateMode);
  renderCropSelection();
});
elements.toggleCropAspect.addEventListener("click", () => {
  setCropAspectLock(!state.cropAspectLocked, true);
  renderCropSelection();
});
elements.resetCrop.addEventListener("click", resetCropSelection);
elements.applyCrop.addEventListener("click", () => applyCrop().catch((error) => showToast(error.message, true)));
elements.cropZoomInput?.addEventListener("input", () => {
  setCropZoom(elements.cropZoomInput.value);
});
elements.cropDialog.addEventListener("click", (event) => { if (event.target === elements.cropDialog) closeCropDialog(); });
elements.cropStage.addEventListener("pointerdown", (event) => {
  if (!state.cropDisplay || !state.cropSelection || event.button !== 0) return;
  const rotateHandle = event.target.closest("[data-rotate-corner]");
  if (state.cropRotateMode || rotateHandle) {
    if (!rotateHandle) return;
    event.preventDefault();
    elements.cropStage.setPointerCapture(event.pointerId);
    const point = cropStagePoint(event);
    state.cropDrag = {
      type: "rotate",
      pointerId: event.pointerId,
      startAngle: angleFromCropCenter(point),
      currentDegrees: 0,
    };
    return;
  }
  const isCropSurface = event.target.closest("#cropSelection") || event.target.closest("#cropImage");
  if (!isCropSurface) return;
  event.preventDefault();
  elements.cropStage.setPointerCapture(event.pointerId);
  state.cropDrag = {
    type: "crop",
    pointerId: event.pointerId,
    handle: "move",
    start: cropPoint(event),
    selection: { ...state.cropSelection },
  };
});
elements.cropStage.addEventListener("pointermove", (event) => {
  const drag = state.cropDrag;
  if (!drag || drag.pointerId !== event.pointerId || !state.cropDisplay) return;
  if (drag.type === "rotate") {
    const point = cropStagePoint(event);
    let delta = (angleFromCropCenter(point) - drag.startAngle) * (180 / Math.PI);
    if (delta > 180) delta -= 360;
    if (delta < -180) delta += 360;
    drag.currentDegrees = delta;
    const radians = (delta * Math.PI) / 180;
    const cos = Math.abs(Math.cos(radians));
    const sin = Math.abs(Math.sin(radians));
    const display = state.cropDisplay;
    const rotatedWidth = display.width * cos + display.height * sin;
    const rotatedHeight = display.width * sin + display.height * cos;
    const previewScale = Math.min(
      1,
      Math.max(1, elements.cropStage.clientWidth - 32) / Math.max(1, rotatedWidth),
      Math.max(1, elements.cropStage.clientHeight - 32) / Math.max(1, rotatedHeight),
    );
    elements.cropImage.style.transformOrigin = "center center";
    elements.cropImage.style.transform = `rotate(${delta}deg) scale(${previewScale})`;
    elements.cropRotateLayer.style.transformOrigin = "center center";
    elements.cropRotateLayer.style.transform = `rotate(${delta}deg) scale(${previewScale})`;
    elements.cropRotateLayer.querySelectorAll("[data-rotate-corner]").forEach((handle) => {
      handle.style.transform = `rotate(${-delta}deg) scale(${1 / previewScale})`;
    });
    renderCropSelection();
    return;
  }
  const point = cropPoint(event);
  const dx = point.x - drag.start.x;
  const dy = point.y - drag.start.y;
  const original = drag.selection;
  const minWidth = Math.min(0.25, 36 / state.cropDisplay.width);
  const minHeight = Math.min(0.25, 36 / state.cropDisplay.height);
  if (drag.handle === "move") {
    state.cropSelection = {
      ...original,
      x: clamp(original.x + dx, 0, 1 - original.width),
      y: clamp(original.y + dy, 0, 1 - original.height),
    };
  } else {
    let left = original.x;
    let top = original.y;
    let right = original.x + original.width;
    let bottom = original.y + original.height;
    if (drag.handle.includes("w")) left = clamp(original.x + dx, 0, right - minWidth);
    if (drag.handle.includes("e")) right = clamp(right + dx, left + minWidth, 1);
    if (drag.handle.includes("n")) top = clamp(original.y + dy, 0, bottom - minHeight);
    if (drag.handle.includes("s")) bottom = clamp(bottom + dy, top + minHeight, 1);
    state.cropSelection = { x: left, y: top, width: right - left, height: bottom - top };
  }
  renderCropSelection();
});
const endCropDrag = (event) => {
  const drag = state.cropDrag;
  if (!drag || drag.pointerId !== event.pointerId) return;
  if (drag.type === "rotate") {
    const degrees = Number(drag.currentDegrees) || 0;
    state.cropDrag = null;
    clearCropLiveRotation();
    if (Math.abs(degrees) >= 0.2) rotateCropImageByDegrees(degrees);
    else renderCropSelection();
    return;
  }
  state.cropDrag = null;
};
elements.cropStage.addEventListener("pointerup", endCropDrag);
elements.cropStage.addEventListener("pointercancel", endCropDrag);
elements.editorForm.addEventListener("submit", saveEditor);
elements.characterSelect.addEventListener("change", () => switchCharacter(elements.characterSelect.value));
function handleEditorInput(event) {
  if (!state.topic) return;
  markDirty();
  if ([
    "leftLabelInput", "rightLabelInput", "leftLabelColor", "rightLabelColor",
    "labelFontFamily",
    "leftSubLabelInput", "rightSubLabelInput", "leftSubLabelColor", "rightSubLabelColor",
    "scriptInput", "karaokeActiveColor", "karaokeColor", "karaokeSize",
    "backgroundType", "backgroundColor",
    "backgroundMusicVolume",
  ].includes(event.target.id)) sendDraftToPreview();
 if (event.target.id === "sfxVolumeInput") elements.sfxVolumeText.textContent = `${Math.round(Number(event.target.value) * 100)}%`;
  if (event.target.id === "scriptInput") renderPoseList();
 if (event.target.id === "karaokeSize") elements.karaokeSizeText.textContent = `${Math.round(Number(event.target.value) * 100)}%`;
  if (event.target.id === "backgroundMusicVolume") {
    const volume = clamp(Number(event.target.value) || DEFAULT_BACKGROUND_MUSIC_VOLUME, 0.05, 0.5);
    if (state.topic) state.topic.backgroundMusicVolume = volume;
    if (elements.backgroundMusicVolumeText) elements.backgroundMusicVolumeText.textContent = `${Math.round(volume * 100)}%`;
  }
  if (event.target.id === "backgroundType" && state.topic) {
    state.topic.backgroundType = elements.backgroundType.value;
    if (elements.backgroundType.value === "image" && !state.topic.backgroundImage && !state.pendingUploads.backgroundImage) {
      elements.backgroundImageFile?.click();
    }
    syncBackgroundControls();
  }
  if (event.target.id === "backgroundColor" && state.topic) {
    state.topic.backgroundColor = elements.backgroundColor.value;
  }
  scheduleAutoSave();
}
elements.editorForm.addEventListener("input", handleEditorInput);
elements.scriptInput.addEventListener("input", handleEditorInput);
elements.leftImageFile.addEventListener("change", () => {
  const file = elements.leftImageFile.files[0];
  if (!file) return;
  setBaseImageFile("left", file);
});
elements.rightImageFile.addEventListener("change", () => {
  const file = elements.rightImageFile.files[0];
  if (!file) return;
  setBaseImageFile("right", file);
});
elements.deleteLeftImage.addEventListener("click", () => clearBaseImage("left"));
elements.deleteRightImage.addEventListener("click", () => clearBaseImage("right"));
elements.backgroundImageFile?.addEventListener("change", () => {
  const file = elements.backgroundImageFile.files[0];
  if (!file) return;
  setBackgroundImageFile(file);
});
elements.deleteBackgroundImage?.addEventListener("click", () => clearBackgroundImage());
elements.backgroundMusicFile?.addEventListener("change", () => {
  const file = elements.backgroundMusicFile.files[0];
  if (!file) return;
  setBackgroundMusicFile(file);
});
elements.deleteBackgroundMusic?.addEventListener("click", () => clearBackgroundMusic());
document.querySelectorAll('[data-action="crop-base-image"]').forEach((button) => button.addEventListener("click", () => {
  const keys = IMAGE_SIDES[button.dataset.side];
  if (keys) openCropDialog({ type: "base", side: button.dataset.side, thumb: elements[keys.thumb] });
}));
document.querySelectorAll('[data-action="remove-bg-base-image"]').forEach((button) => button.addEventListener("click", () => {
  removeBackgroundForSlot({ type: "base", side: button.dataset.side }, button).catch((error) => showToast(error.message, true));
}));
function addComparisonScene(layout) {
  if (!state.topic) return;
  const lines = scriptLines();
  const startSentence = Math.min(6, Math.max(1, lines.length));
  const single = layout === "single";
  const scene = {
    id: `${single ? "single-image" : "comparison"}-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
    layout: single ? "single" : "pair",
    startSentence,
    leftLabel: single ? tr("Nhãn chính", "Main label") : (elements.leftLabelInput.value.trim() || DEFAULT_LABEL_A()),
    rightLabel: single ? "" : (elements.rightLabelInput.value.trim() || DEFAULT_LABEL_B()),
    leftLabelColor: elements.leftLabelColor.value || "#090909",
    rightLabelColor: single ? "#090909" : (elements.rightLabelColor.value || "#090909"),
    labelColor: elements.leftLabelColor.value || "#090909",
    labelFontFamily: normalizeLabelFontFamily(elements.labelFontFamily?.value || state.topic.labelFontFamily),
    showSubLabels: false,
    leftSubLabel: "",
    rightSubLabel: "",
    leftSubLabelColor: DEFAULT_SUBLABEL_COLOR,
    rightSubLabelColor: DEFAULT_SUBLABEL_COLOR,
    leftImage: "assets/placeholder-left.svg",
    rightImage: "assets/placeholder-right.svg",
    leftImageZoom: 1, leftImageX: 0, leftImageY: 0,
    rightImageZoom: 1, rightImageX: 0, rightImageY: 0,
  };
  state.topic.comparisons = [...(state.topic.comparisons || []), scene];
  renderComparisonList();
  jumpPreviewToComparison(scene);
  markDirty();
  sendDraftToPreview();
  scheduleAutoSave(500);
}

elements.addComparisonButton.addEventListener("click", () => addComparisonScene("pair"));
elements.addSingleImageButton?.addEventListener("click", () => addComparisonScene("single"));
elements.deleteBaseComparison?.addEventListener("click", () => {
  if (!state.topic || !baseComparisonEnabled()) return;
  state.topic.baseComparisonEnabled = false;
  state.previewComparisonId = "";
  syncBaseComparisonVisibility();
  renderComparisonList();
  jumpPreviewToComparison(state.topic.comparisons?.[0]);
  markDirty();
  sendDraftToPreview();
  scheduleAutoSave(200);
});
const primaryComparisonBlock = document.querySelector(".comparison-block-primary");
primaryComparisonBlock?.addEventListener("pointerdown", jumpPreviewToBaseComparison);
primaryComparisonBlock?.addEventListener("focusin", jumpPreviewToBaseComparison);
elements.comparisonList.addEventListener("pointerdown", (event) => {
  const comparison = comparisonById(event.target.closest("[data-comparison-id]")?.dataset.comparisonId);
  jumpPreviewToComparison(comparison);
});
elements.comparisonList.addEventListener("focusin", (event) => {
  const comparison = comparisonById(event.target.closest("[data-comparison-id]")?.dataset.comparisonId);
  jumpPreviewToComparison(comparison);
});
elements.comparisonList.addEventListener("click", (event) => {
  const button = event.target.closest("[data-action]");
  if (!button || !state.topic) return;
  const id = button.closest("[data-comparison-id]")?.dataset.comparisonId;
  if (button.dataset.action === "crop-comparison-image") {
    const thumb = button.closest(".upload-card")?.querySelector("[data-comparison-thumb]");
    openCropDialog({ type: "comparison", comparisonId: id, side: button.dataset.side, thumb });
    return;
  }
  if (button.dataset.action === "remove-bg-comparison-image") {
    removeBackgroundForSlot({ type: "comparison", comparisonId: id, side: button.dataset.side }, button)
      .catch((error) => showToast(error.message, true));
    return;
  }
  if (button.dataset.action === "clear-comparison-image") {
    const comparison = comparisonById(id);
    if (comparison) clearComparisonImage(comparison, button.dataset.side);
    return;
  }
  if (button.dataset.action === "remove-comparison") {
    if (state.previewComparisonId === id) state.previewComparisonId = "";
    state.topic.comparisons = (state.topic.comparisons || []).filter((item) => item.id !== id);
    Object.keys(state.pendingUploads).filter((key) => key.startsWith(`comparison:${id}:`)).forEach((key) => delete state.pendingUploads[key]);
    renderComparisonList();
    markDirty();
    sendDraftToPreview();
    scheduleAutoSave(200);
  }
});
elements.comparisonList.addEventListener("input", (event) => {
  const card = event.target.closest("[data-comparison-id]");
  const comparison = comparisonById(card?.dataset.comparisonId);
  const field = event.target.dataset.field;
  if (!comparison || !field) return;
  if (field === "startSentence") comparison.startSentence = Number(event.target.value) || 1;
  else if (field.endsWith("ImageZoom")) {
    const side = event.target.dataset.side;
    const frame = comparisonFrame(comparison, side);
    writeComparisonFrame(comparison, side, { ...frame, zoom: Number(event.target.value) });
    applyComparisonThumb(comparison.id, side);
  } else comparison[field] = event.target.value;
  jumpPreviewToComparison(comparison);
  markDirty();
  sendDraftToPreview();
  scheduleAutoSave(field.endsWith("ImageZoom") ? 200 : 500);
});
elements.comparisonList.addEventListener("change", (event) => {
  const input = event.target.closest("[data-comparison-file]");
  if (!input) return;
  const card = input.closest("[data-comparison-id]");
  const comparison = comparisonById(card?.dataset.comparisonId);
  const file = input.files?.[0];
  const side = input.dataset.side;
  if (!comparison || !file || !side) return;
  setComparisonImageFile(comparison, side, file);
});
elements.comparisonList.addEventListener("pointerdown", (event) => {
  const viewport = event.target.closest("[data-comparison-viewport]");
  if (!viewport || event.button !== 0) return;
  const card = viewport.closest("[data-comparison-id]");
  const comparison = comparisonById(card?.dataset.comparisonId);
  const side = viewport.dataset.side;
  const thumb = viewport.querySelector("[data-comparison-thumb]");
  if (!comparison || !thumb) return;
  event.preventDefault();
  const frame = comparisonFrame(comparison, side);
  const layout = imageFrameLayout(thumb.naturalWidth, thumb.naturalHeight, viewport.clientWidth, viewport.clientHeight, frame.zoom, frame.x, frame.y);
  state.imageDrag = {
    comparisonId: comparison.id, side, pointerId: event.pointerId,
    startX: event.clientX, startY: event.clientY, originLeft: layout.left, originTop: layout.top,
    width: layout.width, height: layout.height, viewWidth: viewport.clientWidth || 1, viewHeight: viewport.clientHeight || 1, zoom: frame.zoom,
  };
  viewport.classList.add("dragging");
  viewport.setPointerCapture(event.pointerId);
});
elements.comparisonList.addEventListener("pointermove", (event) => {
  const drag = state.imageDrag;
  if (!drag?.comparisonId || drag.pointerId !== event.pointerId) return;
  const comparison = comparisonById(drag.comparisonId);
  if (!comparison) return;
  const left = drag.originLeft + event.clientX - drag.startX;
  const top = drag.originTop + event.clientY - drag.startY;
  const maxOffsetX = Math.max(0, (drag.width - drag.viewWidth) / 2);
  const maxOffsetY = Math.max(0, (drag.height - drag.viewHeight) / 2);
  writeComparisonFrame(comparison, drag.side, {
    zoom: drag.zoom,
    x: maxOffsetX ? clamp((left - (drag.viewWidth - drag.width) / 2) / maxOffsetX * 50, -50, 50) : 0,
    y: maxOffsetY ? clamp((top - (drag.viewHeight - drag.height) / 2) / maxOffsetY * 50, -50, 50) : 0,
  });
  applyComparisonThumb(comparison.id, drag.side);
  sendDraftToPreview();
});
const endComparisonDrag = (event) => {
  const drag = state.imageDrag;
  if (!drag?.comparisonId || drag.pointerId !== event.pointerId) return;
  event.target.closest("[data-comparison-viewport]")?.classList.remove("dragging");
  state.imageDrag = null;
  markDirty();
  scheduleAutoSave(200);
};
elements.comparisonList.addEventListener("pointerup", endComparisonDrag);
elements.comparisonList.addEventListener("pointercancel", endComparisonDrag);
["left", "right", "background"].forEach((side) => {
  const keys = IMAGE_SIDES[side];
  if (!elements[keys.zoomInput] || !elements[keys.viewport] || !elements[keys.thumb]) return;
  elements[keys.zoomInput].addEventListener("input", () => {
    if (!state.topic) return;
    const frame = readImageFrame(side);
    writeImageFrame(side, { zoom: Number(elements[keys.zoomInput].value), x: frame.x, y: frame.y });
    applyImageFrameToThumb(side);
    markDirty();
    sendDraftToPreview();
    scheduleAutoSave(200);
  });
  bindImageViewport(side);
});
elements.customSfxFile.addEventListener("change", () => { const file = elements.customSfxFile.files[0]; if (file) uploadCustomSfx(file).catch((error) => showToast(error.message, true)); elements.customSfxFile.value = ""; });
elements.autoPoseButton.addEventListener("click", autoSelectPoses);
elements.scriptInput.addEventListener("paste", (event) => {
  if (!event.clipboardData?.getData("text/plain")) return;
  setTimeout(autoSelectPoses, 0);
});
elements.poseSfxMap.addEventListener("change", (event) => {
  const select = event.target.closest("select");
  if (!select || !state.topic) return;
  const row = select.closest("[data-pose]");
  if (!row) return;
  const map = { ...normalizePoseSfx(state.topic.poseSfx) };

  if (select.dataset.field === "pose-map-pose") {
    state.activePoseSfx = select.value;
    renderPoseSfxMap();
    return;
  }
  if (select.dataset.field === "pose-map-sfx") {
    map[row.dataset.pose] = select.value;
    state.topic.poseSfx = normalizePoseSfx(map);
    markDirty(); sendDraftToPreview(); scheduleAutoSave(200);
  }
});
elements.poseSfxMap.addEventListener("click", (event) => {
  const button = event.target.closest('[data-action="preview-pose-sfx"]');
  if (!button) return;
  previewSfx(sfxForPose(button.dataset.pose || state.activePoseSfx));
});
elements.poseList.addEventListener("change", (event) => {
  const row = event.target.closest("[data-index]"); if (!row) return;
  const index = Number(row.dataset.index);
  if (event.target.dataset.field === "pose") state.poseBySegment[index].pose = event.target.value;
  if (event.target.dataset.field === "speaker") {
    state.speakerBySegment[index] = { speaker: resolveSpeakerKey(event.target.value, state.topic) || defaultSpeakerForIndex(index, scriptLines().length) };
  }
  markDirty(); sendDraftToPreview(); scheduleAutoSave(250);
});
elements.minusButton.addEventListener("click", () => stepPreview(-1));
elements.plusButton.addEventListener("click", () => stepPreview(1));
elements.renderButton.addEventListener("click", async () => {
  if (state.dirty && !(await saveEditor())) return;
  window.location.href = `/?project=${encodeURIComponent(project)}`;
});
window.addEventListener("paste", async (event) => {
  if (!state.topic) return;
  const clipboardImages = [...(event.clipboardData?.items || [])]
    .filter((item) => item.kind === "file" && item.type.startsWith("image/"))
    .map((item) => item.getAsFile())
    .filter(Boolean);
  if (!clipboardImages.length) return;
  event.preventDefault();
  const images = clipboardImages.map(normalizedClipboardImage).filter(Boolean);
  if (!images.length) {
    showToast("Ảnh clipboard cần ở định dạng PNG, JPG hoặc WebP.", true);
    return;
  }
  const pasteMode = currentPasteImageMode();
  const cropMode = pasteMode === "square";
  let placed = 0;
  const destinations = [];
  const usedAspects = new Set();
  for (const file of images) {
    const slot = nextEmptyImageSlot();
    if (!slot) break;
    const frameAspect = slot.type === "comparison" ? comparisonFrameAspect(slot.comparison) : 1;
    let frame = null;
    try {
      frame = cropMode ? await frameForClipboardImage(file, frameAspect) : null;
    } catch (error) {
      showToast(error.message, true);
      return;
    }
    if (slot.type === "base") setBaseImageFile(slot.side, file, frame);
    else setComparisonImageFile(slot.comparison, slot.side, file, frame);
    if (cropMode) usedAspects.add(frameAspect === 1 ? "1:1" : "16:9");
    placed += 1;
    const comparisonName = slot.type === "base"
      ? (slot.side === "left" ? "Ảnh bên trái" : "Ảnh bên phải")
      : comparisonSceneName(slot.comparison);
    destinations.push(slot.type === "comparison" && comparisonLayout(slot.comparison) === "single"
      ? comparisonName
      : `${comparisonName} · bên ${slot.side === "left" ? "trái" : "phải"}`);
  }
  if (!placed) {
    showToast("Không còn ô ảnh trống. Hãy xoá một ảnh trước khi dán.", true);
    return;
  }
  const remaining = images.length - placed;
  const cropLabel = usedAspects.size ? `${tr("Đã căn khung", "Framed")} ${[...usedAspects].join(" / ")} ${tr("và dán", "and pasted")}` : tr("Đã dán", "Pasted");
  showToast(`${cropLabel} ${placed} ${tr("ảnh vào", "image(s) into")} ${destinations.join(", ")}${remaining ? `; ${tr("còn", "")} ${remaining} ${tr("ảnh chưa có ô trống", "image(s) had no empty slot")}` : ""}.`);
});

window.addEventListener("beforeunload", (event) => { if (state.dirty) { event.preventDefault(); event.returnValue = ""; } });
window.addEventListener("keydown", (event) => {
  if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
  const target = event.target;
  if (target instanceof HTMLInputElement || target instanceof HTMLTextAreaElement || target instanceof HTMLSelectElement || target?.isContentEditable) return;
  event.preventDefault();
  stepPreview(event.key === "ArrowRight" ? 1 : -1);
});
new ResizeObserver(fitPreviewFrame).observe(elements.phoneFrame.parentElement);
fitPreviewFrame();
loadProject();
