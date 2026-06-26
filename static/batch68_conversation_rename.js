(function () {
  "use strict";

  if (window.__batch68ConversationRenameLoaded) {
    return;
  }
  window.__batch68ConversationRenameLoaded = true;

  const STATE = {
    titles: {},
    conversations: [],
    currentConversationId: null,
    titleMapLoadedAt: 0,
    scanTimer: null,
    originalFetch: window.fetch ? window.fetch.bind(window) : null
  };

  function log() {
    try {
      if (window.localStorage && window.localStorage.getItem("batch68_debug") === "1") {
        console.log.apply(console, ["[Batch68Rename]"].concat(Array.prototype.slice.call(arguments)));
      }
    } catch (_) {}
  }

  function isObject(x) {
    return x && typeof x === "object" && !Array.isArray(x);
  }

  function getConversationId(obj) {
    if (!isObject(obj)) return null;
    return obj.conversation_id || obj.conversationId || obj.cid || obj.id || obj.session_id || obj.sessionId || null;
  }

  function getConversationTitle(obj) {
    if (!isObject(obj)) return "";
    return String(
      obj.custom_title ||
      obj.title ||
      obj.display_title ||
      obj.name ||
      obj.last_question ||
      obj.question ||
      obj.first_question ||
      ""
    ).trim();
  }

  function getConversationOriginalTitle(obj) {
    if (!isObject(obj)) return "";
    return String(
      obj.original_title ||
      obj.last_question ||
      obj.question ||
      obj.first_question ||
      obj.title ||
      obj.display_title ||
      obj.name ||
      ""
    ).trim();
  }

  function normalizeTitle(s) {
    return String(s || "").replace(/\s+/g, " ").trim();
  }

  function sameOrPrefix(a, b) {
    a = normalizeTitle(a);
    b = normalizeTitle(b);
    if (!a || !b) return false;
    return a === b || a.indexOf(b) >= 0 || b.indexOf(a) >= 0 || a.slice(0, 18) === b.slice(0, 18);
  }

  function collectArraysWithConversations(obj, out, depth) {
    if (!obj || depth > 5) return;
    if (Array.isArray(obj)) {
      if (obj.some(x => isObject(x) && getConversationId(x))) {
        out.push(obj);
      }
      obj.forEach(x => collectArraysWithConversations(x, out, depth + 1));
      return;
    }
    if (isObject(obj)) {
      Object.keys(obj).forEach(k => collectArraysWithConversations(obj[k], out, depth + 1));
    }
  }

  function rememberConversationsFromData(data) {
    const arrays = [];
    collectArraysWithConversations(data, arrays, 0);
    arrays.forEach(arr => {
      arr.forEach(item => {
        if (!isObject(item)) return;
        const cid = getConversationId(item);
        if (!cid) return;
        const title = getConversationTitle(item);
        const oldIndex = STATE.conversations.findIndex(x => x.conversation_id === cid);
        const rec = {
          conversation_id: cid,
          title: title,
          original_title: getConversationOriginalTitle(item),
          raw: item
        };
        if (oldIndex >= 0) {
          STATE.conversations[oldIndex] = rec;
        } else {
          STATE.conversations.push(rec);
        }
      });
    });
  }

  function applyTitlesToData(data) {
    const arrays = [];
    collectArraysWithConversations(data, arrays, 0);
    arrays.forEach(arr => {
      arr.forEach(item => {
        if (!isObject(item)) return;
        const cid = getConversationId(item);
        if (!cid) return;
        const custom = STATE.titles[cid];
        if (!custom) return;
        if (!item.original_title) {
          item.original_title = getConversationOriginalTitle(item);
        }
        item.custom_title = custom;
        item.title = custom;
        item.display_title = custom;
        item.name = custom;
        item.last_question = custom;
      });
    });
    rememberConversationsFromData(data);
    return data;
  }

  async function loadTitleMap(force) {
    const now = Date.now();
    if (!force && now - STATE.titleMapLoadedAt < 3000) {
      return STATE.titles;
    }
    if (!STATE.originalFetch) return STATE.titles;
    try {
      const resp = await STATE.originalFetch("/api/v1/conversation-titles?user=web_user", {
        method: "GET",
        headers: { "Accept": "application/json" },
        cache: "no-store"
      });
      if (!resp.ok) return STATE.titles;
      const data = await resp.json();
      if (data && data.titles && typeof data.titles === "object") {
        STATE.titles = data.titles;
        STATE.titleMapLoadedAt = now;
      }
    } catch (e) {
      log("loadTitleMap failed", e);
    }
    return STATE.titles;
  }

  function isConversationsListUrl(url, method) {
    try {
      const u = new URL(String(url), window.location.origin);
      return (!method || method === "GET") &&
        u.pathname === "/api/v1/conversations";
    } catch (_) {
      return false;
    }
  }

  function isChatUrl(url, method) {
    try {
      const u = new URL(String(url), window.location.origin);
      return method === "POST" && u.pathname === "/api/v1/chat";
    } catch (_) {
      return false;
    }
  }

  function getFetchMethod(input, init) {
    return String((init && init.method) || (input && input.method) || "GET").toUpperCase();
  }

  function getFetchUrl(input) {
    return typeof input === "string" ? input : (input && input.url) || "";
  }

  if (STATE.originalFetch) {
    window.fetch = async function batch68FetchWrapper(input, init) {
      const method = getFetchMethod(input, init);
      const url = getFetchUrl(input);
      const resp = await STATE.originalFetch(input, init);

      if (isConversationsListUrl(url, method)) {
        try {
          await loadTitleMap(true);
          const data = await resp.clone().json();
          rememberConversationsFromData(data);
          const patched = applyTitlesToData(data);
          const headers = new Headers(resp.headers);
          headers.set("content-type", "application/json; charset=utf-8");
          setTimeout(scanAndDecorate, 100);
          return new Response(JSON.stringify(patched), {
            status: resp.status,
            statusText: resp.statusText,
            headers: headers
          });
        } catch (e) {
          log("conversation list patch failed", e);
          return resp;
        }
      }

      if (isChatUrl(url, method)) {
        try {
          const data = await resp.clone().json();
          const cid = data && (data.conversation_id || data.conversationId || data.cid);
          if (cid) {
            STATE.currentConversationId = cid;
            setTimeout(scanAndDecorate, 300);
          }
        } catch (_) {}
      }

      return resp;
    };
  }

  function candidateElements() {
    const nodes = Array.from(document.querySelectorAll("button, a, li, div[role='button'], [data-conversation-id], [data-id], .conversation, .conversation-item, .chat-item, .history-item"));
    return nodes.filter(el => {
      if (!el || el.classList.contains("batch68-rename-button")) return false;
      const text = normalizeTitle(el.innerText || el.textContent || "");
      if (!text || text.length < 4 || text.length > 180) return false;
      if (text.includes("重命名当前会话")) return false;
      return STATE.conversations.some(c =>
        sameOrPrefix(text, c.title) ||
        sameOrPrefix(text, c.original_title) ||
        (STATE.titles[c.conversation_id] && sameOrPrefix(text, STATE.titles[c.conversation_id]))
      );
    });
  }

  function inferConversationForElement(el) {
    if (!el) return null;

    const directAttrs = [
      "data-conversation-id",
      "data-conversationid",
      "data-cid",
      "data-id",
      "data-session-id"
    ];

    for (const attr of directAttrs) {
      const v = el.getAttribute && el.getAttribute(attr);
      if (v && STATE.conversations.some(c => c.conversation_id === v)) {
        return STATE.conversations.find(c => c.conversation_id === v);
      }
    }

    let cur = el;
    for (let i = 0; cur && i < 5; i++, cur = cur.parentElement) {
      for (const attr of directAttrs) {
        const v = cur.getAttribute && cur.getAttribute(attr);
        if (v && STATE.conversations.some(c => c.conversation_id === v)) {
          return STATE.conversations.find(c => c.conversation_id === v);
        }
      }
    }

    const text = normalizeTitle(el.innerText || el.textContent || "");
    if (!text) return null;

    let best = null;
    for (const c of STATE.conversations) {
      const custom = STATE.titles[c.conversation_id];
      if ((custom && sameOrPrefix(text, custom)) || sameOrPrefix(text, c.title) || sameOrPrefix(text, c.original_title)) {
        best = c;
        break;
      }
    }
    return best;
  }

  async function renameConversation(cid, oldTitle) {
    if (!cid) {
      alert("未识别到当前会话 ID，无法重命名。请先点击进入一个历史会话或发送一轮消息后再试。");
      return;
    }

    const next = window.prompt("请输入新的会话名称：", oldTitle || STATE.titles[cid] || "");
    if (next === null) return;
    const title = normalizeTitle(next);
    if (!title) {
      alert("会话名称不能为空。");
      return;
    }
    if (title.length > 80) {
      alert("会话名称过长，请控制在 80 个字符以内。");
      return;
    }

    try {
      const resp = await STATE.originalFetch("/api/v1/conversation-titles/" + encodeURIComponent(cid), {
        method: "PATCH",
        headers: {
          "Content-Type": "application/json",
          "Accept": "application/json"
        },
        body: JSON.stringify({
          user: "web_user",
          title: title
        })
      });
      const data = await resp.json().catch(() => ({}));
      if (!resp.ok || data.ok === false) {
        alert("重命名失败：" + (data.error || resp.status));
        return;
      }

      STATE.titles[cid] = title;
      STATE.titleMapLoadedAt = Date.now();
      STATE.conversations.forEach(c => {
        if (c.conversation_id === cid) {
          c.title = title;
          c.raw = c.raw || {};
          c.raw.custom_title = title;
          c.raw.title = title;
          c.raw.display_title = title;
        }
      });

      updateVisibleTitle(cid, title);
      setTimeout(scanAndDecorate, 100);
    } catch (e) {
      alert("重命名请求失败：" + e);
    }
  }

  function updateVisibleTitle(cid, title) {
    const candidates = candidateElements();
    candidates.forEach(el => {
      const c = inferConversationForElement(el);
      if (!c || c.conversation_id !== cid) return;
      const btn = el.querySelector && el.querySelector(".batch68-rename-button");
      if (btn) btn.remove();

      const textNodes = [];
      const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
      let node;
      while ((node = walker.nextNode())) {
        const t = normalizeTitle(node.nodeValue);
        if (t && t.length >= 4) textNodes.push(node);
      }

      if (textNodes.length) {
        textNodes[0].nodeValue = title;
      } else {
        el.textContent = title;
      }
    });
  }

  function ensureGlobalRenameButton() {
    if (document.getElementById("batch68-rename-current")) return;

    const btn = document.createElement("button");
    btn.id = "batch68-rename-current";
    btn.textContent = "重命名当前会话";
    btn.title = "重命名当前会话";
    btn.style.position = "fixed";
    btn.style.left = "12px";
    btn.style.bottom = "12px";
    btn.style.zIndex = "2147483647";
    btn.style.fontSize = "12px";
    btn.style.padding = "6px 9px";
    btn.style.border = "1px solid #444";
    btn.style.borderRadius = "8px";
    btn.style.background = "#111";
    btn.style.color = "#fff";
    btn.style.cursor = "pointer";
    btn.style.opacity = "0.78";

    btn.addEventListener("mouseenter", () => { btn.style.opacity = "1"; });
    btn.addEventListener("mouseleave", () => { btn.style.opacity = "0.78"; });

    btn.addEventListener("click", async function (ev) {
      ev.preventDefault();
      ev.stopPropagation();

      await loadTitleMap(true);

      let cid = STATE.currentConversationId;
      if (!cid && STATE.conversations.length) {
        cid = STATE.conversations[0].conversation_id;
      }

      const c = STATE.conversations.find(x => x.conversation_id === cid);
      await renameConversation(cid, c && (STATE.titles[cid] || c.title || c.original_title));
    });

    document.body.appendChild(btn);
  }

  function decorateElement(el) {
    if (!el || el.dataset.batch68RenameDecorated === "1") return;
    const c = inferConversationForElement(el);
    if (!c || !c.conversation_id) return;

    el.dataset.batch68RenameDecorated = "1";
    el.dataset.batch68ConversationId = c.conversation_id;

    const oldPos = window.getComputedStyle(el).position;
    if (oldPos === "static") {
      el.style.position = "relative";
    }

    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "batch68-rename-button";
    btn.textContent = "✎";
    btn.title = "重命名会话";
    btn.style.marginLeft = "6px";
    btn.style.fontSize = "12px";
    btn.style.lineHeight = "1";
    btn.style.padding = "2px 4px";
    btn.style.border = "1px solid #555";
    btn.style.borderRadius = "5px";
    btn.style.background = "transparent";
    btn.style.color = "inherit";
    btn.style.cursor = "pointer";
    btn.style.opacity = "0.55";

    btn.addEventListener("mouseenter", () => { btn.style.opacity = "1"; });
    btn.addEventListener("mouseleave", () => { btn.style.opacity = "0.55"; });

    btn.addEventListener("click", async function (ev) {
      ev.preventDefault();
      ev.stopPropagation();
      await loadTitleMap(true);
      const current = inferConversationForElement(el) || c;
      const cid = current.conversation_id;
      await renameConversation(cid, STATE.titles[cid] || current.title || current.original_title);
    });

    el.appendChild(btn);
  }

  async function scanAndDecorate() {
    try {
      await loadTitleMap(false);
      ensureGlobalRenameButton();
      candidateElements().forEach(decorateElement);
    } catch (e) {
      log("scanAndDecorate failed", e);
    }
  }

  function startObserver() {
    if (STATE.scanTimer) return;
    STATE.scanTimer = window.setInterval(scanAndDecorate, 2000);

    const mo = new MutationObserver(function () {
      window.clearTimeout(window.__batch68ScanDebounce);
      window.__batch68ScanDebounce = window.setTimeout(scanAndDecorate, 250);
    });
    mo.observe(document.documentElement || document.body, {
      childList: true,
      subtree: true
    });

    document.addEventListener("click", function () {
      setTimeout(scanAndDecorate, 300);
    }, true);
  }

  function boot() {
    if (!document.body) {
      setTimeout(boot, 100);
      return;
    }
    loadTitleMap(true).then(scanAndDecorate);
    startObserver();
  }

  boot();
})();
