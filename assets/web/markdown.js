/*
 * markdown.js —— 最小且安全的 Markdown 渲染器（编辑器实时预览用）
 * ================================================================
 *
 * 【为什么不引第三方库】
 * 页面 CSP 是 `script-src 'self'`，所有脚本必须是项目自带文件；而常用的
 * marked/markdown-it 体积不小。日程备注需要的语法很少，自己实现一个反而更小。
 *
 * 【安全约束】
 * 输入先整体转义 HTML，再套 Markdown 规则，因此任何原始 HTML/脚本都不可能
 * 进入输出；链接只放行 http/https/mailto，其余协议的 href 被丢弃。
 * 这使得返回值可以安全地赋给 innerHTML。
 *
 * 【支持的语法】
 *   # ～ #### 标题、**粗体**、*斜体*、~~删除线~~、`行内代码`、``` 代码块 ```、
 *   - 或 * 无序列表、1. 有序列表、> 引用、--- 分隔线、[文字](链接)、段落与换行。
 *
 * 同时以 window.NanqiMarkdown（页面）与 module.exports（node --test）导出。
 */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.NanqiMarkdown = factory();
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';

  function escapeHtml(text) {
    return String(text == null ? '' : text).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  function safeHref(url) {
    var trimmed = String(url || '').trim();
    return /^(https?:|mailto:)/i.test(trimmed) ? trimmed : '';
  }

  // 行内语法（输入必须是已转义的文本）。行内代码先摘出来存进 stash，
  // 用 \u0001 序号 \u0002 占位（正常文本不会出现这两个控制字符），最后再还原。
  function inline(escaped) {
    var stash = [];
    escaped = escaped.replace(/`([^`\n]+)`/g, function (_m, code) {
      stash.push('<code>' + code + '</code>');
      return '\u0001' + (stash.length - 1) + '\u0002';
    });
    escaped = escaped
      .replace(/\[([^\]\n]+)\]\(((?:[^()\s]|\([^()\s]*\))+)\)/g, function (_m, text, url) {
        var href = safeHref(url);
        return href ? '<a href="' + href + '" target="_blank" rel="noreferrer">' + text + '</a>' : text;
      })
      .replace(/\*\*([^*\n]+)\*\*/g, '<strong>$1</strong>')
      .replace(/(^|[^*])\*([^*\n]+)\*/g, '$1<em>$2</em>')
      .replace(/~~([^~\n]+)~~/g, '<del>$1</del>');
    return escaped.replace(/\u0001(\d+)\u0002/g, function (_m, i) { return stash[+i]; });
  }

  function render(source) {
    var lines = String(source == null ? '' : source).replace(/\r\n?/g, '\n').split('\n');
    var html = [];
    var paragraph = [];
    var listTag = null;   // 'ul' | 'ol' | null
    var listItems = [];
    var quote = [];
    var inCode = false;
    var codeLines = [];

    function flushParagraph() {
      if (paragraph.length) {
        html.push('<p>' + paragraph.map(inline).join('<br>') + '</p>');
        paragraph = [];
      }
    }
    function flushList() {
      if (listTag) {
        html.push('<' + listTag + '>' + listItems.map(function (item) {
          return '<li>' + inline(item) + '</li>';
        }).join('') + '</' + listTag + '>');
        listTag = null;
        listItems = [];
      }
    }
    function flushQuote() {
      if (quote.length) {
        html.push('<blockquote>' + quote.map(inline).join('<br>') + '</blockquote>');
        quote = [];
      }
    }
    function flushAll() { flushParagraph(); flushList(); flushQuote(); }
    function openList(tag) {
      if (listTag !== tag) { flushList(); listTag = tag; }
    }

    for (var i = 0; i < lines.length; i++) {
      var line = lines[i];
      if (/^\s*```/.test(line)) {
        if (inCode) {
          html.push('<pre><code>' + codeLines.join('\n') + '</code></pre>');
          codeLines = [];
          inCode = false;
        } else {
          flushAll();
          inCode = true;
        }
        continue;
      }
      if (inCode) { codeLines.push(escapeHtml(line)); continue; }

      var match;
      if ((match = /^\s{0,3}(#{1,4})\s+(.*)$/.exec(line))) {
        flushAll();
        html.push('<h' + match[1].length + '>' + inline(escapeHtml(match[2])) + '</h' + match[1].length + '>');
      } else if (/^\s*(-{3,}|\*{3,})\s*$/.test(line)) {
        flushAll();
        html.push('<hr>');
      } else if ((match = /^\s*[-*]\s+(.*)$/.exec(line))) {
        flushParagraph(); flushQuote();
        openList('ul');
        listItems.push(escapeHtml(match[1]));
      } else if ((match = /^\s*\d+(?:\.\s+|、\s?)(.*)$/.exec(line))) {
        flushParagraph(); flushQuote();
        openList('ol');
        listItems.push(escapeHtml(match[1]));
      } else if ((match = /^\s*>\s?(.*)$/.exec(line))) {
        flushParagraph(); flushList();
        quote.push(escapeHtml(match[1]));
      } else if (line.trim() === '') {
        flushAll();
      } else {
        flushList(); flushQuote();
        paragraph.push(escapeHtml(line));
      }
    }
    if (inCode) html.push('<pre><code>' + codeLines.join('\n') + '</code></pre>');
    flushAll();
    return html.join('\n');
  }

  return { render: render, escapeHtml: escapeHtml };
});
