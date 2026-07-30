const DEFAULT_TOPIC = "project/inox-304-vs-316/topic.json";

const elements = {
  stage: document.querySelector("#stage"),
  stageBackgroundImage: document.querySelector("#stageBackgroundImage"),
  leftLabel: document.querySelector("#leftLabel"),
  rightLabel: document.querySelector("#rightLabel"),
  leftSubLabel: document.querySelector("#leftSubLabel"),
  rightSubLabel: document.querySelector("#rightSubLabel"),
  leftImage: document.querySelector("#leftImage"),
  rightImage: document.querySelector("#rightImage"),
  karaoke: document.querySelector("#karaoke"),
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
let poseSwapRaf = 0;
let currentComparisonKey = "";
let currentComparisonScene = null;
let previewComparisonId = "";
let previewTimeLock = null;
let presenterLayoutRaf = 0;
let presenterLayoutGeneration = 0;
let lastPresenterSource = "";
let lastKaraokeGroupKey = "";
const presenterAlphaBounds = new Map();
const presenterFrameCache = new Map();
const IMPORTED_PRESENTER_GAP_PX = 24;
const IMPORTED_PRESENTER_BOTTOM_PX = 28;
const IMPORTED_PRESENTER_MAX_WIDTH = 0.58;
const IMPORTED_PRESENTER_MAX_HEIGHT = 0.52;

if (renderMode) document.body.classList.add("render-mode");
if (previewFrame) document.body.classList.add("preview-frame");
if (offlineRender) document.body.classList.add("offline-render");
if (renderMode && !previewFrame && !offlineRender) elements.syncMarker.classList.add("visible");

function resolveTopicAsset(path) {
  return new URL(path, new URL(topicUrl, window.location.href)).href;
}

function formatTime(value) {
  const seconds = Math.max(0, Math.floor(Number(value) || 0));
  return `${String(Math.floor(seconds / 60)).padStart(2, "0")}:${String(seconds % 60).padStart(2, "0")}`;
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
    leftLabelColor: nextTopic.leftLabelColor || nextTopic.labelColor || "#090909",
    rightLabelColor: nextTopic.rightLabelColor || nextTopic.labelColor || "#090909",
    leftSubLabelColor: nextTopic.leftSubLabelColor || "#808080",
    rightSubLabelColor: nextTopic.rightSubLabelColor || "#808080",
    showSubLabels: nextTopic.showSubLabels === true
      || Boolean(String(nextTopic.leftSubLabel || "").trim() || String(nextTopic.rightSubLabel || "").trim()),
  };
}

function comparisonAt(time) {
  if (previewComparisonId === "base") return baseComparison();
  if (previewComparisonId) {
    const locked = (Array.isArray(topic.comparisons) ? topic.comparisons : [])
      .find((item) => String(item.id || "") === previewComparisonId);
    if (locked) return locked;
  }
  let sentence = 1;
  (topic.segments || []).forEach((segment, index) => {
    if (Number(segment.start) <= Number(time) + 0.001) sentence = index + 1;
  });
  let selected = baseComparison();
  [...(Array.isArray(topic.comparisons) ? topic.comparisons : [])]
    .sort((a, b) => Number(a.startSentence) - Number(b.startSentence))
    .forEach((item) => {
      if (Number(item.startSentence) <= sentence) selected = item;
    });
  return selected;
}

