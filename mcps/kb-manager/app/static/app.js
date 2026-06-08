// API base URL
const API_BASE = '';

// State
let selectedFile = null;
let currentCollection = null; // текущая коллекция
let currentCollectionType = null; // faq | kb | docs
let activeCollections = {
    faq: null,
    kb: null
}; // активные коллекции
let collectionsByType = {}; // коллекции по типам
let activeAliases = {}; // активные алиасы
// управление промптами
let currentPromptContent = ""; //текст текущего промпта
let promptFiles = []; // список файлов промптов для агента
let botStartMessageContent = ""; // стартовое сообщение текст
let newsEditor = null; // текстовое поле отправки новости
let newsHistoryTimer = null; // Переменная для хранения таймера
let currentAgent = null; // текущий агент
let promptEditorMDE = null; // редактор промпта в markdown формате
let currentUser = null; // текущий пользователь
// Логи: фильтры и кэш
let logFilters = {};
let logsCache = []; // кэш последних записей для быстрой фильтрации
let logsPage = 0; // стандартная страница логов
const LOGS_PAGE_SIZE = 100; // число логов на страницу
// переменные для аналитики
let hourChart = null;
let dayChart = null;
let userSearch = "";
let allUsers = [];
let filteredUsers = [];
let analyticsLoaded = false; // флаг загрузки аналитики
let userPage = 0; 
const PAGE_SIZE = 10; // число отображаемых пользователей и документов на странице
let docPage = 0;
let allDocs = [];
let statSources = [];
// пагинация для групп пользователей
let currentPage = 1;
let pageSize = 20;
let allUsersCache = [];
// фильтр пользователей
let activeStatsFilter = "all";
let filteredUsersCache = []; // кэш фильтрованных пользователей


// Initialize on load
document.addEventListener('DOMContentLoaded', async () => {
    await checkAuth(); //Проверка авторизации
    await loadAliasData(); // загрузка данных Алиаса
    await loadCollections(); // загрузка коллекций
    await loadActiveCollections(); // загрузка активных коллекций
    await loadCollectionInfo(); // загрузка информации о коллекциях
    await loadManagerCollectionInfo(); // загрузка информации о коллекции для менеджера
    await loadDocuments(); // загрузка документов
    await loadSyncSettings(); // загрузка настроек синхронизации
    refreshTablesList();
    subscribeToSync();
    await loadFilesystemTree();
    startLogsAutoRefresh();
    // блок для работы с новостями
    const uploadBox = document.getElementById("news-upload-box");
    const fileInput = document.getElementById("news-files");
    const fileInfo = document.getElementById("news-file-info");
    const fileName = document.getElementById("news-file-name");
    const removeBtn = document.getElementById("news-file-remove");
    if (!uploadBox || !fileInput) return;
    uploadBox.addEventListener("click", () => fileInput.click());
    fileInput.addEventListener("change", () => {
        if (fileInput.files.length > 0) {
            fileName.textContent = fileInput.files[0].name;
            fileInfo.style.display = "flex";
        }
    });
    removeBtn.addEventListener("click", () => {
        fileInput.value = "";
        fileInfo.style.display = "none";
    });
    // Инициализация Quill редактора новостей
    if (document.getElementById("news-editor")) {
        newsEditor = new Quill('#news-editor', {
            theme: 'snow',
            modules: {
                toolbar: '#news-toolbar'
            },
            placeholder: "Введите текст..."
        });
    }
});
// #############################
// MENU FOR ALL PAGES INSIDE UI 
// #############################

// load active collections
async function loadActiveCollections() {
    const res = await fetch("/api/collections/active");
    const data = await res.json();
    activeCollections = data;
}
// collection load
async function loadCollections() {
    const activeEl = document.getElementById('current-collection');
    const selectEl = document.getElementById('collection-select');
    selectEl.innerHTML = '<option disabled selected>Загрузка коллекций...</option>';
    activeEl.textContent = 'Активная коллекция: загрузка...';
    try {
        const response = await fetch(`${API_BASE}/api/collections`);
        const data = await response.json();
        const { current_collection, collections} = data;
        currentCollection = current_collection;
        // ативная коллекция для отображения типа
        const activeOption = collections.find(c => c.name === current_collection);
        if (activeOption) {
            currentCollectionType = activeOption.type;
        }
        // Активная коллекция
        activeEl.textContent = `${current_collection}`;
        // Dropdown
        selectEl.innerHTML = '';
        collections.forEach(col => {
            const opt = document.createElement('option');
            opt.value = col.name;
            opt.textContent = col.name;
            opt.dataset.type = col.type;
            if (col.name === current_collection) {
                opt.selected = true;
            }
            selectEl.appendChild(opt);
        });
        selectEl.onchange = () => {
            const selectedOption = selectEl.selectedOptions[0];
            switchCollection(
                selectedOption.value,
                selectedOption.dataset.type
            );
        };
    } catch (e) {
        activeEl.textContent = 'Активная коллекция ошибка';
        selectEl.innerHTML = '<option disabled>Ошибка загрузки коллекций</option>';
        console.error(e);
    }
}
// Switch collection
async function switchCollection(collectionName, collectionType) {
    if (!collectionName) return;
    currentCollectionType = collectionType;
    currentCollection = collectionName;

    try {
        const response = await fetch(`${API_BASE}/api/collections/switch`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ collection_name: collectionName, collection_type: collectionType})
        });
        if (!response.ok) {
            throw new Error(`Переключение провалилось: ${response.status}`);
        }
        const data = await response.json();
        // Перезагрузка UI
        await loadAliasData();
        await loadCollections();
        await loadActiveCollections();
        await loadCollectionInfo();
        await loadManagerCollectionInfo();
        await loadDocuments();
        await loadFilesystemTree();
        // Очистка поиска
        document.getElementById('search-results').innerHTML = '';
        document.getElementById('search-query').value = '';

    } catch (e) {
        console.error('[switchCollection] error', e);
        alert(`Не удалось переключить коллекцию: ${e.message}`);
    }
}
// Collection Info
async function loadCollectionInfo() {
    try {
        const response = await fetch(`${API_BASE}/api/collections/info`);
        const data = await response.json();
        const isFAQ = currentCollectionType === 'faq';
        document.getElementById('collection-info').innerHTML = 
            `
                ${isFAQ? "Документы": "Точки"} 
                <strong>${data.points_count-1 || 0}</strong>
                | Версия платформы
                <strong>${data.platform_version || 0}</strong>
                | Последняя Синхронизация
                <strong>${data.last_sync? formatDate(data.last_sync): "В процессе"}</strong>
                | Следующая Синхронизация
                <strong>${data.next_sync? formatDate(data.next_sync): "Пока не установлена"}</strong>
            `;
        } catch (error) {
        console.error('Error loading collection info:', error);
    }
}
// collection info for manager 
async function loadManagerCollectionInfo() {
    try {
        const response = await fetch(`${API_BASE}/api/collections/info?collection=kb_collection`);
        const data = await response.json();
        document.getElementById("manager-collection-info").innerHTML = `
            Документы
            <strong>${data.points_count - 1 || 0}</strong>
            | Версия платформы
            <strong>${data.platform_version || 0}</strong>
            | Последняя Синхронизация
            <strong>
                ${data.last_sync
                    ? formatDate(data.last_sync)
                    : "В процессе"}
            </strong>
            | Следующая Синхронизация
            <strong>
                ${data.next_sync
                    ? formatDate(data.next_sync)
                    : "Пока не установлена"}
            </strong>
        `;
    } catch (error) {
        console.error(
            "Error loading manager collection info:",
            error
        );
    }
}
// кнопка удаления коллекции
document
  .getElementById("delete-collection-btn")
  .addEventListener("click", async () => {
    const select = document.getElementById("collection-select");
    const deletedCollection = select.value;
    if (!deletedCollection) {
      alert("Коллекция не выбрана");
      return;
    }
    const confirmed = confirm(
      `Вы уверены что хотите удалить коллекцию? "${deletedCollection}"?\n\n Это действие необратимо.`
    );
    if (!confirmed) return;
    const deletedType = getCollectionType(deletedCollection);
    try {
      const res = await fetch("/api/collections/delete", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({collection: deletedCollection }),
      });
      const data = await res.json();
      if (!res.ok) {
        const err = await res.text();
        throw new Error(err);
      }
      alert(`Коллекция "${data.deleted_collection}" удалена`);
      // обновляем список коллекций
      await loadAliasData();
      await loadCollections();
      await loadActiveCollections();
      //   переключаем UI на активную alias-коллекцию
      if (deletedType && activeCollections?.[deletedType]?.collection) {
        const nextCollection = activeCollections[deletedType].collection;
        select.value = nextCollection;
        select.dispatchEvent(new Event("change"));
      } else {
        // fallback на первую доступную
        select.selectedIndex = 0;
        select.dispatchEvent(new Event("change"));
      }
    } catch (err) {
      alert(`Не удалось удалить коллекцию: Это активная коллекция`); 
      console.error(err);
    }
  });
// кнопка модальности создания коллекции
function openCreateCollectionModal() {
    const modal = document.getElementById("create-collection-modal");
    modal.classList.add("active");
}
// закрытие модальности создания коллекции
function closeCreateCollectionModal() {
    const modal = document.getElementById("create-collection-modal");
    modal.classList.remove("active");
}
// создание коллекции через модальное окно
async function createCollection() {
    const version = document.getElementById("newCollectionVersion").value.trim();
    const type = document.getElementById("newCollectionType").value;
    const errorBox = document.getElementById("create-collection-error");
    errorBox.classList.add("hidden");
    errorBox.textContent = "";
    if (!version || !/^\d+(\.\d+)?$/.test(version)) {
        errorBox.textContent = "Версия должна быть числом (например, 1 или 1.2)";
        errorBox.classList.remove("hidden");
        return;
    }
    if (!version) {
        errorBox.textContent = "Требуется версия";
        errorBox.classList.remove("hidden");
        return;
    }
    const collectionName = `${type}_collection_v${version}`;
    try {
        const res = await fetch("/api/collections/create", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ version, type })
        });
        const data = await res.json();
        if (!res.ok) {
            throw new Error(data.error || "Не удалось создать коллекцию");
        }
        closeCreateCollectionModal();
        await loadAliasData();
        await loadCollections();
        await loadActiveCollections();
        const select = document.getElementById("collection-select");
        select.value = collectionName;
        select.dispatchEvent(new Event("change"));
    } catch (err) {
        errorBox.textContent = err.message;
        errorBox.classList.remove("hidden");
    }
}
// загрузка алиасов по разным коллекциям
async function loadAliasData() {
    const [collectionsRes, activeRes] = await Promise.all([
        fetch("/api/collections/by-type"),
        fetch("/api/collections/active")
    ]);
    collectionsByType = await collectionsRes.json();
    activeAliases = await activeRes.json();
}
// модальность для переключения между активными alias 
async function openSwitchCollectionModal() {
    document.getElementById("switch-collection-modal").classList.add("active");
     if (!collectionsByType.faq) {
        await loadAliasData();
    }
    loadAliasCollections();
}
// загрузка алиасов для коллекций
function loadAliasCollections() {
    const type = document.getElementById("switchCollectionType").value;
    const targetSelect = document.getElementById("switchCollectionTarget");
    const activeCollection = activeAliases[type]?.collection;
    const collections = collectionsByType[type] || [];
    targetSelect.innerHTML = "";
    collections.forEach(name => {
        const option = document.createElement("option");
        option.value = name;
        option.textContent = name;
        if (name === activeCollection) {
            option.disabled = true;
            option.textContent += " (активная)";
        }
        targetSelect.appendChild(option);
    });
}
// закрытие модальности переключения коллекций
function closeSwitchCollectionModal() {
    document.getElementById("switch-collection-modal").classList.remove("active");
}
// переключение alias между коллекциями 
async function switchCollectionAlias() {
    const type = document.getElementById("switchCollectionType").value;
    const collection = document.getElementById("switchCollectionTarget").value;
    const errorBox = document.getElementById("switch-collection-error");
    errorBox.classList.add("hidden");
    errorBox.textContent = "";
    if (!collection) {
        errorBox.textContent = "Выберите коллекцию";
        errorBox.classList.remove("hidden");
        return;
    }
    try {
        const res = await fetch("/api/collections/switch-alias", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                collection_name: collection,
                collection_type: type
            })
        });
        const data = await res.json();
        if (!res.ok) {
            throw new Error(data.detail || "Не удалось переключить алиас");
        }
        const current = document.getElementById("collection-select").value;
        if (current.startsWith(`${type}_`) && current !== collection) {
            const confirmSwitch = confirm(
                `Алиас переключен на "${collection}".\n\n Переключить интерфейс на эту коллекцию?`
            );
            if (confirmSwitch) {
                const select = document.getElementById("collection-select");
                select.value = collection;
                select.dispatchEvent(new Event("change"));
            }
        }
        closeSwitchCollectionModal();
        // обновляем UI
        await loadAliasData();
        await loadActiveCollections();
    } catch (err) {
        errorBox.textContent = err.message;
        errorBox.classList.remove("hidden");
    }
}
// Tab Management
function showTab(tabName, event=null) {
    // Hide all tabs
    // tabs
    document.querySelectorAll('.tab-content').forEach(tab => {
        tab.classList.remove('active');
    });
    // buttons
    document.querySelectorAll('.tab-button').forEach(btn => {
        btn.classList.remove('active');
    });
    // Show selected tab
    document.getElementById(`${tabName}-tab`).classList.add('active');
    if (event && event.currentTarget) {
        event.currentTarget.classList.add('active');
    }
    else {
        const button = document.querySelector(`[data-tab="${tabName}"]`);
        if (button) button.classList.add('active');
    }
    updateHeaderVisibility(tabName);
    // Load data if needed
    if (tabName === 'documents') {
        loadDocuments();
    } else if (tabName === 'search') {
        loadKnowledgeBasesForSearch();
    } else if (tabName === 'tree_files'){
        const managerOverlay = document.getElementById("manager-tree-overlay");
        const defaultHeader = document.getElementById("default-tree-header");
        //  отображаем 1 из 2 интерфейсов в зависимости от роли пользователя
        if (currentUser?.role === "manager") {
            managerOverlay.style.display = "block";
            defaultHeader.style.display = "none";
            loadManagerCollectionInfo();
        } else {
            managerOverlay.style.display = "none";
            defaultHeader.style.display = "flex";
        }
        // строим дерево файловой системы
        loadFilesystemTree();
    } else if (tabName === 'news_send'){
        loadNewsHistory();
    } else if (tabName === 'prompts'){
        loadPromptsTab();
    } else if (tabName === 'bot_settings'){
        loadBotStartMessage();
        loadBotHelpMessage();
    } else if (tabName === 'user_groups'){
        loadUserGroups();
    } else if (tabName === 'analytics'){
        // инициализация автоматического рендера аналитики за последние 7 дней
        initAnalytics();   // выставим даты
        loadAnalytics();   // загрузим
        analyticsLoaded = true;
    } else if (tabName === 'dialogs'){
        updateDialogs()
    }
}

