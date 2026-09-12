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
    pendingDelete: null,
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
      return true;
    } catch (error) {
      state.context = { platforms: {}, brand_routes: {} };
      state.brands = [];
      state.selectedBrandId = "";
      renderAll();
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
    const platform = $("#socialPlatformSelect")?.value || "youtube";
    const route = brand ? routeFor(brand, platform) : null;
    const existingId = routeIdentity(route, platform);
    const meta = PLATFORMS[platform];
    const container = $("#socialFormFields");
    if (!container || !meta) return;
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
        field("Tên hiển thị", "socialDisplayName", { optional: true, placeholder: "Tự lấy từ account nếu bỏ trống", value: route?.name || "" });
      setDialogNote(accounts.length ? "Chỉ những account đã kết nối mới xuất hiện trong danh sách." : "Chưa có account. Hãy kết nối " + meta.label + " trong Cài đặt trước.", accounts.length ? "" : "warning");
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

  function openSocialDialog(platform = "") {
    if (!state.brands.length) {
      openBrandDialog();
      showToast("Tạo Brand trước khi thêm Social.");
      return;
    }
    populateSocialBrands();
    $("#socialPlatformSelect").value = platform && PLATFORMS[platform] ? platform : "youtube";
    $("#socialDialogTitle").textContent = platform && PLATFORMS[platform] ? "Gán " + PLATFORMS[platform].label : "Thêm kênh xuất bản";
    renderSocialFields();
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

  async function saveSocial() {
    const data = readSocialPayload();
    if (!data.brand || !data.platform) throw new Error("Hãy chọn Brand và nền tảng.");
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
    showToast(PLATFORMS[data.platform].label + " đã được lưu cho " + brandName(selectedBrand()) + ".");
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

  function applyTheme(theme) {
    document.body.classList.toggle("theme-light", theme !== "dark");
    const button = $("#socialThemeToggle");
    if (!button) return;
    button.querySelector(".theme-symbol").textContent = theme === "dark" ? "☾" : "☼";
    button.querySelector("span:last-child").textContent = theme === "dark" ? "Dark" : "Light";
  }

  function bindEvents() {
    $("#addSocialButton")?.addEventListener("click", () => openSocialDialog());
    $("#detailAddSocialButton")?.addEventListener("click", () => openSocialDialog());
    $("#addBrandButton")?.addEventListener("click", openBrandDialog);
    $("#sidebarAddBrand")?.addEventListener("click", openBrandDialog);
    $("#emptyAddBrandButton")?.addEventListener("click", openBrandDialog);
    $("#detailAddBrandButton")?.addEventListener("click", openBrandDialog);
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
      renderSocialFields();
    });
    $("#socialPlatformSelect")?.addEventListener("change", (event) => {
      $("#socialDialogTitle").textContent = "Gán " + PLATFORMS[event.target.value].label;
      renderSocialFields();
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
  });
})();
