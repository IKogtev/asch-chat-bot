// API base URL
const API_BASE = '';

// State
let selectedFile = null;
let currentCollection = null;
let currentCollectionType = null; // faq | kb | docs
let activeCollections = {
    faq: null,
    kb: null
};
let collectionsByType = {};
let activeAliases = {};
let currentPromptContent = "";
let promptFiles = [];
let botStartMessageContent = "";
let newsEditor = null;


// Initialize on load
document.addEventListener('DOMContentLoaded', () => {
    loadAliasData();
    loadCollections();
    loadActiveCollections();
    loadCollectionInfo();
    loadDocuments();
    setupDragAndDrop();
    loadSyncSettings();
    subscribeToSync();
    loadFilesystemTree();
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
            placeholder: "Напишите новость..."
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
    selectEl.innerHTML = '<option disabled selected>Loading collections...</option>';
    activeEl.textContent = 'Active collection: loading...';
    console.log('[loadCollections] called');
    try {
        const response = await fetch(`${API_BASE}/api/collections`);
        const data = await response.json();

        const { current_collection, collections} = data;
        currentCollection = current_collection;

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
        console.log('activeEl', activeEl);
        console.log('selectEl', selectEl);

    } catch (e) {
        activeEl.textContent = 'Active collection: error';
        selectEl.innerHTML = '<option disabled>Error loading collections</option>';
        console.error(e);
    }
}
// Switch collection
async function switchCollection(collectionName, collectionType) {
    if (!collectionName) return;

    console.log('[switchCollection] switching to', collectionName, collectionType);

    currentCollectionType = collectionType;
    currentCollection = collectionName;

    try {
        const response = await fetch(`${API_BASE}/api/collections/switch`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ collection_name: collectionName, collection_type: collectionType})
        });

        if (!response.ok) {
            throw new Error(`Switch failed: ${response.status}`);
        }

        const data = await response.json();
        console.log('[switchCollection] success', data);

        // Перезагрузка UI
        await loadAliasData();
        await loadCollections();
        await loadActiveCollections();
        await loadCollectionInfo();
        await loadDocuments();
        await loadFilesystemTree();
        

        // Очистка поиска
        document.getElementById('search-results').innerHTML = '';
        document.getElementById('search-query').value = '';

    } catch (e) {
        console.error('[switchCollection] error', e);
        alert(`Failed to switch collection: ${e.message}`);
    }
}
// Collection Info
async function loadCollectionInfo() {
    try {
        const response = await fetch(`${API_BASE}/api/collections/info`);
        const data = await response.json();
        const isFAQ = currentCollectionType === 'faq';

        document.getElementById('collection-info').innerHTML = 
            `Collection: <strong>${data.name}</strong> 
                | ${isFAQ? "Documents": "Points"} 
                <strong>${data.points_count-1 || 0}</strong>
                | Platform Version
                <strong>${data.platform_version || 0}</strong>
                | Last Synchronization
                <strong>${data.last_sync? formatDate(data.last_sync): "In process now"}</strong>
                | Next Synchronization
                <strong>${data.next_sync? formatDate(data.next_sync): "Not set yet"}</strong>
            `;
    } catch (error) {
        console.error('Error loading collection info:', error);
    }
}
// кнопка удаления коллекции
document
  .getElementById("delete-collection-btn")
  .addEventListener("click", async () => {
    const select = document.getElementById("collection-select");
    const deletedCollection = select.value;

    if (!deletedCollection) {
      alert("No collection selected");
      return;
    }

    const confirmed = confirm(
      `Are you sure you want to delete collection "${deletedCollection}"?\n\nThis action cannot be undone.`
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
      alert(`Collection "${data.deleted_collection}" deleted`);

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
      alert(`Failed to delete collection: It's active collection`); 
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
        errorBox.textContent = "Version must be a number (e.g., 1 or 1.2)";
        errorBox.classList.remove("hidden");
        return;
    }
    if (!version) {
        errorBox.textContent = "Version is required";
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
            throw new Error(data.error || "Failed to create collection");
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
            option.textContent += " (active)";
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
        errorBox.textContent = "Select a collection";
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
            throw new Error(data.detail || "Failed to switch alias");
        }

        const current = document.getElementById("collection-select").value;

        if (current.startsWith(`${type}_`) && current !== collection) {
            const confirmSwitch = confirm(
                `Alias switched to "${collection}".\n\nSwitch UI to this collection?`
            );

            if (confirmSwitch) {
                const select = document.getElementById("collection-select");
                select.value = collection;
                select.dispatchEvent(new Event("change"));
            }
        }

        closeSwitchCollectionModal();

        // 🔄 обновляем UI
        await loadAliasData();
        await loadActiveCollections();

        

    } catch (err) {
        errorBox.textContent = err.message;
        errorBox.classList.remove("hidden");
    }
}
// Tab Management
function showTab(tabName) {
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
    event.target.classList.add('active');

    const button = event.currentTarget;
    button.classList.add('active');
    
    // Load data if needed
    if (tabName === 'documents') {
        loadDocuments();
    } else if (tabName === 'search') {
        loadKnowledgeBasesForSearch();
    } else if (tabName === 'tree_files'){
        loadFilesystemTree();
    } else if (tabName === 'news_send'){
        loadNewsHistory();
    } else if (tabName === 'prompts'){
        loadPromptsTab();
    } else if (tabName === 'bot_settings'){
        loadBotStartMessage();
    } else if (tabName === 'user_groups'){
        loadUserGroups();
    }
}