// #############################
// Utilities subsystem
// #############################
// функция управления видимостью заголовка управления коллекциями
function updateHeaderVisibility(tabName) {
    const header = document.getElementById("collections-header");
    const allowedTabs = [
        "documents",
        "search",
        "tree_files"
    ];
    if (
    allowedTabs.includes(tabName) &&
        currentUser?.role !== "manager"
    ) {
        header.style.display = "block";
    } else {
        header.style.display = "none";
    }
}
// функция для открытия и закрытия сайдбара
function toggleSidebar() {
    const sidebar = document.getElementById("sidebar");
    const icon = document.getElementById("toggle-icon");
    sidebar.classList.toggle("expanded");
    sidebar.classList.toggle("collapsed");
    // меняем иконку
    if (sidebar.classList.contains("expanded")) {
        icon.textContent = "✖";
    } else {
        icon.textContent = "☰";
    }
}
// extract question from text
function extractQuestionFromText(text){
    if (!text) return '';
    // search question
    const match = text.match(/Question:\s*(.+?)(?:\n|context:|$)/i);
    if (!match) return text;
    // Убираем возможный "**Вопрос:**"
    return match[1]
        .replace(/\*\*Вопрос:\*\*/gi, '')
        .replace(/\*\*/g, '')
        .trim();
}
// create div format
function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}
// форматирование даты в стандарты
function formatDate(dateString) {
    if (!dateString) return 'Неизвестно';
    try {
        let iso = dateString.trim();
        // если нет timezone → считаем UTC
        if (!iso.endsWith('Z') && !iso.match(/[+-]\d{2}(:\d{2})?$/)) {
            iso = iso.replace(' ', 'T') + 'Z';
        } else {
            iso = iso.replace(' ', 'T');
        }
        const date = new Date(iso);
        if (isNaN(date.getTime())) {
            console.error("Invalid date:", dateString);
            return 'Неправильная дата';
        }
        // ВСЕГДА приводим к Москве
        return new Intl.DateTimeFormat('ru-RU', {
            timeZone: 'Europe/Moscow',
            year: 'numeric',
            month: '2-digit',
            day: '2-digit',
            hour: '2-digit',
            minute: '2-digit',
            second: '2-digit',
            hour12: false
        }).format(date);
    } catch (e) {
        return 'Неправильная дата';
    }
}
// отправка уведомлений
function showNotification(message, type) {
    alert(message);
}
// получение типа коллекции
function getCollectionType(name) {
  if (name.startsWith("faq_")) return "faq";
  if (name.startsWith("kb_")) return "kb";
  return null;
}
// Function to get title for faq or kb
function getDocumentTitle(doc) {
    // FAQ
    const question =
        doc.question_preview ||
        doc.question ||
        doc.payload?.question;
    if (question) {
        return escapeHtml(extractQuestionFromText(question));
    }
    // иначе KB
    const baseTitle =
        doc.source_name ||
        doc.payload?.source_name ||
        doc.source ||
        doc.filename ||
        'Документ';
    let sectionPath = doc.section_path || doc.payload?.section_path;
    if (!sectionPath) {
        return escapeHtml(baseTitle);
    }
    if (typeof sectionPath === "string") {
        // поддержка разных форматов
        if (sectionPath.includes(",")) {
            sectionPath = sectionPath.split(",").map(s => s.trim());
        } else if (sectionPath.includes("/")) {
            sectionPath = sectionPath.split("/").map(s => s.trim());
        } else {
            sectionPath = [sectionPath];
        }
    }
    // Если это массив
    if (Array.isArray(sectionPath)) {
        const cleaned = sectionPath
            .map(s => s.trim())
            .filter(Boolean);   
        const withoutFirst = cleaned.slice(1);
        if (withoutFirst.length > 0) {
            return escapeHtml(
                withoutFirst.join("/") + "/" + baseTitle
            );
        }
    }
    return escapeHtml(baseTitle);
}
// загрузка настроек синхронизации
async function loadSyncSettings() {
    const res = await fetch("/api/sync/settings")
    const data = await res.json()
    document.getElementById("sync-interval").innerText =
        data.interval_hours
}
// изменение интервала синхронизации
async function changeSyncInterval(){
    const current = document.getElementById("sync-interval").innerText
    const hours = prompt("Введите интервал синхронизации в часах", current)
    if(!hours) return;
    const parsedHours = parseInt(hours, 10);
    if (isNaN(parsedHours) || parsedHours <= 0) {
        alert("Пожалуйста, введите действительное положительное число.");
        return;
    }
    const res = await fetch("/api/sync/settings", {
        method:"POST",
        headers:{"Content-Type":"application/json"},
        body:JSON.stringify({hours:parseInt(hours)})
    })
    if(res.ok){
        loadSyncSettings()
        loadCollectionInfo()
        loadManagerCollectionInfo();
    }
}
// функция разворачивания kb 
function toggleKB(kbId) {
    const kbDocs = document.getElementById(`kb-${kbId}`);
    const icon = document.getElementById(`icon-${kbId}`);
    if (kbDocs.style.display === 'none') {
        kbDocs.style.display = 'block';
        icon.textContent = '▼';
    } else {
        kbDocs.style.display = 'none';
        icon.textContent = '▶';
    }
}
// функция извлечения вопроса из FAQ
function parseFaqQuestion(text) {
    if (!text) return "";
    // убираем "Question:"
    let q = text.replace(/^Question:\s*/i, "");
    // отрезаем всё после context:
    q = q.split(/\ncontext:/i)[0];
    return q.trim();
}
// отображение групп пользователей
function getTargetGroupName(group) {
    const names = {
        "all": "Все пользователи",
        "manager_group": "👔 Менеджеры",
        "coach_group": "🎓 Коучи"
    };
    return names[group] || group;
}
// функция сдвига времени
function shiftToUTC(dateStr) {
    if (!dateStr) return "";
    const dt = new Date(dateStr);
    return dt.toISOString();
}
// формат под datetime-local
function formatMSK(dt) {
    return dt.toLocaleString('sv-SE', {
        timeZone: 'Europe/Moscow',
        year: 'numeric',
        month: '2-digit',
        day: '2-digit',
        hour: '2-digit',
        minute: '2-digit',
        hour12: false
    }).replace(',', 'T');
}
// превращаем технический статус в русский текст
function getStatusName(status) {
    const statuses = {
        'pending': 'В ожидании',
        'sent': 'Отправлено',
        'processing': 'Отправляется...',
        'error': 'Ошибка'
    };
    // Если придет какой-то новый статус, которого нет в списке — вернем его как есть
    return statuses[status] || status;
}
// #############################
// DOCUMENTS TAB LOGIC
// #############################

