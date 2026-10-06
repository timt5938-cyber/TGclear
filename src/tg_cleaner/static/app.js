/**
 * Telegram Cleaner - Web Client Application
 * Pure modern vanilla JS. Minimalist Deep Dark aesthetic.
 * Strict adherence to domain terminology and clean architecture.
 */

// ============================================================================
// State
// ============================================================================

const state = {
  auth: {
    authorized: false,
    phone: null,
    user: null,
    mock_mode: false,
  },
  dialogs: [],
  selectedIds: new Set(),
  filterChip: 'all', // 'all', 'candidates', 'protected'
  searchQuery: '',
  sort: {
    column: 'unread_count',
    direction: 'desc',
  },
  filterConfig: {
    user_inactive_days: 60,
    unread_threshold: 200,
    dormancy_days: 90,
    logic_mode: 'ANY',
    protect_admin: true,
    protect_pinned: true,
    protect_recent_read: true,
    protect_recent_read_days: 2,
    ignore_archived: true,
    protected_folder_ids: [],
    filter_user_inactive_enabled: true,
    filter_unread_enabled: true,
    filter_dormancy_enabled: true,
  },
  historyBatches: [],
  whitelist: [],
  backups: [],
  expandedBatches: new Set(),
  batchSelectedSnapshots: {}, // batchId -> Set of snapshot IDs
  ws: null,
  activeTask: null,
};

// ============================================================================
// API Service
// ============================================================================

const api = {
  async getAuthStatus() {
    const res = await fetch('/api/auth/status');
    return await res.json();
  },
  async sendCode(phone, api_id, api_hash) {
    const res = await fetch('/api/auth/send-code', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ phone, api_id: Number(api_id), api_hash }),
    });
    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.detail || 'Ошибка отправки кода');
    }
    return await res.json();
  },
  async verifyCode(phone, code) {
    const res = await fetch('/api/auth/verify-code', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ phone, code }),
    });
    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.detail || 'Неверный код подтверждения');
    }
    return await res.json();
  },
  async verifyPassword(password) {
    const res = await fetch('/api/auth/verify-password', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ password }),
    });
    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.detail || 'Неверный 2FA пароль');
    }
    return await res.json();
  },
  async logout() {
    const res = await fetch('/api/auth/logout', { method: 'POST' });
    return await res.json();
  },
  async getDialogs() {
    const res = await fetch('/api/dialogs');
    return await res.json();
  },
  async scanDialogs(config) {
    const res = await fetch('/api/scan', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: config ? JSON.stringify(config) : undefined,
    });
    return await res.json();
  },
  async evaluateDialogs(config) {
    const res = await fetch('/api/evaluate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(config),
    });
    return await res.json();
  },
  async leaveEntities(entityIds) {
    const res = await fetch('/api/leave', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ entity_ids: entityIds }),
    });
    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.detail || 'Ошибка при выходе из сущностей');
    }
    return await res.json();
  },
  async getFolders() {
    const res = await fetch('/api/folders');
    return await res.json();
  },
  async getHistory() {
    const res = await fetch('/api/history');
    return await res.json();
  },
  async rollbackBatch(batchId, snapshotIds = null) {
    const res = await fetch(`/api/history/${batchId}/rollback`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: snapshotIds ? JSON.stringify({ snapshot_ids: snapshotIds }) : undefined,
    });
    return await res.json();
  },
  async getWhitelist() {
    const res = await fetch('/api/whitelist');
    return await res.json();
  },
  async addToWhitelist(entity_id, title, username = null) {
    const res = await fetch('/api/whitelist', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ entity_id: Number(entity_id), title, username }),
    });
    return await res.json();
  },
  async removeFromWhitelist(entity_id) {
    const res = await fetch(`/api/whitelist/${entity_id}`, { method: 'DELETE' });
    return await res.json();
  },
  async getBackups() {
    const res = await fetch('/api/backups');
    return await res.json();
  },
  async openBackupsFolder() {
    const res = await fetch('/api/backups/open-folder', { method: 'POST' });
    return await res.json();
  },
  async cancelTask() {
    const res = await fetch('/api/tasks/cancel', { method: 'POST' });
    return await res.json();
  },
  async getSettings() {
    const res = await fetch('/api/settings');
    return await res.json();
  },
};

// ============================================================================
// Notification Toasts
// ============================================================================

function showToast(message, type = 'info') {
  const container = document.getElementById('toastContainer');
  const toast = document.createElement('div');
  toast.className = `toast toast-${type}`;

  let iconSvg = '';
  if (type === 'success') {
    iconSvg = '<svg class="icon icon-sm" viewBox="0 0 24 24"><polyline points="20 6 9 17 4 12"/></svg>';
  } else if (type === 'error') {
    iconSvg = '<svg class="icon icon-sm" viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"/><line x1="15" y1="9" x2="9" y2="15"/><line x1="9" y1="9" x2="15" y2="15"/></svg>';
  } else {
    iconSvg = '<svg class="icon icon-sm" viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"/><line x1="12" y1="16" x2="12" y2="12"/><line x1="12" y1="8" x2="12.01" y2="8"/></svg>';
  }

  toast.innerHTML = `
    ${iconSvg}
    <span>${escapeHtml(message)}</span>
  `;

  container.appendChild(toast);
  setTimeout(() => {
    toast.style.opacity = '0';
    toast.style.transform = 'translateY(8px)';
    toast.style.transition = 'all 150ms ease';
    setTimeout(() => toast.remove(), 160);
  }, 3500);
}

// Escape HTML utility
function escapeHtml(str) {
  if (!str) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}

// ============================================================================
// WebSocket & Live Progress Terminal
// ============================================================================

function setupWebSocket() {
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  const wsUrl = `${protocol}//${window.location.host}/ws/events`;

  try {
    state.ws = new WebSocket(wsUrl);

    state.ws.onopen = () => {
      console.log('WebSocket connected to event stream');
    };

    state.ws.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data);
        handleWsEvent(msg);
      } catch (err) {
        console.error('Error parsing WS message:', err);
      }
    };

    state.ws.onclose = () => {
      console.warn('WebSocket closed. Reconnecting in 3s...');
      setTimeout(setupWebSocket, 3000);
    };

    state.ws.onerror = (err) => {
      console.error('WebSocket error:', err);
    };
  } catch (err) {
    console.error('Failed to init WebSocket:', err);
  }
}

