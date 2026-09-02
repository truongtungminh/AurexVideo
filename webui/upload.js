const state = { projects: [], selected: new URLSearchParams(location.search).get("project") || "", status: null, affiliateContext: null, affiliateProduct: null, affiliateLink: "" };
const elements = Object.fromEntries([
  "uploadProject", "uploadVideo", "videoState", "uploadTitle", "youtubeDescription", "facebookCaption",
  "affiliateCard", "affiliateStatus", "affiliateMode", "affiliatePlacement", "affiliateQuery", "affiliateSearchButton", "affiliateLinkButton", "affiliateProduct", "affiliateLink", "affiliateAutoComment", "affiliateDashboardLink",
  "uploadYoutubeChannel", "youtubePrivacy", "youtubeScheduleToggle", "youtubeScheduleRow", "youtubeScheduleTime", "uploadYoutubeButton", "uploadFacebookPage", "facebookScheduleToggle", "facebookScheduleRow", "facebookScheduleTime", "uploadFacebookButton", "instagramScheduleToggle", "instagramScheduleRow", "instagramScheduleTime", "uploadInstagram", "threadsScheduleToggle", "threadsScheduleRow", "threadsScheduleTime", "uploadThreads", "tiktokCaption", "tiktokScheduleToggle", "tiktokScheduleRow", "tiktokScheduleTime", "uploadTiktokButton", "configureTiktokButton", "tiktokConfigModal", "tiktokConfigClose", "tiktokConfigState", "zernioApiKey", "zernioAccountId", "tiktokSaveButton", "tiktokDisconnectButton", "uploadBinanceButton", "binanceDuration", "binanceCaption",
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

function queryString(params) {
  const query = new URLSearchParams(Object.entries(params).filter(([, value]) => value));
  return query.size ? `?${query}` : "";
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
  if (!toggle || !row || !time) return;
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
setupScheduleToggle("instagramScheduleToggle", "instagramScheduleRow", "instagramScheduleTime");
setupScheduleToggle("threadsScheduleToggle", "threadsScheduleRow", "threadsScheduleTime");

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
  const metadata = await api(`/api/social/upload-metadata?project=${encodeURIComponent(state.selected)}`);
  elements.uploadTitle.value = metadata.title;
  elements.youtubeDescription.value = metadata.youtubeDescription;
  elements.facebookCaption.value = metadata.facebookCaption;
  elements.youtubePrivacy.value = metadata.privacyStatus || "public";
  elements.tiktokCaption.value = metadata.tiktokCaption || metadata.instagramCaption || metadata.facebookCaption || "";
  await loadAffiliateContext();
}

function affiliateBrand() {
  return state.affiliateContext?.selected_brand || state.affiliateContext?.project_brand || "";
}

function formatAffiliatePercent(value) {
  const number = Number(value) || 0;
  return `${(number <= 1 ? number * 100 : number).toFixed(1)}%`;
}

function renderAffiliateProduct() {
  if (!elements.affiliateProduct) return;
  const product = state.affiliateProduct;
  elements.affiliateLink.textContent = state.affiliateLink ? `Link Affiliate: ${state.affiliateLink}` : "";
  elements.affiliateLinkButton.disabled = !product || !affiliateBrand();
  if (!product) {
    elements.affiliateProduct.className = "affiliate-product empty";
    elements.affiliateProduct.textContent = "Chưa chọn sản phẩm.";
    return;
  }
  elements.affiliateProduct.className = "affiliate-product";
  elements.affiliateProduct.replaceChildren();
  if (product.image_url) {
    const image = document.createElement("img");
    image.src = product.image_url;
    image.alt = "";
    elements.affiliateProduct.append(image);
  } else {
    const placeholder = document.createElement("div");
    placeholder.className = "affiliate-product-image-placeholder";
    placeholder.textContent = "🛒";
    elements.affiliateProduct.append(placeholder);
  }
  const detail = document.createElement("div");
  const name = document.createElement("strong");
  name.textContent = product.name || "Shopee product";
  detail.append(name);
  const meta = document.createElement("small");
  const price = Number(product.price_min) > 0 ? `${Number(product.price_min).toLocaleString("vi-VN")}đ` : "Giá chưa có";
  meta.textContent = `${price} · Hoa hồng ${formatAffiliatePercent(product.commission_rate)} · Liên quan ${formatAffiliatePercent(product.relevance_score)}`;
  detail.append(meta);
  elements.affiliateProduct.append(detail);
  const score = document.createElement("span");
  score.className = "score";
  score.textContent = `Score ${(Number(product.ranking_score) || 0).toFixed(2)}`;
  elements.affiliateProduct.append(score);
}

async function loadAffiliateContext() {
  if (!elements.affiliateCard || !state.selected) return;
  const context = await api(`/api/affiliate/context?project=${encodeURIComponent(state.selected)}`);
  state.affiliateContext = context;
  state.affiliateProduct = null;
  state.affiliateLink = "";
  const affiliate = context.affiliate || {};
  const settings = affiliate.settings || {};
  const connection = affiliate.connection || {};
  const pool = affiliate.pool || {};
  const brand = context.selected_brand || context.project_brand || "—";
  const poolStatus = pool.configured
    ? `Pool Shopee ${pool.enabled || 0}/${pool.total || 0} sản phẩm đang bật`
    : "Pool Shopee đang trống";
  elements.affiliateMode.value = settings.enabled ? (settings.mode || "manual") : "off";
  elements.affiliatePlacement.value = settings.placement || "first_comment";
  elements.affiliateQuery.value = "";
  elements.affiliateAutoComment.checked = ["first_comment", "caption_and_comment"].includes(elements.affiliatePlacement.value);
  elements.affiliateStatus.textContent = settings.mode === "auto"
    ? `${poolStatus} · AUTO chỉ dùng sản phẩm đã duyệt · Brand ${brand}`
    : connection.connected
      ? `Đã kết nối ${connection.display_name || "Shopee Affiliate"} · Brand ${brand}`
      : (connection.message || `${poolStatus} · Brand ${brand}`);
  elements.affiliateDashboardLink.href = `/affiliate${context.selected_brand ? `?brand=${encodeURIComponent(context.selected_brand)}` : ""}`;
  elements.affiliateSearchButton.disabled = !connection.connected && !pool.configured;
  renderAffiliateProduct();
}

async function searchAffiliateProducts() {
  const brand = affiliateBrand();
  if (!brand) return showToast("Project chưa có Brand để tìm sản phẩm.", true);
  const query = elements.affiliateQuery.value.trim();
  const mode = elements.affiliateMode.value || "off";
  if (!query && mode !== "auto") return showToast("Nhập từ khoá sản phẩm trước.", true);
  elements.affiliateSearchButton.disabled = true;
  try {
    let result;
    const poolConfigured = Boolean(state.affiliateContext?.affiliate?.pool?.configured);
    if (mode === "auto" || (!state.affiliateContext?.affiliate?.connection?.connected && poolConfigured)) {
      result = await api(`/api/affiliate/pool${queryString({ brand, q: query, enabledOnly: "true", limit: 50 })}`);
      let products = result.products || result.items || [];
      if (!products.length && query) {
        result = await api(`/api/affiliate/pool${queryString({ brand, enabledOnly: "true", limit: 50 })}`);
        products = result.products || result.items || [];
      }
      state.affiliateProduct = products[0] || null;
    } else {
      result = await api(`/api/affiliate/products?brand=${encodeURIComponent(brand)}&project=${encodeURIComponent(state.selected)}&query=${encodeURIComponent(query)}`);
      state.affiliateProduct = result.product || result.products?.[0] || null;
    }
    state.affiliateLink = state.affiliateProduct?.affiliate_url || "";
    renderAffiliateProduct();
    if (!state.affiliateProduct) showToast("Không có sản phẩm phù hợp.", true);
    else showToast(mode === "auto" ? "Đã chọn sản phẩm trong Pool Shopee." : "Đã chọn sản phẩm có điểm phù hợp nhất.");
  } catch (error) {
    showToast(error.message, true);
  } finally {
    elements.affiliateSearchButton.disabled = false;
  }
}

async function generateAffiliateLink() {
  const brand = affiliateBrand();
  if (!brand || !state.affiliateProduct) return showToast("Hãy chọn Brand và sản phẩm trước.", true);
  elements.affiliateLinkButton.disabled = true;
  try {
    const pageId = elements.uploadFacebookPage?.value || "";
    const result = await api("/api/affiliate/link", {
      method: "POST",
      body: JSON.stringify({
        project: state.selected,
        brand,
        productId: state.affiliateProduct.id,
        originUrl: state.affiliateProduct.origin_url || "",
        affiliateUrl: state.affiliateLink || state.affiliateProduct.affiliate_url || "",
        placement: elements.affiliatePlacement.value,
        pageId,
        linkProvider: state.affiliateProduct.link_provider || "",
        product: state.affiliateProduct,
      }),
    });
    state.affiliateLink = result.link?.affiliate_url || "";
    renderAffiliateProduct();
    showToast("Đã tạo link Shopee Affiliate có SubID.");
  } catch (error) {
    showToast(error.message, true);
  } finally {
    elements.affiliateLinkButton.disabled = false;
  }
}

function readAffiliatePayload() {
  const mode = elements.affiliateMode?.value || "off";
  return {
    enabled: mode !== "off",
    mode,
    placement: elements.affiliatePlacement?.value || "first_comment",
    query: elements.affiliateQuery?.value.trim() || "",
    productId: state.affiliateProduct?.id || "",
    originUrl: state.affiliateProduct?.origin_url || "",
    affiliateUrl: state.affiliateLink || state.affiliateProduct?.affiliate_url || "",
    linkProvider: state.affiliateProduct?.link_provider || state.affiliateProduct?.raw?._aurex_link_provider || "",
    product: state.affiliateProduct || null,
    autoComment: Boolean(elements.affiliateAutoComment?.checked),
  };
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
  const button = platform === "youtube" ? elements.uploadYoutubeButton : platform === "facebook" ? elements.uploadFacebookButton : platform === "tiktok" ? elements.uploadTiktokButton : platform === "instagram" ? elements.uploadInstagram : platform === "threads" ? elements.uploadThreads : elements.uploadBinanceButton;
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
            : platform === "instagram"
              ? (elements.instagramScheduleToggle.checked ? readScheduleTime(elements.instagramScheduleTime, "Instagram") : "")
              : platform === "threads"
                ? (elements.threadsScheduleToggle.checked ? readScheduleTime(elements.threadsScheduleTime, "Threads") : "")
                : "";
    const payload =
      platform === "youtube"
        ? { project: state.selected, title: elements.uploadTitle.value, description: elements.youtubeDescription.value, privacyStatus: elements.youtubePrivacy.value, ...(scheduledPublishAt ? { scheduledPublishAt } : {}) }
        : platform === "facebook"
          ? { project: state.selected, facebookCaption: elements.facebookCaption.value, facebookVideoState: "PUBLISHED", affiliate: readAffiliatePayload(), ...(scheduledPublishAt ? { scheduledPublishAt } : {}) }
          : platform === "tiktok"
            ? { project: state.selected, tiktokCaption: elements.tiktokCaption.value, ...(scheduledPublishAt ? { scheduledPublishAt, scheduleTimezone: Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC' } : {}) }
            : platform === "instagram"
              ? { project: state.selected, instagramCaption: elements.instagramCaption.value, ...(scheduledPublishAt ? { scheduledPublishAt } : {}) }
              : platform === "threads"
                ? { project: state.selected, threadsText: elements.threadsText.value, ...(scheduledPublishAt ? { scheduledPublishAt } : {}) }
                : { project: state.selected, duration: Number(elements.binanceDuration.value), text: elements.binanceCaption.value };
    const result = await api(`/api/social/${platform}/upload`, { method: "POST", body: JSON.stringify(payload) });
    const resultMessage = result.message || "Upload hoàn tất.";
    elements.uploadResult.replaceChildren(document.createTextNode(resultMessage + (result.url ? " " : "")));
    if (result.url) {
      const link = document.createElement("a"); link.href = result.url; link.target = "_blank"; link.textContent = "Mở bài đã đăng ↗";
      if (["DRAFT", "INBOX"].includes(String(result.state || "").toUpperCase())) link.textContent = "Mở Creator Inbox/Draft ↗";
      elements.uploadResult.append(link);
    }
    showToast(resultMessage);
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

elements.uploadProject?.addEventListener("change", () => { state.selected = elements.uploadProject.value; history.replaceState({}, "", `/upload?project=${encodeURIComponent(state.selected)}`); loadProject().catch((error) => showToast(error.message, true)); });
elements.uploadYoutubeChannel?.addEventListener("change", () => setActive("youtube", elements.uploadYoutubeChannel.value).catch((error) => showToast(error.message, true)));
elements.uploadFacebookPage?.addEventListener("change", () => setActive("facebook", elements.uploadFacebookPage.value).catch((error) => showToast(error.message, true)));
elements.uploadYoutubeButton?.addEventListener("click", () => upload("youtube"));
elements.uploadFacebookButton?.addEventListener("click", () => upload("facebook"));
elements.uploadTiktokButton?.addEventListener("click", () => upload("tiktok"));
elements.uploadInstagram?.addEventListener("click", () => upload("instagram"));
elements.uploadThreads?.addEventListener("click", () => upload("threads"));
elements.affiliateSearchButton?.addEventListener("click", searchAffiliateProducts);
elements.affiliateLinkButton?.addEventListener("click", generateAffiliateLink);
elements.affiliatePlacement?.addEventListener("change", () => { state.affiliateLink = ""; renderAffiliateProduct(); });
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
