const project = new URLSearchParams(location.search).get("project");
const $ = (selector) => document.querySelector(selector);
const state = {
  topic: null,
  timer: 0,
  saving: false,
  savePromise: null,
  selected: "",
  previewSentence: 1,
  backgroundPreviewUrl: "",
  introMediaPreviewUrl: "",
  introLogoPreviewUrl: "",
};
let previewFrameRaf = 0;
let pendingPreviewSlideId = "";

const isEnglishUi = () => (window.__AUREX_LANGUAGE__ || document.documentElement.lang) === "en";
const tr = (vi, en) => isEnglishUi() ? en : vi;

const DEFAULT_FONT = '"Inter", -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif';
const DEFAULT_BACKGROUND_COLOR = "#f5eee3";
const DEFAULT_INTRO_COLOR = "#0b1020";
const DEFAULT_MUSIC_VOLUME = 0.18;
const CUSTOM_INTRO_DURATION_SECONDS = 3;
const MAX_TEXT_LAYERS = 8;
const FONT_OPTIONS = [
  [DEFAULT_FONT, "Inter · Mặc định", "Inter · Default"],
  ['"Manrope", "Inter", sans-serif', "Manrope · Hiện đại", "Manrope · Modern"],
  ['"Literata", Georgia, serif', "Literata · Thanh lịch", "Literata · Elegant"],
  ['"Nunito", "Inter", sans-serif', "Nunito · Thân thiện", "Nunito · Friendly"],
  ['"Playfair Display", Georgia, serif', "Playfair · Cổ điển", "Playfair · Classic"],
  ['"Be Vietnam Pro", "Inter", sans-serif', "Be Vietnam Pro · Việt", "Be Vietnam Pro · Vietnamese"],
  ['"Lexend", "Inter", sans-serif', "Lexend · Rõ nét", "Lexend · Clear"],
  ['"Quicksand", "Inter", sans-serif', "Quicksand · Tròn mềm", "Quicksand · Soft round"],
  ['"Saira", "Inter", sans-serif', "Saira · Năng động", "Saira · Dynamic"],
  ['"Roboto", "Inter", sans-serif', "Roboto · Phổ thông", "Roboto · Universal"],
];
const EFFECT_OPTIONS = [
  ["none", "Không", "None"],
  ["fade", "Mờ dần", "Fade"],
  ["zoom", "Phóng to", "Zoom"],
  ["rise", "Trượt lên", "Slide up"],
  ["swipe", "Trượt sang", "Slide left"],
];

const api = async (url, options = {}) => {
  const response = await fetch(url, {
    cache: "no-store",
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body.error || `HTTP ${response.status}`);
  return body;
};

const escapeHtml = (value) => String(value ?? "").replace(/[&<>'"]/g, (char) => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;",
})[char]);

const token = (prefix) => {
  const id = globalThis.crypto?.randomUUID?.() || Math.random().toString(36).slice(2);
  return `${prefix}-${id.replaceAll("-", "").slice(0, 12)}`;
};

const clamp = (value, low, high) => {
  const numeric = Number(value);
  return Math.min(high, Math.max(low, Number.isFinite(numeric) ? numeric : low));
};

function lines() {
  return String($("#script")?.value || "")
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean);
}

function retime() {
  if (!state.topic) return;
  const copy = lines();
  const count = Math.max(1, copy.length);
  const duration = Math.max(1, count * 3);
  state.topic.duration = duration;
  state.topic.segments = (copy.length ? copy : [""]).map((text, index) => ({
    text,
    start: +(index * duration / count).toFixed(3),
    end: +((index + 1) * duration / count).toFixed(3),
  }));
}

function defaultTextLayer(y = 20) {
  return {
    id: token("text"), type: "text", text: "", font: DEFAULT_FONT, color: "#090909", fontSize: 1.2,
    x: 0, y, w: 100, h: 12,
  };
}

function defaultMediaLayer() {
  return {
    id: token("media"), type: "image", src: "assets/placeholder-left.svg",
    x: 0, y: 0, w: 100, h: 100, zoom: 1, offsetX: 0, offsetY: 0,
  };
}

function defaultIntro() {
  return {
    type: "none",
    duration: CUSTOM_INTRO_DURATION_SECONDS,
    color: DEFAULT_INTRO_COLOR,
    media: "",
    mediaType: "image",
    mediaZoom: 1,
    mediaX: 0,
    mediaY: 0,
    logo: "",
    logoScale: 1,
    title: "",
    titleColor: "#ffffff",
    titleSize: 1,
  };
}