function handleWsEvent(msg) {
  const drawer = document.getElementById('progressDrawer');
  const actionTitle = document.getElementById('drawerActionTitle');
  const statusText = document.getElementById('drawerStatusText');
  const counterBadge = document.getElementById('drawerCounterBadge');
  const progressFill = document.getElementById('drawerProgressFill');
  const btnCancel = document.getElementById('btnDrawerCancel');
  const btnClose = document.getElementById('btnDrawerClose');

  if (msg.type === 'start') {
    drawer.classList.add('active');
    btnCancel.style.display = 'inline-flex';
    btnClose.style.display = 'none';
    clearTerminalLogs();

    if (msg.action === 'scan') {
      actionTitle.textContent = 'Сканирование диалогов...';
      statusText.textContent = 'Поиск каналов и супергрупп';
      counterBadge.textContent = 'Сканирование...';
      progressFill.style.width = '10%';
      progressFill.className = 'progress-fill';
      addTerminalLog('Старт анализа диалогов аккаунта', 'info');
    } else if (msg.action === 'leave') {
      actionTitle.textContent = 'Физический выход из каналов';
      statusText.textContent = `Подготовка выхода из ${msg.total} сущностей`;
      counterBadge.textContent = `0 / ${msg.total}`;
      progressFill.style.width = '0%';
      progressFill.className = 'progress-fill danger';
      addTerminalLog(`Запуск сессии выхода. Снапшот сохранен в C:\\tg\\backups\\`, 'ok');
    } else if (msg.action === 'rollback') {
      actionTitle.textContent = 'Выборочный откат (Rollback)';
      statusText.textContent = 'Возврат в публичные каналы';
      counterBadge.textContent = 'В процессе...';
      progressFill.style.width = '0%';
      progressFill.className = 'progress-fill success';
      addTerminalLog('Старт операции восстановления (отката)', 'info');
    }
  } else if (msg.type === 'progress') {
    const data = msg.data || {};
    if (msg.action === 'scan') {
      statusText.textContent = `Анализируется: ${data.current || '...'}`;
      counterBadge.textContent = `Найдено: ${data.found || 0} (Всего: ${data.scanned || 0})`;
      progressFill.style.width = '50%';
      if (data.current) {
        addTerminalLog(`Обнаружен канал: ${data.current}`, 'info');
      }
    } else if (msg.action === 'leave') {
      if (data.status === 'flood_wait') {
        statusText.textContent = `Ограничение Telegram (FloodWait): пауза ${data.wait_seconds} сек.`;
        addTerminalLog(`[FloodWait] Пауза ${data.wait_seconds}с для ${data.entity}...`, 'warn');
      } else {
        const cur = data.current || 0;
        const tot = data.total || 1;
        const pct = Math.round((cur / tot) * 100);
        progressFill.style.width = `${pct}%`;
        counterBadge.textContent = `${cur} / ${tot}`;
        statusText.textContent = `Покинут: ${data.entity || '...'}`;

        if (data.status === 'failed') {
          addTerminalLog(`Ошибка выхода из ${data.entity}: ${data.error || 'неизвестно'}`, 'err');
        } else {
          addTerminalLog(`Успешно покинут: ${data.entity}`, 'ok');
        }
      }
    } else if (msg.action === 'rollback') {
      if (data.status === 'flood_wait') {
        statusText.textContent = `Ограничение Telegram: ожидание ${data.wait_seconds} сек.`;
        addTerminalLog(`[FloodWait] Ожидание ${data.wait_seconds}с...`, 'warn');
      } else if (data.status === 'unrestorable_private') {
        addTerminalLog(`[Пропуск] ${data.entity}: Приватный канал без @username`, 'warn');
      } else if (data.status === 'restored') {
        addTerminalLog(`[Восстановлен] Вступление в ${data.entity} успешно`, 'ok');
      } else if (data.status === 'failed') {
        addTerminalLog(`[Ошибка] Не удалось вступить в ${data.entity}: ${data.error}`, 'err');
      }
    }
  } else if (msg.type === 'done') {
    progressFill.style.width = '100%';
    btnCancel.style.display = 'none';
    btnClose.style.display = 'inline-flex';

    if (msg.action === 'scan') {
      actionTitle.textContent = 'Сканирование завершено';
      statusText.textContent = `Всего каналов и групп: ${msg.total}`;
      counterBadge.textContent = `Итого: ${msg.total}`;
      addTerminalLog(`Сканирование успешно завершено. Найдено: ${msg.total}`, 'ok');
      showToast(`Найдено ${msg.total} каналов и чатов`, 'success');
      loadFolders();
      loadDialogs();
    } else if (msg.action === 'leave') {
      const res = msg.result || {};
      actionTitle.textContent = 'Очистка завершена';
      statusText.textContent = `Покинуто: ${res.departed || 0}, ошибок: ${res.failed || 0}`;
      counterBadge.textContent = `${res.departed || 0} / ${res.total || 0}`;
      addTerminalLog(`Очистка завершена. Успешно: ${res.departed}, Ошибок: ${res.failed}`, 'ok');
      showToast(`Покинуто ${res.departed} каналов`, 'success');
      loadDialogs();
      loadHistory();
      loadBackups();
    } else if (msg.action === 'rollback') {
      const res = msg.result || {};
      actionTitle.textContent = 'Откат завершен';
      statusText.textContent = `Восстановлено: ${res.restored || 0}, приватных: ${res.unrestorable_private || 0}`;
      addTerminalLog(`Откат завершен. Восстановлено: ${res.restored}, Приватных: ${res.unrestorable_private}`, 'ok');
      showToast(`Восстановлено ${res.restored} публичных каналов`, 'success');
      loadHistory();
      loadBackups();
    }
  } else if (msg.type === 'cancelled') {
    actionTitle.textContent = 'Операция отменена';
    statusText.textContent = 'Действие прервано пользователем';
    btnCancel.style.display = 'none';
    btnClose.style.display = 'inline-flex';
    addTerminalLog('Операция отменена пользователем', 'warn');
    showToast('Операция отменена', 'info');
  } else if (msg.type === 'error') {
    actionTitle.textContent = 'Ошибка операции';
    statusText.textContent = msg.error || 'Произошла непредвиденная ошибка';
    btnCancel.style.display = 'none';
    btnClose.style.display = 'inline-flex';
    addTerminalLog(`Критическая ошибка: ${msg.error}`, 'err');
    showToast(msg.error || 'Ошибка операции', 'error');
  }
}

function clearTerminalLogs() {
  const logs = document.getElementById('drawerTerminalLogs');
  logs.innerHTML = '';
}

function addTerminalLog(message, level = 'info') {
  const logs = document.getElementById('drawerTerminalLogs');
  const entry = document.createElement('div');
  entry.className = 'log-entry';

  const timeStr = new Date().toLocaleTimeString('ru-RU');
  let levelClass = 'log-msg';
  if (level === 'ok') levelClass = 'log-ok';
  if (level === 'warn') levelClass = 'log-warn';
  if (level === 'err') levelClass = 'log-err';

  entry.innerHTML = `
    <span class="log-time">[${timeStr}]</span>
    <span class="${levelClass}">${escapeHtml(message)}</span>
  `;

  logs.appendChild(entry);
  logs.scrollTop = logs.scrollHeight;
}

// ============================================================================
// UI Initializers & Event Listeners
// ============================================================================

document.addEventListener('DOMContentLoaded', async () => {
  setupTabs();
  setupFilterControls();
  setupActionButtons();
  setupAuthModal();
  setupLeaveModal();
  setupDrawer();
  setupWebSocket();

  // Load initial settings and auth status
  await loadSettings();
  await checkAuthStatus();
  await loadFolders();
  await loadDialogs();
  await loadWhitelist();
});

// Tab switching
function setupTabs() {
  const tabs = document.querySelectorAll('.tab-btn');
  tabs.forEach((btn) => {
    btn.addEventListener('click', () => {
      const targetTab = btn.getAttribute('data-tab');

      tabs.forEach((t) => {
        t.classList.remove('active');
        t.setAttribute('aria-selected', 'false');
      });
      btn.classList.add('active');
      btn.setAttribute('aria-selected', 'true');

      document.querySelectorAll('.tab-content').forEach((sec) => {
        sec.classList.remove('active');
      });

      const activeSection = document.getElementById(targetTab);
      if (activeSection) {
        activeSection.classList.add('active');
      }

      // Contextual refresh
      if (targetTab === 'tab-history') {
        loadHistory();
      } else if (targetTab === 'tab-whitelist') {
        loadWhitelist();
      } else if (targetTab === 'tab-backups') {
        loadBackups();
      }
    });
  });

  // Table header sorting
  document.querySelectorAll('th.sortable').forEach((th) => {
    th.addEventListener('click', () => {
      const col = th.getAttribute('data-sort');
      if (state.sort.column === col) {
        state.sort.direction = state.sort.direction === 'asc' ? 'desc' : 'asc';
      } else {
        state.sort.column = col;
        state.sort.direction = 'desc';
      }
      renderDialogsTable();
    });
  });
}

