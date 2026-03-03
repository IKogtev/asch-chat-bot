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


// Initialize on load
document.addEventListener('DOMContentLoaded', () => {
    loadAliasData();
    loadCollections();
    loadActiveCollections();
    loadCollectionInfo();
    loadDocuments();
    setupDragAndDrop();
});

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
        

        // Очистка поиска
        document.getElementById('search-results').innerHTML = '';
        document.getElementById('search-query').value = '';

    } catch (e) {
        console.error('[switchCollection] error', e);
        alert(`Failed to switch collection: ${e.message}`);
    }
}


// Tab Management
function showTab(tabName) {
    // Hide all tabs
    document.querySelectorAll('.tab-content').forEach(tab => {
        tab.classList.remove('active');
    });
    document.querySelectorAll('.tab-button').forEach(btn => {
        btn.classList.remove('active');
    });
    
    // Show selected tab
    document.getElementById(`${tabName}-tab`).classList.add('active');
    event.target.classList.add('active');
    
    // Load data if needed
    if (tabName === 'documents') {
        loadDocuments();
    } else if (tabName === 'search') {
        loadKnowledgeBasesForSearch();
    } else if (tabName === 'tree_files'){
        loadFilesystemTree();
    }
}

// Load knowledge bases for search filter
async function loadKnowledgeBasesForSearch() {
    try {
        const response = await fetch(`${API_BASE}/api/knowledge-bases`);
        const knowledgeBases = await response.json();
        
        const kbSelect = document.getElementById('search-kb');
        const currentValue = kbSelect.value;
        
        // Keep "All Knowledge Bases" option and add individual KBs
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

// Collection Info
async function loadCollectionInfo() {
    try {
        const response = await fetch(`${API_BASE}/api/collections/info`);
        const data = await response.json();
        const isFAQ = currentCollectionType === 'faq';
        
        document.getElementById('collection-info').innerHTML = 
            `Collection: <strong>${data.name}</strong> | ${isFAQ? "Documents": "Points"} <strong>${data.points_count-1 || 0}</strong>`;
    } catch (error) {
        console.error('Error loading collection info:', error);
    }
}

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
    return escapeHtml(
        doc.source_name ||
        doc.payload?.source_name ||
        doc.source ||
        doc.filename ||
        'Document'
    );
}

document.addEventListener("click", function (e) {
    if (e.target.classList.contains("view-doc-btn")) {
        const docId = e.target.dataset.docId;
        const docName = e.target.dataset.docName;
        viewDocument(docId, docName);
    }
    if (e.target.classList.contains("delete-doc-btn")) {
        const docId = e.target.dataset.docId;
        const docName = e.target.dataset.docName;
        deleteDocument(docId, docName);
    }
});

document.addEventListener("click", async function (e) {

    if (e.target.classList.contains("sync-kb-btn")) {

        e.stopPropagation(); // чтобы не сработал toggleKB

        const button = e.target;
        const kbId = button.dataset.kbId;

        button.disabled = true;
        button.innerText = "⏳ Syncing...";

        const formData = new FormData();
        formData.append("kb_id", kbId);
        formData.append("collection_type", "kb");

        try {
            const response = await fetch("/api/filesystem/sync", {
                method: "POST",
                body: formData
            });

            if (!response.ok) {
                throw new Error("Sync failed");
            }

            button.innerText = "✅ Synced";

            setTimeout(() => {
                button.innerText = "🔄 Sync KB";
                button.disabled = false;
            }, 1500);

            if (button.innerText==="✅ Synced"){
                loadDocuments()
            }

        } catch (err) {
            console.error(err);
            button.innerText = "❌ Error";
            button.disabled = false;
        }
    }
    
});

// Documents Management
async function loadDocuments() {
    const container = document.getElementById('documents-list');
    container.innerHTML = '<div class="loading">Loading knowledge bases...</div>';
    
    try {
        const response = await fetch(`${API_BASE}/api/knowledge-bases`);
        const knowledgeBases = await response.json();
        const isFAQ = currentCollectionType === 'faq';
        
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
    } catch (error) {
        container.innerHTML = `
            <div class="result-message error">
                Error loading documents: ${error.message}
            </div>
        `;
    }
}

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
        } else {
            throw new Error('Failed to delete KB');
        }
       
    } catch (err) {
        alert(`Error deleting knowledge base: ${err.message}`);
    }
}



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
            return `
            <div class="chunk-item-compact">
                <div class="chunk-header-compact">
                    <strong>Chunk ${chunkIndex + 1}</strong> (${charCount} chars)
                </div>
                <div class="result-text">${escapeHtml(isFAQ? question+" - "+ answer : chunk.text)}</div>
                <div class="chunk-metadata-json">
                    { "source_name": "${escapeHtml(meta.source_name || meta.source || 'unknown')}",
                      "kb_id": "${escapeHtml(meta.kb_id || 'N/A')}", 
                      "user_id": "${escapeHtml(meta.user_id || 'N/A')}", 
                      "source_type": "${escapeHtml(meta.source_type || 'N/A')}", 
                      "version": ${meta.version || 1}, 
                      "document_id": "${escapeHtml(meta.document_id || '')}", 
                      "created_at": "${meta.created_at || 'Unknown'}",
                      "section_path": "[${meta.section_path || '[]'}]" }
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

// Search
function handleSearchKeypress(event) {
    if (event.key === 'Enter') {
        performSearch();
    }
}

function parseFaqQuestion(text) {
    if (!text) return "";

    // убираем "Question:"
    let q = text.replace(/^Question:\s*/i, "");

    // отрезаем всё после context:
    q = q.split(/\ncontext:/i)[0];

    return q.trim();
}



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
                        { 
                          "source_name": "${escapeHtml(r.source_name || 'unknown')}",
                          "kb_id": "${escapeHtml(r.kb_id || 'N/A')}",
                          "user_id": "${escapeHtml(r.user_id || 'N/A')}",
                          "source_type": "${escapeHtml(r.source_type || 'N/A')}",
                          "document_id": "${escapeHtml(r.document_id || '')}",
                          "created_at": "${r.created_at || 'Unknown'}",
                          "score": ${score}
                        }
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

// Upload
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

function handleFileSelect(event) {
    const file = event.target.files[0];
    if (file) {
        handleFile(file);
    }
}

function handleFile(file) {
    selectedFile = file;
    
    document.getElementById('file-name').textContent = file.name;
    document.getElementById('file-info').style.display = 'block';
    document.getElementById('upload-btn').disabled = false;
    document.getElementById('upload-result').className = 'result-message';
    document.getElementById('upload-result').style.display = 'none';
}

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

function cancelUpload() {
    const resultDiv = document.getElementById('upload-result');
    resultDiv.style.display = 'none';
    
    // Reset form
    selectedFile = null;
    document.getElementById('file-input').value = '';
    document.getElementById('file-info').style.display = 'none';
    document.getElementById('upload-btn').disabled = true;
}

// Modal
function closeModal() {
    document.getElementById('chunks-modal').classList.remove('active');
}

// Close modal on outside click
document.getElementById('chunks-modal').addEventListener('click', (e) => {
    if (e.target.id === 'chunks-modal') {
        closeModal();
    }
});

// Utilities
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

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

function formatDate(dateString) {
    if (!dateString) return 'Unknown';
    const date = new Date(dateString);
    return date.toLocaleDateString() + ' ' + date.toLocaleTimeString();
}

function showNotification(message, type) {
    // Simple notification - you can enhance this
    alert(message);
}

function getCollectionType(name) {
  if (name.startsWith("faq_")) return "faq";
  if (name.startsWith("kb_")) return "kb";
  return null;
}

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

function openCreateCollectionModal() {
    const modal = document.getElementById("create-collection-modal");
    modal.classList.add("active");
}

function closeCreateCollectionModal() {
    const modal = document.getElementById("create-collection-modal");
    modal.classList.remove("active");
}

async function createCollection() {
    const version = document.getElementById("newCollectionVersion").value.trim();
    const type = document.getElementById("newCollectionType").value;
    const errorBox = document.getElementById("create-collection-error");

    errorBox.classList.add("hidden");
    errorBox.textContent = "";

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


async function loadAliasData() {
    const [collectionsRes, activeRes] = await Promise.all([
        fetch("/api/collections/by-type"),
        fetch("/api/collections/active")
    ]);

    collectionsByType = await collectionsRes.json();
    activeAliases = await activeRes.json();
}


async function openSwitchCollectionModal() {
    document.getElementById("switch-collection-modal").classList.add("active");
     if (!collectionsByType.faq) {
        await loadAliasData();
    }

    loadAliasCollections();
}

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


function closeSwitchCollectionModal() {
    document.getElementById("switch-collection-modal").classList.remove("active");
}

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
        // await loadCollections();
        await loadActiveCollections();

        

    } catch (err) {
        errorBox.textContent = err.message;
        errorBox.classList.remove("hidden");
    }
}

async function loadFilesystemTree() {
    const container = document.getElementById("filesystem-tree");

    container.innerHTML = "⏳ Loading...";

    try {
        const response = await fetch("/api/filesystem/folders");
        const tree = await response.json();

        container.innerHTML = renderTree(tree);

    } catch (err) {
        container.innerHTML = "❌ Error loading tree";
        console.error(err);
    }
}

function renderTree(node) {
    let html = "<ul class='tree'>";

    for (const key in node) {

        if (key === "files") {
            node[key].forEach(file => {
                html += `<li class="file">📄 ${escapeHtml(file)}</li>`;
            });
        }

        else if (typeof node[key] === "object") {
            html += `
                <li class="folder">
                    <span class="folder-toggle">📁 ${escapeHtml(key)}</span>
                    <div class="folder-content">
                        ${renderTree(node[key])}
                    </div>
                </li>
            `;
        }
    }

    html += "</ul>";
    return html;
}

document.addEventListener("click", function (e) {

    if (e.target.classList.contains("folder-toggle")) {
        const content = e.target.nextElementSibling;
        content.classList.toggle("open");
    }

});