// #############################
// Utilities subsystem
// #############################
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
    if (!dateString) return 'Unknown';
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
            return 'Invalid date';
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
        return 'Invalid date';
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
        'Document';

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
    const hours = prompt("Enter sync interval in hours", current)
    if(!hours) return;

    const parsedHours = parseInt(hours, 10);
    if (isNaN(parsedHours) || parsedHours <= 0) {
        alert("Please enter a valid positive number.");
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
// #############################
// DOCUMENTS TAB LOGIC
// #############################

// Documents Management
async function loadDocuments() {
    const container = document.getElementById('documents-list');
    container.innerHTML = '<div class="loading">Loading knowledge bases...</div>';
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
                    <p>No documents found. Upload your first document to get started!</p>
                </div>
            `;
            loadCollectionInfo();
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
                            🔄 Sync KB
                        </button>
                        <span class="badge badge-primary">${kb.document_count} documents</span>
                        <span class="badge badge-secondary">${kb.total_chunks} chunks</span>
                        <button
                            class="btn btn-danger btn-small"
                            title="Delete knowledge base"
                            onclick="event.stopPropagation(); deleteKnowledgeBase('${escapeHtml(kb.kb_id)}')"
                        >
                            🗑️ Delete
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
                                        👁️ View
                                    </button>
                                    <button 
                                        class="delete-doc-btn btn btn-danger btn-small"
                                        data-doc-id="${doc.document_id}"
                                        data-doc-name="${doc.source_name || doc.source}">
                                        🗑️ Delete
                                    </button>
                                </div>
                            </div>
                            <div class="document-meta">
                                <div class="meta-item">
                                    <span class="badge badge-primary">${doc.chunks_count ?? doc.total_chunks ?? '?'} chunks</span>
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
        loadFilesystemTree();
    } catch (error) {
        container.innerHTML = `
            <div class="result-message error">
                Error loading documents: ${error.message}
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

    if (e.target.classList.contains("sync-kb-btn")) {

        e.stopPropagation(); // чтобы не сработал toggleKB

        const button = e.target;
        const kbId = button.dataset.kbId;

        button.disabled = true;
        button.innerText = "⏳ Syncing...";

        const formData = new FormData();
        formData.append("kb_id", kbId);
        formData.append("collection_name", currentCollection);

        try {
            const response = await fetch("/api/filesystem/sync", {
                method: "POST",
                body: formData
            });
            const data = await response.json();
            if (!response.ok) {
                throw new Error("Sync failed");
            }
            alert(`✅ KB "${kbId}" synced`);
            button.innerText = "✅ Synced";

            setTimeout(() => {
                button.innerText = "🔄 Sync KB";
                button.disabled = false;
            }, 1500);

            if (button.innerText==="✅ Synced"){
                await loadDocuments();
                await loadCollectionInfo();
            }

        } catch (err) {
            console.error(err);
            button.innerText = "❌ Error";
            button.disabled = false;
        }
    }
    
});
// подписка на очередь событий для отслеживания автоматического обновления 
// при синхронизации атомарной
function subscribeToSync() {
    const eventSource = new EventSource("/api/filesystem/sync_events");

    eventSource.onmessage = function (event) {
        if (event.data === "sync_completed") {
            console.log("🔄 Sync completed → refreshing UI");
            loadDocuments();
            loadFilesystemTree();
        }
    };

    eventSource.onerror = function () {
        console.error("SSE error");
        eventSource.close();
    };
}