// ============================================================================
// Filter Controls & Real-time Evaluation
// ============================================================================

let evaluateTimeout = null;

function setupFilterControls() {
  const sliderInactive = document.getElementById('sliderUserInactive');
  const numInactive = document.getElementById('numUserInactive');

  const sliderUnread = document.getElementById('sliderUnreadThreshold');
  const numUnread = document.getElementById('numUnreadThreshold');

  const sliderDormancy = document.getElementById('sliderDormancyDays');
  const numDormancy = document.getElementById('numDormancyDays');

  const sliderProtectRecent = document.getElementById('sliderProtectRecentRead');
  const numProtectRecent = document.getElementById('numProtectRecentRead');

  const toggleLogic = document.getElementById('toggleLogicMode');
  const labelLogic = document.getElementById('labelLogicMode');
  const toggleAdmin = document.getElementById('toggleProtectAdmin');
  const togglePinned = document.getElementById('toggleProtectPinned');
  const toggleProtectRecent = document.getElementById('toggleProtectRecentRead');
  const toggleIgnoreArchived = document.getElementById('toggleIgnoreArchived');

  function updateLogicLabel() {
    if (toggleLogic && labelLogic) {
      if (toggleLogic.checked) {
        labelLogic.textContent = 'И (все условия)';
        labelLogic.className = 'badge badge-protected';
      } else {
        labelLogic.textContent = 'ИЛИ (любое условие)';
        labelLogic.className = 'badge badge-candidate';
      }
    }
  }

  function syncConfigFromUI() {
    const inactiveVal = numInactive ? Number(numInactive.value) : (sliderInactive ? Number(sliderInactive.value) : 60);
    const unreadVal = numUnread ? Number(numUnread.value) : (sliderUnread ? Number(sliderUnread.value) : 200);
    const dormancyVal = numDormancy ? Number(numDormancy.value) : (sliderDormancy ? Number(sliderDormancy.value) : 90);
    const recentVal = numProtectRecent ? Number(numProtectRecent.value) : (sliderProtectRecent ? Number(sliderProtectRecent.value) : 2);

    state.filterConfig.user_inactive_days = inactiveVal;
    state.filterConfig.unread_threshold = unreadVal;
    state.filterConfig.dormancy_days = dormancyVal;
    state.filterConfig.protect_recent_read_days = recentVal;

    state.filterConfig.logic_mode = toggleLogic && toggleLogic.checked ? 'ALL' : 'ANY';
    state.filterConfig.protect_admin = toggleAdmin ? toggleAdmin.checked : true;
    state.filterConfig.protect_pinned = togglePinned ? togglePinned.checked : true;
    state.filterConfig.protect_recent_read = toggleProtectRecent ? toggleProtectRecent.checked : true;
    state.filterConfig.ignore_archived = toggleIgnoreArchived ? toggleIgnoreArchived.checked : true;

    updateLogicLabel();
  }

  function triggerReevaluation() {
    syncConfigFromUI();
    clearTimeout(evaluateTimeout);
    evaluateTimeout = setTimeout(async () => {
      if (state.dialogs.length > 0) {
        try {
          const res = await api.evaluateDialogs(state.filterConfig);
          state.dialogs = res.dialogs;
          // Synchronize selections with candidate status
          state.selectedIds.clear();
          state.dialogs.forEach((d) => {
            if (d.is_candidate) state.selectedIds.add(d.id);
          });
          renderDialogsTable();
          updateMetrics();
        } catch (err) {
          console.error('Error re-evaluating filters:', err);
        }
      }
    }, 200);
  }

  // Two-way synchronization for sliders and direct number inputs
  if (sliderInactive && numInactive) {
    sliderInactive.addEventListener('input', () => {
      numInactive.value = sliderInactive.value;
      triggerReevaluation();
    });
    const handleNumInactive = () => {
      let val = Math.max(0, parseInt(numInactive.value, 10) || 0);
      if (val > Number(sliderInactive.max)) sliderInactive.max = val;
      sliderInactive.value = val;
      triggerReevaluation();
    };
    numInactive.addEventListener('input', handleNumInactive);
    numInactive.addEventListener('change', handleNumInactive);
  }

  if (sliderUnread && numUnread) {
    sliderUnread.addEventListener('input', () => {
      numUnread.value = sliderUnread.value;
      triggerReevaluation();
    });
    const handleNumUnread = () => {
      let val = Math.max(0, parseInt(numUnread.value, 10) || 0);
      if (val > Number(sliderUnread.max)) sliderUnread.max = Math.max(val, 2000);
      sliderUnread.value = val;
      triggerReevaluation();
    };
    numUnread.addEventListener('input', handleNumUnread);
    numUnread.addEventListener('change', handleNumUnread);
  }

  if (sliderDormancy && numDormancy) {
    sliderDormancy.addEventListener('input', () => {
      numDormancy.value = sliderDormancy.value;
      triggerReevaluation();
    });
    const handleNumDormancy = () => {
      let val = Math.max(0, parseInt(numDormancy.value, 10) || 0);
      if (val > Number(sliderDormancy.max)) sliderDormancy.max = val;
      sliderDormancy.value = val;
      triggerReevaluation();
    };
    numDormancy.addEventListener('input', handleNumDormancy);
    numDormancy.addEventListener('change', handleNumDormancy);
  }

  if (sliderProtectRecent && numProtectRecent) {
    sliderProtectRecent.addEventListener('input', () => {
      numProtectRecent.value = sliderProtectRecent.value;
      triggerReevaluation();
    });
    const handleNumProtectRecent = () => {
      let val = Math.max(0, parseInt(numProtectRecent.value, 10) || 0);
      if (val > Number(sliderProtectRecent.max)) sliderProtectRecent.max = Math.max(val, 30);
      sliderProtectRecent.value = val;
      triggerReevaluation();
    };
    numProtectRecent.addEventListener('input', handleNumProtectRecent);
    numProtectRecent.addEventListener('change', handleNumProtectRecent);
  }

  if (toggleLogic) toggleLogic.addEventListener('change', triggerReevaluation);
  if (toggleAdmin) toggleAdmin.addEventListener('change', triggerReevaluation);
  if (togglePinned) togglePinned.addEventListener('change', triggerReevaluation);
  if (toggleProtectRecent) toggleProtectRecent.addEventListener('change', triggerReevaluation);
  if (toggleIgnoreArchived) toggleIgnoreArchived.addEventListener('change', triggerReevaluation);

  // Folder buttons
  const btnSelectAllFolders = document.getElementById('btnSelectAllFolders');
  const btnDeselectAllFolders = document.getElementById('btnDeselectAllFolders');

  if (btnSelectAllFolders) {
    btnSelectAllFolders.addEventListener('click', () => {
      const container = document.getElementById('foldersChipsContainer');
      if (!container) return;
      const checks = container.querySelectorAll('.folder-check');
      state.filterConfig.protected_folder_ids = [];
      checks.forEach((chk) => {
        chk.checked = true;
        const fid = Number(chk.getAttribute('data-folder-id'));
        state.filterConfig.protected_folder_ids.push(fid);
        const chip = chk.closest('.folder-chip');
        if (chip) chip.classList.add('active');
      });
      triggerReevaluation();
    });
  }

  if (btnDeselectAllFolders) {
    btnDeselectAllFolders.addEventListener('click', () => {
      const container = document.getElementById('foldersChipsContainer');
      if (!container) return;
      const checks = container.querySelectorAll('.folder-check');
      state.filterConfig.protected_folder_ids = [];
      checks.forEach((chk) => {
        chk.checked = false;
        const chip = chk.closest('.folder-chip');
        if (chip) chip.classList.remove('active');
      });
      triggerReevaluation();
    });
  }

  // Search input
  const inputSearch = document.getElementById('inputSearch');
  if (inputSearch) {
    inputSearch.addEventListener('input', (e) => {
      state.searchQuery = e.target.value.trim().toLowerCase();
      renderDialogsTable();
    });
  }

  // Filter chips (All, Candidates, Protected)
  const btnAll = document.getElementById('btnFilterAll');
  const btnCand = document.getElementById('btnFilterCandidates');
  const btnProt = document.getElementById('btnFilterProtected');

  [btnAll, btnCand, btnProt].forEach((btn) => {
    if (!btn) return;
    btn.addEventListener('click', () => {
      [btnAll, btnCand, btnProt].forEach((b) => b && b.classList.remove('active'));
      btn.classList.add('active');

      if (btn === btnAll) state.filterChip = 'all';
      if (btn === btnCand) state.filterChip = 'candidates';
      if (btn === btnProt) state.filterChip = 'protected';

      renderDialogsTable();
    });
  });

  window.triggerFilterReevaluation = triggerReevaluation;
}

