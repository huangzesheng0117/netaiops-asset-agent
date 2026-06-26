(function () {
  "use strict";

  /*
   * Batch64 safe markdown renderer
   *
   * 修复目标：
   * 1. 保留 Markdown 渲染能力。
   * 2. 禁止扫描/重写全页面父级 div。
   * 3. 禁止 MutationObserver + innerHTML 自触发递归。
   * 4. 只渲染“叶子级文本块”，不渲染页面容器、输入框、按钮、表格、代码块内部。
   */

  if (window.__N63_SAFE_MARKDOWN_RENDERER_V2__) {
    return;
  }
  window.__N63_SAFE_MARKDOWN_RENDERER_V2__ = true;

  var rendering = false;
  var scheduled = false;
  var rendered = new WeakSet();
  var observer = null;

  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");
  }

  function inlineMd(s) {
    var x = esc(s);
    x = x.replace(/`([^`]+)`/g, '<code class="n63-inline-code">$1</code>');
    x = x.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
    x = x.replace(/__([^_]+)__/g, '<strong>$1</strong>');
    x = x.replace(/\*([^*]+)\*/g, '<em>$1</em>');
    return x;
  }

  function isTableBlock(lines) {
    if (!lines || lines.length < 2) return false;
    if (lines[0].indexOf("|") < 0 || lines[1].indexOf("|") < 0) return false;
    return /^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$/.test(lines[1]);
  }

  function renderTable(lines) {
    var headers = lines[0].trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map(function (x) {
      return x.trim();
    });

    var body = lines.slice(2).filter(function (line) {
      return line.trim() && line.indexOf("|") >= 0;
    });

    var html = '<div class="n63-table-wrap"><table class="n63-table"><thead><tr>';
    headers.forEach(function (h) {
      html += "<th>" + inlineMd(h) + "</th>";
    });
    html += "</tr></thead><tbody>";

    body.forEach(function (line) {
      var cells = line.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map(function (x) {
        return x.trim();
      });
      html += "<tr>";
      for (var i = 0; i < headers.length; i++) {
        html += "<td>" + inlineMd(cells[i] || "") + "</td>";
      }
      html += "</tr>";
    });

    html += "</tbody></table></div>";
    return html;
  }

  function renderMarkdown(raw) {
    raw = String(raw == null ? "" : raw).replace(/\r\n/g, "\n");
    var lines = raw.split("\n");
    var out = [];
    var i = 0;
    var inCode = false;
    var codeLines = [];
    var para = [];

    function flushPara() {
      if (!para.length) return;
      out.push("<p>" + inlineMd(para.join("\n")).replace(/\n/g, "<br>") + "</p>");
      para = [];
    }

    function flushCode() {
      var code = codeLines.join("\n");
      out.push(
        '<div class="n63-code-wrap">' +
          '<button type="button" class="n63-copy-btn">复制</button>' +
          "<pre><code>" + esc(code) + "</code></pre>" +
        "</div>"
      );
      codeLines = [];
    }

    while (i < lines.length) {
      var line = lines[i];

      if (/^\s*```/.test(line)) {
        if (inCode) {
          inCode = false;
          flushCode();
        } else {
          flushPara();
          inCode = true;
          codeLines = [];
        }
        i += 1;
        continue;
      }

      if (inCode) {
        codeLines.push(line);
        i += 1;
        continue;
      }

      if (!line.trim()) {
        flushPara();
        i += 1;
        continue;
      }

      if (/^#{1,6}\s+/.test(line)) {
        flushPara();
        var m = line.match(/^(#{1,6})\s+(.*)$/);
        var lvl = Math.min(4, Math.max(3, m[1].length + 2));
        out.push("<h" + lvl + ">" + inlineMd(m[2].trim()) + "</h" + lvl + ">");
        i += 1;
        continue;
      }

      if (i + 1 < lines.length && isTableBlock(lines.slice(i))) {
        flushPara();
        var t = [];
        while (i < lines.length && lines[i].trim() && lines[i].indexOf("|") >= 0) {
          t.push(lines[i]);
          i += 1;
        }
        out.push(renderTable(t));
        continue;
      }

      if (/^\s*[-*]\s+/.test(line)) {
        flushPara();
        out.push("<ul>");
        while (i < lines.length && /^\s*[-*]\s+/.test(lines[i])) {
          out.push("<li>" + inlineMd(lines[i].replace(/^\s*[-*]\s+/, "")) + "</li>");
          i += 1;
        }
        out.push("</ul>");
        continue;
      }

      if (/^\s*\d+\.\s+/.test(line)) {
        flushPara();
        out.push("<ol>");
        while (i < lines.length && /^\s*\d+\.\s+/.test(lines[i])) {
          out.push("<li>" + inlineMd(lines[i].replace(/^\s*\d+\.\s+/, "")) + "</li>");
          i += 1;
        }
        out.push("</ol>");
        continue;
      }

      para.push(line);
      i += 1;
    }

    if (inCode) flushCode();
    flushPara();

    return '<div class="n63-md">' + out.join("\n") + "</div>";
  }

  function hasMarkdownSignal(text) {
    if (!text) return false;
    if (text.length < 8) return false;
    return (
      /```/.test(text) ||
      /^#{1,6}\s+/m.test(text) ||
      /\*\*[^*]+\*\*/.test(text) ||
      /(^|\n)\s*\|.+\|/.test(text) ||
      /(^|\n)\s*[-*]\s+/.test(text) ||
      /(^|\n)\s*\d+\.\s+/.test(text) ||
      /基于本地LLM对MCP\/Netmiko原始命令输出的分析/.test(text)
    );
  }

  function directTextLength(el) {
    var n = 0;
    el.childNodes.forEach(function (child) {
      if (child.nodeType === Node.TEXT_NODE) n += child.textContent.length;
      if (child.nodeType === Node.ELEMENT_NODE && child.tagName === "BR") n += 1;
    });
    return n;
  }

  function isUnsafeContainer(el) {
    if (!el || el.nodeType !== 1) return true;
    var tag = el.tagName;
    if (!tag) return true;

    tag = tag.toUpperCase();
    if (tag === "HTML" || tag === "BODY" || tag === "MAIN" || tag === "SCRIPT" || tag === "STYLE") return true;
    if (tag === "TEXTAREA" || tag === "INPUT" || tag === "BUTTON" || tag === "SELECT" || tag === "OPTION") return true;
    if (tag === "PRE" || tag === "CODE" || tag === "TABLE" || tag === "THEAD" || tag === "TBODY" || tag === "TR" || tag === "TH") return true;

    if (el.closest && el.closest(".n63-md,.n63-code-wrap,.n63-table-wrap,pre,code,table,textarea,input,button,select")) return true;
    if (el.id && /root|app/i.test(el.id)) return true;

    return false;
  }

  function isCandidate(el) {
    if (isUnsafeContainer(el)) return false;
    if (rendered.has(el)) return false;
    if (el.dataset && el.dataset.n63Rendered === "1") return false;

    var text = (el.innerText || el.textContent || "").trim();
    if (!hasMarkdownSignal(text)) return false;

    if (text.length > 180000) return false;

    // 防止把整页/整段聊天列表父容器拿去 innerHTML。
    // 对“叶子级文本块”放行；对包含大量子节点或直接文本占比很低的父容器跳过。
    var childCount = el.children ? el.children.length : 0;
    var directLen = directTextLength(el);
    var ratio = text.length ? directLen / text.length : 0;

    if (childCount > 8) return false;
    if (childCount > 0 && ratio < 0.55) return false;

    if (el.querySelector && el.querySelector("textarea,input,button,select,form,nav,header,aside")) return false;
    return true;
  }

  function findCandidates(root) {
    var out = [];
    var base = root && root.querySelectorAll ? root : document.body;
    if (!base) return out;

    // 不再使用全量 div querySelectorAll 后直接渲染。
    // 通过 TreeWalker 先找包含 markdown 信号的文本节点，再提升到最近的安全叶子块。
    var walker = document.createTreeWalker(
      base,
      NodeFilter.SHOW_TEXT,
      {
        acceptNode: function (node) {
          var t = node.nodeValue || "";
          if (hasMarkdownSignal(t)) return NodeFilter.FILTER_ACCEPT;
          return NodeFilter.FILTER_REJECT;
        }
      }
    );

    var seen = new WeakSet();
    var node;
    while ((node = walker.nextNode())) {
      var el = node.parentElement;
      while (el && !isCandidate(el) && el.parentElement && el !== base) {
        el = el.parentElement;
        if (isUnsafeContainer(el)) break;
      }
      if (el && isCandidate(el) && !seen.has(el)) {
        seen.add(el);
        out.push(el);
      }
    }

    return out;
  }

  function installCopyHandlers(container) {
    if (!container || !container.querySelectorAll) return;
    container.querySelectorAll(".n63-copy-btn").forEach(function (btn) {
      if (btn.dataset.n63CopyBound === "1") return;
      btn.dataset.n63CopyBound = "1";
      btn.addEventListener("click", function (ev) {
        ev.preventDefault();
        ev.stopPropagation();
        var code = btn.parentElement && btn.parentElement.querySelector("pre code");
        var text = code ? code.innerText : "";
        if (navigator.clipboard && navigator.clipboard.writeText) {
          navigator.clipboard.writeText(text).then(function () {
            btn.textContent = "已复制";
            setTimeout(function () { btn.textContent = "复制"; }, 1200);
          }).catch(function () {
            btn.textContent = "复制失败";
            setTimeout(function () { btn.textContent = "复制"; }, 1200);
          });
        }
      });
    });
  }

  function renderOne(el) {
    if (!isCandidate(el)) return false;

    var raw = (el.innerText || el.textContent || "").trim();
    if (!raw) return false;

    rendered.add(el);
    if (el.dataset) {
      el.dataset.n63Rendered = "1";
      el.dataset.n63RawLen = String(raw.length);
    }

    el.innerHTML = renderMarkdown(raw);
    installCopyHandlers(el);
    return true;
  }

  function scan(root) {
    if (rendering) return;
    var candidates = findCandidates(root || document.body);
    if (!candidates.length) return;

    rendering = true;
    try {
      if (observer) observer.disconnect();
      candidates.forEach(renderOne);
    } catch (e) {
      console.error("[N63 markdown renderer] render failed:", e);
    } finally {
      rendering = false;
      reconnectObserver();
    }
  }

  function schedule(root) {
    if (rendering || scheduled) return;
    scheduled = true;
    window.requestAnimationFrame(function () {
      scheduled = false;
      scan(root || document.body);
    });
  }

  function reconnectObserver() {
    if (!document.body) return;
    if (!observer) {
      observer = new MutationObserver(function (mutations) {
        if (rendering) return;

        var shouldScan = false;
        for (var i = 0; i < mutations.length; i++) {
          var m = mutations[i];
          if (m.type !== "childList") continue;
          for (var j = 0; j < m.addedNodes.length; j++) {
            var n = m.addedNodes[j];
            if (n.nodeType === Node.ELEMENT_NODE || n.nodeType === Node.TEXT_NODE) {
              shouldScan = true;
              break;
            }
          }
          if (shouldScan) break;
        }

        if (shouldScan) schedule(document.body);
      });
    }

    try {
      observer.observe(document.body, {
        childList: true,
        subtree: true
      });
    } catch (e) {
      console.error("[N63 markdown renderer] observer failed:", e);
    }
  }

  function addStyle() {
    if (document.getElementById("n63-safe-md-style")) return;
    var css = ''
      + '.n63-md{font-size:14px;line-height:1.65;color:#111827;word-break:break-word;}'
      + '.n63-md p{margin:.45em 0;}'
      + '.n63-md h3,.n63-md h4{margin:.85em 0 .4em;font-weight:650;line-height:1.35;}'
      + '.n63-md h3{font-size:16px;}'
      + '.n63-md h4{font-size:15px;}'
      + '.n63-md ul,.n63-md ol{margin:.4em 0 .6em 1.4em;padding:0;}'
      + '.n63-md li{margin:.25em 0;}'
      + '.n63-inline-code{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;background:#f4f4f5;border:1px solid #e4e4e7;border-radius:4px;padding:1px 4px;}'
      + '.n63-code-wrap{position:relative;margin:.75em 0;}'
      + '.n63-code-wrap pre{margin:0;padding:12px;overflow:auto;border:1px solid #e5e7eb;border-radius:8px;background:#f8fafc;}'
      + '.n63-code-wrap code{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:13px;}'
      + '.n63-copy-btn{position:absolute;right:8px;top:8px;font-size:12px;border:1px solid #d4d4d8;background:#fff;border-radius:6px;padding:2px 8px;cursor:pointer;}'
      + '.n63-table-wrap{overflow:auto;margin:.75em 0;}'
      + '.n63-table{border-collapse:collapse;width:100%;font-size:13px;}'
      + '.n63-table th,.n63-table td{border:1px solid #e5e7eb;padding:6px 8px;text-align:left;vertical-align:top;}'
      + '.n63-table th{background:#f8fafc;font-weight:650;}';

    var style = document.createElement("style");
    style.id = "n63-safe-md-style";
    style.textContent = css;
    document.head.appendChild(style);
  }

  function boot() {
    addStyle();
    schedule(document.body);
    reconnectObserver();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot, { once: true });
  } else {
    boot();
  }
})();