function applyComparisonToView(scene, force = false) {
  if (!scene) return;
  const key = String(scene.id || `sentence-${scene.startSentence || 1}`);
  if (!force && key === currentComparisonKey) return;
  currentComparisonKey = key;
  currentComparisonScene = scene;
  elements.leftLabel.textContent = formatTopicLabel(scene.leftLabel);
  elements.rightLabel.textContent = formatTopicLabel(scene.rightLabel);
  elements.leftLabel.style.color = String(scene.leftLabelColor || scene.labelColor || topic.leftLabelColor || topic.labelColor || "#090909");
  elements.rightLabel.style.color = String(scene.rightLabelColor || scene.labelColor || topic.rightLabelColor || topic.labelColor || "#090909");
  applySubLabel(elements.leftSubLabel, scene.leftSubLabel, scene.leftSubLabelColor || topic.leftSubLabelColor, scene);
  applySubLabel(elements.rightSubLabel, scene.rightSubLabel, scene.rightSubLabelColor || topic.rightSubLabelColor, scene);
  const leftSrc = resolveTopicAsset(scene.leftImage);
  const rightSrc = resolveTopicAsset(scene.rightImage);
  if (elements.leftImage.src !== leftSrc) elements.leftImage.src = leftSrc;
  if (elements.rightImage.src !== rightSrc) elements.rightImage.src = rightSrc;
  applyImageFrame(elements.leftImage, scene.leftImageZoom, scene.leftImageX, scene.leftImageY);
  applyImageFrame(elements.rightImage, scene.rightImageZoom, scene.rightImageX, scene.rightImageY);
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
  element.style.color = String(color || "#808080");
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
  const max = stageWidth * 6.7 / 100;
  const min = stageWidth * 2.8 / 100;
  let low = min;
  let high = max;
  const labels = [elements.leftLabel, elements.rightLabel];
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
  // Đồng bộ với renderer AurexVideo: trong khoảng trống giữa hai word timing,
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
  if (mediaReady(element)) return Promise.resolve();
  return new Promise((resolve, reject) => {
    const cleanup = () => {
      element.removeEventListener("loadeddata", onReady);
      element.removeEventListener("load", onReady);
      element.removeEventListener("error", onError);
    };
    const onReady = () => {
      cleanup();
      resolve();
    };
    const onError = () => {
      cleanup();
      reject(new Error(`Không tải được media: ${mediaSource(element)}`));
    };
    element.addEventListener("loadeddata", onReady, { once: true });
    element.addEventListener("load", onReady, { once: true });
    element.addEventListener("error", onError, { once: true });
  });
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
  player.volume = topic.sfxVolume ?? 0.7;
  player.play().catch(() => {});
  lastSfxAt = time;
}

function focusSideForPose(poseName) {
  const id = String(poseName || "").toLowerCase();
  const label = String(topic.poseLabels?.[poseName] || "").toLowerCase();
  if (id.includes("left") || label.includes("trái") || label.includes("left")) return "left";
  if (id.includes("right") || label.includes("phải") || label.includes("right")) return "right";
  const poseIndex = Object.keys(topic.poseAssets || {}).indexOf(poseName);
  if (poseIndex === 0 || poseIndex === 3) return "left";
  if (poseIndex === 1 || poseIndex === 4) return "right";
  return "";
}

function updateMediaFocus(poseName) {
  const leftSlot = elements.leftImage.parentElement;
  const rightSlot = elements.rightImage.parentElement;
  if (!leftSlot || !rightSlot) return;
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
    return;
  }
  const source = mediaSource(elements.teacher);
  if (!source || !mediaReady(elements.teacher)) return;
  const bounds = await alphaBoundsForImage(elements.teacher);
  if (!bounds || generation !== presenterLayoutGeneration || source !== mediaSource(elements.teacher)) return;

  const stageRect = elements.stage.getBoundingClientRect();
  const subtitleRects = Array.from(elements.karaoke.children, (node) => node.getBoundingClientRect());
  const subtitleBottom = subtitleRects.length
    ? Math.max(...subtitleRects.map((rect) => rect.bottom))
    : stageRect.top + stageRect.height * 0.518;
  const logicalScale = stageRect.height / 1920;
  const visibleWidth = bounds.right - bounds.left;
  const visibleHeight = bounds.bottom - bounds.top;
  const visibleTop = subtitleBottom - stageRect.top + IMPORTED_PRESENTER_GAP_PX * logicalScale;
  const visibleBottom = stageRect.height - IMPORTED_PRESENTER_BOTTOM_PX * logicalScale;
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
}

function scheduleImportedPresenterLayout() {
  presenterLayoutGeneration += 1;
  const generation = presenterLayoutGeneration;
  cancelAnimationFrame(presenterLayoutRaf);
  presenterLayoutRaf = requestAnimationFrame(() => {
    layoutImportedPresenter(generation).catch(console.error);
  });
}

function setPose(event, time, allowSfx = false) {
  if (event.index !== lastPoseIndex) {
    currentPose = event.pose;
    lastPoseIndex = event.index;
    if (allowSfx) playPoseSfx(event, time);
  }
  // Use exactly one full-body sprite per pose to keep the presenter stationary.
  const nextSrc = poseImage(currentPose, true);
  const currentSrc = mediaSource(elements.teacher);
  const shouldResetVideo = isVideoAssetSource(nextSrc) && event.index !== lastPoseIndex;
  if (currentSrc !== nextSrc || shouldResetVideo) {
    if (currentSrc !== nextSrc) {
      elements.teacher.classList.add("pose-swap");
      elements.teacher.src = nextSrc;
      lastPresenterSource = nextSrc;
      cancelAnimationFrame(poseSwapRaf);
      poseSwapRaf = requestAnimationFrame(() => {
        poseSwapRaf = requestAnimationFrame(() => elements.teacher.classList.remove("pose-swap"));
      });
    }
    if (isVideoAssetSource(nextSrc)) {
      elements.teacher.muted = true;
      elements.teacher.loop = true;
      elements.teacher.playsInline = true;
      elements.teacher.currentTime = 0;
      elements.teacher.play().catch(() => {});
    }
  }
  updateMediaFocus(currentPose);
}