async function loadFolders() {
  const container = document.getElementById('foldersChipsContainer');
  if (!container) return;

  try {
    const folders = await api.getFolders();
    const list = Array.isArray(folders) ? folders : (folders.folders || []);
    if (list.length === 0) {
      container.innerHTML = '<span class="text-subtle" style="font-size: 0.8rem;">Пользовательские папки в Telegram не найдены</span>';
      return;
    }

    container.innerHTML = list.map((f) => {
      const isChecked = state.filterConfig.protected_folder_ids && state.filterConfig.protected_folder_ids.includes(f.id);
      return `
        <label class="folder-chip ${isChecked ? 'active' : ''}" data-folder-id="${f.id}" title="${escapeHtml(f.title)} (ID: ${f.id})">
          <input type="checkbox" class="folder-check" data-folder-id="${f.id}" ${isChecked ? 'checked' : ''} style="accent-color: var(--accent-indigo); cursor: pointer;">
          <span style="font-weight: 500;">${escapeHtml(f.title)}</span>
          <span class="folder-badge">${f.peer_count || 0}</span>
        </label>
      `;
    }).join('');

    container.querySelectorAll('.folder-check').forEach((chk) => {
      chk.addEventListener('change', (e) => {
        const folderId = Number(e.target.getAttribute('data-folder-id'));
        const chip = e.target.closest('.folder-chip');
        if (!Array.isArray(state.filterConfig.protected_folder_ids)) {
          state.filterConfig.protected_folder_ids = [];
        }

        if (e.target.checked) {
          if (!state.filterConfig.protected_folder_ids.includes(folderId)) {
            state.filterConfig.protected_folder_ids.push(folderId);
          }
          if (chip) chip.classList.add('active');
        } else {
          state.filterConfig.protected_folder_ids = state.filterConfig.protected_folder_ids.filter((id) => id !== folderId);
          if (chip) chip.classList.remove('active');
        }

        if (window.triggerFilterReevaluation) {
          window.triggerFilterReevaluation();
        }
      });
    });
  } catch (err) {
    console.error('Error loading folders:', err);
    container.innerHTML = '<span class="text-subtle" style="font-size: 0.8rem; color: var(--accent-crimson);">Не удалось загрузить папки</span>';
  }
}

// Action Bar Buttons
function setupActionButtons() {
  document.getElementById('btnScanDialogs').addEventListener('click', startScan);
  document.getElementById('btnHeaderScan').addEventListener('click', startScan);

  document.getElementById('btnSelectAllCandidates').addEventListener('click', () => {
    state.selectedIds.clear();
    state.dialogs.forEach((d) => {
      if (d.is_candidate) state.selectedIds.add(d.id);
    });
    renderDialogsTable();
    updateMetrics();
  });

  document.getElementById('btnInvertSelection').addEventListener('click', () => {
    const visible = getFilteredDialogs();
    visible.forEach((d) => {
      if (state.selectedIds.has(d.id)) {
        state.selectedIds.delete(d.id);
      } else {
        state.selectedIds.add(d.id);
      }
    });
    renderDialogsTable();
    updateMetrics();
  });

  document.getElementById('btnDeselectAll').addEventListener('click', () => {
    state.selectedIds.clear();
    renderDialogsTable();
    updateMetrics();
  });

  // Master Checkbox
  const checkMaster = document.getElementById('checkMaster');
  checkMaster.addEventListener('change', (e) => {
    const visible = getFilteredDialogs();
    if (e.target.checked) {
      visible.forEach((d) => state.selectedIds.add(d.id));
    } else {
      visible.forEach((d) => state.selectedIds.delete(d.id));
    }
    renderDialogsTable();
    updateMetrics();
  });

  // Leave Selected Button
  document.getElementById('btnLeaveSelected').addEventListener('click', openLeaveConfirmationModal);

  // History & Backups Refresh
  document.getElementById('btnRefreshHistory').addEventListener('click', loadHistory);
  document.getElementById('btnRefreshBackups').addEventListener('click', loadBackups);
  document.getElementById('btnOpenExplorer').addEventListener('click', async () => {
    try {
      await api.openBackupsFolder();
      showToast('Папка бэкапов открыта в Проводнике', 'success');
    } catch (err) {
      showToast('Не удалось открыть Проводник', 'error');
    }
  });

  // Whitelist Form Add
  document.getElementById('formAddWhitelist').addEventListener('submit', async (e) => {
    e.preventDefault();
    const idInput = document.getElementById('wlEntityId');
    const titleInput = document.getElementById('wlTitle');
    const userInput = document.getElementById('wlUsername');

    try {
      await api.addToWhitelist(idInput.value, titleInput.value, userInput.value || null);
      showToast('Канал успешно добавлен в белый список', 'success');
      idInput.value = '';
      titleInput.value = '';
      userInput.value = '';
      await loadWhitelist();
      await loadDialogs();
    } catch (err) {
      showToast('Ошибка добавления в белый список', 'error');
    }
  });
}

// ============================================================================
// Data Loading & Rendering
// ============================================================================

