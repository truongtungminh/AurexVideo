(() => {
  const $ = (selector) => document.querySelector(selector);
  const form = $('#autoProjectForm');
  const nameInput = $('#projectName');
  const nameError = $('#nameError');
  const promptInput = $('#promptTemplate');
  const promptError = $('#promptError');
  const youtubeTitlePromptInput = $('#youtubeTitlePromptTemplate');
  const youtubeTitlePromptError = $('#youtubeTitlePromptError');
  const socialCaptionPromptInput = $('#socialCaptionPromptTemplate');
  const socialCaptionPromptError = $('#socialCaptionPromptError');
  const status = $('#formStatus');
  const submitButton = $('#submitButton');
  const characterSelect = $('#characterSelect');
  const characterCover = $('#characterCover');
  const characterMeta = $('#characterMeta');
  const voiceSelect = $('#voiceSelect');
  const voiceMeta = $('#voiceMeta');
  const voicePreview = $('#voicePreview');
  const templateSelect = $('#templateSelect');
  let characters = [];
  let voices = [];
  let currentAudio = null;
  let defaultPrompt = `Viết một kịch bản video ngắn khoảng {{duration}} giây bằng tiếng Việt về chủ đề: {{keyword}}.\n\nYêu cầu:\n- Hook rõ ở câu đầu.\n- Sentence case tự nhiên, dễ đọc bằng TTS.\n- Nội dung có mở đầu, diễn giải và kết luận.\n- Không chèn timestamp, markdown fence hoặc marker nội bộ.\n- Mỗi dòng là một câu thoại/reveal.\n- Giữ đúng thông tin, không tự bịa số liệu.`;
  let defaultYoutubeTitlePrompt = `Từ nội dung video đã được tạo và duyệt dưới đây, viết một YouTube title rõ, hấp dẫn, dưới 100 ký tự cho chủ đề {{keyword}}.\n\nNội dung đã duyệt:\n{{content}}\n\nChỉ trả về title, không thêm giải thích hoặc marker nội bộ. Bám sát nội dung, không tự bịa dữ kiện.`;
  let defaultSocialCaptionPrompt = `Từ nội dung video đã được tạo và duyệt dưới đây, viết caption dùng chung cho Facebook và YouTube về chủ đề {{keyword}}.\n\nNội dung đã duyệt:\n{{content}}\n\nViết caption dễ đọc, tóm tắt đúng nội dung, không tự bịa dữ kiện và không thêm marker hoặc giải thích nội bộ.`;

  const api = async (url, options = {}) => {
    const response = await fetch(url, {
      cache: 'no-store',
      headers: {'Content-Type': 'application/json', ...(options.headers || {})},
      ...options,
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
    return data;
  };

  const slugify = (value) => String(value || '')
    .normalize('NFD').replace(/[\u0300-\u036f]/g, '')
    .replace(/đ/g, 'd').toLowerCase()
    .replace(/[^a-z0-9]+/g, '-').replace(/-+/g, '-')
    .replace(/^-+|-+$/g, '').slice(0, 64).replace(/-+$/g, '');

  const setStatus = (message, kind = '') => {
    status.textContent = message;
    status.className = `status ${kind}`.trim();
  };

  const selectedCharacter = () => characters.find((item) => item.id === characterSelect.value) || null;
  const selectedVoice = () => voices.find((item) => item.id === voiceSelect.value) || null;

  const renderCharacter = () => {
    const character = selectedCharacter();
    characterCover.hidden = !character;
    characterCover.src = character?.coverUrl || character?.cover_url || '';
    characterCover.alt = character ? `Preview ${character.name || character.id}` : 'Character preview';
    characterMeta.textContent = character
      ? `${character.poseCount || character.poses?.length || 0} poses · id ${character.id}`
      : 'Chưa có character';
    $('#editCharacter').disabled = !character;
    $('#deleteCharacter').disabled = !character;
  };

  const renderVoice = () => {
    const voice = selectedVoice();
    if (!voice) {
      voiceMeta.textContent = 'Voice ID sẽ hiển thị ở đây';
      voicePreview.disabled = true;
      return;
    }
    voiceMeta.textContent = `Voice ID: ${voice.id}`;
    voicePreview.disabled = false;
  };

  const loadCharacters = async () => {
    const data = await api('/api/characters');
    characters = data.characters || [];
    characterSelect.innerHTML = characters.map((character) =>
      `<option value="${escapeHtml(character.id)}">${escapeHtml(character.name || character.id)}</option>`
    ).join('');
    if (!characters.length) {
      characterSelect.innerHTML = '<option value="">Chưa có character</option>';
      characterSelect.disabled = true;
    } else {
      const preferred = characters.find((item) => item.id === 'popsy') || characters[0];
      characterSelect.value = preferred.id;
    }
    renderCharacter();
  };

  const loadVoices = async () => {
    const data = await api('/api/voices/favourites');
    voices = data.data || data.voices || [];
    voiceSelect.innerHTML = voices.map((voice) =>
      `<option value="${escapeHtml(voice.id)}">${escapeHtml(voice.name || voice.id)}</option>`
    ).join('');
    if (!voices.length) {
      voiceSelect.innerHTML = '<option value="">Chưa có voice Maziao</option>';
      voiceSelect.disabled = true;
    } else {
      const preferred = voices.find((item) => item.id === 'clone_8ci7vkGMoJLyKe9IJ7MfV') || voices[0];
      voiceSelect.value = preferred.id;
    }
    renderVoice();
  };

  const loadTemplates = async () => {
    const data = await api('/api/autoproject-templates');
    const templates = data.templates || [];
    templateSelect.innerHTML = templates.map((template) =>
      `<option value="${escapeHtml(template.id)}">${escapeHtml(template.name)}${template.description ? ` — ${escapeHtml(template.description)}` : ''}</option>`
    ).join('');
    if (!templates.length) {
      templateSelect.innerHTML = '<option value="">Chưa có template native</option>';
      templateSelect.disabled = true;
    }
  };

  const escapeHtml = (value) => String(value ?? '').replace(/[&<>"']/g, (char) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  }[char]));

  nameInput.addEventListener('input', () => {
    const next = slugify(nameInput.value);
    if (next !== nameInput.value) nameInput.value = next;
    nameError.textContent = '';
    nameInput.removeAttribute('aria-invalid');
  });
  characterSelect.addEventListener('change', renderCharacter);
  voiceSelect.addEventListener('change', renderVoice);

  voicePreview.addEventListener('click', async () => {
    const voice = selectedVoice();
    if (!voice) return;
    const previewUrl = voice.previewUrl || voice.preview_url || voice.refFile || voice.ref_file;
    if (!previewUrl) {
      setStatus('Voice này chưa có link preview.', 'error');
      return;
    }
    if (currentAudio && !currentAudio.paused) {
      currentAudio.pause();
      voicePreview.classList.remove('playing');
      voicePreview.textContent = '▶';
      return;
    }
    try {
      currentAudio = currentAudio || new Audio();
      currentAudio.src = previewUrl;
      currentAudio.onended = () => {
        voicePreview.classList.remove('playing');
        voicePreview.textContent = '▶';
      };
      await currentAudio.play();
      voicePreview.classList.add('playing');
      voicePreview.textContent = 'Ⅱ';
    } catch (error) {
      setStatus(`Không phát được voice preview: ${error.message}`, 'error');
    }
  });

  $('#resetPrompt').addEventListener('click', () => {
    promptInput.value = defaultPrompt;
    promptError.textContent = '';
    promptInput.removeAttribute('aria-invalid');
  });
  $('#resetYoutubeTitlePrompt').addEventListener('click', () => {
    youtubeTitlePromptInput.value = defaultYoutubeTitlePrompt;
    youtubeTitlePromptError.textContent = '';
    youtubeTitlePromptInput.removeAttribute('aria-invalid');
  });
  $('#resetSocialCaptionPrompt').addEventListener('click', () => {
    socialCaptionPromptInput.value = defaultSocialCaptionPrompt;
    socialCaptionPromptError.textContent = '';
    socialCaptionPromptInput.removeAttribute('aria-invalid');
  });

  const forwardToCharacterManager = () => {
    setStatus('Mở thư viện character…');
    window.location.href = '/new-project';
  };
  $('#editCharacter').addEventListener('click', forwardToCharacterManager);
  $('#createCharacter').addEventListener('click', forwardToCharacterManager);
  $('#deleteCharacter').addEventListener('click', () => {
    const character = selectedCharacter();
    if (!character) return;
    if (window.confirm(`Mở thư viện để quản lý character “${character.id}”?`)) forwardToCharacterManager();
  });

  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    nameError.textContent = '';
    promptError.textContent = '';
    youtubeTitlePromptError.textContent = '';
    socialCaptionPromptError.textContent = '';
    nameInput.removeAttribute('aria-invalid');
    promptInput.removeAttribute('aria-invalid');
    youtubeTitlePromptInput.removeAttribute('aria-invalid');
    socialCaptionPromptInput.removeAttribute('aria-invalid');
    const slug = slugify(nameInput.value);
    if (!/^[a-z0-9][a-z0-9-]{1,62}[a-z0-9]$/.test(slug)) {
      nameError.textContent = 'Tên cần có ít nhất 3 ký tự và dùng chữ thường, số, dấu gạch ngang.';
      nameInput.setAttribute('aria-invalid', 'true');
      nameInput.focus();
      return;
    }
    if (!promptInput.value.includes('{{keyword}}')) {
      promptError.textContent = 'Prompt bắt buộc phải có biến {{keyword}}.';
      promptInput.setAttribute('aria-invalid', 'true');
      promptInput.focus();
      return;
    }
    if (!youtubeTitlePromptInput.value.includes('{{content}}')) {
      youtubeTitlePromptError.textContent = 'Prompt YouTube title bắt buộc phải có biến {{content}}.';
      youtubeTitlePromptInput.setAttribute('aria-invalid', 'true');
      youtubeTitlePromptInput.focus();
      return;
    }
    if (!socialCaptionPromptInput.value.includes('{{content}}')) {
      socialCaptionPromptError.textContent = 'Prompt caption Facebook/YouTube bắt buộc phải có biến {{content}}.';
      socialCaptionPromptInput.setAttribute('aria-invalid', 'true');
      socialCaptionPromptInput.focus();
      return;
    }
    if (!characterSelect.value || !voiceSelect.value || !templateSelect.value) {
      setStatus('Cần chọn character, voice và template trước khi tạo.', 'error');
      return;
    }
    submitButton.disabled = true;
    setStatus('Đang tạo AutoProject…');
    try {
      const data = await api('/api/autoprojects', {
        method: 'POST',
        body: JSON.stringify({
          id: slug,
          name: slug,
          characterId: characterSelect.value,
          voiceId: voiceSelect.value,
          templateId: templateSelect.value,
          promptTemplate: promptInput.value,
          youtubeTitlePromptTemplate: youtubeTitlePromptInput.value,
          socialCaptionPromptTemplate: socialCaptionPromptInput.value,
        }),
      });
      nameInput.value = data.autoproject.id;
      setStatus(`Đã tạo AutoProject “${data.autoproject.id}”. Trang Manager sẽ được nối ở Task 4.`, 'ok');
      submitButton.textContent = 'Đã tạo AutoProject';
    } catch (error) {
      setStatus(error.message, 'error');
      if (/tồn tại|exists|duplicate/i.test(error.message)) {
        nameError.textContent = 'Tên AutoProject đã tồn tại. Hãy chọn tên khác.';
        nameInput.setAttribute('aria-invalid', 'true');
      }
      submitButton.disabled = false;
    }
  });

  promptInput.value = defaultPrompt;
  youtubeTitlePromptInput.value = defaultYoutubeTitlePrompt;
  socialCaptionPromptInput.value = defaultSocialCaptionPrompt;
  Promise.all([loadCharacters(), loadVoices(), loadTemplates()]).catch((error) => setStatus(`Không tải được cấu hình: ${error.message}`, 'error'));
})();
