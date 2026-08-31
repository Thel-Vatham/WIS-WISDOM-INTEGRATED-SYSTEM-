// ============================================================
// WIS Console — Lógica de Frontend Sobria & Consola Resizable
// ============================================================

(function () {
  "use strict";

  // ----- Referencias DOM -----
  const $ = (id) => document.getElementById(id);
  const orb = $("orb");
  const brandStatus = $("brand-status");
  const conn = $("conn");
  const connText = conn ? conn.querySelector(".conn-text") : null;
  const messages = $("messages");
  const empty = $("empty");
  const typing = $("typing");
  const input = $("input");
  const sendBtn = $("send");

  // Elementos de Consola Inferior Resizable
  const bottomConsole = $("bottom-console");
  const consoleResizer = $("console-resizer");
  const btnToggleConsole = $("btn-toggle-console");
  const btnToggleConsoleTop = $("btn-toggle-console-top");
  const btnClearTrace = $("btn-clear-trace");

  // Tab Feeds & Stats
  const cognitiveTrace = $("cognitive-trace");
  const logsPre = $("logs-pre");
  const statFacts = $("stat-facts");
  const statVault = $("stat-vault");
  const statAbilities = $("stat-abilities");
  const abilitiesList = $("abilities-list");
  const factsList = $("facts-list");
  const optAutoscroll = $("opt-autoscroll");
  const optTts = $("opt-tts");

  // Nuevos elementos de seguridad y loop agéntico
  const modeChip = $("mode-chip");
  const modeIcon = $("mode-icon");
  const modeLabel = $("mode-label");
  const stepBadge = $("step-badge");
  const stepNum = $("step-num");
  const stepMax = $("step-max");
  const approvalOverlay = $("approval-overlay");
  const approvalBadge = $("approval-badge");
  const approvalTitle = $("approval-title");
  const approvalDesc = $("approval-desc");
  const approvalDetail = $("approval-detail");
  const btnProceed = $("btn-proceed");
  const btnDeny = $("btn-deny");

  // Nuevos elementos de Proactividad
  const proactivityInbox = $("proactivity-inbox");
  const proactivityRoutines = $("proactivity-routines");
  const proactivityRules = $("proactivity-rules");

  // ----- Estado Interno -----
  let ws = null;
  let wsAlive = false;
  let reconnectAttempts = 0;
  let activeWisMsg = null;
  let pendingText = "";
  let wisToken = null;
  let currentSecurityMode = "secure";

  // Resizing state
  let isResizing = false;
  let startY = 0;
  let startHeight = 240;

  // ----- Auth Token & Fetch -----
  async function loadAuthToken() {
    try {
      const res = await fetch(apiUrl("/api/auth/bootstrap"));
      if (!res.ok) return null;
      const data = await res.json();
      wisToken = data.token || null;
      return wisToken;
    } catch (e) {
      return null;
    }
  }

  function authFetch(url, options) {
    const opts = options || {};
    if (wisToken) {
      opts.headers = Object.assign({}, opts.headers || {}, {
        Authorization: "Bearer " + wisToken,
      });
    }
    return fetch(url, opts);
  }

  function wsUrl() {
    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    let url = `${proto}//${location.host}/ws`;
    if (wisToken) {
      url += `?token=${encodeURIComponent(wisToken)}`;
    }
    return url;
  }

  function apiUrl(path) {
    return `${location.protocol}//${location.host}${path}`;
  }

  function escapeHtml(str) {
    const div = document.createElement("div");
    div.textContent = str == null ? "" : String(str);
    return div.innerHTML;
  }

  function scrollToBottom() {
    if (optAutoscroll && optAutoscroll.checked && messages) {
      messages.scrollTop = messages.scrollHeight;
    }
  }

  // ----- Estado del Orbe -----
  function setOrbState(state) {
    if (orb) orb.setAttribute("data-state", state);
    if (brandStatus) brandStatus.textContent = state;
  }

  function setConnStatus(status) {
    if (!conn || !connText) return;
    conn.classList.remove("online", "offline");
    if (status === "online") {
      conn.classList.add("online");
      connText.textContent = "connected";
    } else if (status === "offline") {
      conn.classList.add("offline");
      connText.textContent = "disconnected";
    } else {
      connText.textContent = "connecting";
    }
  }

  function parseMarkdown(text) {
    if (!text) return "";
    let escaped = escapeHtml(text);

    // Block code blocks
    escaped = escaped.replace(/`{3,}(\w*)\n([\s\S]*?)\n`{3,}/g, (match, lang, code) => {
      return `<pre><code class="language-${lang}">${code}</code></pre>`;
    });
    escaped = escaped.replace(/`{3,}(\w*)([\s\S]*?)`{3,}/g, (match, lang, code) => {
      return `<pre><code class="language-${lang}">${code}</code></pre>`;
    });

    // Inline code
    escaped = escaped.replace(/`([^`\n]+)`/g, '<code class="inline-code">$1</code>');

    // Horizontal Rules (--- or ***)
    escaped = escaped.replace(/^(?:---|___|\*\*\*)$/gm, '<hr class="console-hr">');

    // Markdown Tables (| Header | Header |\n|---|---|...)
    escaped = escaped.replace(/(?:^|\n)((?:\|[^\n]+\|\r?\n)+)/g, (match, tableBlock) => {
      const rows = tableBlock.trim().split('\n').map(r => r.trim());
      if (rows.length < 2) return match;
      
      const delimIndex = rows.findIndex(r => /^\|(?:\s*:?-+:?\s*\|)+$/.test(r));
      if (delimIndex === -1) return match;

      let tableHtml = '\n<div class="table-container"><table class="console-table"><thead><tr>';
      
      const headerCells = rows[0].split('|').slice(1, -1);
      headerCells.forEach(cell => {
        tableHtml += `<th>${cell.trim()}</th>`;
      });
      tableHtml += '</tr></thead><tbody>';

      for (let i = 0; i < rows.length; i++) {
        if (i === delimIndex || i === 0) continue;
        const row = rows[i];
        if (!row.startsWith('|')) continue;
        const cells = row.split('|').slice(1, -1);
        tableHtml += '<tr>';
        cells.forEach(cell => {
          tableHtml += `<td>${cell.trim()}</td>`;
        });
        tableHtml += '</tr>';
      }
      tableHtml += '</tbody></table></div>\n';
      return tableHtml;
    });

    // Headers
    escaped = escaped.replace(/^(#{1,6})\s+(.+)$/gm, (match, hashes, content) => {
      const level = hashes.length;
      return `<h${level}>${content}</h${level}>`;
    });

    // Bullet lists (lines starting with - or * or •)
    escaped = escaped.replace(/^(?:\s*)[-\*•]\s+(.+)$/gm, '<li>$1</li>');

    // Bold
    escaped = escaped.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');

    // Italic
    escaped = escaped.replace(/\*([^*]+)\*/g, '<em>$1</em>');

    // Clean up lists: group adjacent <li> elements into <ul>
    escaped = escaped.replace(/(<li>[\s\S]*?<\/li>)/g, '<ul>$1</ul>');
    escaped = escaped.replace(/<\/ul>\s*<ul>/g, '\n');

    // Convert remaining newlines to br, avoiding pre/table/ul/li/h/hr blocks
    const lines = escaped.split('\n');
    let inPre = false;
    let inTable = false;
    const processedLines = lines.map(line => {
      const trimmed = line.trim();
      if (trimmed.includes('<pre>')) inPre = true;
      if (trimmed.includes('</pre>')) inPre = false;
      if (trimmed.includes('<table')) inTable = true;
      if (trimmed.includes('</table>')) {
        inTable = false;
        return line;
      }
      if (inPre || inTable || trimmed.startsWith('<ul') || trimmed.startsWith('</ul') || trimmed.startsWith('<li') || trimmed.startsWith('</li') || trimmed.startsWith('<h') || trimmed.startsWith('<hr') || trimmed.startsWith('<div') || trimmed.startsWith('</div>')) {
        return line;
      }
      return line + '<br>';
    });
    
    return processedLines.join('\n');
  }

  // ----- Mensajería Chat -----
  function hideEmpty() {
    if (empty && empty.parentNode) empty.remove();
  }

  function addMessage(role, text, meta) {
    hideEmpty();
    const wrap = document.createElement("div");
    wrap.className = `msg msg-${role}`;

    const bubble = document.createElement("div");
    bubble.className = "bubble";
    bubble.innerHTML = parseMarkdown(text);

    if (meta) {
      const m = document.createElement("div");
      m.className = "msg-meta";
      m.textContent = meta;
      bubble.appendChild(m);
    }

    wrap.appendChild(bubble);
    messages.appendChild(wrap);
    scrollToBottom();
    return bubble;
  }

  function ensureActiveWisBubble() {
    if (!activeWisMsg) {
      pendingText = "";
      activeWisMsg = addMessage("wis", "");
    }
    return activeWisMsg;
  }

  function appendWisChunk(chunk) {
    ensureActiveWisBubble();
    pendingText += chunk;
    activeWisMsg.innerHTML = parseMarkdown(pendingText);
    scrollToBottom();
  }

  function finalizeWisMessage(data) {
    const rawResp = data.response || pendingText || "";
    let cleanText = rawResp.trim();
    let callsJson = data.calls || [];

    if (cleanText.startsWith("[") || cleanText.startsWith("{")) {
      try {
        const parsed = JSON.parse(cleanText);
        if (Array.isArray(parsed) && parsed.length) {
          callsJson = parsed;
          const actionNames = parsed.map(c => c.action || c.name || c.skill || "tool_call").join(", ");
          cleanText = `Executed: ${actionNames}`;
        } else if (typeof parsed === "object" && (parsed.action || parsed.name)) {
          callsJson = [parsed];
          cleanText = `Executed: ${parsed.action || parsed.name}`;
        }
      } catch (e) {}
    }

    if (!cleanText && callsJson.length) {
      const actionNames = callsJson.map(c => (typeof c === "string" ? c : c.action || c.name || c.skill || "tool_call")).join(", ");
      cleanText = `Executed: ${actionNames}`;
    }

    speakBack(cleanText);

    if (!activeWisMsg) {
      activeWisMsg = addMessage("wis", cleanText);
    } else {
      activeWisMsg.innerHTML = parseMarkdown(cleanText);
    }

    // Inspector de Detalles
    const pathUsed = data.path || data.path_used || "new";
    const inspectBtn = document.createElement("button");
    inspectBtn.className = "inspect-btn";
    const callsCount = Array.isArray(callsJson) ? callsJson.length : 0;
    const resultsJson = data.results || [];
    inspectBtn.innerHTML = `<span>Diagnostic Logs ${callsCount ? "· " + callsCount + " execution call(s)" : ""}</span> <span class="inspect-tag">${escapeHtml(pathUsed)}</span>`;

    const drawer = document.createElement("div");
    drawer.className = "inspect-drawer";
    drawer.hidden = true;

    let drawerHTML = `<div><b>Execution Pipeline Path:</b> ${escapeHtml(pathUsed)}</div>`;
    if (callsCount > 0) {
      drawerHTML += `<div style="margin-top: 6px;"><b>Tool Calls Sent:</b><pre><code>${escapeHtml(JSON.stringify(callsJson, null, 2))}</code></pre></div>`;
    }
    if (Array.isArray(resultsJson) && resultsJson.length > 0) {
      drawerHTML += `<div style="margin-top: 6px;"><b>Execution Outputs:</b><pre><code>${escapeHtml(JSON.stringify(resultsJson, null, 2))}</code></pre></div>`;
    }
    drawer.innerHTML = drawerHTML;

    inspectBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      drawer.hidden = !drawer.hidden;
      scrollToBottom();
    });

    activeWisMsg.appendChild(inspectBtn);
    activeWisMsg.appendChild(drawer);

    activeWisMsg = null;
    pendingText = "";
    scrollToBottom();
  }

  function setTyping(on) {
    if (!typing) return;
    if (on) typing.removeAttribute("hidden");
    else typing.setAttribute("hidden", "true");
  }

  // ----- Multimodal Attachment State & Handlers -----
  let pendingAttachments = [];
  const attachmentPreview = $("attachment-preview");
  const attachBtn = $("attach-btn");
  const fileInput = $("file-input");
  const dropOverlay = $("drop-overlay");
  const chatMain = $("chat-main");

  function renderAttachmentsPreview() {
    if (!attachmentPreview) return;
    if (!pendingAttachments.length) {
      attachmentPreview.setAttribute("hidden", "true");
      attachmentPreview.innerHTML = "";
      return;
    }
    attachmentPreview.removeAttribute("hidden");
    attachmentPreview.innerHTML = pendingAttachments.map((att, idx) => {
      const icon = att.is_image ? (att.previewUrl ? `<img class="thumb" src="${att.previewUrl}" />` : '🖼️') : '📄';
      return `
        <div class="attachment-chip">
          <span>${icon}</span>
          <span>${escapeHtml(att.filename)}</span>
          <button class="btn-remove" data-idx="${idx}" title="Remove attachment">&times;</button>
        </div>
      `;
    }).join("");

    attachmentPreview.querySelectorAll(".btn-remove").forEach(btn => {
      btn.addEventListener("click", (e) => {
        const idx = parseInt(e.target.getAttribute("data-idx"), 10);
        if (!isNaN(idx)) {
          pendingAttachments.splice(idx, 1);
          renderAttachmentsPreview();
        }
      });
    });
  }

  async function uploadFile(file) {
    if (!file) return;
    const formData = new FormData();
    formData.append("file", file);

    try {
      const res = await authFetch(apiUrl("/api/upload"), {
        method: "POST",
        body: formData,
      });
      if (!res.ok) throw new Error("Upload HTTP error " + res.status);
      const data = await res.json();
      if (data.success) {
        let previewUrl = "";
        if (data.is_image && file instanceof Blob) {
          previewUrl = URL.createObjectURL(file);
        }
        pendingAttachments.push({
          path: data.path,
          filename: data.filename,
          is_image: data.is_image,
          previewUrl: previewUrl,
        });
        renderAttachmentsPreview();
      }
    } catch (err) {
      console.error("Error uploading file to WIS:", err);
    }
  }

  // Ctrl+V (Paste) event listener
  if (input) {
    input.addEventListener("input", () => {
      if (input.value && input.value.trim()) {
        stopSpeech();
      }
    });
    input.addEventListener("keydown", (e) => {
      stopSpeech();
    });
    input.addEventListener("paste", (e) => {
      stopSpeech();
      const items = (e.clipboardData || (e.originalEvent && e.originalEvent.clipboardData))?.items;
      if (!items) return;
      for (let i = 0; i < items.length; i++) {
        const item = items[i];
        if (item.kind === "file") {
          const blob = item.getAsFile();
          if (blob) {
            const filename = blob.name || `pasted_image_${Date.now()}.png`;
            const file = new File([blob], filename, { type: blob.type });
            uploadFile(file);
          }
        }
      }
    });
  }

  // Clip Button & hidden file input
  if (attachBtn && fileInput) {
    attachBtn.addEventListener("click", () => fileInput.click());
    fileInput.addEventListener("change", (e) => {
      const files = e.target.files;
      if (!files || !files.length) return;
      for (let i = 0; i < files.length; i++) {
        uploadFile(files[i]);
      }
      fileInput.value = "";
    });
  }

  // Drag & Drop handlers
  if (chatMain && dropOverlay) {
    let dragCounter = 0;
    window.addEventListener("dragenter", (e) => {
      if (e.dataTransfer && e.dataTransfer.types && e.dataTransfer.types.includes("Files")) {
        e.preventDefault();
        dragCounter++;
        dropOverlay.removeAttribute("hidden");
      }
    });
    window.addEventListener("dragover", (e) => {
      if (e.dataTransfer && e.dataTransfer.types && e.dataTransfer.types.includes("Files")) {
        e.preventDefault();
      }
    });
    window.addEventListener("dragleave", (e) => {
      e.preventDefault();
      dragCounter--;
      if (dragCounter <= 0) {
        dragCounter = 0;
        dropOverlay.setAttribute("hidden", "true");
      }
    });
    window.addEventListener("drop", (e) => {
      e.preventDefault();
      dragCounter = 0;
      dropOverlay.setAttribute("hidden", "true");
      const files = e.dataTransfer ? e.dataTransfer.files : null;
      if (files && files.length) {
        for (let i = 0; i < files.length; i++) {
          uploadFile(files[i]);
        }
      }
    });
  }

  function sendMessage(text) {
    stopSpeech();
    const trimmed = (text || "").trim();
    if (!trimmed && !pendingAttachments.length) return;
    if (!ws || ws.readyState !== WebSocket.OPEN) {
      addMessage("system", "Connection interrupted. Reconnecting...");
      connect();
      return;
    }

    let messageText = trimmed;
    if (pendingAttachments.length > 0) {
      const attNotes = pendingAttachments.map(att => {
        if (att.is_image) {
          return `[Attached image: ${att.filename} -> absolute path: ${att.path}]`;
        } else {
          return `[Attached file: ${att.filename} -> absolute path: ${att.path}]`;
        }
      }).join("\n");

      messageText = messageText ? `${messageText}\n\n${attNotes}` : attNotes;
    }

    addMessage("user", messageText);
    ws.send(JSON.stringify({ type: "message", text: messageText }));
    
    pendingAttachments = [];
    renderAttachmentsPreview();
    
    setOrbState("thinking");
    setTyping(true);
  }

  // ----- Traza Cognitiva en Tiempo Real -----
  function appendTraceItem(htmlContent) {
    if (!cognitiveTrace) return;
    const emptySpan = cognitiveTrace.querySelector(".trace-empty");
    if (emptySpan) emptySpan.remove();

    const item = document.createElement("div");
    item.style.cssText = "border-bottom: 1px solid rgba(255,255,255,0.05); padding-bottom: 4px; line-height: 1.3;";
    item.innerHTML = htmlContent;
    cognitiveTrace.appendChild(item);
    cognitiveTrace.scrollTop = cognitiveTrace.scrollHeight;
  }

  function handleEventBus(msg) {
    const eventName = msg.event || (msg.data && msg.data._event_name) || "event";
    const data = msg.data || {};

    if (eventName === "pipeline.path_start") {
      appendTraceItem(`<span style="color: #38bdf8; font-weight: 600;">PATH:</span> <span style="color:#f1f5f9">${escapeHtml(data.path)}</span> <small style="color:#64748b">(${escapeHtml(data.text)})</small>`);
    } else if (eventName === "reasoning.prompt") {
      const sysPrompt = escapeHtml(data.system_prompt || "");
      const userMsg = escapeHtml(data.user_message || "");
      const model = escapeHtml(data.model || "llm");
      appendTraceItem(`
        <details>
          <summary style="cursor:pointer; color: #fbbf24; font-weight: 600;">SYSTEM PROMPT & USER INPUT <small>(${model})</small></summary>
          <div style="margin-top:4px; font-size:10px; background:rgba(0,0,0,0.5); padding:6px; border-radius:4px; white-space:pre-wrap; max-height:120px; overflow-y:auto;"><strong style="color:#cbd5e1">SYSTEM:</strong>\n${sysPrompt}\n\n<strong style="color:#cbd5e1">USER:</strong>\n${userMsg}</div>
        </details>
      `);
    } else if (eventName === "reasoning.response") {
      const raw = escapeHtml(data.raw_response || "");
      const calls = escapeHtml(JSON.stringify(data.calls || []));
      appendTraceItem(`
        <details>
          <summary style="cursor:pointer; color: #34d399; font-weight: 600;">LLM RESPONSE & PARSED CALLS</summary>
          <div style="margin-top:4px; font-size:10px; background:rgba(0,0,0,0.5); padding:6px; border-radius:4px; white-space:pre-wrap; max-height:120px; overflow-y:auto;"><strong style="color:#cbd5e1">RAW:</strong>\n${raw}\n\n<strong style="color:#cbd5e1">CALLS:</strong>\n${calls}</div>
        </details>
      `);
    } else if (eventName === "pipeline.call_start") {
      const skill = escapeHtml(data.skill || data.action || "");
      const params = escapeHtml(JSON.stringify(data.params || {}));
      appendTraceItem(`<span style="color: #2dd4bf; font-weight: 600;">EXECUTE:</span> <code style="color:#99f6e4">${skill}</code> <code>${params}</code>`);
    } else if (eventName === "pipeline.call_result") {
      const skill = escapeHtml(data.skill || data.action || "");
      const ok = data.success ? `<span style="color:#34d399">OK</span>` : `<span style="color:#f87171">FAIL</span>`;
      const out = escapeHtml(JSON.stringify(data.output || {}));
      appendTraceItem(`<span style="color: #94a3b8;">OUTPUT (${ok}):</span> <code style="color:#99f6e4">${skill}</code> &rarr; ${out}`);
    } else if (eventName === "pipeline.call_blocked") {
      const name = escapeHtml(data.name || "");
      const reason = escapeHtml(data.reason || "");
      appendTraceItem(`<span style="color: #f87171; font-weight: 600;">SECURITY EXCEPTION (BLOCKED):</span> <code>${name}</code> &mdash; ${reason}`);
    } else if (eventName === "pipeline.approval_required") {
      const name = escapeHtml(data.name || "");
      const action = escapeHtml(data.action || "");
      const params = escapeHtml(JSON.stringify(data.params || {}));
      appendTraceItem(`<span style="color: #fbbf24; font-weight: 600;">AWAITING OPERATOR APPROVAL:</span> <code>${name}.${action}</code> <code>${params}</code>`);
      showApprovalOverlay(data);
    } else if (eventName === "pipeline.call_approved") {
      appendTraceItem(`<span style="color: #34d399; font-weight: 600;">OPERATOR APPROVED</span>`);
      hideApprovalOverlay();
    } else if (eventName === "pipeline.call_denied") {
      appendTraceItem(`<span style="color: #f87171; font-weight: 600;">OPERATOR DENIED</span>`);
      hideApprovalOverlay();
    } else if (eventName === "pipeline.loop_step") {
      updateStepBadge(data.step, data.max_steps);
    } else if (eventName === "pipeline.loop_done") {
      hideStepBadge();
    } else if (eventName === "security.mode_changed") {
      updateSecurityModeUI(data.mode);
    }
  }

  function receiveMessage(data) {
    switch (data.type) {
      case "event_bus":
        handleEventBus(data);
        break;
      case "status":
        if (data.state === "thinking") {
          setOrbState("thinking");
          setTyping(true);
        } else if (data.state === "done") {
          setTyping(false);
          hideStepBadge();
        }
        break;
      case "chunk":
        setTyping(false);
        setOrbState("speaking");
        appendWisChunk(String(data.text || ""));
        break;
      case "response":
        setTyping(false);
        setOrbState("speaking");
        finalizeWisMessage(data);
        setTimeout(() => setOrbState("idle"), 500);
        refreshState();
        break;
      case "error":
        setTyping(false);
        finalizeWisMessage({ response: pendingText });
        addMessage("system", "Error: " + (data.error || "desconocido"));
        setOrbState("idle");
        break;
      default:
        break;
    }
  }

  // ----- Control de Aprobación (Estilo Antigravity / IDE Moderno) -----
  function parseApprovalData(data) {
    const name = (data.name || "").toLowerCase();
    const action = (data.action || "").toLowerCase();
    const params = data.params || {};

    let badge = "⚡ TAREA";
    let title = "Autorización Requerida";
    let desc = "Esta acción requiere tu confirmación para proceder.";
    let detail = "";
    let isDanger = false;

    if (name === "system" || action.includes("shell") || action.includes("cmd") || params.command) {
      badge = "⚡ COMANDO SHELL";
      title = "Ejecución de comando en consola";
      desc = "El sistema solicita ejecutar el siguiente comando:";
      detail = typeof params === "string" ? params : (params.command || JSON.stringify(params, null, 2));
    } else if (action.includes("write") || action.includes("create") || action.includes("modify") || action.includes("file")) {
      badge = "📝 ARCHIVO";
      const pathStr = params.path || params.file || params.filename || "";
      title = pathStr ? `Modificar archivo: ${pathStr}` : "Crear / Modificar archivo";
      desc = "El agente requiere permisos para escribir en el sistema de archivos:";
      detail = params.content || params.code || JSON.stringify(params, null, 2);
    } else if (action.includes("delete") || action.includes("remove") || action.includes("unlink")) {
      badge = "🗑️ ELIMINAR";
      isDanger = true;
      const pathStr = params.path || params.target || "";
      title = pathStr ? `Eliminar: ${pathStr}` : "Eliminación de recurso";
      desc = "Acción destructiva solicitada:";
      detail = JSON.stringify(params, null, 2);
    } else if (name === "browser" || action.includes("navigate") || action.includes("goto")) {
      badge = "🌐 NAVEGADOR";
      title = "Navegación Web";
      desc = `Navegar a: ${params.url || params.target || action}`;
      detail = JSON.stringify(params, null, 2);
    } else if (name === "desktop" || action.includes("click") || action.includes("key")) {
      badge = "🖥️ ESCRITORIO";
      title = "Interacción con pantalla / escritorio";
      desc = `Acción de interfaz: ${action}`;
      detail = JSON.stringify(params, null, 2);
    } else {
      badge = `⚙️ ${(name || "SISTEMA").toUpperCase()}`;
      title = `${action || "Acción restringida"}`;
      desc = `Solicitud de la habilidad '${name}':`;
      detail = typeof params === "string" ? params : (params.command || JSON.stringify(params, null, 2));
    }

    return { badge, title, desc, detail, isDanger };
  }

  function showApprovalOverlay(data) {
    if (!approvalOverlay) return;
    const parsed = parseApprovalData(data);

    if (approvalBadge) {
      approvalBadge.textContent = parsed.badge;
      if (parsed.isDanger) {
        approvalBadge.classList.add("approval-badge--danger");
      } else {
        approvalBadge.classList.remove("approval-badge--danger");
      }
    }
    if (approvalTitle) approvalTitle.textContent = parsed.title;
    if (approvalDesc) approvalDesc.textContent = parsed.desc;
    if (approvalDetail) approvalDetail.textContent = parsed.detail;

    approvalOverlay.removeAttribute("hidden");
  }

  function hideApprovalOverlay() {
    if (approvalOverlay) approvalOverlay.setAttribute("hidden", "true");
  }

  async function postApproval(endpoint) {
    try {
      await authFetch(apiUrl(endpoint), { method: "POST" });
    } catch (e) {
      console.error(e);
    }
    hideApprovalOverlay();
  }

  if (btnProceed) btnProceed.addEventListener("click", () => postApproval("/api/approve"));
  if (btnDeny) btnDeny.addEventListener("click", () => postApproval("/api/deny"));

  // ----- Control de Loop Agéntico -----
  function updateStepBadge(step, max) {
    if (!stepBadge || !stepNum || !stepMax) return;
    stepNum.textContent = step;
    stepMax.textContent = max;
    stepBadge.removeAttribute("hidden");
  }

  function hideStepBadge() {
    if (stepBadge) stepBadge.setAttribute("hidden", "true");
  }

  // ----- Control de Seguridad (Secure / Privileged Mode) -----
  async function fetchSecurityMode() {
    try {
      const res = await authFetch(apiUrl("/api/security/mode"));
      if (!res.ok) return;
      const data = await res.json();
      updateSecurityModeUI(data.mode);
    } catch (e) {}
  }

  function updateSecurityModeUI(mode) {
    currentSecurityMode = mode || "secure";
    if (!modeChip || !modeIcon || !modeLabel) return;
    if (currentSecurityMode === "privileged") {
      modeChip.classList.add("privileged");
      modeIcon.textContent = "⚡";
      modeLabel.textContent = "PRIVILEGED";
    } else {
      modeChip.classList.remove("privileged");
      modeIcon.textContent = "🔒";
      modeLabel.textContent = "SECURE";
    }
  }

  async function toggleSecurityMode() {
    const nextMode = currentSecurityMode === "secure" ? "privileged" : "secure";
    try {
      const res = await authFetch(apiUrl("/api/security/mode"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ mode: nextMode }),
      });
      if (res.ok) {
        const data = await res.json();
        updateSecurityModeUI(data.mode);
      }
    } catch (e) {}
  }

  if (modeChip) modeChip.addEventListener("click", toggleSecurityMode);

  // ----- WebSocket Connection -----
  function connect() {
    setConnStatus("connecting");
    try {
      ws = new WebSocket(wsUrl());
    } catch (e) {
      setConnStatus("offline");
      scheduleReconnect();
      return;
    }

    ws.onopen = () => {
      wsAlive = true;
      reconnectAttempts = 0;
      setConnStatus("online");
      setOrbState("idle");
      refreshState();
      fetchSecurityMode();
    };

    ws.onmessage = (ev) => {
      try {
        receiveMessage(JSON.parse(ev.data));
      } catch (e) {}
    };

    ws.onerror = () => setConnStatus("offline");
    ws.onclose = () => {
      wsAlive = false;
      setConnStatus("offline");
      scheduleReconnect();
    };
  }

  function scheduleReconnect() {
    if (reconnectAttempts >= 10) return;
    const delay = Math.min(8000, 800 * Math.pow(1.6, reconnectAttempts));
    reconnectAttempts++;
    setTimeout(connect, delay);
  }

  async function refreshGoals() {
    const feed = $("goals-feed");
    const badgeGoals = $("badge-goals");
    if (!feed) return;

    try {
      const res = await authFetch(apiUrl("/api/goals"));
      if (!res.ok) return;
      const data = await res.json();
      const goals = data.goals || [];

      if (badgeGoals) badgeGoals.textContent = goals.length;

      if (!goals.length) {
        feed.innerHTML = '<span class="muted">No active goals. Register a goal to track live decomposition & execution.</span>';
        return;
      }

      feed.innerHTML = goals.map(g => {
        const prog = g.progress || { percent: 0, done: 0, total: 0 };
        const subtasks = g.subtasks || [];
        const statusEmoji = g.status === 'done' ? '✅' : (g.status === 'failed' ? '❌' : '⚙️');
        
        const subtasksHtml = subtasks.map(s => {
          const icon = s.status === 'done' ? '✅' : (s.status === 'executing' ? '🔄' : (s.status === 'failed' ? '❌' : '⬜'));
          return `<div class="subtask-row ${s.status}">
            <span>${icon} Paso ${s.order_idx + 1}: ${escapeHtml(s.text)}</span>
          </div>`;
        }).join("");

        return `
          <div class="goal-card">
            <div class="goal-header">
              <span class="goal-title">${statusEmoji} ${escapeHtml(g.text)}</span>
              <div class="goal-meta">
                <span class="goal-status-badge">${g.status}</span>
                <span>${prog.done}/${prog.total} (${prog.percent}%)</span>
              </div>
            </div>
            <div class="goal-progress-bar">
              <div class="goal-progress-fill" style="width: ${prog.percent}%"></div>
            </div>
            <div class="subtasks-list">
              ${subtasksHtml}
            </div>
          </div>
        `;
      }).join("");

    } catch (e) {}
  }

  // ----- Estado y Logs -----
  async function refreshState() {
    try {
      const [stateRes, abRes, factsRes] = await Promise.all([
        authFetch(apiUrl("/api/state")).then((r) => r.json()),
        authFetch(apiUrl("/api/abilities")).then((r) => r.json()),
        authFetch(apiUrl("/api/memory/facts")).then((r) => r.json()),
      ]);
      
      const st = stateRes || {};
      const mem = st.memory || {};
      const vault = st.vault || {};
      const active = st.active_abilities || [];

      if (statFacts) statFacts.textContent = mem.facts ?? active.length ?? 0;
      if (statVault) statVault.textContent = vault.healthy ?? vault.total ?? 0;
      if (statAbilities) statAbilities.textContent = active.length;

      const abs = (abRes && abRes.abilities) || [];
      const badgeAbilities = $("badge-abilities");
      if (badgeAbilities) badgeAbilities.textContent = abs.length;
      if (abilitiesList) {
        abilitiesList.innerHTML = abs.length
          ? abs.map((a) => `<li><b>${escapeHtml(a.name || a.skill || "")}</b> <small>(${escapeHtml(a.domain || "core")})</small></li>`).join("")
          : '<li class="muted">No abilities loaded</li>';
      }

      const facts = (factsRes && factsRes.facts) || [];
      const badgeMemory = $("badge-memory");
      if (badgeMemory) badgeMemory.textContent = facts.length;
      if (factsList) {
        factsList.innerHTML = facts.length
          ? facts.slice(-10).map((f) => `<li>${escapeHtml(f)}</li>`).join("")
          : '<li class="muted">No records stored</li>';
      }
    } catch (e) {}
  }

  async function refreshLogs() {
    if (!logsPre) return;
    try {
      const res = await authFetch(apiUrl("/api/logs?lines=30"));
      if (!res.ok) return;
      const data = await res.json();
      if (data.logs && Array.isArray(data.logs)) {
        logsPre.textContent = data.logs.join("\n");
        logsPre.scrollTop = logsPre.scrollHeight;
        const badgeLogs = $("badge-logs");
        if (badgeLogs) badgeLogs.textContent = data.logs.length;
      }
    } catch (e) {}
  }

  // ----- Carga de Datos de Proactividad -----
  async function refreshProactivity() {
    try {
      const [inboxRes, routinesRes, rulesRes] = await Promise.all([
        authFetch(apiUrl("/api/proactivity/inbox")).then(r => r.json()),
        authFetch(apiUrl("/api/proactivity/routines")).then(r => r.json()),
        authFetch(apiUrl("/api/proactivity/rules")).then(r => r.json())
      ]);

      const inbox = inboxRes.inbox || [];
      if (proactivityInbox) {
        proactivityInbox.innerHTML = inbox.length
          ? inbox.map(i => `
              <li class="proactivity-item" data-id="${i.id}">
                <div class="proactivity-item-header">
                  <strong>${escapeHtml(i.rule_name)}</strong>
                  <span class="p-status badge-${i.status}">${i.status}</span>
                </div>
                <div class="proactivity-item-body">${escapeHtml(i.event ? i.event.text : '')}</div>
                <div class="proactivity-item-meta">${new Date(i.ts).toLocaleString()}</div>
              </li>
            `).join("")
          : '<li class="muted">No suggestions pending.</li>';
      }

      const routines = routinesRes.routines || [];
      if (proactivityRoutines) {
        proactivityRoutines.innerHTML = routines.length
          ? routines.map(r => `
              <li class="proactivity-item">
                <div class="proactivity-item-header">
                  <strong>${escapeHtml(r.name)}</strong>
                  <span class="p-cadence">${r.cadence}</span>
                </div>
                <div class="proactivity-item-body">${escapeHtml(r.suggestion_prompt)}</div>
                <div class="proactivity-item-meta">Next: ${r.next_run_at ? new Date(r.next_run_at).toLocaleString() : 'Never'}</div>
              </li>
            `).join("")
          : '<li class="muted">No routines configured.</li>';
      }

      const rules = rulesRes.rules || [];
      if (proactivityRules) {
        proactivityRules.innerHTML = rules.length
          ? rules.map(r => `
              <li class="proactivity-item">
                <div class="proactivity-item-header">
                  <strong>${escapeHtml(r.name)}</strong>
                  <span class="p-event">${r.event_type}</span>
                </div>
                <div class="proactivity-item-body">Pattern: <code>${escapeHtml(r.pattern)}</code></div>
              </li>
            `).join("")
          : '<li class="muted">No rules configured.</li>';
      }
    } catch (e) {}
  }

  setInterval(refreshGoals, 2500);
  setInterval(refreshLogs, 3500);
  setInterval(refreshProactivity, 5000);

  // ── Voice Engine: force Microsoft Zira Desktop - English (United States) ──
  let _cachedVoices = [];
  let _voicesReady = false;

  function _doLoadVoices() {
    if (!("speechSynthesis" in window)) return;
    const v = window.speechSynthesis.getVoices();
    if (v && v.length > 0) {
      _cachedVoices = v;
      _voicesReady = true;
    }
  }

  function _pickEnglishVoice(voices) {
    if (!voices || voices.length === 0) return null;
    const vList = Array.from(voices);

    // 1. Online Natural Neural voices (Microsoft Jenny, Ava, Emma, Aria Online / Natural)
    let v = vList.find(v => (v.name.toLowerCase().includes("natural") || v.name.toLowerCase().includes("online")) && v.lang.toLowerCase().startsWith("en"));
    if (v) return v;

    // 2. Google Natural English Female
    v = vList.find(v => v.name.toLowerCase().includes("google") && v.lang.toLowerCase().startsWith("en"));
    if (v) return v;

    // 3. Conversational & Soft Neural Female Voices (Jenny, Ava, Emma, Aria, Samantha)
    const soft_voices = ["jenny", "ava", "emma", "aria", "samantha", "hazel", "eva", "victoria"];
    v = vList.find(v => v.lang.toLowerCase().startsWith("en") && soft_voices.some(k => v.name.toLowerCase().includes(k)));
    if (v) return v;

    // 4. Any English Female voice (avoiding robotic Zira if possible)
    v = vList.find(v => v.lang.toLowerCase().startsWith("en") && !v.name.toLowerCase().includes("zira"));
    if (v) return v;

    // 5. Any English Voice
    v = vList.find(v => v.lang.toLowerCase().startsWith("en"));
    return v || vList[0] || null;
  }

  function _cleanTextForSpeech(text) {
    if (!text) return "";
    let clean = String(text);

    // 1. Remove code blocks
    clean = clean.replace(/```[\s\S]*?```/g, " code block executed. ");

    // 2. Format markdown tables
    clean = clean.replace(/\|(?:\s*:?-+:?\s*\|)+/g, " ");
    clean = clean.replace(/\|/g, ". ");

    // 3. Remove all Emojis & Special Unicode Symbols
    clean = clean.replace(/[\u{1F600}-\u{1F64F}\u{1F300}-\u{1F5FF}\u{1F680}-\u{1F6FF}\u{1F1E0}-\u{1F1FF}\u{2600}-\u{27BF}\u{1F900}-\u{1F9FF}\u{1F004}\u{1F0CF}\u{1F170}-\u{1F251}\u{2190}-\u{21FF}\u{2300}-\u{23FF}\u{2B50}\u{2B55}]/gu, "");

    // 4. Clean Windows and Unix File Paths (e.g. C:\Users\..\Report.docx -> Report.docx)
    clean = clean.replace(/[A-Za-z]:\\(?:[^\s\\]+\\)+([^\s\\]+)/g, "$1");
    clean = clean.replace(/(?:\/[^\s\/]+)+\/([^\s\/]+)/g, "$1");

    // 5. Replace backslashes (\) with spaces so TTS NEVER pronounces "backslash"
    clean = clean.replace(/[\\]+/g, " ");
    clean = clean.replace(/http[s]?:\/\/\S+/gi, "link");

    // 6. Clean markdown symbols (*, #, _, ~, `, >, -)
    clean = clean.replace(/^#{1,6}\s+/gm, "");
    clean = clean.replace(/[*#_>~`|]/g, " ");
    clean = clean.replace(/[{}[\]"']/g, " ");

    // 7. Collapse whitespace
    clean = clean.replace(/\s+/g, " ").trim();
    return clean;
  }

  function stopSpeech() {
    if ("speechSynthesis" in window) {
      window.speechSynthesis.cancel();
    }
    // Stop backend Kokoro audio if playing
    stopSpeechBackend();
    if (ws && ws.readyState === WebSocket.OPEN) {
      try {
        ws.send(JSON.stringify({ type: "interrupt", action: "stop_speech" }));
      } catch (e) {}
    }
  }

  function _speakNow(text) {
    try {
      stopSpeech();
      let cleanText = _cleanTextForSpeech(text);
      if (!cleanText) return;
      if (cleanText.length > 3000) cleanText = cleanText.substring(0, 3000) + "...";

      const utterance = new SpeechSynthesisUtterance(cleanText);
      utterance.rate = 0.95;
      utterance.lang = "en-US";
      utterance.pitch = 1.0;
      utterance.volume = 1.0;

      const voice = _pickEnglishVoice(_cachedVoices);
      if (voice) utterance.voice = voice;

      window.speechSynthesis.speak(utterance);
    } catch (e) {}
  }

  // ----- Voz TTS (Kokoro ONNX Backend Exclusivo via /api/tts) -----
  let _currentAudio = null;

  function stopSpeechBackend() {
    if (_currentAudio) {
      _currentAudio.pause();
      _currentAudio.src = "";
      _currentAudio = null;
    }
  }

  function speakBack(text) {
    if (!text || !text.trim()) return;
    // Verificar si TTS está habilitado en el toggle
    if (optTts && !optTts.checked) return;

    // Cancelar audio previo
    stopSpeechBackend();

    authFetch(apiUrl("/api/tts"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: text }),
    })
      .then((res) => {
        if (!res.ok) throw new Error(`TTS error ${res.status}`);
        return res.blob();
      })
      .then((blob) => {
        const url = URL.createObjectURL(blob);
        const audio = new Audio(url);
        _currentAudio = audio;
        setOrbState("speaking");
        audio.play().catch((e) => console.warn("Audio play failed:", e));
        audio.onended = () => {
          URL.revokeObjectURL(url);
          _currentAudio = null;
          setOrbState("idle");
        };
      })
      .catch((e) => console.warn("speakBack error:", e));
  }

  // ----- LÓGICA CONSOLA INFERIOR RESIZABLE & PESTAÑAS -----

  // Resizing vertical con mouse/touch
  if (consoleResizer && bottomConsole) {
    const onMouseMove = (e) => {
      if (!isResizing) return;
      const clientY = e.clientY || (e.touches && e.touches[0].clientY);
      if (!clientY) return;
      const dy = startY - clientY;
      let newHeight = startHeight + dy;
      newHeight = Math.max(46, Math.min(newHeight, window.innerHeight * 0.75));
      bottomConsole.style.height = `${newHeight}px`;
      if (newHeight <= 50) {
        bottomConsole.setAttribute("data-state", "collapsed");
      } else {
        bottomConsole.setAttribute("data-state", "expanded");
      }
    };

    const onMouseUp = () => {
      if (isResizing) {
        isResizing = false;
        document.body.style.cursor = "";
        document.body.style.userSelect = "";
      }
    };

    consoleResizer.addEventListener("mousedown", (e) => {
      isResizing = true;
      startY = e.clientY;
      startHeight = bottomConsole.getBoundingClientRect().height;
      document.body.style.cursor = "ns-resize";
      document.body.style.userSelect = "none";
    });

    consoleResizer.addEventListener("touchstart", (e) => {
      isResizing = true;
      startY = e.touches[0].clientY;
      startHeight = bottomConsole.getBoundingClientRect().height;
    });

    window.addEventListener("mousemove", onMouseMove);
    window.addEventListener("mouseup", onMouseUp);
    window.addEventListener("touchmove", onMouseMove);
    window.addEventListener("touchend", onMouseUp);
  }

  // Toggle Plegar / Desplegar Consola
  function toggleConsole() {
    if (!bottomConsole) return;
    const isCollapsed = bottomConsole.getAttribute("data-state") === "collapsed";
    if (isCollapsed) {
      bottomConsole.setAttribute("data-state", "expanded");
      bottomConsole.style.height = "240px";
    } else {
      bottomConsole.setAttribute("data-state", "collapsed");
      bottomConsole.style.height = "46px";
    }
  }

  if (btnToggleConsole) btnToggleConsole.addEventListener("click", toggleConsole);
  if (btnToggleConsoleTop) btnToggleConsoleTop.addEventListener("click", toggleConsole);

  // Cambio de Pestañas
  document.querySelectorAll(".console-tab").forEach((tabBtn) => {
    tabBtn.addEventListener("click", () => {
      const targetId = tabBtn.getAttribute("data-tab");
      document.querySelectorAll(".console-tab").forEach((t) => t.classList.remove("active"));
      document.querySelectorAll(".tab-content").forEach((c) => c.classList.remove("active"));
      tabBtn.classList.add("active");
      const targetContent = $(targetId);
      if (targetContent) targetContent.classList.add("active");
      
      // Si la consola está plegada, desplegarla al cambiar de pestaña
      if (bottomConsole && bottomConsole.getAttribute("data-state") === "collapsed") {
        toggleConsole();
      }

      if (targetId === "tab-proactivity") {
        refreshProactivity();
      }
    });
  });

  if (btnClearTrace) {
    btnClearTrace.addEventListener("click", () => {
      if (cognitiveTrace) cognitiveTrace.innerHTML = '<span class="trace-empty">Awaiting real-time execution trace...</span>';
    });
  }

  // Input events
  function autoGrow() {
    if (!input) return;
    input.style.height = "auto";
    input.style.height = Math.min(input.scrollHeight, 120) + "px";
  }

  if (input) {
    input.addEventListener("input", autoGrow);
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        const text = input.value;
        if (text.trim()) {
          sendMessage(text);
          input.value = "";
          autoGrow();
        }
      }
    });
  }

  if (sendBtn) {
    sendBtn.addEventListener("click", () => {
      const text = input.value;
      if (text.trim()) {
        sendMessage(text);
        input.value = "";
        autoGrow();
      }
    });
  }

  // ----- Reconocimiento de Voz (STT) por Micrófono -----
  const micBtn = $("mic-btn");
  let recognition = null;
  let isListening = false;

  if (micBtn && ("webkitSpeechRecognition" in window || "SpeechRecognition" in window)) {
    const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    recognition = new SpeechRecognition();
    recognition.continuous = false;
    recognition.interimResults = false;
    recognition.lang = "en-US";

    recognition.onstart = () => {
      stopSpeech();
      isListening = true;
      micBtn.classList.add("listening");
      setOrbState("listening");
      if (input) input.placeholder = "Listening... speak now...";
    };

    recognition.onend = () => {
      isListening = false;
      micBtn.classList.remove("listening");
      setOrbState("idle");
      if (input) input.placeholder = "Type a message to WIS...";
    };

    recognition.onerror = (event) => {
      console.error("Speech recognition error:", event.error);
      isListening = false;
      micBtn.classList.remove("listening");
      setOrbState("idle");
      if (input) input.placeholder = "Type a message to WIS...";
    };

    recognition.onresult = (event) => {
      const resultText = event.results[0][0].transcript;
      if (resultText && resultText.trim()) {
        if (input) {
          input.value = resultText;
          autoGrow();
        }
        sendMessage(resultText);
        if (input) input.value = "";
      }
    };

    micBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      if (isListening) {
        recognition.stop();
      } else {
        recognition.start();
      }
    });
  } else if (micBtn) {
    // Fallback: usar el micrófono físico conectado al servidor backend (/api/listen)
    micBtn.addEventListener("click", async (e) => {
      e.stopPropagation();
      stopSpeech();
      micBtn.classList.add("listening");
      setOrbState("listening");
      if (input) input.placeholder = "Escuchando con el microfono del servidor...";
      try {
        const res = await authFetch(apiUrl("/api/listen"), { method: "POST" });
        if (res.ok) {
          const data = await res.json();
          if (data.success && data.data && data.data.text) {
            sendMessage(data.data.text);
          } else {
            addMessage("system", "Error de escucha: " + (data.message || "sin respuesta"));
          }
        } else {
          addMessage("system", "Fallo al conectar con el microfono del servidor.");
        }
      } catch (err) {
        console.error(err);
      } finally {
        micBtn.classList.remove("listening");
        setOrbState("idle");
        if (input) input.placeholder = "Escribe un mensaje a WIS...";
      }
    });
  }

  document.querySelectorAll(".quick-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      const cmd = btn.getAttribute("data-cmd");
      if (cmd) sendMessage(cmd);
    });
  });

  // Startup
  (async function start() {
    await loadAuthToken();
    connect();
    refreshLogs();
    refreshProactivity();
    setOrbState("idle");
  })();
})();
