const UNCATEGORIZED_POOL_CATEGORY = "Chưa phân loại";
const POOL_CATEGORIES = [
  "Thời Trang Nữ", "Thời Trang Nam", "Sắc Đẹp", "Sức Khỏe", "Phụ Kiện Thời Trang",
  "Thiết Bị Điện Gia Dụng", "Giày Dép Nam", "Điện Thoại & Phụ Kiện", "Du lịch & Hành lý",
  "Túi Ví Nữ", "Giày Dép Nữ", "Túi Ví Nam", "Đồng Hồ", "Thiết Bị Âm Thanh",
  "Thực phẩm và đồ uống", "Chăm Sóc Thú Cưng", "Mẹ & Bé", "Thời trang trẻ em & trẻ sơ sinh",
  "Gaming & Console", "Cameras & Flycam", "Nhà cửa & Đời sống", "Thể Thao & Dã Ngoại",
  "Văn Phòng Phẩm", "Sở thích & Sưu tầm", "Ô tô", "Mô tô, xe máy", "Voucher & Dịch vụ",
  "Sách & Tạp Chí", "Máy tính & Laptop", "Deal Gần bạn", UNCATEGORIZED_POOL_CATEGORY,
];
const state = { context: {}, overview: {}, products: [], records: [], pool: {}, poolEditingId: "", poolCategory: "", poc: {}, backfill: {}, brand: new URLSearchParams(location.search).get("brand") || "", period: "7d", status: "all", query: "" };
const elements = Object.fromEntries([
  "brandSelect", "periodSelect", "searchInput", "statusSelect", "refreshButton", "syncState", "shopeeState", "shopeeBadge", "facebookState", "facebookBadge",
  "kpiClicks", "kpiClicksDetail", "kpiOrders", "kpiOrdersDetail", "kpiGmv", "kpiGmvDetail", "kpiCommission", "kpiCommissionDetail", "kpiCtr", "kpiCtrDetail", "kpiConversionRate", "kpiConversionRateDetail", "recordsBody", "recordsCount", "toast",
  "affiliateConfirm", "affiliateConfirmTitle", "affiliateConfirmMessage", "affiliateConfirmClose", "affiliateConfirmCancel", "affiliateConfirmAccept",
  "shopeeAppId", "shopeeSecret", "shopeeApiBaseUrl", "shopeeDisplayName", "saveShopeeConfigButton", "disconnectShopeeButton", "settingsHint",
  "affiliateEnabled", "affiliateMode", "affiliatePlacement", "affiliateProductsPerPost", "affiliateMinRelevance", "affiliateMinCommission", "saveAffiliateSettingsButton",
  "poolAffiliateUrl", "poolProductCategory", "poolProductEnabled", "poolCategoryFilter", "deletePoolCategoryButton", "savePoolProductButton", "cancelPoolEditButton", "poolProductsBody", "poolStatus",
  "pocSummary", "pocContentId", "pocPageId", "pocPostId", "pocCommentId", "pocBannerObserved", "pocEvidenceUrl", "pocNotes", "pocCasesBody",
  "backfillStatus", "backfillLimit", "backfillDays", "backfillPreviewButton", "backfillRunButton", "backfillResultsBody",
].map((id) => [id, document.querySelector(`#${id}`)]));

