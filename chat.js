const chatState = {
  open: false,
  sending: false,
  configured: null,
  messages: [],
};

let focusBinas = null;

export function initChat({ focus } = {}) {
  focusBinas = focus;
  bindChat();
  checkHealth();
}

function bindChat() {
  const form = document.getElementById('chatForm');
  const input = document.getElementById('chatInput');
  const popup = document.getElementById('chatPopup');

  form?.addEventListener('submit', event => {
    event.preventDefault();
    sendMessage();
  });
  input?.addEventListener('keydown', event => {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      sendMessage();
    }
  });
  input?.addEventListener('input', () => autoGrow(input));
  popup?.addEventListener('close', () => {
    chatState.open = false;
    document.getElementById('chatToggle')?.classList.remove('open');
  });
}

async function checkHealth() {
  try {
    const response = await fetch('/api/health');
    const data = await response.json();
    chatState.configured = Boolean(data.configured);
  } catch {
    chatState.configured = false;
  }
  renderMessages();
}

async function sendMessage() {
  const input = document.getElementById('chatInput');
  const text = input?.value.trim();
  if (!text || chatState.sending) return;
  input.value = '';
  autoGrow(input);
  chatState.messages.push({ role: 'user', content: text });
  if (chatState.configured === false) {
    chatState.messages.push({
      role: 'assistant',
      content: 'Vul DEEPSEEK_API_KEY in .env in en herstart de server om de assistent te gebruiken.',
      error: true,
    });
    renderMessages();
    return;
  }
  chatState.sending = true;
  renderMessages();
  try {
    const response = await fetch('/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        messages: chatState.messages
          .filter(msg => (msg.role === 'user' || msg.role === 'assistant') && !msg.error)
          .map(msg => ({ role: msg.role, content: msg.content })),
      }),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(data.error || 'De assistent kon niet antwoorden.');
    }
    const message = {
      role: 'assistant',
      content: data.answer || '',
      page: data.page,
      yFraction: data.yFraction || 0,
      table: data.table || '',
      title: data.title || '',
      highlights: (Array.isArray(data.highlights) ? data.highlights : [])
        .map(item => String(item || '').trim())
        .filter(item => item.length >= 4),
    };
    chatState.messages.push(message);
    if (message.page) focusLocation(message);
  } catch (error) {
    chatState.messages.push({
      role: 'assistant',
      content: error.message || 'Er ging iets mis.',
      error: true,
    });
  } finally {
    chatState.sending = false;
    renderMessages();
  }
}

function focusLocation(message) {
  focusBinas?.({
    page: message.page,
    yFraction: message.yFraction,
    table: message.table,
    title: message.title,
    highlights: message.highlights || [],
  });
}

function renderMessages() {
  const log = document.getElementById('chatMessages');
  if (!log) return;
  log.textContent = '';

  if (!chatState.messages.length) {
    const empty = document.createElement('div');
    empty.className = 'chat-empty';
    const status = chatState.configured === false
      ? 'De assistent is nog niet gekoppeld. Vul <code>DEEPSEEK_API_KEY</code> in <code>.env</code> in en herstart de server.'
      : 'Vraag naar een constante, stof, formule of tabel. Ik zoek in Binas en spring naar de juiste pagina.';
    empty.innerHTML = `<b>Binas-assistent</b><p>${status}</p>`;
    log.appendChild(empty);
  }

  chatState.messages.forEach(msg => {
    const bubble = document.createElement('div');
    bubble.className = `chat-bubble ${msg.role}${msg.error ? ' error' : ''}`;
    if (msg.role === 'assistant') bubble.innerHTML = formatAnswer(msg.content);
    else bubble.textContent = msg.content;
    log.appendChild(bubble);
    if (msg.role === 'assistant' && msg.page && !msg.error) {
      const cite = document.createElement('button');
      cite.type = 'button';
      cite.className = 'chat-cite';
      const table = msg.table ? ` · tabel ${msg.table}` : '';
      cite.textContent = `Toon pagina ${msg.page}${table}`;
      cite.addEventListener('click', () => focusLocation(msg));
      log.appendChild(cite);
    }
  });

  if (chatState.sending) {
    const pending = document.createElement('div');
    pending.className = 'chat-bubble assistant pending';
    pending.innerHTML = '<span class="chat-dots" aria-hidden="true"><i></i><i></i><i></i></span> Zoeken in Binas…';
    log.appendChild(pending);
  }
  log.scrollTop = log.scrollHeight;
}

function formatAnswer(value) {
  const escaped = escapeHtml(value);
  return escaped
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\n/g, '<br>');
}

function escapeHtml(value) {
  return String(value).replace(/[&<>'"]/g, ch => ({
    '&': '&amp;',
    '<': '&lt;',
    '>': '&gt;',
    "'": '&#39;',
    '"': '&quot;',
  }[ch]));
}

function autoGrow(input) {
  input.style.height = 'auto';
  input.style.height = `${Math.min(input.scrollHeight, 120)}px`;
}
