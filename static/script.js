/* ═══════════════════════════════════════════════════════════════
   DataShield AI — Client Logic
   ═══════════════════════════════════════════════════════════════ */

(() => {
    "use strict";

    // ── DOM refs ───────────────────────────────────────────────
    const dropZone       = document.getElementById("drop-zone");
    const fileInput      = document.getElementById("file-input");
    const chatMessages   = document.getElementById("chat-messages");
    const chatForm       = document.getElementById("chat-form");
    const chatInput      = document.getElementById("chat-input");
    const sendBtn        = document.getElementById("send-btn");
    const clearChatBtn   = document.getElementById("clear-chat-btn");
    const schemaSection  = document.getElementById("schema-section");
    const schemaDiagram  = document.getElementById("schema-diagram");
    const toggleSchemaBtn= document.getElementById("toggle-schema-btn");
    const galleryGrid    = document.getElementById("gallery-grid");
    const galleryEmpty   = document.getElementById("gallery-empty");
    const chartCount     = document.getElementById("chart-count");
    const statusPill     = document.getElementById("status-pill");
    const lightbox       = document.getElementById("lightbox");
    const lightboxImg    = document.getElementById("lightbox-img");
    const lightboxClose  = document.getElementById("lightbox-close");
    const toastContainer = document.getElementById("toast-container");

    let knownCharts = new Set();

    // ── Mermaid init ───────────────────────────────────────────
    mermaid.initialize({
        startOnLoad: false,
        theme: "dark",
        themeVariables: {
            darkMode: true,
            background: "#0d0820",
            primaryColor: "#6d28d9",
            primaryTextColor: "#ede9fe",
            primaryBorderColor: "#a78bfa",
            lineColor: "#a78bfa",
            secondaryColor: "#1e1b4b",
            tertiaryColor: "#0d0820",
            fontFamily: "Inter, sans-serif",
            fontSize: "13px",
        },
    });

    // ═══════════════════════════════════════════════════════════
    //  TOAST SYSTEM
    // ═══════════════════════════════════════════════════════════
    function showToast(message, type = "info", durationMs = 3500) {
        const el = document.createElement("div");
        el.className = `toast ${type}`;
        el.style.setProperty("--toast-duration", `${durationMs / 1000}s`);
        el.textContent = message;
        toastContainer.appendChild(el);
        setTimeout(() => el.remove(), durationMs + 500);
    }

    // ═══════════════════════════════════════════════════════════
    //  DRAG & DROP
    // ═══════════════════════════════════════════════════════════
    ["dragenter", "dragover"].forEach(evt =>
        dropZone.addEventListener(evt, e => { e.preventDefault(); dropZone.classList.add("drag-over"); })
    );
    ["dragleave", "drop"].forEach(evt =>
        dropZone.addEventListener(evt, e => { e.preventDefault(); dropZone.classList.remove("drag-over"); })
    );
    dropZone.addEventListener("drop", e => {
        const file = e.dataTransfer.files[0];
        if (file) uploadFile(file);
    });
    dropZone.addEventListener("click", () => fileInput.click());
    fileInput.addEventListener("change", () => {
        if (fileInput.files[0]) uploadFile(fileInput.files[0]);
    });

    // ═══════════════════════════════════════════════════════════
    //  UPLOAD
    // ═══════════════════════════════════════════════════════════
    async function uploadFile(file) {
        if (!file.name.toLowerCase().endsWith(".csv")) {
            showToast("⚠️ Only .csv files are supported", "warning");
            return;
        }

        showToast("📤 Uploading file…", "info");
        const form = new FormData();
        form.append("file", file);

        try {
            showToast("📊 Analyzing schema…", "info", 4000);
            const res = await fetch("/upload", { method: "POST", body: form });
            const data = await res.json();

            if (!res.ok || data.error) {
                showToast(`❌ ${data.error || "Upload failed"}`, "error");
                return;
            }

            showToast("✅ Dataset loaded successfully!", "success");
            dropZone.classList.add("uploaded");
            dropZone.querySelector("p").textContent = file.name;
            dropZone.querySelector(".drop-sub").textContent = `${data.schema.rows} rows × ${data.schema.columns} columns`;

            // Enable chat
            chatInput.disabled = false;
            sendBtn.disabled   = false;
            chatInput.placeholder = `Ask about ${file.name}…`;

            // Status pill
            statusPill.classList.add("active");
            statusPill.querySelector(".label").textContent = file.name;

            // Render schema
            renderSchema(data.schema);
            addSystemMsg(`📂 Loaded **${file.name}** — ${data.schema.rows} rows × ${data.schema.columns} columns`);

        } catch (err) {
            showToast(`❌ Network error: ${err.message}`, "error");
        }
    }

    // ═══════════════════════════════════════════════════════════
    //  MERMAID SCHEMA DIAGRAM
    // ═══════════════════════════════════════════════════════════
    async function renderSchema(schema) {
        schemaSection.classList.remove("hidden");

        // Build mermaid definition
        let def = "graph LR\n";
        const tableId = "T0";
        const safeName = schema.filename.replace(/[^a-zA-Z0-9_]/g, "_");
        def += `    ${tableId}["🗄️ ${schema.filename}"]\n`;

        schema.fields.forEach((f, i) => {
            const fid = `F${i}`;
            const safeLabel = f.name.replace(/"/g, "'");
            def += `    ${tableId} --> ${fid}["${safeLabel}\\n(${f.dtype})"]\n`;
        });

        // Add styling
        def += `    style ${tableId} fill:#6d28d9,stroke:#a78bfa,stroke-width:2px,color:#fff\n`;
        schema.fields.forEach((_, i) => {
            def += `    style F${i} fill:#1e1b4b,stroke:#a78bfa,stroke-width:1px,color:#ede9fe\n`;
        });

        try {
            const { svg } = await mermaid.render("schema-mermaid-" + Date.now(), def);
            schemaDiagram.innerHTML = svg;
            showToast("🗂️ Schema map rendered", "success", 2500);
        } catch (err) {
            schemaDiagram.innerHTML = `<p style="color:var(--error);font-size:.8rem">Schema diagram error: ${err.message}</p>`;
        }
    }

    // ── Toggle schema collapse ─────────────────────────────────
    toggleSchemaBtn.addEventListener("click", () => {
        schemaSection.classList.toggle("collapsed");
        toggleSchemaBtn.textContent = schemaSection.classList.contains("collapsed") ? "▶" : "▼";
    });

    // ═══════════════════════════════════════════════════════════
    //  CHAT
    // ═══════════════════════════════════════════════════════════
    chatForm.addEventListener("submit", async (e) => {
        e.preventDefault();
        const q = chatInput.value.trim();
        if (!q) return;

        addUserMsg(q);
        chatInput.value = "";
        sendBtn.disabled = true;
        chatInput.disabled = true;

        showToast("🧠 AI processing your question…", "info", 5000);
        const typing = addTyping();

        try {
            const res = await fetch("/ask", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ question: q }),
            });
            const data = await res.json();
            typing.remove();

            if (!res.ok || data.error) {
                addAIMsg(`❌ ${data.error || "Something went wrong."}`);
                showToast(`❌ ${data.error || "Error"}`, "error");
            } else {
                // Check for CHART_URL pattern in fallback answer
                if (data.answer && data.answer.startsWith("CHART_URL:")) {
                    const chartUrl = data.answer.replace("CHART_URL:", "").trim();
                    addAIMsg("Here's the chart I generated:", [chartUrl]);
                    addToGallery([chartUrl]);
                    showToast("✅ Chart rendered!", "success");
                } else {
                    addAIMsg(data.answer || "No response from AI.", data.charts || []);
                    if (data.charts && data.charts.length) {
                        addToGallery(data.charts);
                        showToast("✅ Chart rendered!", "success");
                    } else {
                        showToast("✅ Response ready", "success", 2000);
                    }
                }
            }
        } catch (err) {
            typing.remove();
            addAIMsg(`❌ Network error: ${err.message}`);
            showToast(`❌ ${err.message}`, "error");
        } finally {
            sendBtn.disabled = false;
            chatInput.disabled = false;
            chatInput.focus();
        }
    });

    // ── Message helpers ────────────────────────────────────────
    function addUserMsg(text) {
        const el = document.createElement("div");
        el.className = "msg user";
        el.textContent = text;
        chatMessages.appendChild(el);
        scrollChat();
    }

    function addAIMsg(text, chartUrls = []) {
        const el = document.createElement("div");
        el.className = "msg ai";

        // Render basic markdown-like formatting
        let html = escapeHtml(text);

        // Code blocks
        html = html.replace(/```([\s\S]*?)```/g, "<pre><code>$1</code></pre>");
        html = html.replace(/`([^`]+)`/g, "<code>$1</code>");

        // Bold / italic
        html = html.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
        html = html.replace(/\*(.+?)\*/g, "<em>$1</em>");

        // Line breaks
        html = html.replace(/\n/g, "<br/>");

        el.innerHTML = html;

        // Append chart previews
        chartUrls.forEach(url => {
            const wrap = document.createElement("div");
            wrap.className = "chart-preview";
            const img = document.createElement("img");
            img.src = url + "?t=" + Date.now();
            img.alt = "Generated chart";
            img.addEventListener("click", () => openLightbox(img.src));
            wrap.appendChild(img);
            el.appendChild(wrap);
        });

        chatMessages.appendChild(el);
        scrollChat();
    }

    function addSystemMsg(text) {
        const el = document.createElement("div");
        el.className = "msg system";
        // Basic markdown bold
        el.innerHTML = text.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
        chatMessages.appendChild(el);
        scrollChat();
    }

    function addTyping() {
        const el = document.createElement("div");
        el.className = "typing-indicator";
        el.innerHTML = "<span></span><span></span><span></span>";
        chatMessages.appendChild(el);
        scrollChat();
        return el;
    }

    function scrollChat() {
        chatMessages.scrollTop = chatMessages.scrollHeight;
    }

    function escapeHtml(s) {
        const d = document.createElement("div");
        d.appendChild(document.createTextNode(s));
        return d.innerHTML;
    }

    // ── Clear chat ─────────────────────────────────────────────
    clearChatBtn.addEventListener("click", () => {
        chatMessages.innerHTML = "";
        showToast("🗑️ Chat cleared", "info", 2000);
    });

    // ═══════════════════════════════════════════════════════════
    //  GALLERY
    // ═══════════════════════════════════════════════════════════
    function addToGallery(urls) {
        urls.forEach(url => {
            if (knownCharts.has(url)) return;
            knownCharts.add(url);

            galleryEmpty?.remove();

            const card = document.createElement("div");
            card.className = "gallery-card";

            const img = document.createElement("img");
            img.src = url + "?t=" + Date.now();
            img.alt = "Chart";
            img.loading = "lazy";

            const label = document.createElement("div");
            label.className = "card-label";
            label.textContent = url.split("/").pop();

            card.appendChild(img);
            card.appendChild(label);
            card.addEventListener("click", () => openLightbox(img.src));

            galleryGrid.prepend(card);
        });

        chartCount.textContent = knownCharts.size;
    }

    // ── Refresh gallery from server ────────────────────────────
    async function refreshGallery() {
        try {
            const res = await fetch("/charts");
            const data = await res.json();
            if (data.charts) addToGallery(data.charts);
        } catch (_) { /* silent */ }
    }

    // ═══════════════════════════════════════════════════════════
    //  LIGHTBOX
    // ═══════════════════════════════════════════════════════════
    function openLightbox(src) {
        lightboxImg.src = src;
        lightbox.classList.remove("hidden");
    }

    lightboxClose.addEventListener("click", () => lightbox.classList.add("hidden"));
    document.querySelector(".lightbox-backdrop")?.addEventListener("click", () => lightbox.classList.add("hidden"));
    document.addEventListener("keydown", e => {
        if (e.key === "Escape") lightbox.classList.add("hidden");
    });

    // ═══════════════════════════════════════════════════════════
    //  INIT
    // ═══════════════════════════════════════════════════════════
    refreshGallery();
    addSystemMsg("👋 Welcome! Drag a **.csv** file onto the upload area to begin.");

})();
