const DEFAULT_TOPIC = "project/inox-304-vs-316/topic.json";

const elements = {
  stage: document.querySelector("#stage"),
  stageBackgroundImage: document.querySelector("#stageBackgroundImage"),
  stageIntro: document.querySelector("#stageIntro"),
  stageIntroImage: document.querySelector("#stageIntroImage"),
  stageIntroVideo: document.querySelector("#stageIntroVideo"),
  stageIntroLogo: document.querySelector("#stageIntroLogo"),
  stageIntroTitle: document.querySelector("#stageIntroTitle"),
  slideCanvas: document.querySelector("#slideCanvas"),
  leftLabel: document.querySelector("#leftLabel"),
  rightLabel: document.querySelector("#rightLabel"),
  leftSubLabel: document.querySelector("#leftSubLabel"),
  rightSubLabel: document.querySelector("#rightSubLabel"),
  leftImage: document.querySelector("#leftImage"),
  rightImage: document.querySelector("#rightImage"),
  karaoke: document.querySelector("#karaoke"),
  quizText: document.querySelector("#quizText"),
  quizQuestion: document.querySelector("#quizQuestion"),
  quizOptions: document.querySelector("#quizOptions"),
  quizCountdownWrap: document.querySelector("#quizCountdownWrap"),
  quizCountdown: document.querySelector("#quizCountdown"),
  quizAnswer: document.querySelector("#quizAnswer"),
  quizLegacyAnswerCard: document.querySelector("#quizLegacyAnswerCard"),
  teacher: document.querySelector("#teacher"),
  teacherWrap: document.querySelector("#teacherWrap"),
  loading: document.querySelector("#loading"),
  syncMarker: document.querySelector("#syncMarker"),
  voiceover: document.querySelector("#voiceover"),
  backgroundMusic: document.querySelector("#backgroundMusic"),
  playButton: document.querySelector("#playButton"),
  restartButton: document.querySelector("#restartButton"),
  timeText: document.querySelector("#timeText"),
  progress: document.querySelector("#progress"),
};

const params = new URLSearchParams(window.location.search);
const topicUrl = params.get("topic") || DEFAULT_TOPIC;
const renderMode = params.get("render") === "1";
const autoplay = params.get("autoplay") === "1";
const previewFrame = params.get("preview") === "1";
const offlineRender = params.get("offline") === "1";

let topic;
let timedWords = [];
let wordGroups = [];
const CJK_MAX_CHARACTERS = 15;
const LATIN_MAX_CHARACTERS = 26;
let raf = 0;
let currentPose = "";
let lastPoseIndex = -1;
let lastSfxAt = -Infinity;
let sfxPlayers = {};
let previewStopTime = null;
let previewSimulationRaf = 0;
let previewSimulationTime = 0;
let voiceoverAvailable = true;
let poseSwapRaf = 0;
let currentComparisonKey = "";
let currentComparisonScene = null;
let currentCharacterMeta = null;
let lastQuizItemIndex = -1;
let lastHeadingFitKey = "";
const characterMetaCache = new Map();
const characterMetaRequests = new Map();

async function loadCharacterMeta(characterId) {
  const key = String(characterId || "").trim();
  if (!key) {
    currentCharacterMeta = null;
    return;
  }
  if (characterMetaCache.has(key)) {
    currentCharacterMeta = characterMetaCache.get(key);
    return;
  }

  let request = characterMetaRequests.get(key);
  if (!request) {
    request = fetch(`/assets/characters/${encodeURIComponent(key)}/manifest.json`, { cache: "default" })
      .then(async (res) => {
        if (!res.ok) return null;
        const meta = await res.json();
        characterMetaCache.set(key, meta);
        return meta;
      })
      .catch(() => null)
      .finally(() => characterMetaRequests.delete(key));
    characterMetaRequests.set(key, request);
  }
  currentCharacterMeta = await request;
}

function characterLabelColor(fallback) {
  return currentCharacterMeta?.labelColor || fallback;
}
function characterSubLabelColor(fallback) {
  return currentCharacterMeta?.subLabelColor || fallback;
}
let previewComparisonId = "";
let previewSlideId = "";
let previewTimeLock = null;
let presenterLayoutRaf = 0;
let presenterLayoutGeneration = 0;
let lastPresenterSource = "";
let lastIntroStaticKey = "";
let introVisible = false;
let currentTopicPresentationKey = "";
let lastKaraokeGroupKey = "";
let lastKaraokeRenderKey = "";
let lastOfflineLayoutKey = "";
const presenterAlphaBounds = new Map();
const presenterFrameCache = new Map();
const IMPORTED_PRESENTER_GAP_PX = 24;
const IMPORTED_PRESENTER_BOTTOM_PX = 28;
// Keep the Quiz presenter at its original/default vertical position.
const IMPORTED_PRESENTER_QUIZ_LIFT_PX = 0;
const IMPORTED_PRESENTER_MAX_WIDTH = 0.58;
const IMPORTED_PRESENTER_MAX_HEIGHT = 0.52;
const OFFLINE_MEDIA_SYNC_TOLERANCE = 1 / 120;
const OFFLINE_MEDIA_MAX_SEQUENTIAL_DRIFT = 0.02;
const offlineVideoSyncState = {
  source: "",
  poseIndex: null,
  lastTargetTime: null,
};
const offlineMediaSyncStats = {
  seeks: 0,
  skippedSeeks: 0,
  playWaits: 0,
  playWaitMs: 0,
  playDriftCorrections: 0,
  poseChanges: 0,
  poseResets: 0,
  seekWaitMs: 0,
  maxDriftMs: 0,
  maxRequestedDriftMs: 0,
  lastPresentedDriftMs: 0,
};

if (renderMode) document.body.classList.add("render-mode");
if (previewFrame) document.body.classList.add("preview-frame");
if (offlineRender) document.body.classList.add("offline-render");
if (renderMode && !previewFrame && !offlineRender) elements.syncMarker.classList.add("visible");

function resolveTopicAsset(path) {
  return new URL(path, new URL(topicUrl, window.location.href)).href;
}

function activeUiLanguage() {
  let language = "";
  try {
    if (window.parent !== window) language = window.parent.__AUREX_LANGUAGE__;
  } catch (_error) {
    // A standalone preview can be cross-origin; use its own language below.
  }
  if (language !== "en" && language !== "vi") language = window.__AUREX_LANGUAGE__;
  if (language !== "en" && language !== "vi") language = document.documentElement.lang;
  return language === "en" ? "en" : "vi";
}