async function loadSettings() {
  try {
    const s = await api.getSettings();
    if (s.filter_config) {
      const cfg = s.filter_config;
      const inactive = cfg.user_inactive_days !== undefined ? cfg.user_inactive_days : 60;
      const unread = cfg.unread_threshold !== undefined ? cfg.unread_threshold : 200;
      const dormancy = cfg.dormancy_days !== undefined ? cfg.dormancy_days : 90;
      const recent = cfg.protect_recent_read_days !== undefined ? cfg.protect_recent_read_days : 2;

      const sliderInactive = document.getElementById('sliderUserInactive');
      const numInactive = document.getElementById('numUserInactive');
      if (sliderInactive && numInactive) {
        if (inactive > Number(sliderInactive.max)) sliderInactive.max = inactive;
        sliderInactive.value = inactive;
        numInactive.value = inactive;
      }

      const sliderUnread = document.getElementById('sliderUnreadThreshold');
      const numUnread = document.getElementById('numUnreadThreshold');
      if (sliderUnread && numUnread) {
        if (unread > Number(sliderUnread.max)) sliderUnread.max = Math.max(unread, 2000);
        sliderUnread.value = unread;
        numUnread.value = unread;
      }

      const sliderDormancy = document.getElementById('sliderDormancyDays');
      const numDormancy = document.getElementById('numDormancyDays');
      if (sliderDormancy && numDormancy) {
        if (dormancy > Number(sliderDormancy.max)) sliderDormancy.max = dormancy;
        sliderDormancy.value = dormancy;
        numDormancy.value = dormancy;
      }

      const sliderRecent = document.getElementById('sliderProtectRecentRead');
      const numRecent = document.getElementById('numProtectRecentRead');
      if (sliderRecent && numRecent) {
        if (recent > Number(sliderRecent.max)) sliderRecent.max = Math.max(recent, 30);
        sliderRecent.value = recent;
        numRecent.value = recent;
      }

      const toggleLogic = document.getElementById('toggleLogicMode');
      if (toggleLogic) toggleLogic.checked = cfg.logic_mode === 'ALL';

      const toggleAdmin = document.getElementById('toggleProtectAdmin');
      if (toggleAdmin) toggleAdmin.checked = cfg.protect_admin !== false;

      const togglePinned = document.getElementById('toggleProtectPinned');
      if (togglePinned) togglePinned.checked = cfg.protect_pinned !== false;

      const toggleRecent = document.getElementById('toggleProtectRecentRead');
      if (toggleRecent) toggleRecent.checked = cfg.protect_recent_read !== false;

      const toggleArchive = document.getElementById('toggleIgnoreArchived');
      if (toggleArchive) toggleArchive.checked = cfg.ignore_archived !== false;

      if (Array.isArray(cfg.protected_folder_ids)) {
        state.filterConfig.protected_folder_ids = [...cfg.protected_folder_ids];
      }

      state.filterConfig = { ...state.filterConfig, ...cfg };
    }
  } catch (err) {
    console.warn('Could not load settings:', err);
  }
}

async function checkAuthStatus() {
  try {
    const res = await api.getAuthStatus();
    state.auth = res;
    updateAuthUI();
    if (res.authorized) {
      loadFolders();
    }
  } catch (err) {
    console.error('Error fetching auth status:', err);
  }
}

function updateAuthUI() {
  const dot = document.getElementById('authDot');
  const text = document.getElementById('authStatusText');
  const btnLabel = document.getElementById('authBtnLabel');

  if (state.auth.authorized) {
    dot.className = 'status-dot connected';
    const user = state.auth.user || {};
    const handle = user.username ? `@${user.username}` : (user.first_name || state.auth.phone || 'Авторизован');
    text.textContent = handle;
    btnLabel.textContent = 'Аккаунт';
  } else {
    dot.className = 'status-dot disconnected';
    text.textContent = 'Не подключен';
    btnLabel.textContent = 'Войти';
  }
}

async function loadDialogs() {
  try {
    const data = await api.getDialogs();
    state.dialogs = data;

    // Default select candidates
    state.selectedIds.clear();
    state.dialogs.forEach((d) => {
      if (d.is_candidate) state.selectedIds.add(d.id);
    });

    renderDialogsTable();
    updateMetrics();
  } catch (err) {
    console.error('Error loading dialogs:', err);
  }
}

function getFilteredDialogs() {
  let list = [...state.dialogs];

  // Chip filter
  if (state.filterChip === 'candidates') {
    list = list.filter((d) => d.is_candidate);
  } else if (state.filterChip === 'protected') {
    list = list.filter((d) => d.is_protected);
  }

  // Search filter
  if (state.searchQuery) {
    list = list.filter(
      (d) =>
        (d.title && d.title.toLowerCase().includes(state.searchQuery)) ||
        (d.username && d.username.toLowerCase().includes(state.searchQuery))
    );
  }

  // Sorting
  const col = state.sort.column;
  const dir = state.sort.direction === 'asc' ? 1 : -1;

  list.sort((a, b) => {
    let valA = a[col];
    let valB = b[col];

    if (valA === undefined || valA === null) valA = '';
    if (valB === undefined || valB === null) valB = '';

    if (typeof valA === 'string') {
      return valA.localeCompare(valB) * dir;
    }
    return (valA - valB) * dir;
  });

  return list;
}

function updateMetrics() {
  const total = state.dialogs.length;
  const candidates = state.dialogs.filter((d) => d.is_candidate).length;
  const protectedCount = state.dialogs.filter((d) => d.is_protected).length;
  const selected = state.selectedIds.size;

  document.getElementById('metricTotalFound').textContent = total;
  document.getElementById('metricCandidatesCount').textContent = candidates;
  document.getElementById('metricProtectedCount').textContent = protectedCount;
  document.getElementById('metricSelectedCount').textContent = selected;

  const btnLeave = document.getElementById('btnLeaveSelected');
  const leaveBtnLabel = document.getElementById('leaveBtnLabel');

  leaveBtnLabel.textContent = `Покинуть выбранные (${selected})`;
  btnLeave.disabled = selected === 0;
}