function escapeHtml(value) { return String(value ?? "").replace(/[&<>\"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[char])); }
function showToast(message, error = false) { elements.toast.textContent = message; elements.toast.className = `toast visible${error ? " error" : ""}`; clearTimeout(showToast.timer); showToast.timer = setTimeout(() => { elements.toast.className = "toast"; }, 3400); }
let confirmationResolver = null;
function finishConfirmation(accepted) {
  if (!confirmationResolver) return;
  const resolve = confirmationResolver;
  confirmationResolver = null;
  elements.affiliateConfirm.hidden = true;
  resolve(Boolean(accepted));
}
function askConfirmation({ title, message, acceptLabel = "Xác nhận", danger = false }) {
  if (confirmationResolver) return Promise.resolve(false);
  elements.affiliateConfirmTitle.textContent = title;
  elements.affiliateConfirmMessage.textContent = message;
  elements.affiliateConfirmAccept.textContent = acceptLabel;
  elements.affiliateConfirmAccept.className = `button${danger ? " danger" : ""}`;
  elements.affiliateConfirm.hidden = false;
  elements.affiliateConfirmAccept.focus();
  return new Promise((resolve) => { confirmationResolver = resolve; });
}
function queryString(params) { const query = new URLSearchParams(Object.entries(params).filter(([, value]) => value)); return query.size ? `?${query}` : ""; }
async function request(url, options = {}) { const response = await fetch(url, { cache: "no-store", headers: { "Content-Type": "application/json", ...(options.headers || {}) }, ...options }); const payload = await response.json().catch(() => ({})); if (!response.ok) { const error = new Error(payload.error || `HTTP ${response.status}`); error.status = response.status; throw error; } return payload; }
function unavailable(error) { return error?.status === 404 || error?.status === 501; }
function list(value) { return Array.isArray(value) ? value : []; }
function number(value) { const numeric = Number(value); return Number.isFinite(numeric) ? numeric : 0; }
function money(value) { return new Intl.NumberFormat("vi-VN", { style: "currency", currency: "VND", maximumFractionDigits: 0 }).format(number(value)); }
function count(value) { return new Intl.NumberFormat("vi-VN", { maximumFractionDigits: 0 }).format(number(value)); }
function pick(object, keys, fallback = "") { for (const key of keys) if (object?.[key] !== undefined && object?.[key] !== null && object[key] !== "") return object[key]; return fallback; }
function metric(overview, names) { return pick(overview?.kpis || overview?.metrics || overview, names, 0); }

function readBrands(context) { return list(context.brands || context.items || context.data).map((brand) => typeof brand === "string" ? { id: brand, name: brand } : { id: pick(brand, ["id", "slug", "value", "brandId"]), name: pick(brand, ["name", "title", "label", "id", "slug"]) }).filter((brand) => brand.id); }
function renderBrands() { const brands = readBrands(state.context); const requested = state.brand || pick(state.context, ["activeBrandId", "defaultBrandId", "brandId"]); state.brand = brands.some((brand) => brand.id === requested) ? requested : (brands[0]?.id || requested || ""); elements.brandSelect.replaceChildren(); if (!brands.length) elements.brandSelect.append(new Option("Chưa có Brand", "")); else brands.forEach((brand) => elements.brandSelect.append(new Option(brand.name, brand.id, false, brand.id === state.brand))); elements.brandSelect.disabled = !brands.length; }

function platform(context, key) { const platforms = context.platforms || context.connections || context.integrations || {}; return platforms[key] || platforms[key.toLowerCase()] || context[key] || {}; }
function renderPlatform(key, label) { const item = key === "shopee" && state.context.affiliate?.connection ? state.context.affiliate.connection : platform(state.context, key); const configured = Boolean(pick(item, ["connected", "configured", "active", "ok"], false)); const text = pick(item, ["name", "display_name", "accountName", "pageName", "shopName", "message"], configured ? `Đã kết nối ${label}` : "Chưa kết nối"); const badge = configured ? "Sẵn sàng" : "Chưa kết nối"; const stateElement = elements[`${key}State`]; const badgeElement = elements[`${key}Badge`]; stateElement.textContent = text; badgeElement.textContent = badge; badgeElement.className = `status-pill ${configured ? "ok" : "warn"}`; }
function renderConnections() { renderPlatform("shopee", "Shopee"); renderPlatform("facebook", "Facebook"); }
function renderSettings() {
  const affiliate = state.context.affiliate || {};
  const connection = affiliate.connection || {};
  const settings = affiliate.settings || {};
  const hasBrand = Boolean(state.brand);
  const configured = Boolean(connection.connected || connection.configured);
  elements.shopeeAppId.value = connection.app_id || "";
  elements.shopeeSecret.value = "";
  elements.shopeeSecret.placeholder = connection.masked_secret ? `Đã lưu ${connection.masked_secret} · nhập lại để thay đổi` : "App Secret (tối thiểu 16 ký tự)";
  elements.shopeeApiBaseUrl.value = connection.api_base_url || "https://open-api.affiliate.shopee.vn/graphql";
  elements.shopeeDisplayName.value = connection.display_name || "";
  elements.affiliateEnabled.checked = Boolean(settings.enabled && settings.mode !== "off");
  elements.affiliateMode.value = settings.mode || "manual";
  elements.affiliatePlacement.value = settings.placement || "first_comment";
  elements.affiliateProductsPerPost.value = settings.products_per_post ?? 1;
  elements.affiliateMinRelevance.value = settings.min_relevance ?? 0.75;
  elements.affiliateMinCommission.value = Math.round(Number(settings.min_commission ?? 0.05) * 10000) / 100;
  [elements.shopeeAppId, elements.shopeeSecret, elements.shopeeApiBaseUrl, elements.shopeeDisplayName, elements.affiliateEnabled, elements.affiliateMode, elements.affiliatePlacement, elements.affiliateProductsPerPost, elements.affiliateMinRelevance, elements.affiliateMinCommission, elements.saveShopeeConfigButton, elements.disconnectShopeeButton, elements.saveAffiliateSettingsButton].forEach((element) => { if (element) element.disabled = !hasBrand; });
  elements.disconnectShopeeButton.disabled = !hasBrand || !configured;
  elements.settingsHint.textContent = hasBrand ? (connection.message || (configured ? "Secret không được đọc ngược từ máy chủ; nhập lại nếu cần đổi kết nối." : "Nhập App ID và App Secret để tìm sản phẩm chính thức.")) : "Chọn Brand để cấu hình Shopee Affiliate.";
}
function percent(value) { const numeric = number(value); return `${(numeric <= 1 ? numeric * 100 : numeric).toFixed(1)}%`; }
function renderKpis() { const values = { clicks: metric(state.overview, ["clicks", "totalClicks"]), orders: metric(state.overview, ["orders", "totalOrders"]), gmv: metric(state.overview, ["gmv", "totalGmv", "revenue"]), commission: metric(state.overview, ["commission", "totalCommission", "earnings"]), ctr: metric(state.overview, ["ctr", "clickThroughRate"]), conversionRate: metric(state.overview, ["conversion_rate", "conversionRate", "cvr"]) }; const details = state.overview?.changes || state.overview?.comparison || {}; elements.kpiClicks.textContent = count(values.clicks); elements.kpiOrders.textContent = count(values.orders); elements.kpiGmv.textContent = money(values.gmv); elements.kpiCommission.textContent = money(values.commission); elements.kpiCtr.textContent = values.ctr ? percent(values.ctr) : "—"; elements.kpiConversionRate.textContent = values.clicks ? percent(values.conversionRate) : "—"; elements.kpiCtrDetail.textContent = values.ctr ? "Theo views đã ingest" : "Chưa ingest views"; elements.kpiConversionRateDetail.textContent = values.clicks ? "Orders / clicks" : "Chưa có clicks"; ["Clicks", "Orders", "Gmv", "Commission"].forEach((name) => { const key = name.toLowerCase(); const change = pick(details, [key, `${key}Change`], ""); elements[`kpi${name}Detail`].textContent = change ? String(change) : "Trong khoảng đã chọn"; }); }

function normaliseRecord(item) { const productId = pick(item, ["productId", "product_id", "provider_product_id"]); const campaignId = pick(item, ["campaignId", "campaign_id"]); const type = String(pick(item, ["type", "recordType", "kind"], productId ? "product" : campaignId ? "campaign" : "link")).toLowerCase(); return { id: pick(item, ["id", "linkId", "link_id", "productId", "product_id", "provider_product_id", "campaignId", "campaign_id"]), type: ["link", "product", "campaign"].includes(type) ? type : "link", name: pick(item, ["name", "title", "productName", "product_name", "linkName", "campaignName"], "Chưa đặt tên"), subtitle: pick(item, ["shortUrl", "url", "affiliate_url", "origin_url", "sku", "code", "id"]), campaign: pick(item, ["campaign", "campaignName", "campaignTitle"], "—"), status: String(pick(item, ["status", "state"], "active")).toLowerCase(), clicks: pick(item, ["clicks", "totalClicks"], 0), orders: pick(item, ["orders", "totalOrders"], 0), gmv: pick(item, ["gmv", "totalGmv", "revenue"], 0), commission: pick(item, ["commission", "earnings", "totalCommission"], 0), raw: item }; }
function collectRecords() { const overviewRecords = [...list(state.overview.records), ...list(state.overview.links), ...list(state.overview.products), ...list(state.overview.campaigns)]; const productRecords = list(state.products.products || state.products.items || state.products.data || state.products); const combined = [...overviewRecords, ...productRecords].map(normaliseRecord); return combined.filter((record, index, all) => record.id ? all.findIndex((entry) => entry.id === record.id) === index : true); }
function statusLabel(status) { return ({ active: "Đang chạy", paused: "Tạm dừng", ended: "Đã kết thúc", inactive: "Không hoạt động", prepared: "Đã chuẩn bị", published: "Đã đăng", scheduled: "Đã hẹn giờ", draft: "Bản nháp", comment_failed: "Comment lỗi", created: "Đã tạo" })[status] || (status || "—"); }
function typeLabel(type) { return ({ link: "Link", product: "Sản phẩm", campaign: "Campaign" })[type] || "Link"; }
function filteredRecords() { const needle = state.query.trim().toLocaleLowerCase("vi"); return state.records.filter((record) => (state.status === "all" || record.status === state.status) && (!needle || `${record.name} ${record.subtitle} ${record.campaign}`.toLocaleLowerCase("vi").includes(needle))); }
function renderRecords() { const records = filteredRecords(); elements.recordsCount.textContent = state.records.length ? `${records.length}/${state.records.length} mục` : "Chưa có dữ liệu"; if (!records.length) { elements.recordsBody.innerHTML = `<tr><td colspan="9" class="table-empty">${state.records.length ? "Không có mục khớp bộ lọc." : "Chưa có link, sản phẩm hoặc campaign cho Brand này."}</td></tr>`; return; } elements.recordsBody.innerHTML = records.map((record) => `<tr><td><span class="type-tag ${escapeHtml(record.type)}">${escapeHtml(typeLabel(record.type))}</span></td><td><div class="record-content"><strong title="${escapeHtml(record.name)}">${escapeHtml(record.name)}</strong><small title="${escapeHtml(record.subtitle)}">${escapeHtml(record.subtitle)}</small></div></td><td>${escapeHtml(record.campaign)}</td><td><span class="record-status ${escapeHtml(record.status)}">${escapeHtml(statusLabel(record.status))}</span></td><td class="metric-cell">${count(record.clicks)}</td><td class="metric-cell">${count(record.orders)}</td><td class="metric-cell">${money(record.gmv)}</td><td class="metric-cell">${money(record.commission)}</td><td>${record.type === "product" ? `<button class="button tiny secondary" type="button" data-create-link="${escapeHtml(record.id)}">Tạo link</button>` : ""}</td></tr>`).join(""); }

function backfillStatusLabel(status) { return ({ ready: "Sẵn sàng", eligible: "Sẵn sàng", preview: "Sẵn sàng", dry_run: "Preview", commented: "Đã comment", published: "Đã comment", skipped: "Bỏ qua", skipped_existing: "Đã có comment", skipped_no_product: "Bỏ qua", skipped_unavailable: "Bỏ qua", failed: "Lỗi" })[String(status || "").toLowerCase()] || (status || "—"); }
function backfillDate(value) { if (!value) return "—"; const date = new Date(value); return Number.isNaN(date.getTime()) ? String(value) : new Intl.DateTimeFormat("vi-VN", { dateStyle: "short", timeStyle: "short" }).format(date); }
function renderBackfill() {
  if (!elements.backfillResultsBody) return;
  const result = state.backfill || {};
  const items = list(result.items);
  const dryRun = result.dry_run === true || result.dryRun === true;
  const scanned = number(result.scanned);
  const eligible = number(result.eligible);
  if (!state.brand) elements.backfillStatus.textContent = "Chọn Brand để quét đúng Fanpage.";
  else if (result.error) elements.backfillStatus.textContent = String(result.error);
  else if (result.scanned !== undefined) elements.backfillStatus.textContent = `${dryRun ? "Preview" : "Đã chạy"}: ${count(scanned)} bài · ${count(eligible)} đủ điều kiện · ${count(result.commented)} đã comment · ${count(result.skipped)} bỏ qua`;
  else elements.backfillStatus.textContent = "Chạy preview để xem bài nào đủ điều kiện.";
  elements.backfillPreviewButton.disabled = !state.brand;
  const previewToken = String(result.preview_token || result.previewToken || "").trim();
  elements.backfillRunButton.disabled = !state.brand || !dryRun || eligible < 1 || !previewToken;
  if (!items.length) { elements.backfillResultsBody.innerHTML = `<tr><td colspan="5" class="table-empty">${result.scanned !== undefined ? "Không có bài phù hợp trong khoảng đã chọn." : "Chưa chạy preview."}</td></tr>`; return; }
  elements.backfillResultsBody.innerHTML = items.map((item) => {
    const post = item.post || item;
    const product = item.product || {};
    const permalink = String(item.permalink_url || item.permalinkUrl || post.permalink_url || post.permalink || "").trim();
    const postName = String(item.message_preview || item.description_preview || post.message_preview || post.description_preview || item.post_id || item.postId || post.id || "Bài Facebook").trim();
    const postId = String(item.post_id || item.postId || post.post_id || post.id || "").trim();
    const productName = String(product.name || product.product_name || "Chưa chọn được sản phẩm").trim();
    const commission = product.commission_rate === undefined ? "—" : percent(product.commission_rate);
    const reason = String(item.reason || item.error || "").trim();
    return `<tr><td><div class="backfill-post"><strong title="${escapeHtml(postName)}">${escapeHtml(postName)}</strong><small>${escapeHtml(postId)} · ${escapeHtml(backfillDate(item.created_time || item.createdTime || post.created_time))}</small>${permalink ? `<a href="${escapeHtml(permalink)}" target="_blank" rel="noreferrer">Mở bài trên Facebook ↗</a>` : ""}</div></td><td><div class="backfill-product"><strong title="${escapeHtml(productName)}">${escapeHtml(productName)}</strong><small>${escapeHtml(product.provider || item.product_provider || "")}</small></div></td><td class="metric-cell">${escapeHtml(commission)}</td><td><span class="record-status ${escapeHtml(String(item.status || "").toLowerCase())}">${escapeHtml(backfillStatusLabel(item.status))}</span></td><td><div class="backfill-reason">${escapeHtml(reason || (dryRun && product.name ? "Đủ điều kiện sau khi kiểm tra comment hiện có." : "—"))}</div></td></tr>`;
  }).join("");
}
function invalidateBackfillPreview() { if (state.backfill?.scanned !== undefined) { state.backfill = {}; renderBackfill(); } }
async function runBackfill(dryRun = true) {
  if (!state.brand) return showToast("Hãy chọn Brand trước.", true);
  const limit = Math.max(1, Math.min(50, Math.round(number(elements.backfillLimit.value)) || 10));
  const lookbackDays = Math.max(1, Math.min(365, Math.round(number(elements.backfillDays.value)) || 30));
  if (!dryRun) {
    const eligible = number(state.backfill?.eligible);
    if (!eligible) return showToast("Hãy chạy preview và có bài đủ điều kiện trước.", true);
    const accepted = await askConfirmation({
      title: "Comment link lên các bài đã chọn?",
      message: `AurexVideo sẽ comment link lên tối đa ${eligible} bài của Brand “${state.brand}”. Bạn có muốn tiếp tục không?`,
      acceptLabel: "Comment ngay",
    });
    if (!accepted) return;
  }
  elements.backfillPreviewButton.disabled = true;
  elements.backfillRunButton.disabled = true;
  elements.backfillStatus.textContent = dryRun ? "Đang quét bài và chọn sản phẩm..." : "Đang tạo link và comment...";
  try {
    const previewToken = String(state.backfill?.preview_token || state.backfill?.previewToken || "").trim();
    state.backfill = await request("/api/affiliate/backfill", { method: "POST", body: JSON.stringify({ brand: state.brand, limit, lookbackDays, dryRun, ...(dryRun ? {} : { confirm: "COMMENT", previewToken }) }) });
    renderBackfill();
    showToast(dryRun ? `Preview xong: ${number(state.backfill.eligible)} bài đủ điều kiện.` : `Đã xử lý ${number(state.backfill.commented)} comment.`);
  } catch (error) { state.backfill = { error: error.message }; showToast(error.message, true); }
  finally { elements.backfillPreviewButton.disabled = !state.brand; renderBackfill(); }
}

const FALLBACK_POC_CASES = [
  { key: "A", title: "Manual Reel + manual comment", description: "Đăng Reel thủ công · comment thủ công", publish_mode: "manual", comment_mode: "manual" },
  { key: "B", title: "API Reel + manual comment", description: "Đăng Reel qua API · comment thủ công", publish_mode: "api", comment_mode: "manual" },
  { key: "C", title: "Manual Reel + API comment", description: "Đăng Reel thủ công · comment qua API", publish_mode: "manual", comment_mode: "api" },
  { key: "D", title: "API Reel + API comment", description: "Đăng Reel qua API · comment qua API", publish_mode: "api", comment_mode: "api" },
];
function pocCases() { return list(state.poc.cases || state.poc.caseDefinitions || state.poc.case_definitions).length ? list(state.poc.cases || state.poc.caseDefinitions || state.poc.case_definitions) : FALLBACK_POC_CASES; }
function pocRuns() { return list(state.poc.runs || state.poc.items || state.poc.results); }
function pocCaseKey(item) { return String(pick(item, ["case_key", "caseKey", "key", "case"], "")).toUpperCase(); }
function pocStatusLabel(status) { return ({ pending: "Chưa chạy", running: "Đang chạy", passed: "PASS", failed: "FAIL", blocked: "BLOCKED" })[String(status || "pending").toLowerCase()] || "Chưa chạy"; }
function pocStatusClass(status) { const value = String(status || "pending").toLowerCase(); return ["pending", "running", "passed", "failed", "blocked"].includes(value) ? value : "pending"; }
function pocBannerValue(run) { const value = pick(run, ["banner_observed", "bannerObserved", "banner", "affiliate_banner"], ""); if (value === true || ["yes", "true", "1", "observed", "co"].includes(String(value).toLowerCase())) return "yes"; if (value === false || ["no", "false", "0", "not_observed", "khong"].includes(String(value).toLowerCase())) return "no"; return "unknown"; }
function pocBannerLabel(value) { return ({ yes: "Có banner", no: "Không thấy banner", unknown: "Chưa xác định" })[value] || "Chưa xác định"; }
function pocLatestRuns() { const latest = new Map(); pocRuns().forEach((run) => { const key = pocCaseKey(run); if (key && (!latest.has(key) || String(run.updated_at || run.updatedAt || run.created_at || "") > String(latest.get(key).updated_at || latest.get(key).updatedAt || latest.get(key).created_at || ""))) latest.set(key, run); }); return latest; }
function pocDate(value) { if (!value) return "—"; const date = new Date(value); return Number.isNaN(date.getTime()) ? String(value) : new Intl.DateTimeFormat("vi-VN", { dateStyle: "short", timeStyle: "short" }).format(date); }
function pocSummaryText() { const summary = state.poc.summary || {}; if (summary.total !== undefined || summary.total_runs !== undefined) { const total = number(pick(summary, ["total", "total_runs"], 0)); const passed = number(pick(summary, ["passed", "pass"], 0)); const failed = number(pick(summary, ["failed", "fail"], 0)); const blocked = number(summary.blocked); return `${count(total)} case · ${count(passed)} PASS · ${count(failed)} FAIL${blocked ? ` · ${count(blocked)} BLOCKED` : ""}`; } const counts = { running: 0, passed: 0, failed: 0, blocked: 0 }; pocRuns().forEach((run) => { const status = pocStatusClass(run.status); if (counts[status] !== undefined) counts[status] += 1; }); const total = Object.values(counts).reduce((sum, value) => sum + value, 0); return total ? `${count(total)} case · ${count(counts.passed)} PASS · ${count(counts.failed)} FAIL${counts.blocked ? ` · ${count(counts.blocked)} BLOCKED` : ""}` : (state.brand ? "Chưa có lần kiểm thử nào." : "Chọn Brand để bắt đầu POC."); }
function pocPayload(caseKey, status, run = {}) { return { brand: state.brand, caseKey, runId: pick(run, ["id", "run_id"], ""), contentId: elements.pocContentId.value.trim(), pageId: elements.pocPageId.value.trim(), postId: elements.pocPostId.value.trim(), commentId: elements.pocCommentId.value.trim(), bannerObserved: elements.pocBannerObserved.value || "", evidenceUrl: elements.pocEvidenceUrl.value.trim(), notes: elements.pocNotes.value.trim(), status }; }
function renderPoc() { if (!elements.pocCasesBody) return; const latest = pocLatestRuns(); const contentId = elements.pocContentId.value.trim(); const enabled = Boolean(state.brand && contentId); elements.pocSummary.textContent = enabled ? pocSummaryText() : (state.brand ? "Nhập Content / project ID để bắt đầu." : "Chọn Brand để bắt đầu POC."); elements.pocCasesBody.innerHTML = pocCases().map((definition) => { const key = pocCaseKey(definition); const run = latest.get(key) || {}; const status = pocStatusClass(run.status); const banner = pocBannerValue(run); const hasRun = Boolean(pick(run, ["id", "run_id"], "")); const publishMode = String(pick(definition, ["publish_mode", "publishMode"], "manual")).toUpperCase(); const commentMode = String(pick(definition, ["comment_mode", "commentMode"], "manual")).toUpperCase(); return `<tr><td><div class="poc-case-copy"><span class="poc-case-key">${escapeHtml(key)}</span><strong>${escapeHtml(pick(definition, ["title", "name"], `Case ${key}`))}</strong><small>${escapeHtml(pick(definition, ["description", "summary"], ""))}</small></div></td><td><div class="poc-flow"><code>Reel: ${escapeHtml(publishMode)}</code><span>Comment: ${escapeHtml(commentMode)}</span></div></td><td><span class="record-status ${escapeHtml(status)}">${escapeHtml(pocStatusLabel(status))}</span></td><td><span class="poc-banner ${escapeHtml(banner)}">${escapeHtml(pocBannerLabel(banner))}</span></td><td><div class="poc-run-time"><strong>${escapeHtml(pocDate(pick(run, ["updated_at", "updatedAt", "created_at", "createdAt"], "")))}</strong><small>${escapeHtml(pick(run, ["error", "notes", "note", "message"], "") || (hasRun ? "Đã lưu log" : "Chưa có log"))}</small></div></td><td><div class="poc-actions"><button class="button tiny secondary" type="button" data-poc-case="${escapeHtml(key)}" data-poc-status="running" ${enabled ? "" : "disabled"}>${hasRun ? "Chạy lại" : "Bắt đầu"}</button><button class="button tiny poc-pass" type="button" data-poc-case="${escapeHtml(key)}" data-poc-status="passed" ${hasRun && enabled ? "" : "disabled"}>PASS</button><button class="button tiny poc-fail" type="button" data-poc-case="${escapeHtml(key)}" data-poc-status="failed" ${hasRun && enabled ? "" : "disabled"}>FAIL</button><button class="button tiny poc-blocked" type="button" data-poc-case="${escapeHtml(key)}" data-poc-status="blocked" ${hasRun && enabled ? "" : "disabled"}>BLOCKED</button></div></td></tr>`; }).join(""); }
async function loadPoc() { if (!elements.pocCasesBody) return; try { state.poc = await request(`/api/affiliate/poc${queryString({ brand: state.brand, contentId: elements.pocContentId.value.trim(), pageId: elements.pocPageId.value.trim() })}`); const context = state.poc.context || {}; if (!elements.pocPageId.value && (context.page_id || context.pageId)) elements.pocPageId.value = context.page_id || context.pageId; if (!elements.pocContentId.value && state.poc.content_id) elements.pocContentId.value = state.poc.content_id; renderPoc(); } catch (error) { state.poc = { cases: FALLBACK_POC_CASES, runs: [], summary: {} }; renderPoc(); if (!unavailable(error)) showToast(`Không tải POC: ${error.message}`, true); } }
async function updatePoc(caseKey, status) { if (!state.brand) return showToast("Hãy chọn Brand trước.", true); if (!elements.pocContentId.value.trim()) return showToast("Nhập Content / project ID trước khi ghi POC.", true); const run = pocLatestRuns().get(caseKey) || {}; const buttons = [...document.querySelectorAll(`[data-poc-case="${CSS.escape(caseKey)}"]`)]; buttons.forEach((button) => { button.disabled = true; }); try { await request("/api/affiliate/poc", { method: "POST", body: JSON.stringify(pocPayload(caseKey, status, run)) }); showToast(status === "running" ? `Đã bắt đầu case ${caseKey}.` : `Đã ghi ${pocStatusLabel(status)} cho case ${caseKey}.`); await loadPoc(); } catch (error) { showToast(error.message, true); } finally { renderPoc(); } }
function clearPocRunFields() { [elements.pocPageId, elements.pocPostId, elements.pocCommentId, elements.pocEvidenceUrl, elements.pocNotes].forEach((element) => { if (element) element.value = ""; }); if (elements.pocBannerObserved) elements.pocBannerObserved.value = ""; }

function poolItems() { const value = state.pool || {}; return list(value.products || value.items || value.data || value); }
function poolCategories() {
  const fromApi = list(state.pool?.categories).map((value) => String(value || "").trim()).filter(Boolean);
  return fromApi.length ? fromApi : POOL_CATEGORIES;
}
function renderPoolCategoryOptions() {
  const categories = poolCategories();
  const editValue = elements.poolProductCategory?.value || UNCATEGORIZED_POOL_CATEGORY;
  const filterValue = state.poolCategory || "";
  if (elements.poolProductCategory) {
    elements.poolProductCategory.replaceChildren(...categories.map((category) => new Option(category, category)));
    elements.poolProductCategory.value = categories.includes(editValue) ? editValue : UNCATEGORIZED_POOL_CATEGORY;
  }
  if (elements.poolCategoryFilter) {
    elements.poolCategoryFilter.replaceChildren(new Option("Tất cả", ""), ...categories.map((category) => new Option(category, category)));
    elements.poolCategoryFilter.value = categories.includes(filterValue) ? filterValue : "";
    state.poolCategory = elements.poolCategoryFilter.value;
  }
}
function clearPoolForm() {
  state.poolEditingId = "";
  if (elements.poolAffiliateUrl) elements.poolAffiliateUrl.value = "";
  if (elements.poolProductCategory) elements.poolProductCategory.value = UNCATEGORIZED_POOL_CATEGORY;
  if (elements.poolProductEnabled) elements.poolProductEnabled.value = "true";
  if (elements.savePoolProductButton) elements.savePoolProductButton.textContent = "Thêm vào Pool";
  if (elements.cancelPoolEditButton) elements.cancelPoolEditButton.hidden = true;
}
function renderPool() {
  if (!elements.poolProductsBody) return;
  const items = poolItems();
  const enabled = items.filter((item) => Boolean(item.enabled)).length;
  const categoryLabel = state.poolCategory || "Tất cả";
  renderPoolCategoryOptions();
  if (!state.brand) elements.poolStatus.textContent = "Chọn Brand để quản lý link trong Pool.";
  else if (!items.length) elements.poolStatus.textContent = state.poolCategory ? `Không có link trong danh mục “${categoryLabel}”.` : "Pool đang trống · dán các link affiliate đã chọn.";
  else elements.poolStatus.textContent = `${count(enabled)}/${count(items.length)} link đang bật · ${categoryLabel}.`;
  if (elements.deletePoolCategoryButton) {
    elements.deletePoolCategoryButton.disabled = !state.brand || !items.length;
    elements.deletePoolCategoryButton.textContent = `Xoá ${count(items.length)} link`;
  }
  if (!items.length) { elements.poolProductsBody.innerHTML = '<tr><td colspan="4" class="table-empty">Chưa có link trong Pool.</td></tr>'; return; }
  elements.poolProductsBody.innerHTML = items.map((item) => {
    const id = String(item.id || "");
    const affiliate = String(item.affiliate_url || "");
    const category = String(item.category || UNCATEGORIZED_POOL_CATEGORY);
    const status = item.enabled ? "Đang bật" : "Tạm tắt";
    return `<tr><td><a class="pool-link pool-link-wide" href="${escapeHtml(affiliate)}" target="_blank" rel="noreferrer" title="${escapeHtml(affiliate)}">${escapeHtml(affiliate)}</a></td><td><span class="pool-category">${escapeHtml(category)}</span></td><td><span class="record-status ${item.enabled ? "active" : "paused"}">${escapeHtml(status)}</span></td><td><div class="pool-actions"><button class="button tiny secondary" type="button" data-pool-edit="${escapeHtml(id)}">Sửa</button><button class="button tiny danger" type="button" data-pool-delete="${escapeHtml(id)}">Xoá</button></div></td></tr>`;
  }).join("");
}
async function loadPool() {
  if (!elements.poolProductsBody) return;
  if (!state.brand) { state.pool = {}; renderPool(); return; }
  try { state.pool = await request(`/api/affiliate/pool${queryString({ brand: state.brand, category: state.poolCategory })}`); renderPool(); }
  catch (error) { state.pool = {}; renderPool(); if (!unavailable(error)) showToast(`Không tải Pool: ${error.message}`, true); }
}
function poolPayload() {
  const links = String(elements.poolAffiliateUrl?.value || "").split(/\r?\n/).map((value) => value.trim()).filter(Boolean);
  if (state.poolEditingId) {
    return { brand: state.brand, id: state.poolEditingId, affiliateUrl: links[0] || "", category: elements.poolProductCategory.value, enabled: elements.poolProductEnabled.value === "true" };
  }
  return { brand: state.brand, links, category: elements.poolProductCategory.value, enabled: elements.poolProductEnabled.value === "true" };
}
async function savePoolProduct() {
  if (!state.brand) return showToast("Hãy chọn Brand trước.", true);
  const links = String(elements.poolAffiliateUrl?.value || "").split(/\r?\n/).map((value) => value.trim()).filter(Boolean);
  if (state.poolEditingId && links.length !== 1) return showToast("Khi sửa, chỉ để lại đúng một link trong ô nhập.", true);
  if (!state.poolEditingId && !links.length) return showToast("Dán ít nhất một link affiliate, mỗi link một dòng.", true);
  elements.savePoolProductButton.disabled = true;
  try { const result = await request("/api/affiliate/pool", { method: "POST", body: JSON.stringify(poolPayload()) }); showToast(state.poolEditingId ? "Đã cập nhật link trong Pool." : `Đã thêm ${count(result.count || links.length)} link vào Pool.`); clearPoolForm(); await loadPool(); }
  catch (error) { showToast(error.message, true); }
  finally { renderPool(); elements.savePoolProductButton.disabled = !state.brand; }
}
function editPoolProduct(id) {
  const item = poolItems().find((entry) => String(entry.id) === String(id));
  if (!item) return;
  state.poolEditingId = String(item.id || "");
  elements.poolAffiliateUrl.value = item.affiliate_url || "";
  elements.poolProductCategory.value = item.category || UNCATEGORIZED_POOL_CATEGORY;
  elements.poolProductEnabled.value = item.enabled ? "true" : "false";
  elements.savePoolProductButton.textContent = "Lưu thay đổi";
  elements.cancelPoolEditButton.hidden = false;
  elements.poolAffiliateUrl.focus();
}
async function deletePoolProduct(id) {
  if (!state.brand || !id) return;
  const accepted = await askConfirmation({
    title: "Xoá link khỏi Pool?",
    message: `Link này sẽ bị xoá khỏi Pool Shopee của Brand “${state.brand}”. Bạn có chắc muốn tiếp tục không?`,
    acceptLabel: "Xoá link",
    danger: true,
  });
  if (!accepted) return;
  const button = document.querySelector(`[data-pool-delete="${CSS.escape(String(id))}"]`);
  if (button) button.disabled = true;
  try { await request(`/api/affiliate/pool/${encodeURIComponent(id)}${queryString({ brand: state.brand })}`, { method: "DELETE" }); if (state.poolEditingId === String(id)) clearPoolForm(); showToast("Đã xoá sản phẩm khỏi Pool."); await loadPool(); }
  catch (error) { showToast(error.message, true); }
  finally { if (button?.isConnected) button.disabled = false; }
}
async function deletePoolCategory() {
  if (!state.brand) return;
  const items = poolItems();
  if (!items.length) return;
  const categoryLabel = state.poolCategory ? `danh mục “${state.poolCategory}”` : "tất cả danh mục";
  const accepted = await askConfirmation({
    title: `Xoá ${count(items.length)} link khỏi Pool?`,
    message: `Chỉ ${count(items.length)} link của Brand “${state.brand}” trong ${categoryLabel} sẽ bị xoá. Dữ liệu Brand khác không bị ảnh hưởng.`,
    acceptLabel: `Xoá ${count(items.length)} link`,
    danger: true,
  });
  if (!accepted) return;
  elements.deletePoolCategoryButton.disabled = true;
  try {
    const result = await request(`/api/affiliate/pool${queryString({ brand: state.brand, category: state.poolCategory })}`, { method: "DELETE" });
    clearPoolForm();
    showToast(`Đã xoá ${count(result.count ?? result.deleted ?? 0)} link khỏi Pool.`);
    await loadPool();
  } catch (error) { showToast(error.message, true); }
  finally { renderPool(); }
}
async function loadContext() { try { let context = await request(`/api/affiliate/context${queryString({ brand: state.brand })}`); state.context = context; renderBrands(); if (state.brand && context.selected_brand !== state.brand) { context = await request(`/api/affiliate/context${queryString({ brand: state.brand })}`); state.context = context; renderBrands(); } renderConnections(); renderSettings(); renderBackfill(); return true; } catch (error) { state.context = {}; renderBrands(); renderConnections(); renderSettings(); renderBackfill(); elements.syncState.textContent = unavailable(error) ? "API affiliate chưa được bật" : "Không đọc được cấu hình"; return false; } }
async function loadData() { const params = { brand: state.brand, period: state.period, status: state.status === "all" ? "" : state.status, q: state.query }; const [overviewResult, productsResult] = await Promise.allSettled([request(`/api/affiliate/overview${queryString(params)}`), request(`/api/affiliate/products${queryString(params)}`)]); const unavailableApi = [overviewResult, productsResult].some((result) => result.status === "rejected" && unavailable(result.reason)); state.overview = overviewResult.status === "fulfilled" ? overviewResult.value : {}; state.products = productsResult.status === "fulfilled" ? productsResult.value : []; state.records = collectRecords(); renderKpis(); renderRecords(); if (overviewResult.status === "rejected" && !unavailable(overviewResult.reason)) showToast(`Không tải overview: ${overviewResult.reason.message}`, true); if (productsResult.status === "rejected" && !unavailable(productsResult.reason)) showToast(`Không tải danh sách: ${productsResult.reason.message}`, true); elements.syncState.textContent = unavailableApi ? "API affiliate chưa được bật" : (pick(state.overview, ["updatedAt", "syncedAt"], "Đã đồng bộ dữ liệu")); }
async function refresh() { elements.refreshButton.disabled = true; elements.syncState.textContent = "Đang đồng bộ..."; await loadContext(); await loadPool(); await loadData(); await loadPoc(); elements.refreshButton.disabled = false; }
async function createLink(productId) { const record = state.records.find((item) => String(item.id) === String(productId)); if (!record) return; const button = document.querySelector(`[data-create-link="${CSS.escape(String(productId))}"]`); if (button) { button.disabled = true; button.textContent = "Đang tạo..."; } try { const result = await request("/api/affiliate/link", { method: "POST", body: JSON.stringify({ brand: state.brand, productId, product: record.raw }) }); const link = result.link; const url = typeof link === "string" ? link : pick(link, ["affiliate_url", "url", "shortUrl"], pick(result, ["url", "shortUrl"])); if (url) { try { await navigator.clipboard?.writeText(url); } catch (_) {} showToast("Đã tạo và copy link affiliate."); } else showToast(result.message || "Đã gửi yêu cầu tạo link."); await loadData(); } catch (error) { showToast(unavailable(error) ? "Tính năng tạo link sẽ sẵn sàng khi API affiliate được bật." : error.message, true); } finally { if (button?.isConnected) { button.disabled = false; button.textContent = "Tạo link"; } } }

function settingsPayload() { const enabled = Boolean(elements.affiliateEnabled.checked); return { brand: state.brand, enabled, mode: enabled ? elements.affiliateMode.value : "off", placement: elements.affiliatePlacement.value, productsPerPost: Number(elements.affiliateProductsPerPost.value || 1), minRelevance: Number(elements.affiliateMinRelevance.value || 0.75), minCommission: Number(elements.affiliateMinCommission.value || 5) }; }
async function saveAffiliateSettings() { if (!state.brand) return showToast("Hãy chọn Brand trước.", true); elements.saveAffiliateSettingsButton.disabled = true; try { await request("/api/affiliate/settings", { method: "POST", body: JSON.stringify(settingsPayload()) }); showToast("Đã lưu chính sách Affiliate cho Brand."); await refresh(); } catch (error) { showToast(error.message, true); } finally { renderSettings(); } }
async function saveShopeeConfig() { if (!state.brand) return showToast("Hãy chọn Brand trước.", true); const secret = elements.shopeeSecret.value.trim(); if (!secret) return showToast("Nhập lại App Secret để lưu kết nối.", true); elements.saveShopeeConfigButton.disabled = true; try { await request("/api/social/shopee/config", { method: "POST", body: JSON.stringify({ brand: state.brand, appId: elements.shopeeAppId.value.trim(), secret, apiBaseUrl: elements.shopeeApiBaseUrl.value.trim(), displayName: elements.shopeeDisplayName.value.trim(), settings: settingsPayload() }) }); showToast("Đã lưu kết nối Shopee Affiliate."); await refresh(); } catch (error) { showToast(error.message, true); } finally { renderSettings(); } }
async function disconnectShopee() { if (!state.brand) return; const accepted = await askConfirmation({ title: "Gỡ kết nối Shopee?", message: `Kết nối Shopee Affiliate của Brand “${state.brand}” sẽ bị gỡ khỏi cấu hình local. Bạn có chắc muốn tiếp tục không?`, acceptLabel: "Gỡ kết nối", danger: true }); if (!accepted) return; elements.disconnectShopeeButton.disabled = true; try { await request("/api/social/shopee/disconnect", { method: "POST", body: JSON.stringify({ brand: state.brand }) }); showToast("Đã gỡ kết nối Shopee Affiliate."); await refresh(); } catch (error) { showToast(error.message, true); } finally { renderSettings(); } }

elements.brandSelect.addEventListener("change", () => { state.brand = elements.brandSelect.value; state.backfill = {}; state.pool = {}; state.poolCategory = ""; clearPoolForm(); clearPocRunFields(); renderPool(); renderBackfill(); refresh(); });
elements.periodSelect.addEventListener("change", () => { state.period = elements.periodSelect.value; loadData(); });
elements.statusSelect.addEventListener("change", () => { state.status = elements.statusSelect.value; loadData(); });
elements.searchInput.addEventListener("input", () => { state.query = elements.searchInput.value; renderRecords(); clearTimeout(state.searchTimer); state.searchTimer = setTimeout(loadData, 300); });
elements.refreshButton.addEventListener("click", refresh);
elements.recordsBody.addEventListener("click", (event) => { const button = event.target.closest("[data-create-link]"); if (button) createLink(button.dataset.createLink); });
elements.pocCasesBody?.addEventListener("click", (event) => { const button = event.target.closest("[data-poc-case]"); if (button) updatePoc(button.dataset.pocCase, button.dataset.pocStatus); });
elements.affiliateConfirmClose?.addEventListener("click", () => finishConfirmation(false));
elements.affiliateConfirmCancel?.addEventListener("click", () => finishConfirmation(false));
elements.affiliateConfirmAccept?.addEventListener("click", () => finishConfirmation(true));
elements.affiliateConfirm?.addEventListener("click", (event) => { if (event.target === elements.affiliateConfirm) finishConfirmation(false); });
document.addEventListener("keydown", (event) => { if (event.key === "Escape" && confirmationResolver) finishConfirmation(false); });
elements.pocContentId?.addEventListener("change", loadPoc);
elements.pocPageId?.addEventListener("change", loadPoc);
elements.saveAffiliateSettingsButton?.addEventListener("click", saveAffiliateSettings);
elements.saveShopeeConfigButton?.addEventListener("click", saveShopeeConfig);
elements.disconnectShopeeButton?.addEventListener("click", disconnectShopee);
elements.savePoolProductButton?.addEventListener("click", savePoolProduct);
elements.cancelPoolEditButton?.addEventListener("click", clearPoolForm);
elements.poolCategoryFilter?.addEventListener("change", () => { state.poolCategory = elements.poolCategoryFilter.value; clearPoolForm(); loadPool(); });
elements.deletePoolCategoryButton?.addEventListener("click", deletePoolCategory);
elements.poolProductsBody?.addEventListener("click", (event) => { const edit = event.target.closest("[data-pool-edit]"); const remove = event.target.closest("[data-pool-delete]"); if (edit) editPoolProduct(edit.dataset.poolEdit); else if (remove) deletePoolProduct(remove.dataset.poolDelete); });
elements.backfillPreviewButton?.addEventListener("click", () => runBackfill(true));
elements.backfillRunButton?.addEventListener("click", () => runBackfill(false));
elements.backfillLimit?.addEventListener("input", invalidateBackfillPreview);
elements.backfillDays?.addEventListener("input", invalidateBackfillPreview);
refresh();