function customSlidePlaceholderUrl() {
  const label = activeUiLanguage() === "en" ? "Image" : "Ảnh";
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="900" height="1600" viewBox="0 0 900 1600"><rect width="900" height="1600" fill="#fffaf0"/><rect x="28" y="28" width="844" height="1544" rx="42" fill="none" stroke="#de370d" stroke-width="8" stroke-dasharray="18 16"/><circle cx="450" cy="650" r="92" fill="#de370d" opacity=".16"/><path d="M400 650h100M450 600v100" stroke="#de370d" stroke-width="20" stroke-linecap="round"/><text x="450" y="850" text-anchor="middle" fill="#4b3a29" font-family="Arial,sans-serif" font-size="64" font-weight="700">${label}</text></svg>`;
  return `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`;
}

function resolveCustomSlideAsset(path) {
  const value = String(path || "");
  if (!value || value.endsWith("/placeholder-left.svg") || value.endsWith("/placeholder-right.svg")) return customSlidePlaceholderUrl();
  return resolveTopicAsset(path);
}

function formatTime(value) {
  const seconds = Math.max(0, Math.floor(Number(value) || 0));
  return `${String(Math.floor(seconds / 60)).padStart(2, "0")}:${String(seconds % 60).padStart(2, "0")}`;
}

function previewDuration() {
  if (quizItems().length) return quizV2Duration();
  const audioDuration = Number(elements.voiceover.duration);
  if (voiceoverAvailable && Number.isFinite(audioDuration) && audioDuration > 0) return audioDuration;
  return Math.max(0, Number(topic?.duration) || 0);
}

function previewTime() {
  return voiceoverAvailable ? (elements.voiceover.currentTime || 0) : previewSimulationTime;
}

function seekPreview(time) {
  const nextTime = Math.max(0, Number(time) || 0);
  previewSimulationTime = nextTime;
  if (!voiceoverAvailable) return nextTime;
  try {
    elements.voiceover.currentTime = nextTime;
  } catch {
    // A failed media load can leave an audio element with no seekable timeline.
    voiceoverAvailable = false;
  }
  return nextTime;
}

function previewIsPlaying() {
  return !voiceoverAvailable ? Boolean(previewSimulationRaf) : !elements.voiceover.paused && !elements.voiceover.ended;
}

function waitForVoiceoverReady() {
  if (!String(topic?.voiceover || "").trim()) {
    voiceoverAvailable = false;
    return Promise.resolve(false);
  }
  if (!voiceoverAvailable) return Promise.resolve(false);
  return new Promise((resolve) => {
    let settled = false;
    let timeoutId = null;
    const cleanup = () => {
      elements.voiceover.removeEventListener("loadedmetadata", onReady);
      elements.voiceover.removeEventListener("error", onError);
      if (timeoutId !== null) clearTimeout(timeoutId);
    };
    const finish = (available) => {
      if (settled) return;
      settled = true;
      cleanup();
      voiceoverAvailable = available;
      resolve(available);
    };
    const onReady = () => finish(true);
    const onError = () => finish(false);
    if (elements.voiceover.readyState >= 1) {
      finish(true);
      return;
    }
    elements.voiceover.addEventListener("loadedmetadata", onReady, { once: true });
    elements.voiceover.addEventListener("error", onError, { once: true });
    timeoutId = setTimeout(() => finish(false), 10000);
  });
}

function baseComparison(nextTopic = topic) {
  return {
    id: "base",
    startSentence: 1,
    leftLabel: nextTopic.leftLabel,
    rightLabel: nextTopic.rightLabel,
    leftSubLabel: nextTopic.leftSubLabel || "",
    rightSubLabel: nextTopic.rightSubLabel || "",
    leftImage: nextTopic.leftImage,
    rightImage: nextTopic.rightImage,
    leftImageZoom: nextTopic.leftImageZoom,
    leftImageX: nextTopic.leftImageX,
    leftImageY: nextTopic.leftImageY,
    rightImageZoom: nextTopic.rightImageZoom,
    rightImageX: nextTopic.rightImageX,
    rightImageY: nextTopic.rightImageY,
    leftLabelColor: nextTopic.leftLabelColor || nextTopic.labelColor || characterLabelColor("#090909"),
    rightLabelColor: nextTopic.rightLabelColor || nextTopic.labelColor || characterLabelColor("#090909"),
    labelFontFamily: nextTopic.labelFontFamily || '"Inter", -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
    leftSubLabelColor: nextTopic.leftSubLabelColor || characterSubLabelColor("#808080"),
    rightSubLabelColor: nextTopic.rightSubLabelColor || characterSubLabelColor("#808080"),
    showSubLabels: nextTopic.showSubLabels === true
      || Boolean(String(nextTopic.leftSubLabel || "").trim() || String(nextTopic.rightSubLabel || "").trim()),
  };
}

function applyLabelFontFamily(fontFamily) {
  const family = String(fontFamily || '"Inter", -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif');
  elements.leftLabel.style.fontFamily = family;
  elements.rightLabel.style.fontFamily = family;
}

function comparisonAt(time) {
  const comparisons = Array.isArray(topic.comparisons) ? topic.comparisons : [];
  const baseEnabled = topic.baseComparisonEnabled !== false;
  if (previewComparisonId === "base" && baseEnabled) return baseComparison();
  if (previewComparisonId) {
    const locked = comparisons
      .find((item) => String(item.id || "") === previewComparisonId);
    if (locked) return locked;
  }
  let sentence = 1;
  (topic.segments || []).forEach((segment, index) => {
    if (Number(segment.start) <= Number(time) + 0.001) sentence = index + 1;
  });
  let selected = baseEnabled ? baseComparison() : (comparisons[0] || baseComparison());
  [...comparisons]
    .sort((a, b) => Number(a.startSentence) - Number(b.startSentence))
    .forEach((item) => {
      if (Number(item.startSentence) <= sentence) selected = item;
    });
  return selected;
}

function isCustomProject(nextTopic = topic) {
  return String(nextTopic?.projectType || "") === "custom";
}

function customSlideAt(time) {
  const slides = Array.isArray(topic?.slides) ? topic.slides : [];
  if (previewSlideId) {
    const locked = slides.find((slide) => String(slide.id || "") === previewSlideId);
    if (locked) return locked;
  }
  let sentence = 1;
  (topic?.segments || []).forEach((segment, index) => { if (Number(segment.start) <= Number(time) + 0.001) sentence = index + 1; });
  return [...slides].sort((left, right) => Number(left.startSentence) - Number(right.startSentence)).filter((slide) => Number(slide.startSentence) <= sentence).at(-1) || slides[0] || null;
}

function applyCustomSlide(slide) {
  const canvas = elements.slideCanvas;
  if (!canvas) return;
  canvas.hidden = !slide;
  if (!slide) { canvas.replaceChildren(); return; }
  canvas.className = `slide-canvas slide-effect-${String(slide.enterEffect || "fade")}`;
  const key = JSON.stringify(slide);
  if (canvas.dataset.slideKey === key) return;
  canvas.dataset.slideKey = key;
  const fragment = document.createDocumentFragment();
  (Array.isArray(slide.layers) ? slide.layers : []).forEach((layer) => {
    const node = document.createElement("div");
    node.className = `slide-layer slide-layer-${layer.type === "text" ? "text" : "image"}`;
    node.style.left = `${Number(layer.x) || 0}%`; node.style.top = `${Number(layer.y) || 0}%`;
    node.style.width = `${Number(layer.w) || 100}%`;
    node.style.height = `${Number(layer.h) || (layer.type === "text" ? 12 : 100)}%`;
    if (layer.type === "text") {
      const text = document.createElement("p"); text.className = "slide-text"; text.textContent = String(layer.text || "");
      text.style.color = String(layer.color || "#090909"); text.style.fontFamily = String(layer.font || topic.labelFontFamily || "inherit");
      text.style.fontSize = `${Math.max(.5, Math.min(2, Number(layer.fontSize) || 1.2)) * 4.62}cqw`; node.append(text);
    } else {
      const image = document.createElement("img"); image.className = "slide-image"; image.alt = ""; image.src = resolveCustomSlideAsset(layer.src);
      const zoom = Math.max(.1, Math.min(3, Number(layer.zoom) || 1)); const x = Math.max(-50, Math.min(50, Number(layer.offsetX) || 0)); const y = Math.max(-50, Math.min(50, Number(layer.offsetY) || 0));
      image.style.transform = `scale(${zoom}) translate(${x / zoom}%, ${y / zoom}%)`; node.append(image);
    }
    fragment.append(node);
  });
  canvas.replaceChildren(fragment);
}

const CUSTOM_INTRO_DURATION_SECONDS = 3;

function customIntroConfig(nextTopic = topic) {
  const raw = nextTopic?.intro && typeof nextTopic.intro === "object" ? nextTopic.intro : {};
  const rawType = String(raw.type || raw.introType || "none").toLowerCase();
  const type = ["image", "video"].includes(rawType) ? "media" : rawType;
  return { ...raw, type, mediaType: rawType === "video" ? "video" : raw.mediaType };
}

function customIntroIsActive(nextTopic = topic, time = 0) {
  const intro = customIntroConfig(nextTopic);
  return isCustomProject(nextTopic)
    && ["color", "media"].includes(intro.type)
    && Number(time) >= 0
    && Number(time) < CUSTOM_INTRO_DURATION_SECONDS;
}

function introVideoTargetTime(time, duration) {
  const safeDuration = Number(duration);
  if (!Number.isFinite(safeDuration) || safeDuration <= 0) return Math.max(0, Number(time) || 0);
  const value = Math.max(0, Number(time) || 0);
  // Short intro clips loop across the fixed three-second opening window.
  if (safeDuration <= CUSTOM_INTRO_DURATION_SECONDS) return value % safeDuration;
  return Math.min(value, Math.max(0, safeDuration - 0.001));
}

function topicPresentationKey(nextTopic = topic) {
  if (!nextTopic || typeof nextTopic !== "object") return "";
  const presentation = { ...nextTopic };
  if (Array.isArray(presentation.segments)) {
    // Script text changes should only invalidate karaoke timing/markup. Keep
    // the stable segment metadata in the key so retiming, speakers, or pose
    // changes still take the full visual path.
    presentation.segments = presentation.segments.map((segment) => {
      if (!segment || typeof segment !== "object") return segment;
      const copy = { ...segment };
      delete copy.text;
      return copy;
    });
  }
  try {
    return JSON.stringify(presentation);
  } catch (_error) {
    // Messages arrive as JSON, but keep the renderer safe if a test injects a
    // non-serializable object.
    return "__uncacheable__";
  }
}

function hideIntro() {
  const introRoot = elements.stageIntro;
  if (!introRoot) return;
  const video = elements.stageIntroVideo;
  const needsReset = introVisible
    || !introRoot.hidden
    || elements.stage?.classList.contains("intro-active")
    || Boolean(video && !video.paused);
  if (!needsReset) return;
  video?.pause();
  introRoot.hidden = true;
  introRoot.setAttribute("aria-hidden", "true");
  elements.stage?.classList.remove("intro-active");
  introVisible = false;
  lastIntroStaticKey = "";
}

function applyIntro(nextTopic = topic, time = 0) {
  const introRoot = elements.stageIntro;
  if (!introRoot) return false;
  if (!isCustomProject(nextTopic)) {
    hideIntro();
    return false;
  }
  const intro = customIntroConfig(nextTopic);
  const active = ["color", "media"].includes(intro.type)
    && Number(time) >= 0
    && Number(time) < CUSTOM_INTRO_DURATION_SECONDS;
  if (!active) {
    // Standard comparison projects spend almost all of their lifetime here.
    // Avoid touching the DOM or pausing an already-paused video on every
    // animation frame.
    hideIntro();
    return false;
  }

  if (!introVisible) {
    introRoot.hidden = false;
    introRoot.setAttribute("aria-hidden", "false");
    elements.stage?.classList.add("intro-active");
    introVisible = true;
  }

  const video = elements.stageIntroVideo;
  const color = String(intro.color || "#0b1020");
  const mediaPath = intro.type === "media" ? String(intro.media || "").trim() : "";
  const mediaSource = mediaPath ? resolveTopicAsset(mediaPath) : "";
  const videoMedia = String(intro.mediaType || "").toLowerCase() === "video" || isVideoAssetSource(mediaSource);
  const image = elements.stageIntroImage;
  const logoSource = intro.logo ? resolveTopicAsset(intro.logo) : "";
  const logoScale = Math.min(1.6, Math.max(0.5, Number(intro.logoScale) || 1));
  const titleText = String(intro.title || "");
  const titleColor = String(intro.titleColor || "#ffffff");
  const titleSize = Math.min(1.5, Math.max(0.6, Number(intro.titleSize) || 1));
  const staticKey = [
    intro.type,
    color,
    mediaSource,
    videoMedia ? "video" : "image",
    intro.mediaZoom,
    intro.mediaX,
    intro.mediaY,
    logoSource,
    logoScale,
    titleText,
    titleColor,
    titleSize,
  ].map((value) => String(value ?? "")).join("\u001f");

  if (staticKey !== lastIntroStaticKey) {
    lastIntroStaticKey = staticKey;
    introRoot.style.setProperty("--intro-color", color);
    introRoot.style.backgroundColor = "transparent";
    if (videoMedia) {
      if (image) {
        image.hidden = true;
        image.removeAttribute("src");
      }
      if (video) {
        if ((video.getAttribute("src") || "") !== mediaSource) {
          if (mediaSource) video.setAttribute("src", mediaSource);
          else video.removeAttribute("src");
          video.load();
        }
        video.hidden = !mediaSource;
      }
    } else {
      video?.pause();
      if (video) {
        video.hidden = true;
        video.removeAttribute("src");
      }
      if (image) {
        if (image.getAttribute("src") !== mediaSource) {
          if (mediaSource) image.setAttribute("src", mediaSource);
          else image.removeAttribute("src");
        }
        image.hidden = !mediaSource;
      }
    }

    const logo = elements.stageIntroLogo;
    if (logo) {
      if (logo.getAttribute("src") !== logoSource) {
        if (logoSource) logo.setAttribute("src", logoSource);
        else logo.removeAttribute("src");
      }
      logo.hidden = !logoSource;
      if (logoSource) logo.style.width = `${Math.min(62, Math.max(22, 38 * logoScale))}%`;
    }
    const title = elements.stageIntroTitle;
    if (title) {
      title.textContent = titleText;
      title.hidden = !titleText;
      title.style.color = titleColor;
      title.style.setProperty("--intro-title-size", String(titleSize));
    }
  }

  if (videoMedia) {
    if (video && mediaSource && mediaReady(video)) {
      applyImageFrame(video, intro.mediaZoom, intro.mediaX, intro.mediaY);
    }
    if (video && mediaSource && !offlineRender) {
      const duration = Number(video.duration);
      const target = introVideoTargetTime(time, duration);
      if (Math.abs((Number(video.currentTime) || 0) - target) > 0.18) {
        try { video.currentTime = target; } catch (_error) { /* media is still loading */ }
      }
      if (video.paused) video.play().catch(() => {});
    }
  } else if (image && mediaSource) {
    // The frame depends on the responsive slot size, so refresh it only for
    // the active intro path. Inactive standard projects never reach here.
    applyImageFrame(image, intro.mediaZoom, intro.mediaX, intro.mediaY);
  }
  return true;
}

function applyComparisonToView(scene, force = false) {
  if (!scene) return;
  const key = String(scene.id || `sentence-${scene.startSentence || 1}`);
  if (!force && key === currentComparisonKey) return;
  currentComparisonKey = key;
  currentComparisonScene = scene;
  const single = isSingleImageScene(scene);
  const leftSlot = elements.leftImage.parentElement;
  const rightSlot = elements.rightImage.parentElement;
  elements.stage.classList.toggle("single-image-scene", single);
  // A single-image scene is a distinct, centered slot—not the left side of a
  // comparison. Keep the DOM class in sync with the layout contract so the
  // preview and browser renderer share the same semantics.
  leftSlot.classList.toggle("media-slot-single", single);
  leftSlot.classList.toggle("media-slot-left", !single);
  leftSlot.hidden = false;
  rightSlot.hidden = single;
  elements.leftLabel.textContent = formatTopicLabel(scene.leftLabel);
  elements.rightLabel.textContent = single ? "" : formatTopicLabel(scene.rightLabel);
  elements.leftLabel.style.color = String(
    scene.leftLabelColor || scene.labelColor || topic.leftLabelColor || topic.labelColor
      || characterLabelColor("#090909")
  );
  elements.rightLabel.style.color = String(
    scene.rightLabelColor || scene.labelColor || topic.rightLabelColor || topic.labelColor
      || characterLabelColor("#090909")
  );
  applyLabelFontFamily(scene.labelFontFamily || topic.labelFontFamily);
  if (single) {
    elements.rightSubLabel.hidden = true;
    elements.rightSubLabel.textContent = "";
  }
  applySubLabel(elements.leftSubLabel, scene.leftSubLabel, scene.leftSubLabelColor || topic.leftSubLabelColor, scene);
  applySubLabel(elements.rightSubLabel, single ? "" : scene.rightSubLabel, scene.rightSubLabelColor || topic.rightSubLabelColor, scene);
  const leftSrc = singleImagePlaceholderUrl(scene) || resolveTopicAsset(scene.leftImage);
  if (elements.leftImage.src !== leftSrc) elements.leftImage.src = leftSrc;
  if (!single) {
    const rightSrc = resolveTopicAsset(scene.rightImage);
    if (elements.rightImage.src !== rightSrc) elements.rightImage.src = rightSrc;
  }
  applyImageFrame(elements.leftImage, scene.leftImageZoom, scene.leftImageX, scene.leftImageY);
  if (!single) applyImageFrame(elements.rightImage, scene.rightImageZoom, scene.rightImageX, scene.rightImageY);
  requestAnimationFrame(fitHeadings);
}

function applySubLabel(element, text, color, scene) {
  if (!element) return;
  const value = String(text || "").trim();
  const enabled = scene?.showSubLabels === true || Boolean(value);
  if (!enabled || !value) {
    element.hidden = true;
    element.textContent = "";
    return;
  }
  element.hidden = false;
  element.textContent = value;
  element.style.color = String(color || characterSubLabelColor("#808080"));
}

function fitTextToWidth(element, maxCqw, minCqw) {
  const stageWidth = elements.stage.clientWidth || 1080;
  let low = stageWidth * minCqw / 100;
  let high = stageWidth * maxCqw / 100;
  element.style.fontSize = `${high}px`;
  for (let index = 0; index < 12; index += 1) {
    const middle = (low + high) / 2;
    element.style.fontSize = `${middle}px`;
    if (element.scrollWidth <= element.clientWidth) low = middle;
    else high = middle;
  }
  element.style.fontSize = `${Math.max(stageWidth * minCqw / 100, low)}px`;
}

function formatTopicLabel(text) {
  const words = tokenize(text);
  if (words.length < 4) return words.join(" ");
  const mid = Math.ceil(words.length / 2);
  return `${words.slice(0, mid).join(" ")}\n${words.slice(mid).join(" ")}`;
}

function fitLabelPair() {
  const stageWidth = elements.stage.clientWidth || 1080;
  const isEngzy = elements.stage.classList.contains("character-engzy");
  const max = stageWidth * (isEngzy ? 9.4 : 6.7) / 100;
  const min = stageWidth * (isEngzy ? 3.6 : 2.8) / 100;
  let low = min;
  let high = max;
  const labels = isSingleImageScene() ? [elements.leftLabel] : [elements.leftLabel, elements.rightLabel];
  for (let index = 0; index < 12; index += 1) {
    const middle = (low + high) / 2;
    labels.forEach((label) => { label.style.fontSize = `${middle}px`; });
    const fits = labels.every((label) => label.scrollWidth <= label.clientWidth);
    if (fits) low = middle;
    else high = middle;
  }
  const size = Math.max(min, low);
  labels.forEach((label) => { label.style.fontSize = `${size}px`; });
}

function fitHeadings() {
  const headingKey = [
    Math.round(elements.stage.clientWidth || 0),
    Math.round(elements.stage.clientHeight || 0),
    elements.stage.className,
    elements.leftLabel.textContent,
    elements.rightLabel.textContent,
    elements.leftLabel.style.fontFamily,
    elements.rightLabel.style.fontFamily,
    document.fonts?.status || "",
  ].join("\u001f");
  if (headingKey === lastHeadingFitKey) return;
  lastHeadingFitKey = headingKey;
  if (!elements.stage.classList.contains('character-bietchichomet')) fitLabelPair();
  else {
    elements.leftLabel.style.removeProperty('font-size');
    elements.rightLabel.style.removeProperty('font-size');
  }
}

function tokenize(text) {
  return String(text || "").trim().split(/\s+/).filter(Boolean);
}

function scriptCharCounts(text) {
  const sample = String(text || "");
  return {
    ja: (sample.match(/[\u3040-\u30ff\u31f0-\u31ff]/gu) || []).length,
    han: (sample.match(/[\u3400-\u4dbf\u4e00-\u9fff]/gu) || []).length,
    vi: (sample.match(/[ăâđêôơưáàảãạắằẳẵặấầẩẫậéèẻẽẹếềểễệíìỉĩịóòỏõọốồổỗộớờởỡợúùủũụứừửữựýỳỷỹỵ]/giu) || []).length,
    latin: (sample.match(/[A-Za-z]/g) || []).length,
  };
}

function usesCompactCjkText(nextTopic = topic) {
  const language = String(nextTopic?.language || "").toLowerCase();
  if (language === "vi") return false;
  if (language === "ja" || language === "zh") return true;
  const text = (nextTopic?.segments || []).map((segment) => segment.text || "").join("");
  const counts = scriptCharCounts(text);
  const cjk = counts.ja + counts.han;
  const vietBody = counts.vi + counts.latin;
  // Vietnamese scripts that only borrow a few Japanese terms stay word-based.
  if (counts.vi > 0 && vietBody >= Math.max(8, cjk * 2)) return false;
  return counts.ja > 0 || (counts.han > 0 && counts.latin === 0);
}

function usesVietnameseText(nextTopic = topic) {
  if (String(nextTopic?.language || "").toLowerCase() === "vi") return true;
  return /[ăâđêôơưáàảãạắằẳẵặấầẩẫậéèẻẽẹếềểễệíìỉĩịóòỏõọốồổỗộớờởỡợúùủũụứừửữựýỳỷỹỵ]/iu
    .test((nextTopic?.segments || []).map((segment) => segment.text || "").join(" "));
}

function subtitleTokens(text) {
  if (!usesCompactCjkText()) return tokenize(text);
  return Array.from(String(text || "").trim()).filter((character) => !/\s/u.test(character));
}

function maxCjkCharacters(nextTopic = topic) {
  const size = Math.min(1.5, Math.max(0.6, Number(nextTopic?.karaokeSize) || 1.2));
  return Math.max(10, Math.min(CJK_MAX_CHARACTERS, Math.floor(18.5 / size)));
}

function maxLatinCharacters(nextTopic = topic) {
  const size = Math.min(1.5, Math.max(0.6, Number(nextTopic?.karaokeSize) || 1.2));
  return Math.max(18, Math.min(LATIN_MAX_CHARACTERS, Math.floor(31.5 / size)));
}

function buildTimedWords(segments) {
  const words = [];
  for (const segment of segments) {
    if (Array.isArray(segment.words) && segment.words.length) {
      segment.words.forEach((item) => {
        words.push({
          word: item.word || item.text || "",
          start: Number(item.start),
          end: Number(item.end),
          segmentStart: segment.start,
          segmentEnd: segment.end,
        });
      });
      continue;
    }
    const tokens = subtitleTokens(segment.text);
    const duration = Math.max(0.05, segment.end - segment.start);
    const weights = tokens.map((token) => Math.max(1, token.replace(/[^\p{L}\p{N}]/gu, "").length));
    const totalWeight = weights.reduce((sum, value) => sum + value, 0) || tokens.length;
    let cursor = segment.start;
    tokens.forEach((token, index) => {
      const end = index === tokens.length - 1
        ? segment.end
        : cursor + duration * weights[index] / totalWeight;
      words.push({ word: token, start: cursor, end, segmentStart: segment.start, segmentEnd: segment.end });
      cursor = end;
    });
  }
  return words;
}

function buildGroups(words) {
  const groups = [];
  let index = 0;
  const compactCjk = usesCompactCjkText();
  const maxWords = compactCjk ? maxCjkCharacters() : 3;
  const maxChars = compactCjk ? maxWords : maxLatinCharacters();
  while (index < words.length) {
    const segmentEnd = words[index].segmentEnd;
    const candidates = [];
    while (index < words.length && words[index].segmentEnd === segmentEnd) {
      candidates.push(index);
      index += 1;
    }
    let current = [];
    candidates.forEach((wordIndex) => {
      const next = [...current, wordIndex];
      const nextText = next.map((item) => words[item].word).join(compactCjk ? "" : " ");
      if (current.length && (current.length >= maxWords || nextText.length > maxChars)) {
        groups.push(current);
        current = [wordIndex];
      } else {
        current = next;
      }
    });
    if (current.length) groups.push(current);
  }
  return groups;
}

function poseAt(time) {
  let index = 0;
  for (let i = 0; i < topic.poseTimeline.length; i += 1) {
    if (topic.poseTimeline[i].time <= time) index = i;
    else break;
  }
  return { ...topic.poseTimeline[index], index };
}

function activeWordAt(time) {
  // Đồng bộ với renderer FastScene: trong khoảng trống giữa hai word timing,
  // giữ từ gần nhất đã bắt đầu thay vì ẩn subtitle rồi hiện lại.
  let candidate = -1;
  for (let index = 0; index < timedWords.length; index += 1) {
    const word = timedWords[index];
    if (time >= word.start && time <= word.end + 0.08) return index;
    if (word.start <= time) candidate = index;
    if (word.start > time) break;
  }
  const lastEnd = timedWords[timedWords.length - 1]?.end || 0;
  return candidate >= 0 && time <= lastEnd + 0.7 ? candidate : -1;
}

function poseImage(poseName, speaking) {
  const pose = topic.poseAssets[poseName] || topic.poseAssets.question || Object.values(topic.poseAssets)[0];
  return resolveTopicAsset(speaking ? pose.speaking : pose.closed);
}

function poseMediaConfig(poseName) {
  const pose = topic.poseAssets[poseName] || topic.poseAssets.question || Object.values(topic.poseAssets)[0] || {};
  const syncMode = ["scene", "timeline", "freeze"].includes(pose.syncMode) ? pose.syncMode : "scene";
  return {
    syncMode,
    loop: pose.loop !== false,
    loopStart: Math.max(0, Number(pose.loopStart) || 0),
    loopEnd: Math.max(0, Number(pose.loopEnd) || 0),
  };
}

function mediaSource(element) {
  return element.currentSrc || element.src;
}

function mediaReady(element) {
  return element instanceof HTMLVideoElement
    ? element.readyState >= 2 && element.videoWidth > 0 && element.videoHeight > 0
    : element.complete && element.naturalWidth > 0 && element.naturalHeight > 0;
}

function mediaIntrinsicWidth(element) {
  return element instanceof HTMLVideoElement ? element.videoWidth || 1 : element.naturalWidth || 1;
}

function mediaIntrinsicHeight(element) {
  return element instanceof HTMLVideoElement ? element.videoHeight || 1 : element.naturalHeight || 1;
}

function waitForMediaReady(element) {
  if (element instanceof HTMLVideoElement && element.__aurexPoseLoadPromise) {
    // Guard against an already-stuck promise (media never fired ready/error,
    // e.g. a 404 on the offline render mount) so offline render can't deadlock.
    return Promise.race([
      element.__aurexPoseLoadPromise,
      new Promise((resolve) => setTimeout(resolve, 10000)),
    ]);
  }
  if (mediaReady(element)) return Promise.resolve();
  return new Promise((resolve, reject) => {
    let settled = false;
    const cleanup = () => {
      element.removeEventListener("loadeddata", onReady);
      element.removeEventListener("load", onReady);
      element.removeEventListener("error", onError);
      clearTimeout(timer);
    };
    const onReady = () => {
      if (settled) return;
      settled = true;
      cleanup();
      resolve();
    };
    const onError = () => {
      if (settled) return;
      settled = true;
      cleanup();
      reject(new Error(`Không tải được media: ${mediaSource(element)}`));
    };
    const timer = setTimeout(() => {
      if (settled) return;
      settled = true;
      cleanup();
      // Time out instead of hanging forever: a missing/broken media file must
      // not freeze the entire offline render pipeline.
      resolve();
    }, 10000);
    element.addEventListener("loadeddata", onReady, { once: true });
    element.addEventListener("load", onReady, { once: true });
    element.addEventListener("error", onError, { once: true });
  });
}

function presenterWantsVideo(source) {
  // Pose media type is an asset property, not a character-id property. New
  // characters can use video poses too, so detect the actual source instead
  // of maintaining a hard-coded allowlist of character ids.
  return isVideoAssetSource(source);
}

function isSingleImageScene(scene = currentComparisonScene) {
  return String(scene?.layout || "pair").toLowerCase() === "single";
}

function singleImagePlaceholderUrl(scene) {
  if (!isSingleImageScene(scene)) return "";
  if (!String(scene?.leftImage || "").endsWith("/placeholder-left.svg")
    && String(scene?.leftImage || "") !== "assets/placeholder-left.svg") return "";
  const svg = '<svg xmlns="http://www.w3.org/2000/svg" width="1600" height="900" viewBox="0 0 1600 900"><rect width="1600" height="900" fill="#fffaf0"/><rect x="28" y="28" width="1544" height="844" rx="42" fill="none" stroke="#de370d" stroke-width="8" stroke-dasharray="18 16"/><circle cx="800" cy="350" r="92" fill="#de370d" opacity=".16"/><path d="M750 350h100M800 300v100" stroke="#de370d" stroke-width="20" stroke-linecap="round"/><text x="800" y="560" text-anchor="middle" fill="#4b3a29" font-family="Arial, sans-serif" font-size="42" font-weight="700">Ảnh đơn</text></svg>';
  return `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`;
}

function createTeacherElement(isVideo = false) {
  const element = document.createElement(isVideo ? "video" : "img");
  element.className = "teacher";
  element.id = "teacher";
  element.setAttribute("aria-label", "Nhân vật đang trình bày");
  if (isVideo) {
    element.autoplay = true;
    element.muted = true;
    element.playsInline = true;
    element.loop = true;
    element.preload = "auto";
  } else {
    element.alt = "Nhân vật đang trình bày";
  }
  return element;
}

function ensureTeacherElement(isVideo = false) {
  const current = elements.teacher;
  if (!!(current instanceof HTMLVideoElement) === isVideo) return current;
  const next = createTeacherElement(isVideo);
  const currentSrc = mediaSource(current);
  if (currentSrc) next.src = currentSrc;
  current.replaceWith(next);
  elements.teacher = next;
  if (isVideo) {
    next.addEventListener("loadeddata", scheduleImportedPresenterLayout);
    next.addEventListener("seeked", scheduleImportedPresenterLayout);
  } else {
    next.addEventListener("load", scheduleImportedPresenterLayout);
  }
  return next;
}

function isVideoAssetSource(source) {
  return /\.(mp4|webm|m4v|mov)(?:[?#]|$)/i.test(String(source || ""));
}

async function loadPresenterFrame(source) {
  if (presenterFrameCache.has(source)) return presenterFrameCache.get(source);
  if (!isVideoAssetSource(source)) return source;
  const video = document.createElement("video");
  video.crossOrigin = "anonymous";
  video.preload = "auto";
  video.playsInline = true;
  video.muted = true;
  const loaded = new Promise((resolve, reject) => {
    video.onloadeddata = () => resolve();
    video.onerror = () => reject(new Error(`Không tải được pose video: ${source}`));
  });
  video.src = source;
  await loaded;
  const seekTo = Math.min(0.08, Math.max(0.02, Number(video.duration) > 0 ? Number(video.duration) / 12 : 0.04));
  await new Promise((resolve, reject) => {
    video.onseeked = () => resolve();
    video.onerror = () => reject(new Error(`Không đọc được frame pose video: ${source}`));
    try {
      video.currentTime = seekTo;
    } catch {
      resolve();
    }
  });
  const canvas = document.createElement("canvas");
  canvas.width = Math.max(1, video.videoWidth || 1);
  canvas.height = Math.max(1, video.videoHeight || 1);
  const context = canvas.getContext("2d");
  if (!context) return source;
  context.drawImage(video, 0, 0, canvas.width, canvas.height);
  const frame = canvas.toDataURL("image/png");
  presenterFrameCache.set(source, frame);
  return frame;
}

function playPoseSfx(event, time) {
  const sfxName = event.sfx || topic.poseSfx?.[event.pose] || "";
  if (!sfxName) return;
  const cooldown = topic.sfxCooldownSeconds || 0.6;
  // Only throttle forward playback; stepping backward must still trigger SFX.
  if (time >= lastSfxAt && time - lastSfxAt < cooldown) return;
  const player = sfxPlayers[sfxName];
  if (!player) return;
  player.currentTime = 0;
  player.volume = topic.sfxVolume ?? 0.3;
  player.play().catch(() => {});
  lastSfxAt = time;
}

function focusSideForPose(poseName) {
  // Explicit focusSide from the character manifest wins. Only legacy poses
  // without it fall back to reading the id/label; never guess by pose index.
  const explicit = String(topic.poseAssets?.[poseName]?.focusSide || "").toLowerCase();
  if (explicit === "left" || explicit === "right") return explicit;
  if (explicit === "center") return "";
  const id = String(poseName || "").toLowerCase();
  const label = String(topic.poseLabels?.[poseName] || "").toLowerCase();
  if (id.includes("left") || label.includes("trái") || label.includes("left")) return "left";
  if (id.includes("right") || label.includes("phải") || label.includes("right")) return "right";
  return "";
}

function updateMediaFocus(poseName) {
  const leftSlot = elements.leftImage.parentElement;
  const rightSlot = elements.rightImage.parentElement;
  if (!leftSlot || !rightSlot) return;
  if (isSingleImageScene()) {
    leftSlot.classList.remove("dimmed");
    rightSlot.classList.remove("dimmed");
    return;
  }
  const focusSide = focusSideForPose(poseName);
  leftSlot.classList.toggle("dimmed", focusSide === "right");
  rightSlot.classList.toggle("dimmed", focusSide === "left");
}

async function alphaBoundsForImage(image) {
  const source = mediaSource(image);
  const width = mediaIntrinsicWidth(image);
  const height = mediaIntrinsicHeight(image);
  if (!source || !width || !height) return null;
  if (presenterAlphaBounds.has(source)) return presenterAlphaBounds.get(source);

  const canvas = document.createElement("canvas");
  canvas.width = width;
  canvas.height = height;
  const context = canvas.getContext("2d", { willReadFrequently: true });
  context.drawImage(image, 0, 0);
  const pixels = context.getImageData(0, 0, canvas.width, canvas.height).data;
  let left = canvas.width;
  let top = canvas.height;
  let right = 0;
  let bottom = 0;
  for (let y = 0; y < canvas.height; y += 1) {
    for (let x = 0; x < canvas.width; x += 1) {
      if (pixels[(y * canvas.width + x) * 4 + 3] <= 8) continue;
      left = Math.min(left, x);
      top = Math.min(top, y);
      right = Math.max(right, x + 1);
      bottom = Math.max(bottom, y + 1);
    }
  }
  const bounds = right > left && bottom > top ? { left, top, right, bottom } : null;
  presenterAlphaBounds.set(source, bounds);
  return bounds;
}

function clearImportedPresenterLayout() {
  elements.teacher.style.removeProperty("left");
  elements.teacher.style.removeProperty("top");
  elements.teacher.style.removeProperty("width");
  elements.teacher.style.removeProperty("height");
}

async function layoutImportedPresenter(generation = presenterLayoutGeneration) {
  if (!elements.teacherWrap.classList.contains("custom-character")) {
    clearImportedPresenterLayout();
    return true;
  }
  const source = mediaSource(elements.teacher);
  if (!source || !mediaReady(elements.teacher)) return false;
  const bounds = await alphaBoundsForImage(elements.teacher);
  if (!bounds || generation !== presenterLayoutGeneration || source !== mediaSource(elements.teacher)) return false;

  const stageRect = elements.stage.getBoundingClientRect();
  const subtitleRects = Array.from(elements.karaoke.children, (node) => node.getBoundingClientRect());
  const subtitleBottom = subtitleRects.length
    ? Math.max(...subtitleRects.map((rect) => rect.bottom))
    : stageRect.top + stageRect.height * 0.518;
  const logicalScale = stageRect.height / 1920;
  const visibleWidth = bounds.right - bounds.left;
  const visibleHeight = bounds.bottom - bounds.top;
  const presenterLift = isQuizProject(topic) ? IMPORTED_PRESENTER_QUIZ_LIFT_PX * logicalScale : 0;
  const visibleTop = subtitleBottom - stageRect.top + IMPORTED_PRESENTER_GAP_PX * logicalScale - presenterLift;
  const visibleBottom = stageRect.height - IMPORTED_PRESENTER_BOTTOM_PX * logicalScale - presenterLift;
  const heightScale = Math.max(0.01, (visibleBottom - visibleTop) / visibleHeight);
  const widthScale = stageRect.width * IMPORTED_PRESENTER_MAX_WIDTH / visibleWidth;
  const heightCapScale = stageRect.height * IMPORTED_PRESENTER_MAX_HEIGHT / visibleHeight;
  const imageScale = Math.min(heightScale, widthScale, heightCapScale);
  const renderedVisibleWidth = visibleWidth * imageScale;
  const renderedVisibleHeight = visibleHeight * imageScale;
  const renderedVisibleTop = Math.max(visibleTop, visibleBottom - renderedVisibleHeight);

  elements.teacher.style.width = `${mediaIntrinsicWidth(elements.teacher) * imageScale}px`;
  elements.teacher.style.height = `${mediaIntrinsicHeight(elements.teacher) * imageScale}px`;
  elements.teacher.style.left = `${(stageRect.width - renderedVisibleWidth) / 2 - bounds.left * imageScale}px`;
  elements.teacher.style.top = `${renderedVisibleTop - bounds.top * imageScale}px`;
  return true;
}

function scheduleImportedPresenterLayout() {
  presenterLayoutGeneration += 1;
  if (offlineRender) return;
  const generation = presenterLayoutGeneration;
  cancelAnimationFrame(presenterLayoutRaf);
  presenterLayoutRaf = requestAnimationFrame(() => {
    layoutImportedPresenter(generation).catch(console.error);
  });
}


function setPose(event, time, allowSfx = false) {
  const poseChanged = event.index !== lastPoseIndex;
  if (poseChanged) {
    currentPose = event.pose;
    lastPoseIndex = event.index;
    if (offlineRender) offlineMediaSyncStats.poseChanges += 1;
    if (allowSfx) playPoseSfx(event, time);
  }
  // Use exactly one full-body sprite per pose to keep the presenter stationary.
  const nextSrc = poseImage(currentPose, true);
  const useVideo = presenterWantsVideo(nextSrc) && isVideoAssetSource(nextSrc);
  const teacher = ensureTeacherElement(useVideo);
  const currentSrc = mediaSource(teacher);
  const shouldResetVideo = useVideo && poseChanged;
  if (currentSrc !== nextSrc || shouldResetVideo) {
    if (currentSrc !== nextSrc) {
      teacher.src = nextSrc;
      lastPresenterSource = nextSrc;
    }
    if (useVideo) {
      const config = poseMediaConfig(currentPose);
      teacher.muted = true;
      teacher.loop = false;
      teacher.playsInline = true;

      // Keep live preview playback inside the same configured loop window as
      // offline rendering. Offline rendering has its own frame-by-frame seek.
      if (!teacher.__aurexPreviewLoopBound) {
        teacher.addEventListener("timeupdate", () => {
          if (offlineRender) return;
          const activeConfig = poseMediaConfig(currentPose);
          if (!activeConfig.loop || activeConfig.loopEnd <= activeConfig.loopStart) return;
          if (teacher.currentTime >= activeConfig.loopEnd - 0.02) {
            teacher.currentTime = Math.min(activeConfig.loopStart, Math.max(0, (teacher.duration || activeConfig.loopStart + 0.001) - 0.001));
          }
        });
        teacher.__aurexPreviewLoopBound = true;
      }

      const seekToLoopStart = () => {
        if (offlineRender || !shouldResetVideo) return;
        const duration = Number(teacher.duration);
        const start = Math.max(0, Number(config.loopStart) || 0);
        teacher.currentTime = Number.isFinite(duration) && duration > 0
          ? Math.min(start, Math.max(0, duration - 0.001))
          : start;
      };
      if (shouldResetVideo) {
        if (teacher.readyState >= 1 && currentSrc === nextSrc) seekToLoopStart();
        else teacher.addEventListener("loadedmetadata", seekToLoopStart, { once: true });
      }
      // Offline capture advances the media clock explicitly below. Live
      // preview can keep using normal playback.
      if (!offlineRender) teacher.play().catch(() => {});
    }
  }
  updateMediaFocus(currentPose);
}

async function seekOfflineVideoTo(video, targetTime) {
  const startedAt = performance.now();
  await new Promise((resolve, reject) => {
    let settled = false;
    const timeout = window.setTimeout(() => {
      cleanup();
      reject(new Error(`Timeout khi đồng bộ video nhân vật tại ${targetTime.toFixed(3)}s`));
    }, 5000);
    const cleanup = () => {
      window.clearTimeout(timeout);
      video.removeEventListener("seeked", onSeeked);
      video.removeEventListener("error", onError);
    };
    const finish = () => {
      if (settled) return;
      settled = true;
      video.pause();
      cleanup();
      resolve();
    };
    const onSeeked = () => {
      // Changing `src` can leave a queued seeked event for time 0. Ignore that
      // stale event and only accept the event for the timestamp requested below.
      if (Math.abs((Number(video.currentTime) || 0) - targetTime) > OFFLINE_MEDIA_SYNC_TOLERANCE) return;
      finish();
    };
    const onError = () => {
      if (settled) return;
      settled = true;
      cleanup();
      reject(new Error(`Không seek được video nhân vật: ${mediaSource(video)}`));
    };
    if (Math.abs((Number(video.currentTime) || 0) - targetTime) <= OFFLINE_MEDIA_SYNC_TOLERANCE) {
      finish();
      return;
    }
    video.addEventListener("seeked", onSeeked);
    video.addEventListener("error", onError, { once: true });
    video.currentTime = targetTime;
  });
  offlineMediaSyncStats.seeks += 1;
  offlineMediaSyncStats.seekWaitMs += performance.now() - startedAt;
}

async function seekOfflineIntroVideoTo(video, targetTime) {
  const startedAt = performance.now();
  await new Promise((resolve, reject) => {
    let settled = false;
    const timeout = window.setTimeout(() => {
      cleanup();
      reject(new Error(`Timeout khi đồng bộ video intro tại ${targetTime.toFixed(3)}s`));
    }, 5000);
    const cleanup = () => {
      window.clearTimeout(timeout);
      video.removeEventListener("seeked", onSeeked);
      video.removeEventListener("error", onError);
    };
    const finish = () => {
      if (settled) return;
      settled = true;
      video.pause();
      cleanup();
      resolve();
    };
    const onSeeked = () => {
      // A source change can queue a stale seeked event at time 0. Only accept
      // an event that actually lands on this frame's requested timestamp.
      if (Math.abs((Number(video.currentTime) || 0) - targetTime) > OFFLINE_MEDIA_SYNC_TOLERANCE) return;
      finish();
    };
    const onError = () => {
      if (settled) return;
      settled = true;
      cleanup();
      reject(new Error(`Không seek được video intro: ${mediaSource(video)}`));
    };
    if (Math.abs((Number(video.currentTime) || 0) - targetTime) <= OFFLINE_MEDIA_SYNC_TOLERANCE) {
      finish();
      return;
    }
    video.addEventListener("seeked", onSeeked);
    video.addEventListener("error", onError, { once: true });
    try {
      video.currentTime = targetTime;
    } catch (error) {
      onError(error);
    }
  });
  offlineMediaSyncStats.seeks += 1;
  offlineMediaSyncStats.seekWaitMs += performance.now() - startedAt;
}

async function syncIntroVideoToOfflineTimeline(time) {
  const video = elements.stageIntroVideo;
  if (!offlineRender || !video || !customIntroIsActive(topic, time)) return;
  if (video.hidden || !isVideoAssetSource(mediaSource(video))) return;
  if (!mediaReady(video)) await waitForMediaReady(video).catch(() => {});
  if (!mediaReady(video)) return;
  const duration = Number(video.duration);
  if (!Number.isFinite(duration) || duration <= 0) return;
  const targetTime = introVideoTargetTime(time, duration);
  const drift = Math.abs((Number(video.currentTime) || 0) - targetTime);
  offlineMediaSyncStats.maxRequestedDriftMs = Math.max(
    offlineMediaSyncStats.maxRequestedDriftMs,
    drift * 1000,
  );
  if (drift <= OFFLINE_MEDIA_SYNC_TOLERANCE) {
    video.pause();
    offlineMediaSyncStats.skippedSeeks += 1;
    offlineMediaSyncStats.lastPresentedDriftMs = drift * 1000;
    offlineMediaSyncStats.maxDriftMs = Math.max(offlineMediaSyncStats.maxDriftMs, drift * 1000);
    return;
  }
  await seekOfflineIntroVideoTo(video, targetTime);
  const presentedDrift = Math.abs((Number(video.currentTime) || 0) - targetTime) * 1000;
  offlineMediaSyncStats.lastPresentedDriftMs = presentedDrift;
  offlineMediaSyncStats.maxDriftMs = Math.max(offlineMediaSyncStats.maxDriftMs, presentedDrift);
}

async function playOfflineVideoTo(video, targetTime) {
  const startedAt = performance.now();
  await new Promise((resolve, reject) => {
    let settled = false;
    let frameRequestId = null;
    const timeout = window.setTimeout(() => {
      cleanup();
      reject(new Error(`Timeout khi phát tới frame video nhân vật tại ${targetTime.toFixed(3)}s`));
    }, 5000);
    const cleanup = () => {
      window.clearTimeout(timeout);
      video.removeEventListener("error", onError);
      if (frameRequestId !== null && typeof video.cancelVideoFrameCallback === "function") {
        video.cancelVideoFrameCallback(frameRequestId);
      }
    };
    const finish = () => {
      if (settled) return;
      settled = true;
      // Pause immediately after the decoded frame reaches the requested time;
      // the offline canvas then paints a stable frame.
      video.pause();
      cleanup();
      resolve();
    };
    const onError = () => {
      if (settled) return;
      settled = true;
      cleanup();
      reject(new Error(`Không phát được video nhân vật: ${mediaSource(video)}`));
    };
    const check = () => {
      if (settled) return;
      const currentTime = Number(video.currentTime) || 0;
      if (currentTime + OFFLINE_MEDIA_SYNC_TOLERANCE >= targetTime) {
        finish();
        return;
      }
      if (typeof video.requestVideoFrameCallback === "function") {
        frameRequestId = video.requestVideoFrameCallback(() => {
          frameRequestId = null;
          check();
        });
      } else {
        window.setTimeout(check, 16);
      }
    };
    video.addEventListener("error", onError, { once: true });
    const playPromise = video.play();
    if (playPromise && typeof playPromise.catch === "function") playPromise.catch(onError);
    check();
  });
  offlineMediaSyncStats.playWaits += 1;
  offlineMediaSyncStats.playWaitMs += performance.now() - startedAt;
}

async function syncPresenterToOfflineTimeline(event, timelineTime) {
  const video = elements.teacher;
  if (!offlineRender || !(video instanceof HTMLVideoElement) || !isVideoAssetSource(mediaSource(video))) return;
  const source = mediaSource(video);
  const poseIndex = event?.index ?? null;
  const sourceChanged = offlineVideoSyncState.source !== source;
  const poseChanged = offlineVideoSyncState.poseIndex !== poseIndex;
  if (sourceChanged || !mediaReady(video)) await waitForMediaReady(video);

  const duration = Number(video.duration);
  if (!Number.isFinite(duration) || duration <= 0) return;
  const config = poseMediaConfig(event?.pose);
  const sceneStart = Math.max(0, Number(event?.time) || 0);
  const localElapsed = config.syncMode === "timeline"
    ? Math.max(0, Number(timelineTime))
    : Math.max(0, Number(timelineTime) - sceneStart);
  const loopStart = Math.min(duration - 0.001, config.loopStart);
  const configuredEnd = config.loopEnd > loopStart ? config.loopEnd : duration;
  const loopEnd = Math.min(duration, Math.max(loopStart + 0.001, configuredEnd));
  const span = Math.max(0.001, loopEnd - loopStart);
  const targetTime = config.syncMode === "freeze"
    ? loopStart
    : config.loop
      ? loopStart + (localElapsed % span)
      : Math.min(loopEnd - 0.001, loopStart + localElapsed);
  const drift = Math.abs((Number(video.currentTime) || 0) - targetTime);
  offlineMediaSyncStats.maxRequestedDriftMs = Math.max(
    offlineMediaSyncStats.maxRequestedDriftMs,
    drift * 1000,
  );

  const needsPoseReset = sourceChanged || poseChanged;
  if (needsPoseReset) {
    offlineVideoSyncState.source = source;
    offlineVideoSyncState.poseIndex = poseIndex;
    offlineVideoSyncState.lastTargetTime = null;
    offlineMediaSyncStats.poseResets += 1;
  }

  if (drift <= OFFLINE_MEDIA_SYNC_TOLERANCE) {
    video.pause();
    offlineMediaSyncStats.skippedSeeks += 1;
    offlineMediaSyncStats.lastPresentedDriftMs = drift * 1000;
    offlineMediaSyncStats.maxDriftMs = Math.max(offlineMediaSyncStats.maxDriftMs, drift * 1000);
    offlineVideoSyncState.lastTargetTime = targetTime;
    return;
  }

  // Re-seek only when changing poses or wrapping a configured loop. For the
  // normal forward path, let Chromium's decoder advance sequentially.
  const currentTime = Number(video.currentTime) || 0;
  const isBackwardJump = targetTime < currentTime - OFFLINE_MEDIA_SYNC_TOLERANCE;
  if (needsPoseReset || isBackwardJump || config.syncMode === "freeze") {
    video.pause();
    await seekOfflineVideoTo(video, targetTime);
  } else {
    await playOfflineVideoTo(video, targetTime);
  }
  let presentedDrift = Math.abs((Number(video.currentTime) || 0) - targetTime) * 1000;
  if (presentedDrift > OFFLINE_MEDIA_MAX_SEQUENTIAL_DRIFT * 1000) {
    // requestVideoFrameCallback can wake up after a decoder frame boundary.
    // Correct only a meaningful overshoot; the sequential path remains seek
    // free for the common case while keeping animation aligned to its frame.
    offlineMediaSyncStats.playDriftCorrections += 1;
    await seekOfflineVideoTo(video, targetTime);
    presentedDrift = Math.abs((Number(video.currentTime) || 0) - targetTime) * 1000;
  }
  offlineMediaSyncStats.lastPresentedDriftMs = presentedDrift;
  offlineMediaSyncStats.maxDriftMs = Math.max(offlineMediaSyncStats.maxDriftMs, presentedDrift);
  offlineVideoSyncState.lastTargetTime = targetTime;
}

function renderKaraoke(time) {
  const activeIndex = activeWordAt(time);
  if (activeIndex < 0) {
    if (lastKaraokeRenderKey !== "") {
      elements.karaoke.classList.remove("visible");
      elements.karaoke.replaceChildren();
      lastKaraokeRenderKey = "";
    }
    if (lastKaraokeGroupKey) {
      lastKaraokeGroupKey = "";
      scheduleImportedPresenterLayout();
    }
    return;
  }
  const group = wordGroups.find((indexes) => indexes.includes(activeIndex));
  if (!group) return;
  const groupKey = group.join(",");
  const renderKey = `${groupKey}:${activeIndex}`;
  if (renderKey !== lastKaraokeRenderKey) {
    if (groupKey === lastKaraokeGroupKey && elements.karaoke.children.length === group.length) {
      Array.from(elements.karaoke.children).forEach((span, index) => {
        span.classList.toggle("active", group[index] === activeIndex);
      });
    } else {
      const fragment = document.createDocumentFragment();
      group.forEach((wordIndex) => {
        const span = document.createElement("span");
        span.textContent = timedWords[wordIndex].word;
        if (wordIndex === activeIndex) span.className = "active";
        fragment.append(span);
      });
      elements.karaoke.replaceChildren(fragment);
    }
    lastKaraokeRenderKey = renderKey;
  }
  elements.karaoke.classList.add("visible");
  if (groupKey !== lastKaraokeGroupKey) {
    lastKaraokeGroupKey = groupKey;
    scheduleImportedPresenterLayout();
  }
}

function isQuizProject(nextTopic = topic) {
  return String(nextTopic?.projectType || "").toLowerCase() === "quiz";
}

const QUIZ_ANSWER_HOLD_SECONDS = 1;
const QUIZ_V2_THINKING_SECONDS = 5;
const QUIZ_V2_REVEAL_HOLD_SECONDS = 1.4;
const QUIZ_V2_TRANSITION_SECONDS = 1.0;

function quizItems() {
  if (!Array.isArray(topic?.quizItems)) return [];
  return topic.quizItems.filter((item) => item && Array.isArray(item.options) && item.options.length === 3);
}

function quizV2Duration() {
  return quizItems().length * (QUIZ_V2_THINKING_SECONDS + QUIZ_V2_REVEAL_HOLD_SECONDS + QUIZ_V2_TRANSITION_SECONDS);
}

function quizV2ItemAt(time) {
  const items = quizItems();
  if (!items.length) return null;
  const sceneDuration = QUIZ_V2_THINKING_SECONDS + QUIZ_V2_REVEAL_HOLD_SECONDS + QUIZ_V2_TRANSITION_SECONDS;
  const index = Math.min(items.length - 1, Math.max(0, Math.floor(Math.max(0, Number(time) || 0) / sceneDuration)));
  const start = index * sceneDuration;
  return { item: items[index], index, start, elapsed: Math.max(0, (Number(time) || 0) - start), sceneDuration };
}

function quizAnswerStartTime() {
  const configured = Number(topic?.quizAnswerDelay);
  return Math.max(0, Number.isFinite(configured) ? configured : 5);
}

function quizCountdownSoundPath() {
  return String(topic?.quizCountdownSound || (isQuizProject() ? "audio/quiz-countdown.wav" : "")).trim();
}

function quizAnswerText() {
  const raw = String(topic?.quizAnswer || "").trim();
  if (!raw) return "";
  return /^(?:đáp án là|answer is)\s*/iu.test(raw) ? raw : `Đáp án là ${raw}`;
}

function quizPairs() {
  const segments = Array.isArray(topic?.segments) ? topic.segments : [];
  const pairs = [];
  for (let index = 0; index + 1 < segments.length; index += 2) {
    pairs.push({ question: segments[index], answer: segments[index + 1] });
  }
  return pairs;
}

function renderQuizText(time) {
  if (!elements.quizText) return;
  const v2 = quizV2ItemAt(time);
  if (v2) {
    renderQuizV2(v2);
    return;
  }
  const pairs = quizPairs();
  const delay = quizAnswerStartTime();
  let pair = pairs[0];
  // A stale/unaligned segment start must not hide the previous answer. Quiz
  // scenes advance serially: question -> countdown -> answer -> next question.
  for (let index = 0; index < pairs.length; index += 1) {
    const candidate = pairs[index];
    const questionStart = Number(candidate.question?.start) || 0;
    const questionEnd = Math.max(questionStart, Number(candidate.question?.end) || questionStart);
    const answerEnd = Math.max(
      questionEnd,
      Number(candidate.answer?.end) || Number(candidate.answer?.start) || questionEnd,
    );
    const nextQuestionStart = index + 1 < pairs.length
      ? (Number(pairs[index + 1].question?.start) || questionEnd + delay)
      : Infinity;
    const sceneEnd = Math.max(nextQuestionStart, answerEnd + QUIZ_ANSWER_HOLD_SECONDS);
    if (Number(time) <= sceneEnd || index === pairs.length - 1) {
      pair = candidate;
      break;
    }
  }
  if (!pair) {
    elements.quizText.hidden = true;
    return;
  }
  // The countdown begins after the question narration finishes, not when the
  // question scene starts. Rendered audio uses the same question end marker.
  const questionStart = Number(pair.question?.start) || 0;
  const questionEnd = Math.max(questionStart, Number(pair.question?.end) || questionStart);
  const elapsed = Number(time) - questionEnd;
  const answer = String(pair.answer?.text || "").trim();
  const countdownActive = elapsed >= 0 && elapsed < delay;
  const answerVisible = Boolean(answer) && elapsed >= delay;
  const style = (key, fallback) => String(topic?.[key] || fallback);
  elements.quizQuestion.style.fontFamily = style("quizQuestionFontFamily", '"Inter", sans-serif');
  elements.quizQuestion.style.color = style("quizQuestionColor", "#ffffff");
  elements.quizQuestion.style.fontSize = `${Number(topic?.quizQuestionSize) || 7.2}cqw`;
  elements.quizCountdown.style.color = style("quizCountdownColor", "#ffd166");
  elements.quizAnswer.style.fontFamily = style("quizAnswerFontFamily", '"Inter", sans-serif');
  elements.quizAnswer.style.color = style("quizAnswerColor", "#ffffff");
  elements.quizAnswer.style.fontSize = `${Number(topic?.quizAnswerSize) || 6.2}cqw`;
  elements.quizText.hidden = false;
  elements.quizQuestion.textContent = String(pair.question?.text || "").trim();
  elements.quizCountdown.textContent = countdownActive
    ? `${Math.max(1, Math.ceil(delay - elapsed))}`
    : "";
  elements.quizAnswer.textContent = answerVisible
    ? (/^(?:đáp án là|answer is)\s*/iu.test(answer) ? answer : `Đáp án là ${answer}`)
    : "";
}

function renderQuizV2(scene) {
  const { item, index, elapsed } = scene;
  const reveal = elapsed >= QUIZ_V2_THINKING_SECONDS;
  const correctIndex = Math.max(0, Math.min(2, Number(item.correct_index ?? item.correctIndex) || 0));
  const style = (key, fallback) => String(topic?.[key] || fallback);
  elements.quizText.hidden = false;
  elements.quizText.classList.toggle("quiz-v2", true);
  elements.quizText.style.setProperty("--quiz-question-font", style("quizQuestionFontFamily", '"Arial Black", Arial, sans-serif'));
  elements.quizText.style.setProperty("--quiz-question-color", style("quizQuestionColor", "#ffd21c"));
  elements.quizQuestion.textContent = String(item.question || "").trim();
  elements.quizQuestion.style.fontFamily = "var(--quiz-question-font)";
  elements.quizQuestion.style.color = "var(--quiz-question-color)";
  elements.quizQuestion.style.fontSize = `${Number(topic?.quizQuestionSize) || 7.2}cqw`;
  if (elements.quizOptions) {
    elements.quizOptions.hidden = false;
    Array.from(elements.quizOptions.querySelectorAll(".quiz-option")).forEach((option, optionIndex) => {
      option.classList.toggle("is-correct", reveal && optionIndex === correctIndex);
      option.classList.toggle("is-wrong", reveal && optionIndex !== correctIndex);
      option.querySelector(".quiz-option-text").textContent = String(item.options[optionIndex] || "").trim();
    });
  }
  elements.quizCountdown.textContent = reveal ? "" : String(Math.max(1, Math.ceil(QUIZ_V2_THINKING_SECONDS - elapsed)));
  if (elements.quizCountdownWrap) elements.quizCountdownWrap.hidden = reveal;
  if (elements.quizLegacyAnswerCard) elements.quizLegacyAnswerCard.hidden = true;
  if (index !== lastQuizItemIndex) {
    elements.quizText.classList.remove("quiz-v2-enter");
    void elements.quizText.offsetWidth;
    elements.quizText.classList.add("quiz-v2-enter");
    lastQuizItemIndex = index;
  }
}

function renderAt(time, allowPoseSfx = false) {
  if (previewFrame && previewTimeLock !== null) time = previewTimeLock;
  elements.stage.classList.remove("preview-blank");
  elements.stage.classList.toggle("quiz-text-only", isQuizProject());
  if (isQuizProject()) renderQuizText(time);
  else if (isCustomProject()) applyCustomSlide(customSlideAt(time));
  else applyComparisonToView(comparisonAt(time));
  applyIntro(topic, time);
  if (!isQuizProject()) {
    renderKaraoke(time);
  }
  // Quiz uses its countdown bed as the only timed sound effect. Pose dings
  // at the question/answer boundaries would mask the first syllable and make
  // the narration sound clipped, while the character pose still changes.
  setPose(poseAt(time), time, allowPoseSfx && !isQuizProject());
  const duration = previewDuration();
  elements.timeText.textContent = `${formatTime(time)} / ${formatTime(duration)}`;
  elements.progress.value = duration > 0 ? Math.round(time / duration * 1000) : 0;
}

function offlineImagePaths() {
  const paths = [topic.leftImage, topic.rightImage];
  if (String(topic.backgroundType || "").toLowerCase() === "image" && topic.backgroundImage) {
    paths.push(topic.backgroundImage);
  }
  (Array.isArray(topic.comparisons) ? topic.comparisons : []).forEach((scene) => {
    paths.push(scene.leftImage);
    if (!isSingleImageScene(scene)) {
      paths.push(scene.rightImage);
    }
  });
  (Array.isArray(topic.slides) ? topic.slides : []).forEach((slide) => (slide.layers || []).forEach((layer) => { if (layer?.type === "image" && layer.src) paths.push(layer.src); }));
  const intro = customIntroConfig(topic);
  if (intro.type !== "none" && intro.logo) paths.push(intro.logo);
  if (intro.type === "media" && intro.media && !isVideoAssetSource(intro.media)) paths.push(intro.media);
  Object.values(topic.poseAssets || {}).forEach((pose) => {
    paths.push(pose?.closed, pose?.speaking);
  });
  return [...new Set(paths.filter(Boolean).map(resolveTopicAsset))];
}

async function preloadOfflineImages() {
  await Promise.all(offlineImagePaths().map(async (source) => {
    if (isVideoAssetSource(source)) {
      await loadPresenterFrame(source);
      return;
    }
    await new Promise((resolve, reject) => {
      const image = new Image();
      image.onload = resolve;
      image.onerror = () => reject(new Error(`Không tải được ảnh render: ${source}`));
      image.src = source;
    });
  }));
}

async function renderOfflineFrame(time) {
  const poseEvent = poseAt(time);
  renderAt(time);
  // Fire-and-forget media decode (headless Chromium can hang on decode, which
  // would freeze the whole render loop). Character video readiness is handled
  // inside syncPresenterToOfflineTimeline with its own timeouts.
  Promise.all([
    elements.leftImage.decode().catch(() => {}),
    isSingleImageScene()
      ? Promise.resolve()
      : elements.rightImage.decode().catch(() => {}),
    elements.stageBackgroundImage && !elements.stageBackgroundImage.hidden
      ? elements.stageBackgroundImage.decode().catch(() => {})
      : Promise.resolve(),
    elements.stageIntroImage && !elements.stageIntroImage.hidden
      ? elements.stageIntroImage.decode().catch(() => {})
      : Promise.resolve(),
    elements.stageIntroLogo && !elements.stageIntroLogo.hidden
      ? elements.stageIntroLogo.decode().catch(() => {})
      : Promise.resolve(),
  ]).catch(() => {});
  await syncPresenterToOfflineTimeline(poseEvent, time);
  await syncIntroVideoToOfflineTimeline(time);
  applyStageBackground(topic);
  if (currentComparisonScene) {
    applyImageFrame(
      elements.leftImage,
      currentComparisonScene.leftImageZoom,
      currentComparisonScene.leftImageX,
      currentComparisonScene.leftImageY,
    );
    if (!isSingleImageScene(currentComparisonScene)) {
      applyImageFrame(
        elements.rightImage,
        currentComparisonScene.rightImageZoom,
        currentComparisonScene.rightImageX,
        currentComparisonScene.rightImageY,
      );
    }
  }
  const stageRect = elements.stage.getBoundingClientRect();
  const layoutKey = [
    mediaSource(elements.teacher),
    currentComparisonKey,
    lastKaraokeRenderKey,
    Math.round(stageRect.width),
    Math.round(stageRect.height),
  ].join("|");
  if (layoutKey !== lastOfflineLayoutKey) {
    const layoutApplied = await layoutImportedPresenter(presenterLayoutGeneration);
    if (layoutApplied) {
      lastOfflineLayoutKey = layoutKey;
      fitHeadings();
    }
  }
  paintOfflinePresenterFrame();
}

async function prepareIntroOfflineMedia() {
  const intro = customIntroConfig(topic);
  if (String(intro.type || "none").toLowerCase() !== "media") return;
  const mediaPath = String(intro.media || "").trim();
  if (!mediaPath || !isVideoAssetSource(mediaPath) || !elements.stageIntroVideo) return;
  applyIntro(topic, 0);
  await waitForMediaReady(elements.stageIntroVideo).catch(() => {});
  elements.stageIntroVideo.pause();
}

function paintOfflinePresenterFrame() {
  const video = elements.teacher;
  if (!offlineRender || !(video instanceof HTMLVideoElement) || !mediaReady(video)) return;
  let canvas = elements.teacherWrap.querySelector("canvas.offline-presenter-frame");
  if (!canvas) {
    canvas = document.createElement("canvas");
    canvas.className = "teacher offline-presenter-frame";
    canvas.setAttribute("aria-hidden", "true");
    elements.teacherWrap.append(canvas);
  }
  const width = Math.max(1, video.videoWidth || 1);
  const height = Math.max(1, video.videoHeight || 1);
  if (canvas.width !== width) canvas.width = width;
  if (canvas.height !== height) canvas.height = height;
  const context = canvas.getContext("2d", { alpha: true });
  context.clearRect(0, 0, width, height);
  context.drawImage(video, 0, 0, width, height);
  canvas.style.cssText = video.style.cssText;
  canvas.style.visibility = "visible";
  canvas.style.pointerEvents = "none";
  video.style.visibility = "hidden";
}

async function prepareOfflineRender() {
  offlineVideoSyncState.source = "";
  offlineVideoSyncState.poseIndex = null;
  offlineVideoSyncState.lastTargetTime = null;
  lastOfflineLayoutKey = "";
  await Promise.all([
    preloadOfflineImages(),
    document.fonts?.ready || Promise.resolve(),
  ]);
  await prepareIntroOfflineMedia();
  if (elements.teacher instanceof HTMLVideoElement) elements.teacher.pause();
  await renderOfflineFrame(0);
}

function animationLoop() {
  const time = previewTime();
  renderAt(time, true);
  if (previewStopTime !== null && time >= previewStopTime - 0.015) {
    elements.voiceover.pause();
    stopBackgroundMusic();
    seekPreview(previewStopTime);
    renderAt(previewStopTime);
    previewStopTime = null;
    elements.playButton.textContent = "Phát";
    return;
  }
  if (!elements.voiceover.paused && !elements.voiceover.ended) raf = requestAnimationFrame(animationLoop);
  else stopBackgroundMusic();
}

async function waitForAudioClockStart() {
  if (!renderMode || previewFrame || (elements.voiceover.currentTime || 0) > 0.005) return;
  await new Promise((resolve) => {
    const timeoutAt = performance.now() + 750;
    const check = () => {
      if ((elements.voiceover.currentTime || 0) > 0.005 || performance.now() >= timeoutAt) {
        resolve();
        return;
      }
      requestAnimationFrame(check);
    };
    requestAnimationFrame(check);
  });
}

async function startPlayback(fromStart = false) {
  previewStopTime = null;
  if (!voiceoverAvailable) {
    const duration = previewDuration();
    const start = fromStart || previewSimulationTime >= duration - 0.015 ? 0 : previewSimulationTime;
    lastPoseIndex = -1;
    lastSfxAt = -Infinity;
    simulatePreviewRange(start, duration, {
      onComplete: () => {
        elements.playButton.textContent = "Phát";
        window.__AUREX_DEMO_DONE__ = true;
      },
    });
    elements.syncMarker.classList.remove("visible");
    if (!window.__AUREX_DEMO_STARTED_PERF__) window.__AUREX_DEMO_STARTED_PERF__ = performance.now();
    elements.playButton.textContent = "Tạm dừng";
    return;
  }
  if (fromStart || elements.voiceover.ended) {
    seekPreview(0);
    lastPoseIndex = -1;
    lastSfxAt = -Infinity;
  }
  syncBackgroundMusic(previewTime(), { playing: false });
  await elements.voiceover.play();
  // In headless capture, play() can resolve before the media clock advances.
  // Keep the magenta sync frame visible until audio timing truly starts so
  // the separately muxed voiceover and recorded karaoke share the same zero.
  await waitForAudioClockStart();
  syncBackgroundMusic(previewTime(), { playing: !offlineRender });
  renderAt(previewTime(), true);
  elements.syncMarker.classList.remove("visible");
  if (!window.__AUREX_DEMO_STARTED_PERF__) window.__AUREX_DEMO_STARTED_PERF__ = performance.now();
  elements.playButton.textContent = "Tạm dừng";
  cancelAnimationFrame(raf);
  raf = requestAnimationFrame(animationLoop);
}

async function playPreviewRange(start, end) {
  if (!voiceoverAvailable) {
    simulatePreviewRange(start, end);
    return;
  }
  stopPreviewMotion();
  previewStopTime = Math.max(start + 0.08, end);
  seekPreview(start);
  lastPoseIndex = -1;
  lastSfxAt = -Infinity;
  renderAt(start, true);
  syncBackgroundMusic(start, { playing: false });
  await elements.voiceover.play();
  syncBackgroundMusic(start, { playing: true });
  cancelAnimationFrame(raf);
  raf = requestAnimationFrame(animationLoop);
}

function simulatePreviewRange(start, end, { allowSfx = true, allowBgm = true, onComplete } = {}) {
  stopPreviewMotion();
  lastPoseIndex = -1;
  lastSfxAt = -Infinity;
  const startedAt = performance.now();
  const duration = Math.max(0.08, end - start);
  if (allowBgm) syncBackgroundMusic(start, { playing: true });
  const tick = (now) => {
    const elapsed = Math.max(0, (now - startedAt) / 1000);
    const time = Math.min(end, start + elapsed);
    previewSimulationTime = time;
    renderAt(time, allowSfx);
    if (elapsed < duration) previewSimulationRaf = requestAnimationFrame(tick);
    else {
      previewSimulationRaf = 0;
      if (allowBgm) stopBackgroundMusic();
      onComplete?.();
    }
  };
  previewSimulationTime = start;
  renderAt(start, allowSfx);
  previewSimulationRaf = requestAnimationFrame(tick);
}

function stopPreviewMotion() {
  elements.voiceover.pause();
  stopBackgroundMusic();
  cancelAnimationFrame(raf);
  cancelAnimationFrame(previewSimulationRaf);
  raf = 0;
  previewSimulationRaf = 0;
  previewStopTime = null;
}

function showPreviewBlank() {
  stopPreviewMotion();
  previewComparisonId = "";
  previewTimeLock = null;
  lastPoseIndex = -1;
  lastSfxAt = -Infinity;
  elements.karaoke.classList.remove("visible");
  elements.karaoke.replaceChildren();
  hideIntro();
  elements.stage.classList.add("preview-blank");
}

function pausePlayback() {
  stopPreviewMotion();
  elements.playButton.textContent = "Phát";
  renderAt(previewTime());
}

function bindControls() {
  elements.playButton.addEventListener("click", () => {
    if (!previewIsPlaying()) startPlayback().catch(console.error);
    else pausePlayback();
  });
  elements.restartButton.addEventListener("click", () => startPlayback(true).catch(console.error));
  elements.progress.addEventListener("input", () => {
    const duration = previewDuration();
    const time = seekPreview(Number(elements.progress.value) / 1000 * duration);
    lastPoseIndex = -1;
    syncBackgroundMusic(time, { playing: previewIsPlaying() });
    renderAt(time);
  });
  elements.voiceover.addEventListener("ended", () => {
    elements.playButton.textContent = "Phát";
    cancelAnimationFrame(raf);
    const duration = previewDuration();
    // Audio can end between two animation frames. Force one final karaoke
    // render, then wait for the browser to paint it before stopping capture.
    renderAt(duration);
    requestAnimationFrame(() => {
      requestAnimationFrame(() => {
        window.__AUREX_DEMO_DONE__ = true;
      });
    });
  });
}

function imageFrameLayout(naturalWidth, naturalHeight, viewWidth, viewHeight, zoom, panX, panY) {
  const nw = Math.max(1, naturalWidth || 1);
  const nh = Math.max(1, naturalHeight || 1);
  const vw = Math.max(1, viewWidth || 1);
  const vh = Math.max(1, viewHeight || 1);
  const safeZoom = Math.min(3, Math.max(1, Number(zoom) || 1));
  // Cover: lấp đầy cả 2 chiều khung (zoom to nhất vừa khung), crop phần dư.
  // Đổi từ Math.min sang Math.max để ảnh so sánh trái/phải luôn full khung 1:1
  // trong previewer/render, khớp với editor (pasteImageMode=square).
  const base = Math.max(vw / nw, vh / nh);
  const width = nw * base * safeZoom;
  const height = nh * base * safeZoom;
  const maxOffsetX = Math.max(0, (width - vw) / 2);
  const maxOffsetY = Math.max(0, (height - vh) / 2);
  const x = Math.min(50, Math.max(-50, Number(panX) || 0));
  const y = Math.min(50, Math.max(-50, Number(panY) || 0));
  const left = (vw - width) / 2 + (maxOffsetX ? x / 50 * maxOffsetX : 0);
  const top = (vh - height) / 2 + (maxOffsetY ? y / 50 * maxOffsetY : 0);
  return { width, height, left, top };
}

function applyImageFrame(element, zoom, x, y) {
  const parent = element.parentElement;
  const layout = imageFrameLayout(
    element.naturalWidth,
    element.naturalHeight,
    parent?.clientWidth || 0,
    parent?.clientHeight || 0,
    zoom,
    x,
    y,
  );
  element.style.width = `${layout.width}px`;
  element.style.height = `${layout.height}px`;
  element.style.transform = `translate(${layout.left}px, ${layout.top}px)`;
}

function bindStageImageFrame(element, side) {
  const refresh = () => {
    const scene = currentComparisonScene || baseComparison();
    applyImageFrame(element, scene?.[`${side}ImageZoom`], scene?.[`${side}ImageX`], scene?.[`${side}ImageY`]);
  };
  element.addEventListener("load", refresh);
  if (element.parentElement) new ResizeObserver(refresh).observe(element.parentElement);
}

function karaokePosition(nextTopic = topic) {
  const custom = isCustomProject(nextTopic);
  const x = Number(nextTopic?.karaokeX);
  const y = Number(nextTopic?.karaokeY);
  return {
    x: Number.isFinite(x) ? Math.min(12, Math.max(0, x)) : 5,
    y: Number.isFinite(y) ? Math.min(88, Math.max(4, y)) : (custom ? 66 : 46.2),
  };
}

function applyKaraokePosition(nextTopic = topic) {
  if (!elements.karaoke) return;
  const position = karaokePosition(nextTopic);
  elements.karaoke.style.left = `${position.x}%`;
  elements.karaoke.style.top = `${position.y}%`;
}

function applyKaraokeStyle(nextTopic = topic) {
  if (!elements.karaoke) return;
  const color = String(nextTopic?.karaokeColor || "#271f11");
  const active = String(nextTopic?.karaokeActiveColor || "#de370d");
  const size = Math.min(1.5, Math.max(0.6, Number(nextTopic?.karaokeSize) || 1.2));
  elements.karaoke.style.setProperty("--karaoke-color", color);
  elements.karaoke.style.setProperty("--karaoke-active-color", active);
  elements.karaoke.style.setProperty("--karaoke-size", String(size));
  applyKaraokePosition(nextTopic);
  elements.karaoke.classList.toggle("cjk", usesCompactCjkText(nextTopic));
  elements.karaoke.classList.toggle("vietnamese", usesVietnameseText(nextTopic));
  scheduleImportedPresenterLayout();
}

function backgroundMusicActive(nextTopic = topic) {
  return Boolean(String(nextTopic?.backgroundMusic || "").trim());
}

function stopBackgroundMusic() {
  const audio = elements.backgroundMusic;
  if (!audio) return;
  audio.pause();
}

function syncBackgroundMusic(time = 0, { playing = false } = {}) {
  const audio = elements.backgroundMusic;
  if (!audio || offlineRender) return;
  if (!backgroundMusicActive(topic)) {
    stopBackgroundMusic();
    if (audio.getAttribute("src")) {
      audio.removeAttribute("src");
      audio.load();
    }
    return;
  }
  const src = resolveTopicAsset(topic.backgroundMusic);
  if (audio.getAttribute("src") !== src) {
    audio.src = src;
    audio.loop = true;
  }
  audio.volume = Math.min(0.5, Math.max(0.05, Number(topic.backgroundMusicVolume) || 0.18));
  const mediaDuration = Number(audio.duration);
  if (Number.isFinite(mediaDuration) && mediaDuration > 0) {
    const nextTime = Math.max(0, Number(time) || 0) % mediaDuration;
    if (Math.abs((audio.currentTime || 0) - nextTime) > 0.12) audio.currentTime = nextTime;
  }
  if (playing) {
    if (audio.paused) audio.play().catch(() => {});
  } else {
    audio.pause();
  }
}

function applyStageBackground(nextTopic = topic) {
  if (!elements.stage) return;
  const type = String(nextTopic?.backgroundType || "default").toLowerCase();
  const color = String(nextTopic?.backgroundColor || "#f5eee3");
  const imagePath = String(nextTopic?.backgroundImage || "").trim();
  const bgImage = elements.stageBackgroundImage;

  if (type === "color") {
    elements.stage.style.background = color;
    if (bgImage) {
      bgImage.hidden = true;
      bgImage.removeAttribute("src");
    }
    return;
  }

  if (type === "image") {
    elements.stage.style.background = color;
    if (imagePath && bgImage) {
      const src = resolveTopicAsset(imagePath);
      if (bgImage.getAttribute("src") !== src) bgImage.src = src;
      bgImage.hidden = false;
      applyImageFrame(
        bgImage,
        nextTopic?.backgroundImageZoom,
        nextTopic?.backgroundImageX,
        nextTopic?.backgroundImageY,
      );
    } else if (bgImage) {
      bgImage.hidden = true;
      bgImage.removeAttribute("src");
    }
    return;
  }

  elements.stage.style.background = "";
  if (bgImage) {
    bgImage.hidden = true;
    bgImage.removeAttribute("src");
  }
}

async function applyTopicToView(nextTopic, { preserveAudio = true, blank = false } = {}) {
  const nextPresentationKey = topicPresentationKey(nextTopic);
  const presentationChanged = nextPresentationKey !== currentTopicPresentationKey;
  const previousCompactCjk = usesCompactCjkText(topic);
  const previousVietnamese = usesVietnameseText(topic);
  topic = nextTopic;
  currentTopicPresentationKey = nextPresentationKey;
  timedWords = buildTimedWords(topic.segments || []);
  wordGroups = buildGroups(timedWords);

  if (!presentationChanged) {
    // Text edits only change the timed words and subtitle markup. Keep the
    // already-painted image/label/presenter tree intact; rebuilding it for
    // every keystroke is particularly expensive in the desktop WKWebView.
    lastKaraokeGroupKey = "";
    lastKaraokeRenderKey = "";
    lastOfflineLayoutKey = "";
    const scriptStyleChanged = previousCompactCjk !== usesCompactCjkText(topic)
      || previousVietnamese !== usesVietnameseText(topic);
    if (scriptStyleChanged) applyKaraokeStyle(topic);
    // Quiz typography is rendered directly from the current topic. Its style
    // controls can change without changing the media/presenter tree, so paint
    // the current frame even when the presentation structure is unchanged.
    if (isQuizProject(topic)) renderAt(previewTime());
    if (blank) showPreviewBlank();
    return;
  }

  elements.stage.classList.toggle("custom-slide-project", isCustomProject(topic));
  if (!isCustomProject(topic)) { previewSlideId = ""; if (elements.slideCanvas) elements.slideCanvas.hidden = true; }
  currentPose = topic.poseTimeline?.[0]?.pose || Object.keys(topic.poseAssets || {})[0] || "question";
  lastPoseIndex = -1;
  lastKaraokeGroupKey = "";
  lastKaraokeRenderKey = "";
  lastOfflineLayoutKey = "";
  elements.karaoke.classList.remove("visible");
  elements.karaoke.replaceChildren();
  const isCustomCharacter = Boolean(topic.characterId && topic.characterId !== "human-presenter");
  elements.teacherWrap.classList.toggle("custom-character", isCustomCharacter);
  // Gắn class character-<id> để CSS tuỳ biến riêng từng nhân vật (vd. character-bietchichomet).
  // Gắn cả lên #stage để style các sibling (media-slot, label...) theo nhân vật.
  const teacherClasses = elements.teacherWrap.classList;
  const stageClasses = elements.stage.classList;
  teacherClasses.remove(...Array.from(teacherClasses).filter((cls) => cls.startsWith("character-")));
  stageClasses.remove(...Array.from(stageClasses).filter((cls) => cls.startsWith("character-")));
  if (isCustomCharacter && topic.characterId) {
    teacherClasses.add(`character-${topic.characterId}`);
    stageClasses.add(`character-${topic.characterId}`);
  }
  // Tải manifest nhân vật để lấy màu nhãn mặc định (fallback theo character - Phương án A).
  await loadCharacterMeta(isCustomCharacter ? topic.characterId : null);
  if (!isCustomCharacter) clearImportedPresenterLayout();
  currentComparisonKey = "";
  applyStageBackground(topic);
  applyIntro(topic, 0);
  if (blank) {
    // While the editor is still on the empty preview state, keep the latest
    // topic and styling ready without rebuilding the comparison DOM/media.
    applyKaraokeStyle(topic);
    syncBackgroundMusic(previewTime(), { playing: false });
    showPreviewBlank();
    return;
  }
  if (isCustomProject(topic)) applyCustomSlide(customSlideAt(0));
  else applyComparisonToView(baseComparison(topic), true);
  applyKaraokeStyle(topic);
  updateMediaFocus(currentPose);
  if (!preserveAudio) {
    const voiceoverPath = String(topic.voiceover || "").trim();
    voiceoverAvailable = Boolean(voiceoverPath);
    previewSimulationTime = 0;
    if (voiceoverPath) elements.voiceover.src = resolveTopicAsset(voiceoverPath);
    else {
      elements.voiceover.removeAttribute("src");
      elements.voiceover.load();
    }
  }
  syncBackgroundMusic(previewTime(), { playing: previewIsPlaying() && !offlineRender });
}

async function init() {
  const response = await fetch(topicUrl, { cache: "no-store" });
  if (!response.ok) throw new Error(`Không tải được topic: ${response.status}`);
  topic = await response.json();
  await preloadOfflineImages();
  await applyTopicToView(topic, { preserveAudio: false });
  bindStageImageFrame(elements.leftImage, "left");
  bindStageImageFrame(elements.rightImage, "right");
  if (elements.stageBackgroundImage) {
    elements.stageBackgroundImage.addEventListener("load", () => applyStageBackground(topic));
    if (elements.stageBackgroundImage.parentElement) {
      new ResizeObserver(() => applyStageBackground(topic)).observe(elements.stageBackgroundImage.parentElement);
    }
  }
  if (elements.stageIntroImage) {
    elements.stageIntroImage.addEventListener("load", () => applyIntro(topic, previewTime()));
  }
  if (elements.stageIntroVideo) {
    const refreshIntroVideo = () => applyIntro(topic, previewTime());
    elements.stageIntroVideo.addEventListener("loadedmetadata", refreshIntroVideo);
    elements.stageIntroVideo.addEventListener("loadeddata", refreshIntroVideo);
  }

  Object.entries(topic.sfx || {}).forEach(([name, path]) => {
    sfxPlayers[name] = new Audio(resolveTopicAsset(path));
    sfxPlayers[name].preload = "auto";
  });

  currentPose = topic.poseTimeline[0].pose;
  const initialSrc = poseImage(currentPose, false);
  const initialUseVideo = presenterWantsVideo(initialSrc);
  const teacher = ensureTeacherElement(initialUseVideo);
  teacher.src = initialSrc;
  if (initialUseVideo) {
    teacher.muted = true;
    teacher.loop = true;
    teacher.playsInline = true;
    teacher.preload = "auto";
    teacher.load();
  }
  lastPresenterSource = teacher.src;
  elements.teacher.addEventListener("loadeddata", scheduleImportedPresenterLayout);
  elements.teacher.addEventListener("seeked", scheduleImportedPresenterLayout);
  elements.voiceover.addEventListener("error", () => {
    voiceoverAvailable = false;
  });
  bindControls();

  await Promise.all([
    elements.leftImage.decode().catch(() => {}),
    elements.rightImage.decode().catch(() => {}),
    waitForMediaReady(elements.teacher).catch(() => {}),
    waitForVoiceoverReady(),
  ]);

  applyComparisonToView(comparisonAt(0), true);
  elements.loading.classList.add("hidden");
  renderAt(0);
  scheduleImportedPresenterLayout();
  if (previewFrame) showPreviewBlank();
  window.__AUREX_DEMO_READY__ = true;
  window.startPlayback = startPlayback;
  window.renderAt = renderAt;
  window.prepareOfflineRender = prepareOfflineRender;
  window.renderOfflineFrame = renderOfflineFrame;
  window.__AUREX_MEDIA_SYNC_STATS__ = offlineMediaSyncStats;
  window.__layoutImportedPresenterForTest = () => layoutImportedPresenter(presenterLayoutGeneration);
  fitHeadings();
  document.fonts?.ready.then(fitHeadings);
  if (autoplay) await startPlayback(true);
}

let previewWorkTail = Promise.resolve();
let pendingTopicUpdate = null;
let topicUpdateDrainQueued = false;

async function applyPreviewTopicUpdate(data) {
  stopPreviewMotion();
  if (Object.prototype.hasOwnProperty.call(data, "comparisonId")) {
    previewComparisonId = String(data.comparisonId || "");
  }
  if (Object.prototype.hasOwnProperty.call(data, "slideId")) previewSlideId = String(data.slideId || "");
  await applyTopicToView(data.topic, {
    preserveAudio: data.preserveAudio !== false,
    blank: data.blank === true,
  });
  if (data.blank === true) return;
  const time = Math.max(0, Math.min(Number(data.time) || 0, topic.duration || 0));
  previewTimeLock = previewComparisonId ? time : null;
  seekPreview(time);
  renderAt(time);
}

function enqueueTopicUpdate(data) {
  // Keep only the newest draft while an older update is waiting on media or
  // manifest work. This is the same back-pressure pattern used by FastScene.
  pendingTopicUpdate = data;
  if (topicUpdateDrainQueued) return;
  topicUpdateDrainQueued = true;
  previewWorkTail = previewWorkTail
    .catch(() => {})
    .then(async () => {
      while (pendingTopicUpdate) {
        const next = pendingTopicUpdate;
        pendingTopicUpdate = null;
        await applyPreviewTopicUpdate(next);
      }
    })
    .catch(console.error)
    .finally(() => {
      topicUpdateDrainQueued = false;
      if (pendingTopicUpdate) enqueueTopicUpdate(pendingTopicUpdate);
    });
}

function enqueuePreviewWork(work) {
  previewWorkTail = previewWorkTail
    .catch(() => {})
    .then(async () => {
      if (pendingTopicUpdate) {
        const next = pendingTopicUpdate;
        pendingTopicUpdate = null;
        await applyPreviewTopicUpdate(next);
      }
      return work();
    })
    .catch(console.error);
}

window.addEventListener("message", (event) => {
  if (event.origin !== window.location.origin || !event.data || !topic) return;
  const data = event.data;
  if (data.type === "tho-topic-update" && data.topic) {
    enqueueTopicUpdate(data);
    return;
  }
  if (data.type === "tho-preview-comparison-lock") {
    enqueuePreviewWork(() => {
      stopPreviewMotion();
      previewComparisonId = String(data.comparisonId || "");
      const time = Math.max(0, Math.min(Number(data.time) || 0, topic.duration || 0));
      previewTimeLock = previewComparisonId ? time : null;
      seekPreview(time);
      lastPoseIndex = -1;
      renderAt(time);
    });
    return;
  }
  if (data.type === "tho-seek") {
    enqueuePreviewWork(() => {
      stopPreviewMotion();
      const time = Math.max(0, Math.min(Number(data.time) || 0, topic.duration || 0));
      seekPreview(time);
      lastPoseIndex = -1;
      renderAt(time);
    });
    return;
  }
  if (data.type === "tho-play-segment") {
    enqueuePreviewWork(async () => {
      stopPreviewMotion();
      previewComparisonId = "";
      previewTimeLock = null;
      if (data.topic) await applyTopicToView(data.topic, { preserveAudio: true });
      const start = Math.max(0, Math.min(Number(data.start) || 0, topic.duration || 0));
      const end = Math.max(start + 0.08, Math.min(Number(data.end) || start + 0.08, topic.duration || 0));
      if (data.silent) {
        simulatePreviewRange(start, end, {
          allowSfx: !data.parentPoseSfx,
          allowBgm: !data.parentBgm,
        });
      } else {
        playPreviewRange(start, end).catch(console.error);
      }
    });
    return;
  }
  if (data.type === "tho-preview-blank") enqueuePreviewWork(() => showPreviewBlank());
});

new ResizeObserver(() => {
  fitHeadings();
  scheduleImportedPresenterLayout();
}).observe(elements.stage);

init().catch((error) => {
  console.error(error);
  elements.loading.textContent = `Không mở được bài giảng: ${error.message}`;
  window.__AUREX_DEMO_ERROR__ = String(error);
});