// Documents Management
async function loadDocuments() {
    const container = document.getElementById('documents-list');
    container.innerHTML = '<div class="loading">Загрузка баз знаний...</div>';
    // Обновляем document_count в метаданных (для MCP kb-status)
    fetch(`${API_BASE}/api/collections/refresh_metadata`, { method: 'POST' }).catch(() => {});
    try {
        await fetch(`${API_BASE}/api/collections/refresh_metadata`, { method: 'POST' });
    } catch (e) {
        console.warn("Metadata refresh failed, continuing...", e);
    }
    try {
        const response = await fetch(`${API_BASE}/api/knowledge-bases`);
        const knowledgeBases = await response.json();
        if (knowledgeBases.length === 0) {
            container.innerHTML = `
                <div class="empty-state">
                    <div class="icon">📭</div>
                    <p>Документы не найдены. Загрузите свой первый документ, чтобы начать!</p>
                </div>
            `;
            loadCollectionInfo();
            loadManagerCollectionInfo();
            return;
        }
        container.innerHTML = knowledgeBases.map(kb => `
            <div class="kb-section">
                <div class="kb-header" onclick="toggleKB('${escapeHtml(kb.kb_id)}')">
                    <div class="kb-title">
                        <span class="kb-icon" id="icon-${escapeHtml(kb.kb_id)}">▼</span>
                        <strong>📚 ${escapeHtml(kb.kb_id)}</strong>
                    </div>
                    <div class="kb-stats">
                        <button
                            class="sync-kb-btn btn btn-primary btn-small"
                            data-kb-id="${escapeHtml(kb.kb_id)}">
                            🔄 Синхронизация БЗ
                        </button>
                        <span class="badge badge-primary">${kb.document_count} Документы</span>
                        <span class="badge badge-secondary">${kb.total_chunks} Чанки</span>
                        <button
                            class="btn btn-danger btn-small"
                            title="Delete knowledge base"
                            onclick="event.stopPropagation(); deleteKnowledgeBase('${escapeHtml(kb.kb_id)}')"
                        >
                            🗑️ Удалить
                        </button>
                    </div>
                </div>
                <div class="kb-documents" id="kb-${escapeHtml(kb.kb_id)}" style="display: block;">
                    ${kb.documents.map(doc => `
                        <div class="document-card">
                            <div class="document-header">
                                <div class="document-title">📄 ${getDocumentTitle(doc)}</div>
                                <div class="document-actions">
                                    <button 
                                        class="view-doc-btn btn btn-secondary btn-small"
                                        data-doc-id="${doc.document_id}"
                                        data-doc-name="${doc.source_name || doc.source}">
                                        👁️ Посмотреть
                                    </button>
                                    <button 
                                        class="delete-doc-btn btn btn-danger btn-small"
                                        data-doc-id="${doc.document_id}"
                                        data-doc-name="${doc.source_name || doc.source}">
                                        🗑️ Удалить
                                    </button>
                                </div>
                            </div>
                            <div class="document-meta">
                                <div class="meta-item">
                                    <span class="badge badge-primary">${doc.chunks_count ?? doc.total_chunks ?? '?'} чанки</span>
                                </div>
                                <div class="meta-item">
                                    <span class="badge badge-success">${doc.source_type || 'chunk'}</span>
                                </div>
                                <div class="meta-item">
                                    👤 ${escapeHtml(doc.user_id || 'robot')}
                                </div>
                                <div class="meta-item">
                                    v${doc.version || 1}
                                </div>
                                <div class="meta-item">
                                    🕒 ${formatDate(doc.created_at || null)}
                                </div>
                            </div>
                        </div>
                    `).join('')}
                </div>
            </div>
        `).join('');
        loadCollectionInfo();
        loadManagerCollectionInfo();
        loadFilesystemTree();
    } catch (error) {
        container.innerHTML = `
            <div class="result-message error">
                Ошибка загрузки документов: ${error.message}
            </div>
        `;
    }
}
// вызовы функций, чтобы не ломались на плохих названиях 
document.addEventListener("click", function (e) {
    // function to view Document 
    if (e.target.classList.contains("view-doc-btn")) {
        const docId = e.target.dataset.docId;
        const docName = e.target.dataset.docName;
        viewDocument(docId, docName);
    }
    if (e.target.classList.contains("delete-doc-btn")) {
        // to delete document
        const docId = e.target.dataset.docId;
        const docName = e.target.dataset.docName;
        deleteDocument(docId, docName);
    }
});
// функция для синхронизации рялом с kb конкретным
document.addEventListener("click", async function (e) {
    if (
            !e.target.classList.contains(
                "sync-kb-btn"
            )
        ) {
            return;
        }
        e.stopPropagation();
        const button =
            e.target;
        const kbId =
            button.dataset.kbId;
        const originalText =
            button.innerText;
        try {
            button.disabled = true;
            button.innerText =
                "⏳ Запуск...";
            await startSyncTask({
                mode: "kb",
                collection_name:
                    currentCollection,
                kb_id: kbId
            });
        } catch (error) {
            console.error(error);
            alert(
                `Ошибка: ${error.message}`
            );
        } finally {
            button.disabled = false;
            button.innerText =
                originalText;
        }
    }
);
// подписка на очередь событий для отслеживания автоматического обновления 
// при синхронизации атомарной
function subscribeToSync() {
    const eventSource = new EventSource("/api/filesystem/sync_events");
    eventSource.onmessage = function (event) {
        if (event.data === "sync_completed") {
            loadDocuments();
            loadFilesystemTree();
        }
    };
    eventSource.onerror = function () {
        console.error("SSE error");
        eventSource.close();
    };
}
// функция для запуска задачи синхронизации с логами
async function startSyncTask(payload) {
    try {
        const response = await fetch(
            "/api/sync/start",
            {
                method: "POST",
                headers: {
                    "Content-Type":
                        "application/json"
                },
                body: JSON.stringify(
                    payload
                )
            }
        );
        const data =
            await response.json();
        if (!response.ok) {
            throw new Error(
                data.message ||
                "Ошибка запуска"
            );
        }
        await watchSyncTask(
            data.task_id
        );
        return data.task_id;
    } catch (error) {
        console.error(error);
        alert(
            `Ошибка запуска синхронизации: ${error.message}`
        );
        throw error;
    }
}
// открытие модального окна статуса синхронизации
function openSyncStatusModal() {
    document
        .getElementById(
            "sync-status-modal"
        )
        .style.display = "block";
}
// закрытие модального окна статуса синхронизации
function closeSyncStatusModal() {
    document
        .getElementById(
            "sync-status-modal"
        )
        .style.display = "none";
}
// функция для отслеживания статуса задачи синхронизации и обновления UI в реальном времени
async function watchSyncTask(taskId) {
    openSyncStatusModal();
    const statusBox =
        document.getElementById(
            "sync-status-text"
        );
    const progressBox =
        document.getElementById(
            "sync-progress"
        );
    const progressBar =
        document.getElementById(
            "sync-progress-bar"
        );
    const kbBox =
        document.getElementById(
            "sync-current-kb"
        );
    const logBox =
        document.getElementById(
            "sync-log-box"
        );
    statusBox.innerText =
    "Подготовка...";
    progressBox.innerText =
        "-";
    progressBar.style.width =
        "0%";
    kbBox.innerText =
        "-";
    logBox.innerHTML =
        `<div class="sync-log-line">
            Ожидание запуска...
        </div>`;
    let firstResponseReceived =
        false;
    let pollErrors = 0;
    const interval = setInterval(
        async () => {
            try {
                const response =
                    await fetch(
                        `/api/sync/status/${taskId}`
                    );
                const data =
                    await response.json();
                statusBox.innerText =
                    data.status || "-";
                progressBox.innerText =
                    `${data.progress || 0}%`;
                progressBar.style.width =
                    `${data.progress || 0}%`;
                kbBox.innerText =
                    data.current_kb || "-";
                // от менеджера скрываем подробные логи, показывая только статус синхронизации
                if ( currentUser?.role ==="manager") {
                    let managerMessage =
                        "Синхронизация выполняется...";
                    if (
                        data.status === "completed"
                    ) {
                        managerMessage =
                            "✅ Синхронизация выполнена";
                    } else if (
                        data.status === "error"
                    ) {
                        managerMessage =
                            "❌ Ошибка синхронизации";
                    }
                    logBox.innerHTML =
                        `
                        <div class="sync-log-line">
                            ${managerMessage}
                        </div>
                        `;
                } else {
                    logBox.innerHTML =
                        (data.logs || [])
                        .map(
                            log =>
                                `
                                <div class="sync-log-line">
                                    [${log.time}]
                                    ${log.message}
                                </div>
                                `
                        )
                        .join("");
                }
                logBox.scrollTop =
                    logBox.scrollHeight;
                if (
                    data.status === "completed"
                ) {
                    clearInterval(
                        interval
                    );
                    await loadDocuments();
                    await loadCollectionInfo();
                    await loadManagerCollectionInfo();
                    await loadFilesystemTree();
                    statusBox.innerText =
                        "Завершено";
                    return;
                }
                if (
                    data.status === "error"
                ) {
                    clearInterval(
                        interval
                    );
                    statusBox.innerText =
                        "Ошибка";
                    return;
                }
            } catch (error) {
                // retry logic для kuber: если 5 раз подряд не удается получить статус, показываем ошибку и останавливаем поллинг
                console.error(error);
                pollErrors++;
                if (pollErrors >= 5) {
                    clearInterval(interval);
                    statusBox.innerText =
                        "Соединение потеряно";
                }
            }
        },
        1500
    );
}
// функция для синхронизации по всем данным
async function syncAll(btnElement) {
    // 1. Защита: если кнопка не передана, выходим
    if (!btnElement) {
        console.error("Кнопка не передана в функцию syncAll!");
        return;
    }
    // Сохраняем оригинальный текст и состояние
    const originalText =
        btnElement.innerText;
    try {
        btnElement.disabled = true;
        btnElement.innerText =
            "⏳ Запуск...";
        await startSyncTask({
            mode: "all"
        });

    } catch (error) {
        console.error(error);
    } finally {
        btnElement.disabled = false;
        btnElement.innerText =
            originalText;
    }
}
// синхронизация конкретной коллекции
async function syncCurrentCollection(btnElement) {
    if (!currentCollection) {
        alert("Коллекция не выбрана");
        return;
    }
    const originalText = btnElement.innerText;
    try {
        btnElement.disabled = true;
        btnElement.innerText = "⏳ Синхронизация...";
        await startSyncTask({
            mode: "collection",
            collection_name:
                currentCollection
        });
    } catch (error) {
        console.error(error);
        alert(error.message);
    } finally {
        btnElement.disabled = false;
        btnElement.innerText =
            originalText;
    }
}
// удаление баз знаний
async function deleteKnowledgeBase(kbId) {
    if (!confirm(`Удалить базу знаний "${kbId}"?\n\n Все документы будут немедленно удалены.`)) {
        return;
    }
    try {
        const res = await fetch(`${API_BASE}/api/knowledge-bases/delete`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                kb_id: kbId,
                collection_name: currentCollection
            })
        });
        const data = await res.json();
        if (!res.ok) {
            throw new Error(data.detail || "Не удалось удалить базу знаний");
        }
        if (res.ok) {
            showNotification('База знаний успешно удалена', 'success');
            loadDocuments();
        }
    } catch (err) {
        alert(`Ошибка удаления базы знаний: ${err.message}`);
    }
}
// возможность открытия просмотра документа
async function viewDocument(documentId, filename) {
    const modal = document.getElementById('chunks-modal');
    const modalTitle = document.getElementById('modal-title');
    const modalBody = document.getElementById('modal-body');
    modalTitle.textContent = `Чанки: ${filename}`;
    modalBody.innerHTML = '<div class="loading">Загрузка чанков...</div>';
    modal.classList.add('active');
    try {
        const response = await fetch(`${API_BASE}/api/documents/${documentId}`);
        const chunks = await response.json();
        const isFAQ = currentCollectionType === 'faq';
        modalBody.innerHTML = chunks.map(chunk => {
            const charCount = chunk.text.length;
            // Backend returns metadata as "payload"
            const meta = chunk.metadata || chunk.payload || {};
            const chunkIndex = chunk.chunk_index ?? (chunk.chunk_id ? parseInt(chunk.chunk_id.split('#')[1]): 0);
            const answer = chunk.answer
            const question = extractQuestionFromText(chunk.text);
            const cleanMeta = {
                source_name: meta.source_name || meta.source || 'неизвестно',
                kb_id: meta.kb_id || 'N/A',
                user_id: meta.user_id || 'N/A',
                source_type: meta.source_type || 'N/A',
                version: meta.version || 1,
                document_id: meta.document_id || '',
                created_at: meta.created_at || 'Неизвестно',
                section_path: meta.section_path || []
            };
            return `
            <div class="chunk-item-compact">
                <div class="chunk-header-compact">
                    <strong>Чанк ${chunkIndex + 1}</strong> (${charCount} символов)
                </div>
                <div class="result-text">${escapeHtml(isFAQ? question+" - "+ answer : chunk.text)}</div>
                <div class="chunk-metadata-json">
                    <pre>${escapeHtml(JSON.stringify(cleanMeta, null, 2))}</pre>
                </div>
            </div>
        `}).join('');
    } catch (error) {
        modalBody.innerHTML = `
            <div class="result-message error">
                Ошибка загрузки чанков: ${error.message}
            </div>
        `;
    }
}
// удаление документа
async function deleteDocument(documentId, filename) {
    if (!confirm(`Вы уверены, что хотите удалить "${filename}"?`)) {
        return;
    }
    try {
        const response = await fetch(`${API_BASE}/api/documents/${documentId}`, {
            method: 'DELETE'
        });
        if (response.ok) {
            showNotification('Документ успешно удален', 'success');
            loadDocuments();
        } else {
            throw new Error('Не удалось удалить документ');
        }
    } catch (error) {
        showNotification(`Ошибка при удалении документа: ${error.message}`, 'error');
    }
}
// Modal для просмотра чанков файла
function closeModal() {
    document.getElementById('chunks-modal').classList.remove('active');
}
// Close modal on outside click
document.getElementById('chunks-modal').addEventListener('click', (e) => {
    if (e.target.id === 'chunks-modal') {
        closeModal();
    }
});

// #############################
// SEARCH TAB LOGIC
// #############################
// Load knowledge bases for search filter
async function loadKnowledgeBasesForSearch() {
    try {
        const response = await fetch(`${API_BASE}/api/knowledge-bases`);
        const knowledgeBases = await response.json();
        const kbSelect = document.getElementById('search-kb');
        const currentValue = kbSelect.value;
        kbSelect.innerHTML = '<option value="">Все базы знаний</option>' + 
            knowledgeBases.map(kb => 
                `<option value="${escapeHtml(kb.kb_id)}">${escapeHtml(kb.kb_id)} (${kb.document_count} документы)</option>`
            ).join('');
        // Restore previous selection if it still exists
        if (currentValue) {
            kbSelect.value = currentValue;
        }
    } catch (error) {
        console.error('Error loading knowledge bases:', error);
    }
}
// Search
function handleSearchKeypress(event) {
    if (event.key === 'Enter') {
        performSearch();
    }
}
// поиск внутри kb-manager
async function performSearch() {
    const query = document.getElementById('search-query').value.trim();
    const limit = parseInt(document.getElementById('search-limit').value);
    const kbId = document.getElementById('search-kb').value;
    const resultsContainer = document.getElementById('search-results');
    
    if (!query) {
        resultsContainer.innerHTML = `
            <div class="empty-state">
                <div class="icon">🔍</div>
                <p>Введите поисковой запрос, чтобы найти релевантные документы</p>
            </div>
        `;
        return;
    }
    resultsContainer.innerHTML = '<div class="loading">Поиск...</div>';
    try {
        // Build request body with optional filters
        const requestBody = { query, limit };
        if (kbId) {
            requestBody.filters = { kb_id: kbId };
        }
        const response = await fetch(`${API_BASE}/api/search`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify(requestBody)
        });
        if (!response.ok) {
            const err = await response.text();
            throw new Error(err);
        }
        const results = await response.json();
        if (results.length === 0) {
            resultsContainer.innerHTML = `
                <div class="empty-state">
                    <div class="icon">🤷</div>
                    <p>Не найдены результаты для вашего запроса</p>
                </div>
            `;
            return;
        }
        resultsContainer.innerHTML = results.map(r => {
            const isFAQ = currentCollectionType==="faq";
            const chunkIndex = (r.chunk_index ?? 0) + 1;
            const score = typeof r.score === 'number'
                ? r.score.toFixed(3)
                : '—';
            const question = isFAQ?parseFaqQuestion(r.text || 'unknown'): "";
            const answer = isFAQ? (r.answer || ''): "";
            const title = `${isFAQ? question || 'FAQ': r.source_name || r.source || 'Unknown'}`;
            const text = isFAQ ? `${question}${answer ? "\n\n"+answer: ""}` : r.text || '';
            const cleanMeta = {
                source_name: r.source_name || 'unknown',
                kb_id: r.kb_id || 'N/A',
                user_id: r.user_id || 'N/A',
                source_type: r.source_type || 'N/A',
                document_id: r.document_id || '',
                created_at: r.created_at || 'Unknown',
                score: score
            }
            return `
                <div class="chunk-item-compact">
                    <div class="chunk-header-compact">
                        <strong>
                            ${escapeHtml(title)} — Чанк ${chunkIndex}
                        </strong>
                        <span class="chunk-score">Точность: ${score}</span>
                    </div>
                    <div class="result-text">
                        ${escapeHtml(text)}
                    </div>
                    <div class="chunk-metadata-json">
                        <pre>${escapeHtml(JSON.stringify(cleanMeta, null, 2))}</pre>
                    </div>
                </div>
            `;
        }).join('');
    } catch (error) {
        resultsContainer.innerHTML = `
            <div class="result-message error">
                Ошибка при выполнении поиска: ${error.message}
            </div>
        `;
    }
}

// #############################
// UPLOAD TAB LOGIC
// #############################

// загрузка таблиц postgres
async function loadTables() {
    const btn = document.getElementById("load-tables-btn");
    const progress = document.getElementById("tables-load-progress");
    const result = document.getElementById("tables-load-result");
    btn.disabled = true;
    progress.style.display = 'block';
    try {
        const response = await fetch('/api/tables/load', {
            method: 'POST'
        });
        const data = await response.json();
        if (!response.ok) {
            throw new Error(data.detail || 'Ошибка загрузки таблиц');
        }
        const filteredLog = data.stdout
            .split("\n")
            .filter(line => !line.startsWith("dc_"))
            .join("\n");
        showLoadLog(data.stdout);
        document.getElementById("load-log-content").textContent =
            filteredLog;
        result.className = "result-message success";
        result.innerHTML =
            "✓ Таблицы успешно обновлены";
        renderTables(data.tables);
    } catch (error) {
        result.className =
            "result-message error";
        result.innerHTML =
            error.message;
    } finally {
        progress.style.display = "none";
        result.style.display = "block";
        btn.disabled = false;
    }
}
function showLoadLog(logText) {

    const container =
        document.getElementById(
            "load-log-container"
        );

    const content =
        document.getElementById(
            "load-log-content"
        );

    const filteredLog = logText
        .split("\n")
        .filter(line => !line.startsWith("dc_"))
        .join("\n");

    content.textContent = filteredLog;

    container.classList.remove(
        "load-log-hidden"
    );
}

function hideLoadLog() {

    document
        .getElementById(
            "load-log-container"
        )
        .classList.add(
            "load-log-hidden"
        );
}

