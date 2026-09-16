(() => {
  "use strict";

  const PLATFORMS = {
    youtube: { label: "YouTube", short: "YT", description: "Kênh video dài", accountKey: "channels", identityKey: "channel_id" },
    facebook: { label: "Facebook", short: "f", description: "Trang Facebook", accountKey: "pages", identityKey: "page_id" },
    instagram: { label: "Instagram", short: "◎", description: "Instagram Business", accountKey: "accounts", identityKey: "ig_user_id" },
    tiktok: { label: "TikTok", short: "♪", description: "Kênh short video", accountKey: "accounts", identityKey: "account_id" },
    threads: { label: "Threads", short: "T", description: "Kênh hội thoại", accountKey: "accounts", identityKey: "threads_user_id" },
  };

  const state = {
    context: null,
    brands: [],
    selectedBrandId: "",
    search: "",
    socialDialogMode: "add",
    pendingDelete: null,
    pendingDeleteBrand: null,
    metaConnections: [],
    selectedMetaConnectionId: "",
    metaPages: [],
    metaPageSearch: "",
    metaDiagnostic: null,
    toastTimer: null,
  };

  const $ = (selector) => document.querySelector(selector);
  const $$ = (selector) => Array.from(document.querySelectorAll(selector));

  function escapeHtml(value) {
    return String(value ?? "")
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#039;");
  }

  function brandName(brand) {
    return String(brand?.name || brand?.displayName || brand?.id || "");
  }

  function brandInitial(brand) {
    return brandName(brand).trim().charAt(0).toUpperCase() || "A";
  }

  function selectedBrand() {
    return state.brands.find((brand) => brand.id === state.selectedBrandId) || null;
  }

  function routeFor(brand, platform) {
    return brand?.routes?.[platform] || state.context?.brand_routes?.[brand?.id]?.[platform] || null;
  }

  function firstUnassignedPlatform(brand) {
    return Object.keys(PLATFORMS).find((platform) => !routeFor(brand, platform)) || "";
  }

  function routeIdentity(route, platform) {
    if (!route) return "";
    const meta = PLATFORMS[platform] || {};
    const key = platform === "youtube" || platform === "facebook" ? meta.identityKey : "connection_id";
    return String(route[key] || route[meta.identityKey] || route.account_id || route.id || "").trim();
  }

  function routeDisplayIdentity(route, platform) {
    if (!route) return "";
    const meta = PLATFORMS[platform] || {};
    return String(route[meta.identityKey] || route.connection_id || route.account_id || route.id || "").trim();
  }

  function platformStatus(platform) {
    return state.context?.platforms?.[platform] || {};
  }

  function platformAccounts(platform) {
    const status = platformStatus(platform);
    const meta = PLATFORMS[platform] || {};
    return Array.isArray(status[meta.accountKey]) ? status[meta.accountKey] : [];
  }

  function accountIdentity(account, platform) {
    if (!account) return "";
    const meta = PLATFORMS[platform] || {};
    return String(account.id || account.connection_id || account[meta.identityKey] || account.account_id || "").trim();
  }

  function accountLabel(account, platform) {
    if (!account) return "";
    const meta = PLATFORMS[platform] || {};
    return String(account.title || account.name || account.display_name || account.displayName || account[meta.identityKey] || account.connection_id || account.id || "").trim();
  }

  function findAccount(platform, identity) {
    return platformAccounts(platform).find((account) => accountIdentity(account, platform) === String(identity || "")) || null;
  }

  function isPlatformReady(brand, platform) {
    const route = routeFor(brand, platform);
    if (!route) return false;
    const identity = routeIdentity(route, platform);
    const status = platformStatus(platform);
    const account = findAccount(platform, identity);
    if (platform === "youtube" || platform === "facebook") {
      return Boolean(account && !account.error && (status.connected || status.available || status.configured));
    }
    return Boolean(
      route.configured !== false && route.connected !== false && route.available !== false &&
      (!account || (account.connected !== false && account.available !== false && !account.error))
    );
  }

  function platformView(brand, platform) {
    const route = routeFor(brand, platform);
    const status = platformStatus(platform);
    const identity = routeIdentity(route, platform);
    const displayIdentity = routeDisplayIdentity(route, platform);
    const account = findAccount(platform, identity);
    const ready = isPlatformReady(brand, platform);
    const rawIssue = String(account?.error || "").replace(/\s+/g, " ").trim();
    const issue = rawIssue
      ? (platform === "youtube" && /invalid_grant|expired|revoked|refresh/i.test(rawIssue)
        ? "Token đã hết hạn hoặc bị thu hồi"
        : rawIssue.replace(/^YouTube token refresh failed:\s*/i, "").slice(0, 120))
      : (!account && (platform === "youtube" || platform === "facebook") ? "Account không còn trong workspace" : "");
    if (ready) {
      return { tone: "ready", label: "Đã kết nối", detail: route?.name || accountLabel(account, platform) || "Sẵn sàng xuất bản", identity: displayIdentity };
    }
    if (route) {
      return { tone: "attention", label: "Cần kiểm tra", detail: issue || route?.name || accountLabel(account, platform) || status.message || "Kết nối chưa sẵn sàng", identity: displayIdentity };
    }
    if (status.connected || status.available || status.configured || platformAccounts(platform).length) {
      return { tone: "unconfigured", label: "Chưa gán Brand", detail: "Account đã có trong workspace", identity: "" };
    }
    return { tone: "unconfigured", label: "Chưa cấu hình", detail: "Mở Cài đặt để kết nối", identity: "" };
  }

  function showToast(message, isError = false) {
    const toast = $("#socialToast");
    if (!toast) return;
    toast.textContent = message;
    toast.classList.toggle("is-error", isError);
    toast.classList.add("is-visible");
    window.clearTimeout(state.toastTimer);
    state.toastTimer = window.setTimeout(() => toast.classList.remove("is-visible"), 3600);
  }

  async function requestJson(url, options = {}) {
    const response = await fetch(url, { headers: { "Content-Type": "application/json", ...(options.headers || {}) }, ...options });
    let payload = {};
    try { payload = await response.json(); } catch (_) {}
    if (!response.ok) throw new Error(payload.error || "Request thất bại (" + response.status + ").");
    return payload;
  }

  function selectedMetaConnection() {
    return state.metaConnections.find((item) => item.id === state.selectedMetaConnectionId) || null;
  }

  function formatMetaDate(value) {
    if (!value) return "—";
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString("vi-VN", { dateStyle: "short", timeStyle: "short" });
  }

  function formatMetaExpiry(value, known = false, hasToken = false) {
    if (!hasToken) return "—";
    if (!known) return "Không rõ";
    const timestamp = Number(value || 0);
    if (!timestamp) return "Không hết hạn";
    return formatMetaDate(new Date(timestamp * 1000).toISOString());
  }

  function setMetaNote(message, tone = "") {
    const note = $("#metaFormNote");
    if (!note) return;
    note.textContent = message;
    note.className = "dialog-note meta-form-note" + (tone ? " is-" + tone : "");
  }

  function setMetaButtonBusy(button, busy, busyText = "Đang xử lý…") {
    if (!button) return;
    if (busy) {
      button.dataset.label = button.textContent;
      button.textContent = busyText;
      button.disabled = true;
      button.setAttribute("aria-busy", "true");
    } else {
      button.textContent = button.dataset.label || button.textContent;
      button.disabled = false;
      button.removeAttribute("aria-busy");
      delete button.dataset.label;
    }
  }

  function renderMetaConnectionPicker() {
    const select = $("#metaConnectionSelect");
    if (!select) return;
    select.innerHTML = '<option value="">Connection mới</option>' + state.metaConnections.map((connection) =>
      '<option value="' + escapeHtml(connection.id) + '">' + escapeHtml(connection.name || connection.id) + " · " + Number(connection.page_count || 0) + " Pages</option>"
    ).join("");
    select.value = state.selectedMetaConnectionId;
  }

  function populateMetaConnectionForm() {
    const connection = selectedMetaConnection();
    const name = $("#metaConnectionName");
    const businessId = $("#metaBusinessId");
    const token = $("#metaSystemUserToken");
    if (name) name.value = connection?.name || "";
    if (businessId) businessId.value = connection?.business_id || "";
    if (token) {
      token.value = "";
      token.placeholder = connection?.token_configured
        ? "Đã lưu " + (connection.token_hint || "token") + " · để trống nếu giữ nguyên"
        : "Dán token của Người dùng hệ thống";
    }
    const syncButton = $("#metaSyncButton");
    if (syncButton && syncButton.getAttribute("aria-busy") !== "true") {
      syncButton.disabled = !connection;
    }
  }

  function renderMetaDiagnostic() {
    const connection = selectedMetaConnection();
    const diagnostic = state.metaDiagnostic;
    const rawStatus = diagnostic
      ? (diagnostic.valid ? "valid" : "invalid")
      : String(connection?.token_status || (connection?.token_configured ? "unchecked" : "missing"));
    const statusLabels = { valid: "Hợp lệ", invalid: "Không hợp lệ", error: "Cần kiểm tra", unchecked: "Chưa kiểm tra", missing: "Chưa lưu" };
    const status = $("#metaTokenStatus");
    if (status) {
      status.textContent = statusLabels[rawStatus] || rawStatus;
      status.className = rawStatus === "valid" ? "is-valid" : rawStatus === "invalid" ? "is-invalid" : rawStatus === "error" ? "is-warning" : "";
    }
    $("#metaSystemUserId").textContent = diagnostic?.system_user_id || connection?.system_user_id || "—";
    $("#metaAppId").textContent = diagnostic?.app_id || connection?.app_id || "—";
    $("#metaAccessiblePages").textContent = Number(diagnostic?.accessible_pages_count ?? connection?.accessible_pages_count ?? connection?.active_page_count ?? 0);
    $("#metaTokenExpiry").textContent = formatMetaExpiry(
      diagnostic?.expires_at ?? connection?.token_expires_at,
      Boolean(diagnostic?.expiry_known ?? connection?.token_expiry_known),
      Boolean(diagnostic || connection?.token_configured),
    );
    const scopes = diagnostic?.scopes || connection?.scopes || [];
    $("#metaScopes").textContent = Array.isArray(scopes) && scopes.length ? scopes.join(" · ") : "—";
    $("#metaLastChecked").textContent = formatMetaDate(diagnostic?.checked_at || connection?.last_checked_at);
    $("#metaLastSynced").textContent = formatMetaDate(connection?.last_synced_at);
  }

  function renderMetaPages() {
    const list = $("#metaPagesList");
    if (!list) return;
    if (!state.selectedMetaConnectionId) {
      list.innerHTML = '<div class="meta-pages-empty">Lưu Meta Connection và bấm “Đồng bộ Pages” để import Page.</div>';
      return;
    }
    const query = state.metaPageSearch.trim().toLocaleLowerCase();
    const pages = state.metaPages.filter((page) => !query || (String(page.name || "") + " " + String(page.id || "")).toLocaleLowerCase().includes(query));
    if (!pages.length) {
      list.innerHTML = '<div class="meta-pages-empty">' + (state.metaPages.length ? "Không tìm thấy Page phù hợp." : "Connection này chưa có Page. Bấm “Đồng bộ Pages” để lấy dữ liệu từ Meta.") + "</div>";
      return;
    }
    const connection = selectedMetaConnection();
    const brandOptions = state.brands.map((brand) => '<option value="' + escapeHtml(brand.id) + '">' + escapeHtml(brandName(brand)) + "</option>").join("");
    list.innerHTML = pages.map((page) => {
      const inaccessible = page.status === "inaccessible";
      const currentBrand = String(page.brand || "");
      const tasks = Array.isArray(page.tasks) && page.tasks.length
        ? page.tasks.slice(0, 5).map((task) => '<span class="meta-task-chip">' + escapeHtml(task) + "</span>").join("")
        : '<span class="meta-task-chip">Chưa có tasks</span>';
      const thumbnail = /^https?:\/\//i.test(String(page.thumbnail || ""))
        ? '<img class="meta-page-logo" src="' + escapeHtml(page.thumbnail) + '" alt="" />'
        : '<span class="meta-page-logo" aria-hidden="true">f</span>';
      return '<article class="meta-page-row' + (inaccessible ? " is-inaccessible" : "") + '" data-meta-page-id="' + escapeHtml(page.id) + '">' +
        '<span class="meta-page-check" aria-hidden="true">' + (inaccessible ? "!" : "✓") + "</span>" +
        thumbnail +
        '<div class="meta-page-copy"><strong>' + escapeHtml(page.name || page.id) + '</strong><code>ID: ' + escapeHtml(page.id) + "</code></div>" +
        '<div class="meta-task-list">' + tasks + "</div>" +
        '<div class="meta-page-token"><span>' + escapeHtml(connection?.name || "Meta Connection") + '</span><strong class="' + (inaccessible ? "is-inaccessible" : "") + '">' + (inaccessible ? "Mất quyền" : page.token_configured ? "Token hoạt động" : "Thiếu token") + '</strong><small>Đồng bộ ' + escapeHtml(formatMetaDate(page.last_synced_at)) + "</small></div>" +
        '<div class="meta-brand-map"><select data-meta-page-brand="' + escapeHtml(page.id) + '" data-current-brand="' + escapeHtml(currentBrand) + '"' + (inaccessible ? " disabled" : "") + '><option value="">Chưa gán Brand</option>' + brandOptions + '</select><button type="button" data-save-meta-brand="' + escapeHtml(page.id) + '"' + (inaccessible ? " disabled" : "") + '>Lưu</button></div>' +
        "</article>";
    }).join("");
    $$('[data-meta-page-brand]').forEach((select) => { select.value = select.dataset.currentBrand || ""; });
  }

  function renderMetaPanel() {
    renderMetaConnectionPicker();
    renderMetaDiagnostic();
    renderMetaPages();
  }

  async function loadMetaPages() {
    if (!state.selectedMetaConnectionId) {
      state.metaPages = [];
      renderMetaPages();
      return;
    }
    const payload = await requestJson("/api/meta/connections/" + encodeURIComponent(state.selectedMetaConnectionId) + "/pages");
    state.metaPages = Array.isArray(payload.pages) ? payload.pages : [];
    renderMetaPages();
  }

  async function loadMetaConnections(preferredId = "") {
    try {
      const payload = await requestJson("/api/meta/connections");
      state.metaConnections = Array.isArray(payload.connections) ? payload.connections : [];
      const saved = preferredId || state.selectedMetaConnectionId || window.localStorage.getItem("aurex-meta-connection") || "";
      state.selectedMetaConnectionId = state.metaConnections.some((item) => item.id === saved) ? saved : (state.metaConnections[0]?.id || "");
      if (state.selectedMetaConnectionId) window.localStorage.setItem("aurex-meta-connection", state.selectedMetaConnectionId);
      else window.localStorage.removeItem("aurex-meta-connection");
      state.metaDiagnostic = null;
      renderMetaConnectionPicker();
      populateMetaConnectionForm();
      renderMetaDiagnostic();
      await loadMetaPages();
    } catch (error) {
      state.metaConnections = [];
      state.selectedMetaConnectionId = "";
      state.metaPages = [];
      renderMetaPanel();
      setMetaNote(error.message || "Không thể tải Meta Connection.", "error");
    }
  }

  function newMetaConnection() {
    state.selectedMetaConnectionId = "";
    state.metaDiagnostic = null;
    state.metaPages = [];
    window.localStorage.removeItem("aurex-meta-connection");
    renderMetaConnectionPicker();
    populateMetaConnectionForm();
    renderMetaDiagnostic();
    renderMetaPages();
    setMetaNote("Nhập tên và System User Access Token, sau đó chẩn đoán trước khi đồng bộ.");
    $("#metaConnectionName")?.focus();
  }

  async function saveMetaConnection({ silent = false } = {}) {
    const name = $("#metaConnectionName")?.value.trim() || "";
    const token = $("#metaSystemUserToken")?.value.trim() || "";
    const businessId = $("#metaBusinessId")?.value.trim() || "";
    if (!name) throw new Error("Tên Meta Connection không được để trống.");
    if (!state.selectedMetaConnectionId && !token) throw new Error("Hãy nhập System User Access Token.");
    const payload = { name, businessId };
    if (state.selectedMetaConnectionId) payload.id = state.selectedMetaConnectionId;
    if (token) payload.systemUserAccessToken = token;
    const result = await requestJson("/api/meta/connections", { method: "POST", body: JSON.stringify(payload) });
    const connection = result.connection || {};
    state.selectedMetaConnectionId = connection.id || state.selectedMetaConnectionId;
    if (!state.selectedMetaConnectionId) throw new Error("Backend không trả Meta Connection ID.");
    window.localStorage.setItem("aurex-meta-connection", state.selectedMetaConnectionId);
    await loadMetaConnections(state.selectedMetaConnectionId);
    if (!silent) showToast("Đã lưu Meta Connection “" + (connection.name || name) + "”.");
    return state.selectedMetaConnectionId;
  }

  async function diagnoseMetaToken() {
    const button = $("#metaDiagnoseButton");
    const token = $("#metaSystemUserToken")?.value.trim() || "";
    if (!token && !state.selectedMetaConnectionId) throw new Error("Hãy nhập System User Access Token trước.");
    setMetaButtonBusy(button, true, "Đang kiểm tra…");
    try {
      const result = token
        ? await requestJson("/api/meta/diagnose", { method: "POST", body: JSON.stringify({ systemUserAccessToken: token }) })
        : await requestJson("/api/meta/connections/" + encodeURIComponent(state.selectedMetaConnectionId) + "/diagnose", { method: "POST", body: "{}" });
      state.metaDiagnostic = result;
      renderMetaDiagnostic();
      if (result.valid) {
        const suffix = result.error ? " Token hợp lệ nhưng chưa đọc đủ Pages: " + result.error : "";
        setMetaNote("Token hợp lệ · " + Number(result.accessible_pages_count || 0) + " Pages có thể truy cập." + suffix, result.error ? "warning" : "");
        showToast("Token Meta hợp lệ.");
      } else {
        setMetaNote(result.error || "Token Meta không hợp lệ.", "error");
      }
      if (!token && state.selectedMetaConnectionId) await loadMetaConnections(state.selectedMetaConnectionId);
    } finally {
      setMetaButtonBusy(button, false);
    }
  }

  async function syncMetaPages() {
    const button = $("#metaSyncButton");
    const draftToken = $("#metaSystemUserToken")?.value.trim() || "";
    let connectionId = state.selectedMetaConnectionId;
    setMetaButtonBusy(button, true, "Đang đồng bộ…");
    try {
      if (!connectionId || draftToken) connectionId = await saveMetaConnection({ silent: true });
      const result = await requestJson("/api/meta/connections/" + encodeURIComponent(connectionId) + "/sync-pages", { method: "POST", body: "{}" });
      const summary = $("#metaSyncSummary");
      if (summary) {
        summary.hidden = false;
        summary.classList.toggle("has-errors", Boolean(result.failed));
        summary.textContent = "Tổng " + Number(result.total || 0) + " · Thêm " + Number(result.imported || 0) + " · Cập nhật " + Number(result.updated || 0) + " · Lỗi " + Number(result.failed || 0) + (result.inaccessible ? " · Mất quyền " + Number(result.inaccessible) : "");
      }
      await loadMetaConnections(connectionId);
      await loadContext(state.selectedBrandId);
      setMetaNote(result.failed ? "Đồng bộ hoàn tất một phần. Kiểm tra chi tiết lỗi và thử lại." : "Đồng bộ thành công. Page Access Token đã được cập nhật trong backend.", result.failed ? "warning" : "");
      showToast(result.failed ? "Đồng bộ Meta hoàn tất một phần." : "Đã đồng bộ toàn bộ Facebook Pages.", Boolean(result.failed && !result.imported && !result.updated));
    } finally {
      setMetaButtonBusy(button, false);
    }
  }

  async function saveMetaPageBrand(button) {
    const pageId = button?.dataset.saveMetaBrand || "";
    const page = state.metaPages.find((item) => String(item.id) === pageId);
    const select = $$('[data-meta-page-brand]').find((item) => item.dataset.metaPageBrand === pageId) || null;
    if (!page || !select) return;
    const currentBrand = String(page.brand || "");
    const nextBrand = select.value;
    if (currentBrand && nextBrand && currentBrand !== nextBrand) {
      select.value = currentBrand;
      throw new Error("Hãy chọn “Chưa gán Brand” và lưu trước khi chuyển Page sang Brand khác.");
    }
    if (!nextBrand && currentBrand) {
      const query = new URLSearchParams({ brand: currentBrand, platform: "facebook", connectionId: pageId });
      await requestJson("/api/social/brand-route?" + query.toString(), { method: "DELETE" });
      showToast("Đã bỏ gán Page khỏi Brand " + currentBrand + ".");
    } else if (nextBrand) {
      await requestJson("/api/social/brand-route", { method: "POST", body: JSON.stringify({ brand: nextBrand, platform: "facebook", connectionId: pageId, name: page.name || pageId }) });
      showToast("Đã gán Page “" + (page.name || pageId) + "” cho Brand " + nextBrand + ".");
    } else {
      return;
    }
    await loadContext(nextBrand || currentBrand);
    await loadMetaPages();
  }

  function renderSummary() {
    const platformIds = Object.keys(PLATFORMS);
    const linked = state.brands.reduce((total, brand) => total + platformIds.filter((key) => routeFor(brand, key)).length, 0);
    const ready = state.brands.reduce((total, brand) => total + platformIds.filter((platform) => isPlatformReady(brand, platform)).length, 0);
    const attention = state.brands.reduce((total, brand) => total + platformIds.filter((platform) => routeFor(brand, platform) && !isPlatformReady(brand, platform)).length, 0);
    const count = state.brands.length;
    $("#brandCount").textContent = count;
    $("#brandCountNote").textContent = count ? count + " Brand trong thư viện" : "Chưa có Brand";
    $("#linkedCount").textContent = linked;
    $("#linkedCountNote").textContent = linked ? "Theo tất cả Brand" : "Chưa có route";
    $("#readyCount").textContent = ready;
    $("#readyCountNote").textContent = ready ? "Kênh đã kiểm tra" : "Cần kết nối thêm";
    $("#attentionCount").textContent = attention;
    $("#attentionCountNote").textContent = attention ? "Có route cần kiểm tra" : "Không có cảnh báo";
  }

  function renderBrandList() {
    const list = $("#brandList");
    if (!list) return;
    const query = state.search.trim().toLocaleLowerCase();
    const brands = state.brands.filter((brand) => !query || (brandName(brand) + " " + brand.id).toLocaleLowerCase().includes(query));
    if (!brands.length) {
      list.innerHTML = '<div class="brand-list-empty">' + (state.brands.length ? "Không tìm thấy Brand phù hợp." : "Chưa có Brand. Tạo Brand đầu tiên để bắt đầu.") + "</div>";
      return;
    }
    list.innerHTML = brands.map((brand) => {
      const routeCount = Object.keys(PLATFORMS).filter((key) => routeFor(brand, key)).length;
      const selected = brand.id === state.selectedBrandId;
      return '<button class="brand-list-item' + (selected ? " is-selected" : "") + '" type="button" role="option" aria-selected="' + selected + '" data-brand-id="' + escapeHtml(brand.id) + '">' +
        '<span class="brand-list-avatar">' + escapeHtml(brandInitial(brand)) + '</span>' +
        '<span class="brand-list-copy"><strong>' + escapeHtml(brandName(brand)) + '</strong><small>' + escapeHtml(brand.id) + " · " + routeCount + '/5 social</small></span>' +
        '<span class="brand-list-count">' + routeCount + "</span></button>";
    }).join("");
  }

  function renderPlatformGrid() {
    const brand = selectedBrand();
    const grid = $("#platformGrid");
    if (!brand || !grid) return;
    grid.innerHTML = Object.entries(PLATFORMS).map(([platform, meta]) => {
      const view = platformView(brand, platform);
      const hasRoute = Boolean(routeFor(brand, platform));
      const canReconnect = platform === "youtube" && hasRoute;
      const reconnectLabel = view.tone === "attention" ? "Kết nối lại" : "Cập nhật";
      const reconnectButton = canReconnect
        ? '<button class="platform-action reconnect" type="button" data-reconnect-platform="youtube" aria-label="' + reconnectLabel + ' YouTube">' + reconnectLabel + "</button>"
        : "";
      const editLabel = platform === "youtube" && hasRoute ? "Đổi" : (hasRoute ? "Sửa" : "Gán");
      return '<article class="platform-card' + (hasRoute ? " has-route" : "") + (view.tone === "attention" ? " needs-attention" : "") + '">' +
        '<span class="platform-mark ' + platform + '" aria-hidden="true">' + escapeHtml(meta.short) + "</span>" +
        '<div class="platform-copy"><strong>' + escapeHtml(meta.label) + "</strong><small>" + escapeHtml(view.detail || meta.description) + "</small>" +
        (view.identity ? "<code>" + escapeHtml(view.identity) + "</code>" : "") +
        '<span class="platform-status ' + view.tone + '">' + escapeHtml(view.label) + "</span></div>" +
        '<div class="platform-actions">' + reconnectButton + '<button class="platform-action" type="button" data-edit-platform="' + platform + '" aria-label="' + editLabel + " " + meta.label + '">' + editLabel + "</button>" +
        (hasRoute ? '<button class="platform-action delete" type="button" data-delete-platform="' + platform + '" aria-label="Gỡ ' + meta.label + '">×</button>' : "") +
        "</div></article>";
    }).join("");
  }

  function renderDetail() {
    const brand = selectedBrand();
    const empty = $("#brandDetailEmpty");
    const detail = $("#brandDetail");
    if (!brand) {
      empty.hidden = false;
      detail.hidden = true;
      return;
    }
    empty.hidden = true;
    detail.hidden = false;
    $("#detailBrandAvatar").textContent = brandInitial(brand);
    $("#detailBrandName").textContent = brandName(brand);
    $("#detailBrandId").textContent = brand.id;
    const projects = Number(brand.project_count || 0);
    $("#detailProjectCount").textContent = projects + " project" + (projects === 1 ? "" : "s");
    $("#detailLinkedCount").textContent = Object.keys(PLATFORMS).filter((key) => routeFor(brand, key)).length + "/5 đã gán";
    renderPlatformGrid();
  }

  function renderAll() {
    renderSummary();
    renderBrandList();
    renderDetail();
  }

  async function loadContext(preferredBrandId = "") {
    try {
      const payload = await requestJson("/api/social/brands");
      state.context = payload;
      state.brands = Array.isArray(payload.brands) ? payload.brands : [];
      const saved = preferredBrandId || state.selectedBrandId || window.localStorage.getItem("aurex-social-brand") || payload.project_brand || "";
      state.selectedBrandId = state.brands.some((brand) => brand.id === saved) ? saved : (state.brands[0]?.id || "");
      if (state.selectedBrandId) window.localStorage.setItem("aurex-social-brand", state.selectedBrandId);
      renderAll();
      renderMetaPages();
      return true;
    } catch (error) {
      state.context = { platforms: {}, brand_routes: {} };
      state.brands = [];
      state.selectedBrandId = "";
      renderAll();
      renderMetaPages();
      showToast(error.message || "Không thể tải dữ liệu Social.", true);
      return false;
    }
  }

  function closeDialogs() {
    $$("dialog[open]").forEach((dialog) => dialog.close());
  }

  function setDialogNote(message = "", tone = "") {
    const note = $("#socialFormNote");
    if (!note) return;
    note.textContent = message;
    note.className = "dialog-note" + (tone ? " is-" + tone : "");
  }

  function field(label, id, options = {}) {
    const type = options.type || "text";
    const required = options.required ? " required" : "";
    const placeholder = options.placeholder ? ' placeholder="' + escapeHtml(options.placeholder) + '"' : "";
    const value = options.value ? ' value="' + escapeHtml(options.value) + '"' : "";
    const optional = options.optional ? " <small>(tuỳ chọn)</small>" : "";
    return '<label class="dialog-field"><span>' + escapeHtml(label) + optional + '</span><input id="' + id + '" type="' + type + '"' + required + placeholder + value + ' autocomplete="off" /></label>';
  }

  function renderSocialFields() {
    const brand = selectedBrand();
    const platform = $("#socialPlatformSelect")?.value || "";
    const route = state.socialDialogMode === "edit" && brand ? routeFor(brand, platform) : null;
    const existingId = routeIdentity(route, platform);
    const meta = PLATFORMS[platform];
    const container = $("#socialFormFields");
    if (!container) return;
    if (!meta) {
      container.innerHTML = "";
      setDialogNote("Brand này đã gán đủ các nền tảng Social.", "warning");
      return;
    }
    const accounts = platformAccounts(platform).filter((account) => platform === "youtube" || platform === "facebook" || !account.brand || account.brand === brand?.id);
    const options = accounts.map((account) => {
      const id = accountIdentity(account, platform);
      if (!id) return "";
      const selected = id === existingId ? " selected" : "";
      return '<option value="' + escapeHtml(id) + '"' + selected + ">" + escapeHtml(accountLabel(account, platform) || id) + " · " + escapeHtml(id) + "</option>";
    }).join("");

    if (platform === "youtube" || platform === "facebook") {
      const label = platform === "youtube" ? "Channel đã kết nối" : "Page đã kết nối";
      container.innerHTML = '<label class="dialog-field"><span>' + label + '</span><select id="socialConnectionId" required><option value="">Chọn account</option>' + options + "</select></label>" +
        field("Tên hiển thị", "socialDisplayName", { optional: true, placeholder: "Tự lấy từ account nếu bỏ trống", value: route?.name || "" }) +
        (platform === "youtube" && !route
          ? '<div class="social-connect-action"><button class="social-secondary-button compact" type="button" data-connect-youtube-new><span aria-hidden="true">+</span> Kết nối YouTube mới</button><small>Mở Google OAuth và tự gán Channel vào Brand này.</small></div>'
          : "");
      const connectionNote = platform === "youtube" && !route
        ? (accounts.length ? "Chọn Channel đã kết nối hoặc kết nối thêm một YouTube Channel mới." : "Chưa có Channel. Bấm “Kết nối YouTube mới” để cấp quyền Google.")
        : (accounts.length ? "Chỉ những account đã kết nối mới xuất hiện trong danh sách." : "Chưa có account. Hãy kết nối " + meta.label + " trong Cài đặt trước.");
      setDialogNote(connectionNote, accounts.length ? "" : "warning");
      return;
    }

    container.innerHTML = '<label class="dialog-field"><span>Account đã lưu <small>(tuỳ chọn)</small></span><select id="socialExistingConnection"><option value="">Tạo connection mới</option>' + options + "</select></label>" +
      field("Connection ID", "socialConnectionId", { optional: true, placeholder: "Tự tạo nếu bỏ trống", value: existingId }) +
      field("Tên hiển thị", "socialDisplayName", { optional: true, placeholder: "Ví dụ: Brand " + meta.label, value: route?.name || "" }) +
      '<div id="socialCredentialFields"></div>';
    const credentialFields = $("#socialCredentialFields");
    if (platform === "instagram") {
      credentialFields.innerHTML = field("Instagram User ID", "socialPublicId", { placeholder: "Ví dụ: 1784..." }) +
        field("Access Token mới", "socialSecret", { type: "password", placeholder: "Dán token khi tạo hoặc cập nhật" }) +
        field("Graph API version", "socialGraphVersion", { optional: true, value: "v26.0" });
      setDialogNote("Nếu chọn account đã lưu và không nhập credential, AurexVideo chỉ gán lại route. R2 dùng chung được giữ trong Cài đặt.");
    } else if (platform === "tiktok") {
      credentialFields.innerHTML = field("Zernio API key mới", "socialSecret", { type: "password", placeholder: "Dán API key khi tạo hoặc cập nhật" }) +
        field("TikTok account ID", "socialPublicId", { placeholder: "Ví dụ: 6a85..." }) +
        field("API base URL", "socialBaseUrl", { optional: true, value: "https://zernio.com/api/v1" });
      setDialogNote("Chọn account đã lưu để gán route nhanh; nhập API key nếu muốn tạo hoặc cập nhật connection.");
    } else {
      credentialFields.innerHTML = field("Threads User ID", "socialPublicId", { placeholder: "Ví dụ: 2799..." }) +
        field("Access Token mới", "socialSecret", { type: "password", placeholder: "Dán token khi tạo hoặc cập nhật" }) +
        field("Graph API version", "socialGraphVersion", { optional: true, value: "v1.0" });
      setDialogNote("Chọn account đã lưu để gán route nhanh; nhập token nếu muốn tạo hoặc cập nhật connection.");
    }
    $("#socialExistingConnection")?.addEventListener("change", (event) => {
      const selected = event.target.value;
      const connectionInput = $("#socialConnectionId");
      if (connectionInput && selected) connectionInput.value = selected;
      const account = findAccount(platform, selected);
      const displayInput = $("#socialDisplayName");
      if (displayInput && selected && !displayInput.value) displayInput.value = accountLabel(account, platform);
      setDialogNote(selected ? "Account đã lưu sẽ được gán vào Brand này khi bạn bấm Lưu liên kết." : "Nhập credential để tạo connection mới cho Brand.", selected ? "" : "warning");
    });
  }

  function populateSocialBrands() {
    const select = $("#socialBrandSelect");
    if (!select) return;
    select.innerHTML = state.brands.map((brand) => '<option value="' + escapeHtml(brand.id) + '">' + escapeHtml(brandName(brand)) + " · " + escapeHtml(brand.id) + "</option>").join("");
    if (state.selectedBrandId) select.value = state.selectedBrandId;
  }

  function syncSocialDialog() {
    const brand = selectedBrand();
    const platformSelect = $("#socialPlatformSelect");
    const saveButton = $("#socialSaveButton");
    const isEdit = state.socialDialogMode === "edit";
    if (!brand || !platformSelect || !saveButton) return;

    Array.from(platformSelect.options).forEach((option) => {
      const hasRoute = Boolean(routeFor(brand, option.value));
      option.disabled = !isEdit && hasRoute;
      option.textContent = PLATFORMS[option.value].label + (!isEdit && hasRoute ? " · Đã gán" : "");
    });
    if (!isEdit && (!platformSelect.value || routeFor(brand, platformSelect.value))) {
      platformSelect.value = firstUnassignedPlatform(brand);
    }

    const platform = platformSelect.value;
    $("#socialDialogTitle").textContent = isEdit && platform ? "Cập nhật " + PLATFORMS[platform].label : "Thêm kênh xuất bản";
    saveButton.textContent = isEdit ? "Lưu thay đổi" : "Thêm social";
    saveButton.disabled = !platform;
    renderSocialFields();
  }

  function openSocialDialog(platform = "") {
    if (!state.brands.length) {
      openBrandDialog();
      showToast("Tạo Brand trước khi thêm Social.");
      return;
    }
    const brand = selectedBrand();
    const requestedPlatform = platform && PLATFORMS[platform] ? platform : "";
    const isEdit = Boolean(requestedPlatform && routeFor(brand, requestedPlatform));
    const initialPlatform = requestedPlatform || firstUnassignedPlatform(brand);
    if (!isEdit && !initialPlatform) {
      showToast("Brand “" + brandName(brand) + "” đã gán đủ 5 nền tảng Social.");
      return;
    }
    state.socialDialogMode = isEdit ? "edit" : "add";
    populateSocialBrands();
    $("#socialBrandSelect").disabled = isEdit;
    $("#socialPlatformSelect").disabled = isEdit;
    $("#socialPlatformSelect").value = initialPlatform;
    syncSocialDialog();
    $("#socialDialog").showModal();
  }

  function openBrandDialog() {
    $("#brandDisplayName").value = "";
    $("#brandId").value = "";
    $("#brandDialog").showModal();
    window.setTimeout(() => $("#brandDisplayName")?.focus(), 0);
  }

  function openDeleteDialog(platform) {
    const brand = selectedBrand();
    const route = brand ? routeFor(brand, platform) : null;
    if (!brand || !route) return;
    state.pendingDelete = { brandId: brand.id, platform, connectionId: routeIdentity(route, platform) };
    $("#deleteSocialCopy").textContent = "Gỡ " + PLATFORMS[platform].label + ' khỏi Brand “' + brandName(brand) + "”?";
    $("#deleteSocialDialog").showModal();
  }

  function openDeleteBrandDialog() {
    const brand = selectedBrand();
    if (!brand) return;
    const routeCount = Object.keys(PLATFORMS).filter((platform) => routeFor(brand, platform)).length;
    const projectCount = Number(brand.project_count || 0);
    state.pendingDeleteBrand = { id: brand.id, name: brandName(brand), routeCount, projectCount };
    $("#deleteBrandCopy").textContent = 'Bạn sắp xoá Brand “' + brandName(brand) + '”.';
    $("#deleteBrandImpact").textContent = projectCount + " project và " + routeCount + " Social route thuộc Brand này sẽ bị xoá. Thao tác không thể hoàn tác.";
    $("#deleteBrandDialog").showModal();
  }

  function readSocialPayload() {
    return {
      brand: $("#socialBrandSelect").value,
      platform: $("#socialPlatformSelect").value,
      displayName: $("#socialDisplayName")?.value.trim() || "",
      connectionId: $("#socialConnectionId")?.value.trim() || "",
      existingId: $("#socialExistingConnection")?.value.trim() || "",
      publicId: $("#socialPublicId")?.value.trim() || "",
      secret: $("#socialSecret")?.value.trim() || "",
      graphVersion: $("#socialGraphVersion")?.value.trim() || "",
      baseUrl: $("#socialBaseUrl")?.value.trim() || "",
    };
  }

  async function openExternalUrl(url) {
    const response = await fetch("/api/open-external", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url }),
    });
    let payload = {};
    try { payload = await response.json(); } catch (_) {}
    if (!response.ok) throw new Error(payload.error || "Không thể mở trình duyệt.");
  }

  async function refreshSocial() {
    const button = $("#socialRefreshButton");
    if (button?.disabled) return;
    if (button) {
      button.disabled = true;
      button.setAttribute("aria-busy", "true");
    }
    try {
      if (await loadContext(state.selectedBrandId)) showToast("Đã cập nhật trạng thái Social.");
    } finally {
      if (button) {
        button.disabled = false;
        button.removeAttribute("aria-busy");
      }
    }
  }

  async function reconnectYoutube() {
    const brand = selectedBrand();
    const route = brand ? routeFor(brand, "youtube") : null;
    const channelId = routeIdentity(route, "youtube");
    const button = $("[data-reconnect-platform=\"youtube\"]");
    if (!brand || !channelId) {
      showToast("Chưa có route YouTube để kết nối lại.", true);
      return;
    }
    if (button?.disabled) return;
    const originalLabel = button?.textContent || "Kết nối lại";
    if (button) {
      button.disabled = true;
      button.textContent = "Đang mở…";
    }
    try {
      const query = new URLSearchParams({ brand: brand.id, channelId });
      const data = await requestJson("/api/social/youtube/reconnect-url?" + query.toString());
      if (!data.url) throw new Error("Không nhận được đường dẫn Google OAuth.");
      await openExternalUrl(data.url);
      showToast("Đã mở Google OAuth. Cấp lại quyền cho đúng kênh, rồi quay lại Social và bấm Cập nhật.");
    } catch (error) {
      showToast(error.message || "Không thể kết nối lại YouTube.", true);
    } finally {
      if (button) {
        button.disabled = false;
        button.textContent = originalLabel;
      }
    }
  }

  async function connectNewYoutube(button) {
    const brandId = $("#socialBrandSelect")?.value || "";
    if (!brandId) throw new Error("Hãy chọn Brand trước khi kết nối YouTube.");
    const originalLabel = button?.textContent || "Kết nối YouTube mới";
    if (button) {
      button.disabled = true;
      button.textContent = "Đang mở Google…";
    }
    try {
      const query = new URLSearchParams({ brand: brandId });
      const data = await requestJson("/api/social/youtube/connect-url?" + query.toString());
      if (!data.url) throw new Error("Không nhận được đường dẫn Google OAuth.");
      await openExternalUrl(data.url);
      closeDialogs();
      showToast("Đã mở Google OAuth. Kết nối xong quay lại Social và bấm Cập nhật.");
    } finally {
      if (button) {
        button.disabled = false;
        button.textContent = originalLabel;
      }
    }
  }

  async function saveSocial() {
    const data = readSocialPayload();
    if (!data.brand || !data.platform) throw new Error("Hãy chọn Brand và nền tảng.");
    const brand = state.brands.find((item) => item.id === data.brand) || null;
    if (state.socialDialogMode === "add" && routeFor(brand, data.platform)) {
      throw new Error(PLATFORMS[data.platform].label + " đã được gán cho Brand này. Hãy dùng nút Sửa để cập nhật.");
    }
    if (data.platform === "youtube" || data.platform === "facebook") {
      if (!data.connectionId) throw new Error("Hãy chọn account cần gán.");
      await requestJson("/api/social/brand-route", { method: "POST", body: JSON.stringify({ brand: data.brand, platform: data.platform, connectionId: data.connectionId, name: data.displayName }) });
    } else if (data.existingId && !data.secret && !data.publicId) {
      await requestJson("/api/social/brand-route", { method: "POST", body: JSON.stringify({ brand: data.brand, platform: data.platform, connectionId: data.existingId, name: data.displayName }) });
    } else {
      if (!data.publicId || !data.secret) throw new Error(PLATFORMS[data.platform].label + " cần đủ ID và credential.");
      const payload = { brand: data.brand, platform: data.platform, connectionId: data.connectionId, displayName: data.displayName, graphVersion: data.graphVersion };
      if (data.platform === "instagram") { payload.igUserId = data.publicId; payload.accessToken = data.secret; payload.apiMode = "instagram_login"; }
      else if (data.platform === "tiktok") { payload.accountId = data.publicId; payload.apiKey = data.secret; payload.baseUrl = data.baseUrl; }
      else { payload.threadsUserId = data.publicId; payload.accessToken = data.secret; }
      await requestJson("/api/social/brand-connection", { method: "POST", body: JSON.stringify(payload) });
    }
    closeDialogs();
    showToast(PLATFORMS[data.platform].label + (state.socialDialogMode === "edit" ? " đã được cập nhật cho " : " đã được thêm cho ") + brandName(brand) + ".");
    await loadContext(data.brand);
  }

  async function createBrand() {
    const displayName = $("#brandDisplayName").value.trim();
    const id = $("#brandId").value.trim();
    if (!displayName) throw new Error("Tên hiển thị Brand không được để trống.");
    const payload = { displayName };
    if (id) payload.id = id;
    const result = await requestJson("/api/brands", { method: "POST", body: JSON.stringify(payload) });
    const created = result.brand || {};
    closeDialogs();
    showToast("Đã tạo Brand " + (created.displayName || created.id || displayName) + ".");
    await loadContext(created.id || id);
  }

  async function removeSocial() {
    const target = state.pendingDelete;
    if (!target) return;
    const query = new URLSearchParams({ brand: target.brandId, platform: target.platform, connectionId: target.connectionId || "" });
    await requestJson("/api/social/brand-route?" + query.toString(), { method: "DELETE" });
    state.pendingDelete = null;
    closeDialogs();
    showToast(PLATFORMS[target.platform].label + " đã được gỡ khỏi Brand.");
    await loadContext(target.brandId);
  }

  async function deleteBrand() {
    const target = state.pendingDeleteBrand;
    if (!target) return;
    const result = await requestJson("/api/brands/" + encodeURIComponent(target.id) + "?cascade=true", { method: "DELETE" });
    const projectCount = Array.isArray(result.projects) ? result.projects.length : target.projectCount;
    const routeCount = Array.isArray(result.social) ? result.social.length : target.routeCount;
    state.pendingDeleteBrand = null;
    state.selectedBrandId = "";
    window.localStorage.removeItem("aurex-social-brand");
    closeDialogs();
    showToast("Đã xoá Brand " + target.name + " cùng " + projectCount + " project và " + routeCount + " Social route.");
    await loadContext();
  }

  function applyTheme(theme) {
    document.body.classList.toggle("theme-light", theme !== "dark");
    const button = $("#socialThemeToggle");
    if (!button) return;
    button.querySelector(".theme-symbol").textContent = theme === "dark" ? "☾" : "☼";
    button.querySelector("span:last-child").textContent = theme === "dark" ? "Dark" : "Light";
  }

  function bindEvents() {
    $("#metaConnectionSelect")?.addEventListener("change", async (event) => {
      const connectionId = event.target.value;
      if (!connectionId) {
        newMetaConnection();
        return;
      }
      state.selectedMetaConnectionId = connectionId;
      state.metaDiagnostic = null;
      window.localStorage.setItem("aurex-meta-connection", connectionId);
      populateMetaConnectionForm();
      renderMetaDiagnostic();
      try { await loadMetaPages(); } catch (error) { setMetaNote(error.message || "Không thể tải Facebook Pages.", "error"); }
    });
    $("#metaNewConnectionButton")?.addEventListener("click", newMetaConnection);
    $("#metaPageSearch")?.addEventListener("input", (event) => { state.metaPageSearch = event.target.value; renderMetaPages(); });
    $("#metaConnectionForm")?.addEventListener("submit", async (event) => {
      event.preventDefault();
      const button = $("#metaSaveButton");
      setMetaButtonBusy(button, true, "Đang lưu…");
      try {
        await saveMetaConnection();
        setMetaNote("Connection đã được lưu. Có thể chẩn đoán hoặc đồng bộ Pages.");
      } catch (error) {
        setMetaNote(error.message || "Không thể lưu Meta Connection.", "error");
        showToast(error.message || "Không thể lưu Meta Connection.", true);
      } finally {
        setMetaButtonBusy(button, false);
      }
    });
    $("#metaDiagnoseButton")?.addEventListener("click", async () => {
      try { await diagnoseMetaToken(); } catch (error) { setMetaNote(error.message || "Không thể chẩn đoán token.", "error"); showToast(error.message || "Không thể chẩn đoán token.", true); }
    });
    $("#metaSyncButton")?.addEventListener("click", async () => {
      try { await syncMetaPages(); } catch (error) { setMetaNote(error.message || "Không thể đồng bộ Pages.", "error"); showToast(error.message || "Không thể đồng bộ Pages.", true); }
    });
    $("#metaPagesList")?.addEventListener("click", async (event) => {
      const button = event.target.closest("[data-save-meta-brand]");
      if (!button || button.disabled) return;
      button.disabled = true;
      try { await saveMetaPageBrand(button); } catch (error) { setMetaNote(error.message || "Không thể lưu Brand mapping.", "error"); showToast(error.message || "Không thể lưu Brand mapping.", true); } finally { button.disabled = false; }
    });
    $("#addSocialButton")?.addEventListener("click", () => openSocialDialog());
    $("#detailAddSocialButton")?.addEventListener("click", () => openSocialDialog());
    $("#addBrandButton")?.addEventListener("click", openBrandDialog);
    $("#sidebarAddBrand")?.addEventListener("click", openBrandDialog);
    $("#emptyAddBrandButton")?.addEventListener("click", openBrandDialog);
    $("#detailDeleteBrandButton")?.addEventListener("click", openDeleteBrandDialog);
    $("#brandSearch")?.addEventListener("input", (event) => { state.search = event.target.value; renderBrandList(); });
    $("#brandList")?.addEventListener("click", (event) => {
      const button = event.target.closest("[data-brand-id]");
      if (!button) return;
      state.selectedBrandId = button.dataset.brandId;
      window.localStorage.setItem("aurex-social-brand", state.selectedBrandId);
      renderAll();
    });
    $("#platformGrid")?.addEventListener("click", (event) => {
      const reconnect = event.target.closest("[data-reconnect-platform]");
      const edit = event.target.closest("[data-edit-platform]");
      const remove = event.target.closest("[data-delete-platform]");
      if (reconnect?.dataset.reconnectPlatform === "youtube") reconnectYoutube();
      if (edit) openSocialDialog(edit.dataset.editPlatform);
      if (remove) openDeleteDialog(remove.dataset.deletePlatform);
    });
    $("#socialBrandSelect")?.addEventListener("change", (event) => {
      state.selectedBrandId = event.target.value;
      window.localStorage.setItem("aurex-social-brand", state.selectedBrandId);
      syncSocialDialog();
    });
    $("#socialPlatformSelect")?.addEventListener("change", syncSocialDialog);
    $("#socialFormFields")?.addEventListener("click", async (event) => {
      const button = event.target.closest("[data-connect-youtube-new]");
      if (!button || button.disabled) return;
      try { await connectNewYoutube(button); } catch (error) { setDialogNote(error.message || "Không thể kết nối YouTube.", "error"); showToast(error.message || "Không thể kết nối YouTube.", true); }
    });
    $("#socialForm")?.addEventListener("submit", async (event) => {
      event.preventDefault();
      const button = $("#socialSaveButton");
      button.disabled = true;
      try { await saveSocial(); } catch (error) { setDialogNote(error.message || "Không thể lưu liên kết.", "error"); showToast(error.message || "Không thể lưu liên kết.", true); } finally { button.disabled = false; }
    });
    $("#brandForm")?.addEventListener("submit", async (event) => {
      event.preventDefault();
      const button = event.currentTarget.querySelector('button[type="submit"]');
      button.disabled = true;
      try { await createBrand(); } catch (error) { showToast(error.message || "Không thể tạo Brand.", true); } finally { button.disabled = false; }
    });
    $("#deleteSocialForm")?.addEventListener("submit", async (event) => {
      event.preventDefault();
      const button = event.currentTarget.querySelector('button[type="submit"]');
      button.disabled = true;
      try { await removeSocial(); } catch (error) { showToast(error.message || "Không thể gỡ liên kết.", true); } finally { button.disabled = false; }
    });
    $("#deleteBrandForm")?.addEventListener("submit", async (event) => {
      event.preventDefault();
      const button = $("#deleteBrandConfirmButton");
      button.disabled = true;
      try { await deleteBrand(); } catch (error) { showToast(error.message || "Không thể xoá Brand.", true); } finally { button.disabled = false; }
    });
    $$("[data-close-dialog]").forEach((button) => button.addEventListener("click", closeDialogs));
    $$("dialog").forEach((dialog) => dialog.addEventListener("click", (event) => { if (event.target === dialog) dialog.close(); }));
    $("#socialRefreshButton")?.addEventListener("click", refreshSocial);
    $("#socialThemeToggle")?.addEventListener("click", () => {
      const next = document.body.classList.contains("theme-light") ? "dark" : "light";
      window.localStorage.setItem("aurexvideo-theme", next);
      applyTheme(next);
    });
  }

  document.addEventListener("DOMContentLoaded", () => {
    applyTheme(window.localStorage.getItem("aurexvideo-theme") || "light");
    bindEvents();
    loadContext();
    loadMetaConnections();
  });
})();
