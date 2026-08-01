const messagesEl = document.getElementById("messages");
const chainEl = document.getElementById("chainList");
const composer = document.getElementById("composer");
const input = document.getElementById("messageInput");
const statusPill = document.getElementById("statusPill");
const clearChain = document.getElementById("clearChain");

function createSessionId() {
  if (window.crypto?.randomUUID) {
    return window.crypto.randomUUID();
  }
  return `session-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

try {
  localStorage.removeItem("gtap_agent_session_id");
} catch {
  // Ignore storage failures in locked-down browser contexts.
}

let sessionId = createSessionId();
let stepCounter = 0;
let activeAssistantNode = null;
let activeAssistantMarkdown = "";
let activeThinkingPre = null;

const isFileMode = window.location.protocol === "file:";

function setBusy(isBusy, label = "") {
  statusPill.textContent = label || (isBusy ? "Running" : "Ready");
  statusPill.classList.toggle("busy", isBusy);
  input.disabled = isBusy;
  composer.querySelector("button").disabled = isBusy;
  document.querySelectorAll(".quick-actions button").forEach((button) => {
    button.disabled = isBusy;
  });
}

function addMessage(role, text = "", renderMarkdown = false) {
  const node = document.createElement("div");
  node.className = `message ${role}`;
  if (renderMarkdown) {
    node.classList.add("markdown-body");
    node.innerHTML = renderMarkdownContent(text);
  } else {
    node.textContent = text;
  }
  messagesEl.appendChild(node);
  messagesEl.scrollTop = messagesEl.scrollHeight;
  return node;
}

function appendAssistantDelta(text) {
  if (!activeAssistantNode) {
    activeAssistantMarkdown = "";
    activeAssistantNode = addMessage("assistant", "", true);
  }
  activeAssistantMarkdown += text;
  activeAssistantNode.innerHTML = renderMarkdownContent(activeAssistantMarkdown);
  messagesEl.scrollTop = messagesEl.scrollHeight;
}

function closeAssistantDelta() {
  activeAssistantNode = null;
  activeAssistantMarkdown = "";
}

function appendThinkingDelta(text) {
  if (!activeThinkingPre) {
    const wrapper = document.createElement("details");
    wrapper.className = "thinking-block";
    wrapper.innerHTML = `
      <summary>Agent reasoning</summary>
      <pre></pre>
    `;
    messagesEl.appendChild(wrapper);
    activeThinkingPre = wrapper.querySelector("pre");
  }
  activeThinkingPre.textContent += text;
  messagesEl.scrollTop = messagesEl.scrollHeight;
}

function closeThinkingBlock() {
  activeThinkingPre = null;
}

function ensureChainNotEmpty() {
  const empty = chainEl.querySelector(".empty-state");
  if (empty) empty.remove();
}

function pretty(value) {
  if (typeof value === "string") {
    try {
      return JSON.stringify(JSON.parse(value), null, 2);
    } catch {
      return value;
    }
  }
  return JSON.stringify(value, null, 2);
}

function createStep(name, argumentsText) {
  ensureChainNotEmpty();
  stepCounter += 1;
  const card = document.createElement("article");
  card.className = "step-card running";
  card.dataset.name = name;
  card.innerHTML = `
    <div class="step-head">
      <div class="step-name">${stepCounter}. ${escapeHtml(name)}</div>
      <div class="step-state">Running</div>
    </div>
    <div class="kv">Arguments</div>
    <pre>${escapeHtml(pretty(argumentsText))}</pre>
  `;
  chainEl.prepend(card);
  return card;
}

function finishStep(name, result) {
  const cards = [...chainEl.querySelectorAll(".step-card.running")];
  const card = cards.find((item) => item.dataset.name === name) || cards[0];
  if (!card) return;
  const ok = Boolean(result?.ok);
  card.classList.remove("running");
  card.classList.add(ok ? "ok" : "fail");
  card.querySelector(".step-state").textContent = ok ? "Completed" : "Failed";

  const output = result?.output || {};
  const summaries = output?.summaries || {};
  const detail = document.createElement("div");
  detail.innerHTML = `
    <div class="kv">Exit code: ${escapeHtml(String(output.returncode ?? ""))}; duration: ${escapeHtml(String(output.elapsed_seconds ?? ""))} seconds</div>
    <details open>
      <summary>Summary and output</summary>
      <pre>${escapeHtml(formatToolResult(result, summaries))}</pre>
    </details>
  `;
  card.appendChild(detail);
}

function formatToolResult(result, summaries) {
  const output = result?.output || {};
  const lines = [];
  if (result?.error) lines.push(`ERROR: ${result.error}`);
  if (output.command) lines.push(`COMMAND: ${output.command}`);
  if (output.stdout_tail) lines.push(`\nSTDOUT:\n${output.stdout_tail}`);
  if (output.stderr_tail) lines.push(`\nSTDERR:\n${output.stderr_tail}`);
  const summaryEntries = Object.entries(summaries || {});
  if (summaryEntries.length) {
    lines.push("\nSUMMARY FILES:");
    for (const [path, text] of summaryEntries) {
      lines.push(`\n[${path}]\n${text}`);
    }
  }
  return lines.join("\n") || pretty(result);
}

function escapeHtml(text) {
  return String(text)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function renderInlineMarkdown(text) {
  let html = escapeHtml(text);
  html = html.replace(/`([^`]+)`/g, "<code>$1</code>");
  html = html.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  html = html.replace(/\*([^*]+)\*/g, "<em>$1</em>");
  html = html.replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank" rel="noreferrer">$1</a>');
  return html;
}

function renderTable(lines) {
  const rows = lines.map((line) => line.trim().replace(/^\||\|$/g, "").split("|").map((cell) => cell.trim()));
  if (rows.length < 2) return null;
  const separator = rows[1].every((cell) => /^:?-{3,}:?$/.test(cell));
  if (!separator) return null;
  const headers = rows[0];
  const body = rows.slice(2);
  return `<table><thead><tr>${headers.map((cell) => `<th>${renderInlineMarkdown(cell)}</th>`).join("")}</tr></thead><tbody>${body
    .map((row) => `<tr>${row.map((cell) => `<td>${renderInlineMarkdown(cell)}</td>`).join("")}</tr>`)
    .join("")}</tbody></table>`;
}

function renderMarkdownContent(markdown) {
  const lines = String(markdown || "").replace(/\r\n/g, "\n").split("\n");
  const html = [];
  let paragraph = [];
  let list = [];
  let code = [];
  let inCode = false;
  let table = [];

  function flushParagraph() {
    if (paragraph.length) {
      html.push(`<p>${renderInlineMarkdown(paragraph.join(" "))}</p>`);
      paragraph = [];
    }
  }

  function flushList() {
    if (list.length) {
      html.push(`<ul>${list.map((item) => `<li>${renderInlineMarkdown(item)}</li>`).join("")}</ul>`);
      list = [];
    }
  }

  function flushTable() {
    if (table.length) {
      const rendered = renderTable(table);
      if (rendered) {
        html.push(rendered);
      } else {
        table.forEach((line) => paragraph.push(line));
      }
      table = [];
    }
  }

  for (const line of lines) {
    if (line.trim().startsWith("```")) {
      if (inCode) {
        html.push(`<pre><code>${escapeHtml(code.join("\n"))}</code></pre>`);
        code = [];
        inCode = false;
      } else {
        flushParagraph();
        flushList();
        flushTable();
        inCode = true;
      }
      continue;
    }
    if (inCode) {
      code.push(line);
      continue;
    }
    if (/^\s*\|.*\|\s*$/.test(line)) {
      flushParagraph();
      flushList();
      table.push(line);
      continue;
    }
    flushTable();
    const heading = /^(#{1,4})\s+(.+)$/.exec(line);
    if (heading) {
      flushParagraph();
      flushList();
      const level = heading[1].length + 1;
      html.push(`<h${level}>${renderInlineMarkdown(heading[2])}</h${level}>`);
      continue;
    }
    const bullet = /^\s*[-*]\s+(.+)$/.exec(line);
    if (bullet) {
      flushParagraph();
      list.push(bullet[1]);
      continue;
    }
    if (!line.trim()) {
      flushParagraph();
      flushList();
      continue;
    }
    paragraph.push(line.trim());
  }
  flushTable();
  flushParagraph();
  flushList();
  if (inCode) html.push(`<pre><code>${escapeHtml(code.join("\n"))}</code></pre>`);
  return html.join("");
}

function handleEvent(event) {
  if (event.type === "session" && event.session_id) {
    sessionId = event.session_id;
  }
  if (event.type === "reasoning_delta") {
    setBusy(true, "Thinking");
    appendThinkingDelta(event.content || "");
  }
  if (event.type === "assistant_delta" && event.content) {
    closeThinkingBlock();
    setBusy(true, "Responding");
    appendAssistantDelta(event.content);
  }
  if (event.type === "assistant" && event.content) {
    closeThinkingBlock();
    if (activeAssistantNode && activeAssistantMarkdown === event.content) {
      closeAssistantDelta();
    } else {
      closeAssistantDelta();
      addMessage("assistant", event.content, true);
    }
  }
  if (event.type === "tool_start") {
    closeThinkingBlock();
    closeAssistantDelta();
    setBusy(true, event.name);
    createStep(event.name, event.arguments);
  }
  if (event.type === "tool_end") {
    finishStep(event.name, event.result);
  }
  if (event.type === "error") {
    closeThinkingBlock();
    closeAssistantDelta();
    addMessage("error", `${event.name || "error"}: ${event.content}`);
  }
}

async function sendMessage(text) {
  if (isFileMode) {
    addMessage(
      "error",
      "This page is running in static file mode, so it cannot call GTAP Agent or local Python scripts. From the project root, run: python web\\server.py --host 127.0.0.1 --port 8765, then open http://127.0.0.1:8765"
    );
    return;
  }
  addMessage("user", text);
  closeThinkingBlock();
  closeAssistantDelta();
  setBusy(true, "Starting");

  try {
    const response = await fetch("/api/chat_stream", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({session_id: sessionId, message: text}),
    });
    if (!response.ok || !response.body) throw new Error(`HTTP ${response.status}`);

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    while (true) {
      const {value, done} = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, {stream: true});
      const lines = buffer.split("\n");
      buffer = lines.pop() || "";
      for (const line of lines) {
        if (!line.trim()) continue;
        const event = JSON.parse(line);
        if (event.type === "done") continue;
        handleEvent(event);
      }
    }
    if (buffer.trim()) handleEvent(JSON.parse(buffer));
  } catch (error) {
    addMessage("error", error.message);
  } finally {
    closeAssistantDelta();
    setBusy(false);
  }
}

composer.addEventListener("submit", (event) => {
  event.preventDefault();
  const text = input.value.trim();
  if (!text) return;
  input.value = "";
  sendMessage(text);
});

document.querySelectorAll("[data-prompt]").forEach((button) => {
  button.addEventListener("click", () => {
    input.value = button.dataset.prompt;
    input.focus();
  });
});

clearChain.addEventListener("click", () => {
  chainEl.innerHTML = '<div class="empty-state">Tool calls will appear here, including scripts, arguments, exit codes, and summary files.</div>';
  stepCounter = 0;
});

if (isFileMode) {
  addMessage(
    "assistant",
    "This is static preview mode. The page can be viewed, but the full GTAP Agent requires the local Python server to protect the API key, execute scripts, and return tool results.\n\nStart it with: `python web\\server.py --host 127.0.0.1 --port 8765`\n\nThen open: `http://127.0.0.1:8765`",
    true
  );
  setBusy(false, "Static preview");
} else {
  addMessage("assistant", "Connected to the local GTAP tools. You can ask me to fetch observed data, build the 2024 baseline update, apply a policy scenario to the baseline, run a specified CMF, or execute the complete workflow.", true);
}
