
    // ── State ──
    // Patch 45: Immer neue Session beim Start – keine gespeicherte sessionId laden
    let sessionId = crypto.randomUUID();
    let chatMessages = [];  // Patch 67: { text, sender, timestamp } für Chat-Export

    let currentProfile = null;  // { name, display_name, theme_color, token, permission_level, allowed_model, temperature }
    let mediaRecorder, audioChunks = [], isRecording = false;
    let pwVisible = false;
    let evtSource = null;

    const messagesDiv    = document.getElementById('chatMessages');
    const textInput      = document.getElementById('text-input');
    const micErrorDiv    = document.getElementById('mic-error');
    const sessionList    = document.getElementById('session-list');
    const transcriptHint = document.getElementById('transcript-hint');
    const loginScreen    = document.getElementById('login-screen');
    const chatScreen     = document.getElementById('chat-screen');
    const mainHeader     = document.getElementById('main-header');
    const profileBadge   = document.getElementById('profile-badge');
    const statusBar      = document.getElementById('status-bar');

    // ── Profil aus localStorage wiederherstellen ──
    (function restoreProfile() {
        const stored = localStorage.getItem('nala_profile');
        if (stored) {
            try {
                currentProfile = JSON.parse(stored);
                showChatScreen();
            } catch (_) {
                localStorage.removeItem('nala_profile');
            }
        }
    })();

    // ── SSE EventSource (Patch 46) ──
    function connectSSE() {
        if (evtSource) {
            evtSource.close();
            evtSource = null;
        }
        evtSource = new EventSource(`/nala/events?session_id=${sessionId}`);
        evtSource.onmessage = (e) => {
            try {
                const evt = JSON.parse(e.data);
                if (evt.type === 'done') {
                    statusBar.style.opacity = '0';
                } else {
                    statusBar.textContent = evt.message;
                    statusBar.style.opacity = '1';
                }
            } catch (_) {}
        };
        evtSource.onerror = () => {
            // Reconnect nach Fehler – EventSource macht das automatisch
        };
    }

    function disconnectSSE() {
        if (evtSource) {
            evtSource.close();
            evtSource = null;
        }
        statusBar.style.opacity = '0';
        statusBar.textContent = '';
    }

    // ── Passwort-Toggle ──
    function togglePw() {
        pwVisible = !pwVisible;
        const pwField = document.getElementById('login-password');
        pwField.type = pwVisible ? 'text' : 'password';
        document.getElementById('eye-icon').innerHTML = pwVisible
            ? '<path d="M17.94 17.94A10.07 10.07 0 0112 20c-7 0-11-8-11-8a18.45 18.45 0 015.06-5.94M9.9 4.24A9.12 9.12 0 0112 4c7 0 11 8 11 8a18.5 18.5 0 01-2.16 3.19m-6.72-1.07a3 3 0 11-4.24-4.24"/><line x1="1" y1="1" x2="23" y2="23"/>'
            : '<path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/>';
    }

    // ── Login-Logik ──
    document.getElementById('login-username').addEventListener('keypress', e => {
        if (e.key === 'Enter') document.getElementById('login-password').focus();
    });
    document.getElementById('login-password').addEventListener('keypress', e => {
        if (e.key === 'Enter') doLogin();
    });

    async function doLogin() {
        const errorDiv = document.getElementById('login-error');
        const username = document.getElementById('login-username').value.trim();
        if (!username) {
            errorDiv.textContent = 'Benutzername eingeben.';
            return;
        }
        const pw = document.getElementById('login-password').value;
        if (!pw) {
            errorDiv.textContent = 'Passwort eingeben.';
            return;
        }
        errorDiv.textContent = '';
        try {
            const resp = await fetch('/nala/profile/login', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ profile: username, password: pw })
            });
            if (!resp.ok) {
                const err = await resp.json().catch(() => ({}));
                errorDiv.textContent = err.detail || 'Falsches Passwort.';
                return;
            }
            const data = await resp.json();
            currentProfile = {
                name:             username,
                display_name:     data.display_name,
                theme_color:      data.theme_color,
                token:            data.token,
                permission_level: data.permission_level || 'guest',
                allowed_model:    data.allowed_model    || null,
                temperature:      data.temperature      ?? null,  // Patch 61: Per-User Temperatur-Override
            };
            localStorage.setItem('nala_profile', JSON.stringify(currentProfile));
            document.getElementById('login-password').value = '';
            document.getElementById('login-username').value = '';
            showChatScreen();
        } catch (e) {
            errorDiv.textContent = 'Verbindungsfehler.';
        }
    }

    function doLogout() {
        localStorage.removeItem('nala_profile');
        currentProfile = null;
        disconnectSSE();
        // Neue sessionId für nächsten Login
        sessionId = crypto.randomUUID();
        chatMessages = [];  // Patch 67
        messagesDiv.innerHTML = '';
        chatScreen.style.display = 'none';
        loginScreen.style.display = 'flex';
    }

    function showChatScreen() {
        loginScreen.style.display = 'none';
        chatScreen.style.display  = 'flex';
        if (currentProfile) {
            mainHeader.style.background = currentProfile.theme_color;
            profileBadge.textContent    = '– ' + currentProfile.display_name;
        }
        loadSessions();
        connectSSE();
        if (messagesDiv.children.length === 0) {
            fetchGreeting();  // Patch 67: dynamische Begrüßung per API
        }
    }

    // ── Profile-Header für API-Requests (Patch 54: Bearer-Token statt X-Permission-Level) ──
    function profileHeaders(extra) {
        const h = Object.assign({ 'X-Session-ID': sessionId }, extra || {});
        if (currentProfile && currentProfile.token) {
            h['Authorization'] = 'Bearer ' + currentProfile.token;
        }
        return h;
    }

    // ── 401-Handler: automatisch ausloggen ──
    function handle401() {
        localStorage.removeItem('nala_profile');
        currentProfile = null;
        disconnectSSE();
        sessionId = crypto.randomUUID();
        chatMessages = [];  // Patch 67
        messagesDiv.innerHTML = '';
        chatScreen.style.display = 'none';
        loginScreen.style.display = 'flex';
        document.getElementById('login-error').textContent = 'Sitzung abgelaufen – bitte erneut einloggen.';
    }

    // ── Archiv ──
    async function loadSessions() {
        try {
            // Patch 67 fix: Auth-Header mitschicken – /archive/* ist JWT-geschützt
            const response = await fetch('/archive/sessions', { headers: profileHeaders() });
            if (!response.ok) {
                sessionList.innerHTML = '<li class="session-item">Keine Chats (Auth-Fehler)</li>';
                return;
            }
            const sessions = await response.json();
            sessionList.innerHTML = '';
            if (sessions.length === 0) {
                sessionList.innerHTML = '<li class="session-item">Keine Chats</li>';
            } else {
                sessions.forEach(s => {
                    const li = document.createElement('li');
                    li.className = 'session-item';
                    li.innerHTML = `<div>${s.first_message || 'Neuer Chat'}</div>
                                    <div class="session-date">${new Date(s.created_at).toLocaleString()}</div>`;
                    li.onclick = () => loadSession(s.session_id);
                    sessionList.appendChild(li);
                });
            }
        } catch (e) {
            sessionList.innerHTML = '<li class="session-item">Fehler beim Laden</li>';
        }
    }

    async function loadSession(sid) {
        try {
            const response = await fetch(`/archive/session/${sid}`, { headers: profileHeaders() });
            const messages = await response.json();
            // sessionId im Speicher aktualisieren (kein localStorage-Eintrag)
            sessionId = sid;
            messagesDiv.innerHTML = '';
            chatMessages = [];  // Patch 67: Reset für neue Session
            messages.forEach(m => {
                const ts = m.timestamp
                    ? new Date(m.timestamp).toLocaleTimeString('de-DE', { hour: '2-digit', minute: '2-digit' })
                    : null;
                addMessage(m.content, m.role === 'user' ? 'user' : 'bot', ts);
            });
            toggleSidebar();
            // SSE neu verbinden mit neuer sessionId
            connectSSE();
        } catch (e) {
            alert('Fehler beim Laden des Chats');
        }
    }

    // ── Chat ──
    async function sendTextMessage() {
        const text = textInput.value.trim();
        if (!text) return;
        transcriptHint.textContent = '';
        sendMessage(text);
    }

    async function sendMessage(text) {
        addMessage(text, 'user');
        textInput.value = '';
        // Status-Bar zurücksetzen bei neuem Chat
        statusBar.textContent = '';
        statusBar.style.opacity = '0';

        try {
            const response = await fetch('/v1/chat/completions', {
                method: 'POST',
                headers: profileHeaders({ 'Content-Type': 'application/json' }),
                body: JSON.stringify({ messages: [{ role: 'user', content: text }] })
            });
            if (response.status === 401) { handle401(); return; }
            const data = await response.json();
            const reply = data.choices?.[0]?.message?.content || 'Keine Antwort';
            addMessage(reply, 'bot');
            loadSessions();
        } catch (error) {
            addMessage('❌ Fehler: ' + error.message, 'bot');
        }
    }

    // ── Mikrofon + editierbares Transkript ──
    async function toggleRecording() {
        if (!isRecording) {
            try {
                const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
                mediaRecorder = new MediaRecorder(stream);
                audioChunks = [];
                mediaRecorder.ondataavailable = e => audioChunks.push(e.data);
                mediaRecorder.onstop = async () => {
                    const audioBlob = new Blob(audioChunks, { type: 'audio/webm' });
                    const formData  = new FormData();
                    formData.append('file', audioBlob, 'recording.webm');
                    try {
                        const response = await fetch('/nala/voice', {
                            method: 'POST',
                            headers: profileHeaders(),
                            body: formData
                        });
                        if (response.status === 401) { handle401(); return; }
                        const data = await response.json();
                        const transcript = data.transcript || '';
                        if (transcript) {
                            // Transkript ins Eingabefeld – KEIN Auto-Send
                            textInput.value = transcript;
                            textInput.setSelectionRange(transcript.length, transcript.length);
                            textInput.focus();
                            transcriptHint.textContent = '🎤 Transkript – prüfen und mit Enter senden';
                        }
                    } catch (e) {
                        addMessage('❌ Fehler bei Spracherkennung', 'bot');
                    }
                };
                mediaRecorder.start();
                isRecording = true;
                document.getElementById('micBtn').classList.add('recording');
                micErrorDiv.textContent = '';
            } catch (error) {
                micErrorDiv.textContent = '❌ Mikrofon nicht erlaubt. Bitte erlaube den Zugriff in den Browser-Einstellungen.';
            }
        } else {
            mediaRecorder.stop();
            mediaRecorder.stream.getTracks().forEach(track => track.stop());
            isRecording = false;
            document.getElementById('micBtn').classList.remove('recording');
        }
    }

    // Hinweistext ausblenden sobald der User zu tippen beginnt
    textInput.addEventListener('input', () => {
        if (transcriptHint.textContent) transcriptHint.textContent = '';
        // Patch 67: auto-expand
        textInput.style.height = 'auto';
        textInput.style.height = Math.min(textInput.scrollHeight, 140) + 'px';
    });

    // Patch 67: Textarea auto-expand on focus/blur
    textInput.addEventListener('focus', () => {
        textInput.style.height = 'auto';
        const sh = textInput.scrollHeight;
        textInput.style.height = Math.min(Math.max(sh, 96), 140) + 'px';
    });
    textInput.addEventListener('blur', () => {
        if (!textInput.value.trim()) {
            textInput.style.height = '48px';
        }
    });

    // Patch 67: keydown statt keypress (Shift+Enter = Zeilenumbruch, Enter = Senden)
    textInput.addEventListener('keydown', e => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            sendTextMessage();
        }
    });

    // ── Nachrichten anzeigen (Patch 65: Export-Dropdown / Patch 67: Toolbar + Tracking) ──
    function addMessage(text, sender, tsOverride) {
        const now = new Date();
        const timeStr = tsOverride || now.toLocaleTimeString('de-DE', { hour: '2-digit', minute: '2-digit' });
        chatMessages.push({ text, sender, timestamp: timeStr });

        const color = (currentProfile && sender === 'user') ? currentProfile.theme_color : null;
        const msgDiv = document.createElement('div');
        msgDiv.className = `message ${sender === 'user' ? 'user-message' : 'bot-message'}`;
        if (color) msgDiv.style.background = color;
        msgDiv.textContent = text;

        const wrapper = document.createElement('div');
        wrapper.className = sender === 'user' ? 'msg-wrapper user-wrapper' : 'msg-wrapper';
        wrapper.appendChild(msgDiv);

        // Patch 67: Toolbar mit Timestamp + Kopieren-Button
        const toolbar = document.createElement('div');
        toolbar.className = 'msg-toolbar';
        const timeSpan = document.createElement('span');
        timeSpan.textContent = timeStr;
        const copyBtn = document.createElement('button');
        copyBtn.className = 'copy-btn';
        copyBtn.title = 'Kopieren';
        copyBtn.textContent = '📋';
        copyBtn.onclick = () => copyBubble(text, copyBtn);
        toolbar.appendChild(timeSpan);
        toolbar.appendChild(copyBtn);
        wrapper.appendChild(toolbar);

        if (sender === 'bot') {
            const exportRow = document.createElement('div');
            exportRow.className = 'export-row';
            const sel = document.createElement('select');
            sel.className = 'export-select';
            sel.title = 'Antwort exportieren';
            sel.innerHTML = '<option value="">⬇ Export…</option>'
                + '<option value="pdf">Als PDF</option>'
                + '<option value="docx">Als DOCX</option>'
                + '<option value="md">Als Markdown</option>'
                + '<option value="txt">Als TXT</option>';
            sel.addEventListener('change', function() {
                const fmt = this.value;
                if (!fmt) return;
                this.value = '';
                exportMessage(text, fmt);
            });
            exportRow.appendChild(sel);
            wrapper.appendChild(exportRow);
        }
        messagesDiv.appendChild(wrapper);
        messagesDiv.scrollTop = messagesDiv.scrollHeight;
    }

    // Patch 67: Kopieren-Feedback
    function copyBubble(text, btn) {
        navigator.clipboard.writeText(text).then(() => {
            const orig = btn.textContent;
            btn.textContent = '✓';
            btn.classList.add('copy-ok');
            setTimeout(() => { btn.textContent = orig; btn.classList.remove('copy-ok'); }, 1500);
        }).catch(() => {});
    }

    // ── Export-Funktion (Patch 65) ──
    async function exportMessage(text, fmt) {
        if (!currentProfile) { alert('Nicht eingeloggt'); return; }
        try {
            const res = await fetch('/nala/export', {
                method: 'POST',
                headers: profileHeaders({ 'Content-Type': 'application/json' }),
                body: JSON.stringify({ text, format: fmt, filename: 'nala_antwort' })
            });
            if (!res.ok) {
                const err = await res.json().catch(() => ({}));
                alert('Export fehlgeschlagen: ' + (err.detail || res.status));
                return;
            }
            const blob = await res.blob();
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = `nala_antwort.${fmt}`;
            document.body.appendChild(a);
            a.click();
            setTimeout(() => { URL.revokeObjectURL(url); a.remove(); }, 1000);
        } catch (e) {
            alert('Export-Fehler: ' + e.message);
        }
    }

    function toggleSidebar() {
        const sidebar = document.getElementById('sidebar');
        sidebar.classList.toggle('open');
        document.getElementById('overlay').classList.toggle('show');
        // Patch 47: Mein-Ton-Prompt laden wenn Sidebar öffnet
        if (sidebar.classList.contains('open')) loadMyPrompt();
    }

    // ── Patch 47: Mein Ton ──
    async function loadMyPrompt() {
        if (!currentProfile) return;
        try {
            const res = await fetch('/nala/profile/my_prompt', {
                headers: profileHeaders()
            });
            if (!res.ok) return;
            const data = await res.json();
            document.getElementById('my-prompt-area').value = data.prompt || '';
        } catch (_) {}
    }

    // ── Patch 67: Neue Session ──
    function newSession() {
        sessionId = crypto.randomUUID();
        chatMessages = [];
        messagesDiv.innerHTML = '';
        toggleSidebar();
        connectSSE();
        fetchGreeting();
    }

    // ── Patch 67: Chat als .txt exportieren ──
    function exportChat() {
        if (chatMessages.length === 0) {
            alert('Kein Chat zum Exportieren.');
            return;
        }
        const lines = chatMessages.map(m =>
            `[${m.timestamp}] ${m.sender === 'user' ? 'Du' : 'Nala'}: ${m.text}`
        );
        const content = lines.join('\n');
        const blob = new Blob([content], { type: 'text/plain; charset=utf-8' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `nala_chat_${new Date().toISOString().slice(0, 10)}.txt`;
        document.body.appendChild(a);
        a.click();
        setTimeout(() => { URL.revokeObjectURL(url); a.remove(); }, 1000);
    }

    // ── Patch 67: Vollbild-Modal ──
    function fullscreenOpen() {
        document.getElementById('fullscreen-modal').classList.add('open');
        document.getElementById('fullscreen-textarea').value = textInput.value;
        document.getElementById('fullscreen-textarea').focus();
    }
    function fullscreenClose(accept) {
        if (accept) {
            textInput.value = document.getElementById('fullscreen-textarea').value;
            textInput.style.height = 'auto';
            textInput.style.height = Math.min(textInput.scrollHeight, 140) + 'px';
            textInput.focus();
        }
        document.getElementById('fullscreen-modal').classList.remove('open');
    }

    // ── Patch 67: Dynamische Begrüßung ──
    async function fetchGreeting() {
        let greeting = 'Hallo! Wie kann ich dir helfen?';
        try {
            const res = await fetch('/nala/greeting', { headers: profileHeaders() });
            if (res.ok) {
                const data = await res.json();
                greeting = data.greeting || greeting;
            }
        } catch (_) {}
        addMessage(greeting, 'bot');
    }

    async function saveMyPrompt() {
        if (!currentProfile) return;
        const prompt = document.getElementById('my-prompt-area').value;
        const statusEl = document.getElementById('my-prompt-status');
        try {
            const res = await fetch('/nala/profile/my_prompt', {
                method: 'POST',
                headers: profileHeaders({ 'Content-Type': 'application/json' }),
                body: JSON.stringify({ prompt })
            });
            if (res.ok) {
                statusEl.textContent = '✓ Gespeichert';
                setTimeout(() => { statusEl.textContent = ''; }, 2500);
            } else {
                statusEl.textContent = '❌ Fehler beim Speichern';
            }
        } catch (_) {
            statusEl.textContent = '❌ Verbindungsfehler';
        }
    }
