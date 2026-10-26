/*
 * bridge.js —— 把页面接到 Python 上（替代 Electron 的 preload.cjs）
 * ================================================================
 *
 * 【它替代了什么】
 * Electron 版里，preload 脚本用 contextBridge 往页面注入了一个 `window.nanqi`，
 * 里面每个方法都是 `ipcRenderer.invoke('xxx', ...)`。三个页面的脚本
 * （renderer.js / widget.js / reminder.js / updates.js / task-actions.js）
 * 全部直接调用 `window.nanqi.*`，所以这里必须**提供同名同形的方法**，
 * 才能让那些文件一个字都不用改。
 *
 * 【为什么必须是外部文件】
 * 三个 HTML 都带内容安全策略 `script-src 'self'`，内联 <script> 会被浏览器拒绝；
 * 因此连接逻辑放在这个外部文件里。qwebchannel.js 由 Python 在
 * DocumentCreation 时注入（注入脚本不受页面 CSP 约束）。
 *
 * 【协议】
 * Python 侧只暴露一个方法 `bridge.invoke(name, argsJson, callback)`：
 *   - 调用成功 → 回调收到 {"ok": true, "value": ...}
 *   - 调用失败 → 回调收到 {"ok": false, "error": "中文提示"}
 * 这里把失败还原成 `throw new Error(提示)`，于是原来的 `catch(e){toast(e.message)}`
 * 逻辑照常工作（和 ipcRenderer.invoke 被 reject 时的行为一致）。
 * 状态推送用信号：`bridge.stateChanged` / `bridge.appError`，主窗口还额外有
 * `main.newTask` / `main.editTask`（只送主窗口，不广播）。
 */