function renderDialogsTable() {
  const tbody = document.getElementById('dialogsTableBody');
  const visible = getFilteredDialogs();

  if (visible.length === 0) {
    tbody.innerHTML = `
      <tr>
        <td colspan="8" style="text-align: center; padding: 40px 24px; color: var(--text-muted);">
          ${state.dialogs.length === 0 ? 'Диалоги еще не отсканированы. Нажмите «Сканировать диалоги».' : 'Нет элементов, удовлетворяющих условиям фильтра и поиска.'}
        </td>
      </tr>
    `;
    return;
  }

  const rowsHtml = visible.map((d) => {
    const isChecked = state.selectedIds.has(d.id);
    const rowClass = isChecked ? 'row-selected' : '';

    // Entity type badge
    let typeBadge = '<span class="badge badge-type">Канал</span>';
    if (d.entity_type === 'supergroup') {
      typeBadge = '<span class="badge badge-type" style="color: #A78BFA; border-color: rgba(167, 139, 250, 0.3);">Супергруппа</span>';
    } else if (d.entity_type === 'group') {
      typeBadge = '<span class="badge badge-type" style="color: #94A3B8;">Группа</span>';
    }

    // Username display
    const usernameHtml = d.username
      ? `<a href="https://t.me/${d.username}" target="_blank" rel="noopener noreferrer" style="color: var(--accent-indigo); text-decoration: none;">@${d.username}</a>`
      : `<span class="text-subtle" style="font-size: 0.8rem;">[Приватный]</span>`;

    // Status badges
    let statusHtml = '';
    if (d.is_protected) {
      if (d.is_admin || d.is_creator) {
        statusHtml += '<span class="badge badge-protected">Защищен (Админ)</span> ';
      }
      if (d.is_pinned) {
        statusHtml += '<span class="badge badge-pinned">Закреплен</span> ';
      }
      if (d.is_whitelisted) {
        statusHtml += '<span class="badge badge-whitelist">Белый список</span> ';
      }
      if (d.is_folder_protected) {
        statusHtml += '<span class="badge badge-protected" style="border-color: rgba(99, 102, 241, 0.4); color: #818CF8;">Папка защищена</span> ';
      }
      if (d.is_recent_read_protected) {
        statusHtml += '<span class="badge badge-protected" style="border-color: rgba(16, 185, 129, 0.4); color: #34D399;">Читали недавно</span> ';
      }
    } else if (d.is_candidate) {
      const reasonText = d.trigger_reasons && d.trigger_reasons.length > 0 ? d.trigger_reasons.join('; ') : 'Условия фильтра совпали';
      statusHtml = `<span class="badge badge-candidate" title="${escapeHtml(reasonText)}">Кандидат</span><div class="text-subtle" style="font-size: 0.72rem; margin-top: 3px; max-width: 220px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">${escapeHtml(reasonText)}</div>`;
    } else {
      statusHtml = '<span class="text-subtle" style="font-size: 0.8rem;">Активен</span>';
    }

    // Whitelist action
    const isWl = state.whitelist.some((w) => w.entity_id === d.id);
    const wlActionBtn = isWl
      ? `<button class="btn btn-ghost btn-sm" onclick="handleRemoveFromWhitelist(${d.id})" title="Исключить из белого списка" style="color: var(--accent-crimson);">Исключить</button>`
      : `<button class="btn btn-ghost btn-sm" onclick="handleAddToWhitelist(${d.id}, '${escapeHtml(d.title)}', '${d.username || ''}')" title="Добавить в белый список">В белый список</button>`;

    const archiveBadge = d.is_archived
      ? '<span class="badge" style="background: rgba(148, 163, 184, 0.12); color: #94A3B8; border: 1px solid rgba(148, 163, 184, 0.25);">Архив</span>'
      : '';
    const folderBadges = (d.folders || [])
      .map((f) => `<span class="badge badge-folder" style="font-size: 0.7rem; padding: 1px 6px;">${escapeHtml(f)}</span>`)
      .join('');

    return `
      <tr class="${rowClass}" data-id="${d.id}">
        <td style="text-align: center;">
          <input type="checkbox" class="row-check" data-id="${d.id}" ${isChecked ? 'checked' : ''}>
        </td>
        <td>
          <div class="flex flex-col gap-1">
            <span style="font-weight: 500;">${escapeHtml(d.title)}</span>
            <div class="flex items-center gap-1" style="flex-wrap: wrap;">
              ${typeBadge}
              ${archiveBadge}
              ${folderBadges}
            </div>
          </div>
        </td>
        <td>${usernameHtml}</td>
        <td>
          <span class="badge ${d.unread_count > 0 ? 'badge-candidate' : 'badge-protected'} text-mono">
            ${d.unread_count}
          </span>
        </td>
        <td class="text-mono">${d.dormancy_days} дн.</td>
        <td class="text-mono">${d.user_inactive_days} дн.</td>
        <td>${statusHtml}</td>
        <td style="text-align: right;">${wlActionBtn}</td>
      </tr>
    `;
  }).join('');

  tbody.innerHTML = rowsHtml;

  // Row checkboxes event delegation
  tbody.querySelectorAll('.row-check').forEach((chk) => {
    chk.addEventListener('change', (e) => {
      const id = Number(e.target.getAttribute('data-id'));
      if (e.target.checked) {
        state.selectedIds.add(id);
      } else {
        state.selectedIds.delete(id);
      }
      updateMetrics();
      const tr = e.target.closest('tr');
      if (tr) {
        if (e.target.checked) tr.classList.add('row-selected');
        else tr.classList.remove('row-selected');
      }
    });
  });
}

// Global actions called from HTML strings
window.handleAddToWhitelist = async function (entityId, title, username) {
  try {
    await api.addToWhitelist(entityId, title, username || null);
    showToast(`«${title}» добавлен в белый список`, 'success');
    await loadWhitelist();
    await loadDialogs();
  } catch (err) {
    showToast('Ошибка при добавлении в белый список', 'error');
  }
};

window.handleRemoveFromWhitelist = async function (entityId) {
  try {
    await api.removeFromWhitelist(entityId);
    showToast('Канал исключен из белого списка', 'info');
    await loadWhitelist();
    await loadDialogs();
  } catch (err) {
    showToast('Ошибка при удалении из белого списка', 'error');
  }
};

// ============================================================================
// Scanning Operation
// ============================================================================

async function startScan() {
  if (!state.auth.authorized && !state.auth.mock_mode) {
    showToast('Сначала авторизуйтесь в Telegram', 'error');
    openAuthModal();
    return;
  }

  try {
    const res = await api.scanDialogs(state.filterConfig);
    if (res.status === 'already_running') {
      showToast('Сканирование уже запущено', 'info');
    }
  } catch (err) {
    showToast('Не удалось запустить сканирование', 'error');
  }
}

// ============================================================================
// Tab 2: History & Selective Rollback
// ============================================================================

async function loadHistory() {
  try {
    const batches = await api.getHistory();
    state.historyBatches = batches;
    renderHistory();
  } catch (err) {
    console.error('Error loading history:', err);
  }
}

function renderHistory() {
  const container = document.getElementById('batchesContainer');
  if (state.historyBatches.length === 0) {
    container.innerHTML = `
      <div class="card" style="text-align: center; padding: 48px; color: var(--text-muted);">
        История сессий очистки пуста. После выхода из каналов здесь появятся снапшоты для отката.
      </div>
    `;
    return;
  }

  const batchesHtml = state.historyBatches.map((batch) => {
    const isExpanded = state.expandedBatches.has(batch.id);
    const dateFormatted = new Date(batch.created_at).toLocaleString('ru-RU');
    const snapshots = batch.snapshots || [];
    const selectedSnapshots = state.batchSelectedSnapshots[batch.id] || new Set();

    let snapshotsTableHtml = '';
    if (isExpanded) {
      if (snapshots.length === 0) {
        snapshotsTableHtml = '<div style="padding: 16px; color: var(--text-muted);">Снапшоты отсутствуют в данной сессии.</div>';
      } else {
        const rows = snapshots.map((s) => {
          const isSelected = selectedSnapshots.has(s.id);
          const isPrivate = s.is_private || !s.username;

          let statusBadge = '<span class="badge badge-candidate">Покинут</span>';
          if (s.restore_status === 'restored') {
            statusBadge = '<span class="badge badge-restored">Восстановлен</span>';
          } else if (s.restore_status === 'unrestorable_private') {
            statusBadge = '<span class="badge badge-unrestorable" title="Приватный канал без @username">Приватный (нет возврата)</span>';
          } else if (s.restore_status === 'failed') {
            statusBadge = `<span class="badge badge-candidate" title="${escapeHtml(s.error_message || '')}">Ошибка</span>`;
          }

          const usernameDisplay = s.username
            ? `@${s.username}`
            : '<span class="text-danger" style="font-size: 0.78rem;">Приватный (без @username)</span>';

          const disableCheckbox = isPrivate || s.restore_status === 'restored';

          return `
            <tr>
              <td style="width: 38px; text-align: center;">
                <input type="checkbox" class="snap-check" data-batch="${batch.id}" data-id="${s.id}" ${isSelected ? 'checked' : ''} ${disableCheckbox ? 'disabled' : ''}>
              </td>
              <td><strong>${escapeHtml(s.title)}</strong></td>
              <td>${usernameDisplay}</td>
              <td><span class="badge badge-type">${escapeHtml(s.entity_type)}</span></td>
              <td>${statusBadge}</td>
              <td class="text-subtle text-mono" style="font-size: 0.78rem;">${new Date(s.left_at).toLocaleTimeString('ru-RU')}</td>
              <td class="text-subtle" style="font-size: 0.75rem; max-width: 200px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">
                ${escapeHtml(s.error_message || s.trigger_reasons || '-')}
              </td>
            </tr>
          `;
        }).join('');

        snapshotsTableHtml = `
          <div style="margin-top: 16px; border-top: 1px solid var(--border-subtle); padding-top: 16px;">
            <div class="flex items-center justify-between" style="margin-bottom: 12px;">
              <span style="font-size: 0.85rem; font-weight: 600;">Список сущностей сессии (${snapshots.length})</span>
              <div class="flex items-center gap-2">
                <button class="btn btn-secondary btn-sm" onclick="handleRollbackSelected('${batch.id}')">
                  Восстановить выбранные (${selectedSnapshots.size})
                </button>
                <button class="btn btn-primary btn-sm" onclick="handleRollbackAll('${batch.id}')">
                  Восстановить всё (публичные)
                </button>
              </div>
            </div>
            <div class="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th style="width: 38px;"></th>
                    <th>Название</th>
                    <th>Юзернейм</th>
                    <th>Тип</th>
                    <th>Статус</th>
                    <th>Время выхода</th>
                    <th>Инфо / Ошибка</th>
                  </tr>
                </thead>
                <tbody>
                  ${rows}
                </tbody>
              </table>
            </div>
          </div>
        `;
      }
    }

    return `
      <div class="card" data-batch-id="${batch.id}">
        <div class="flex items-center justify-between" style="flex-wrap: wrap; gap: 12px;">
          <div>
            <div class="flex items-center gap-2">
              <strong style="font-size: 1.05rem;">Сессия ${escapeHtml(batch.id)}</strong>
              <span class="badge badge-protected text-mono">${dateFormatted}</span>
            </div>
            <div class="flex items-center gap-4 text-subtle" style="font-size: 0.82rem; margin-top: 4px;">
              <span>Кандидатов: <strong>${batch.total_candidates}</strong></span>
              <span>Покинуто: <strong class="text-danger">${batch.departed_count}</strong></span>
              <span>Восстановлено: <strong class="text-success">${batch.restored_count}</strong></span>
            </div>
          </div>

          <div class="flex items-center gap-2">
            <button class="btn btn-secondary btn-sm" onclick="toggleBatchExpand('${batch.id}')">
              ${isExpanded ? 'Скрыть список' : `Показать каналы (${snapshots.length})`}
            </button>
          </div>
        </div>

        ${snapshotsTableHtml}
      </div>
    `;
  }).join('');

  container.innerHTML = batchesHtml;

  // Snapshot checkboxes listener
  container.querySelectorAll('.snap-check').forEach((chk) => {
    chk.addEventListener('change', (e) => {
      const bId = e.target.getAttribute('data-batch');
      const sId = Number(e.target.getAttribute('data-id'));

      if (!state.batchSelectedSnapshots[bId]) {
        state.batchSelectedSnapshots[bId] = new Set();
      }

      if (e.target.checked) {
        state.batchSelectedSnapshots[bId].add(sId);
      } else {
        state.batchSelectedSnapshots[bId].delete(sId);
      }
      renderHistory();
    });
  });
}

