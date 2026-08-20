(() => {
  const MAX_AUDIO_BYTES = 200 * 1024 * 1024;
  const MAX_BRAND_LOGO_BYTES = 20 * 1024 * 1024;
  const RENDER_PREFERENCES_KEY = 'aurexvideo-render-preferences-v2';
  const LEGACY_RENDER_PREFERENCES_KEY = 'aurexvideo-render-preferences-v1';

  function clearLegacyRenderPreferencesIfDefault() {
    let legacy = null;
    try {
      const raw = localStorage.getItem(LEGACY_RENDER_PREFERENCES_KEY);
      if (raw) legacy = JSON.parse(raw);
    } catch (_) {
      legacy = null;
    }
    if (!legacy || typeof legacy !== 'object') return;
    const hasOldBranding = legacy.branding === true;
    const hasOldSpeed = Number(legacy.speed) === 1.25;
    if (!hasOldBranding && !hasOldSpeed) return;
    try { localStorage.removeItem(RENDER_PREFERENCES_KEY); } catch (_) {}
    try { localStorage.removeItem(LEGACY_RENDER_PREFERENCES_KEY); } catch (_) {}
    const speedInput = $('#renderSpeed');
    if (speedInput) speedInput.value = '1.0';
    if (typeof syncSpeedPresets === 'function') syncSpeedPresets();
  }
  const fallbackProject = window.__RENDER_PROJECT__;
  const fallbackOutputUrl = window.__RENDER_OUTPUT_URL__;
  const projects = Array.isArray(window.__PROJECTS__) && window.__PROJECTS__.length
    ? window.__PROJECTS__
    : (fallbackProject ? [{ name: fallbackProject, url: `/project/${fallbackProject}/`, output_url: fallbackOutputUrl, video_url: fallbackOutputUrl, script_count: 0 }] : []);
  const projectMap = new Map(projects.map((project) => [project.name, project]));
  const MAZIAO_PREVIEW_FALLBACKS = {
    clone_8ci7vkGMoJLyKe9IJ7MfV: 'https://r2-storage.maziao.com/users/CCj33YhtJanC9o8E0j5b/voices/clone_voice_TKAC9TA2IZKeB9iUgHrke.mp3',
    'clone_fY2_e5-7EbOgfEQlggwg0': 'https://r2-storage.maziao.com/users/CCj33YhtJanC9o8E0j5b/voices/clone_voice__kDkJCtGB4qx1fPI8XqgO.mp3',
  };
  const state = {
    engine: 'maziao',
    jobId: null,
    pollTimer: null,
    project: null,
    characterId: '',
    maziaoTopic: null,
    maziaoVoiceCatalog: [],
    maziaoSpeakerValues: {},
    outputUrl: null,
    busy: false,
    canceling: false,
    uploadProject: null,
    uploadBrand: '',
    uploadBrands: [],
    brandRoutes: {},
    socialStatus: {},
    uploadTargets: new Set(),
    socialReady: { youtube: false, facebook: false, instagram: false, threads: false },
    socialConfigured: { youtube: false, facebook: false, instagram: false, threads: false },
    instagramConfig: {},
    threadsConfig: {},
    youtubeRedirectUri: `${window.location.origin}/api/social/youtube/callback`,
    facebookCommentTargetId: '',
    facebookActivePageId: '',
    defaultUploadTags: [],
    defaultUploadCaption: '',
    renderOptions: {
      brandLogoFile: null,
      brandLogoName: '',
      brandName: 'aurexvideo.app',
    },
  };

  let youtubeConfigSaving = false;
  let facebookConfigSaving = false;
  let threadsConfigSaving = false;
  let maziaoPreviewAudio = null;
  let maziaoPreviewButton = null;
  let renameProjectTarget = '';
  let renameProjectTrigger = null;

  function $(selector) {
    return document.querySelector(selector);
  }

  function $all(selector) {
    return Array.from(document.querySelectorAll(selector));
  }

  function updateTrialExportUsage(payload) {
    const used = Number(payload?.trial_exports_used);
    const limit = Number(payload?.trial_export_limit);
    const output = $('#trialExportUsage');
    if (output && Number.isFinite(used) && Number.isFinite(limit)) {
      output.textContent = `${used}/${limit}`;
    }
  }

  async function openExternalUrl(url) {
    const response = await fetch('/api/open-external', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url }),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
  }

  function setButtonLabel(button, label) {
    const labelEl = button?.querySelector?.('span:last-child');
    if (labelEl) labelEl.textContent = label;
    else if (button) button.textContent = label;
  }

  const THEME_ICONS = {
    sun: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="4"></circle><path d="M12 2v2"></path><path d="M12 20v2"></path><path d="m4.93 4.93 1.41 1.41"></path><path d="m17.66 17.66 1.41 1.41"></path><path d="M2 12h2"></path><path d="M20 12h2"></path><path d="m6.34 17.66-1.41 1.41"></path><path d="m19.07 4.93-1.41 1.41"></path></svg>',
    moon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9Z"></path></svg>',
  };

  function applyTheme(theme) {
    const nextTheme = theme === 'light' ? 'light' : 'dark';
    document.body.classList.toggle('theme-light', nextTheme === 'light');
    localStorage.setItem('aurexvideo-theme', nextTheme);
    $all('[data-theme-toggle]').forEach((button) => {
      const icon = button.querySelector('.btn-icon');
      const label = button.querySelector('span:last-child');
      if (icon) icon.innerHTML = nextTheme === 'light' ? THEME_ICONS.sun : THEME_ICONS.moon;
      if (label) label.textContent = nextTheme === 'light' ? 'Light' : 'Dark';
    });
  }

  function toggleTheme() {
    applyTheme(document.body.classList.contains('theme-light') ? 'dark' : 'light');
  }

  function currentProject() {
    return projectMap.get(state.project) || null;
  }

  function videoPageUrl(projectName = state.project) {
    return projectName ? `/watch/${encodeURIComponent(projectName)}` : '#';
  }

  function saveRenderPreferences() {
    const brandingControl = $('#renderBranding');
    const value = {
      branding: brandingControl?.checked === true,
      brandName: state.renderOptions.brandName || 'aurexvideo.app',
    };
    localStorage.setItem(RENDER_PREFERENCES_KEY, JSON.stringify(value));
  }

  function loadRenderPreferences() {
    clearLegacyRenderPreferencesIfDefault();
    try {
      const value = JSON.parse(localStorage.getItem(RENDER_PREFERENCES_KEY) || '{}');
      const brandingControl = $('#renderBranding');
      if (typeof value.branding === 'boolean' && brandingControl) brandingControl.checked = value.branding;
      else if (brandingControl) brandingControl.checked = false;
      if (typeof value.brandName === 'string' && value.brandName.trim()) state.renderOptions.brandName = value.brandName.trim();
    } catch (_) {
      const brandingControl = $('#renderBranding');
      if (brandingControl && !brandingControl.disabled) brandingControl.checked = false;
      localStorage.removeItem(RENDER_PREFERENCES_KEY);
    }
  }

  async function loadBrandingConfig() {
    const response = await fetch('/api/render-branding', { cache: 'no-store' });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
    const brandingControl = $('#renderBranding');
    const brandButton = $('#openBrandConfig');
    if (data.locked) {
      if (brandingControl) {
        brandingControl.checked = true;
        brandingControl.disabled = true;
      }
      if (brandButton) brandButton.disabled = true;
    }
    if (data.configured) {
      state.renderOptions.brandName = String(data.brandName || 'aurexvideo.app').trim() || 'aurexvideo.app';
    }
    state.renderOptions.brandLogoName = data.hasLogo ? String(data.logoName || 'Logo đã lưu') : '';
    saveRenderPreferences();
    const logoName = $('#brandLogoFileName');
    if (logoName) logoName.textContent = state.renderOptions.brandLogoName || 'Logo AurexVideo mặc định';
  }

  function rowForProject(projectName) {
    return $all('.project-row[data-project]').find((element) => element.dataset.project === projectName) || null;
  }

  function projectRenderState(project, jobs) {
    const projectJobs = (Array.isArray(jobs) ? jobs : []).filter((job) => job.project === project?.name);
    const active = projectJobs.find((job) => ['authorizing', 'queued', 'running', 'cancelling'].includes(job.status));
    if (active) return { state: 'rendering', label: tr('Đang render', 'Rendering'), tone: 'warn' };
    if (project?.video_url || projectJobs.some((job) => job.status === 'done')) {
      return { state: 'rendered', label: tr('Đã render', 'Rendered'), tone: 'ok' };
    }
    if (projectJobs.some((job) => job.status === 'failed')) {
      return { state: 'failed', label: tr('Render lỗi', 'Render failed'), tone: 'bad' };
    }
    if (project?.has_script) return { state: 'ready', label: tr('Sẵn sàng', 'Ready'), tone: 'ok' };
    return { state: 'missing-script', label: tr('Thiếu script', 'Missing script'), tone: 'bad' };
  }

  function applyProjectStatus(projectName, status) {
    const row = rowForProject(projectName);
    const badge = row?.querySelector('.status-pill');
    if (!row || !badge) return;
    row.dataset.renderState = status.state;
    badge.textContent = status.label;
    badge.className = `status-pill ${status.tone}`;
  }

  async function refreshProjectStatuses() {
    const response = await fetch('/api/jobs', { cache: 'no-store' });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
    const jobs = Array.isArray(data.jobs) ? data.jobs : [];
    (window.__PROJECTS__ || []).forEach((project) => applyProjectStatus(project.name, projectRenderState(project, jobs)));
  }

  function setStatus(message, tone = 'warn') {
    const status = $('#renderStatus');
    if (!status) return;
    status.textContent = message;
    status.hidden = !message;
    status.className = `status ${tone}`;
  }

  function isTrialUpgradeError(message) {
    return /(?:đã dùng hết|trial).*(?:lượt xuất|export)|nâng cấp pro|upgrade pro/i.test(String(message || ''));
  }

  function isVietnameseUi() {
    return (window.__AUREX_LANGUAGE__ || document.documentElement.lang) !== 'en';
  }

  function tr(vi, en) {
    return isVietnameseUi() ? vi : en;
  }

  function trialLimitMessage() {
    return tr(
      'Bạn đã dùng hết 3 lượt xuất video Trial. Chọn gói tháng hoặc gói năm để tiếp tục.',
      'You have used all 3 Trial video exports. Choose a monthly or yearly plan to continue.',
    );
  }

  function showTrialUpgradePrompt(message) {
    document.querySelector('#trialUpgradePrompt')?.remove();
    const isVietnamese = isVietnameseUi();
    const copy = isVietnamese
      ? {
          title: 'Bạn đã dùng hết 3 lượt xuất Trial',
          body: trialLimitMessage(),
          monthlyTitle: 'Pro theo tháng',
          monthlyPrice: '99.000đ',
          monthlyTerm: '/ 30 ngày',
          monthlyCopy: 'Thanh toán một lần. Khi hết hạn, bạn chủ động gia hạn ngay trong AurexVideo.',
          monthlyCta: 'Chọn gói tháng',
          yearlyBadge: 'Tiết kiệm 58%',
          yearlyTitle: 'Pro theo năm',
          yearlyPrice: '499.000đ',
          yearlyTerm: '/ 12 tháng',
          yearlyCopy: 'Phù hợp khi dùng lâu dài, tương đương khoảng 41.600đ mỗi tháng.',
          yearlyCta: 'Chọn gói năm',
          later: 'Để sau',
          opening: 'Đang mở trang thanh toán…',
          opened: 'Đã mở trang thanh toán. AurexVideo sẽ tự cập nhật sau khi bạn thanh toán.',
          activated: 'Aurex Pro đã được kích hoạt. Đang tải lại ứng dụng…',
        }
      : {
          title: 'Your 3 Trial exports are used',
          body: trialLimitMessage(),
          monthlyTitle: 'Monthly Pro',
          monthlyPrice: '$5',
          monthlyTerm: '/ 30 days',
          monthlyCopy: 'One-time payment. Renew in AurexVideo whenever the plan expires.',
          monthlyCta: 'Choose monthly',
          yearlyBadge: 'Save 67%',
          yearlyTitle: 'Yearly Pro',
          yearlyPrice: '$20',
          yearlyTerm: '/ 12 months',
          yearlyCopy: 'Best for long-term use, equivalent to about $1.67 per month.',
          yearlyCta: 'Choose yearly',
          later: 'Maybe later',
          opening: 'Opening checkout…',
          opened: 'Checkout opened. Aurex will update automatically after you pay.',
          activated: 'Aurex Pro is active. Reloading the app…',
        };
    const backdrop = document.createElement('div');
    backdrop.id = 'trialUpgradePrompt';
    backdrop.setAttribute('role', 'presentation');
    backdrop.style.cssText = 'position:fixed;inset:0;z-index:10000;display:grid;place-items:center;padding:24px;background:rgba(10,12,10,.76);backdrop-filter:blur(6px)';
    const dialog = document.createElement('section');
    dialog.setAttribute('role', 'dialog');
    dialog.setAttribute('aria-modal', 'true');
    dialog.style.cssText = 'width:min(720px,100%);padding:34px;border:2px solid #171914;border-radius:22px;background:#fffaf1;color:#171914;box-shadow:10px 10px 0 #ff4b2b';
    dialog.innerHTML = `
      <p style="margin:0;color:#d93b20;font-weight:850;letter-spacing:.08em;text-transform:uppercase">Aurex Pro</p>
      <h2 style="margin:12px 0 0;font-size:30px;line-height:1.1">${copy.title}</h2>
      <p style="margin:14px 0 0;color:#5f5b52;font-size:15px;line-height:1.55">${copy.body}</p>
      <div style="display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px;margin-top:22px">
        <article style="display:grid;gap:12px;min-height:230px;border:1px solid #d9d0c2;border-radius:18px;padding:20px;background:#fff">
          <h3 style="margin:0;font-size:20px">${copy.monthlyTitle}</h3>
          <div style="display:flex;align-items:baseline;gap:7px"><strong style="font-size:34px;letter-spacing:-.04em">${copy.monthlyPrice}</strong><span style="color:#5f5b52;font-size:12px;font-weight:750">${copy.monthlyTerm}</span></div>
          <p style="margin:0;color:#5f5b52;font-size:12px;font-weight:750;line-height:1.5">${copy.monthlyCopy}</p>
          <button data-checkout-plan="monthly" type="button" style="width:100%;min-height:48px;margin-top:auto;border:0;border-radius:14px;background:#f2b261;color:#1c1209;font-size:14px;font-weight:900;cursor:pointer">${copy.monthlyCta}</button>
        </article>
        <article style="display:grid;gap:12px;min-height:230px;border:1px solid rgba(155,255,63,.55);border-radius:18px;padding:20px;background:linear-gradient(145deg,rgba(155,255,63,.12),#fff)">
          <span style="width:fit-content;border-radius:999px;padding:6px 10px;color:#16200e;background:#9bff3f;font-size:10px;font-weight:950;text-transform:uppercase">${copy.yearlyBadge}</span>
          <h3 style="margin:0;font-size:20px">${copy.yearlyTitle}</h3>
          <div style="display:flex;align-items:baseline;gap:7px"><strong style="font-size:34px;letter-spacing:-.04em">${copy.yearlyPrice}</strong><span style="color:#5f5b52;font-size:12px;font-weight:750">${copy.yearlyTerm}</span></div>
          <p style="margin:0;color:#5f5b52;font-size:12px;font-weight:750;line-height:1.5">${copy.yearlyCopy}</p>
          <button data-checkout-plan="yearly" type="button" style="width:100%;min-height:48px;margin-top:auto;border:0;border-radius:14px;background:#9bff3f;color:#1c1209;font-size:14px;font-weight:900;cursor:pointer">${copy.yearlyCta}</button>
        </article>
      </div>
      <div style="display:flex;justify-content:flex-end;margin-top:18px">
        <button data-upgrade-later type="button" style="min-height:44px;padding:0 18px;border:1px solid #aaa398;border-radius:12px;background:transparent;color:#171914;font-size:14px;font-weight:700;cursor:pointer">${copy.later}</button>
      </div>
      <p data-upgrade-status style="min-height:22px;margin:12px 0 0;color:#a83320;font-size:13px;font-weight:800;text-align:center"></p>`;
    backdrop.appendChild(dialog);
    document.body.appendChild(backdrop);
    const handleLicenseUpdated = (event) => {
      if (event?.detail?.plan !== 'pro') return;
      const status = dialog.querySelector('[data-upgrade-status]');
      if (status) status.textContent = copy.activated;
      window.setTimeout(() => window.location.reload(), 900);
    };
    window.addEventListener('aurexvideo-license-updated', handleLicenseUpdated);
    const close = () => {
      window.removeEventListener('aurexvideo-license-updated', handleLicenseUpdated);
      backdrop.remove();
    };
    dialog.querySelector('[data-upgrade-later]')?.addEventListener('click', close);
    backdrop.addEventListener('mousedown', (event) => { if (event.target === backdrop) close(); });
    const startCheckout = async (plan, button) => {
      const status = dialog.querySelector('[data-upgrade-status]');
      dialog.querySelectorAll('[data-checkout-plan]').forEach((item) => { item.disabled = true; });
      button.disabled = true;
      if (status) status.textContent = copy.opening;
      try {
        const response = await fetch('/api/license/checkout', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ locale: isVietnamese ? 'vi' : 'en', plan }),
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
        if (status) status.textContent = copy.opened;
      } catch (error) {
        dialog.querySelectorAll('[data-checkout-plan]').forEach((item) => { item.disabled = false; });
        if (status) status.textContent = error.message || String(error);
      }
    };
    dialog.querySelectorAll('[data-checkout-plan]').forEach((button) => {
      button.addEventListener('click', () => {
        const plan = button.dataset.checkoutPlan === 'monthly' ? 'monthly' : 'yearly';
        startCheckout(plan, button);
      });
    });
  }

  function reloadWithSourceProjects(data) {
    const nextProjects = Array.isArray(data?.projects) ? data.projects : [];
    const url = new URL(window.location.href);
    const firstProject = nextProjects[0]?.name || '';
    if (firstProject) url.searchParams.set('project', firstProject);
    else url.searchParams.delete('project');
    window.location.href = `${url.pathname}${url.search}`;
  }

  function setSourceSelectStatus(message, tone = 'warn') {
    if ($('#renderStatus')) {
      setStatus(message, tone);
    } else if ($('#uploadStatus')) {
      setUploadStatus(message, tone);
    }
  }

  async function selectSourceRootFromFinder() {
    const buttons = $all('[data-source-root-select]');
    buttons.forEach((button) => { button.disabled = true; });
    setSourceSelectStatus('Đang mở Finder để chọn source folder...', 'warn');
    try {
      const response = await fetch('/api/source-root/select', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({}),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
      const count = Array.isArray(data.projects) ? data.projects.length : 0;
      setSourceSelectStatus(`Đã đổi source. Tìm thấy ${count} project.`, 'good');
      window.setTimeout(() => reloadWithSourceProjects(data), 250);
    } catch (error) {
      setSourceSelectStatus(error.message || String(error), 'bad');
    } finally {
      buttons.forEach((button) => { button.disabled = false; });
    }
  }

  function setRevealOutputButton(projectName = state.project, visible = false) {
    const button = $('#revealOutput');
    if (!button) return;
    button.dataset.project = projectName || '';
    button.hidden = !visible;
    button.disabled = false;
  }

  function setUploadCenterLink(projectName = state.project, visible = false) {
    const link = $('#uploadCenterLink');
    if (!link) return;
    link.href = projectName ? `/upload?project=${encodeURIComponent(projectName)}` : '/upload';
    link.hidden = !visible;
  }

  function setUploadEmpty(projectName = state.project, visible = false) {
    const empty = $('#uploadEmpty');
    if (!empty) return;
    const title = $('#uploadEmptyProject');
    if (title) title.textContent = projectName ? `${projectName} chưa có final_video.mp4` : 'Project này chưa có final_video.mp4';
    empty.hidden = !visible;
  }

  function setProjectVideoBadge(project) {
    const badge = $('#projectVideoBadge');
    if (!badge) return;
    const ready = Boolean(project?.video_url);
    badge.textContent = ready
      ? tr('có final_video', 'has final_video')
      : tr('chưa render', 'not rendered');
    badge.className = `project-video-badge ${ready ? 'ready' : 'missing'}`;
  }

  function setUploadStatus(message = '', tone = 'warn') {
    const status = $('#uploadStatus');
    if (!status) return;
    status.textContent = message;
    status.hidden = !message;
    status.className = `upload-status ${tone}`;
  }

  function setUploadResult(message = '', tone = 'warn', links = []) {
    const result = $('#uploadResult');
    if (!result) return;
    result.textContent = '';
    result.hidden = !message && !links.length;
    result.className = `upload-result ${tone}`;
    if (message) {
      const text = document.createElement('span');
      text.textContent = message;
      result.appendChild(text);
    }
    links.forEach((link) => {
      result.appendChild(document.createTextNode(' '));
      const anchor = document.createElement('a');
      anchor.href = link.href;
      anchor.target = '_blank';
      anchor.rel = 'noreferrer';
      anchor.textContent = link.label;
      result.appendChild(anchor);
    });
  }

  function waitMs(ms) {
    return new Promise((resolve) => window.setTimeout(resolve, Math.max(0, Number(ms) || 0)));
  }

  async function commentFacebookSourceWithDelay(project, facebookUploadedAt) {
    const firstDelay = 30000;
    const retryDelay = 45000;
    const elapsed = Date.now() - Number(facebookUploadedAt || 0);
    const waitBeforeFirstTry = Math.max(0, firstDelay - elapsed);
    if (waitBeforeFirstTry > 0) {
      setUploadStatus(`YouTube xong. Chờ ${Math.ceil(waitBeforeFirstTry / 1000)} giây để Facebook tạo object rồi comment nguồn...`, 'warn');
      await waitMs(waitBeforeFirstTry);
    }
    try {
      setUploadStatus('Đang comment nguồn lên Facebook...', 'warn');
      return await commentFacebookSource(project);
    } catch (firstError) {
      setUploadStatus(`Comment nguồn chưa được. Chờ ${Math.ceil(retryDelay / 1000)} giây rồi thử lại...`, 'warn');
      await waitMs(retryDelay);
      try {
        setUploadStatus('Đang thử comment nguồn lên Facebook lần 2...', 'warn');
        return await commentFacebookSource(project);
      } catch (secondError) {
        secondError.message = `${secondError.message || String(secondError)}. Lần thử đầu cũng lỗi: ${firstError.message || String(firstError)}`;
        throw secondError;
      }
    }
  }

  function flashCopyButton(button) {
    if (!button) return;
    const icon = button.querySelector('.btn-icon');
    if (!icon) return;
    if (!button.dataset.defaultIcon) button.dataset.defaultIcon = icon.textContent || '⧉';
    if (!button.dataset.defaultTitle) button.dataset.defaultTitle = button.getAttribute('title') || 'Copy';
    if (!button.dataset.defaultAriaLabel) button.dataset.defaultAriaLabel = button.getAttribute('aria-label') || button.dataset.defaultTitle;
    icon.textContent = '✓';
    button.setAttribute('title', 'Đã copy');
    button.setAttribute('aria-label', 'Đã copy');
    if (button._copyResetTimer) window.clearTimeout(button._copyResetTimer);
    button._copyResetTimer = window.setTimeout(() => {
      icon.textContent = button.dataset.defaultIcon || '⧉';
      button.setAttribute('title', button.dataset.defaultTitle || 'Copy');
      button.setAttribute('aria-label', button.dataset.defaultAriaLabel || button.dataset.defaultTitle || 'Copy');
    }, 1400);
  }

  function fallbackCopyText(text) {
    const helper = document.createElement('textarea');
    helper.value = text;
    helper.setAttribute('readonly', '');
    helper.style.position = 'fixed';
    helper.style.opacity = '0';
    helper.style.pointerEvents = 'none';
    document.body.appendChild(helper);
    helper.select();
    helper.setSelectionRange(0, helper.value.length);
    const ok = document.execCommand('copy');
    document.body.removeChild(helper);
    if (!ok) throw new Error('Clipboard copy failed.');
  }

  async function copyText(text) {
    if (navigator.clipboard?.writeText) await navigator.clipboard.writeText(text);
    else fallbackCopyText(text);
  }

  async function copyFieldValue(fieldId, label, button) {
    const field = document.getElementById(fieldId);
    const value = String(field?.value || '');
    if (!value.trim()) {
      setUploadStatus(`${label} đang trống.`, 'warn');
      return;
    }
    try {
      await copyText(value);
      flashCopyButton(button);
    } catch (error) {
      setUploadStatus(`Không copy được ${label}.`, 'bad');
    }
  }

  function hideUploadPanel() {
    const panel = $('#uploadPanel');
    if (panel) panel.hidden = true;
    state.uploadProject = null;
    state.facebookCommentTargetId = '';
    setUploadStatus('');
    setUploadResult('');
    updateFacebookCommentButton();
  }

  async function loadUploadMetadata(projectName) {
    const title = $('#uploadTitle');
    const youtubeDescription = $('#youtubeDescription');
    const facebookCaption = $('#facebookCaption');
    const instagramCaption = $('#instagramCaption');
    const tiktokCaption = $('#tiktokCaption');
    const binanceCaption = $('#binanceCaption');
    const facebookSourceComment = $('#facebookSourceComment');
    const privacy = $('#youtubePrivacy');
    const facebookVideoState = $('#facebookVideoState');
    if (!title || !youtubeDescription || !facebookCaption) return;
    const ytScheduleToggle = $('#youtubeScheduleToggle');
    const ytScheduleRow = $('#youtubeScheduleRow');
    const fbScheduleToggle = $('#facebookScheduleToggle');
    const fbScheduleRow = $('#facebookScheduleRow');
    const ttScheduleToggle = $('#tiktokScheduleToggle');
    const ttScheduleRow = $('#tiktokScheduleRow');
    if (ytScheduleToggle) ytScheduleToggle.checked = false;
    if (ytScheduleRow) ytScheduleRow.hidden = true;
    if (fbScheduleToggle) fbScheduleToggle.checked = false;
    if (fbScheduleRow) fbScheduleRow.hidden = true;
    if (ttScheduleToggle) ttScheduleToggle.checked = false;
    if (ttScheduleRow) ttScheduleRow.hidden = true;
    if (privacy) privacy.disabled = false;
    try {
      const response = await fetch(`/api/social/upload-metadata?project=${encodeURIComponent(projectName)}`, { cache: 'no-store' });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
      title.value = data.title || '';
      youtubeDescription.value = data.youtubeDescription || data.description || '';
      facebookCaption.value = data.facebookCaption || '';
      if (instagramCaption) instagramCaption.value = data.instagramCaption || data.facebookCaption || '';
      if (tiktokCaption) tiktokCaption.value = data.tiktokCaption || data.instagramCaption || data.facebookCaption || '';
       if (binanceCaption) binanceCaption.value = data.binanceCaption || '';
      if (facebookSourceComment) facebookSourceComment.value = data.facebookSourceComment || '';
      if (privacy && data.privacyStatus) privacy.value = data.privacyStatus;
      if (facebookVideoState && data.facebookVideoState) facebookVideoState.value = data.facebookVideoState === 'PUBLISHED' ? 'PUBLISHED' : 'DRAFT';
      // Auto-fill Binance duration from the actual rendered video when available.
      const binanceDuration = $('#binanceDuration');
      if (binanceDuration && !binanceDuration.dataset.touched) {
        const backendDur = Number(data.durationSeconds || 0);
        const videoUrl = state.outputUrl || projectMap.get(projectName)?.video_url || data.video_url || `/project/${encodeURIComponent(projectName)}/output/final_video.mp4`;
        if (backendDur > 0) {
          binanceDuration.value = backendDur.toFixed(1);
        } else if (videoUrl) {
          const probe = document.createElement('video');
          probe.preload = 'metadata';
          probe.onloadedmetadata = () => {
            if (!binanceDuration.dataset.touched && probe.duration > 0) {
              binanceDuration.value = probe.duration.toFixed(1);
            }
            probe.remove();
          };
          probe.onerror = () => probe.remove();
          probe.src = videoUrl;
        }
      }
      state.defaultUploadTags = Array.isArray(data.tags) ? data.tags.map((tag) => String(tag || '').trim()).filter(Boolean) : [];
      state.defaultUploadCaption = String(data.defaultUploadCopy?.caption || data.facebookCaption || data.youtubeDescription || '');
    } catch (error) {
      setUploadStatus(error.message || String(error), 'bad');
    } finally {
      updateUploadBothButton();
      updateFacebookCommentButton();
    }
  }

  function updateFacebookCommentButton() {
    const button = $('#commentFacebookSource');
    if (!button) return;
    const hasComment = Boolean(($('#facebookSourceComment')?.value || '').trim());
    button.disabled = !(state.socialReady.facebook && state.facebookCommentTargetId && hasComment);
  }

  function updateUploadBothButton() {
    const uploadBothPublic = $('#uploadBothPublic');
    if (!uploadBothPublic) return;
    const isPublic = !scheduleActive() && $('#youtubePrivacy')?.value === 'public' && $('#facebookVideoState')?.value === 'PUBLISHED';
    uploadBothPublic.disabled = !(isPublic && state.socialReady.youtube && state.socialReady.facebook);
    setButtonLabel(uploadBothPublic, isPublic ? 'Upload Facebook + YouTube + comment nguồn' : 'Chọn Public + Publish trước');
  }

  function updateMetaAllButton() {
    const button = $('#uploadMetaAll');
    if (!button) return;
    const scheduled = scheduleActive();
    const facebookPublished = !scheduled && $('#facebookVideoState')?.value === 'PUBLISHED';
    const ready = state.socialReady.instagram && state.socialReady.facebook && state.socialReady.threads;
    button.disabled = !(ready && facebookPublished);
    if (scheduled) setButtonLabel(button, 'Tắt hẹn giờ đăng trước');
    else if (!ready) setButtonLabel(button, 'Cấu hình đủ Instagram + Facebook + Threads');
    else if (!facebookPublished) setButtonLabel(button, 'Chọn Facebook Publish now trước');
    else setButtonLabel(button, 'Đăng Instagram + Facebook + Threads');
  }

  function setAccountAvatar(avatar, thumbnail) {
    if (!avatar) return;
    avatar.hidden = !thumbnail;
    avatar.onerror = () => {
      avatar.hidden = true;
      avatar.removeAttribute('src');
    };
    if (thumbnail) avatar.src = thumbnail;
    else avatar.removeAttribute('src');
  }

  function appendAccountContent(button, account, withChevron = false) {
    const accountId = String(account.id || '');
    const avatar = document.createElement('img');
    avatar.alt = '';
    setAccountAvatar(avatar, String(account.thumbnail || ''));

    const body = document.createElement('div');
    const name = document.createElement('strong');
    const id = document.createElement('span');
    name.textContent = String(account.title || account.name || accountId);
    id.textContent = `ID: ${accountId}`;
    body.append(name, id);
    button.append(avatar, body);
    if (withChevron) {
      const chevron = document.createElement('span');
      chevron.className = 'account-chevron';
      chevron.textContent = '⌄';
      button.appendChild(chevron);
    }
  }

  function closePlatformAccountMenus(exceptPrefix = '') {
    $all('.platform-account-list.open').forEach((list) => {
      if (exceptPrefix && list.id === `${exceptPrefix}AccountList`) return;
      list.classList.remove('open');
      const trigger = list.querySelector('.platform-account-trigger');
      if (trigger) trigger.setAttribute('aria-expanded', 'false');
    });
  }

  function renderPlatformAccounts(prefix, accounts = [], activeId = '') {
    const list = $(`#${prefix}AccountList`);
    if (!list) return;
    const normalized = accounts.filter((account) => account?.id);
    list.textContent = '';
    list.classList.remove('open');
    list.hidden = !normalized.length;
    if (!normalized.length) return;

    const activeAccount = normalized.find((account) => account.id === activeId || account.active) || normalized[0];
    const activeAccountId = String(activeAccount.id || '');
    const trigger = document.createElement('button');
    trigger.type = 'button';
    trigger.className = 'platform-account platform-account-trigger active';
    trigger.dataset.accountId = activeAccountId;
    trigger.setAttribute('aria-haspopup', 'listbox');
    trigger.setAttribute('aria-expanded', 'false');
    trigger.setAttribute('title', normalized.length > 1 ? 'Bấm để chọn account khác' : 'Account đang chọn');
    appendAccountContent(trigger, activeAccount, normalized.length > 1);
    trigger.addEventListener('click', () => {
      if (normalized.length <= 1) return;
      const isOpen = list.classList.contains('open');
      closePlatformAccountMenus(prefix);
      list.classList.toggle('open', !isOpen);
      trigger.setAttribute('aria-expanded', !isOpen ? 'true' : 'false');
    });
    list.appendChild(trigger);

    if (normalized.length <= 1) return;

    const menu = document.createElement('div');
    menu.className = 'platform-account-options';
    menu.setAttribute('role', 'listbox');
    normalized.forEach((account) => {
      const accountId = String(account.id || '');
      const active = accountId === activeAccountId;
      const option = document.createElement('button');
      option.type = 'button';
      option.className = `platform-account platform-account-option ${active ? 'active' : ''}`;
      option.dataset.accountId = accountId;
      option.setAttribute('role', 'option');
      option.setAttribute('aria-selected', active ? 'true' : 'false');
      option.setAttribute('title', active ? 'Đang chọn' : 'Bấm để chọn account này');
      appendAccountContent(option, account);
      option.addEventListener('click', () => {
        closePlatformAccountMenus();
        if (!accountId || active) return;
        if (prefix === 'youtube') setYoutubeActiveChannel(accountId);
        else setFacebookActivePage(accountId);
      });
      const remove = document.createElement('button');
      remove.type = 'button';
      remove.className = 'platform-account-remove';
      remove.textContent = '×';
      remove.setAttribute('title', 'Gỡ tài khoản này');
      remove.addEventListener('click', (event) => {
        event.stopPropagation();
        closePlatformAccountMenus();
        disconnectSocialAccount(prefix, accountId, account.name || account.title || accountId);
      });
      const row = document.createElement('div');
      row.className = 'platform-account-row';
      row.appendChild(option);
      row.appendChild(remove);
      menu.appendChild(row);
    });
    list.appendChild(menu);
  }

  async function disconnectSocialAccount(prefix, accountId, label) {
    if (!accountId) return;
    const platform = prefix === 'youtube' ? 'YouTube Channel' : 'Facebook Page';
    if (!window.confirm(`Gỡ ${platform} “${label}” khỏi AurexVideo?`)) return;
    setAccountListDisabled(prefix, true);
    setUploadStatus(`Đang gỡ ${platform}...`, 'warn');
    try {
      const body = prefix === 'youtube' ? { channelId: accountId } : { pageId: accountId };
      const response = await fetch(`/api/social/${prefix}/disconnect`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
      setUploadStatus(`Đã gỡ ${platform}.`, 'good');
      await refreshSocialStatus();
    } catch (error) {
      setUploadStatus(error.message || String(error), 'bad');
    } finally {
      setAccountListDisabled(prefix, false);
    }
  }

  function setAccountListDisabled(prefix, disabled) {
    $all(`#${prefix}AccountList button`).forEach((button) => {
      button.disabled = disabled;
    });
  }

  function accountListFallback(account = {}) {
    const accountId = String(account?.id || '');
    return accountId ? [account] : [];
  }

  function syncYoutubeChannels(youtube = {}) {
    const fallbackChannel = accountListFallback(youtube.channel);
    const channels = Array.isArray(youtube.channels) && youtube.channels.length ? youtube.channels : fallbackChannel;
    const activeChannelId = String(youtube.active_channel_id || youtube.channel?.id || channels.find((channel) => channel.active)?.id || '');
    renderPlatformAccounts('youtube', channels, activeChannelId);
  }

  function syncFacebookPages(facebook = {}) {
    const pages = Array.isArray(facebook.pages) ? facebook.pages : [];
    const activePageId = String(facebook.active_page_id || facebook.page_id || facebook.page?.id || pages.find((page) => page.active)?.id || '');
    renderPlatformAccounts('facebook', pages.length ? pages : accountListFallback(facebook.page), activePageId);
  }

  function syncYoutubeConfigUi(youtube = {}) {
    const clientIdInput = $('#youtubeClientId');
    const clientSecretInput = $('#youtubeClientSecret');
    const redirectInput = $('#youtubeRedirectUri');
    const modalOpen = Boolean($('#youtubeConfigModal') && !$('#youtubeConfigModal').hidden);
    const currentRedirectUri = `${window.location.origin}/api/social/youtube/callback`;
    state.youtubeRedirectUri = youtube.configured
      ? String(youtube.redirect_uri || currentRedirectUri)
      : currentRedirectUri;
    if (redirectInput && document.activeElement !== redirectInput) redirectInput.value = state.youtubeRedirectUri;
    if (clientIdInput && !modalOpen && document.activeElement !== clientIdInput) clientIdInput.value = '';
    if (clientSecretInput && !modalOpen && document.activeElement !== clientSecretInput) clientSecretInput.value = '';
    if (clientIdInput) clientIdInput.placeholder = youtube.configured ? 'Đã lưu Client ID, dán ID mới nếu muốn đổi' : 'Dán OAuth Client ID';
    if (clientSecretInput) clientSecretInput.placeholder = youtube.configured ? 'Đã lưu Client Secret, dán secret mới nếu muốn đổi' : 'Dán Client Secret';
  }

  function openYoutubeConfigModal() {
    const modal = $('#youtubeConfigModal');
    const clientIdInput = $('#youtubeClientId');
    const clientSecretInput = $('#youtubeClientSecret');
    const redirectInput = $('#youtubeRedirectUri');
    if (!modal) return;
    modal.hidden = false;
    if (clientIdInput) clientIdInput.value = '';
    if (clientSecretInput) clientSecretInput.value = '';
    if (redirectInput) redirectInput.value = state.youtubeRedirectUri;
    if (clientIdInput) clientIdInput.focus();
  }

  function closeYoutubeConfigModal() {
    const modal = $('#youtubeConfigModal');
    const clientIdInput = $('#youtubeClientId');
    const clientSecretInput = $('#youtubeClientSecret');
    if (clientIdInput) clientIdInput.value = '';
    if (clientSecretInput) clientSecretInput.value = '';
    if (modal) modal.hidden = true;
  }

  function openDefaultTagsModal() {
    const modal = $('#defaultTagsModal');
    const input = $('#defaultCaptionInput');
    if (!modal) return;
    modal.hidden = false;
    if (input) {
      input.value = state.defaultUploadCaption;
      input.focus();
    }
  }

  function closeDefaultTagsModal() {
    const modal = $('#defaultTagsModal');
    if (modal) modal.hidden = true;
  }

  async function saveDefaultTags() {
    const button = $('#saveDefaultTags');
    const input = $('#defaultCaptionInput');
    if (!input) return;
    if (button) button.disabled = true;
    try {
      const response = await fetch('/api/social/default-tags', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ caption: input.value }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
      state.defaultUploadTags = Array.isArray(data.tags) ? data.tags : [];
      state.defaultUploadCaption = String(data.caption || '');
      const title = $('#uploadTitle');
      const youtubeDescription = $('#youtubeDescription');
      const facebookCaption = $('#facebookCaption');
      const instagramCaption = $('#instagramCaption');
      if (title) title.value = data.title || '';
      if (youtubeDescription) youtubeDescription.value = data.youtubeDescription || state.defaultUploadCaption;
      if (facebookCaption) facebookCaption.value = data.facebookCaption || state.defaultUploadCaption;
      if (instagramCaption) instagramCaption.value = data.instagramCaption || data.facebookCaption || state.defaultUploadCaption;
      closeDefaultTagsModal();
      setUploadStatus('Đã lưu caption mặc định.', 'good');
    } catch (error) {
      setUploadStatus(error.message || String(error), 'bad');
    } finally {
      if (button) button.disabled = false;
    }
  }

  function syncFacebookConfigUi(facebook = {}) {
    const pageIdInput = $('#facebookPageId');
    const tokenInput = $('#facebookPageAccessToken');
    const configState = $('#facebookConfigState');
    const activePageId = String(facebook.active_page_id || facebook.page_id || facebook.page?.id || '').trim();
    const modalOpen = Boolean($('#facebookConfigModal') && !$('#facebookConfigModal').hidden);
    state.facebookActivePageId = activePageId;
    if (pageIdInput && document.activeElement !== pageIdInput && activePageId) {
      pageIdInput.value = activePageId;
    }
    if (tokenInput) {
      if (!modalOpen && document.activeElement !== tokenInput) tokenInput.value = '';
      tokenInput.placeholder = facebook.configured
        ? 'Đã lưu token, dán token mới nếu muốn đổi'
        : 'Dán Page access token';
    }
    if (configState) {
      configState.textContent = '';
      configState.hidden = true;
    }
  }

  function openFacebookConfigModal() {
    const modal = $('#facebookConfigModal');
    const pageIdInput = $('#facebookPageId');
    const tokenInput = $('#facebookPageAccessToken');
    if (!modal) return;
    modal.hidden = false;
    if (pageIdInput && !pageIdInput.value.trim() && state.facebookActivePageId) {
      pageIdInput.value = state.facebookActivePageId;
    }
    if (tokenInput) {
      tokenInput.value = '';
      tokenInput.focus();
    } else if (pageIdInput) {
      pageIdInput.focus();
    }
  }

  function closeFacebookConfigModal() {
    const modal = $('#facebookConfigModal');
    const tokenInput = $('#facebookPageAccessToken');
    if (tokenInput) tokenInput.value = '';
    if (modal) modal.hidden = true;
  }

  function syncInstagramConfigUi(instagram = {}) {
    const igUserIdInput = $('#instagramIgUserId');
    const apiModeInput = $('#instagramApiMode');
    const graphVersionInput = $('#instagramGraphVersion');
    const r2AccountIdInput = $('#r2AccountId');
    const r2BucketInput = $('#r2Bucket');
    const r2AccessKeyInput = $('#r2AccessKeyId');
    const r2SecretInput = $('#r2SecretAccessKey');
    const r2PublicUrlInput = $('#r2PublicBaseUrl');
    const r2ObjectPrefixInput = $('#r2ObjectPrefix');
    const r2RetainInput = $('#r2RetainMedia');
    const modalOpen = Boolean($('#instagramConfigModal') && !$('#instagramConfigModal').hidden);
    const r2 = instagram.r2 || {};
    if (igUserIdInput && document.activeElement !== igUserIdInput) igUserIdInput.value = String(instagram.ig_user_id || '');
    if (apiModeInput && document.activeElement !== apiModeInput) apiModeInput.value = String(instagram.api_mode || 'instagram_login');
    if (graphVersionInput && document.activeElement !== graphVersionInput) graphVersionInput.value = String(instagram.graph_version || 'v25.0');
    if (r2AccountIdInput && !modalOpen && document.activeElement !== r2AccountIdInput) r2AccountIdInput.value = '';
    if (r2BucketInput && document.activeElement !== r2BucketInput) r2BucketInput.value = String(r2.bucket || '');
    if (r2AccessKeyInput && !modalOpen && document.activeElement !== r2AccessKeyInput) r2AccessKeyInput.value = '';
    if (r2SecretInput && !modalOpen && document.activeElement !== r2SecretInput) r2SecretInput.value = '';
    if (r2PublicUrlInput && document.activeElement !== r2PublicUrlInput) r2PublicUrlInput.value = String(r2.public_base_url || '');
    if (r2ObjectPrefixInput && document.activeElement !== r2ObjectPrefixInput) r2ObjectPrefixInput.value = String(r2.object_prefix || 'instagram');
    if (r2RetainInput && document.activeElement !== r2RetainInput) r2RetainInput.checked = Boolean(r2.retain_media);
    const tokenInput = $('#instagramAccessToken');
    if (tokenInput) tokenInput.placeholder = instagram.configured ? 'Đã lưu token, dán token mới nếu muốn đổi' : 'Dán token dài hạn';
    if (r2AccountIdInput) r2AccountIdInput.placeholder = r2.configured ? 'Đã lưu Account ID, dán ID mới nếu muốn đổi' : 'Cloudflare Account ID';
    if (r2AccessKeyInput) r2AccessKeyInput.placeholder = r2.configured ? 'Đã lưu Access Key ID, dán key mới nếu muốn đổi' : 'Access Key ID';
    if (r2SecretInput) r2SecretInput.placeholder = r2.configured ? 'Đã lưu Secret Key, dán key mới nếu muốn đổi' : 'Secret Access Key';
  }

  function openInstagramConfigModal() {
    const modal = $('#instagramConfigModal');
    if (!modal) return;
    modal.hidden = false;
    syncInstagramConfigUi(state.instagramConfig || {});
    const tokenInput = $('#instagramAccessToken');
    if (tokenInput) tokenInput.value = '';
    ['#r2AccountId', '#r2AccessKeyId', '#r2SecretAccessKey'].forEach((selector) => {
      const input = $(selector);
      if (input) input.value = '';
    });
    if (tokenInput) tokenInput.focus();
    else $('#instagramIgUserId')?.focus();
  }

  function closeInstagramConfigModal() {
    const modal = $('#instagramConfigModal');
    ['#instagramAccessToken', '#r2AccountId', '#r2AccessKeyId', '#r2SecretAccessKey'].forEach((selector) => {
      const input = $(selector);
      if (input) input.value = '';
    });
    if (modal) modal.hidden = true;
  }

  let instagramConfigSaving = false;
  async function saveInstagramConfig() {
    const button = $('#saveInstagramConfig');
    const payload = {
      igUserId: $('#instagramIgUserId')?.value.trim() || '',
      accessToken: $('#instagramAccessToken')?.value.trim() || '',
      apiMode: $('#instagramApiMode')?.value || 'instagram_login',
      graphVersion: $('#instagramGraphVersion')?.value.trim() || 'v25.0',
      r2AccountId: $('#r2AccountId')?.value.trim() || '',
      r2Bucket: $('#r2Bucket')?.value.trim() || '',
      r2AccessKeyId: $('#r2AccessKeyId')?.value.trim() || '',
      r2SecretAccessKey: $('#r2SecretAccessKey')?.value.trim() || '',
      r2PublicBaseUrl: $('#r2PublicBaseUrl')?.value.trim() || '',
      r2ObjectPrefix: $('#r2ObjectPrefix')?.value.trim() || 'instagram',
      r2RetainMedia: $('#r2RetainMedia')?.checked === true,
    };
    if (!payload.igUserId || !payload.accessToken || !payload.r2AccountId || !payload.r2Bucket || !payload.r2AccessKeyId || !payload.r2SecretAccessKey || !payload.r2PublicBaseUrl) {
      setUploadStatus('Nhập đủ Instagram ID/token và toàn bộ thông tin R2 trước khi lưu.', 'bad');
      return;
    }
    if (instagramConfigSaving) return;
    instagramConfigSaving = true;
    if (button) button.disabled = true;
    setUploadStatus('Đang lưu cấu hình Instagram + R2...', 'warn');
    try {
      const response = await fetch('/api/social/instagram/config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
      closeInstagramConfigModal();
      await refreshSocialStatus();
      setUploadStatus('Đã lưu cấu hình Instagram API và Cloudflare R2.', 'good');
    } catch (error) {
      setUploadStatus(error.message || String(error), 'bad');
    } finally {
      instagramConfigSaving = false;
      if (button) button.disabled = false;
    }
  }

  function syncThreadsConfigUi(threads = {}) {
    const userIdInput = $('#threadsUserId');
    const tokenInput = $('#threadsAccessToken');
    const graphVersionInput = $('#threadsGraphVersion');
    const configState = $('#threadsConfigState');
    const userId = String(threads.threads_user_id || threads.user_id || '').trim();
    if (userIdInput && document.activeElement !== userIdInput) userIdInput.value = userId;
    if (graphVersionInput && document.activeElement !== graphVersionInput) graphVersionInput.value = String(threads.graph_version || 'v1.0');
    if (tokenInput) {
      tokenInput.placeholder = threads.configured
        ? 'Đã lưu token, dán token mới nếu muốn đổi'
        : 'Dán token Threads';
      if (!$('#threadsConfigModal') || $('#threadsConfigModal').hidden) tokenInput.value = '';
    }
    if (configState) {
      configState.textContent = threads.message || (threads.available ? 'Threads đã sẵn sàng.' : 'Cần cấu hình Threads API.');
    }
  }

  function openThreadsConfigModal() {
    const modal = $('#threadsConfigModal');
    if (!modal) return;
    modal.hidden = false;
    syncThreadsConfigUi(state.threadsConfig || {});
    const tokenInput = $('#threadsAccessToken');
    if (tokenInput) {
      tokenInput.value = '';
      tokenInput.focus();
    } else {
      $('#threadsUserId')?.focus();
    }
  }

  function closeThreadsConfigModal() {
    const modal = $('#threadsConfigModal');
    const tokenInput = $('#threadsAccessToken');
    if (tokenInput) tokenInput.value = '';
    if (modal) modal.hidden = true;
  }

  async function saveThreadsConfig() {
    const userIdInput = $('#threadsUserId');
    const tokenInput = $('#threadsAccessToken');
    const graphVersionInput = $('#threadsGraphVersion');
    const button = $('#saveThreadsConfig');
    const threadsUserId = userIdInput?.value.trim() || '';
    const accessToken = tokenInput?.value.trim() || '';
    const graphVersion = graphVersionInput?.value.trim() || 'v1.0';
    if (!threadsUserId || !accessToken) {
      setUploadStatus('Nhập đủ Threads User ID và access token trước khi lưu.', 'bad');
      return;
    }
    if (threadsConfigSaving) return;
    threadsConfigSaving = true;
    if (button) button.disabled = true;
    setUploadStatus('Đang lưu cấu hình Threads...', 'warn');
    try {
      const response = await fetch('/api/social/threads/config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ threadsUserId, accessToken, graphVersion }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
      closeThreadsConfigModal();
      await refreshSocialStatus();
      setUploadStatus('Đã lưu cấu hình Threads.', 'good');
    } catch (error) {
      setUploadStatus(error.message || String(error), 'bad');
    } finally {
      threadsConfigSaving = false;
      if (button) button.disabled = false;
    }
  }

  async function setYoutubeActiveChannel(channelId) {
    if (!channelId) return;
    setAccountListDisabled('youtube', true);
    setUploadStatus('Đang đổi YouTube Channel...', 'warn');
    try {
      const response = await fetch('/api/social/youtube/active-channel', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ channelId }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
      setUploadStatus('Đã đổi YouTube Channel.', 'good');
      await refreshSocialStatus();
    } catch (error) {
      setUploadStatus(error.message || String(error), 'bad');
    } finally {
      setAccountListDisabled('youtube', false);
    }
  }

  async function setFacebookActivePage(pageId) {
    if (!pageId) return;
    setAccountListDisabled('facebook', true);
    setUploadStatus('Đang đổi Facebook Page...', 'warn');
    try {
      const response = await fetch('/api/social/facebook/active-page', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ pageId }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
      setUploadStatus('Đã đổi Facebook Page.', 'good');
      await refreshSocialStatus();
    } catch (error) {
      setUploadStatus(error.message || String(error), 'bad');
    } finally {
      setAccountListDisabled('facebook', false);
    }
  }

  async function refreshSocialStatus() {
    const connectYoutube = $('#connectYoutube');
    const uploadYoutube = $('#uploadYoutube');
    const uploadFacebook = $('#uploadFacebook');
    const uploadInstagram = $('#uploadInstagram');
    const uploadTiktok = $('#uploadTiktok');
    const tiktokConfigState = $('#tiktokConfigState');
    const uploadThreads = $('#uploadThreads');
    const uploadMetaAll = $('#uploadMetaAll');
    const instagramConfigState = $('#instagramConfigState');
    const threadsConfigState = $('#threadsConfigState');
    const commentFacebookSource = $('#commentFacebookSource');
    const facebookVideoState = $('#facebookVideoState');
    if (!connectYoutube || !uploadYoutube) return;
    try {
      const response = await fetch('/api/social/status', { cache: 'no-store' });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
      const youtube = data.platforms?.youtube || {};
      const facebook = data.platforms?.facebook || {};
      const instagram = data.platforms?.instagram || {};
      const threads = data.platforms?.threads || {};
      const tiktok = data.platforms?.tiktok || {};
      const canYoutube = Boolean(youtube.configured && youtube.connected);
      const canFacebook = Boolean(facebook.available);
      const canInstagram = Boolean(instagram.available);
      const canThreads = Boolean(threads.available);
      const canTiktok = Boolean(tiktok.connected);
      state.instagramConfig = instagram;
      state.threadsConfig = threads;
      state.socialReady = { youtube: canYoutube, facebook: canFacebook, instagram: canInstagram, threads: canThreads, tiktok: canTiktok };
      state.socialConfigured = { youtube: Boolean(youtube.configured), facebook: Boolean(facebook.configured), instagram: Boolean(instagram.configured), threads: Boolean(threads.configured) };
      syncYoutubeChannels(youtube);
      syncFacebookPages(facebook);
      syncYoutubeConfigUi(youtube);
      syncFacebookConfigUi(facebook);
      syncInstagramConfigUi(instagram);
      syncThreadsConfigUi(threads);
      if (tiktokConfigState) tiktokConfigState.textContent = tiktok.message || (canTiktok ? 'Zernio đã kết nối.' : 'Cần cấu hình Zernio API key và TikTok account ID.');
      setButtonLabel(connectYoutube, 'Thêm channel');
      uploadYoutube.disabled = !canYoutube;
      if (uploadFacebook) {
        uploadFacebook.disabled = !canFacebook;
        setButtonLabel(uploadFacebook, canFacebook ? 'Upload Facebook Reels' : 'Cấu hình Facebook');
      }
      if (uploadTiktok) {
        uploadTiktok.disabled = !canTiktok;
        setButtonLabel(uploadTiktok, canTiktok ? 'Đăng TikTok' : 'Cấu hình Zernio');
      }
      if (uploadInstagram) {
        uploadInstagram.disabled = !canInstagram;
        setButtonLabel(uploadInstagram, canInstagram ? 'Upload Instagram Reels' : 'Cấu hình Instagram + R2');
      }
      if (uploadThreads) {
        uploadThreads.disabled = !canThreads;
        setButtonLabel(uploadThreads, canThreads ? 'Upload Threads' : 'Cấu hình Threads');
      }
      if (instagramConfigState) {
        instagramConfigState.textContent = instagram.message || (canInstagram ? 'Instagram và R2 đã sẵn sàng.' : 'Cần cấu hình Instagram API và Cloudflare R2.');
      }
      if (threadsConfigState) {
        threadsConfigState.textContent = threads.message || (canThreads ? 'Threads đã sẵn sàng.' : 'Cần cấu hình Threads API.');
      }
      const binance = data.platforms?.binance || {};
      const uploadBinance = $('#uploadBinance');
      const openBinanceConfig = $('#openBinanceConfig');
      if (uploadBinance) {
        const canBinance = Boolean(binance.configured && binance.connected);
        uploadBinance.disabled = !canBinance;
        setButtonLabel(uploadBinance, canBinance ? 'Đăng Binance' : 'Cấu hình Binance');
      }
      if (openBinanceConfig) {
        setButtonLabel(openBinanceConfig, binance.configured ? 'Đổi OpenAPI key' : 'Cấu hình OpenAPI key');
      }
      updateFacebookCommentButton();
      if (facebookVideoState && facebook.video_state) facebookVideoState.value = facebook.video_state === 'PUBLISHED' ? 'PUBLISHED' : 'DRAFT';
      const ready = [];
      if (canYoutube) ready.push('YouTube');
      if (canFacebook) ready.push('Facebook Reels');
      if (canInstagram) ready.push('Instagram Reels');
      if (canThreads) ready.push('Threads');
      if (ready.length) {
        setUploadStatus(`${ready.join(', ')} đã sẵn sàng.`, 'good');
      } else {
        const missingYoutube = !youtube.configured;
        const missingFacebook = !facebook.configured;
        if (missingYoutube && missingFacebook) {
          setUploadStatus('Chưa kết nối nền tảng. Bấm Thêm channel hoặc Thêm Page để nhập thông tin trực tiếp.', 'warn');
        } else {
          const nextSteps = [];
          if (youtube.configured && !youtube.connected) nextSteps.push('YouTube đã có OAuth key. Bấm Thêm channel để kết nối kênh.');
          if (missingYoutube) nextSteps.push(youtube.message || 'YouTube chưa có OAuth key. Bấm Thêm channel để nhập trực tiếp.');
          if (missingFacebook) nextSteps.push(facebook.message || 'Facebook chưa có Page. Bấm Thêm Page để nhập trực tiếp.');
          if (!canInstagram) nextSteps.push(instagram.message || 'Instagram chưa cấu hình API và R2.');
          if (!canThreads) nextSteps.push(threads.message || 'Threads chưa cấu hình API.');
          setUploadStatus(nextSteps[0] || 'Chưa kết nối Social API.', 'warn');
        }
      }
      updateUploadBothButton();
      updateMetaAllButton();
    } catch (error) {
      state.socialReady = { youtube: false, facebook: false, instagram: false, threads: false };
      state.socialConfigured = { youtube: false, facebook: false, instagram: false, threads: false };
      state.instagramConfig = {};
      state.threadsConfig = {};
      syncYoutubeConfigUi({ configured: false });
      syncFacebookConfigUi({ configured: false });
      uploadYoutube.disabled = true;
      if (uploadFacebook) uploadFacebook.disabled = true;
      if (uploadInstagram) uploadInstagram.disabled = true;
      if (uploadThreads) uploadThreads.disabled = true;
      if (uploadMetaAll) uploadMetaAll.disabled = true;
      if (instagramConfigState) instagramConfigState.textContent = error.message || String(error);
      if (threadsConfigState) threadsConfigState.textContent = error.message || String(error);
      if (commentFacebookSource) commentFacebookSource.disabled = true;
      updateUploadBothButton();
      updateMetaAllButton();
      updateFacebookCommentButton();
      setUploadStatus(error.message || String(error), 'bad');
    }
  }

  async function showUploadPanel(projectName, videoUrl) {
    const panel = $('#uploadPanel');
    if (!panel) return;
    state.uploadProject = projectName;
    state.outputUrl = videoUrl || state.outputUrl;
    state.facebookCommentTargetId = '';
    panel.hidden = false;
    setUploadEmpty(projectName, false);
    setUploadResult('');
    await loadUploadMetadata(projectName);
    await refreshSocialStatus();
  }

  function setRenderState(title = '', logs = [], tone = 'running', progress = {}) {
    const panel = $('#renderState');
    const titleEl = $('#stateTitle');
    const logEl = $('#stateList');
    const percentEl = $('#statePercent');
    const barEl = $('#stateProgressBar');
    if (!panel || !titleEl || !logEl) return;
    panel.hidden = !title;
    panel.className = `render-state ${tone}`;
    titleEl.textContent = title;
    const percent = Math.max(0, Math.min(100, Number(progress.percent || 0)));
    if (percentEl) percentEl.textContent = `${Math.round(percent)}%`;
    if (barEl) barEl.style.width = `${percent}%`;
    const lines = Array.isArray(logs) ? logs : [logs];
    logEl.textContent = lines.filter(Boolean).join('\n') || tr('Chưa có log kỹ thuật.', 'No technical logs yet.');
  }

  function formatElapsed(seconds) {
    const totalSeconds = Math.max(0, Math.floor(seconds || 0));
    const hours = Math.floor(totalSeconds / 3600);
    const minutes = Math.floor((totalSeconds % 3600) / 60);
    const remainingSeconds = totalSeconds % 60;
    if (hours) return `${hours}h${minutes}m${remainingSeconds}s`;
    if (minutes) return `${minutes}m${remainingSeconds}s`;
    return `${remainingSeconds}s`;
  }

  function elapsedForJob(job) {
    const startedAt = Number(job.started_at || job.created_at || 0);
    if (!startedAt) return '0s';
    const finishedAt = Number(job.finished_at || 0);
    const endedAt = ['done', 'failed', 'cancelled'].includes(job.status) && finishedAt ? finishedAt : Date.now() / 1000;
    return formatElapsed(endedAt - startedAt);
  }

  function renderTitle(label, job) {
    return tr(`${label} (đã chạy ${elapsedForJob(job)})`, `${label} (ran ${elapsedForJob(job)})`);
  }

  function latestRawLogLine(logs = '') {
    const lines = String(logs).split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
    return lines.length ? lines[lines.length - 1] : '';
  }

  function renderProgressForJob(job) {
    const logs = job.logs || '';
    const rawLine = latestRawLogLine(logs);
    let progress = {
      title: tr('Bắt đầu render...', 'Starting render…'),
      log: rawLine,
      stage: 0,
      percent: 5,
    };

    const candidates = [
      [/Tạo voiceover|Generating Edge TTS|ElevenLabs|atempo=|volume=|render-voiceover\.wav/, tr('Đang chuẩn bị audio...', 'Preparing audio…'), 0, 7],
      [/Whisper transcription|Whisper nhận|align_voiceover/, tr('Whisper đang căn phụ đề...', 'Whisper is aligning subtitles…'), 1, 13],
      [/Phát hiện \d+ khoảng lặng|speech starts after silence|Đã căn \d+ dòng/, tr('Đang gắn pose và âm thanh...', 'Applying poses and sounds…'), 2, 20],
      [/Recording one-scene video|Rendering one-scene video frame-by-frame|render_demo\.py/, tr('Đang render video...', 'Rendering video…'), 3, 25],
      [/Measured browser preroll|frame=\s*\d+/, tr('Đang ghép và mã hóa MP4...', 'Muxing and encoding MP4…'), 4, 90],
      [/Applying branding/, tr('Đang gắn logo AurexVideo...', 'Applying AurexVideo logo…'), 4, 96],
      [/^Done:/m, tr('Sắp hoàn thành...', 'Almost done…'), 4, 99],
    ];
    let newestIndex = -1;
    candidates.forEach(([pattern, title, stage, percent]) => {
      const matches = [...logs.matchAll(new RegExp(pattern.source, 'g'))];
      const index = matches.length ? matches[matches.length - 1].index : -1;
      if (index > newestIndex) {
        newestIndex = index;
        progress = { title, log: rawLine, stage, percent };
      }
    });
    const recordingMatches = [...logs.matchAll(/(?:Recording progress|Rendering frames): (\d+(?:\.\d+)?)\/(\d+(?:\.\d+)?)s/g)];
    if (recordingMatches.length && recordingMatches[recordingMatches.length - 1].index >= newestIndex) {
      const match = recordingMatches[recordingMatches.length - 1];
      const current = Number(match[1]);
      const total = Math.max(1, Number(match[2]));
      progress = {
        title: tr(`Đang render video ${current}s / ${total}s`, `Rendering video ${current}s / ${total}s`),
        log: rawLine,
        stage: 3,
        percent: 25 + Math.min(1, current / total) * 60,
      };
    }

    if (job.status === 'done') return { title: tr('Hoàn tất render', 'Render complete'), log: rawLine, stage: 5, percent: 100 };
    if (job.status === 'failed') return { ...progress, title: tr('Render thất bại', 'Render failed'), log: job.error || rawLine };
    if (job.status === 'cancelled') return { ...progress, title: tr('Đã dừng render', 'Render stopped'), log: rawLine || tr('Render đã được dừng.', 'Render was stopped.') };
    if (job.status === 'cancelling') return { ...progress, title: tr('Đang dừng render...', 'Stopping render…'), log: rawLine || tr('Đang gửi tín hiệu dừng job render.', 'Sending stop signal to the render job.') };
    return progress;
  }

  function setEngine(engine) {
    state.engine = engine;
    $all('[data-engine]').forEach((button) => {
      button.classList.toggle('active', button.dataset.engine === engine);
    });
    $all('[data-pane]').forEach((pane) => {
      pane.hidden = pane.dataset.pane !== engine;
    });
    syncAdvancedSettings();
    if (engine === 'vieneu' || engine === 'aurextts') loadVieneuVoices();
  }

  function syncAdvancedSettings() {
    $all('[data-advanced-engine]').forEach((pane) => {
      pane.hidden = pane.dataset.advancedEngine !== state.engine;
    });
  }


  function currentEdgeVoice() {
    const selectedVoice = $('#edgeVoice')?.value || 'vi-VN-NamMinhNeural';
    if (selectedVoice !== 'custom') return selectedVoice;
    const customVoice = $('#edgeVoiceCustom')?.value.trim() || '';
    return customVoice;
  }

  function currentVieneuVoice() {
    return String($('#vieneuVoice')?.value || $('#aurexttsVoice')?.value || '').trim() || 'chautinhtri';
  }

  async function loadVieneuConfig() {
    try {
      const response = await fetch('/api/tts/vieneu/config', { cache: 'no-store' });
      if (!response.ok) return;
      const config = await response.json();
      const mode = $('#vieneuMode');
      if (mode && config.mode) mode.value = config.mode;
      const device = $('#vieneuDevice');
      if (device && config.device) device.value = config.device;
    } catch (error) { /* keep defaults */ }
  }

  async function loadVieneuVoices() {
    const select = $('#vieneuVoice') || $('#aurexttsVoice');
    if (!select) return;
    const stateEl = $('#vieneuConfigState') || $('#aurexttsConfigState');
    try {
      const response = await fetch('/api/tts/vieneu/health', { cache: 'no-store' });
      const health = await response.json();
      if (!health.ok) {
        select.innerHTML = '<option value="chautinhtri">Châu Tinh Trì (Clone)</option>';
        if (stateEl) stateEl.textContent = health.error || 'VieNeu-TTS chưa sẵn sàng.';
        return;
      }
      const voices = Array.isArray(health.voices) ? health.voices : [];
      select.innerHTML = voices
        .map((voice) => {
          const id = String(voice?.id ?? voice ?? '');
          const label = String(voice?.name ?? id);
          return `<option value="${escapeHtml(id)}">${escapeHtml(label)}</option>`;
        })
        .join('');
      if (stateEl) stateEl.textContent = `VieNeu-TTS OK · ${voices.length} giọng sẵn sàng.`;
    } catch (error) {
      select.innerHTML = '<option value="chautinhtri">Châu Tinh Trì (Clone)</option>';
      if (stateEl) stateEl.textContent = error.message || 'Lỗi kiểm tra VieNeu-TTS.';
    }
  }

  function currentMaziaoVoice() {
    collectMaziaoSpeakerValues();
    const model = maziaoSpeakerModels()[0];
    const value = model ? state.maziaoSpeakerValues[normalizedSpeakerKey(model.key)] : null;
    return String(value?.voiceId || model?.seed?.voiceId || 'oncoinx').trim() || 'oncoinx';
  }

  function currentMaziaoTtsMode() {
    return $('#maziaoTtsMode')?.value || 'auto';
  }

  function effectiveMaziaoTtsMode() {
    const selected = currentMaziaoTtsMode();
    if (selected !== 'auto') return selected;
    const config = maziaoTopicTtsConfig(state.maziaoTopic);
    const raw = String(config.mode || config.ttsMode || config.tts_mode || '').toLowerCase().replace(/[^a-z]/g, '');
    return raw.includes('multispeaker') ? 'multiSpeakers' : 'paragraph';
  }

  function syncMaziaoTtsMode() {
    const customization = $('#maziaoCustomization');
    if (customization) customization.hidden = state.engine !== 'maziao';
    renderMaziaoSpeakerCards();
  }

  function currentMaziaoTtsConfig() {
    if (effectiveMaziaoTtsMode() !== 'multiSpeakers') return undefined;
    collectMaziaoSpeakerValues();
    const speakers = {};
    for (const model of maziaoSpeakerModels()) {
      const value = state.maziaoSpeakerValues[normalizedSpeakerKey(model.key)] || model.seed || {};
      const voiceId = String(value.voiceId || 'oncoinx').trim();
      speakers[model.key] = {
        alias: String(value.alias || model.alias || model.key).trim(),
        voiceId,
        speed: clampNumber(value.speed, 1, 0.4, 1.6),
        pitch: clampNumber(value.pitch, 1, 0.6, 1.4),
      };
    }
    return { speakers };
  }

  function escapeHtml(value) {
    return String(value ?? '').replace(/[&<>"']/g, (character) => ({
      '&': '&amp;',
      '<': '&lt;',
      '>': '&gt;',
      '"': '&quot;',
      "'": '&#39;',
    }[character]));
  }

  function normalizedSpeakerKey(value) {
    return String(value || '').trim().toLowerCase();
  }

  function clampNumber(value, fallback, minimum, maximum) {
    const number = Number(value);
    if (!Number.isFinite(number)) return fallback;
    return Math.min(maximum, Math.max(minimum, number));
  }

  function maziaoTopicTtsConfig(topic) {
    const config = {};
    if (!topic || typeof topic !== 'object') return config;
    for (const key of ['tts', 'ttsConfig', 'tts_config']) {
      if (topic[key] && typeof topic[key] === 'object' && !Array.isArray(topic[key])) Object.assign(config, topic[key]);
    }
    if (!config.speakers && (Array.isArray(topic.speakers) || (topic.speakers && typeof topic.speakers === 'object'))) {
      config.speakers = topic.speakers;
    }
    return config;
  }

  function segmentSpeakerReference(segment) {
    if (!segment || typeof segment !== 'object') return '';
    const explicit = String(segment.speaker || segment.speakerId || segment.speaker_id || segment.role || '').trim();
    if (explicit) return explicit;
    const text = String(segment.text || '').trim();
    const match = text.match(/^\[([^\]]+)\]\s*(.*)$/s);
    if (match && !/^delay=/i.test(match[1].trim())) return match[1].trim();
    return '';
  }

  function maziaoSpeakerModels() {
    const topic = state.maziaoTopic || {};
    const config = maziaoTopicTtsConfig(topic);
    const segments = Array.isArray(topic.segments) ? topic.segments : [];
    const rawSpeakers = config.speakers;
    const entries = [];
    if (Array.isArray(rawSpeakers)) {
      rawSpeakers.forEach((value, index) => {
        if (!value || typeof value !== 'object') return;
        const key = String(value.id || value.key || value.alias || value.name || `speaker-${index + 1}`).trim();
        entries.push([key, value]);
      });
    } else if (rawSpeakers && typeof rawSpeakers === 'object') {
      Object.entries(rawSpeakers).forEach(([key, value]) => entries.push([key, value]));
    }

    const models = [];
    const used = new Set();
    const addModel = (key, value = {}, count = 0) => {
      const cleanKey = String(key || '').trim() || `speaker-${models.length + 1}`;
      const normalized = normalizedSpeakerKey(cleanKey);
      if (used.has(normalized)) return;
      used.add(normalized);
      const seed = typeof value === 'string' ? { voiceId: value } : (value && typeof value === 'object' ? { ...value } : {});
      const alias = String(seed.alias || seed.name || cleanKey).trim();
      const references = new Set([normalizedSpeakerKey(cleanKey), normalizedSpeakerKey(alias)]);
      const matchingCount = cleanKey === 'default'
        ? segments.length
        : segments.filter((segment) => references.has(normalizedSpeakerKey(segmentSpeakerReference(segment)))).length;
      models.push({ key: cleanKey, alias, count: Math.max(count, matchingCount), seed });
    };

    entries.forEach(([key, value]) => addModel(key, value));
    segments.forEach((segment) => {
      const reference = segmentSpeakerReference(segment);
      if (!reference) return;
      const found = models.some((model) => normalizedSpeakerKey(model.key) === normalizedSpeakerKey(reference) || normalizedSpeakerKey(model.alias) === normalizedSpeakerKey(reference));
      if (!found) addModel(reference, { alias: reference }, 0);
    });

    if (!models.length) addModel('default', { alias: 'Toàn bộ script' }, segments.length || 1);
    if (effectiveMaziaoTtsMode() !== 'multiSpeakers') {
      const first = models[0];
      return [{
        key: 'default',
        alias: 'Toàn bộ script',
        count: segments.length || first.count || 1,
        seed: first.seed || {},
      }];
    }
    return models;
  }

  function maziaoVoiceMeta(voiceId) {
    const requested = String(voiceId || '').trim();
    const lower = requested.toLowerCase();
    return state.maziaoVoiceCatalog.find((item) => (
      item.id === requested
      || String(item.voiceId || '') === requested
      || String(item.name || '').trim().toLowerCase() === lower
      || (lower === 'oncoinx' && String(item.name || '').toLowerCase() === 'oncoinx')
    )) || {
      id: requested,
      name: requested || 'OncoinX',
      previewUrl: MAZIAO_PREVIEW_FALLBACKS[requested] || '',
      language: 'Vietnamese',
      gender: '',
      type: '',
    };
  }

  function initialsForVoice(name) {
    const words = String(name || 'Voice').trim().split(/\s+/).filter(Boolean);
    return (words.length > 1 ? words[0][0] + words[words.length - 1][0] : words[0].slice(0, 2)).toUpperCase();
  }

  function voiceTagText(value, fallback = '') {
    const text = String(value || fallback).trim();
    if (!text) return '';
    return text.length <= 18 ? text : `${text.slice(0, 16)}…`;
  }

  function collectMaziaoSpeakerValues() {
    $all('[data-maziao-speaker-card]').forEach((card) => {
      const key = card.dataset.speakerKey || 'default';
      const select = card.querySelector('[data-maziao-voice-select]');
      const speed = card.querySelector('[data-maziao-speed]');
      const pitch = card.querySelector('[data-maziao-pitch]');
      state.maziaoSpeakerValues[normalizedSpeakerKey(key)] = {
        alias: card.dataset.speakerAlias || key,
        voiceId: String(select?.value || 'oncoinx').trim(),
        speed: clampNumber(speed?.value, 1, 0.4, 1.6),
        pitch: clampNumber(pitch?.value, 1, 0.6, 1.4),
      };
    });
  }

  function renderMaziaoSpeakerCards() {
    const container = $('#maziaoSpeakerCards');
    if (!container) return;
    const empty = $('#maziaoSpeakerEmpty');
    const models = maziaoSpeakerModels();
    if (!models.length) {
      container.replaceChildren();
      if (empty) empty.hidden = false;
      return;
    }
    if (empty) empty.hidden = true;
    const accents = ['violet', 'blue', 'mint', 'orange'];
    const tones = ['pink', 'blue', 'green', 'orange'];
    container.innerHTML = models.map((model, index) => {
      const normalized = normalizedSpeakerKey(model.key);
      const existing = state.maziaoSpeakerValues[normalized] || {};
      const seed = { ...(model.seed || {}), ...existing };
      const voiceId = String(seed.voiceId || 'oncoinx').trim();
      const voice = maziaoVoiceMeta(voiceId);
      const options = [...state.maziaoVoiceCatalog];
      if (voiceId && !options.some((item) => item.id === voiceId || item.voiceId === voiceId)) {
        options.unshift({ id: voiceId, name: voice.name || voiceId, previewUrl: voice.previewUrl || '' });
      }
      const optionHtml = options.map((item) => {
        const id = String(item.id || item.voiceId || '').trim();
        if (!id) return '';
        return `<option value="${escapeHtml(id)}"${id === voiceId ? ' selected' : ''}>${escapeHtml(item.name || id)}</option>`;
      }).join('');
      const gender = voiceTagText(voice.gender, '');
      const language = voiceTagText(voice.language || voice.lang, 'Vietnamese');
      const type = voiceTagText(voice.type || voice.category, voice.clone ? 'Clone' : '');
      const tags = [gender, language, type].filter(Boolean).map((tag, tagIndex) => `<span class="maziao-voice-tag${tagIndex === 0 ? ' gender' : ''}">${escapeHtml(tag)}</span>`).join('');
      const previewUrl = String(voice.previewUrl || MAZIAO_PREVIEW_FALLBACKS[voiceId] || '').trim();
      return `<article class="maziao-speaker-card" data-maziao-speaker-card data-speaker-key="${escapeHtml(model.key)}" data-speaker-alias="${escapeHtml(model.alias)}" data-accent="${accents[index % accents.length]}">
        <div class="maziao-speaker-head">
          <span class="maziao-speaker-badge">${escapeHtml(model.alias)}</span>
          <span class="maziao-speaker-count">Gồm ${model.count || 1} segment${model.count === 1 ? '' : 's'}</span>
          <button type="button" class="maziao-speaker-preview" data-maziao-preview data-preview-url="${escapeHtml(previewUrl)}" aria-label="Nghe thử ${escapeHtml(voice.name || voiceId)}" title="Nghe thử voice" ${previewUrl ? '' : 'disabled'}>
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3Z"/><path d="M19 10v2a7 7 0 0 1-14 0v-2"/><path d="M12 19v3"/></svg>
          </button>
        </div>
        <div class="maziao-voice-picker" data-avatar-tone="${tones[index % tones.length]}">
          <span class="maziao-voice-avatar">${escapeHtml(initialsForVoice(voice.name || voiceId))}</span>
          <span class="maziao-voice-info"><strong class="maziao-voice-name">${escapeHtml(voice.name || voiceId || 'OncoinX')}</strong><span class="maziao-voice-tags">${tags}</span></span>
          <span class="maziao-voice-chevron" aria-hidden="true">⌄</span>
          <select class="maziao-speaker-voice" data-maziao-voice-select data-speaker-key="${escapeHtml(model.key)}" aria-label="Chọn voice cho ${escapeHtml(model.alias)}">${optionHtml}</select>
        </div>
        <div class="maziao-speaker-controls">
          <label class="maziao-speaker-control"><span>Voice speed</span><input type="number" min="0.4" max="1.6" step="0.05" value="${escapeHtml(clampNumber(seed.speed, 1, 0.4, 1.6).toFixed(2))}" data-maziao-speed aria-label="Voice speed ${escapeHtml(model.alias)}" /></label>
          <label class="maziao-speaker-control"><span>Pitch</span><input type="number" min="0.6" max="1.4" step="0.05" value="${escapeHtml(clampNumber(seed.pitch, 1, 0.6, 1.4).toFixed(2))}" data-maziao-pitch aria-label="Pitch ${escapeHtml(model.alias)}" /></label>
        </div>
      </article>`;
    }).join('');
    syncMaziaoPreviewAvailability();
  }

  async function loadMaziaoProjectTopic(projectName) {
    stopMaziaoPreview();
    state.maziaoTopic = null;
    state.maziaoSpeakerValues = {};
    renderMaziaoSpeakerCards();
    if (!projectName) return;
    try {
      const response = await fetch(`/api/projects/${encodeURIComponent(projectName)}/topic`, { cache: 'no-store' });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || `HTTP ${response.status}`);
      state.maziaoTopic = payload.topic || {};
      renderMaziaoSpeakerCards();
    } catch (error) {
      state.maziaoTopic = null;
      renderMaziaoSpeakerCards();
    }
  }

  async function applyRememberedTtsVoice(characterId) {
    if (!characterId) return;
    const select = state.engine === 'edge'
      ? $('#edgeVoice')
      : ($('#maziaoSpeakerCards [data-maziao-voice-select][data-speaker-key="default"]') || $('#maziaoSpeakerCards [data-maziao-voice-select]'));
    if (!select) return;
    let remembered = '';
    try {
      const response = await fetch(`/api/tts-voices?characterId=${encodeURIComponent(characterId)}`, { cache: 'no-store' });
      if (response.ok) remembered = (await response.json())?.default || '';
    } catch (error) {
      return;
    }
    if (!remembered) return;
    const match = Array.from(select.options).find(
      (option) => option.value === remembered
        || option.dataset.voiceId === remembered
        || String(option.textContent || '').trim() === remembered
    );
    if (match) {
      select.value = match.value;
      if (state.engine === 'maziao') {
        collectMaziaoSpeakerValues();
        renderMaziaoSpeakerCards();
      }
    }
  }

  async function saveRememberedTtsVoice() {
    const characterId = state.characterId;
    if (!characterId) return;
    const voice = state.engine === 'edge' ? currentEdgeVoice() : currentMaziaoVoice();
    if (!voice) return;
    try {
      await fetch('/api/tts-voices', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ characterId, voice }),
      });
    } catch (error) {
      /* ignore save failures */
    }
  }

  function syncSpeedPresets() {
    const speedInput = $('#renderSpeed');
    const value = speedInput ? Number(speedInput.value || 0) : 0;
    $all('.speed-preset[data-speed]').forEach((button) => {
      button.classList.toggle('active', Number(button.dataset.speed) === value);
    });
  }

  function syncVolumePresets() {
    const volumeInput = $('#renderVolume');
    const value = volumeInput ? Number(volumeInput.value || 0) : 0;
    $all('.speed-preset[data-volume]').forEach((button) => {
      button.classList.toggle('active', Number(button.dataset.volume) === value);
    });
  }


  async function copyProjectScript(projectName, button) {
    const project = String(projectName || '').trim();
    if (!project) {
      setStatus('Không tìm thấy project để copy script.', 'bad');
      return;
    }
    if (button) button.disabled = true;
    try {
      const response = await fetch(`/api/project-script?project=${encodeURIComponent(project)}`, { cache: 'no-store' });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
      const text = Array.isArray(data.lines) ? data.lines.join('\n') : '';
      if (!text.trim()) throw new Error(`${project} chưa có script.txt hoặc script đang trống.`);
      await copyText(text);
      flashCopyButton(button);
      setStatus(`Đã copy script của ${project}.`, 'good');
    } catch (error) {
      setStatus(error.message || String(error), 'bad');
    } finally {
      if (button) button.disabled = false;
    }
  }

  function updateProjectUrl(projectName) {
    if (window.location.pathname !== '/') return;
    const url = new URL(window.location.href);
    url.searchParams.set('project', projectName);
    window.history.replaceState(null, '', url);
  }

  function setSelectedProject(projectName, updateUrl = true) {
    const project = projectMap.get(projectName);
    if (!project) {
      setStatus('Chưa có dự án nào để render.', 'bad');
      return;
    }

    state.project = project.name;
    state.outputUrl = project.output_url || project.video_url || `/project/${encodeURIComponent(project.name)}/output/final_video.mp4`;

    const select = $('#projectSelect');
    if (select) select.value = project.name;
    setProjectVideoBadge(project);

    const selectedName = $('#selectedName');
    if (selectedName) selectedName.textContent = project.name;

    const selectedBadge = $('#selectedBadge');
    if (selectedBadge) selectedBadge.textContent = `${project.script_count || '?'} câu`;

    const previewLink = $('#previewLink');
    if (previewLink) previewLink.href = project.url;

    const existingVideoLink = $('#existingVideoLink');
    if (existingVideoLink) {
      existingVideoLink.href = videoPageUrl(project.name);
      existingVideoLink.hidden = !project.video_url;
    }

    const videoLink = $('#videoLink');
    if (videoLink) {
      videoLink.href = videoPageUrl(project.name);
      videoLink.hidden = true;
    }
    setRevealOutputButton(project.name, !state.busy && Boolean(project.video_url));
    setUploadCenterLink(project.name, Boolean(project.video_url));

    $all('.project-row[data-project]').forEach((row) => row.classList.toggle('selected', row.dataset.project === project.name));
    $all('.select-btn').forEach((button) => button.classList.toggle('active', button.dataset.project === project.name));

    if (!state.busy) {
      setRenderState('', []);
      if (project.video_url) {
        setStatus('', 'warn');
        showUploadPanel(project.name, project.video_url);
      } else {
        hideUploadPanel();
        setUploadEmpty(project.name, Boolean($('#uploadPanel')));
        setStatus(window.location.pathname === '/upload' ? `${project.name} chưa có final_video.mp4. Render ở Dashboard trước khi upload.` : '', project.has_script ? 'warn' : 'bad');
      }
    }
    if (updateUrl) updateProjectUrl(project.name);
    loadMaziaoProjectTopic(project.name).then(() => loadProjectCharacterVoice(project.name));
  }

  async function loadProjectCharacterVoice(projectName) {
    if (!projectName) return;
    let characterId = '';
    try {
      const response = await fetch(`/project/${encodeURIComponent(projectName)}/topic.json`, { cache: 'no-store' });
      if (response.ok) {
        const topic = await response.json();
        characterId = String(topic?.characterId || '').trim();
      }
    } catch (error) {
      characterId = '';
    }
    state.characterId = characterId;
    if (characterId) await applyRememberedTtsVoice(characterId);
  }

  function markProjectHasOutput(projectName, videoUrl) {
    const project = projectMap.get(projectName);
    if (project) {
      project.video_url = videoUrl;
      project.has_output = true;
    }

    const row = rowForProject(projectName);
    const deleteButton = row ? row.querySelector('.delete-output-btn[data-project]') : null;
    if (deleteButton) {
      deleteButton.disabled = false;
      deleteButton.classList.remove('disabled');
    }
    const slot = row ? row.querySelector('.row-video-slot') : null;
    if (slot) {
      slot.textContent = '';
      slot.classList.remove('muted');
      const link = document.createElement('a');
      link.className = 'small-link icon-btn';
      link.href = videoPageUrl(projectName);
      link.innerHTML = '<span class="btn-icon">▶</span><span>Mở</span>';
      slot.appendChild(link);
    }

    const existingVideoLink = $('#existingVideoLink');
    if (existingVideoLink && state.project === projectName) {
      existingVideoLink.href = videoPageUrl(projectName);
      existingVideoLink.hidden = false;
    }
    if (state.project === projectName) setRevealOutputButton(projectName, true);
    if (state.project === projectName) setUploadCenterLink(projectName, true);
    if (state.project === projectName) setProjectVideoBadge(project);
  }

  function markProjectOutputDeleted(projectName) {
    const project = projectMap.get(projectName);
    if (project) {
      project.video_url = null;
      project.has_output = false;
    }

    const row = rowForProject(projectName);
    const deleteButton = row ? row.querySelector('.delete-output-btn[data-project]') : null;
    if (deleteButton) {
      deleteButton.disabled = false;
      deleteButton.classList.remove('disabled');
    }
    const slot = row ? row.querySelector('.row-video-slot') : null;
    if (slot) {
      slot.textContent = '';
      slot.classList.remove('muted');
      const disabled = document.createElement('span');
      disabled.className = 'icon-btn disabled';
      disabled.innerHTML = '<span class="btn-icon">▶</span><span>Mở</span>';
      slot.appendChild(disabled);
    }

    if (state.project === projectName) {
      const existingVideoLink = $('#existingVideoLink');
      if (existingVideoLink) existingVideoLink.hidden = true;
      const videoLink = $('#videoLink');
      if (videoLink) videoLink.hidden = true;
      setRevealOutputButton(projectName, false);
      setUploadCenterLink(projectName, false);
      setProjectVideoBadge(project);
      setUploadEmpty(projectName, Boolean($('#uploadPanel')));
      hideUploadPanel();
    }
  }

  async function revealOutput(projectName) {
    const project = projectMap.get(projectName || state.project);
    if (!project) {
      setStatus('Không tìm thấy dự án để mở output.', 'bad');
      return;
    }
    const button = $('#revealOutput');
    if (button) button.disabled = true;
    setStatus(`Đang mở vị trí final_video.mp4 của ${project.name}...`, 'warn');
    try {
      const response = await fetch('/api/output/reveal', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ project: project.name }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
      setStatus(`Đã mở vị trí final_video.mp4 của ${project.name}.`, 'good');
    } catch (error) {
      setStatus(error.message || String(error), 'bad');
    } finally {
      if (button) button.disabled = false;
    }
  }

  async function deleteOutput(projectName, skipConfirm = false) {
    const project = projectMap.get(projectName);
    if (!project) {
      setStatus('Không tìm thấy dự án để xoá output.', 'bad');
      return;
    }
    if (!project.has_output) {
      setStatus(`${project.name} chưa có output để xoá.`, 'warn');
      return;
    }

    if (!skipConfirm) {
      const confirmed = window.confirm(
        `Xoá toàn bộ thư mục output của "${project.name}"?\n\nHành động này sẽ xoá final_video.mp4 và các file render cache trong thư mục output của project.`
      );
      if (!confirmed) return;
    }

    const buttons = [
      ...$all('.delete-output-btn[data-project]').filter((button) => button.dataset.project === project.name),
      $('#deleteSelectedOutput'),
    ].filter(Boolean);
    buttons.forEach((button) => { button.disabled = true; });
    setStatus(`Đang xoá output của ${project.name}...`, 'warn');

    try {
      const response = await fetch('/api/output/delete', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ project: project.name, confirm: true }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);

      markProjectOutputDeleted(project.name);
      setStatus(data.deleted ? `Đã xoá output của ${project.name}.` : `${project.name} chưa có output để xoá.`, 'good');
    } catch (error) {
      setStatus(error.message || String(error), 'bad');
      setRenderState('Không thể xoá output', [error.message || String(error)], 'failed');
    } finally {
      buttons.forEach((button) => { button.disabled = false; });
    }
  }

  function slugifyProjectName(value) {
    return String(value || '')
      .normalize('NFD')
      .replace(/[\u0300-\u036f]/g, '')
      .replace(/đ/g, 'd')
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, '-')
      .replace(/-+/g, '-')
      .replace(/^-+|-+$/g, '');
  }

  function validateRenameProjectName(value, currentName = '') {
    const name = slugifyProjectName(value);
    if (!name) return 'Vui lòng nhập tên project.';
    if (name.length > 120) return 'Tên project tối đa 120 ký tự.';
    if (!/^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(name)) {
      return 'Chỉ dùng chữ thường không dấu, số và dấu gạch ngang; ví dụ: tieu-thuyet-phan-1.';
    }
    return '';
  }

  function setRenameProjectError(message = '') {
    const error = $('#renameProjectError');
    const input = $('#renameProjectInput');
    if (error) {
      error.textContent = message;
      error.hidden = !message;
    }
    if (input) input.setAttribute('aria-invalid', message ? 'true' : 'false');
  }

  function syncRenameProjectValidation() {
    const input = $('#renameProjectInput');
    const submit = $('#renameProjectSubmit');
    if (!input || !submit) return false;
    const normalized = slugifyProjectName(input.value);
    if (normalized !== input.value) input.value = normalized;
    const error = validateRenameProjectName(input.value, renameProjectTarget);
    const unchanged = input.value.trim() === renameProjectTarget;
    setRenameProjectError(error);
    submit.disabled = Boolean(error) || unchanged;
    return !error && !unchanged;
  }

  function openRenameProjectModal(projectName, trigger = null) {
    const project = projectMap.get(projectName);
    if (!project) {
      setStatus('Không tìm thấy project để đổi tên.', 'bad');
      return;
    }
    const modal = $('#renameProjectModal');
    const input = $('#renameProjectInput');
    if (!modal || !input) return;
    renameProjectTarget = project.name;
    renameProjectTrigger = trigger;
    const current = $('#renameProjectCurrent');
    if (current) current.textContent = project.name;
    input.value = project.name;
    modal.hidden = false;
    syncRenameProjectValidation();
    window.requestAnimationFrame(() => {
      input.focus();
      input.select();
    });
  }

  function closeRenameProjectModal() {
    const modal = $('#renameProjectModal');
    if (!modal || modal.hidden) return;
    modal.hidden = true;
    setRenameProjectError('');
    const trigger = renameProjectTrigger;
    renameProjectTarget = '';
    renameProjectTrigger = null;
    trigger?.focus?.();
  }

  async function renameProject(projectName, nextName, button) {
    const project = projectMap.get(projectName);
    if (!project) {
      setRenameProjectError('Không tìm thấy project để đổi tên.');
      return false;
    }
    if (button) button.disabled = true;
    setStatus(`Đang đổi tên ${project.name} thành ${nextName}...`, 'warn');
    try {
      const response = await fetch('/api/project/rename', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ project: project.name, name: nextName }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
      const url = new URL('/', window.location.origin);
      url.searchParams.set('project', data.project);
      window.location.assign(url);
      return true;
    } catch (error) {
      const message = error.message || String(error);
      setRenameProjectError(message);
      setStatus(message, 'bad');
      return false;
    } finally {
      if (button) button.disabled = false;
      if (renameProjectTrigger) renameProjectTrigger.disabled = false;
    }
  }

  async function duplicateProject(projectName, button) {
    const project = projectMap.get(projectName);
    if (!project) {
      setStatus('Không tìm thấy project để nhân bản.', 'bad');
      return false;
    }
    if (button) button.disabled = true;
    setStatus(`Đang nhân bản ${project.name}...`, 'warn');
    try {
      const response = await fetch('/api/project/duplicate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ project: project.name }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
      const url = new URL('/', window.location.origin);
      url.searchParams.set('project', data.project);
      window.location.assign(url);
      return true;
    } catch (error) {
      setStatus(error.message || String(error), 'bad');
      return false;
    } finally {
      if (button) button.disabled = false;
    }
  }

  function closeDeleteChoice() {
    $('#deleteChoiceModal')?.remove();
  }

  function openDeleteChoice(projectName) {
    const project = projectMap.get(projectName);
    if (!project) {
      setStatus('Không tìm thấy project để xoá.', 'bad');
      return;
    }
    closeDeleteChoice();
    const modal = document.createElement('div');
    modal.id = 'deleteChoiceModal';
    modal.className = 'delete-choice-backdrop';
    modal.innerHTML = `<div class="delete-choice-card" role="dialog" aria-modal="true" aria-labelledby="deleteChoiceTitle">
      <h3 id="deleteChoiceTitle">Bạn muốn xoá phần nào?</h3>
      <p>Project: <span class="delete-choice-project"></span></p>
      <div class="delete-choice-actions">
        <button class="delete-choice-output-button" type="button" data-delete-choice="output">Chỉ xoá output render</button>
        <button class="delete-choice-project-button" type="button" data-delete-choice="project">Xoá toàn bộ project</button>
        <button class="delete-choice-cancel-button" type="button" data-delete-choice="cancel">Huỷ</button>
      </div>
    </div>`;
    modal.querySelector('.delete-choice-project').textContent = project.name;
    const outputButton = modal.querySelector('[data-delete-choice="output"]');
    outputButton.disabled = !project.has_output;
    outputButton.textContent = project.has_output ? 'Chỉ xoá output render' : 'Project chưa có output';
    modal.addEventListener('click', async (event) => {
      const choice = event.target.closest('[data-delete-choice]')?.dataset.deleteChoice;
      if (!choice) {
        if (event.target === modal) closeDeleteChoice();
        return;
      }
      if (choice === 'cancel') return closeDeleteChoice();
      if (choice === 'output') {
        closeDeleteChoice();
        await deleteOutput(project.name, true);
        return;
      }
      if (choice === 'project') {
        await deleteProject(project.name, modal);
      }
    });
    document.body.appendChild(modal);
  }

  async function deleteProject(projectName, modal) {
    const project = projectMap.get(projectName);
    if (!project) return;
    const button = modal?.querySelector('[data-delete-choice="project"]');
    if (button) {
      button.disabled = true;
      button.textContent = 'Đang xoá toàn bộ project...';
    }
    setStatus(`Đang xoá project ${project.name}...`, 'warn');
    try {
      const response = await fetch('/api/project/delete', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ project: project.name, confirm: true }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
      closeDeleteChoice();
      const url = new URL(window.location.href);
      url.searchParams.delete('project');
      window.location.href = `${url.pathname}${url.search}`;
    } catch (error) {
      setStatus(error.message || String(error), 'bad');
      if (button) {
        button.disabled = false;
        button.textContent = 'Xoá toàn bộ project';
      }
    }
  }

  function fileToBase64(file) {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => {
        const result = String(reader.result || '');
        resolve(result.includes(',') ? result.split(',', 2)[1] : result);
      };
      reader.onerror = () => reject(reader.error || new Error('Không đọc được file audio.'));
      reader.readAsDataURL(file);
    });
  }

  function assertFileSize(file, maxBytes, label) {
    if (!file) return;
    if (file.size > maxBytes) {
      const maxMb = Math.round(maxBytes / 1024 / 1024);
      throw new Error(`${label} lớn hơn ${maxMb} MB.`);
    }
  }

  function openBrandConfigModal() {
    if ($('#openBrandConfig')?.disabled || $('#renderBranding')?.disabled) return;
    const modal = $('#brandConfigModal');
    if (!modal) return;
    const nameInput = $('#brandNameInput');
    const logoInput = $('#brandLogoFile');
    const logoName = $('#brandLogoFileName');
    if (nameInput) nameInput.value = state.renderOptions.brandName || 'aurexvideo.app';
    if (logoInput) logoInput.value = '';
    if (logoName) logoName.textContent = state.renderOptions.brandLogoName || 'Logo AurexVideo mặc định';
    modal.hidden = false;
  }

  function closeBrandConfigModal() {
    const modal = $('#brandConfigModal');
    if (modal) modal.hidden = true;
  }

  async function saveBrandConfig() {
    const name = ($('#brandNameInput')?.value || '').trim();
    if (name.length > 64) {
      setStatus('Tên brand tối đa 64 ký tự.', 'bad');
      return;
    }
    const logoFile = $('#brandLogoFile')?.files?.[0] || null;
    try {
      assertFileSize(logoFile, MAX_BRAND_LOGO_BYTES, 'File logo');
    } catch (error) {
      setStatus(error.message || String(error), 'bad');
      return;
    }
    const saveButton = $('#saveBrandConfig');
    if (saveButton) saveButton.disabled = true;
    setStatus('Đang lưu Logo + brand...', 'warn');
    try {
      const payload = { brandName: name || 'aurexvideo.app' };
      if (logoFile) payload.logo = { name: logoFile.name, data: await fileToBase64(logoFile) };
      const response = await fetch('/api/render-branding', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
      state.renderOptions.brandName = String(data.brandName || 'aurexvideo.app').trim() || 'aurexvideo.app';
      state.renderOptions.brandLogoFile = null;
      state.renderOptions.brandLogoName = data.hasLogo ? String(data.logoName || 'Logo đã lưu') : '';
      saveRenderPreferences();
      const logoInput = $('#brandLogoFile');
      const logoName = $('#brandLogoFileName');
      if (logoInput) logoInput.value = '';
      if (logoName) logoName.textContent = state.renderOptions.brandLogoName || 'Logo AurexVideo mặc định';
      closeBrandConfigModal();
      setStatus('Đã lưu Logo + brand.', 'good');
    } catch (error) {
      setStatus(error.message || String(error), 'bad');
    } finally {
      if (saveButton) saveButton.disabled = false;
    }
  }

  async function loadMaziaoFavourites() {
    const cards = $('#maziaoSpeakerCards');
    if (!cards) return;
    cards.innerHTML = '<p class="maziao-speaker-empty">Đang tải danh sách voice...</p>';
    try {
      const response = await fetch('/api/voices/favourites', { cache: 'no-store' });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || `HTTP ${response.status}`);
      const items = Array.isArray(payload?.data?.items)
        ? payload.data.items
        : (Array.isArray(payload?.data)
          ? payload.data
          : (Array.isArray(payload?.items) ? payload.items : []));
      state.maziaoVoiceCatalog = items.map((item) => {
        const voiceId = String(item?.id || '').trim();
        return {
          id: voiceId,
          name: String(item?.name || voiceId),
          previewUrl: String(item?.previewUrl || MAZIAO_PREVIEW_FALLBACKS[voiceId] || '').trim(),
          language: String(item?.language || item?.lang || '').trim(),
          gender: String(item?.gender || '').trim(),
          type: String(item?.type || item?.category || (item?.isClone ? 'Clone' : '')).trim(),
          clone: Boolean(item?.isClone),
        };
      }).filter((item) => item.id);
      renderMaziaoSpeakerCards();
    } catch (error) {
      state.maziaoVoiceCatalog = [];
      renderMaziaoSpeakerCards();
      setStatus(`Không tải được giọng Maziao: ${error.message || error}`, 'bad');
    }
  }

  function setMaziaoPreviewPlaying(playing, targetButton = maziaoPreviewButton) {
    const button = targetButton;
    if (!button) return;
    button.classList.toggle('is-playing', playing);
    button.setAttribute('aria-pressed', String(playing));
    button.setAttribute('aria-label', playing ? 'Tạm dừng nghe thử' : 'Nghe thử giọng');
    button.title = playing ? 'Tạm dừng nghe thử' : 'Nghe thử giọng';
  }

  function stopMaziaoPreview() {
    const activeButton = maziaoPreviewButton;
    if (maziaoPreviewAudio) {
      maziaoPreviewAudio.pause();
      maziaoPreviewAudio.currentTime = 0;
      maziaoPreviewAudio = null;
    }
    maziaoPreviewButton = null;
    setMaziaoPreviewPlaying(false, activeButton);
  }

  function syncMaziaoPreviewAvailability() {
    $all('[data-maziao-preview]').forEach((button) => {
      button.disabled = !String(button.dataset.previewUrl || '').trim();
    });
  }

  function requestMaziaoPreview(targetButton) {
    try {
      const button = targetButton || $('#maziaoSpeakerCards [data-maziao-preview]');
      const previewUrl = String(button?.dataset?.previewUrl || '').trim();
      if (!previewUrl) {
        return;
      }
      if (maziaoPreviewAudio && !maziaoPreviewAudio.paused && maziaoPreviewButton === button) {
        maziaoPreviewAudio.pause();
        setMaziaoPreviewPlaying(false, button);
        return;
      }
      if (!maziaoPreviewAudio || maziaoPreviewAudio.src !== previewUrl) {
        stopMaziaoPreview();
        maziaoPreviewButton = button;
        maziaoPreviewAudio = new Audio(previewUrl);
        maziaoPreviewAudio.addEventListener('ended', () => {
          const finishedButton = maziaoPreviewButton;
          maziaoPreviewAudio = null;
          maziaoPreviewButton = null;
          setMaziaoPreviewPlaying(false, finishedButton);
        }, { once: true });
        maziaoPreviewAudio.addEventListener('error', () => {
          const failedButton = maziaoPreviewButton;
          maziaoPreviewAudio = null;
          maziaoPreviewButton = null;
          setMaziaoPreviewPlaying(false, failedButton);
          setStatus('Không thể phát link preview của giọng này.', 'bad');
        }, { once: true });
      }
      maziaoPreviewAudio.play()
        .then(() => setMaziaoPreviewPlaying(true, button))
        .catch((error) => {
          setMaziaoPreviewPlaying(false, button);
          setStatus(`Không thể phát thử: ${error.message || error}`, 'bad');
        });
    } catch (error) {
      setStatus(`Không thể phát thử: ${error.message || error}`, 'bad');
    }
  }

  async function startRender() {

    const startButton = $('#startRender');
    const stopButton = $('#stopRender');
    const videoLink = $('#videoLink');
    if (startButton) startButton.disabled = true;
    if (stopButton) {
      stopButton.hidden = true;
      stopButton.disabled = true;
    }
    if (videoLink) videoLink.hidden = true;
    setRevealOutputButton(state.project, false);
    state.busy = true;
    state.canceling = false;
    setRenderState(tr('Đang chuẩn bị render', 'Preparing to render'), [tr('Kiểm tra project và thông số render.', 'Checking project and render settings.')]);

    try {
      const project = currentProject();
      if (!project) throw new Error(tr('Vui lòng chọn một dự án trước.', 'Please select a project first.'));

      const payload = {
        project: project.name,
        engine: state.engine,
        speed: Number($('#renderSpeed').value || 1.0),
        volume: Number($('#renderVolume').value || 1),
        size: $('#renderSize')?.value || '1080x1920',
        fps: 30,
        outro: false,
        branding: $('#renderBranding')?.checked === true,
      };
      if (payload.branding) {
        payload.brandName = state.renderOptions.brandName || 'aurexvideo.app';
        if (state.renderOptions.brandLogoFile) {
          assertFileSize(state.renderOptions.brandLogoFile, MAX_BRAND_LOGO_BYTES, 'File logo');
          payload.brandLogo = {
            name: state.renderOptions.brandLogoFile.name,
            data: await fileToBase64(state.renderOptions.brandLogoFile),
          };
        }
      }

      if (state.engine === 'maziao') {
        payload.voice = currentMaziaoVoice();
        payload.ttsMode = currentMaziaoTtsMode();
        const ttsConfig = currentMaziaoTtsConfig();
        if (ttsConfig) payload.ttsConfig = ttsConfig;
        payload.force = $('#maziaoForce')?.checked;
        payload.rebuildAudioCache = payload.force;
        setRenderState(
          tr('Đang chuẩn bị render', 'Preparing to render'),
          [payload.ttsMode === 'multiSpeakers'
            ? tr(
              'Maziao multi-speakers tạo voiceover theo voice của từng segment; sau đó hệ thống căn lại từng câu và từng từ.',
              'Maziao multi-speakers creates a voiceover per segment voice; the system then realigns each sentence and word.',
            )
            : tr(
              'OncoinX tạo voiceover từ kịch bản hiện tại; sau đó hệ thống căn lại từng câu và từng từ.',
              'OncoinX generates a voiceover from the current script; the system then realigns each sentence and word.',
            )],
        );
      } else if (state.engine === 'vieneu' || state.engine === 'aurextts') {
        payload.voice = currentVieneuVoice();
        payload.mode = $('#vieneuMode')?.value || 'v3turbo';
        payload.device = $('#vieneuDevice')?.value || 'cpu';
        payload.force = $('#vieneuForce')?.checked || $('#aurexttsForce')?.checked;
        payload.rebuildAudioCache = payload.force;
        setRenderState(
          tr('Đang chuẩn bị render', 'Preparing to render'),
          [tr(
            'VieNeu-TTS v3 Turbo tạo voiceover chất lượng cao (48kHz); Whisper sẽ căn lại từng câu và từng từ.',
            'VieNeu-TTS v3 Turbo creates high-quality 48kHz voiceover; Whisper will realign each sentence and word.',
          )],
        );
      } else {
        payload.voice = currentEdgeVoice();
        payload.force = $('#edgeForce')?.checked;
        payload.rebuildAudioCache = payload.force;
        setRenderState(
          tr('Đang chuẩn bị render', 'Preparing to render'),
          [tr(
            'Edge TTS tạo một voiceover đầy đủ; Whisper sẽ căn lại từng câu và từng từ.',
            'Edge TTS creates one full voiceover; Whisper will realign each sentence and word.',
          )],
        );
      }

      const response = await fetch('/api/render', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);

      updateTrialExportUsage(data);

      state.jobId = data.id;
      if (stopButton) {
        stopButton.hidden = false;
        stopButton.disabled = false;
      }
      const progress = renderProgressForJob(data);
      setStatus(`Đã bắt đầu job render: ${data.id}`, 'good');
      setRenderState(renderTitle(progress.title, data), [data.logs || progress.log], 'running', progress);
      refreshProjectStatuses().catch(() => {});
      await pollJob();
      if (state.pollTimer) window.clearInterval(state.pollTimer);
      state.pollTimer = window.setInterval(pollJob, 1500);
    } catch (error) {
      state.busy = false;
      const message = error.message || String(error);
      const displayMessage = isTrialUpgradeError(message) ? trialLimitMessage() : message;
      const failTitle = tr('Không thể bắt đầu render', 'Could not start render');
      setStatus(displayMessage, 'bad');
      setRenderState(failTitle, [displayMessage], 'failed');
      if (isTrialUpgradeError(message)) showTrialUpgradePrompt(message);
      if (startButton) startButton.disabled = false;
      if (stopButton) {
        stopButton.hidden = true;
        stopButton.disabled = true;
      }
    }
  }

  async function stopRender() {
    if (!state.jobId || state.canceling) return;
    const confirmed = window.confirm(tr(
      'Dừng job render đang chạy? Audio/video đang tạo dở có thể còn lại trong thư mục output.',
      'Stop the running render job? Partial audio/video files may remain in the output folder.',
    ));
    if (!confirmed) return;
    const stopButton = $('#stopRender');
    if (stopButton) stopButton.disabled = true;
    state.canceling = true;
    setStatus(tr('Đang dừng render...', 'Stopping render…'), 'warn');
    setRenderState(tr('Đang dừng render...', 'Stopping render…'), [tr('Đang gửi tín hiệu dừng job render.', 'Sending stop signal to the render job.')]);
    try {
      const response = await fetch(`/api/jobs/${state.jobId}/cancel`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({}),
      });
      const job = await response.json();
      if (!response.ok) throw new Error(job.error || `HTTP ${response.status}`);
      const progress = renderProgressForJob(job);
      setRenderState(renderTitle(progress.title, job), [job.logs || progress.log], job.status === 'cancelled' ? 'cancelled' : 'running', progress);
      refreshProjectStatuses().catch(() => {});
    } catch (error) {
      state.canceling = false;
      if (stopButton) stopButton.disabled = false;
      setStatus(error.message || String(error), 'bad');
      setRenderState(tr('Không thể dừng render', 'Could not stop render'), [error.message || String(error)], 'failed');
    }
  }

  async function pollJob() {
    if (!state.jobId) return;
    const startButton = $('#startRender');
    const stopButton = $('#stopRender');
    const videoLink = $('#videoLink');

    try {
      const response = await fetch(`/api/jobs/${state.jobId}`, { cache: 'no-store' });
      const job = await response.json();
      if (!response.ok) throw new Error(job.error || `HTTP ${response.status}`);

      updateTrialExportUsage(job);

      const progress = renderProgressForJob(job);
      if (job.status === 'done') {
        if (state.pollTimer) window.clearInterval(state.pollTimer);
        state.pollTimer = null;
        state.busy = false;
        state.canceling = false;
        if (startButton) startButton.disabled = false;
        if (stopButton) {
          stopButton.hidden = true;
          stopButton.disabled = true;
        }
        const finalUrl = job.video_url || state.outputUrl;
        setStatus(tr('Render hoàn tất. final_video.mp4 đã sẵn sàng.', 'Render complete. final_video.mp4 is ready.'), 'good');
        setRenderState(renderTitle(progress.title, job), [job.logs || progress.log], 'done', progress);
        refreshProjectStatuses().catch(() => {});
        if (videoLink) {
          videoLink.href = videoPageUrl(job.project || state.project);
          videoLink.hidden = false;
        }
        setRevealOutputButton(job.project || state.project, true);
        markProjectHasOutput(job.project || state.project, finalUrl);
        await showUploadPanel(job.project || state.project, finalUrl);
      } else if (job.status === 'failed') {
        if (state.pollTimer) window.clearInterval(state.pollTimer);
        state.pollTimer = null;
        state.busy = false;
        state.canceling = false;
        if (startButton) startButton.disabled = false;
        if (stopButton) {
          stopButton.hidden = true;
          stopButton.disabled = true;
        }
        setStatus(progress.log || tr('Render thất bại.', 'Render failed.'), 'bad');
        setRenderState(renderTitle(progress.title, job), [job.logs || progress.log], 'failed', progress);
        refreshProjectStatuses().catch(() => {});
      } else if (job.status === 'cancelled') {
        if (state.pollTimer) window.clearInterval(state.pollTimer);
        state.pollTimer = null;
        state.busy = false;
        state.canceling = false;
        if (startButton) startButton.disabled = false;
        if (stopButton) {
          stopButton.hidden = true;
          stopButton.disabled = true;
        }
        setStatus(tr('Đã dừng render.', 'Render stopped.'), 'warn');
        setRenderState(renderTitle(progress.title, job), [job.logs || progress.log], 'cancelled', progress);
        refreshProjectStatuses().catch(() => {});
      } else {
        if (stopButton) {
          stopButton.hidden = false;
          stopButton.disabled = job.status === 'cancelling' || state.canceling;
        }
        setStatus(job.status === 'queued'
          ? tr('Render đang chờ xử lý...', 'Render is queued…')
          : tr('Render đang chạy...', 'Render is running…'), 'warn');
        setRenderState(renderTitle(progress.title, job), [job.logs || progress.log], 'running', progress);
        refreshProjectStatuses().catch(() => {});
      }
    } catch (error) {
      if (state.pollTimer) window.clearInterval(state.pollTimer);
      state.pollTimer = null;
      state.busy = false;
      state.canceling = false;
      if (startButton) startButton.disabled = false;
      if (stopButton) {
        stopButton.hidden = true;
        stopButton.disabled = true;
      }
      setStatus(error.message || String(error), 'bad');
      setRenderState(tr('Lỗi khi đọc trạng thái render', 'Failed to read render status'), [error.message || String(error)], 'failed');
    }
  }

  $all('[data-engine]').forEach((button) => {
    button.addEventListener('click', () => setEngine(button.dataset.engine));
  });

  syncAdvancedSettings();
  syncMaziaoTtsMode();
  const maziaoTtsModeInput = $('#maziaoTtsMode');
  if (maziaoTtsModeInput) maziaoTtsModeInput.addEventListener('change', syncMaziaoTtsMode);
  loadMaziaoFavourites();
  loadVieneuConfig().then(() => loadVieneuVoices());
  const vieneuVoiceSelect = $('#vieneuVoice') || $('#aurexttsVoice');
  if (vieneuVoiceSelect) vieneuVoiceSelect.addEventListener('change', () => {
    saveRememberedTtsVoice();
  });
  const checkVieneuButton = $('#checkVieneu') || $('#checkAurextts');
  if (checkVieneuButton) checkVieneuButton.addEventListener('click', async () => {
    try {
      const response = await fetch('/api/tts/vieneu/health', { cache: 'no-store' });
      const health = await response.json();
      const stateEl = $('#vieneuConfigState') || $('#aurexttsConfigState');
      if (health.ok) {
        if (stateEl) stateEl.textContent = `VieNeu-TTS OK · ${(health.voices || []).length} giọng.`;
        loadVieneuVoices();
      } else if (stateEl) {
        stateEl.textContent = health.error || 'VieNeu-TTS chưa sẵn sàng.';
      }
    } catch (error) {
      const stateEl = $('#vieneuConfigState') || $('#aurexttsConfigState');
      if (stateEl) stateEl.textContent = error.message || String(error);
    }
  });

  const maziaoSpeakerCards = $('#maziaoSpeakerCards');
  if (maziaoSpeakerCards) {
    maziaoSpeakerCards.addEventListener('change', (event) => {
      if (event.target.closest('[data-maziao-voice-select]')) {
        stopMaziaoPreview();
        collectMaziaoSpeakerValues();
        renderMaziaoSpeakerCards();
        saveRememberedTtsVoice();
      }
    });
    maziaoSpeakerCards.addEventListener('input', (event) => {
      if (event.target.closest('[data-maziao-speed], [data-maziao-pitch]')) collectMaziaoSpeakerValues();
    });
    maziaoSpeakerCards.addEventListener('click', (event) => {
      const button = event.target.closest('[data-maziao-preview]');
      if (button) requestMaziaoPreview(button);
    });
  }

  const edgeVoiceInput = $('#edgeVoice');
  if (edgeVoiceInput) {
    edgeVoiceInput.addEventListener('change', () => {
      saveRememberedTtsVoice();
    });
  }

  $all('.speed-preset[data-speed]').forEach((button) => {
    button.addEventListener('click', () => {
      const speedInput = $('#renderSpeed');
      if (speedInput) speedInput.value = button.dataset.speed;
      syncSpeedPresets();
    });
  });

  const speedInput = $('#renderSpeed');
  if (speedInput) speedInput.addEventListener('input', syncSpeedPresets);

  $all('.speed-preset[data-volume]').forEach((button) => {
    button.addEventListener('click', () => {
      const volumeInput = $('#renderVolume');
      if (volumeInput) volumeInput.value = button.dataset.volume;
      syncVolumePresets();
    });
  });

  const volumeInput = $('#renderVolume');
  if (volumeInput) volumeInput.addEventListener('input', syncVolumePresets);
  syncVolumePresets();

  loadRenderPreferences();
  loadBrandingConfig().catch((error) => setStatus(error.message || String(error), 'bad'));

  const renderBranding = $('#renderBranding');
  if (renderBranding && !renderBranding.disabled) renderBranding.addEventListener('change', () => {
    saveRenderPreferences();
    setStatus('Đã tự lưu tuỳ chọn Logo + brand.', 'good');
  });

  const brandLogoInput = $('#brandLogoFile');
  const brandLogoFileName = $('#brandLogoFileName');
  if (brandLogoInput && brandLogoFileName) {
    brandLogoInput.addEventListener('change', () => {
      const file = brandLogoInput.files?.[0] || null;
      brandLogoFileName.textContent = file ? file.name : (state.renderOptions.brandLogoName || 'Logo AurexVideo mặc định');
    });
  }

  const openBrandConfigButton = $('#openBrandConfig');
  if (openBrandConfigButton && !openBrandConfigButton.disabled) openBrandConfigButton.addEventListener('click', openBrandConfigModal);
  const saveBrandConfigButton = $('#saveBrandConfig');
  if (saveBrandConfigButton) saveBrandConfigButton.addEventListener('click', saveBrandConfig);
  ['#closeBrandConfig', '#cancelBrandConfig'].forEach((selector) => {
    const button = $(selector);
    if (button) button.addEventListener('click', closeBrandConfigModal);
  });
  const brandConfigModal = $('#brandConfigModal');
  if (brandConfigModal) brandConfigModal.addEventListener('click', (event) => {
    if (event.target === brandConfigModal) closeBrandConfigModal();
  });

  const projectSelect = $('#projectSelect');
  if (projectSelect) {
    projectSelect.addEventListener('change', () => setSelectedProject(projectSelect.value));
  }

  $all('.select-btn').forEach((button) => {
    button.addEventListener('click', () => setSelectedProject(button.dataset.project));
  });

  $all('.project-rename-button[data-project]').forEach((button) => {
    button.addEventListener('click', () => openRenameProjectModal(button.dataset.project, button));
  });

  $all('.project-duplicate-button[data-project]').forEach((button) => {
    button.addEventListener('click', () => duplicateProject(button.dataset.project, button));
  });

  const renameProjectInput = $('#renameProjectInput');
  if (renameProjectInput) renameProjectInput.addEventListener('input', syncRenameProjectValidation);

  const renameProjectForm = $('#renameProjectForm');
  if (renameProjectForm) {
    renameProjectForm.addEventListener('submit', async (event) => {
      event.preventDefault();
      if (!syncRenameProjectValidation()) return;
      const submit = $('#renameProjectSubmit');
      const nextName = slugifyProjectName(renameProjectInput.value);
      renameProjectInput.value = nextName;
      if (renameProjectTrigger) renameProjectTrigger.disabled = true;
      await renameProject(renameProjectTarget, nextName, submit);
    });
  }

  ['#closeRenameProject', '#cancelRenameProject'].forEach((selector) => {
    const button = $(selector);
    if (button) button.addEventListener('click', closeRenameProjectModal);
  });

  const renameProjectModal = $('#renameProjectModal');
  if (renameProjectModal) {
    renameProjectModal.addEventListener('click', (event) => {
      if (event.target === renameProjectModal) closeRenameProjectModal();
    });
  }

  $all('.delete-output-btn[data-project]').forEach((button) => {
    button.addEventListener('click', () => openDeleteChoice(button.dataset.project));
  });

  $all('.copy-script-btn[data-project]').forEach((button) => {
    button.addEventListener('click', () => copyProjectScript(button.dataset.project, button));
  });

  const deleteSelectedOutput = $('#deleteSelectedOutput');
  if (deleteSelectedOutput) {
    deleteSelectedOutput.addEventListener('click', () => deleteOutput(state.project));
  }

  const revealOutputButton = $('#revealOutput');
  if (revealOutputButton) {
    revealOutputButton.addEventListener('click', () => revealOutput(revealOutputButton.dataset.project || state.project));
  }

  const stopRenderButton = $('#stopRender');
  if (stopRenderButton) {
    stopRenderButton.addEventListener('click', stopRender);
  }

  function scheduleIsoValue(input, label) {
    if (!input) return '';
    if (!input.value) throw new Error(`Chưa chọn thời gian hẹn đăng cho ${label}.`);
    const date = new Date(input.value);
    if (Number.isNaN(date.getTime())) throw new Error(`Thời gian hẹn đăng ${label} không hợp lệ.`);
    if (date.getTime() <= Date.now()) throw new Error(`Thời gian hẹn đăng ${label} phải ở tương lai.`);
    return date.toISOString();
  }

  function scheduleLocalValue(date) {
    const pad = (n) => String(n).padStart(2, '0');
    return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
  }

  function scheduleActive() {
    return Boolean($('#youtubeScheduleToggle')?.checked || $('#facebookScheduleToggle')?.checked);
  }

  async function uploadYoutubeVideo(project) {
    const scheduleEnabled = Boolean($('#youtubeScheduleToggle')?.checked);
    const scheduledPublishAt = scheduleEnabled ? scheduleIsoValue($('#youtubeScheduleTime'), 'YouTube') : '';
    const response = await fetch('/api/social/youtube/upload', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        project,
        title: $('#uploadTitle')?.value || '',
        description: $('#youtubeDescription')?.value || '',
        tags: state.defaultUploadTags,
        privacyStatus: scheduleEnabled ? 'private' : ($('#youtubePrivacy')?.value || 'public'),
        ...(scheduledPublishAt ? { scheduledPublishAt } : {}),
      }),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
    return data;
  }

  let binanceConfigSaving = false;
  function openBinanceConfigModal() {
    const modal = $('#binanceConfigModal');
    const keyInput = $('#binanceApiKey');
    if (!modal) return;
    modal.hidden = false;
    if (keyInput) {
      keyInput.value = '';
      keyInput.focus();
    }
  }

  function closeBinanceConfigModal() {
    const modal = $('#binanceConfigModal');
    const keyInput = $('#binanceApiKey');
    if (keyInput) keyInput.value = '';
    if (modal) modal.hidden = true;
  }

  async function saveBinanceConfig() {
    const keyInput = $('#binanceApiKey');
    const button = $('#saveBinanceConfig');
    const apiKey = keyInput?.value.trim() || '';
    if (!apiKey) {
      setUploadStatus('Nhập OpenAPI key trước khi lưu.', 'bad');
      return;
    }
    if (binanceConfigSaving) return;
    binanceConfigSaving = true;
    if (button) button.disabled = true;
    setUploadStatus('Đang lưu cấu hình Binance Square...', 'warn');
    try {
      const response = await fetch('/api/social/binance/config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ apiKey }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
      if (keyInput) keyInput.value = '';
      await refreshSocialStatus();
      setUploadStatus('Đã lưu OpenAPI key Binance Square trong ứng dụng.', 'good');
      closeBinanceConfigModal();
    } catch (error) {
      setUploadStatus(error.message || String(error), 'bad');
    } finally {
      binanceConfigSaving = false;
      if (button) button.disabled = false;
    }
  }

  async function disconnectBinanceConfig() {
    const button = $('#disconnectBinance');
    if (button) button.disabled = true;
    setUploadStatus('Đang gỡ cấu hình Binance Square...', 'warn');
    try {
      const response = await fetch('/api/social/binance/disconnect', { method: 'POST' });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
      await refreshSocialStatus();
      setUploadStatus('Đã gỡ cấu hình Binance Square.', 'good');
      closeBinanceConfigModal();
    } catch (error) {
      setUploadStatus(error.message || String(error), 'bad');
    } finally {
      if (button) button.disabled = false;
    }
  }

  async function uploadBinanceVideo(project) {
    const duration = parseFloat($('#binanceDuration')?.value || '0') || 0;
    const text = ($('#binanceCaption')?.value || '').trim();
    if (!duration || duration <= 0) {
      setUploadStatus('Nhập thời lượng video (giây) hợp lệ trước khi đăng Binance.', 'bad');
      return null;
    }
    setUploadStatus('Đang upload Binance Square...', 'warn');
    const response = await fetch('/api/social/binance/upload', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ project, duration, text }),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
    return data;
  }

  async function uploadFacebookReel(project, facebookVideoState = $('#facebookVideoState')?.value || 'PUBLISHED') {
    const scheduledPublishAt = $('#facebookScheduleToggle')?.checked ? scheduleIsoValue($('#facebookScheduleTime'), 'Facebook') : '';
    const response = await fetch('/api/social/facebook/upload', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        project,
        facebookCaption: $('#facebookCaption')?.value || '',
        facebookVideoState,
        ...(scheduledPublishAt ? { scheduledPublishAt } : {}),
      }),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
    return data;
  }

  async function uploadTiktokVideo(project) {
    const scheduledPublishAt = $('#tiktokScheduleToggle')?.checked ? scheduleIsoValue($('#tiktokScheduleTime'), 'TikTok') : '';
    const response = await fetch('/api/social/tiktok/upload', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ project, tiktokCaption: $('#tiktokCaption')?.value || '', ...(scheduledPublishAt ? { scheduledPublishAt, scheduleTimezone: Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC' } : {}) }) });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
    return data;
  }

  async function uploadInstagramReel(project) {
    const response = await fetch('/api/social/instagram/upload', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        project,
        instagramCaption: $('#instagramCaption')?.value || '',
      }),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
    return data;
  }

  async function uploadThreadsVideo(project) {
    const response = await fetch('/api/social/threads/upload', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        project,
        threadsText: $('#threadsText')?.value || '',
      }),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
    return data;
  }

  async function publishMetaAll(project) {
    const scheduledPublishAt = $('#facebookScheduleToggle')?.checked ? scheduleIsoValue($('#facebookScheduleTime'), 'Facebook') : '';
    const response = await fetch('/api/social/publish-all', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        project,
        instagramCaption: $('#instagramCaption')?.value || '',
        facebookCaption: $('#facebookCaption')?.value || '',
        threadsText: $('#threadsText')?.value || '',
        facebookSourceComment: $('#facebookSourceComment')?.value || '',
        facebookVideoState: $('#facebookVideoState')?.value || 'PUBLISHED',
        ...(scheduledPublishAt ? { scheduledPublishAt } : {}),
      }),
    });
    const data = await response.json();
    if (!response.ok && !data.platforms) throw new Error(data.error || `HTTP ${response.status}`);
    return data;
  }

  async function commentFacebookSource(project) {
    const response = await fetch('/api/social/facebook/comment-source', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        project,
        sourceCommentTargetId: state.facebookCommentTargetId,
        facebookSourceComment: $('#facebookSourceComment')?.value || '',
      }),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
    return data;
  }

  async function saveFacebookConfig() {
    const pageIdInput = $('#facebookPageId');
    const tokenInput = $('#facebookPageAccessToken');
    const button = $('#saveFacebookConfig');
    const pageId = pageIdInput?.value.trim() || '';
    const pageAccessToken = tokenInput?.value.trim() || '';
    if (!pageId || !pageAccessToken) {
      setUploadStatus('Nhập đủ Facebook Page ID và Page access token trước khi lưu.', 'bad');
      return;
    }
    if (facebookConfigSaving) return;
    facebookConfigSaving = true;
    if (button) button.disabled = true;
    setUploadStatus('Đang lưu cấu hình Facebook...', 'warn');
    try {
      const response = await fetch('/api/social/facebook/config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ pageId, pageAccessToken }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
      if (tokenInput) tokenInput.value = '';
      if (pageIdInput) pageIdInput.value = data.active_page_id || pageId;
      await refreshSocialStatus();
      setUploadStatus('Đã lưu Facebook Page ID và Page access token trong ứng dụng.', 'good');
      closeFacebookConfigModal();
    } catch (error) {
      setUploadStatus(error.message || String(error), 'bad');
    } finally {
      facebookConfigSaving = false;
      if (button) button.disabled = false;
    }
  }

  async function startYoutubeConnection() {
    const project = state.uploadProject || state.project;
    const button = $('#connectYoutube');
    if (!project) {
      setUploadStatus('Vui lòng render hoặc chọn project trước.', 'bad');
      return;
    }
    if (button) button.disabled = true;
    setUploadStatus('Đang mở Google OAuth trong trình duyệt mặc định...', 'warn');
    try {
      const response = await fetch(`/api/social/youtube/connect-url?project=${encodeURIComponent(project)}`, { cache: 'no-store' });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
      if (!data.url) throw new Error('Không nhận được đường dẫn Google OAuth.');
      await openExternalUrl(data.url);
      setUploadStatus('Đã mở Google OAuth trong trình duyệt mặc định. Kết nối xong quay lại ứng dụng để upload.', 'warn');
    } catch (error) {
      setUploadStatus(error.message || String(error), 'bad');
    } finally {
      if (button) button.disabled = false;
    }
  }

  async function saveYoutubeConfig() {
    const clientIdInput = $('#youtubeClientId');
    const clientSecretInput = $('#youtubeClientSecret');
    const redirectInput = $('#youtubeRedirectUri');
    const button = $('#saveYoutubeConfig');
    const clientId = clientIdInput?.value.trim() || '';
    const clientSecret = clientSecretInput?.value.trim() || '';
    const redirectUri = redirectInput?.value.trim() || '';
    if (!clientId || !clientSecret) {
      setUploadStatus('Nhập đủ OAuth Client ID và Client Secret trước khi lưu.', 'bad');
      return;
    }
    if (youtubeConfigSaving) return;
    youtubeConfigSaving = true;
    if (button) button.disabled = true;
    setUploadStatus('Đang lưu OAuth key YouTube...', 'warn');
    try {
      const response = await fetch('/api/social/youtube/config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ clientId, clientSecret, redirectUri }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
      state.socialConfigured.youtube = true;
      state.youtubeRedirectUri = data.redirect_uri || redirectUri;
      closeYoutubeConfigModal();
      await refreshSocialStatus();
      await startYoutubeConnection();
    } catch (error) {
      setUploadStatus(error.message || String(error), 'bad');
    } finally {
      youtubeConfigSaving = false;
      if (button) button.disabled = false;
    }
  }

  const connectYoutube = $('#connectYoutube');
  if (connectYoutube) {
    connectYoutube.addEventListener('click', async () => {
      if (!state.socialConfigured.youtube) {
        openYoutubeConfigModal();
        return;
      }
      await startYoutubeConnection();
    });
  }

  const uploadYoutube = $('#uploadYoutube');
  if (uploadYoutube) {
    uploadYoutube.addEventListener('click', async () => {
      const project = state.uploadProject || state.project;
      if (!project) {
        setUploadStatus('Vui lòng render project trước.', 'bad');
        return;
      }
      uploadYoutube.disabled = true;
      setUploadResult('');
      setUploadStatus('Đang upload lên YouTube...', 'warn');
      try {
        const data = await uploadYoutubeVideo(project);
        setUploadStatus('Upload YouTube xong. Mở Studio để kiểm tra và publish nếu cần.', 'good');
        setUploadResult(data.message || 'Uploaded.', 'good', [
          { label: 'Mở video', href: data.url },
          { label: 'Mở Studio', href: data.studio_url },
        ]);
      } catch (error) {
        setUploadStatus(error.message || String(error), 'bad');
      } finally {
        await refreshSocialStatus();
      }
    });
  }

  const uploadFacebook = $('#uploadFacebook');
  if (uploadFacebook) {
    uploadFacebook.addEventListener('click', async () => {
      const project = state.uploadProject || state.project;
      if (!project) {
        setUploadStatus('Vui lòng render project trước.', 'bad');
        return;
      }
      const facebookVideoState = $('#facebookVideoState')?.value || 'PUBLISHED';
      uploadFacebook.disabled = true;
      setUploadResult('');
      setUploadStatus(`Đang upload Facebook Reels (${facebookVideoState})...`, 'warn');
      try {
        const data = await uploadFacebookReel(project, facebookVideoState);
        state.facebookCommentTargetId = facebookVideoState === 'PUBLISHED' ? (data.source_comment_target_id || data.post_id || data.video_id || '') : '';
        updateFacebookCommentButton();
        setUploadStatus(data.message || 'Upload Facebook Reels xong.', 'good');
        const links = [];
        if (data.url) links.push({ label: 'Mở Reel/Post', href: data.url });
        setUploadResult(data.message || 'Uploaded.', 'good', links);
        const sourceComment = ($('#facebookSourceComment')?.value || '').trim();
        if (facebookVideoState === 'PUBLISHED' && sourceComment && state.facebookCommentTargetId) {
          commentFacebookSourceWithDelay(project, Date.now())
            .then((commentData) => {
              setUploadStatus(commentData.message || 'Đã comment nguồn lên Facebook.', 'good');
              setUploadResult(commentData.message || 'Đã comment nguồn lên Facebook.', 'good', links);
            })
            .catch((commentError) => {
              setUploadStatus(commentError.message || String(commentError), 'bad');
            });
        }
      } catch (error) {
        setUploadStatus(error.message || String(error), 'bad');
      } finally {
        await refreshSocialStatus();
      }
    });
  }

  const commentFacebookSourceButton = $('#commentFacebookSource');
  if (commentFacebookSourceButton) {
    commentFacebookSourceButton.addEventListener('click', async () => {
      const project = state.uploadProject || state.project;
      if (!project) {
        setUploadStatus('Vui lòng render project trước.', 'bad');
        return;
      }
      if (!state.facebookCommentTargetId) {
        setUploadStatus('Upload Facebook Reels ở trạng thái Publish now trước, rồi bấm Comment source.', 'bad');
        return;
      }
      if (!(($('#facebookSourceComment')?.value || '').trim())) {
        setUploadStatus('Source comment đang trống.', 'bad');
        return;
      }
      commentFacebookSourceButton.disabled = true;
      setUploadStatus('Đang comment source lên Facebook...', 'warn');
      try {
        const data = await commentFacebookSource(project);
        setUploadStatus(data.message || 'Comment source xong.', 'good');
        setUploadResult(data.message || 'Source comment posted.', 'good');
      } catch (error) {
        setUploadStatus(error.message || String(error), 'bad');
      } finally {
        updateFacebookCommentButton();
      }
    });
  }

  const saveFacebookConfigButton = $('#saveFacebookConfig');
  if (saveFacebookConfigButton) {
    saveFacebookConfigButton.addEventListener('click', saveFacebookConfig);
  }

  const uploadTiktok = $('#uploadTiktok');
  if (uploadTiktok) {
    uploadTiktok.addEventListener('click', async () => {
      const project = state.uploadProject || state.project;
      if (!project) return setUploadStatus('Vui lòng render project trước.', 'bad');
      uploadTiktok.disabled = true; setUploadStatus('Đang upload TikTok qua Zernio...', 'warn');
      try { const data = await uploadTiktokVideo(project); setUploadStatus(data.message || 'Đăng TikTok xong.', 'good'); setUploadResult(data.message || 'Uploaded.', 'good', data.url ? [{ label: 'Mở TikTok', href: data.url }] : []); } catch (error) { setUploadStatus(error.message || String(error), 'bad'); } finally { await refreshSocialStatus(); }
    });
  }

  const openTiktokConfig = $('#openTiktokConfig');
  if (openTiktokConfig) openTiktokConfig.addEventListener('click', async () => {
    const apiKey = window.prompt('Zernio API key:'); if (!apiKey) return;
    const accountId = window.prompt('TikTok account ID trong Zernio:'); if (!accountId) return;
    try { const response = await fetch('/api/social/tiktok/config', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ apiKey, accountId }) }); const data = await response.json(); if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`); setUploadStatus('Đã lưu cấu hình Zernio TikTok.', 'good'); await refreshSocialStatus(); } catch (error) { setUploadStatus(error.message || String(error), 'bad'); }
  });

  const uploadInstagram = $('#uploadInstagram');
  if (uploadInstagram) {
    uploadInstagram.addEventListener('click', async () => {
      const project = state.uploadProject || state.project;
      if (!project) {
        setUploadStatus('Vui lòng render project trước.', 'bad');
        return;
      }
      uploadInstagram.disabled = true;
      setUploadResult('');
      setUploadStatus('Đang upload Instagram Reels qua R2...', 'warn');
      try {
        const data = await uploadInstagramReel(project);
        const links = [];
        if (data.url) links.push({ label: 'Mở Instagram Reel', href: data.url });
        setUploadStatus(data.message || 'Upload Instagram Reels xong.', 'good');
        setUploadResult(data.message || 'Uploaded.', 'good', links);
      } catch (error) {
        setUploadStatus(error.message || String(error), 'bad');
      } finally {
        await refreshSocialStatus();
      }
    });
  }

  const uploadThreads = $('#uploadThreads');
  if (uploadThreads) {
    uploadThreads.addEventListener('click', async () => {
      const project = state.uploadProject || state.project;
      if (!project) {
        setUploadStatus('Vui lòng render project trước.', 'bad');
        return;
      }
      uploadThreads.disabled = true;
      setUploadResult('');
      setUploadStatus('Đang upload Threads...', 'warn');
      try {
        const data = await uploadThreadsVideo(project);
        const links = [];
        if (data.url) links.push({ label: 'Mở Threads', href: data.url });
        setUploadStatus(data.message || 'Upload Threads xong.', 'good');
        setUploadResult(data.message || 'Uploaded.', 'good', links);
      } catch (error) {
        setUploadStatus(error.message || String(error), 'bad');
      } finally {
        await refreshSocialStatus();
      }
    });
  }

  const openInstagramConfigButton = $('#openInstagramConfig');
  if (openInstagramConfigButton) openInstagramConfigButton.addEventListener('click', openInstagramConfigModal);
  ['#closeInstagramConfig', '#cancelInstagramConfig'].forEach((selector) => {
    const button = $(selector);
    if (button) button.addEventListener('click', closeInstagramConfigModal);
  });
  const instagramConfigModal = $('#instagramConfigModal');
  if (instagramConfigModal) {
    instagramConfigModal.addEventListener('click', (event) => {
      if (event.target === instagramConfigModal) closeInstagramConfigModal();
    });
  }
  const saveInstagramConfigButton = $('#saveInstagramConfig');
  if (saveInstagramConfigButton) saveInstagramConfigButton.addEventListener('click', saveInstagramConfig);

  const openThreadsConfigButton = $('#openThreadsConfig');
  if (openThreadsConfigButton) openThreadsConfigButton.addEventListener('click', openThreadsConfigModal);
  ['#closeThreadsConfig', '#cancelThreadsConfig'].forEach((selector) => {
    const button = $(selector);
    if (button) button.addEventListener('click', closeThreadsConfigModal);
  });
  const threadsConfigModal = $('#threadsConfigModal');
  if (threadsConfigModal) {
    threadsConfigModal.addEventListener('click', (event) => {
      if (event.target === threadsConfigModal) closeThreadsConfigModal();
    });
  }
  const saveThreadsConfigButton = $('#saveThreadsConfig');
  if (saveThreadsConfigButton) saveThreadsConfigButton.addEventListener('click', saveThreadsConfig);

  const openBinanceConfigButton = $('#openBinanceConfig');
  if (openBinanceConfigButton) {
    openBinanceConfigButton.addEventListener('click', openBinanceConfigModal);
  }
  ['#closeBinanceConfig'].forEach((selector) => {
    const button = $(selector);
    if (button) button.addEventListener('click', closeBinanceConfigModal);
  });
  const binanceConfigModal = $('#binanceConfigModal');
  if (binanceConfigModal) {
    binanceConfigModal.addEventListener('click', (event) => {
      if (event.target === binanceConfigModal) closeBinanceConfigModal();
    });
  }
  const saveBinanceConfigButton = $('#saveBinanceConfig');
  if (saveBinanceConfigButton) {
    saveBinanceConfigButton.addEventListener('click', saveBinanceConfig);
  }
  const disconnectBinanceButton = $('#disconnectBinance');
  if (disconnectBinanceButton) {
    disconnectBinanceButton.addEventListener('click', disconnectBinanceConfig);
  }
  const binanceDurationField = $('#binanceDuration');
  if (binanceDurationField) {
    binanceDurationField.addEventListener('input', () => { binanceDurationField.dataset.touched = '1'; });
  }
  const uploadBinanceButton = $('#uploadBinance');
  if (uploadBinanceButton) {
    uploadBinanceButton.addEventListener('click', async () => {
      const project = state.uploadProject || state.project;
      if (!project) {
        setUploadStatus('Vui lòng render hoặc chọn project trước.', 'bad');
        return;
      }
      uploadBinanceButton.disabled = true;
      try {
        const data = await uploadBinanceVideo(project);
        if (data?.url) {
          setUploadResult('Đã đăng Binance Square.', 'good', [{ label: 'Mở bài đã đăng', href: data.url }]);
        } else {
          setUploadResult('Đã đăng Binance Square.', 'good');
        }
        setUploadStatus('Upload Binance Square xong.', 'good');
      } catch (error) {
        setUploadStatus(error.message || String(error), 'bad');
      } finally {
        await refreshSocialStatus();
      }
    });
  }

  const saveYoutubeConfigButton = $('#saveYoutubeConfig');
  if (saveYoutubeConfigButton) {
    saveYoutubeConfigButton.addEventListener('click', saveYoutubeConfig);
  }

  const openYoutubeConfigButton = $('#openYoutubeConfig');
  if (openYoutubeConfigButton) {
    openYoutubeConfigButton.addEventListener('click', openYoutubeConfigModal);
  }

  const openDefaultTagsButton = $('#openDefaultTags');
  if (openDefaultTagsButton) openDefaultTagsButton.addEventListener('click', openDefaultTagsModal);

  const saveDefaultTagsButton = $('#saveDefaultTags');
  if (saveDefaultTagsButton) saveDefaultTagsButton.addEventListener('click', saveDefaultTags);

  ['#closeDefaultTags', '#cancelDefaultTags'].forEach((selector) => {
    const button = $(selector);
    if (button) button.addEventListener('click', closeDefaultTagsModal);
  });

  const defaultTagsModal = $('#defaultTagsModal');
  if (defaultTagsModal) {
    defaultTagsModal.addEventListener('click', (event) => {
      if (event.target === defaultTagsModal) closeDefaultTagsModal();
    });
  }

  ['#closeYoutubeConfig', '#cancelYoutubeConfig'].forEach((selector) => {
    const button = $(selector);
    if (button) button.addEventListener('click', closeYoutubeConfigModal);
  });

  const youtubeConfigModal = $('#youtubeConfigModal');
  if (youtubeConfigModal) {
    youtubeConfigModal.addEventListener('click', (event) => {
      if (event.target === youtubeConfigModal) closeYoutubeConfigModal();
    });
  }

  const openFacebookConfigButton = $('#openFacebookConfig');
  if (openFacebookConfigButton) {
    openFacebookConfigButton.addEventListener('click', openFacebookConfigModal);
  }

  ['#closeFacebookConfig', '#cancelFacebookConfig'].forEach((selector) => {
    const button = $(selector);
    if (button) button.addEventListener('click', closeFacebookConfigModal);
  });

  const facebookConfigModal = $('#facebookConfigModal');
  if (facebookConfigModal) {
    facebookConfigModal.addEventListener('click', (event) => {
      if (event.target === facebookConfigModal) closeFacebookConfigModal();
    });
  }

  const uploadBothPublic = $('#uploadBothPublic');
  if (uploadBothPublic) {
    uploadBothPublic.addEventListener('click', async () => {
      const project = state.uploadProject || state.project;
      if (!project) {
        setUploadStatus('Vui lòng render project trước.', 'bad');
        return;
      }
      if (scheduleActive()) {
        setUploadStatus('Tắt hẹn giờ đăng để dùng nút upload hàng loạt.', 'bad');
        updateUploadBothButton();
        return;
      }
      if ($('#youtubePrivacy')?.value !== 'public' || $('#facebookVideoState')?.value !== 'PUBLISHED') {
        setUploadStatus('Chọn YouTube Public và Reels Publish now trước.', 'bad');
        updateUploadBothButton();
        return;
      }
      const links = [];
      uploadBothPublic.disabled = true;
      if (uploadYoutube) uploadYoutube.disabled = true;
      if (uploadFacebook) uploadFacebook.disabled = true;
      setUploadResult('');
      setUploadStatus('Đang upload Facebook Reels trước...', 'warn');
      try {
        const facebookData = await uploadFacebookReel(project, 'PUBLISHED');
        const facebookUploadedAt = Date.now();
        state.facebookCommentTargetId = facebookData.source_comment_target_id || facebookData.post_id || facebookData.video_id || '';
        updateFacebookCommentButton();
        if (facebookData.url) links.push({ label: 'Mở Reel/Post', href: facebookData.url });
        setUploadStatus('Facebook Reels xong. Đang upload YouTube...', 'warn');
        const youtubeData = await uploadYoutubeVideo(project);
        if (youtubeData.url) links.push({ label: 'Mở YouTube', href: youtubeData.url });
        if (youtubeData.studio_url) links.push({ label: 'Mở Studio', href: youtubeData.studio_url });
        const sourceComment = ($('#facebookSourceComment')?.value || '').trim();
        if (sourceComment) {
          if (!state.facebookCommentTargetId) {
            throw new Error('Facebook upload xong nhưng chưa có Reel/Post ID để comment nguồn.');
          }
          const commentData = await commentFacebookSourceWithDelay(project, facebookUploadedAt);
          setUploadResult(commentData.message || 'Đã comment nguồn lên Facebook.', 'good', links);
        } else {
          setUploadResult('Đã upload public cả hai nền tảng. Source comment đang trống nên bỏ qua bước comment.', 'good', links);
        }
        setUploadStatus('Upload Facebook, YouTube và xử lý nguồn xong.', 'good');
      } catch (error) {
        setUploadStatus(error.message || String(error), 'bad');
        if (links.length) setUploadResult('Một phần đã upload xong trước khi lỗi.', 'warn', links);
      } finally {
        await refreshSocialStatus();
      }
    });
  }

  const uploadMetaAll = $('#uploadMetaAll');
  if (uploadMetaAll) {
    uploadMetaAll.addEventListener('click', async () => {
      const project = state.uploadProject || state.project;
      if (!project) {
        setUploadStatus('Vui lòng render project trước.', 'bad');
        return;
      }
      if (!state.socialReady.instagram || !state.socialReady.facebook || !state.socialReady.threads) {
        setUploadStatus('Cần cấu hình đủ Instagram, Facebook và Threads trước khi đăng chung.', 'bad');
        updateMetaAllButton();
        return;
      }
      if (scheduleActive()) {
        setUploadStatus('Tắt hẹn giờ đăng để dùng nút đăng chung.', 'bad');
        updateMetaAllButton();
        return;
      }
      if ($('#facebookVideoState')?.value !== 'PUBLISHED') {
        setUploadStatus('Chọn Facebook Publish now trước khi đăng chung.', 'bad');
        updateMetaAllButton();
        return;
      }
      uploadMetaAll.disabled = true;
      if (uploadInstagram) uploadInstagram.disabled = true;
      if (uploadFacebook) uploadFacebook.disabled = true;
      if (uploadThreads) uploadThreads.disabled = true;
      setUploadResult('');
      setUploadStatus('Đang đăng Instagram, Facebook và Threads...', 'warn');
      try {
        const data = await publishMetaAll(project);
        const links = [];
        const platformLabels = { instagram: 'Instagram', facebook: 'Facebook', threads: 'Threads' };
        const succeeded = [];
        const failed = [];
        Object.entries(platformLabels).forEach(([platform, label]) => {
          const result = data.platforms?.[platform] || {};
          if (result.ok) {
            succeeded.push(label);
            if (result.url) links.push({ label: `Mở ${label}`, href: result.url });
          } else {
            failed.push(`${label}: ${result.error || 'thất bại'}`);
          }
        });
        const commentResult = data.platforms?.facebook_comment;
        if (commentResult && !commentResult.ok) {
          failed.push(`Facebook comment: ${commentResult.error || 'thất bại'}`);
        }
        const level = data.ok ? 'good' : data.partial ? 'warn' : 'bad';
        const summary = failed.length
          ? `Đã đăng: ${succeeded.join(', ') || 'chưa có nền tảng nào'}\n${failed.join('\n')}`
          : (data.message || 'Đã đăng cả ba nền tảng.');
        setUploadStatus(data.message || summary, level);
        setUploadResult(summary, level, links);
      } catch (error) {
        setUploadStatus(error.message || String(error), 'bad');
      } finally {
        await refreshSocialStatus();
      }
    });
  }

  ['#youtubePrivacy', '#facebookVideoState'].forEach((selector) => {
    const control = $(selector);
    if (control) control.addEventListener('change', () => {
      updateUploadBothButton();
      updateMetaAllButton();
    });
  });
  function applyScheduleToggle(toggleId, rowId, timeId, isYoutube) {
    const toggle = $('#' + toggleId);
    const row = $('#' + rowId);
    const time = $('#' + timeId);
    if (!toggle || !row || !time) return;
    const enabled = toggle.checked;
    if (enabled) {
      time.min = scheduleLocalValue(new Date(Date.now() + 15 * 60 * 1000));
    } else {
      time.value = '';
    }
    row.hidden = !enabled;
    if (isYoutube) {
      const privacy = $('#youtubePrivacy');
      if (privacy) {
        privacy.disabled = enabled;
        if (enabled) privacy.value = 'private';
      }
    }
    updateUploadBothButton();
    updateMetaAllButton();
  }

  // Delegated listener: works regardless of when/where the toggle elements
  // appear in the DOM (the upload page re-renders platform cards).
  document.addEventListener('change', (event) => {
    const target = event.target;
    if (!target || typeof target.id !== 'string') return;
    if (target.id === 'youtubeScheduleToggle') {
      applyScheduleToggle('youtubeScheduleToggle', 'youtubeScheduleRow', 'youtubeScheduleTime', true);
    } else if (target.id === 'facebookScheduleToggle') {
      applyScheduleToggle('facebookScheduleToggle', 'facebookScheduleRow', 'facebookScheduleTime', false);
    } else if (target.id === 'tiktokScheduleToggle') {
      applyScheduleToggle('tiktokScheduleToggle', 'tiktokScheduleRow', 'tiktokScheduleTime', false);
    }
  });
  const facebookSourceComment = $('#facebookSourceComment');
  if (facebookSourceComment) facebookSourceComment.addEventListener('input', updateFacebookCommentButton);
  $all('[data-copy-target]').forEach((button) => {
    button.addEventListener('click', () => copyFieldValue(
      button.dataset.copyTarget || '',
      button.dataset.copyLabel || 'Nội dung này',
      button,
    ));
  });

  document.addEventListener('click', (event) => {
    const externalLink = event.target.closest('a[href]');
    if (externalLink) {
      const targetUrl = new URL(externalLink.href, window.location.href);
      if (/^https?:$/.test(targetUrl.protocol) && targetUrl.origin !== window.location.origin) {
        event.preventDefault();
        openExternalUrl(targetUrl.href).catch((error) => {
          setStatus(`Không mở được trình duyệt: ${error.message || error}`, 'bad');
        });
        return;
      }
    }
    if (!event.target.closest('.platform-account-list')) closePlatformAccountMenus();
  });

  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') {
      closePlatformAccountMenus();
      closeFacebookConfigModal();
      closeBrandConfigModal();
      closeThreadsConfigModal();
      closeRenameProjectModal();
    }
  });

  window.addEventListener('focus', () => {
    const panel = $('#uploadPanel');
    if (panel && !panel.hidden) refreshSocialStatus();
  });

  const refreshProjects = $('#refreshProjects');
  if (refreshProjects) {
    refreshProjects.addEventListener('click', () => window.location.reload());
  }

  $all('[data-source-root-select]').forEach((button) => {
    button.addEventListener('click', selectSourceRootFromFinder);
  });

  const storedTheme = localStorage.getItem('aurexvideo-theme');
  applyTheme(storedTheme || 'light');
  $all('[data-theme-toggle]').forEach((button) => {
    button.addEventListener('click', toggleTheme);
  });

  $all('.project-row[data-project]').forEach((row) => {
    row.addEventListener('click', (event) => {
      if (event.target.closest('a, button')) return;
      setSelectedProject(row.dataset.project);
    });
  });

  function syncEdgeVoiceCustomField() {
    const edgeVoice = $('#edgeVoice');
    const customField = $('#edgeVoiceCustomField');
    if (edgeVoice && customField) {
      customField.hidden = edgeVoice.value !== 'custom';
    }
  }

  const startButton = $('#startRender');
  if (startButton) startButton.addEventListener('click', startRender);

  const edgeVoice = $('#edgeVoice');
  if (edgeVoice) edgeVoice.addEventListener('change', syncEdgeVoiceCustomField);

  const initialFromUrl = new URLSearchParams(window.location.search).get('project');
  const initialProject = window.__INITIAL_PROJECT__ || initialFromUrl || projects[0]?.name;
  if (initialProject) setSelectedProject(initialProject, false);
  refreshProjectStatuses().catch(() => {});
  const projectStatusTimer = window.setInterval(() => refreshProjectStatuses().catch(() => {}), 5000);
  window.addEventListener('beforeunload', () => window.clearInterval(projectStatusTimer));
  if (typeof syncEdgeVoiceCustomField === 'function') syncEdgeVoiceCustomField();
  syncSpeedPresets();

  // Restore the render options saved by the previous render.
  async function restoreRenderPreferences() {
    let preferences = null;
    try {
      const response = await fetch('/api/render-preferences', { cache: 'no-store' });
      if (!response.ok) return;
      preferences = (await response.json()).preferences;
    } catch (error) {
      return;
    }
    if (!preferences || typeof preferences !== 'object') return;
    const applyValue = (selector, value) => {
      const field = $(selector);
      if (!field || value === undefined || value === null || value === '') return;
      field.value = String(value);
    };
    applyValue('#renderSpeed', preferences.speed);
    applyValue('#renderVolume', preferences.volume);
    applyValue('#renderSize', preferences.size);
    applyValue('#maziaoTtsMode', preferences.ttsMode);
    const branding = $('#renderBranding');
    if (branding && !branding.disabled && typeof preferences.branding === 'boolean') {
      branding.checked = preferences.branding;
    }
    const rebuildAudioCache = preferences.rebuildAudioCache ?? preferences.force;
    ['#maziaoForce', '#edgeForce'].forEach((selector) => {
      const field = $(selector);
      if (field && typeof rebuildAudioCache === 'boolean') field.checked = rebuildAudioCache;
    });
    if (preferences.engine && typeof setEngine === 'function') {
      try { setEngine(preferences.engine); } catch (error) { /* engine tab may be fixed */ }
    }
    syncSpeedPresets();
    syncMaziaoTtsMode();
    if (typeof syncEdgeVoiceCustomField === 'function') syncEdgeVoiceCustomField();
  }
  restoreRenderPreferences();
})();