(function () {
  'use strict';

  var handlers = { state: [], error: [], newTask: [], editTask: [] };
  var bridgeObject = null;
  var resolveReady;
  var ready = new Promise(function (resolve) { resolveReady = resolve; });

  function emit(kind, payload) {
    handlers[kind].forEach(function (fn) {
      try { fn(payload); } catch (error) { console.error(error); if(kind!=='error')emit('error','界面更新失败，请重试'); }
    });
  }

  function call(name, args) {
    return ready.then(function () {
      return new Promise(function (resolve, reject) {
        var timer=setTimeout(function(){reject(new Error("应用通信超时，请重试"));},15000);
        bridgeObject.invoke(name, JSON.stringify(args || []), function (raw) {
          clearTimeout(timer);
          var envelope;
          try {
            envelope = JSON.parse(raw);
          } catch (error) {
            reject(new Error('与应用的通信失败，请重启南七日程'));
            return;
          }
          if (envelope && envelope.ok) resolve(envelope.value);
          else reject(new Error((envelope && envelope.error) || '操作失败'));
        });
      });
    });
  }

  // 与 preload.cjs 一一对应的接口清单。
  var api = {
    get: function () { return call('get'); },
    saveTask: function (task, repeat, examDate) { return call('save-task', [task, repeat, examDate || null]); },
    previewTaskAction: function (id, action) { return call('preview-task-action', [id, action]); },
    applyTaskAction: function (request) { return call('apply-task-action', [request]); },
    moveTask: function (id, date, time) { return call('move-task', [id, date, time]); },
    reminderAction: function (id, action) { return call('reminder-action', [id, action]); },
    settings: function (settings) { return call('settings', [settings]); },
    testReminder: function () { return call('test-reminder'); },
    nextReminderMessage: function () { return call('next-reminder-message'); },
    exportData: function () { return call('export'); },
    importData: function () { return call('import'); },
    openData: function () { return call('open-data'); },
    openWidget: function () { return call('open-widget'); },
    closeWidget: function () { return call('close-widget'); },
    widgetSettings: function (patch) { return call('widget-settings', [patch]); },
    editTask: function (id) { return call('edit-task', [id]); },
    showMain: function () { return call('show-main'); },
    quit: function () { return call('quit'); },
    // 本版本新增：打开内置浏览器（移植自 ustc-assistant 的 QtWebEngine 浏览器）。
    openBrowser: function (url) { return call('open-browser', [url || '']); },
  openMail: function () { return call('open-mail'); },
    // 「一键添加」：kind = schedule / ercourse / exam（选择类型 → 打开对应网页）。
    importFrom: function (kind) { return call('import-from', [kind || 'schedule']); },
    // 课程整体平移 N 天（对齐真实教学周）。
    shiftCourses: function (days) { return call('shift-courses', [days]); },
    setDragRegions: function (regions) { return call('set-drag-regions', [regions]); },
    // 自定义分类 / 附件 / 课表导入（v1.1 新增）。
    saveCategory: function (category) { return call('save-category', [category]); },
    deleteCategory: function (id) { return call('delete-category', [id]); },
    pickAttachments: function () { return call('pick-attachments'); },
    openAttachment: function (path) { return call('open-attachment', [path]); },
    // 事件订阅（与 preload 的 onState/onNew/onEditTask/onError 同名同义）。
    onState: function (fn) { handlers.state.push(fn); },
    onNew: function (fn) { handlers.newTask.push(fn); },
    onEditTask: function (fn) { handlers.editTask.push(fn); },
    onError: function (fn) { handlers.error.push(fn); }
  };
  window.nanqi = api;

  function wireExtras() {
    // 1) 主界面右上角的「浏览器」按钮（index.html），打开内置浏览器窗口。
    var browserButton = document.getElementById('open-browser');
    if (browserButton && !browserButton.dataset.bound) {
      browserButton.dataset.bound = '1';
      browserButton.addEventListener('click', function () { api.openBrowser('').catch(function(e){emit('error',e.message);}); });
    }

    // 2) 桌面便签：把「标题栏可拖动区域」和「按钮等不可拖动区域」上报给 Python。
    //    Qt 侧据此在 WM_NCHITTEST 里回 HTCAPTION / HTCLIENT，
    //    从而得到与普通窗口一致的拖动体验，而标题栏里的按钮仍然可以点击。
    var chrome = document.querySelector('.widget-chrome');
    if (chrome && !chrome.dataset.bound) {
      chrome.dataset.bound = '1';
      var report = function () {
        var drag = [];
        var noDrag = [];
        var box = chrome.getBoundingClientRect();
        if (box.width > 0 && box.height > 0) drag.push([box.left, box.top, box.width, box.height]);
        var protectedNodes = document.querySelectorAll(
          '.widget-chrome button, .widget-chrome input, .widget-chrome select, .widget-chrome .chrome-actions'
        );
        Array.prototype.forEach.call(protectedNodes, function (node) {
          var rect = node.getBoundingClientRect();
          if (rect.width > 0 && rect.height > 0) {
            noDrag.push([rect.left, rect.top, rect.width, rect.height]);
          }
        });
        api.setDragRegions({ drag: drag, noDrag: noDrag }).catch(function () { /* 忽略 */ });
      };
      report();
      var timer;
      window.addEventListener('resize', function () {
        clearTimeout(timer);
        timer = setTimeout(report, 60);
      });
      // 便签内容重绘会改变标题栏位置时也刷新一次。
      setInterval(report, 2000);
    }
  }

  function connect() {
    if (typeof QWebChannel === 'undefined' || !window.qt || !qt.webChannelTransport) {
      // 通道还没就绪（注入脚本尚未执行）：短暂等待后重试。
      setTimeout(connect, 30);
      return;
    }
    new QWebChannel(qt.webChannelTransport, function (channel) {
      bridgeObject = channel.objects.bridge;
      if (!bridgeObject) {
        setTimeout(connect, 60);
        return;
      }
      bridgeObject.stateChanged.connect(function (payload) {
        var snapshot;
        try { snapshot = JSON.parse(payload); } catch (error) { return; }
        emit('state', snapshot);
      });
      bridgeObject.appError.connect(function (message) { emit('error', message); });

      var main = channel.objects.main;
      if (main) {
        main.newTask.connect(function (date) { emit('newTask', date); });
        main.editTask.connect(function (id) { emit('editTask', id); });
      }
      resolveReady();
      wireExtras();
    });
  }

  connect();
})();