window.toggleBatchExpand = function (batchId) {
  if (state.expandedBatches.has(batchId)) {
    state.expandedBatches.delete(batchId);
  } else {
    state.expandedBatches.add(batchId);
  }
  renderHistory();
};

window.handleRollbackAll = async function (batchId) {
  try {
    await api.rollbackBatch(batchId, null);
  } catch (err) {
    showToast('Ошибка при запуске отката', 'error');
  }
};

window.handleRollbackSelected = async function (batchId) {
  const selected = state.batchSelectedSnapshots[batchId];
  if (!selected || selected.size === 0) {
    showToast('Выберите хотя бы один канал для восстановления', 'info');
    return;
  }
  try {
    await api.rollbackBatch(batchId, Array.from(selected));
  } catch (err) {
    showToast('Ошибка при запуске отката', 'error');
  }
};

// ============================================================================
// Tab 3: Whitelist
// ============================================================================

async function loadWhitelist() {
  try {
    const list = await api.getWhitelist();
    state.whitelist = list;
    renderWhitelist();
  } catch (err) {
    console.error('Error loading whitelist:', err);
  }
}

function renderWhitelist() {
  const tbody = document.getElementById('whitelistTableBody');
  if (state.whitelist.length === 0) {
    tbody.innerHTML = `
      <tr>
        <td colspan="5" style="text-align: center; padding: 40px; color: var(--text-muted);">
          Белый список пуст. Добавьте важные каналы, которые никогда не должны покидаться.
        </td>
      </tr>
    `;
    return;
  }

  tbody.innerHTML = state.whitelist.map((w) => {
    const dateFormatted = new Date(w.added_at).toLocaleString('ru-RU');
    const usernameHtml = w.username ? `@${w.username}` : '<span class="text-subtle">-</span>';

    return `
      <tr>
        <td class="text-mono" style="color: var(--accent-indigo);">${w.entity_id}</td>
        <td><strong>${escapeHtml(w.title)}</strong></td>
        <td>${usernameHtml}</td>
        <td class="text-subtle text-mono" style="font-size: 0.8rem;">${dateFormatted}</td>
        <td style="text-align: right;">
          <button class="btn btn-danger-outline btn-sm" onclick="handleRemoveFromWhitelist(${w.entity_id})">
            Удалить из списка
          </button>
        </td>
      </tr>
    `;
  }).join('');
}

// ============================================================================
// Tab 4: Backups
// ============================================================================

async function loadBackups() {
  try {
    const list = await api.getBackups();
    state.backups = list;
    renderBackups();
  } catch (err) {
    console.error('Error loading backups:', err);
  }
}

function renderBackups() {
  const tbody = document.getElementById('backupsTableBody');
  if (state.backups.length === 0) {
    tbody.innerHTML = `
      <tr>
        <td colspan="6" style="text-align: center; padding: 40px; color: var(--text-muted);">
          В папке C:\\tg\\backups пока нет резервных копий. Они создаются перед очисткой.
        </td>
      </tr>
    `;
    return;
  }

  tbody.innerHTML = state.backups.map((b) => {
    const dateFormatted = new Date(b.created_at).toLocaleString('ru-RU');
    const badgeType = b.file_type === 'json' ? 'badge-pinned' : 'badge-whitelist';

    return `
      <tr>
        <td class="text-mono" style="font-weight: 500;">${escapeHtml(b.filename)}</td>
        <td><span class="badge ${badgeType}">${b.file_type.toUpperCase()}</span></td>
        <td class="text-mono">${b.size_kb} KB</td>
        <td class="text-subtle text-mono" style="font-size: 0.8rem;">${dateFormatted}</td>
        <td class="text-subtle text-mono">${escapeHtml(b.batch_id || '-')}</td>
        <td style="text-align: right;">
          <a href="/api/backups/download/${encodeURIComponent(b.filename)}" class="btn btn-secondary btn-sm" download>
            <svg class="icon icon-sm" viewBox="0 0 24 24"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
            Скачать
          </a>
        </td>
      </tr>
    `;
  }).join('');
}

// ============================================================================
// Modals: Leave Confirmation
// ============================================================================

function setupLeaveModal() {
  const modal = document.getElementById('modalLeaveConfirm');
  const btnClose = document.getElementById('btnLeaveModalClose');
  const btnCancel = document.getElementById('btnLeaveCancel');
  const btnProceed = document.getElementById('btnLeaveProceed');

  btnClose.addEventListener('click', () => modal.classList.remove('active'));
  btnCancel.addEventListener('click', () => modal.classList.remove('active'));

  btnProceed.addEventListener('click', async () => {
    modal.classList.remove('active');
    const ids = Array.from(state.selectedIds);
    if (ids.length === 0) return;

    try {
      await api.leaveEntities(ids);
    } catch (err) {
      showToast(err.message || 'Ошибка запуска выхода', 'error');
    }
  });
}