function renderKaraoke(time) {
  const activeIndex = activeWordAt(time);
  if (activeIndex < 0) {
    elements.karaoke.classList.remove("visible");
    elements.karaoke.replaceChildren();
    if (lastKaraokeGroupKey) {
      lastKaraokeGroupKey = "";
      scheduleImportedPresenterLayout();
    }
    return;
  }
  const group = wordGroups.find((indexes) => indexes.includes(activeIndex));
  if (!group) return;
  const fragment = document.createDocumentFragment();
  group.forEach((wordIndex) => {
    const span = document.createElement("span");
    span.textContent = timedWords[wordIndex].word;
    if (wordIndex === activeIndex) span.className = "active";
    fragment.append(span);
  });
  elements.karaoke.replaceChildren(fragment);
  elements.karaoke.classList.add("visible");
  const groupKey = group.join(",");
  if (groupKey !== lastKaraokeGroupKey) {
    lastKaraokeGroupKey = groupKey;
    scheduleImportedPresenterLayout();
  }
}

function renderAt(time, allowPoseSfx = false) {
  if (previewFrame && previewTimeLock !== null) time = previewTimeLock;
  elements.stage.classList.remove("preview-blank");
  applyComparisonToView(comparisonAt(time));
  renderKaraoke(time);
  setPose(poseAt(time), time, allowPoseSfx);
  const duration = elements.voiceover.duration || topic.duration || 0;
  elements.timeText.textContent = `${formatTime(time)} / ${formatTime(duration)}`;
  elements.progress.value = duration > 0 ? Math.round(time / duration * 1000) : 0;
}

function offlineImagePaths() {
  const paths = [topic.leftImage, topic.rightImage];
  if (String(topic.backgroundType || "").toLowerCase() === "image" && topic.backgroundImage) {
    paths.push(topic.backgroundImage);
  }
  (Array.isArray(topic.comparisons) ? topic.comparisons : []).forEach((scene) => {
    paths.push(scene.leftImage, scene.rightImage);
  });
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
  renderAt(time);
  await Promise.all([
    elements.leftImage.decode().catch(() => {}),
    elements.rightImage.decode().catch(() => {}),
    waitForMediaReady(elements.teacher).catch(() => {}),
    elements.stageBackgroundImage && !elements.stageBackgroundImage.hidden
      ? elements.stageBackgroundImage.decode().catch(() => {})
      : Promise.resolve(),
  ]);
  applyStageBackground(topic);
  if (currentComparisonScene) {
    applyImageFrame(
      elements.leftImage,
      currentComparisonScene.leftImageZoom,
      currentComparisonScene.leftImageX,
      currentComparisonScene.leftImageY,
    );
    applyImageFrame(
      elements.rightImage,
      currentComparisonScene.rightImageZoom,
      currentComparisonScene.rightImageX,
      currentComparisonScene.rightImageY,
    );
  }
  await layoutImportedPresenter(presenterLayoutGeneration);
  fitHeadings();
}

async function prepareOfflineRender() {
  await Promise.all([
    preloadOfflineImages(),
    document.fonts?.ready || Promise.resolve(),
  ]);
  await renderOfflineFrame(0);
}