// функция для синхронизации по всем данным
async function syncAll(btnElement) {
    // 1. Защита: если кнопка не передана, выходим
    if (!btnElement) {
        console.error("Кнопка не передана в функцию syncAll!");
        return;
    }

    // Сохраняем оригинальный текст и состояние
    const originalText = btnElement.innerText;
    
    try {
        // 2. Блокируем кнопку визуально и функционально
        btnElement.disabled = true;
        btnElement.innerText = "⏳ Синхронизация...";
        btnElement.style.opacity = "0.7"; // Визуальный эффект

        console.log("Отправка запроса на /api/filesystem/sync_all...");

        // 3. Делаем запрос
        const response = await fetch("/api/filesystem/sync_all", {
            method: "POST",
            headers: {
                "Content-Type": "application/json"
            }
        });

        // 4. Проверяем статус ответа
        if (!response.ok) {
            // Пытаемся получить текст ошибки от сервера
            const errorData = await response.json().catch(() => ({}));
            throw new Error(errorData.detail || `Ошибка сервера: ${response.status}`);
        }

        const result = await response.json();
        console.log("Успех!", result);

        // 5. Показываем успех
        btnElement.innerText = "✅ Готово";
        btnElement.style.backgroundColor = "#28a745"; 

        // 6. Обновляем список документов (если функция существует)
        if (typeof loadDocuments === 'function') {
            await loadDocuments();
        } else {
            console.warn("Функция loadDocuments не найдена, список не обновлен.");
        }

    } catch (error) {
        console.error("Ошибка синхронизации:", error);
        btnElement.innerText = "❌ Ошибка";
        btnElement.style.backgroundColor = "#dc3545"; // Красный цвет
        alert("Не удалось синхронизировать: " + error.message);
    } finally {
        // 7. Возвращаем кнопку в исходное состояние через 2 секунды
        setTimeout(() => {
            btnElement.disabled = false;
            btnElement.innerText = originalText;
            btnElement.style.opacity = "1";
            btnElement.style.backgroundColor = ""; // Сброс цвета
        }, 2000);
    }
}
// удаление баз знаний
async function deleteKnowledgeBase(kbId) {
    if (!confirm(`Delete knowledge base "${kbId}"?\n\nAll documents will be permanently removed.`)) {
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
            throw new Error(data.detail || "Failed to delete knowledge base");
        }
        if (res.ok) {
            showNotification('Knowledge base deleted successfully', 'success');
            loadDocuments();
        }
       
    } catch (err) {
        alert(`Error deleting knowledge base: ${err.message}`);
    }
}
// возможность открытия просмотра документа
async function viewDocument(documentId, filename) {
    const modal = document.getElementById('chunks-modal');
    const modalTitle = document.getElementById('modal-title');
    const modalBody = document.getElementById('modal-body');
    
    modalTitle.textContent = `Chunks: ${filename}`;
    modalBody.innerHTML = '<div class="loading">Loading chunks...</div>';
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
                source_name: meta.source_name || meta.source || 'unknown',
                kb_id: meta.kb_id || 'N/A',
                user_id: meta.user_id || 'N/A',
                source_type: meta.source_type || 'N/A',
                version: meta.version || 1,
                document_id: meta.document_id || '',
                created_at: meta.created_at || 'Unknown',
                section_path: meta.section_path || []
            };
            return `
            <div class="chunk-item-compact">
                <div class="chunk-header-compact">
                    <strong>Chunk ${chunkIndex + 1}</strong> (${charCount} chars)
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
                Error loading chunks: ${error.message}
            </div>
        `;
    }
}
// удаление документа
async function deleteDocument(documentId, filename) {
    if (!confirm(`Are you sure you want to delete "${filename}"?`)) {
        return;
    }
    
    try {
        const response = await fetch(`${API_BASE}/api/documents/${documentId}`, {
            method: 'DELETE'
        });
        
        if (response.ok) {
            showNotification('Document deleted successfully', 'success');
            loadDocuments();
        } else {
            throw new Error('Failed to delete document');
        }
    } catch (error) {
        showNotification(`Error deleting document: ${error.message}`, 'error');
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
        kbSelect.innerHTML = '<option value="">All Knowledge Bases</option>' + 
            knowledgeBases.map(kb => 
                `<option value="${escapeHtml(kb.kb_id)}">${escapeHtml(kb.kb_id)} (${kb.document_count} docs)</option>`
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
                <p>Enter a search query to find relevant documents</p>
            </div>
        `;
        return;
    }
    
    resultsContainer.innerHTML = '<div class="loading">Searching...</div>';
    
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
                    <p>No results found for your query</p>
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
                            ${escapeHtml(title)} — Chunk ${chunkIndex}
                        </strong>
                        <span class="chunk-score">Score: ${score}</span>
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
                Error performing search: ${error.message}
            </div>
        `;
    }
}

// #############################
// UPLOAD TAB LOGIC
// #############################

// Настройка возможности drag and drop
function setupDragAndDrop() {
    const uploadBox = document.getElementById('upload-box');
    
    uploadBox.addEventListener('dragover', (e) => {
        e.preventDefault();
        uploadBox.classList.add('dragover');
    });
    
    uploadBox.addEventListener('dragleave', () => {
        uploadBox.classList.remove('dragover');
    });
    
    uploadBox.addEventListener('drop', (e) => {
        e.preventDefault();
        uploadBox.classList.remove('dragover');
        
        const files = e.dataTransfer.files;
        if (files.length > 0) {
            handleFile(files[0]);
        }
    });
    
    uploadBox.addEventListener('click', (e) => {
        // Only trigger file input if not clicking on the input itself
        if (e.target.id !== 'file-input') {
            document.getElementById('file-input').click();
        }
    });
}
// отображение выбранного файла
function handleFileSelect(event) {
    const file = event.target.files[0];
    if (file) {
        handleFile(file);
    }
}
// передача файла
function handleFile(file) {
    selectedFile = file;
    
    document.getElementById('file-name').textContent = file.name;
    document.getElementById('file-info').style.display = 'block';
    document.getElementById('upload-btn').disabled = false;
    document.getElementById('upload-result').className = 'result-message';
    document.getElementById('upload-result').style.display = 'none';
}
// загрузка документа
async function uploadDocument(uploadMode = 'check') {
    if (!selectedFile) {
        return;
    }
    
    const uploadBtn = document.getElementById('upload-btn');
    const progressDiv = document.getElementById('upload-progress');
    const resultDiv = document.getElementById('upload-result');
    const kb_id = document.getElementById('upload-kb-id').value;
    const user_id = document.getElementById('upload-user-id').value;
    
    uploadBtn.disabled = true;
    progressDiv.style.display = 'block';
    resultDiv.style.display = 'none';
    
    try {
        const formData = new FormData();
        formData.append('file', selectedFile);
        formData.append('kb_id', kb_id);
        formData.append('user_id', user_id);
        formData.append('upload_mode', uploadMode);
        formData.append('collection_type', currentCollectionType);
        formData.append('collection_name', currentCollection);
        
        console.log('Uploading file:', selectedFile.name, 'Mode:', uploadMode);
        
        const response = await fetch(`${API_BASE}/api/documents/upload`, {
            method: 'POST',
            body: formData
        }).catch(err => {
            console.error('Fetch error:', err);
            throw new Error(`Network error: ${err.message}. The file may be too large or the server may be unreachable.`);
        });
        
        console.log('Response status:', response.status);
        
        let result;
        try {
            result = await response.json();
        } catch (e) {
            console.error('JSON parse error:', e);
            throw new Error(`Server returned invalid response. Status: ${response.status}`);
        }
        
        if (response.ok) {
            resultDiv.className = 'result-message success';
            const versionInfo = result.version > 1 ? ` (Version ${result.version})` : '';
            const replacedInfo = result.replaced ? '<br><strong>Replaced old version</strong>' : '';
            resultDiv.innerHTML = `
                <strong>✓ Success!</strong>${replacedInfo}<br>
                Document uploaded: ${escapeHtml(result.source_name)}${versionInfo}<br>
                Type: ${escapeHtml(result.source_type)}<br>
                Chunks created: ${result.points_count}<br>
                Document ID: ${result.document_id}
            `;
            
            // Reset form
            selectedFile = null;
            document.getElementById('file-input').value = '';
            document.getElementById('file-info').style.display = 'none';
            document.getElementById('upload-btn').disabled = true;
            
            // Reload documents
            loadDocuments();
        } else if (response.status === 409 && result.conflict_type) {
            // Handle conflicts
            handleUploadConflict(result);
        } else {
            throw new Error(result.detail || result.message || 'Upload failed');
        }
    } catch (error) {
        console.error('Upload error:', error);
        resultDiv.className = 'result-message error';
        resultDiv.innerHTML = `<strong>✗ Error:</strong> ${escapeHtml(error.message)}`;
    } finally {
        progressDiv.style.display = 'none';
        resultDiv.style.display = 'block';
        uploadBtn.disabled = false;
    }
}
// разрешение конфликта файлов
function handleUploadConflict(conflictData) {
    const resultDiv = document.getElementById('upload-result');
    
    if (conflictData.conflict_type === 'exact_duplicate') {
        // Exact duplicate - just show message, no action needed
        resultDiv.className = 'result-message warning';
        resultDiv.innerHTML = `
            <strong>⚠️ Duplicate File</strong><br>
            ${escapeHtml(conflictData.message)}<br>
            <small>Uploaded: ${formatDate(conflictData.existing_document.created_at)}</small><br>
            <small>Document ID: ${conflictData.existing_document.document_id}</small>
        `;
    } else if (conflictData.conflict_type === 'content_duplicate') {
        // Same content, different filename
        resultDiv.className = 'result-message warning';
        resultDiv.innerHTML = `
            <strong>⚠️ Content Already Exists</strong><br>
            ${escapeHtml(conflictData.message)}<br>
            <small>${escapeHtml(conflictData.suggestion)}</small>
        `;
    } else if (conflictData.conflict_type === 'version_conflict') {
        // Same filename, different content - offer options
        resultDiv.className = 'result-message warning';
        resultDiv.innerHTML = `
            <strong>⚠️ File Version Conflict</strong><br>
            ${escapeHtml(conflictData.message)}<br>
            <small>Existing version uploaded: ${formatDate(conflictData.existing_document.created_at)}</small><br>
            <small>Current version: ${conflictData.existing_document.version}</small><br>
            <br>
            <strong>What would you like to do?</strong><br>
            <button onclick="uploadDocument('replace')" class="btn btn-warning btn-small" style="margin: 5px;">
                🔄 Replace Old Version
            </button>
            <button onclick="uploadDocument('keep-both')" class="btn btn-primary btn-small" style="margin: 5px;">
                📑 Keep Both Versions
            </button>
            <button onclick="cancelUpload()" class="btn btn-secondary btn-small" style="margin: 5px;">
                ❌ Cancel
            </button>
        `;
    }
}
// отмена загрузки
function cancelUpload() {
    const resultDiv = document.getElementById('upload-result');
    resultDiv.style.display = 'none';
    
    // Reset form
    selectedFile = null;
    document.getElementById('file-input').value = '';
    document.getElementById('file-info').style.display = 'none';
    document.getElementById('upload-btn').disabled = true;
}

// #############################
// TREE TAB LOGIC
// #############################

// построение дерева файлов
async function loadFilesystemTree() {
    const container = document.getElementById("filesystem-tree");

    container.innerHTML = "⏳ Loading...";

    try {
        // const res = await fetch("/api/filesystem/node?path=");
        const res = await fetch(
            `/api/filesystem/node?path=&collection_name=${encodeURIComponent(currentCollection)}`
        );
        const data = await res.json();

        container.innerHTML = renderNode("", data);

    } catch (err) {
        container.innerHTML = "❌ Error loading tree";
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
// открытие папок внутри дерева
document.addEventListener("click", async function (e) {
    if (!e.target.classList.contains("folder-toggle")) return;

    const toggle = e.target;
    const content = toggle.nextElementSibling;
    const path = toggle.dataset.path;

    if (toggle.dataset.loaded === "false") {
        try {
            content.innerHTML = "⏳ Loading...";

            // const res = await fetch(`/api/filesystem/node?path=${encodeURIComponent(path)}`);
            const res = await fetch(
                `/api/filesystem/node?path=${encodeURIComponent(path)}&collection_name=${encodeURIComponent(currentCollection)}`
            );
            const data = await res.json();

            content.innerHTML = renderNode(path, data);
            toggle.dataset.loaded = "true";

        } catch (err) {
            content.innerHTML = "❌ Error";
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
        loadNewsHistory();
    }
}

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
    container.innerHTML = '<div class="loading">Загрузка...</div>';

    try {
        const res = await fetch("/api/news");
        if (!res.ok) {
            throw new Error("Failed to load news");
        }

        const data = await res.json();

        if (!data || data.length === 0) {
            container.innerHTML = `
                <div class="empty-state">
                    <div class="icon">📭</div>
                    <p>Нет новостей</p>
                </div>
            `;
            return;
        }

        container.innerHTML = data.map(n =>{
            const files = n.files || [];
            const targetGroup = n.target_group || "all";
            const filesHtml = files.length > 0
                ? files.map(f => `
                    <div style="margin-top:5px;">
                        📎 ${escapeHtml(f.name)}
                        <button 
                            class="btn btn-small btn-secondary"
                            onclick="viewNewsFile('${f.name}')">
                            View File 👁
                        </button>
                    </div>
                `).join("")
                : '<div style="color:#999;">Без файлов</div>';
            return `
                <div class="document-card">
                    <div class="document-header">
                        <div class="document-title">
                            📰 ID: ${n.id}
                        </div>
                        <button 
                            class="btn btn-primary btn-small"
                            onclick="reuseNewsById(${n.id})">
                            📋 Использовать
                        </button>
                        <button 
                            class="btn btn-primary btn-danger btn-small"
                            onclick="deleteNews(${n.id})">
                            🗑️ Удалить
                        </button>
                    </div>
                    
                    <div class="document-meta">
                        <div class="meta-item">
                            📅 ${formatDate(n.created_at)}
                        </div>
                        <div class="meta-item">
                            ⏰ ${n.scheduled_at ? formatDate(n.scheduled_at) : formatDate(n.created_at)}
                        </div>
                        <div class="meta-item">
                            📊 ${n.status}
                        </div>
                        <div class="meta-item">
                            👥 ${getTargetGroupName(targetGroup) || n.target_group || "all"}
                        </div>
                    </div>

                    <div class="result-text" style="margin-top:10px;">
                        <div class="news-preview">
                            ${n.text || ""}
                        </div>
                    </div>
                    <div style="margin-top:10px;">
                        ${filesHtml}
                    </div>
                </div>
            `; 
        }).join("");

    } catch (e) {
        container.innerHTML = `
            <div class="result-message error">
                Ошибка загрузки: ${e.message}
            </div>
        `;
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

// Загрузка вкладки Prompts
async function loadPromptsTab() {
    await loadPromptFiles();
    await loadCurrentPrompt();
}
// Загрузка списка файлов промптов
async function loadPromptFiles() {
    const filesList = document.getElementById("prompt-files-list");
    filesList.innerHTML = '<div class="loading">Загрузка...</div>';
    
    try {
        const res = await fetch("/api/prompts/list");
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
    const editor = document.getElementById("prompt-editor");
    const metaFilename = document.getElementById("prompt-filename");
    const metaSize = document.getElementById("prompt-size");
    const metaModified = document.getElementById("prompt-modified");
    
    editor.value = "Загрузка...";
    editor.disabled = true;
    
    try {
        const res = await fetch("/api/prompts/current");
        const data = await res.json();
        
        currentPromptContent = data.content;
        editor.value = currentPromptContent;
        editor.disabled = false;
        
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
        editor.value = `Ошибка загрузки: ${err.message}`;
        console.error("Error loading current prompt:", err);
    }
}
// Загрузка конкретного файла промпта
async function loadPromptFile(filename) {
    const editor = document.getElementById("prompt-editor");
    
    try {
        const res = await fetch(`/api/prompts/file/${encodeURIComponent(filename)}`);
        const data = await res.json();
        
        editor.value = data.content;
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
        const res = await fetch("/api/prompts/backup", { method: "POST" });
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
    const editor = document.getElementById("prompt-editor");
    const resultDiv = document.getElementById("prompt-result");
    
    const newContent = editor.value;
    
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
            body: JSON.stringify({ content: newContent })
        });
        
        const data = await res.json();
        
        if (res.ok) {
            resultDiv.className = "result-message success";
            resultDiv.innerHTML = `✅ Промпт сохранён!<br>📦 Бэкап создан автоматически`;
            currentPromptContent = newContent;
            await loadPromptFiles();
            // await loadCurrentPrompt();
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
        const res = await fetch(`/api/prompts/restore/${encodeURIComponent(filename)}`, {
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
        const res = await fetch(`/api/prompts/file/${encodeURIComponent(filename)}`, {
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

// #############################
// GROUPS TAB LOGIC
// #############################

// Загрузка списка пользователей с группами
async function loadUserGroups() {
    const container = document.getElementById("user-groups-list");
    const searchQuery = document.getElementById("user-search")?.value || "";
    const groupFilter = document.getElementById("group-filter")?.value || "all";
    if (!container) return;

    container.innerHTML = '<div class="loading">Загрузка пользователей...</div>';
    try {
        const res = await fetch("/api/subscribers");  
        if (!res.ok) {
            throw new Error(`Ошибка ${res.status}: ${res.statusText}`);
        }
        const users = await res.json();
        
        // Фильтрация по поиску
        let filtered = users.filter(u => {
            if (!searchQuery) return true;
            const q = searchQuery.toLowerCase();
            return (
                String(u.user_id).includes(q) ||
                (u.username && u.username.toLowerCase().includes(q)) ||
                (u.first_name && u.first_name.toLowerCase().includes(q)) ||
                (u.last_name && u.last_name.toLowerCase().includes(q))
            );
        });
        
        // Фильтрация по группе
        if (groupFilter === "manager_group") {
            filtered = filtered.filter(u => u.manager_group);
        } else if (groupFilter === "coach_group") {
            filtered = filtered.filter(u => u.coach_group);
        } else if (groupFilter === "no_groups") {
            filtered = filtered.filter(u => !u.manager_group && !u.coach_group);
        }
        
        // Обновление статистики
        updateGroupStats(users);
        if (filtered.length === 0) {
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
                        <th>User ID</th>
                        <th>Username</th>
                        <th>Имя</th>
                        <th class="text-center">👔 Менеджер</th>
                        <th class="text-center">🎓 Коуч</th>
                        <th>Телефон</th>
                        <th>Последний вход</th>
                    </tr>
                </thead>
                <tbody>
                    ${filtered.map(u => `
                        <tr>
                            <td class="font-monospace">${u.user_id}</td>
                            <td>${escapeHtml(u.username || '-')}</td>
                            <td>${escapeHtml(u.first_name || '')} ${escapeHtml(u.last_name || '')}</td>
                            <td class="text-center">
                                <input type="checkbox" 
                                      ${u.manager_group ? 'checked' : ''} 
                                      onchange="toggleUserGroup(${u.user_id}, 'manager_group', this.checked)">
                            </td>
                            <td class="text-center">
                                <input type="checkbox" 
                                      ${u.coach_group ? 'checked' : ''} 
                                      onchange="toggleUserGroup(${u.user_id}, 'coach_group', this.checked)">
                            </td>
                            <td>${escapeHtml(u.phone_number || '-')}</td>
                            <td>${formatDate(u.last_seen)}</td>
                        </tr>
                    `).join('')}
                </tbody>
            </table>
        `;
        
    } catch (e) {
        container.innerHTML = `
            <div class="result-message error">
                Ошибка загрузки: ${e.message}
            </div>
        `;
        console.error("Error loading user groups:", e);
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
            <div class="groups-stat-card groups-stat-total">
                <div class="groups-stat-number">${total}</div>
                <div class="groups-stat-label">Всего пользователей</div>
            </div>
            <div class="groups-stat-card groups-stat-managers">
                <div class="groups-stat-number">${managers}</div>
                <div class="groups-stat-label">👔 Менеджеры</div>
            </div>
            <div class="groups-stat-card groups-stat-couchs">
                <div class="groups-stat-number">${couchs}</div>
                <div class="groups-stat-label">🎓 Коучи</div>
            </div>
            <div class="groups-stat-card groups-stat-both">
                <div class="groups-stat-number">${both}</div>
                <div class="groups-stat-label">В обеих группах</div>
            </div>
            <div class="groups-stat-card groups-stat-none">
                <div class="groups-stat-number">${noGroups}</div>
                <div class="groups-stat-label">Без групп</div>
            </div>
        </div>
    `;
}
// Переключение группы пользователя
async function toggleUserGroup(userId, group, value) {
    try {
        const res = await fetch("/api/subscribers/group", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                user_id: userId,
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
        
        showNotification(`Пользователь ${userId} ${action} группы "${groupName}"`, "success");
        
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
    const table = document.querySelector("#user-groups-list table");
    if (!table) {
        alert("Нет данных для экспорта");
        return;
    }
    
    let csv = [];
    const rows = table.querySelectorAll("tr");
    
    rows.forEach(row => {
        const cols = row.querySelectorAll("th, td");
        const rowData = [];
        
        cols.forEach((col, index) => {
            // Для чекбоксов берём состояние
            const checkbox = col.querySelector("input[type='checkbox']");
            if (checkbox) {
                rowData.push(`"${checkbox.checked ? 'Yes' : 'No'}"`);
            } else {
                rowData.push(`"${col.textContent.trim().replace(/"/g, '""')}"`);
            }
        });
        
        csv.push(rowData.join(","));
    });
    
    const csvContent = csv.join("\n");
    const blob = new Blob([csvContent], { type: "text/csv;charset=utf-8;" });
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = `users_groups_${new Date().toISOString().split('T')[0]}.csv`;
    link.click();
    
    showNotification("Экспорт выполнен успешно", "success");
}