function ensureIntro() {
  if (!state.topic) return defaultIntro();
  const current = state.topic.intro && typeof state.topic.intro === "object" ? state.topic.intro : {};
  state.topic.intro = { ...defaultIntro(), ...current, duration: CUSTOM_INTRO_DURATION_SECONDS };
  const rawType = String(state.topic.intro.type || "").toLowerCase();
  state.topic.intro.type = ["none", "color", "media"].includes(rawType)
    ? rawType
    : ["image", "video"].includes(rawType) ? "media" : "none";
  if (rawType === "video") state.topic.intro.mediaType = "video";
  state.topic.intro.mediaZoom = clamp(state.topic.intro.mediaZoom, 1, 3);
  state.topic.intro.mediaX = clamp(state.topic.intro.mediaX, -50, 50);
  state.topic.intro.mediaY = clamp(state.topic.intro.mediaY, -50, 50);
  state.topic.intro.logoScale = clamp(state.topic.intro.logoScale, 0.5, 1.6);
  state.topic.intro.titleSize = clamp(state.topic.intro.titleSize, 0.6, 1.5);
  return state.topic.intro;
}

function isVideoAssetPath(value) {
  return /\.(mp4|webm|m4v|mov)(?:[?#]|$)/i.test(String(value || ""));
}

function defaultSlide(startSentence = 1, sourceSlide = null) {
  if (sourceSlide) {
    const layers = (Array.isArray(sourceSlide.layers) ? sourceSlide.layers : []).map((layer) => {
      if (layer.type === "text") {
        return {
          ...defaultTextLayer(clamp(layer.y, 0, 92)),
          x: clamp(layer.x, 0, 92), y: clamp(layer.y, 0, 92), w: clamp(layer.w, 8, 100), h: clamp(layer.h, 6, 100),
          font: normalizeFont(layer.font), color: layer.color || "#090909", fontSize: clamp(layer.fontSize, 0.5, 2),
        };
      }
      if (layer.type === "image") {
        return {
          ...defaultMediaLayer(),
          x: clamp(layer.x, 0, 92), y: clamp(layer.y, 0, 92), w: clamp(layer.w, 8, 100), h: clamp(layer.h, 6, 100),
        };
      }
      return null;
    }).filter(Boolean);
    return {
      id: token("slide"), startSentence: Math.max(1, Number(startSentence) || 1), enterEffect: "fade",
      layers: layers.length ? layers : [defaultTextLayer(), defaultMediaLayer()],
    };
  }
  return {
    id: token("slide"), startSentence: Math.max(1, Number(startSentence) || 1), enterEffect: "fade",
    layers: [defaultTextLayer(), defaultMediaLayer()],
  };
}

function ensureSlide(slide) {
  if (!Array.isArray(slide.layers)) slide.layers = [];
  if (!slide.layers.some((layer) => layer?.type === "text")) slide.layers.unshift(defaultTextLayer());
  if (!slide.layers.some((layer) => layer?.type === "image")) slide.layers.push(defaultMediaLayer());
  slide.layers = slide.layers.filter((layer) => layer && (layer.type === "text" || layer.type === "image"));
  slide.layers.filter((layer) => layer.type === "text").forEach((layer) => {
    layer.font = normalizeFont(layer.font);
    layer.fontSize = clamp(layer.fontSize, 0.5, 2);
  });
  return slide;
}

function isStockLayout(layer, layout) {
  return layer && ["x", "y", "w", "h"].every((key) => Math.abs(Number(layer[key]) - layout[key]) < 0.01);
}

function migrateLegacyStockLayout() {
  let changed = false;
  for (const slide of state.topic?.slides || []) {
    const text = (slide.layers || []).find((layer) => layer?.type === "text");
    const media = (slide.layers || []).find((layer) => layer?.type === "image");
    if (isStockLayout(text, { x: 6, y: 16, w: 88, h: 14 })) {
      Object.assign(text, { x: 0, y: 20, w: 100, h: 12 });
      changed = true;
    }
    if (isStockLayout(media, { x: 6, y: 34, w: 88, h: 38 })
      || isStockLayout(media, { x: 0, y: 34, w: 100, h: 32 })) {
      Object.assign(media, { x: 0, y: 0, w: 100, h: 100 });
      changed = true;
    }
  }
  return changed;
}

function slideTextLayers(slide) {
  return (slide?.layers || []).filter((layer) => layer.type === "text");
}

function slideTextLayer(slide, id = "") {
  return slideTextLayers(slide).find((layer) => !id || layer.id === id) || slideTextLayers(slide)[0] || null;
}

function slideMediaLayer(slide) {
  return (slide?.layers || []).find((layer) => layer.type === "image") || null;
}

function normalizeFont(value) {
  const raw = String(value || "").trim();
  if (raw.toLowerCase() === "default") return DEFAULT_FONT;
  return FONT_OPTIONS.some(([stack]) => stack === raw) ? raw : DEFAULT_FONT;
}

function fontOptions(selected) {
  const current = normalizeFont(selected);
  return FONT_OPTIONS.map(([stack, vi, en]) => `<option value="${escapeHtml(stack)}" ${stack === current ? "selected" : ""}>${escapeHtml(tr(vi, en))}</option>`).join("");
}

function effectOptions(selected) {
  const current = EFFECT_OPTIONS.some(([id]) => id === selected) ? selected : "fade";
  return EFFECT_OPTIONS.map(([id, vi, en]) => `<option value="${id}" ${id === current ? "selected" : ""}>${escapeHtml(tr(vi, en))}</option>`).join("");
}

function sentenceOptions(selected) {
  const script = lines();
  if (!script.length) return `<option value="1" selected>${tr("Câu", "Sentence")} 1</option>`;
  return script.map((line, index) => {
    const number = index + 1;
    const preview = line.length > 42 ? `${line.slice(0, 42)}…` : line;
    return `<option value="${number}" ${number === Number(selected) ? "selected" : ""}>${tr("Câu", "Sentence")} ${number} · ${escapeHtml(preview)}</option>`;
  }).join("");
}

function isPlaceholder(src) {
  const value = String(src || "");
  return !value || value === "assets/placeholder-left.svg" || value.endsWith("/placeholder-left.svg") || value.endsWith("/placeholder-right.svg");
}

function placeholderUrl() {
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="900" height="1600" viewBox="0 0 900 1600"><rect width="900" height="1600" fill="#fffaf0"/><rect x="28" y="28" width="844" height="1544" rx="42" fill="none" stroke="#de370d" stroke-width="8" stroke-dasharray="18 16"/><circle cx="450" cy="650" r="92" fill="#de370d" opacity=".16"/><path d="M400 650h100M450 600v100" stroke="#de370d" stroke-width="20" stroke-linecap="round"/><text x="450" y="850" text-anchor="middle" fill="#4b3a29" font-family="Arial,sans-serif" font-size="64" font-weight="700">Ảnh</text></svg>`;
  return `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`;
}

function assetUrl(path) {
  const value = String(path || "");
  if (!value || value.startsWith("data:") || value.startsWith("blob:") || value.startsWith("/")) return value;
  return `/project/${encodeURIComponent(project)}/${value.split("/").map(encodeURIComponent).join("/")}`;
}

function slideImageUrl(media) {
  return isPlaceholder(media?.src) ? placeholderUrl() : assetUrl(media.src);
}

function renderSlides() {
  const container = $("#slides");
  if (!container || !state.topic) return;
  const max = Math.max(1, lines().length);
  state.topic.slides = Array.isArray(state.topic.slides) && state.topic.slides.length ? state.topic.slides : [defaultSlide(1)];
  container.innerHTML = state.topic.slides.map((rawSlide, index) => {
    const slide = ensureSlide(rawSlide);
    const texts = slideTextLayers(slide);
    const media = slideMediaLayer(slide);
    const textRows = texts.map((text) => `
      <div class="slide-text-row" data-layer-id="${escapeHtml(text.id)}">
        <div class="comparison-label-field">
          <label class="sr-only">${tr("Chữ trên slide", "Slide text")}</label>
          <div>
            <input data-text data-layer-id="${escapeHtml(text.id)}" placeholder="${tr("Tiêu đề hoặc chữ trên slide", "Title or text on the slide")}" value="${escapeHtml(text.text || "")}" />
            <input class="label-color-input" data-color data-layer-id="${escapeHtml(text.id)}" type="color" value="${escapeHtml(text.color || "#090909")}" aria-label="${tr("Màu chữ trên slide", "Slide text color")}" />
          </div>
        </div>
        <label class="comparison-font-row"><span>${tr("Font chữ", "Text font")}</span><select data-font data-layer-id="${escapeHtml(text.id)}">${fontOptions(text.font)}</select>${texts.length > 1 ? `<button class="button tiny danger" data-action="remove-text" data-layer-id="${escapeHtml(text.id)}" type="button">${tr("Xoá", "Delete")}</button>` : ""}</label>
        <label class="image-zoom"><span>${tr("Cỡ chữ", "Text size")} <output data-size-output data-layer-id="${escapeHtml(text.id)}">${Math.round(Number(text.fontSize || 1.2) * 100)}%</output></span><input data-size data-layer-id="${escapeHtml(text.id)}" type="range" min="0.5" max="2" step="0.05" value="${Number(text.fontSize || 1.2)}" /></label>
      </div>`).join("");
    return `<section class="slide-card comparison-block" data-id="${escapeHtml(slide.id)}">
      <div class="comparison-block-heading">
        <div class="slide-card-title"><strong>Slide ${index + 1}</strong><button class="button tiny danger comparison-remove" data-action="remove" type="button">${tr("Xoá", "Delete")}</button></div>
        <label class="comparison-start">${tr("Bắt đầu từ câu", "Start at sentence")}<select data-start>${sentenceOptions(Math.min(max, Math.max(1, Number(slide.startSentence) || 1)))}</select></label>
      </div>
      <div class="slide-text-list">${textRows}</div>
      <button class="button secondary add-scene-button" data-action="add-text" type="button" ${texts.length >= MAX_TEXT_LAYERS ? "disabled" : ""}>＋ ${tr("Thêm chữ", "Add text")}</button>
      <div class="upload-grid single-image-upload-grid">
        <div class="upload-card">
          <span class="sr-only">${tr("Ảnh", "Image")}</span>
          <div class="image-viewport" data-image-viewport title="${tr("Kéo để di chuyển vị trí hiển thị", "Drag to reposition")}"><img data-thumb src="${escapeHtml(slideImageUrl(media))}" alt="" draggable="false" /></div>
          <label class="image-zoom"><span>Zoom <output data-zoom-output>${Math.round(Number(media.zoom || 1) * 100)}%</output></span><input data-zoom type="range" min="0.1" max="3" step="0.01" value="${Number(media.zoom || 1)}" /></label>
          <div class="image-actions"><label class="replace-image">${tr("Thay ảnh", "Replace media")}<input data-image type="file" accept="image/png,image/jpeg,image/webp" /></label><button class="delete-image" data-action="clear-media" type="button">${tr("Xoá", "Delete")}</button></div>
        </div>
      </div>
      <div class="slide-effect-tools"><label class="comparison-font-row slide-effect-row"><span>${tr("Hiệu ứng", "Effect")}</span><select data-effect>${effectOptions(slide.enterEffect)}</select></label></div>
    </section>`;
  }).join("");
  highlightActiveSlide();
  updatePreviewCounter();
}

function updatePreviewCounter() {
  const total = Math.max(1, lines().length);
  state.previewSentence = clamp(state.previewSentence, 1, total);
  const counter = $("#previewTime");
  if (counter) counter.textContent = `${state.previewSentence}/${total}`;
}

function highlightActiveSlide() {
  $("#slides")?.querySelectorAll("[data-id]").forEach((card) => {
    card.classList.toggle("is-active", card.dataset.id === state.selected);
  });
}

function activeSlideForSentence(sentence) {
  const slides = [...(state.topic?.slides || [])].sort((left, right) => Number(left.startSentence) - Number(right.startSentence));
  return slides.filter((slide) => Number(slide.startSentence) <= sentence).at(-1) || slides[0] || null;
}

function flushPreview() {
  previewFrameRaf = 0;
  if (!state.topic) return;
  const slideId = pendingPreviewSlideId || state.selected;
  pendingPreviewSlideId = "";
  const slide = (state.topic.slides || []).find((item) => item.id === slideId) || activeSlideForSentence(state.previewSentence);
  state.selected = slide?.id || "";
  highlightActiveSlide();
  $("#previewFrame")?.contentWindow?.postMessage({
    type: "tho-topic-update", topic: state.topic, slideId: state.selected, time: 0,
  }, location.origin);
}

function sendPreview(slideId = state.selected) {
  if (!state.topic) return;
  pendingPreviewSlideId = slideId || "";
  if (previewFrameRaf) return;
  previewFrameRaf = requestAnimationFrame(flushPreview);
}

function selectSlide(slide, sentence = Number(slide?.startSentence) || 1) {
  if (!slide) return;
  state.selected = slide.id;
  state.previewSentence = sentence;
  updatePreviewCounter();
  highlightActiveSlide();
  sendPreview(slide.id);
}

function fitPreviewFrame() {
  const phoneFrame = $("#phoneFrame");
  const stage = phoneFrame?.parentElement;
  if (!phoneFrame || !stage) return;
  const bounds = stage.getBoundingClientRect();
  const maxWidth = Math.max(1, bounds.width - 14);
  const maxHeight = Math.max(1, bounds.height - 14);
  const width = Math.floor(Math.min(maxWidth, maxHeight * 9 / 16));
  const height = Math.floor(width * 16 / 9);
  phoneFrame.style.width = `${width}px`;
  phoneFrame.style.height = `${height}px`;
  const frame = $("#previewFrame");
  if (frame) {
    frame.style.width = "100%";
    frame.style.height = "100%";
  }
}

function navigateSentence(delta) {
  const total = Math.max(1, lines().length);
  state.previewSentence = clamp(state.previewSentence + delta, 1, total);
  selectSlide(activeSlideForSentence(state.previewSentence), state.previewSentence);
}

function setStatus(text) {
  const node = $("#saveState");
  if (node) node.textContent = text;
}

function queueSave(delay = 350) {
  clearTimeout(state.timer);
  setStatus("Đang chờ tự lưu…");
  state.timer = setTimeout(() => { void save(); }, delay);
}

async function save() {
  if (!state.topic) return false;
  if (state.saving) return state.savePromise || false;
  state.saving = true;
  state.savePromise = (async () => {
    retime();
    setStatus("Đang tự lưu…");
    try {
      const result = await api(`/api/projects/${encodeURIComponent(project)}/topic`, {
        method: "PUT",
        body: JSON.stringify({ ...state.topic, projectType: "custom" }),
      });
      state.topic = result.topic || state.topic;
      state.topic.slides = Array.isArray(state.topic.slides) && state.topic.slides.length ? state.topic.slides : [defaultSlide(1)];
      if (!state.topic.slides.some((slide) => slide.id === state.selected)) state.selected = state.topic.slides[0].id;
      syncSettings();
      updatePreviewCounter();
      sendPreview();
      setStatus("Đã tự lưu");
      return true;
    } catch (error) {
      setStatus(error.message);
      return false;
    } finally {
      state.saving = false;
      state.savePromise = null;
    }
  })();
  return state.savePromise;
}

function fileData(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result);
    reader.onerror = reject;
    reader.readAsDataURL(file);
  });
}

async function uploadProjectAsset(kind, file) {
  return api(`/api/projects/${encodeURIComponent(project)}/upload`, {
    method: "POST",
    body: JSON.stringify({ kind, name: file.name, data: await fileData(file) }),
  });
}

async function uploadSlideImage(slide, file) {
  if (!slide || !file) return;
  try {
    setStatus("Đang tải ảnh…");
    const uploaded = await uploadProjectAsset("leftImage", file);
    slideMediaLayer(slide).src = uploaded.path;
    selectSlide(slide);
    renderSlides();
    queueSave(20);
  } catch (error) {
    setStatus(error.message);
  }
}

function syncIntroSettings() {
  if (!state.topic) return;
  const intro = ensureIntro();
  const type = $("#introType");
  if (type) type.value = intro.type;
  const color = $("#introColor");
  if (color) color.value = intro.color || DEFAULT_INTRO_COLOR;
  const colorField = $("#introColorField");
  if (colorField) colorField.hidden = intro.type === "none";
  const controlsRow = type?.closest(".intro-controls-row");
  if (controlsRow) controlsRow.classList.toggle("intro-color-disabled", intro.type === "none");
  const mediaPanel = $("#introMediaPanel");
  if (mediaPanel) mediaPanel.hidden = intro.type !== "media";

  const mediaPath = String(intro.media || "").trim();
  const mediaSrc = state.introMediaPreviewUrl || (mediaPath ? assetUrl(mediaPath) : "");
  const video = $("#introThumbVideo");
  const image = $("#introThumb");
  const videoMedia = intro.mediaType === "video" || isVideoAssetPath(mediaPath);
  if (videoMedia) {
    if (image) {
      image.hidden = true;
      image.removeAttribute("src");
    }
    if (video) {
      if ((video.getAttribute("src") || "") !== mediaSrc) {
        if (mediaSrc) video.setAttribute("src", mediaSrc);
        else video.removeAttribute("src");
        video.load();
      }
      video.hidden = !mediaSrc;
    }
  } else {
    if (video) {
      video.pause();
      video.hidden = true;
      video.removeAttribute("src");
    }
    if (image) {
      image.hidden = !mediaSrc;
      if (mediaSrc) image.src = mediaSrc;
      else image.removeAttribute("src");
    }
  }
  const mediaZoom = clamp(intro.mediaZoom, 1, 3);
  if ($("#introMediaZoom")) $("#introMediaZoom").value = String(mediaZoom);
  if ($("#introMediaZoomText")) $("#introMediaZoomText").textContent = `${Math.round(mediaZoom * 100)}%`;
  if ($("#deleteIntroMedia")) $("#deleteIntroMedia").disabled = !mediaPath && !state.introMediaPreviewUrl;

  const logoPath = String(intro.logo || "").trim();
  const logoSrc = state.introLogoPreviewUrl || (logoPath ? assetUrl(logoPath) : "");
  const logoThumb = $("#introLogoThumb");
  const logoPlaceholder = $("#introLogoPlaceholder");
  if (logoThumb) {
    logoThumb.hidden = !logoSrc;
    if (logoSrc) logoThumb.src = logoSrc;
    else logoThumb.removeAttribute("src");
  }
  if (logoPlaceholder) logoPlaceholder.hidden = Boolean(logoSrc);
  if ($("#deleteIntroLogo")) $("#deleteIntroLogo").disabled = !logoPath && !state.introLogoPreviewUrl;

  if ($("#introTitle")) $("#introTitle").value = intro.title || "";
  if ($("#introTitleColor")) $("#introTitleColor").value = intro.titleColor || "#ffffff";
  const titleSize = clamp(intro.titleSize, 0.6, 1.5);
  if ($("#introTitleSize")) $("#introTitleSize").value = String(titleSize);
  if ($("#introTitleSizeText")) $("#introTitleSizeText").textContent = `${Math.round(titleSize * 100)}%`;
}

function syncSettings() {
  if (!state.topic) return;
  syncIntroSettings();
  const backgroundType = $("#backgroundType");
  if (backgroundType) backgroundType.value = ["default", "color", "image"].includes(state.topic.backgroundType) ? state.topic.backgroundType : "default";
  const backgroundColor = $("#backgroundColor");
  if (backgroundColor) backgroundColor.value = state.topic.backgroundColor || DEFAULT_BACKGROUND_COLOR;
  const backgroundPanel = $("#backgroundImagePanel");
  if (backgroundPanel) backgroundPanel.hidden = backgroundType?.value !== "image";
  const backgroundThumb = $("#backgroundThumb");
  const backgroundPath = String(state.topic.backgroundImage || "").trim();
  if (backgroundThumb) {
    const src = state.backgroundPreviewUrl || (backgroundPath ? assetUrl(backgroundPath) : "");
    backgroundThumb.hidden = !src;
    if (src) backgroundThumb.src = src;
    else backgroundThumb.removeAttribute("src");
  }
  const backgroundZoom = clamp(state.topic.backgroundImageZoom ?? 1, 1, 3);
  if ($("#backgroundImageZoom")) $("#backgroundImageZoom").value = String(backgroundZoom);
  if ($("#backgroundZoomText")) $("#backgroundZoomText").textContent = `${Math.round(backgroundZoom * 100)}%`;

  const activeColor = $("#karaokeActiveColor");
  const baseColor = $("#karaokeColor");
  const karaokeSize = $("#karaokeSize");
  if (activeColor) activeColor.value = state.topic.karaokeActiveColor || "#de370d";
  if (baseColor) baseColor.value = state.topic.karaokeColor || "#271f11";
  if (karaokeSize) karaokeSize.value = String(state.topic.karaokeSize ?? 1.2);
  if ($("#karaokeSizeText")) $("#karaokeSizeText").textContent = `${Math.round(Number(karaokeSize?.value || 1.2) * 100)}%`;

  const musicPath = String(state.topic.backgroundMusic || "").trim();
  if ($("#backgroundMusicName")) $("#backgroundMusicName").textContent = musicPath ? musicPath.split("/").pop() : "Chưa có file nhạc";
  const musicVolume = clamp(state.topic.backgroundMusicVolume ?? DEFAULT_MUSIC_VOLUME, 0.05, 0.5);
  if ($("#backgroundMusicVolume")) $("#backgroundMusicVolume").value = String(musicVolume);
  if ($("#backgroundMusicVolumeText")) $("#backgroundMusicVolumeText").textContent = `${Math.round(musicVolume * 100)}%`;
  if ($("#deleteBackgroundMusic")) $("#deleteBackgroundMusic").disabled = !musicPath;
}

async function uploadBackgroundImage(file) {
  if (!file || !state.topic) return;
  state.topic.backgroundType = "image";
  state.backgroundPreviewUrl = URL.createObjectURL(file);
  syncSettings();
  try {
    setStatus("Đang tải ảnh nền…");
    const uploaded = await uploadProjectAsset("backgroundImage", file);
    state.topic.backgroundImage = uploaded.path;
    state.backgroundPreviewUrl = "";
    syncSettings();
    sendPreview();
    queueSave(20);
  } catch (error) {
    setStatus(error.message);
  }
}

async function uploadIntroMedia(file) {
  if (!file || !state.topic) return;
  const intro = ensureIntro();
  intro.type = "media";
  intro.mediaType = isVideoAssetPath(file.name) ? "video" : "image";
  if (state.introMediaPreviewUrl) URL.revokeObjectURL(state.introMediaPreviewUrl);
  state.introMediaPreviewUrl = URL.createObjectURL(file);
  syncIntroSettings();
  sendPreview();
  try {
    setStatus("Đang tải intro…");
    const uploaded = await uploadProjectAsset("introMedia", file);
    intro.media = uploaded.path;
    intro.mediaType = uploaded.mediaType || intro.mediaType;
    URL.revokeObjectURL(state.introMediaPreviewUrl);
    state.introMediaPreviewUrl = "";
    syncIntroSettings();
    sendPreview();
    queueSave(20);
  } catch (error) {
    setStatus(error.message);
  }
}

async function uploadIntroLogo(file) {
  if (!file || !state.topic) return;
  const intro = ensureIntro();
  if (state.introLogoPreviewUrl) URL.revokeObjectURL(state.introLogoPreviewUrl);
  state.introLogoPreviewUrl = URL.createObjectURL(file);
  syncIntroSettings();
  sendPreview();
  try {
    setStatus("Đang tải logo intro…");
    const uploaded = await uploadProjectAsset("introLogo", file);
    intro.logo = uploaded.path;
    URL.revokeObjectURL(state.introLogoPreviewUrl);
    state.introLogoPreviewUrl = "";
    syncIntroSettings();
    sendPreview();
    queueSave(20);
  } catch (error) {
    setStatus(error.message);
  }
}

async function uploadBackgroundMusic(file) {
  if (!file || !state.topic) return;
  try {
    setStatus("Đang tải nhạc…");
    const uploaded = await uploadProjectAsset("backgroundMusic", file);
    state.topic.backgroundMusic = uploaded.path;
    state.topic.backgroundMusicEnabled = true;
    syncSettings();
    sendPreview();
    queueSave(20);
  } catch (error) {
    setStatus(error.message);
  }
}

function updateSlideField(target) {
  const card = target.closest("[data-id]");
  const slide = state.topic?.slides?.find((item) => item.id === card?.dataset.id);
  if (!slide) return;
  ensureSlide(slide);
  const layer = slideTextLayer(slide, target.dataset.layerId);
  if (target.matches("[data-text]") && layer) layer.text = target.value;
  if (target.matches("[data-color]") && layer) layer.color = target.value;
  if (target.matches("[data-font]") && layer) layer.font = normalizeFont(target.value);
  if (target.matches("[data-size]") && layer) {
    layer.fontSize = clamp(target.value, 0.5, 2);
    const output = card.querySelector(`[data-size-output][data-layer-id="${CSS.escape(layer.id)}"]`);
    if (output) output.textContent = `${Math.round(layer.fontSize * 100)}%`;
  }
  const media = slideMediaLayer(slide);
  if (target.matches("[data-zoom]") && media) {
    media.zoom = clamp(target.value, 0.1, 3);
    const output = card.querySelector("[data-zoom-output]");
    if (output) output.textContent = `${Math.round(media.zoom * 100)}%`;
  }
  if (target.matches("[data-start]")) {
    slide.startSentence = Math.max(1, Number(target.value) || 1);
    state.previewSentence = slide.startSentence;
  }
  if (target.matches("[data-effect]")) slide.enterEffect = target.value;
  state.selected = slide.id;
  updatePreviewCounter();
  highlightActiveSlide();
  sendPreview(slide.id);
  queueSave();
}

$("#script")?.addEventListener("input", () => {
  retime();
  renderSlides();
  updatePreviewCounter();
  sendPreview();
  queueSave();
});

$("#slides")?.addEventListener("input", (event) => {
  if (event.target.matches("[data-image]")) return;
  updateSlideField(event.target);
});

$("#slides")?.addEventListener("change", async (event) => {
  if (event.target.matches("[data-image]")) {
    const card = event.target.closest("[data-id]");
    const slide = state.topic?.slides?.find((item) => item.id === card?.dataset.id);
    await uploadSlideImage(slide, event.target.files?.[0]);
    event.target.value = "";
    return;
  }
  updateSlideField(event.target);
});

$("#slides")?.addEventListener("focusin", (event) => {
  const card = event.target.closest("[data-id]");
  const slide = state.topic?.slides?.find((item) => item.id === card?.dataset.id);
  if (slide) selectSlide(slide);
});

$("#slides")?.addEventListener("click", (event) => {
  const action = event.target.closest("[data-action]");
  const card = event.target.closest("[data-id]");
  const slide = state.topic?.slides?.find((item) => item.id === card?.dataset.id);
  if (!slide) return;
  if (action?.dataset.action === "remove") {
    state.topic.slides = state.topic.slides.filter((item) => item !== slide);
    if (!state.topic.slides.length) state.topic.slides = [defaultSlide(1)];
    state.selected = state.topic.slides[0].id;
    renderSlides();
    sendPreview();
    queueSave(50);
    return;
  }
  if (action?.dataset.action === "add-text") {
    const texts = slideTextLayers(slide);
    if (texts.length >= MAX_TEXT_LAYERS) return;
    slide.layers.push(defaultTextLayer(Math.min(80, 16 + texts.length * 15)));
    renderSlides();
    selectSlide(slide);
    queueSave(50);
    return;
  }
  if (action?.dataset.action === "remove-text") {
    if (slideTextLayers(slide).length <= 1) return;
    slide.layers = slide.layers.filter((layer) => layer.id !== action.dataset.layerId);
    renderSlides();
    selectSlide(slide);
    queueSave(50);
    return;
  }
  if (action?.dataset.action === "clear-media") {
    const media = slideMediaLayer(slide);
    Object.assign(media, defaultMediaLayer(), { id: media.id });
    renderSlides();
    selectSlide(slide);
    queueSave(50);
    return;
  }
  selectSlide(slide);
});

$("#addSlideButton")?.addEventListener("click", () => {
  if (!state.topic) return;
  const max = Math.max(1, lines().length);
  const startSentence = Math.min(max, state.topic.slides.length + 1);
  const previous = state.topic.slides.at(-1);
  const slide = defaultSlide(startSentence, previous);
  state.topic.slides.push(slide);
  renderSlides();
  selectSlide(slide, startSentence);
  queueSave(50);
});

$("#previousSentence")?.addEventListener("click", () => navigateSentence(-1));
$("#nextSentence")?.addEventListener("click", () => navigateSentence(1));

$("#editorForm")?.addEventListener("input", (event) => {
  const target = event.target;
  if (!state.topic || ![
    "backgroundColor", "backgroundImageZoom", "karaokeActiveColor", "karaokeColor", "karaokeSize", "backgroundMusicVolume",
    "introColor", "introMediaZoom", "introTitle", "introTitleColor", "introTitleSize",
  ].includes(target.id)) return;
  if (target.id === "backgroundColor") state.topic.backgroundColor = target.value;
  if (target.id === "backgroundImageZoom") state.topic.backgroundImageZoom = clamp(target.value, 1, 3);
  if (target.id === "karaokeActiveColor") state.topic.karaokeActiveColor = target.value;
  if (target.id === "karaokeColor") state.topic.karaokeColor = target.value;
  if (target.id === "karaokeSize") state.topic.karaokeSize = clamp(target.value, 0.6, 1.5);
  if (target.id === "backgroundMusicVolume") state.topic.backgroundMusicVolume = clamp(target.value, 0.05, 0.5);
  const intro = ensureIntro();
  if (target.id === "introColor") intro.color = target.value;
  if (target.id === "introMediaZoom") intro.mediaZoom = clamp(target.value, 1, 3);
  if (target.id === "introTitle") intro.title = target.value;
  if (target.id === "introTitleColor") intro.titleColor = target.value;
  if (target.id === "introTitleSize") intro.titleSize = clamp(target.value, 0.6, 1.5);
  syncSettings();
  sendPreview();
  queueSave();
});

$("#editorForm")?.addEventListener("change", (event) => {
  const target = event.target;
  if (!state.topic) return;
  if (target.id === "backgroundType") {
    state.topic.backgroundType = ["default", "color", "image"].includes(target.value) ? target.value : "default";
    syncSettings();
    if (target.value === "image" && !state.topic.backgroundImage) $("#backgroundImageFile")?.click();
    sendPreview();
    queueSave();
  }
  if (target.id === "introType") {
    const intro = ensureIntro();
    intro.type = ["none", "color", "media"].includes(target.value) ? target.value : "none";
    syncSettings();
    if (intro.type === "media" && !intro.media) $("#introMediaFile")?.click();
    sendPreview();
    queueSave();
  }
});

$("#backgroundImageFile")?.addEventListener("change", async (event) => {
  await uploadBackgroundImage(event.target.files?.[0]);
  event.target.value = "";
});

$("#introMediaFile")?.addEventListener("change", async (event) => {
  await uploadIntroMedia(event.target.files?.[0]);
  event.target.value = "";
});

$("#introLogoFile")?.addEventListener("change", async (event) => {
  await uploadIntroLogo(event.target.files?.[0]);
  event.target.value = "";
});

$("#deleteBackgroundImage")?.addEventListener("click", () => {
  if (!state.topic) return;
  state.topic.backgroundImage = "";
  state.topic.backgroundType = "default";
  state.backgroundPreviewUrl = "";
  syncSettings();
  sendPreview();
  queueSave(50);
});

$("#deleteIntroMedia")?.addEventListener("click", () => {
  if (!state.topic) return;
  const intro = ensureIntro();
  intro.media = "";
  intro.mediaType = "image";
  intro.type = "color";
  if (state.introMediaPreviewUrl) URL.revokeObjectURL(state.introMediaPreviewUrl);
  state.introMediaPreviewUrl = "";
  syncIntroSettings();
  sendPreview();
  queueSave(50);
});

$("#deleteIntroLogo")?.addEventListener("click", () => {
  if (!state.topic) return;
  const intro = ensureIntro();
  intro.logo = "";
  if (state.introLogoPreviewUrl) URL.revokeObjectURL(state.introLogoPreviewUrl);
  state.introLogoPreviewUrl = "";
  syncIntroSettings();
  sendPreview();
  queueSave(50);
});

$("#backgroundMusicFile")?.addEventListener("change", async (event) => {
  await uploadBackgroundMusic(event.target.files?.[0]);
  event.target.value = "";
});

$("#deleteBackgroundMusic")?.addEventListener("click", () => {
  if (!state.topic) return;
  state.topic.backgroundMusic = "";
  state.topic.backgroundMusicEnabled = false;
  syncSettings();
  sendPreview();
  queueSave(50);
});

$("#renderButton")?.addEventListener("click", async () => {
  const saved = await save();
  if (saved) location.href = `/?project=${encodeURIComponent(project)}`;
});

async function load() {
  if (!project) {
    location.href = "/";
    return;
  }
  try {
    const result = await api(`/api/projects/${encodeURIComponent(project)}/topic`);
    if (result.topic.projectType !== "custom") {
      location.replace(`/project/${encodeURIComponent(project)}/`);
      return;
    }
    state.topic = result.topic;
    ensureIntro();
    state.topic.slides = Array.isArray(state.topic.slides) && state.topic.slides.length ? state.topic.slides : [defaultSlide(1)];
    const migratedLegacyLayout = migrateLegacyStockLayout();
    $("#projectTitle").textContent = project;
    $("#script").value = (state.topic.segments || []).map((segment) => segment.text || "").join("\n");
    state.previewSentence = 1;
    state.selected = state.topic.slides[0].id;
    syncSettings();
    renderSlides();
    fitPreviewFrame();
    const frame = $("#previewFrame");
    frame.src = `/index.html?topic=${encodeURIComponent(`/project/${project}/topic.json`)}&render=1&preview=1`;
    frame.addEventListener("load", () => {
      sendPreview();
      setTimeout(sendPreview, 250);
      setTimeout(sendPreview, 900);
    });
    setStatus("Tự động lưu đã bật");
    if (migratedLegacyLayout) queueSave(50);
  } catch (error) {
    setStatus(error.message);
  }
}

const previewStage = $("#phoneFrame")?.parentElement;
if (previewStage && "ResizeObserver" in window) new ResizeObserver(fitPreviewFrame).observe(previewStage);
window.addEventListener("resize", fitPreviewFrame);
load();