// отображение информации о таблице в модальном окне
async function showTableInfo(tableName) {
    const modal = document.getElementById("table-info-modal");
    const modalTitle = document.getElementById("modal-table-title");
    const loadingDiv = document.getElementById("modal-loading");
    const contentDiv = document.getElementById("modal-content");
    const tableHead = document.getElementById("table-data-head");
    const tableBody = document.getElementById("table-data-body");
    modalTitle.innerText = `Загрузка: ${tableName}`;
    tableHead.innerHTML = "";
    tableBody.innerHTML = "";
    // 2. УПРАВЛЕНИЕ ВИДИМОСТЬЮ (сначала скрываем контент, показываем лоадер)
    contentDiv.style.display = "none";
    loadingDiv.style.display = "block";
    // 3. Открываем саму модалку
    modal.style.display = "flex";
    try {
        // Делаем запрос к API
        const response = await fetch(`/api/tables/${encodeURIComponent(tableName)}`);
        if (!response.ok) {
            throw new Error("Не удалось получить информацию о таблице");
        }
        const data = await response.json();
        // Заполняем данные
        modalTitle.innerText = `Таблица: ${data.table}`; // Меняем заголовок на правильный
        // заголовок таблицы
        tableHead.innerHTML = `
            <tr>
                ${data.columns.map(
                    c => `<th>${c}</th>`
                ).join("")}
            </tr>
        `;
        // строки таблицы
        tableBody.innerHTML = data.data
            .map(row => `
                <tr>
                    ${data.columns.map(
                        c => `<td>${row[c] ?? ""}</td>`
                    ).join("")}
                </tr>
            `)
            .join("");
        loadingDiv.style.display = "none";
        contentDiv.style.display = "block";
    } catch (error) {
        loadingDiv.style.display = "none";
        alert("Ошибка: " + error.message);
        closeTableModal();
    }
}
// обновление списка таблиц (вызывается после загрузки новых таблиц)
async function refreshTablesList() {
    const tablesList = document.getElementById("tables-list");
    try {
        const response = await fetch("/api/tables");
        if (!response.ok) {
            throw new Error("Не удалось получить список таблиц");
        }
        const data = await response.json();
        renderTables(data.tables);
    } catch (error) {
        tablesList.innerHTML = `
            <div class="result-message error">
                ${error.message}
            </div>
        `;
    }
}
// рендер таблиц 
function renderTables(tables) {
    const tablesList = document.getElementById("tables-list");
    tablesList.innerHTML = `
        <h4>Текущие таблицы:</h4>
        <div class="tables-grid">
            ${tables.map(t => `
                <button
                    class="table-card"
                    onclick="showTableInfo('${t}')"
                >
                    🗂️ ${t}
                </button>
            `).join("")}
        </div>
    `;
}
// закрытие модального окна с информацией о таблице
function closeTableModal() {
    document.getElementById("table-info-modal").style.display = "none";
}

// #############################
// TREE TAB LOGIC
// #############################

// построение дерева файлов
async function loadFilesystemTree() {
    const container = document.getElementById("filesystem-tree");
    container.innerHTML = "⏳ Загрузка...";
    try {
        // MANAGER -> полное дерево kb_collection
        if (currentUser?.role === "manager") {
            const res = await fetch("/api/filesystem/folders");
            const data = await res.json();
            container.innerHTML = renderManagerTree(data);
            return;
        }
        // ADMIN / обычный режим → lazy loading по коллекциям
        const res = await fetch(
            `/api/filesystem/node?path=&collection_name=${encodeURIComponent(currentCollection)}`
        );
        const data = await res.json();
        container.innerHTML = renderNode("", data);
    } catch (err) {
        container.innerHTML = "❌ Ошибка загрузки дерева";
    }
}
// рендеринг дерева
function renderNode(path, data) {
    let html = "<ul class='tree'>";
    data.folders.forEach(folder => {
        const newPath = path ? `${path}/${folder}` : folder;
        html += `
            <li class="folder">
                <span class="folder-toggle" data-path="${newPath}" data-loaded="false">
                    📁 ${escapeHtml(folder)}
                </span>
                <div class="folder-content"></div>
            </li>
        `;
    });
    data.files.forEach(file => {
        html += `<li class="file">📄 ${escapeHtml(file)}</li>`;
    });
    html += "</ul>";
    return html;
}

// рендеринг полного дерева для менеджера
function renderManagerTree(tree, currentPath = "") {
    let html = "<ul class='tree'>";
    for (const [key, value] of Object.entries(tree)) {
        // files
        if (key === "files" && Array.isArray(value)) {
            value.forEach(file => {
                html += `
                    <li class="file">
                        📄 ${escapeHtml(file)}
                    </li>
                `;
            });
            continue;
        }
        // folders
        const newPath = currentPath
            ? `${currentPath}/${key}`
            : key;
        html += `
            <li class="folder">
                <details>
                    <summary>
                        📁 ${escapeHtml(key)}
                    </summary>
                    ${renderManagerTree(value, newPath)}
                </details>
            </li>
        `;
    }
    html += "</ul>";
    return html;
}
// открытие папок внутри дерева
document.addEventListener("click", async function (e) {
    if (!e.target.classList.contains("folder-toggle")) return;
    const toggle = e.target;
    const content = toggle.nextElementSibling;
    const path = toggle.dataset.path;
    if (toggle.dataset.loaded === "false") {
        try {
            content.innerHTML = "⏳ Загрузка...";
            const res = await fetch(
                `/api/filesystem/node?path=${encodeURIComponent(path)}&collection_name=${encodeURIComponent(currentCollection)}`
            );
            const data = await res.json();
            content.innerHTML = renderNode(path, data);
            toggle.dataset.loaded = "true";
        } catch (err) {
            content.innerHTML = "❌ Ошибка";
        }
    }
    content.classList.toggle("open");
});

// #############################
// NEWS TAB LOGIC
// #############################