function animationLoop() {
  const time = elements.voiceover.currentTime || 0;
  renderAt(time, true);
  if (previewStopTime !== null && time >= previewStopTime - 0.015) {
    elements.voiceover.pause();
    stopBackgroundMusic();
    elements.voiceover.currentTime = previewStopTime;
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
  if (fromStart || elements.voiceover.ended) {
    elements.voiceover.currentTime = 0;
    lastPoseIndex = -1;
    lastSfxAt = -Infinity;
  }
  syncBackgroundMusic(elements.voiceover.currentTime || 0, { playing: false });
  await elements.voiceover.play();
  // In headless capture, play() can resolve before the media clock advances.
  // Keep the magenta sync frame visible until audio timing truly starts so
  // the separately muxed voiceover and recorded karaoke share the same zero.
  await waitForAudioClockStart();
  syncBackgroundMusic(elements.voiceover.currentTime || 0, { playing: !offlineRender });
  renderAt(elements.voiceover.currentTime || 0, true);
  elements.syncMarker.classList.remove("visible");
  if (!window.__AUREX_DEMO_STARTED_PERF__) window.__AUREX_DEMO_STARTED_PERF__ = performance.now();
  elements.playButton.textContent = "Tạm dừng";
  cancelAnimationFrame(raf);
  raf = requestAnimationFrame(animationLoop);
}

async function playPreviewRange(start, end) {
  stopPreviewMotion();
  previewStopTime = Math.max(start + 0.08, end);
  elements.voiceover.currentTime = start;
  lastPoseIndex = -1;
  lastSfxAt = -Infinity;
  renderAt(start, true);
  syncBackgroundMusic(start, { playing: false });
  await elements.voiceover.play();
  syncBackgroundMusic(start, { playing: true });
  cancelAnimationFrame(raf);
  raf = requestAnimationFrame(animationLoop);
}

function simulatePreviewRange(start, end, { allowSfx = true, allowBgm = true } = {}) {
  stopPreviewMotion();
  lastPoseIndex = -1;
  lastSfxAt = -Infinity;
  const startedAt = performance.now();
  const duration = Math.max(0.08, end - start);
  if (allowBgm) syncBackgroundMusic(start, { playing: true });
  const tick = (now) => {
    const elapsed = Math.max(0, (now - startedAt) / 1000);
    const time = Math.min(end, start + elapsed);
    renderAt(time, allowSfx);
    if (elapsed < duration) previewSimulationRaf = requestAnimationFrame(tick);
    else if (allowBgm) stopBackgroundMusic();
  };
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
  elements.stage.classList.add("preview-blank");
}

function pausePlayback() {
  elements.voiceover.pause();
  stopBackgroundMusic();
  elements.playButton.textContent = "Phát";
  cancelAnimationFrame(raf);
  renderAt(elements.voiceover.currentTime || 0);
}

function bindControls() {
  elements.playButton.addEventListener("click", () => {
    if (elements.voiceover.paused) startPlayback();
    else pausePlayback();
  });
  elements.restartButton.addEventListener("click", () => startPlayback(true));
  elements.progress.addEventListener("input", () => {
    const duration = elements.voiceover.duration || topic.duration || 0;
    elements.voiceover.currentTime = Number(elements.progress.value) / 1000 * duration;
    lastPoseIndex = -1;
    syncBackgroundMusic(elements.voiceover.currentTime || 0, { playing: !elements.voiceover.paused });
    renderAt(elements.voiceover.currentTime);
  });
  elements.voiceover.addEventListener("ended", () => {
    elements.playButton.textContent = "Phát";
    cancelAnimationFrame(raf);
    const duration = elements.voiceover.duration || topic.duration || 0;
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
  const base = Math.min(vw / nw, vh / nh);
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

function applyKaraokeStyle(nextTopic = topic) {
  if (!elements.karaoke) return;
  const color = String(nextTopic?.karaokeColor || "#271f11");
  const active = String(nextTopic?.karaokeActiveColor || "#de370d");
  const size = Math.min(1.5, Math.max(0.6, Number(nextTopic?.karaokeSize) || 1.2));
  elements.karaoke.style.setProperty("--karaoke-color", color);
  elements.karaoke.style.setProperty("--karaoke-active-color", active);
  elements.karaoke.style.setProperty("--karaoke-size", String(size));
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

function applyTopicToView(nextTopic, { preserveAudio = true } = {}) {
  topic = nextTopic;
  timedWords = buildTimedWords(topic.segments || []);
  wordGroups = buildGroups(timedWords);
  currentPose = topic.poseTimeline?.[0]?.pose || Object.keys(topic.poseAssets || {})[0] || "question";
  lastPoseIndex = -1;
  const isCustomCharacter = Boolean(topic.characterId && topic.characterId !== "human-presenter");
  elements.teacherWrap.classList.toggle("custom-character", isCustomCharacter);
  // Gắn class character-<id> để CSS tuỳ biến riêng từng nhân vật (vd. character-bietchichomet).
  // Gắn cả lên #stage để style các sibling (media-slot, label...) theo nhân vật.
  const stageClasses = elements.stage.classList;
  stageClasses.remove(...Array.from(stageClasses).filter((cls) => cls.startsWith("character-")));
  if (isCustomCharacter && topic.characterId) {
    elements.teacherWrap.classList.add(`character-${topic.characterId}`);
    stageClasses.add(`character-${topic.characterId}`);
  }
  if (!isCustomCharacter) clearImportedPresenterLayout();
  currentComparisonKey = "";
  applyStageBackground(topic);
  applyComparisonToView(baseComparison(topic), true);
  applyKaraokeStyle(topic);
  updateMediaFocus(currentPose);
  if (!preserveAudio) elements.voiceover.src = resolveTopicAsset(topic.voiceover);
  syncBackgroundMusic(elements.voiceover.currentTime || 0, { playing: !elements.voiceover.paused && !offlineRender });
  requestAnimationFrame(fitHeadings);
}

async function init() {
  const response = await fetch(topicUrl, { cache: "no-store" });
  if (!response.ok) throw new Error(`Không tải được topic: ${response.status}`);
  topic = await response.json();
  await preloadOfflineImages();
  applyTopicToView(topic, { preserveAudio: false });
  bindStageImageFrame(elements.leftImage, "left");
  bindStageImageFrame(elements.rightImage, "right");
  if (elements.stageBackgroundImage) {
    elements.stageBackgroundImage.addEventListener("load", () => applyStageBackground(topic));
    if (elements.stageBackgroundImage.parentElement) {
      new ResizeObserver(() => applyStageBackground(topic)).observe(elements.stageBackgroundImage.parentElement);
    }
  }

  Object.entries(topic.sfx || {}).forEach(([name, path]) => {
    sfxPlayers[name] = new Audio(resolveTopicAsset(path));
    sfxPlayers[name].preload = "auto";
  });

  currentPose = topic.poseTimeline[0].pose;
  elements.teacher.src = poseImage(currentPose, false);
  elements.teacher.muted = true;
  elements.teacher.loop = true;
  elements.teacher.playsInline = true;
  elements.teacher.preload = "auto";
  elements.teacher.load();
  lastPresenterSource = elements.teacher.src;
  elements.teacher.addEventListener("loadeddata", scheduleImportedPresenterLayout);
  elements.teacher.addEventListener("seeked", scheduleImportedPresenterLayout);
  bindControls();

  await Promise.all([
    elements.leftImage.decode().catch(() => {}),
    elements.rightImage.decode().catch(() => {}),
    waitForMediaReady(elements.teacher).catch(() => {}),
    new Promise((resolve) => {
      if (elements.voiceover.readyState >= 1) resolve();
      else elements.voiceover.addEventListener("loadedmetadata", resolve, { once: true });
    }),
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
  window.__layoutImportedPresenterForTest = () => layoutImportedPresenter(presenterLayoutGeneration);
  fitHeadings();
  document.fonts?.ready.then(fitHeadings);
  if (autoplay) await startPlayback(true);
}

window.addEventListener("message", (event) => {
  if (event.origin !== window.location.origin || !event.data || !topic) return;
  if (event.data.type === "tho-topic-update" && event.data.topic) {
    stopPreviewMotion();
    if (Object.prototype.hasOwnProperty.call(event.data, "comparisonId")) {
      previewComparisonId = String(event.data.comparisonId || "");
    }
    applyTopicToView(event.data.topic, { preserveAudio: true });
    const time = Math.max(0, Math.min(Number(event.data.time) || 0, topic.duration || 0));
    previewTimeLock = previewComparisonId ? time : null;
    elements.voiceover.currentTime = time;
    renderAt(time);
  }
  if (event.data.type === "tho-preview-comparison-lock") {
    stopPreviewMotion();
    previewComparisonId = String(event.data.comparisonId || "");
    const time = Math.max(0, Math.min(Number(event.data.time) || 0, topic.duration || 0));
    previewTimeLock = previewComparisonId ? time : null;
    elements.voiceover.currentTime = time;
    lastPoseIndex = -1;
    renderAt(time);
  }
  if (event.data.type === "tho-seek") {
    stopPreviewMotion();
    const time = Math.max(0, Math.min(Number(event.data.time) || 0, topic.duration || 0));
    elements.voiceover.currentTime = time;
    lastPoseIndex = -1;
    renderAt(time);
  }
  if (event.data.type === "tho-play-segment") {
    previewComparisonId = "";
    previewTimeLock = null;
    if (event.data.topic) applyTopicToView(event.data.topic, { preserveAudio: true });
    const start = Math.max(0, Math.min(Number(event.data.start) || 0, topic.duration || 0));
    const end = Math.max(start + 0.08, Math.min(Number(event.data.end) || start + 0.08, topic.duration || 0));
    if (event.data.silent) {
      simulatePreviewRange(start, end, {
        allowSfx: !event.data.parentPoseSfx,
        allowBgm: !event.data.parentBgm,
      });
    } else {
      playPreviewRange(start, end).catch(console.error);
    }
  }
  if (event.data.type === "tho-preview-blank") showPreviewBlank();
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
