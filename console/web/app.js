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

    // Convert remaining newlines to br, but avoid adding extra ones inside pre/ul/li blocks
    const lines = escaped.split('\n');
    let inPre = false;
    const processedLines = lines.map(line => {
      if (line.includes('<pre>')) inPre = true;
      if (line.includes('</pre>')) inPre = false;
      if (inPre || line.trim().startsWith('<ul') || line.trim().startsWith('</ul') || line.trim().startsWith('<li') || line.trim().startsWith('</li') || line.trim().startsWith('<h')) {
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

  function sendMessage(text) {
    if (!text || !text.trim()) return;
    if (!ws || ws.readyState !== WebSocket.OPEN) {
      addMessage("system", "Connection interrupted. Reconnecting...");
      connect();
      return;
    }
    addMessage("user", text.trim());
    ws.send(JSON.stringify({ type: "message", text: text.trim() }));
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

  // ----- Control de Aprobación -----
  function showApprovalOverlay(data) {
    if (!approvalOverlay) return;
    const name = data.name || "unknown";
    const action = data.action || "unknown";
    const params = data.params || {};

    if (approvalDesc) approvalDesc.textContent = `Ability '${name}' requests execution of '${action}':`;
    if (approvalDetail) approvalDetail.textContent = JSON.stringify(params, null, 2);
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
      if (abilitiesList) {
        abilitiesList.innerHTML = abs.length
          ? abs.map((a) => `<li><b>${escapeHtml(a.name || a.skill || "")}</b> <small>(${escapeHtml(a.domain || "core")})</small></li>`).join("")
          : '<li class="muted">No abilities loaded</li>';
      }

      const facts = (factsRes && factsRes.facts) || [];
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
    // 1. Exact: Microsoft Zira Desktop - English (United States)
    let v = voices.find(v => v.name === "Microsoft Zira Desktop - English (United States)");
    if (v) return v;
    // 2. Any Zira
    v = voices.find(v => v.name.toLowerCase().includes("zira"));
    if (v) return v;
    // 3. Any named English US female voice
    const en_female = ["aria", "jenny", "eva", "samantha", "victoria", "karen", "catherine", "linda", "hazel"];
    v = voices.find(v => v.lang.toLowerCase().startsWith("en") && en_female.some(k => v.name.toLowerCase().includes(k)));
    if (v) return v;
    // 4. Any English voice whatsoever
    v = voices.find(v => v.lang.toLowerCase().startsWith("en-us") || v.lang.toLowerCase().startsWith("en-gb"));
    if (v) return v;
    v = voices.find(v => v.lang.toLowerCase().startsWith("en"));
    return v || null;
  }

  function _speakNow(text) {
    try {
      window.speechSynthesis.cancel();
      let cleanText = text.replace(/```[\s\S]*?```/g, "Code executed.")
                          .replace(/\|[\s\S]*?\|/g, " ")
                          .replace(/[*#_>~`]/g, " ")
                          .replace(/[{}[\]"']/g, " ")
                          .replace(/\s+/g, " ")
                          .trim();
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

  // ----- Voz TTS -----
  function speakBack(text) {
    if (!optTts || !optTts.checked || !("speechSynthesis" in window)) return;
    if (!text) return;

    if (_voicesReady) {
      _speakNow(text);
    } else {
      // Voices not yet loaded — wait for them then speak
      _doLoadVoices();
      if (_voicesReady) {
        _speakNow(text);
      } else {
        const originalHandler = window.speechSynthesis.onvoiceschanged;
        window.speechSynthesis.onvoiceschanged = function() {
          _doLoadVoices();
          window.speechSynthesis.onvoiceschanged = originalHandler || null;
          _speakNow(text);
        };
      }
    }
  }

  // Prime voice loading immediately so it is ready by first response
  if ("speechSynthesis" in window) {
    _doLoadVoices();
    if (!_voicesReady && window.speechSynthesis.onvoiceschanged !== undefined) {
      window.speechSynthesis.onvoiceschanged = _doLoadVoices;
    }
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