// отправка новостей
async function sendNews() {
    const html = newsEditor.root.innerHTML.trim();
    // Проверка что не пусто
    if (!html || html === "<p><br></p>") {
        alert("Введите текст новости");
        return;
    }
    const resultDiv = document.getElementById("news-result");
    const scheduleTimeEl = document.getElementById("news-schedule-time");
    const scheduleTime = scheduleTimeEl.value;
    const sendBtn = document.getElementById("news-send-btn");
    const fileInput = document.getElementById("news-files");
    const reusePath = fileInput.dataset.reusePath;
    // выбранная группа получателей
    const targetGroupEl = document.querySelector('input[name="news-target-group"]:checked');
    const targetGroup = targetGroupEl ? targetGroupEl.value : "all";
    const formData = new FormData();
    if (fileInput.files.length > 0) {
        // пользователь выбрал новый файл → ПЕРЕЗАТИРАЕМ reuse
        formData.append("files", fileInput.files[0]);
        console.log("Using NEW file");
    } else if (reusePath && reusePath.trim() !== "") {
        // если новый не выбран → используем старый
        formData.append("reuse_file_path", reusePath);
        console.log("Reusing OLD file");
    } else {
        console.log("No file attached");
    }
    formData.append("html", html);
    formData.append("target_group", targetGroup);
    if (scheduleTime) {
        const utcTime = new Date(scheduleTime).toISOString();
        const now = new Date().toISOString();
        if (utcTime <= now){
            alert("❌ Нельзя выбрать прошедшее время");
            return;
        }
        formData.append("schedule_time", utcTime);
    }
    // Блокировка кнопки
    sendBtn.disabled = true;
    sendBtn.innerText = "⏳ Отправка...";
    resultDiv.innerHTML = "";
    try {
        const res = await fetch("/api/news/send", {
            method: "POST",
            body: formData
        });
        const data = await res.json();
        const recipients = data.count ?? data.sent ?? 0;
        if (res.ok) {
            resultDiv.innerHTML = `
                ✅ Отправлено!<br>
                📬 Получателей: ${recipients}<br>
                👥 Группа: ${getTargetGroupName(targetGroup)}
            `;
            newsEditor.setText("");
            // Очищаем форму
            fileInput.value = "";
            document.getElementById("news-file-info").style.display = "none";
            delete fileInput.dataset.reusePath;
        } else {
            throw new Error(data.detail || "Ошибка отправки");
        }
        if (scheduleTime){
            alert(`📅 Новость запланирована \n📬 Получателей: ${recipients}`);
        } else{
            alert(`📤 Отправлено сразу \n📬 Получателей: ${recipients}`);
        }
    } catch (err) {
        resultDiv.innerHTML = `❌ Ошибка: ${err.message}`;
    } finally {
        sendBtn.disabled = false;
        sendBtn.innerText = "📤 Отправить новость";
        await loadNewsHistory();
    }
}
// удаление файла прикрепленного к новости
document.getElementById("news-file-remove").onclick = () => {
    const fileInput = document.getElementById("news-files");
    // удаляем reuse
    delete fileInput.dataset.reusePath;
    // чистим input
    fileInput.value = "";
    // скрываем UI
    document.getElementById("news-file-info").style.display = "none";
    console.log("File removed (reuse cleared)");
};
// добавление файла прикрепленного к новости
document.getElementById("news-files").addEventListener("change", (e) => {
    const fileInput = e.target;
    if (fileInput.files.length > 0) {
        // пользователь выбрал новый файл → убираем reuse
        delete fileInput.dataset.reusePath;
        const file = fileInput.files[0];
        document.getElementById("news-file-name").textContent = file.name;
        document.getElementById("news-file-info").style.display = "flex";
        console.log("New file selected, reuse cleared");
    }
});
// загрузка истории новостей
async function loadNewsHistory() {
    const container = document.getElementById("news-history");
    const isFirstLoad = container.innerHTML.includes("loading") || container.innerHTML === "";
    if (isFirstLoad) {
        container.innerHTML = '<div class="loading">Загрузка...</div>';
    }
    try {
        const res = await fetch("/api/news");
        if (!res.ok) {
            throw new Error("Ошибка загрузки новости");
        }
        const data = await res.json();
        if (!data || data.length === 0) {
            container.innerHTML = `
                <div class="empty-state">
                    <div class="icon">📭</div>
                    <p>Нет новостей</p>
                </div>
            `;
            stopNewsPolling(); // Останавливаем опрос, если новостей нет
            return;
        }
        container.innerHTML = data.map(n =>{
            const files = n.files || [];
            const targetGroup = n.target_group || "all";
            const statusRussian = getStatusName(n.status);
            const filesHtml = files.length > 0
                ? files.map(f => `
                    <div class="news-file">
                        📎 ${escapeHtml(f.name)}
                        <button 
                            class="btn btn-small btn-secondary"
                            onclick="viewNewsFile('${f.name}')">
                            Посмотреть файл 👁
                        </button>
                    </div>
                `).join("")
                : '<div class="news-no-files">Без файлов</div>';
            return `
                <div class="document-card" ${n.status === 'pending' ? 'status-pending' : ''}">
                    <div class="document-header">
                        <div class="document-title">
                            📰 ID: ${n.id}
                        </div>
                        <button 
                            class="btn btn-primary btn-small"
                            onclick="reuseNewsById(${n.id})">
                            📋  Редактировать и отправить
                        </button>
                        <button 
                            class="btn btn-primary btn-danger btn-small"
                            onclick="deleteNews(${n.id})">
                            🗑️ Удалить
                        </button>
                    </div>
                    <div class="news-meta">
                        <span> Создано: ${formatDate(n.created_at)}</span>
                        <span> Отправлено: ${n.scheduled_at ? formatDate(n.scheduled_at) : formatDate(n.created_at)}</span>
                        <span> Статус: ${statusRussian}</span>
                        <span> Получатели: ${getTargetGroupName(targetGroup) || n.target_group || "all"}</span>
                    </div>
                    <div class="news-content">
                        ${n.text || ""}
                    </div>
                    <div class="news-files">
                        ${filesHtml}
                    </div>
                </div>
            `; 
        }).join("");
        // --- ЛОГИКА АВТО-ОБНОВЛЕНИЯ ---
        // Проверяем, есть ли хотя бы одна новость в статусе "pending"
        const hasPending = data.some(n => n.status === "pending" || n.status === "processing");
        if (hasPending) {
            console.log("Есть ожидающие новости, запускаю поллинг...");
            startNewsPolling();
        } else {
            stopNewsPolling();
        }
    } catch (e) {
        console.error(e);
        if (isFirstLoad) container.innerHTML = `<div class="result-message error">Ошибка: ${e.message}</div>`;
    }
}
// Функция запуска опроса
function startNewsPolling() {
    if (newsHistoryTimer) return; // Чтобы не плодить кучу таймеров
    newsHistoryTimer = setInterval(() => {
        // Проверяем, активна ли вкладка новостей (чтобы не грузить сервер вхолостую)
        const newsTab = document.getElementById("news_send-tab");
        if (newsTab && newsTab.classList.contains("active") || newsTab.style.display !== "none") {
            loadNewsHistory();
        }
    }, 10000); // 10 секунд — оптимально для новостей
}
// Функция остановки опроса
function stopNewsPolling() {
    if (newsHistoryTimer) {
        clearInterval(newsHistoryTimer);
        newsHistoryTimer = null;
    }
}
// функция удаления новости из истории
async function deleteNews(id) {
    const confirmDelete = confirm("Удалить новость из истории?");
    if (!confirmDelete) return;
    try {
        const res = await fetch(`/api/news/${id}`, {
            method: "DELETE"
        });
        if (!res.ok) {
            const data = await res.json();
            throw new Error(data.detail || "Ошибка удаления");
        }
        showNotification("Новость удалена", "success");
        // перезагрузка списка
        loadNewsHistory();
    } catch (e) {
        alert("Ошибка: " + e.message);
    }
}                   
// просмотр файла новости
async function viewNewsFile(name) {
    try {
        const res = await fetch(`/api/local-file-news?name=${encodeURIComponent(name)}`);
        if (!res.ok) {
            throw new Error("Ошибка загрузки файла");
        }
        const contentType = res.headers.get("content-type") || "";
        const fileExt = name.split('.').pop().toLowerCase();
        const textExtensions = ['md', 'txt', 'json', 'csv', 'xml', 'html', 'htm'];
        const isTextFile = textExtensions.includes(fileExt);
        // текст / markdown / json - показываем в модалке
        if (isTextFile || contentType.includes("text")) {
            const text = await res.text();
            // Сохраняем форматирование с помощью white-space: pre-wrap
            document.getElementById("file-content").innerHTML = `
                <div class="file-content-text">
                    ${text.replace(/</g, "&lt;").replace(/>/g, "&gt;")}
                </div>
            `;
            document.getElementById("file-modal").style.display = "block";
        } 
        // pdf / изображения — тоже в модалку
        else if (contentType.includes("pdf") || contentType.includes("image")) {
            const blob = await res.blob();
            const url = URL.createObjectURL(blob);
            document.getElementById("file-content").innerHTML = `
                <iframe src="${url}" class="file-content-iframe"></iframe>
            `;
            document.getElementById("file-modal").style.display = "block";
        }
        else {
            // Остальные файлы - скачиваем
            const blob = await res.blob();
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = name;
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            URL.revokeObjectURL(url);
        }
    } catch (e) {
        alert("Ошибка: " + e.message);
    }
}
// переиспользование новости по id
async function reuseNewsById(id) {
    const res = await fetch(`/api/news/${id}`);
    const news = await res.json();
    reuseNews(news);
}
// функция переиспользования новости
function reuseNews(news) {
    // 1. текст
    if (newsEditor && news.text) {
        newsEditor.setText(""); // очистка 
        newsEditor.clipboard.dangerouslyPasteHTML(news.text);
    }
    // востанавливаем группу получателей
    const targetGroup = news.target_group || "all";
    const groupRadio = document.querySelector(`input[name="news-target-group"][value="${targetGroup}"]`);
    if (groupRadio) {
        groupRadio.checked = true;
    }
    // 2. файл
    const fileInput = document.getElementById("news-files");
    const fileInfo = document.getElementById("news-file-info");
    const fileName = document.getElementById("news-file-name");
    if (news.files && Array.isArray(news.files) && news.files.length > 0) {
        const f = news.files[0];
        fileName.textContent = f.name + " (reuse)";
        fileInfo.style.display = "flex";
        // сохраняем путь
        fileInput.dataset.reusePath = f.path || "";
        // очищаем input (на всякий)
        fileInput.value = "";
    } else {
        fileInput.value = "";
        fileInfo.style.display = "none";
        delete fileInput.dataset.reusePath;
    }
    showNotification("Новость загружена как шаблон", "success");
}
// закрытие модальности просмотра новости
function closeFileModal() {
    const modal = document.getElementById("file-modal");
    modal.style.display = "none";
    // чистим контент
    document.getElementById("file-content").innerHTML = "";
}
// #############################
// PROMPTS TAB LOGIC
// #############################
// Загрузка вкладки Prompts новой логики
async function loadPromptsTab() {
    const container = document.getElementById("agents-list");
    container.innerHTML = '<div class="loading">Загрузка агентов...</div>';
    try {
        const res = await fetch("/api/prompts/agents");
        const agents = await res.json();
        if (!agents.length) {
            container.innerHTML = `
                <div class="empty-state">
                    <div class="icon">🤖</div>
                    <p>Агенты не найдены</p>
                </div>
            `;
            return;
        }
        container.innerHTML = agents.map(agent => `
            <div class="document-card">
                <div class="document-header">
                    <div class="document-title">🤖 ${agent}</div>
                    <div class="document-actions">
                        <button 
                            class="btn btn-primary btn-small"
                            onclick="openAgent('${agent}')">
                            👁️ Открыть агента
                        </button>
                    </div>
                </div>
            </div>
        `).join("");
    } catch (err) {
        container.innerHTML = `<div class="result-message error">Ошибка загрузки агентов</div>`;
        console.error(err);
    }
}
// открытие агента 
async function openAgent(agent) {
    currentAgent = agent;
    // инициализация редактора, если еще не создан
    if (!promptEditorMDE) {
        promptEditorMDE = new EasyMDE({
            element: document.getElementById("prompt-editor"),
            spellChecker: false,
            status: false,
        });
    }
    // открываем модалку
    const modal = document.getElementById("agent-modal");
    if (modal) {
        modal.classList.add("active");
    }
    // заголовок модалки
    const title = document.getElementById("agent-modal-title");
    if (title) {
        title.textContent = `🤖 ${agent}`;
    }
    // сбрасываем UI внутри
    const filesList = document.getElementById("prompt-files-list");
    if (filesList) {
        filesList.innerHTML = '<div class="loading">Загрузка...</div>';
    }
    if (promptEditorMDE) {
        promptEditorMDE.value("");
    }
    // загружаем файлы агента
    await loadPromptFiles();
    // грузим текущий промпт 
    await loadCurrentPrompt();
}
// закрытие окна агента
function closeAgentModal() {
    const modal = document.getElementById("agent-modal");
    if (modal) {
        modal.classList.remove("active");
    }
    currentAgent = null;
}
// Загрузка списка файлов промптов
async function loadPromptFiles() {
    const filesList = document.getElementById("prompt-files-list");
    filesList.innerHTML = '<div class="loading">Загрузка...</div>';
    try {
        const res = await fetch(`/api/prompts/list?agent=${currentAgent}`);
        const data = await res.json();
        promptFiles = data.files || [];
        if (promptFiles.length === 0) {
            filesList.innerHTML = '<div class="empty-state">Нет файлов промптов</div>';
            return;
        }
        filesList.innerHTML = promptFiles.map(file => `
            <div class="prompt-file-item ${file.is_current ? 'current-prompt' : ''} ${file.is_backup ? 'backup-file' : ''}" 
                 onclick="loadPromptFile('${escapeHtml(file.name)}')">
                <div class="file-name">📄 ${escapeHtml(file.name)}</div>
                <div class="file-meta">
                    <span>📦 ${(file.size / 1024).toFixed(1)} KB</span>
                    <span>📅 ${formatDate(file.modified)}</span>
                    ${file.is_current ? '<span>✅ Текущий</span>' : ''}
                </div>
                ${file.is_backup ? `
                <div class="file-actions" onclick="event.stopPropagation()">
                    <button class="btn-restore" onclick="restorePrompt('${escapeHtml(file.name)}')">
                        ↩️ Восстановить
                    </button>
                    <button class="btn-delete" onclick="deletePromptFile('${escapeHtml(file.name)}')">
                        🗑️ Удалить
                    </button>
                </div>
                ` : ''}
            </div>
        `).join('');
    } catch (err) {
        filesList.innerHTML = `<div class="result-message error">Ошибка: ${err.message}</div>`;
        console.error("Error loading prompt files:", err);
    }
}
// Загрузка текущего промпта
async function loadCurrentPrompt() {
    // const editor = document.getElementById("prompt-editor");
    const metaFilename = document.getElementById("prompt-filename");
    const metaSize = document.getElementById("prompt-size");
    const metaModified = document.getElementById("prompt-modified");
    promptEditorMDE.value("Загрузка...");
    promptEditorMDE.codemirror.setOption("readOnly", true);
    try {
        const res = await fetch(`/api/prompts/current?agent=${currentAgent}`);
        const data = await res.json();
        currentPromptContent = data.content;
        promptEditorMDE.value(currentPromptContent);
        promptEditorMDE.codemirror.setOption("readOnly", false);
        metaFilename.textContent = data.name;
        metaSize.textContent = `${(data.size / 1024).toFixed(1)} KB`;
        metaModified.textContent = formatDate(data.modified);
        // Подсветка текущего файла в списке
        document.querySelectorAll(".prompt-file-item").forEach(item => {
            item.classList.remove("active");
            const fileNameEl = item.querySelector(".file-name");
            if (fileNameEl && fileNameEl.textContent.includes(data.name)) {
                item.classList.add("active");
            }
        });
    } catch (err) {
        promptEditorMDE.value(`Ошибка загрузки: ${err.message}`);
        console.error("Error loading current prompt:", err);
    }
}
// Загрузка конкретного файла промпта
async function loadPromptFile(filename) {
    try {
        const res = await fetch(`/api/prompts/file/${encodeURIComponent(filename)}?agent=${currentAgent}`);
        const data = await res.json();
        promptEditorMDE.value(data.content);
        currentPromptContent = data.content;
        // Обновление мета-информации
        document.getElementById("prompt-filename").textContent = data.name;
        document.getElementById("prompt-size").textContent = `${(data.size / 1024).toFixed(1)} KB`;
        document.getElementById("prompt-modified").textContent = formatDate(data.modified);
        // Подсветка активного элемента
        document.querySelectorAll(".prompt-file-item").forEach(item => {
            item.classList.remove("active");
            const fileNameEl = item.querySelector(".file-name");
            if (fileNameEl && fileNameEl.textContent.includes(filename)){
                item.classList.add("active");
            }
        });
    } catch (err) {
        alert(`Ошибка загрузки: ${err.message}`);
        console.error("Error loading prompt file:", err);
    }
}
// Создание бэкапа
async function createBackup() {
    const resultDiv = document.getElementById("prompt-result");
    resultDiv.className = "result-message";
    resultDiv.style.display = "block";
    resultDiv.innerHTML = "⏳ Создание бэкапа...";
    try {
        const res = await fetch(`/api/prompts/backup?agent=${currentAgent}`, { method: "POST" });
        const data = await res.json();
        if (res.ok) {
            resultDiv.className = "result-message success";
            resultDiv.innerHTML = `✅ Бэкап создан: ${data.backup_name}`;
            await loadPromptFiles();
        } else {
            throw new Error(data.detail || "Ошибка создания бэкапа");
        }
    } catch (err) {
        resultDiv.className = "result-message error";
        resultDiv.innerHTML = `❌ Ошибка: ${err.message}`;
    }
}
// Сохранение промпта
async function savePrompt() {
    const resultDiv = document.getElementById("prompt-result");
    const newContent = promptEditorMDE.value();
    if (!newContent.trim()) {
        alert("Промпт не может быть пустым");
        return;
    }
    if (!confirm("Сохранить изменения? Будет создан автоматический бэкап.")) {
        return;
    }
    resultDiv.className = "result-message";
    resultDiv.style.display = "block";
    resultDiv.innerHTML = "⏳ Сохранение...";
    try {
        const res = await fetch("/api/prompts/save", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                 content: newContent,
                 agent: currentAgent 
            })
        });
        const data = await res.json();
        if (res.ok) {
            resultDiv.className = "result-message success";
            resultDiv.innerHTML = `✅ Промпт сохранён!<br>📦 Бэкап создан автоматически`;
            currentPromptContent = newContent;
            await loadPromptFiles();
        } else {
            throw new Error(data.detail || "Ошибка сохранения");
        }
    } catch (err) {
        resultDiv.className = "result-message error";
        resultDiv.innerHTML = `❌ Ошибка: ${err.message}`;
    }
}
// Восстановление из бэкапа
async function restorePrompt(filename) {
    if (!confirm(`Восстановить промпт из ${filename}?\n\nТекущий промпт будет заменён.`)) {
        return;
    }
    try {
        const res = await fetch(`/api/prompts/restore/${encodeURIComponent(filename)}?agent=${currentAgent}`, {
            method: "POST"
        });   
        const data = await res.json();
        if (res.ok) {
            alert(`✅ Восстановлено из ${filename}`);
            await loadCurrentPrompt();
            await loadPromptFiles();
        } else {
            throw new Error(data.detail || "Ошибка восстановления");
        }
    } catch (err) {
        alert(`❌ Ошибка: ${err.message}`);
        console.error("Error restoring prompt:", err);
    }
}
// Удаление файла бэкапа
async function deletePromptFile(filename) {
    if (!confirm(`Удалить файл ${filename}?`)) {
        return;
    }
    try {
        const res = await fetch(`/api/prompts/file/${encodeURIComponent(filename)}?agent=${currentAgent}`, {
            method: "DELETE"
        });   
        const data = await res.json();
        if (res.ok) {
            await loadPromptFiles();
        } else {
            throw new Error(data.detail || "Ошибка удаления");
        }
    } catch (err) {
        alert(`❌ Ошибка: ${err.message}`);
        console.error("Error deleting prompt file:", err);
    }
}
// #############################
// BOT SETTINGS TAB LOGIC
// #############################
// загрузка стартового сообщения бота
async function loadBotStartMessage() {
    const editor = document.getElementById("bot-start-editor");
    const metaSize = document.getElementById("bot-start-size");
    const metaModified = document.getElementById("bot-start-modified");
    if (!editor) return;
    editor.value = "Загрузка...";
    editor.disabled = true;
    try {
        const res = await fetch("/api/prompts/bot-start");
        const data = await res.json();
        botStartMessageContent = data.content;
        editor.value = botStartMessageContent;
        editor.disabled = false;
        if (metaSize) metaSize.textContent = `${(data.size / 1024).toFixed(1)} KB`;
        if (metaModified) metaModified.textContent = formatDate(data.modified);
    } catch (err) {
        editor.value = `Ошибка загрузки: ${err.message}`;
        console.error("Error loading bot start message:", err);
    }
}
// сохранение нового стартового сообщения бота
async function saveBotStartMessage() {
    const editor = document.getElementById("bot-start-editor");
    const resultDiv = document.getElementById("bot-start-result");
    if (!editor || !resultDiv) return;
    const newContent = editor.value;
    if (!newContent.trim()) {
        alert("Стартовое сообщение не может быть пустым");
        return;
    }
    resultDiv.className = "result-message";
    resultDiv.style.display = "block";
    resultDiv.innerHTML = "⏳ Сохранение...";
    try {
        const res = await fetch("/api/prompts/bot-start", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ content: newContent })
        });
        const data = await res.json();
        if (res.ok) {
            resultDiv.className = "result-message success";
            resultDiv.innerHTML = `✅ Стартовое сообщение сохранено!<br>📝 Символов: ${data.length || 0}`;
            botStartMessageContent = newContent;
        } else {
            throw new Error(data.detail || "Ошибка сохранения");
        }
    } catch (err) {
        resultDiv.className = "result-message error";
        resultDiv.innerHTML = `❌ Ошибка: ${err.message}`;
    }
}
// загрузка сообщения помощи бота
async function loadBotHelpMessage() {
    const editor = document.getElementById("bot-help-editor");
    const metaSize = document.getElementById("bot-help-size");
    const metaModified = document.getElementById("bot-help-modified");
    if (!editor) return;
    editor.value = "Загрузка...";
    editor.disabled = true;
    try {
        const res = await fetch("/api/prompts/bot-help");
        const data = await res.json();
        botHelpMessageContent = data.content;
        editor.value = botHelpMessageContent;
        editor.disabled = false;
        if (metaSize) metaSize.textContent = `${(data.size / 1024).toFixed(1)} KB`;
        if (metaModified) metaModified.textContent = formatDate(data.modified);
    } catch (err) {
        editor.value = `Ошибка загрузки: ${err.message}`;
        console.error("Error loading bot help message:", err);
    }
}
// сохранение нового сообщения помощи бота
async function saveBotHelpMessage(){
    const editor = document.getElementById("bot-help-editor");
    const resultDiv = document.getElementById("bot-help-result");
    if (!editor || !resultDiv) return;
    const newContent = editor.value;
    if (!newContent.trim()) {
        alert("Сообщение помощи не может быть пустым");
        return;
    }
    resultDiv.className = "result-message";
    resultDiv.style.display = "block";
    resultDiv.innerHTML = "⏳ Сохранение...";
    try {
        const res = await fetch("/api/prompts/bot-help", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ content: newContent })
        });
        const data = await res.json();
        if (res.ok) {
            resultDiv.className = "result-message success";
            resultDiv.innerHTML = `✅ Сообщение помощи сохранено!<br>📝 Символов: ${data.length || 0}`;
            botHelpMessageContent = newContent;
        } else {
            throw new Error(data.detail || "Ошибка сохранения");
        }
    } catch (err) {
        resultDiv.className = "result-message error";
        resultDiv.innerHTML = `❌ Ошибка: ${err.message}`;
    }
}
// #############################
// GROUPS TAB LOGIC
// #############################
// Загрузка списка пользователей с группами
async function loadUserGroups(skipFetch = false) {
    const container = document.getElementById("user-groups-list");
    const searchQuery = document.getElementById("user-search")?.value || "";
    if (!container) return;
    // Загружаем с API только если нужно
    if (!skipFetch) {
        container.innerHTML = '<div class="loading">Загрузка пользователей...</div>';
        try {
            const res = await fetch("/api/subscribers");  
            if (!res.ok) {
                throw new Error(`Ошибка ${res.status}: ${res.statusText}`);
            }
            const users = await res.json();
            allUsersCache = users;
        } catch (e) {
            container.innerHTML = `
                <div class="result-message error">
                    Ошибка загрузки: ${e.message}
                </div>
            `;
            console.error(e);
            return;
        }
    }    
    // Фильтрация по поиску
    filteredUsersCache = allUsersCache.filter(u => {
        const q = searchQuery.toLowerCase();
        // поиск по global_user_id, username, first_name, last_name
        const matchesMainInfo = !searchQuery || (
            String(u.global_user_id).toLowerCase().includes(q) ||
            (u.username && u.username.toLowerCase().includes(q)) ||
            (u.first_name && u.first_name.toLowerCase().includes(q)) ||
            (u.last_name && u.last_name.toLowerCase().includes(q))
        );
        // поиск по аккаунтам
        const matchesAccounts = !searchQuery || (u.accounts && u.accounts.some(acc => 
            String(acc.platform_user_id).includes(q) || 
            (acc.username && acc.username.toLowerCase().includes(q))
        ));
        const matchesSearch = matchesMainInfo || matchesAccounts;
        // Фильтрация по группе
        let matchesGroup = true;
        if (activeStatsFilter === "manager") matchesGroup = !!u.manager_group;
        else if (activeStatsFilter === "coach") matchesGroup = !!u.coach_group;
        else if (activeStatsFilter === "both") matchesGroup = (u.manager_group && u.coach_group);
        else if (activeStatsFilter === "none") matchesGroup = (!u.manager_group && !u.coach_group);
        return matchesSearch && matchesGroup;
    });
    // пагинация расчеты
    const totalPages = Math.max(1, Math.ceil(filteredUsersCache.length / pageSize));
    if (currentPage > totalPages) currentPage = totalPages;
    const start = (currentPage - 1) * pageSize;
    const end = start + pageSize;
    const paginated = filteredUsersCache.slice(start, end);
    // Обновление статистики
    updateGroupStats(allUsersCache);
    if (paginated.length === 0) {
        container.innerHTML = `
            <div class="empty-state">
                <div class="icon">📭</div>
                <p>Пользователи не найдены</p>
            </div>
        `;
        return;
    }
    container.innerHTML = `
        <table class="user-groups-table">
            <thead>
                <tr>
                    <th>Username</th>
                    <th>Имя</th>
                    <th>Фамилия</th>
                    <th class="text-center">👔 Менеджер</th>
                    <th class="text-center">🎓 Коуч</th>
                    <th>Аккаунты</th>
                    <th>Последний вход</th>
                    <th class="text-center">🚫 Блок</th>
                    <th>User ID</th>
                </tr>
            </thead>
            <tbody>
                ${paginated.map(u => {
                    // Логика отображения: если имя unknown, пробуем показать username
                    const displayName = (u.first_name === 'unknown' && u.username) ? u.username : (u.first_name || 'unknown');
                    const displayLastName = u.last_name || '';    
                    return `
                    <tr class="${u.is_blocked ? 'user-blocked' : ''}">
                        <td>${escapeHtml(u.username || '-')}</td>
                        <td>${escapeHtml(displayName || '')}</td>
                        <td>${escapeHtml(displayLastName || '')}</td>
                        <td class="text-center">
                            <input type="checkbox" 
                                    ${u.manager_group ? 'checked' : ''} 
                                    onchange="toggleUserGroup('${u.global_user_id}', 'manager_group', this.checked)">
                        </td>
                        <td class="text-center">
                            <input type="checkbox" 
                                    ${u.coach_group ? 'checked' : ''} 
                                    onchange="toggleUserGroup('${u.global_user_id}', 'coach_group', this.checked)">
                        </td>
                        <td>
                            <div class="accounts-cell">
                                <button class="btn btn-secondary btn-small" onclick="toggleAccountsRow('${u.global_user_id}')">
                                    📱 Аккаунты (${u.accounts.length})
                                </button>
                                <div id="acc-details-${u.global_user_id}" class="accounts-details-hidden" style="display:none;">
                                    ${u.accounts.map(acc => `
                                        <div class="account-badge-mini">
                                            <span class="platform-tag">${acc.platform}</span>: <code>${acc.platform_user_id}</code>
                                        </div>
                                    `).join('')}
                                </div>
                            </div>
                        </td>
                        <td>${formatDate(u.last_seen)}</td>
                        <td>
                            <input type="checkbox" 
                                ${u.is_blocked ? 'checked' : ''} 
                                onchange="toggleUserBlock('${u.global_user_id}', this.checked)">
                        </td>
                        <td class="font-monospace">${u.global_user_id}</td>
                    </tr>
                    `;
                }).join('')}
            </tbody>
        </table>
        <!-- пагинация -->
        <div class="pagination-wrapper">
            <div class="pagination-controls">
                <span>Показывать по:</span>
                <select id="page-size" onchange="changePageSize()">
                    <option value="20" ${pageSize === 20 ? 'selected' : ''}>20</option>
                    <option value="50" ${pageSize === 50 ? 'selected' : ''}>50</option>
                    <option value="100" ${pageSize === 100 ? 'selected' : ''}>100</option>
                </select>
            </div>

            <div class="pagination">
                <button onclick="prevPage()" ${currentPage === 1 ? "disabled" : ""} class="btn btn-primary btn-small">⬅ Назад</button>
                
                <span class="page-info">Страница <strong>${currentPage}</strong> из <strong>${totalPages}</strong></span>

                <button onclick="nextPage()" ${currentPage >= totalPages ? "disabled" : ""} class="btn btn-primary btn-small">Вперед ➡</button>
            </div>
        </div>
    `;
}
// Блокировка/разблокировка пользователя
async function toggleUserBlock(globalUserId, value) {
    try {
        const res = await fetch("/api/subscribers/block", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                global_user_id: globalUserId,
                value: value
            })
        });
        if (!res.ok) {
            throw new Error(`Ошибка ${res.status}`);
        }
        const action = value ? "заблокирован" : "разблокирован";
        showNotification(
            `Пользователь ${globalUserId} ${action}`,
            "success"
        );
        await loadUserGroups();
    } catch (e) {
        alert(`Ошибка блокировки: ${e.message}`);
        await loadUserGroups();
    }
}
// отображение/скрытие строки с аккаунтами пользователя
function toggleAccountsRow(userId) {
    const el = document.getElementById(`acc-details-${userId}`);
    if (el) {
        el.style.display = el.style.display === 'none' ? 'block' : 'none';
    }
}
// пагинация - листание страниц
function nextPage() {
    const totalPages = Math.ceil(filteredUsersCache.length / pageSize);
    if (currentPage < totalPages) {
        currentPage++;
        loadUserGroups(true); // Листаем без запроса к API
    }
}
function prevPage() {
    if (currentPage > 1) {
        currentPage--;
        loadUserGroups(true); // Листаем без запроса к API
    }
}
// изменение размера страницы
function changePageSize() {
    const select = document.getElementById("page-size");
    if (select) {
        pageSize = parseInt(select.value);
        currentPage = 1; // Всегда возвращаемся на 1 страницу
        loadUserGroups(true); // Листаем без запроса к API
    }
}
// Обновление статистики по группам
function updateGroupStats(users) {
    const statsDiv = document.getElementById("user-groups-stats");
    if (!statsDiv) return;
    const total = users.length;
    const managers = users.filter(u => u.manager_group).length;
    const couchs = users.filter(u => u.coach_group).length;
    const both = users.filter(u => u.manager_group && u.coach_group).length;
    const noGroups = users.filter(u => !u.manager_group && !u.coach_group).length;
    statsDiv.innerHTML = `
        <h3>📊 Статистика пользователей</h3>
        <div class="groups-stats-grid">
            <div class="groups-stat-card groups-stat-total ${activeStatsFilter === 'all' ? 'active' : ''}"
                onclick="applyStatsFilter('all')">
                <div class="groups-stat-number">${total}</div>
                <div class="groups-stat-label">Всего пользователей</div>
            </div>
            <div class="groups-stat-card groups-stat-managers ${activeStatsFilter === 'manager' ? 'active' : ''}"
                onclick="applyStatsFilter('manager')">
                <div class="groups-stat-number">${managers}</div>
                <div class="groups-stat-label">👔 Менеджеры</div>
            </div>
            <div class="groups-stat-card groups-stat-couchs ${activeStatsFilter === 'coach' ? 'active' : ''}"
                onclick="applyStatsFilter('coach')">
                <div class="groups-stat-number">${couchs}</div>
                <div class="groups-stat-label">🎓 Коучи</div>
            </div>
            <div class="groups-stat-card groups-stat-both ${activeStatsFilter === 'both' ? 'active' : ''}"
                onclick="applyStatsFilter('both')">
                <div class="groups-stat-number">${both}</div>
                <div class="groups-stat-label">В обеих группах</div>
            </div>
            <div class="groups-stat-card groups-stat-none ${activeStatsFilter === 'none' ? 'active' : ''}"
                onclick="applyStatsFilter('none')">
                <div class="groups-stat-number">${noGroups}</div>
                <div class="groups-stat-label">Без групп</div>
            </div>
        </div>
    `;
}
// функция фильтра
function applyStatsFilter(filter) {
    activeStatsFilter = filter;
    currentPage = 1; // сброс страницы
    loadUserGroups();
}
// Переключение группы пользователя
async function toggleUserGroup(globalUserId, group, value) {
    try {
        const res = await fetch("/api/subscribers/group", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                global_user_id: globalUserId,
                group: group,
                value: value
            })
        });
        if (!res.ok) {
            const errorData = await res.json().catch(() => ({}));
            throw new Error(errorData.detail || `Ошибка ${res.status}`);
        }
        const groupName = group === "manager_group" ? "👔 Менеджер" : "🎓 Коуч";
        const action = value ? "добавлен в" : "удалён из";
        showNotification(`Пользователь ${globalUserId} ${action} группы "${groupName}"`, "success");
        // Перезагружаем список для обновления статистики
        await loadUserGroups();
    } catch (e) {
        alert(`Ошибка обновления группы: ${e.message}`);
        // Возвращаем чекбокс в исходное состояние
        await loadUserGroups();
    }
}
// Экспорт пользователей в CSV
function exportUsers() {
    const search = document.getElementById("user-search")?.value || "";
    const params = new URLSearchParams();
    if (search) params.set("search", search);
    if (activeStatsFilter && activeStatsFilter !== "all") {
        params.set("group", activeStatsFilter);
    }
    window.open(`/api/subscribers/export?${params.toString()}`);
}
/// #############################
// AUTH ACCESS LOGIC
// #############################
// проверка авторизованности
async function checkAuth() {
    try {
        const res = await fetch("/api/me", {
            credentials: "include"
        });
        if (!res.ok) throw new Error();
        const user = await res.json();
        currentUser = user;
        document.getElementById("login-screen").style.display = "none";
        document.getElementById("app").style.display = "block";
        applyRoleAccess();
    } catch {
        document.getElementById("login-screen").style.display = "flex";
        document.getElementById("app").style.display = "none";
    }
}
// авторизация
async function login() {
    const username = document.getElementById("login-username").value;
    const password = document.getElementById("login-password").value;
    const formData = new FormData();
    formData.append("username", username);
    formData.append("password", password);
    const res = await fetch("/api/login", {
        method: "POST",
        body: formData
    });
    if (res.ok) {
        checkAuth();
    } else {
        document.getElementById("login-error").innerText = "Неверные учетные данные";
    }
}
// выход из учетной записи
async function logout() {
    await fetch("/api/logout", { method: "POST" });
    checkAuth();
}
// ролевые правила
function applyRoleAccess() {
    if (!currentUser) return;
    const role = currentUser.role;
    // Все вкладки
    const tabNames = [
        "documents",
        "search",
        "upload",
        "tree_files",
        "news_send",
        "prompts",
        "bot_settings",
        "user_groups",
        "logs",
        "analytics",
        "dialogs"
    ];
    tabNames.forEach(name => {
        const tab = document.getElementById(`${name}-tab`);
        if (tab) tab.classList.remove("active");
        hideTabButton(name);
    });
    if (role === "admin") {
        // admin видит всё
        tabNames.forEach(name => {
            showTabButton(name);
        });
    }
    if (role === "manager") {
        // manager ограничен
        const allowed = [
            "tree_files",
            "news_send",
            "user_groups",
            "analytics",
            "dialogs"
        ];
        allowed.forEach(name => {
            showTabButton(name);
        });
    }
    openFirstAvailableTab();
}
// сокрытие вкладок 
function hideTabButton(tabName) {
    const btn = document.querySelector(`[data-tab="${tabName}"]`);
    if (btn) btn.style.display = "none";
}
// отображение вкладок
function showTabButton(tabName) {
    const btn = document.querySelector(`[data-tab="${tabName}"]`);
    if (btn) btn.style.display = "block";
}
// открытие после авторизации первой вкладки
function openFirstAvailableTab() {
    const buttons = document.querySelectorAll(".tab-button");
    for (let btn of buttons) {
        if (btn.style.display !== "none") {
            const tabName = btn.dataset.tab;
            showTab(tabName); // без event
            return;
        }
    }
}
// #############################
// LOGS TAB LOGIC
// #############################
// функция форматирования в json вид чтобы отрисовывать
function formatJSON(payload) {
    try {
        if (typeof payload === "string") {
            payload = JSON.parse(payload);
        }
        return JSON.stringify(payload, null, 2);
    } catch {
        return String(payload);
    }
}
// отрисовка переключения между страницами
function renderPagination(dataLength) {
    const el = document.getElementById("logs-pagination");
    el.innerHTML = `
        <div class="pagination">
            <button onclick="prevLogs()" ${logsPage === 0 ? "disabled" : ""} class="btn btn-primary btn-small">⬅</button>
            <span>Страница ${logsPage + 1}</span>
            <button onclick="nextLogs()" ${dataLength < LOGS_PAGE_SIZE ? "disabled" : ""} class="btn btn-primary btn-small">➡</button>
        </div>
    `;
}
// отрисовка json payload 
function renderPayload(payload) {
    const formatted = formatJSON(payload);
    return `<pre>${escapeHtml(formatted)}</pre>`;
}
// функция загрузки логов
async function loadLogs() {
    const params = new URLSearchParams();
    params.set("limit", LOGS_PAGE_SIZE);
    params.set("offset", logsPage * LOGS_PAGE_SIZE);
    Object.entries(logFilters).forEach(([k, v]) => {
        if (!v) return;
        if (k === "created_at") {
            params.set(k, shiftToUTC(v)); // сдвигаем время для правильности
        } else {
            params.set(k, v);
        }
    });
    const res = await fetch(`/api/events?${params}`);
    const data = await res.json();
    logsCache = data;
    // создаём таблицу один раз
    if (!document.getElementById("logs-body")) {
        document.getElementById("logs-table").innerHTML = `
            <table id="logs-table-inner">
                <thead>
                    <tr>
                        <th>User ID</th>
                        <th>User name</th>
                        <th>Event</th>
                        <th>Channel</th>
                        <th>Time</th>
                        <th>Payload</th>
                    </tr>
                </thead>
                <tbody id="logs-body"></tbody>
            </table>
        `;
    }
    renderLogs();
}
// настраиваем фильтры для логов
function setupLogFilters() {
    document.querySelectorAll('.log-filter').forEach(input => {
        let timeout;
        input.addEventListener('input', (e) => {
            clearTimeout(timeout);
            timeout = setTimeout(() => {
                const column = e.target.dataset.column;
                const value = e.target.value.trim();
                if (!value) {
                    delete logFilters[column];
                } else {
                    logFilters[column] = value;
                }
                logsPage = 0; // сброс страницы
                loadLogs();   // запрос на сервер
            }, 300);
        });
    });
}
// отображаем логи
function renderLogs() {
    const tbody = document.getElementById("logs-body");
    if (!tbody) return;
    tbody.innerHTML = '';
    logsCache.forEach(row => {
        const tr = document.createElement("tr");
        tr.innerHTML = `
            <td>${escapeHtml(row.user_id || "-")}</td>
            <td>${escapeHtml(row.user_name || "-")}</td>
            <td>${escapeHtml(row.event_type)}</td>
            <td>${escapeHtml(row.channel || "-")}</td>
            <td>${new Date(row.created_at).toLocaleString()}</td>
            <td class="payload-cell">
                ${renderPayload(row.payload)}
            </td>
        `;
        tbody.appendChild(tr);
    });
    if (logsCache.length === 0) {
        tbody.innerHTML = `
            <tr>
                <td colspan="6" style="text-align:center;padding:20px;">
                    🔍 Ничего не найдено
                </td>
            </tr>
        `;
    }
    // отрисовка переключение между вкладками
    renderPagination(logsCache.length);
}
// переключение на следующую страницу
function nextLogs() {
    logsPage++;
    loadLogs();
}
// переключение на прошлую страницу
function prevLogs() {
    if (logsPage > 0) {
        logsPage--;
        loadLogs();
    }
}
// функция автоматического обновления логов каждые 10 секунд
function startLogsAutoRefresh() {
    setupLogFilters();
    loadLogs(true); // первый запуск
    // задаем интервал логирования
    setInterval(() => {
        loadLogs(false);
    }, 10000); // каждые 10 секунд
}
// функция экспорта логов для аналитики
function exportAnalytics() {
    // Временно убрал промежуток за который делать выгрузку
    // const from = document.getElementById("from").value;
    // const to = document.getElementById("to").value;
    // window.open(`/api/analytics/export?from_ts=${from}&to_ts=${to}`);
    window.open("/api/analytics/export");
}
// #############################
// ANALYTICS TAB LOGIC
// #############################
// функция инициализации аналитики при нажатии по вкладке
function initAnalytics() {
    const now = new Date();
    // сегодня 
    const to = new Date(now);
    // 7 дней назад
    const from = new Date(now);
    from.setDate(from.getDate() - 7);
    document.getElementById("from").value = formatMSK(from);
    document.getElementById("to").value = formatMSK(to);
}
// функция открытия модалки с пользователями, загрузившими конкретный документ
async function openUsersModal(filename) {
    const fromRaw = document.getElementById("from").value;
    const toRaw = document.getElementById("to").value;
    // сдвигаем к времени бд
    const from = shiftToUTC(fromRaw);
    const to = shiftToUTC(toRaw);
    const modalList = document.getElementById("users-list");
    document.getElementById("users-modal").style.display = "block";
    const users = await fetch(
        `/api/analytics/document-users?filename=${encodeURIComponent(filename)}&from_ts=${from}&to_ts=${to}`
    ).then(r => r.json());
    if (users.length === 0) {
        modalList.innerHTML = `<div class="empty-state">Никто еще не скачивал этот файл</div>`;
    } else {
        modalList.innerHTML = `
            <div class="modal-table-container">
                <table class="modal-table">
                    <thead>
                        <tr>
                            <th>Пользователь</th>
                            <th>Источник</th>
                            <th class="text-center">Скачиваний</th>
                        </tr>
                    </thead>
                    <tbody>
                        ${users.map(u => `
                            <tr>
                                <td>
                                    <span class="user-name-text">${escapeHtml(u.user_name || 'unknown')}</span>
                                    <code class="user-id-code">${u.global_user_id}</code>
                                </td>
                                <td>
                                    <span class="source-badge">
                                        ${u.source === "search" ? "🔍 Поиск" : "📂 Меню"}
                                    </span>
                                </td>
                                <td class="text-center">
                                    <strong>${u.downloads}</strong>
                                </td>
                            </tr>
                        `).join("")}
                    </tbody>
                </table>
            </div>
        `;
    }
}
// функция закрытия модалки с пользователями
function closeUsersModal() {
    const modal = document.getElementById("users-modal");
    modal.style.display = "none";
    document.getElementById("users-list").innerHTML = "";
}
// функция трансформации времени в корректное для визуализации
function formatTime(ms) {
    if (ms === null || ms === undefined) return "0 мс";
    if (ms < 1000) return `${ms} мс`;
    return `${(ms / 1000).toFixed(2)} сек`;
}
// функция рендерит статистику
function renderStats(stats, channels) {
    const el = document.getElementById("stats");
    el.innerHTML = `
        <h3>📈 Статистика</h3>
        <p><b>Уникальные пользователи:</b> ${stats.unique_users}</p>
        <p><b>Сообщений всего:</b> ${stats.total_messages}</p>
        <p> Среднее количество сообщений на пользователя: ${Math.round(stats.avg_messages_per_user || 0)}</p>
        <p>🔝 Максимальное количество сообщений на пользователя: ${stats.max_messages_per_user}</p>
        <p>⚡ Среднее время ответа: ${formatTime(stats.avg_response_time || 0)}</p>
        <p>⚡ Медианное время ответа: ${formatTime(stats.median_response_time || 0)}</p>
        <hr>
        <h4>📡 Каналы</h4>
        ${channels.map(c => `
            <div>${c.channel || "unknown"}: ${c.messages}</div>
        `).join("")}
    `;
}
// рендеринг топа пользователей
function renderTopUsers(users) {
    allUsers = users;
    filteredUsers = users;
    drawUsers();
}
// отрисовка пользователей
function drawUsers() {
    const el = document.getElementById("top-users");
    // сохраняем состояние инпута
    const active = document.activeElement;
    const isInputFocused = active && active.tagName === "INPUT";
    const cursorPos = isInputFocused ? active.selectionStart : null;
    const total = filteredUsers.length;
    const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));
    // защита от выхода за границы
    if (userPage >= totalPages) userPage = totalPages - 1;
    if (userPage < 0) userPage = 0;
    const start = userPage * PAGE_SIZE;
    const page = filteredUsers.slice(start, start + PAGE_SIZE);
    el.innerHTML = `
        <h3>👤 Топ пользователей</h3>
        <input placeholder="Поиск..." value="${userSearch}"
            oninput="searchUsers(this.value)"
        />
        ${page.length === 0 ? "<div>Нет пользователей</div>" : page.map(u => `
            <div class="user-card">
                <b>${u.user_name || "unknown"}</b>
                <div>Всего сообщений: ${u.messages}</div>
                <div>В среднем за неделю сообщений: ${u.avg_weekly_messages.toFixed(1)}</div>
                <button onclick="openUserDialogs('${u.global_user_id}', '${u.user_name || "unknown"}')" class="btn btn-primary btn-small">
                    👁 Диалог
                </button>
            </div>
        `).join("")}
        <div class="pagination">
            <button onclick="prevUsers()" ${userPage === 0 ? "disabled" : ""} class="btn btn-primary btn-small">⬅</button>
            <span>Страница ${userPage + 1} / ${totalPages}</span>
            <button onclick="nextUsers()" ${userPage >= totalPages - 1 ? "disabled" : ""} class="btn btn-primary btn-small">➡</button>
        </div>
    `;
    // возвращаем фокусирование где был
    if (isInputFocused) {
        const input = el.querySelector("input");
        if (input) {
            input.focus();
            if (cursorPos !== null) {
                input.setSelectionRange(cursorPos, cursorPos);
            }
        }
    }
}
// поиск пользователей внутри топа
function searchUsers(q) {
    userSearch = q;
    const query = q.toLowerCase();
    filteredUsers = allUsers.filter(u =>
        (u.global_user_id || "").toLowerCase().includes(query) ||
        (u.user_name || "").toLowerCase().includes(query)
    );
    userPage = 0;
    drawUsers();
}
// обработка открытия следующей страницы
function nextUsers() {
    const totalPages = Math.ceil(filteredUsers.length / PAGE_SIZE);
    if (userPage < totalPages - 1) {
        userPage++;
        drawUsers();
    }
}
// обработка открытия прошлой страницы
function prevUsers() {
    if (userPage > 0) {
        userPage--;
        drawUsers();
    }
}
// лейбл источника загрузки документа
function sourceLabel(src) {
    const map = {
        'search': '🔍 Поиск',
        'menu': '📂 Меню',
        'chat': '💬 Чат',
        'unknown': '❓ Неизвестно'
    };
    return map[src] || `📁 ${src}`;
}
// отрисовка топа документов в запросах
function drawDocs() {
    const el = document.getElementById("top-docs");
    // вычисляем страницы
    const total = allDocs.length;
    const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));
    if (docPage >= totalPages) docPage = totalPages - 1;
    if (docPage < 0) docPage = 0;
    const start = docPage * PAGE_SIZE;
    const page = allDocs.slice(start, start + PAGE_SIZE);
    // вычисляем общее число скачиваний
    const totalDownloads = statSources.reduce((sum, s) => sum + Number(s.downloads), 0);
    const sourcesHtml = `
        <div class="sources-block">
            <h4>📊 Источники загрузок</h4>
            <div><b>Всего:</b> ${totalDownloads}</div>
            ${statSources.map(s => `
                <div>
                    ${sourceLabel(s.source)}:
                    ${s.downloads}
                </div>
            `).join("")}
        </div>
    `;
    el.innerHTML = `
        ${sourcesHtml}

        <hr>

        <h3>📄 Топ документов</h3>


        ${page.length === 0 ? "<div>Нет документов</div>" : page.map(d => `
            <div class="doc-item">
                <b>${d.file_name}</b>
                <div>${d.total_downloads} скачиваний</div>
                <div class="doc-sources">
                    🔍 ${d.search_downloads || 0}
                    📂 ${d.menu_downloads || 0}
                </div>
                <button onclick="openUsersModal('${d.file_name}')" class="btn btn-primary btn-small">
                    👁 Кто скачивал
                </button>
            </div>
        `).join("")}

        <div class="pagination">
            <button onclick="prevDocs()" ${docPage === 0 ? "disabled" : ""} class="btn btn-primary btn-small">⬅</button>

            <span>Страница ${docPage + 1} / ${totalPages}</span>

            <button onclick="nextDocs()" ${docPage >= totalPages - 1 ? "disabled" : ""} class="btn btn-primary btn-small">➡</button>
        </div>
    `;
}
// следующая страница документов
function nextDocs() {
    const totalPages = Math.ceil(allDocs.length / PAGE_SIZE);
    if (docPage < totalPages - 1) {
        docPage++;
        drawDocs();
    }
}
// прошлая страница документов
function prevDocs() {
    if (docPage > 0) {
        docPage--;
        drawDocs();
    }
}
// рендеринг топа документов
function renderTopDocs(docs, sources) {
    allDocs = docs;
    statSources = sources;
    // отрисовываем документы
    drawDocs();
}
// загрузка аналитической активности 
async function loadActivity(from, to) {
    const [activity, words, phrases] = await Promise.all([
        fetch(`/api/analytics/activity?from_ts=${from}&to_ts=${to}`).then(r => r.json()),
        fetch(`/api/analytics/top-words?from_ts=${from}&to_ts=${to}`).then(r => r.json()),
        fetch(`/api/analytics/top-phrases?from_ts=${from}&to_ts=${to}`).then(r => r.json())
    ]);
    // отрисовка графиков активности и облак
    renderActivity(activity, words, phrases);
}
// отрисовка графиков активности
function renderActivity(activity, words, phrases) {

    // ===== ЧАСЫ =====
    const hours = Array.from({ length: 24 }, (_, i) => i);

    const hourMap = {};
    hours.forEach(h => hourMap[h] = 0);

    activity.forEach(a => {
        const hour = Number(a.hour);
        hourMap[hour] += Number(a.messages);
    });

    const hourLabels = hours.map(h => `${h}:00`);
    const hourData = hours.map(h => hourMap[h]);

    // уничтожаем старый график
    if (hourChart) {
        hourChart.destroy();
    }

    const ctx = document.getElementById("activityChart").getContext("2d");
    // строим график по сообщениям в час
    hourChart = new Chart(ctx, {
        type: "bar",
        data: {
            labels: hourLabels,
            datasets: [{
                label: "Сообщений в час",
                data: hourData
            }]
        },
        options: {
            responsive: true,
            plugins: {
                legend: { display: false }
            }
        }
    });

    // ===== ДНИ =====
    const days = ["ПН","ВТ","СР","ЧТ","ПТ","СБ","ВС"];
    const dayMap = {};
    // Инициализируем счётчики для всех дней (0-6, где 0=ВС по стандарту JS)
    for (let i = 0; i < 7; i++) {
        dayMap[i] = 0;
    }
    //  Агрегация: суммируем сообщения по дням недели
    activity.forEach(a => {
        const day = Number(a.day);
        dayMap[day] += Number(a.messages);
    });
    // Подготовка данных для графика: выравниваем по 7 дням с циклическим сдвигом
    const dayData = days.map((_, displayIndex) => {
        const dataIndex = (displayIndex + 1) % 7;
        return dayMap[dataIndex];
    });
    // уничтожаем старый график
    if (dayChart) {
        dayChart.destroy();
    }
    // строим график сообщений в день
    dayChart = new Chart(document.getElementById("dayChart"), {
        type: "bar",
        data: {
            labels: days,
            datasets: [{
                label: "Сообщений в день",
                data: dayData
            }]
        },
        options: {
            responsive: true
        }
    });
    // ===== WORD CLOUD =====
    renderCloud("word-cloud", words);
    // phrases
    renderCloud("phrase-cloud", phrases);
}
// функция отрисовки облака
function renderCloud(id, data) {
    const el = document.getElementById(id);
    if (!el) return;

    // Ждём реальных размеров элемента (размеры заданы через CSS-класс)
    if (el.offsetWidth === 0) {
        setTimeout(() => renderCloud(id, data), 150);
        return;
    }

    el.innerHTML = "";

    if (!data || data.length === 0) {
        el.innerHTML = "<div class='cloud-empty'>Нет данных</div>";
        return;
    }

    // Читаем размеры из CSS — ничего не устанавливаем напрямую
    const width = el.offsetWidth;
    const height = el.offsetHeight;

    const list = data.map(w => [w.text, w.value]);

    // Диапазон значений
    const values = list.map(item => item[1]);
    const maxVal = Math.max(...values);
    const minVal = Math.min(...values);

    // Адаптивные размеры шрифтов под контейнер
    const maxFontSize = Math.min(width / 8, height / 3, 72);
    const minFontSize = Math.max(12, maxFontSize / 6);

    // КЛЮЧЕВОЕ: нормализуем значения к реальным размерам шрифтов
    const normalizedList = list.map(item => {
        const ratio = maxVal === minVal ? 0.5 : (item[1] - minVal) / (maxVal - minVal);
        const fontSize = minFontSize + ratio * (maxFontSize - minFontSize);
        return [item[0], fontSize];
    });

    // Адаптивный gridSize — чем меньше контейнер, тем плотнее сетка
    const gridSize = Math.max(4, Math.round(8 * (800 / Math.max(width, 400))));

    setTimeout(() => {
        try {
            WordCloud(el, {
                list: normalizedList,
                gridSize: gridSize,
                weightFactor: 1,              // значения уже в пикселях
                fontFamily: "Arial, sans-serif",
                fontWeight: "normal",
                color: function() {
                    const colors = [
                        '#1f77b4', '#2ca02c', '#d62728', '#9467bd',
                        '#ff7f0e', '#8c564b', '#e377c2', '#17becf',
                        '#bcbd22', '#3366cc', '#dc3912', '#ff9900'
                    ];
                    return colors[Math.floor(Math.random() * colors.length)];
                },
                backgroundColor: "#fff",
                rotateRatio: 0.3,             // только 30% повёрнуты — читаемость
                rotationSteps: 2,             // только 0° и 90°
                minRotation: -Math.PI / 2,
                maxRotation: Math.PI / 2,
                drawOutOfBound: false,        // не рисовать за пределами
                shrinkToFit: true,            // сжимать длинные слова
                clearCanvas: true
            });
        } catch (e) {
            console.error('WordCloud error:', e);
        }
    }, 100);
}
// загрузка всей аналитики
async function loadAnalytics() {
    const fromRaw = document.getElementById("from").value;
    const toRaw = document.getElementById("to").value;

    const from = shiftToUTC(fromRaw);
    const to = shiftToUTC(toRaw);
    
    const [stats, channels, users, docs, sources] = await Promise.all([
        fetch(`/api/analytics/stats?from_ts=${from}&to_ts=${to}`).then(r=>r.json()),
        fetch(`/api/analytics/channels?from_ts=${from}&to_ts=${to}`).then(r=>r.json()),
        fetch(`/api/analytics/top-users?from_ts=${from}&to_ts=${to}`).then(r=>r.json()),
        fetch(`/api/analytics/top-documents?from_ts=${from}&to_ts=${to}`).then(r=>r.json()),
        fetch(`/api/analytics/documents-sources?from_ts=${from}&to_ts=${to}`).then(r=>r.json())
    ]);

    renderStats(stats, channels);
    renderTopUsers(users);
    renderTopDocs(docs, sources)
    loadActivity(from, to);
}
// открытие окна диалогов пользователя
async function openUserDialogs(userId, userName) {
    const fromRaw = document.getElementById("from").value;
    const toRaw = document.getElementById("to").value;
    // сдвигаем к времени бд
    const from = shiftToUTC(fromRaw);
    const to = shiftToUTC(toRaw);
    const dialogs = await fetch(
        `/api/analytics/user-dialogs?user_id=${userId}&from_ts=${from}&to_ts=${to}`
    ).then(r => r.json());
    // устанавливаем заголовок чтобы понятно было с каким пользователем работаем 
    document.getElementById("dialogs-title").innerText =
        `💬 ${userName} (${userId})`;
    // скачивание диалога
    document.getElementById("download-dialogs").onclick = () => {
        window.open(
            `/api/analytics/export-user-dialogs?user_id=${userId}&from_ts=${from}&to_ts=${to}`
        );
    };
    renderUserDialogs(dialogs);
    document.getElementById("dialogs-modal").style.display = "block";
}
// отрисовка диалога пользователя 
function renderUserDialogs(dialogs) {
    const el = document.getElementById("dialogs-list");

    el.innerHTML =
        dialogs.length === 0
            ? "<div>Нет диалогов</div>"
            : dialogs.map(d => {
                // Определяем иконку/название мессенджера
                let channelBadge = "";
                if (d.channel === "telegram") {
                    channelBadge = `<span class="badge" style="background:#0088cc; color:white; padding:2px 6px; border-radius:4px; font-size:11px;">Telegram</span>`;
                } else if (d.channel === "max") {
                    channelBadge = `<span class="badge" style="background:#ff4b4b; color:white; padding:2px 6px; border-radius:4px; font-size:11px;">Max</span>`;
                } else if (d.channel) {
                    channelBadge = `<span class="badge" style="background:#666; color:white; padding:2px 6px; border-radius:4px; font-size:11px;">${d.channel}</span>`;
                }
                let answer;
                // преобразуем ответ с добавлением времени ответа
                if (d.response) {
                    if (d.file_paths && d.response.includes("||")) {
                        answer = d.response
                            .split("||")
                            .map(f => "📄 " + f.split("/").pop())
                            .join("<br>");
                    } else {
                        answer = escapeHtml(d.response);
                    }
                } else {
                    answer = "—";
                }
                answer += ` <small style="color:#999;">(${formatTime(d.response_time || 0)})</small>`;

                return `
                    <div class="dialog-item">
                        <div class="dialog-header">
                            <span>${new Date(d.message_time).toLocaleString("ru-RU", {
                                    timeZone: "Europe/Moscow"
                                })}
                            </span>
                            ${channelBadge}
                        </div>

                        <div class="dialog-q">🧑 ${d.message || "-"}</div>
                        <div class="dialog-a">🤖 ${answer}</div>
                    </div>
                `;
            }).join("");
}
// закрытие модальности диалога
function closeDialogsModal() {
    document.getElementById("dialogs-modal").style.display = "none";
    document.getElementById("dialogs-list").innerHTML = "";
}
// #############################
// DIALOGS TAB LOGIC
// #############################
// инициализация дат
function initDialogs() {
    const fromEl = document.getElementById("dialogs-from");
    const toEl = document.getElementById("dialogs-to");
    const now = new Date();
    const to = new Date(now);
    const from = new Date(now);
    from.setDate(from.getDate() - 1); // по умолчанию 1 день
    const fromStr = formatMSK(from)
    const toStr = formatMSK(to)
    fromEl.value = fromStr;
    toEl.value = toStr;
}
// проверка существования диалогов
function ensureDialogsDates() {
    const fromEl = document.getElementById("dialogs-from");
    const toEl = document.getElementById("dialogs-to");

    if (!fromEl.value || !toEl.value) {
        initDialogs();
    }
}
// обновление диалогов при изменении дат или фильтров
function updateDialogs(){
    initDialogs();
    loadDialogs();
}
// загрузка диалогов
async function loadDialogs() {
    ensureDialogsDates();
    const fromRaw = document.getElementById("dialogs-from").value;
    const toRaw = document.getElementById("dialogs-to").value;
    const from = shiftToUTC(fromRaw);
    const to = shiftToUTC(toRaw);

    const user = document.getElementById("dialogs-user").value;
    const text = document.getElementById("dialogs-text").value;

    const params = new URLSearchParams({
        from_ts: from,
        to_ts: to
    });

    if (user) params.set("user", user);
    if (text) params.set("text", text);

    const res = await fetch(`/api/analytics/dialogs?${params}`);
    if (!res.ok) {
        const text = await res.text();
        console.error("Server error:", text);
        alert("Ошибка сервера, смотри консоль");
        return;
    }
    const data = await res.json();

    renderDialogs(data);
}
// рендер таблицы диалогов
function renderDialogs(data) {
    const container = document.getElementById("dialogs-table");

    if (!data || data.length === 0) {
        container.innerHTML = "<p class='empty-state'>За этот период диалогов не найдено</p>";
        return;
    }

    container.innerHTML = `
        <table class="modern-table">
            <thead>
                <tr>
                    <th>Пользователь</th>
                    <th>Платформа</th>
                    <th>Время (МСК)</th>
                    <th>Запрос</th>
                    <th>Ответ бота</th>
                    <th>Скорость</th>
                </tr>
            </thead>
            <tbody>
                ${data.map(row => {

                    // ✅ нормальное имя
                    let userName = (row.user_name || '').trim();
                    if (!userName || userName === "None None") {
                        userName = "Аноним";
                    }
                    const date = row.message_time ? new Date(row.message_time).toLocaleString('ru-RU') : '---';
                    const respTime = row.response_time ? `${(row.response_time / 1000).toFixed(2)}с` : '';
                    let answer;
                    if (row.response) {
                        answer = row.response;
                    } else if (row.file_paths) {
                        const files = row.file_paths.split("||")
                            .map(f => "📄 " + f.split('/').pop())
                            .join("<br>");
                        answer = files;
                    } else {
                        answer = "—";
                    }
                    return `
                    <tr>
                        <td class="user-cell">
                            <strong>${escapeHtml(userName)}</strong><br>
                            <small>${escapeHtml(row.global_user_id)}</small>
                        </td>
                        <td>
                            ${row.channel === 'telegram' ? '📨 Telegram' : '⚡ Max'}
                        </td>
                        <td class="time-cell">${date}</td>
                        <td class="msg-cell">${escapeHtml(row.message || '')}</td>
                        <td class="resp-cell">${escapeHtml(answer)}</td>
                        <td class="meta-cell">${respTime}</td>
                    </tr>
                `}).join('')}
            </tbody>
        </table>
    `;
}
// функция экспорта диалогов 
function exportDialogs() {
    const from = shiftToUTC(document.getElementById("dialogs-from").value);
    const to = shiftToUTC(document.getElementById("dialogs-to").value);

    window.open(`/api/analytics/export-dialogs?from_ts=${from}&to_ts=${to}`);
}