function openLeaveConfirmationModal() {
  const modal = document.getElementById('modalLeaveConfirm');
  const totalCountEl = document.getElementById('confirmTotalCount');
  const publicCountEl = document.getElementById('confirmPublicCount');
  const privateCountEl = document.getElementById('confirmPrivateCount');
  const tbody = document.getElementById('confirmSelectedTableBody');

  const selectedList = state.dialogs.filter((d) => state.selectedIds.has(d.id));
  const publicCount = selectedList.filter((d) => !d.is_private && d.username).length;
  const privateCount = selectedList.filter((d) => d.is_private || !d.username).length;

  totalCountEl.textContent = `${selectedList.length} сущностей`;
  publicCountEl.textContent = publicCount;
  privateCountEl.textContent = privateCount;

  tbody.innerHTML = selectedList.map((d) => {
    const isPriv = d.is_private || !d.username;
    const userDisplay = d.username ? `@${d.username}` : '<span class="text-danger">[Приватный]</span>';
    const reasons = d.trigger_reasons && d.trigger_reasons.length > 0 ? d.trigger_reasons.join(', ') : 'Кандидат';

    return `
      <tr>
        <td><strong>${escapeHtml(d.title)}</strong></td>
        <td>${userDisplay}</td>
        <td><span class="badge badge-type">${escapeHtml(d.entity_type)}</span></td>
        <td class="text-subtle" style="font-size: 0.75rem;">${escapeHtml(reasons)}</td>
      </tr>
    `;
  }).join('');

  modal.classList.add('active');
}

// ============================================================================
// Modals: Auth Wizard
// ============================================================================

let authPendingPhone = '';

function setupAuthModal() {
  const modal = document.getElementById('modalAuth');
  const btnHeaderAuth = document.getElementById('btnHeaderAuth');
  const authStatusBadge = document.getElementById('authStatusBadge');
  const btnClose = document.getElementById('btnAuthModalClose');
  const btnCancel = document.getElementById('btnAuthCancel');

  const step1 = document.getElementById('authStep1');
  const step2 = document.getElementById('authStep2');
  const step3 = document.getElementById('authStep3');
  const step4 = document.getElementById('authStep4');

  const btnStep1 = document.getElementById('btnAuthSubmitStep1');
  const btnStep2 = document.getElementById('btnAuthSubmitStep2');
  const btnStep3 = document.getElementById('btnAuthSubmitStep3');
  const btnLogout = document.getElementById('btnAuthLogout');

  function openModal() {
    modal.classList.add('active');
    if (state.auth.authorized) {
      showStep(4);
    } else {
      showStep(1);
    }
  }

  btnHeaderAuth.addEventListener('click', openModal);
  authStatusBadge.addEventListener('click', openModal);

  btnClose.addEventListener('click', () => modal.classList.remove('active'));
  btnCancel.addEventListener('click', () => modal.classList.remove('active'));

  function showStep(num) {
    step1.style.display = num === 1 ? 'flex' : 'none';
    step2.style.display = num === 2 ? 'flex' : 'none';
    step3.style.display = num === 3 ? 'flex' : 'none';
    step4.style.display = num === 4 ? 'flex' : 'none';

    btnStep1.style.display = num === 1 ? 'inline-flex' : 'none';
    btnStep2.style.display = num === 2 ? 'inline-flex' : 'none';
    btnStep3.style.display = num === 3 ? 'inline-flex' : 'none';
    btnLogout.style.display = num === 4 ? 'inline-flex' : 'none';

    if (num === 4 && state.auth.user) {
      const u = state.auth.user;
      document.getElementById('authAccountName').textContent = `${u.first_name || ''} ${u.last_name || ''}`.trim() || 'Пользователь Telegram';
      document.getElementById('authAccountDetail').textContent = `@${u.username || '—'} • ${u.phone || state.auth.phone || ''} (ID: ${u.id || ''})`;
    }
  }

  // Step 1: Submit API Credentials & Phone
  btnStep1.addEventListener('click', async () => {
    const apiId = document.getElementById('authApiId').value.trim();
    const apiHash = document.getElementById('authApiHash').value.trim();
    const phone = document.getElementById('authPhone').value.trim();

    if (!apiId || !apiHash || !phone) {
      showToast('Заполните все поля (API ID, API Hash, телефон)', 'error');
      return;
    }

    authPendingPhone = phone;
    btnStep1.disabled = true;
    btnStep1.textContent = 'Отправка...';

    try {
      await api.sendCode(phone, apiId, apiHash);
      document.getElementById('authPhoneDisplay').textContent = phone;
      showToast('Код успешно отправлен в Telegram', 'success');
      showStep(2);
    } catch (err) {
      showToast(err.message || 'Ошибка отправки кода', 'error');
    } finally {
      btnStep1.disabled = false;
      btnStep1.textContent = 'Получить код';
    }
  });

  // Step 2: Submit Code
  btnStep2.addEventListener('click', async () => {
    const code = document.getElementById('authCode').value.trim();
    if (!code) {
      showToast('Введите 5-значный код подтверждения', 'error');
      return;
    }

    btnStep2.disabled = true;
    btnStep2.textContent = 'Проверка...';

    try {
      const res = await api.verifyCode(authPendingPhone, code);
      if (res.status === '2fa_required') {
        showToast('Требуется пароль двухфакторной аутентификации (2FA)', 'info');
        showStep(3);
      } else {
        showToast('Авторизация успешно завершена!', 'success');
        await checkAuthStatus();
        showStep(4);
      }
    } catch (err) {
      showToast(err.message || 'Ошибка проверки кода', 'error');
    } finally {
      btnStep2.disabled = false;
      btnStep2.textContent = 'Войти';
    }
  });

  // Step 3: Submit 2FA Password
  btnStep3.addEventListener('click', async () => {
    const pass = document.getElementById('authPassword').value;
    if (!pass) {
      showToast('Введите 2FA пароль', 'error');
      return;
    }

    btnStep3.disabled = true;
    btnStep3.textContent = 'Проверка...';

    try {
      await api.verifyPassword(pass);
      showToast('2FA верификация пройдена!', 'success');
      await checkAuthStatus();
      showStep(4);
    } catch (err) {
      showToast(err.message || 'Неверный 2FA пароль', 'error');
    } finally {
      btnStep3.disabled = false;
      btnStep3.textContent = 'Подтвердить пароль';
    }
  });

  // Step 4: Logout
  btnLogout.addEventListener('click', async () => {
    try {
      await api.logout();
      showToast('Выход из аккаунта выполнен', 'info');
      state.dialogs = [];
      state.selectedIds.clear();
      await checkAuthStatus();
      renderDialogsTable();
      updateMetrics();
      showStep(1);
    } catch (err) {
      showToast('Ошибка при выходе', 'error');
    }
  });
}

function openAuthModal() {
  const modal = document.getElementById('modalAuth');
  modal.classList.add('active');
}

// ============================================================================
// Drawer (Bottom Terminal)
// ============================================================================

function setupDrawer() {
  const drawer = document.getElementById('progressDrawer');
  const btnClose = document.getElementById('btnDrawerClose');
  const btnCancel = document.getElementById('btnDrawerCancel');

  btnClose.addEventListener('click', () => {
    drawer.classList.remove('active');
  });

  btnCancel.addEventListener('click', async () => {
    try {
      await api.cancelTask();
      showToast('Запрос на отмену отправлен...', 'info');
    } catch (err) {
      showToast('Не удалось отменить операцию', 'error');
    }
  });
}
