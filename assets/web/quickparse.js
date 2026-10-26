/*
 * quickparse.js —— 计划输入的自然语言解析（中文）
 * ================================================================
 *
 * 【它做什么】
 * 把「明天下午3点到5点 组会 #项目 @工作 每周 提前10分钟」这样的第一行拆成
 * 结构化字段：日期 / 开始时间 / 结束时间 / 重复 / 提醒 / 标签 / 分类 / 优先级 /
 * 类型（日程/计划/复习）。第二行起是备注（Markdown），原样保留。
 *
 * 【约定】
 *   - 标签：#标签（可多个）；分类：@分类名（匹配现有分类的名称或 id）；
 *   - 优先级：「!!」或「！重要」；
 *   - 没有 上午/下午 修饰的 1~6 点按下午计（中文口语习惯），7~11 点按上午计；
 *     需要精确时用 24 小时制（15:00）或加上 上午/下午；
 *   - 每周/每天重复可通过「连续N周/天」给出结束范围；
 *   - 「复习」「备考」会去掉并标记为复习计划；「考试」只标记、保留在标题里。
 *
 * 解析不抛错：认不出的片段留在标题里，由用户决定。
 * 同时以 window.NanqiQuick（页面）与 module.exports（node --test）导出。
 */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.NanqiQuick = factory();
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';

  var WEEKDAYS = { '一': 1, '二': 2, '三': 3, '四': 4, '五': 5, '六': 6, '日': 0, '天': 0 };
  var ALLOWED_REMINDERS = [-1, 0, 5, 10, 15, 30, 60, 1440];

  function pad2(n) { return (n < 10 ? '0' : '') + n; }
  function dateKey(d) { return d.getFullYear() + '-' + pad2(d.getMonth() + 1) + '-' + pad2(d.getDate()); }
  function addDays(d, n) { var x = new Date(d); x.setDate(x.getDate() + n); return x; }
  function minutesOf(t) { return +t.slice(0, 2) * 60 + +t.slice(3); }
  function timeOf(minutes) { return pad2(Math.floor(minutes / 60)) + ':' + pad2(minutes % 60); }

  // 无修饰词的小时换算：1~6 → 下午（13~18 点），7~11 → 上午，12 → 12 点。
  function applyModifier(hour, modifier) {
    if (modifier === '下午' || modifier === '傍晚' || modifier === '晚上') return hour < 12 ? hour + 12 : hour;
    if (modifier === '中午') return hour < 6 ? hour + 12 : hour;
    if (modifier === '凌晨') return hour < 12 ? hour : 0;
    if (modifier === '上午' || modifier === '早上' || modifier === '早晨') return hour === 12 ? 0 : hour;
    return hour <= 6 ? hour + 12 : hour;
  }

  function nearestReminder(minutes) {
    var best = -1, bestGap = Infinity;
    ALLOWED_REMINDERS.forEach(function (value) {
      var gap = Math.abs(value - minutes);
      if (gap < bestGap) { bestGap = gap; best = value; }
    });
    return best;
  }

  /**
   * 解析输入文本。options: { now: Date, categories: [{id, name}] }
   * 返回 { title, notes, date, time, endTime, repeat, reminder, priority, tags,
   *        category, kind, detected:{date,time,endTime,repeat,reminder,priority,tags,category,kind} }
   * 未识别出的字段为 null（reminder 除外，未提及时也是 null，由调用方填默认值）。
   */
  function parse(text, options) {
    options = options || {};
    var now = options.now || new Date();
    var today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
    var categories = options.categories || [];

    var lines = String(text == null ? '' : text).replace(/\r\n?/g, '\n').split('\n');
    var first = lines[0] || '';
    var notes = lines.slice(1).join('\n').replace(/^\s+/, '');

    var result = {
      title: '', notes: notes,
      date: null, time: null, endTime: null,
      repeat: null, reminder: null, priority: null,
      tags: [], category: null, kind: null,
      detected: {},
    };
    var found = result.detected;

    function take(re, handler) {
      var match = re.exec(first);
      if (!match) return false;
      first = (first.slice(0, match.index) + ' ' + first.slice(match.index + match[0].length));
      handler(match);
      return true;
    }

    // ── 标签 #tag（可多个）────────────────────────────────────────────
    var tagRe = /#([^\s#，。,；;@]{1,20})/g, tagMatch;
    while ((tagMatch = tagRe.exec(first))) {
      if (result.tags.indexOf(tagMatch[1]) < 0) result.tags.push(tagMatch[1]);
    }
    if (result.tags.length) {
      first = first.replace(/#[^\s#，。,；;@]{1,20}/g, ' ');
      found.tags = true;
    }

    // ── 分类 @名称 ────────────────────────────────────────────────────
    take(/@([^\s@#]{1,12})/, function (m) {
      var want = m[1].toLowerCase();
      categories.forEach(function (c) {
        var names = [c.id, c.name].concat(String(c.name || '').split('/').map(function (s) { return s.trim(); }));
        names.forEach(function (n) {
          if (n && String(n).toLowerCase() === want) result.category = c.id;
        });
      });
      if (result.category) found.category = true;
    });

    // ── 优先级 ────────────────────────────────────────────────────────
    take(/(?:!!|！重要|！!)/, function () { result.priority = 'high'; found.priority = true; });

    // ── 提醒 ──────────────────────────────────────────────────────────
    take(/不提醒/, function () { result.reminder = -1; found.reminder = true; });
    if (!found.reminder) take(/(?:准时|到时|开始时)提醒/, function () { result.reminder = 0; found.reminder = true; });
    if (!found.reminder) take(/提前\s*(\d{1,4})\s*(分钟|分|个小时|小时|天)(?:提醒)?/, function (m) {
      var n = +m[1];
      var minutes = m[2] === '天' ? n * 1440 : (m[2] === '小时' || m[2] === '个小时') ? n * 60 : n;
      result.reminder = nearestReminder(minutes);
      found.reminder = true;
    });

    // ── 类型（复习计划）───────────────────────────────────────────────
    take(/复习|备考/, function () { result.kind = 'review'; found.kind = true; });
    if (!found.kind && /考试|期中|期末/.test(first)) { result.kind = 'review'; found.kind = true; }

    // ── 日期 ──────────────────────────────────────────────────────────
    var weekdayPinned = null;
    take(/大后天/, function () { result.date = dateKey(addDays(today, 3)); found.date = true; });
    if (!found.date) take(/后天/, function () { result.date = dateKey(addDays(today, 2)); found.date = true; });
    if (!found.date) take(/明天|明日/, function () { result.date = dateKey(addDays(today, 1)); found.date = true; });
    if (!found.date) take(/今天|今日/, function () { result.date = dateKey(today); found.date = true; });
    if (!found.date) take(/(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*[日号]?/, function (m) {
      result.date = m[1] + '-' + pad2(+m[2]) + '-' + pad2(+m[3]); found.date = true;
    });
    if (!found.date) take(/(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})/, function (m) {
      result.date = m[1] + '-' + pad2(+m[2]) + '-' + pad2(+m[3]); found.date = true;
    });
    if (!found.date) take(/(\d{1,2})\s*月\s*(\d{1,2})\s*[日号]/, function (m) {
      var candidate = new Date(today.getFullYear(), +m[1] - 1, +m[2]);
      if (candidate < today) candidate = new Date(today.getFullYear() + 1, +m[1] - 1, +m[2]);
      result.date = dateKey(candidate); found.date = true;
    });
    // (?<!每) 防止把「每周三」里的「周三」当日期吃掉——那是重复的表达
    if (!found.date) take(/(?<!每)(下下|下|本|这)?\s*(?:周|星期)([一二三四五六日天])/, function (m) {
      var target = WEEKDAYS[m[2]];
      var delta = (target - today.getDay() + 7) % 7;
      if (m[1] === '下') delta = ((target - today.getDay() + 7) % 7) + 7;
      else if (m[1] === '下下') delta = ((target - today.getDay() + 7) % 7) + 14;
      result.date = dateKey(addDays(today, delta));
      found.date = true;
    });

    // ── 时间段（先范围后单点）─────────────────────────────────────────
    var MOD = '(凌晨|早上|早晨|上午|中午|下午|傍晚|晚上)';
    var rangeRe = new RegExp(
      MOD + '?\\s*(\\d{1,2})\\s*(?:[:：.]|点)\\s*(\\d{1,2}|半)?\\s*分?\\s*(?:到|至|—|–|~|～|-)\\s*' +
      MOD + '?\\s*(\\d{1,2})\\s*(?:[:：.]|点)\\s*(\\d{1,2}|半)?\\s*分?'
    );
    var rangeMatch = rangeRe.exec(first);
    if (rangeMatch) {
      var startHour = +rangeMatch[2];
      var startMin = rangeMatch[3] === '半' ? 30 : (rangeMatch[3] ? +rangeMatch[3] : 0);
      var endHour = +rangeMatch[5];
      var endMin = rangeMatch[6] === '半' ? 30 : (rangeMatch[6] ? +rangeMatch[6] : 0);
      var startMod = rangeMatch[1] || null;
      var endMod = rangeMatch[4] || startMod;
      startHour = applyModifier(startHour, startMod);
      endHour = applyModifier(endHour, endMod);
      if (endHour * 60 + endMin <= startHour * 60 + startMin) endHour += 12;
      if (startHour <= 23 && endHour * 60 + endMin <= 24 * 60 + 12 * 60) {
        result.time = timeOf(startHour * 60 + startMin);
        result.endTime = timeOf(Math.min(endHour * 60 + endMin, 23 * 60 + 59));
        found.time = true; found.endTime = true;
        first = first.slice(0, rangeMatch.index) + ' ' + first.slice(rangeMatch.index + rangeMatch[0].length);
      }
    }
    if (!found.time) {
      var singleRe = new RegExp(MOD + '?\\s*(\\d{1,2})\\s*(?:[:：.]|点)\\s*(\\d{1,2}|半)?\\s*分?');
      var singleMatch = singleRe.exec(first);
      if (singleMatch) {
        var hour = +singleMatch[2];
        var minute = singleMatch[3] === '半' ? 30 : (singleMatch[3] ? +singleMatch[3] : 0);
        hour = applyModifier(hour, singleMatch[1] || null);
        if (hour <= 23 && minute <= 59) {
          result.time = timeOf(hour * 60 + minute);
          found.time = true;
          first = first.slice(0, singleMatch.index) + ' ' + first.slice(singleMatch.index + singleMatch[0].length);
        }
      }
    }

    // ── 重复 ──────────────────────────────────────────────────────────
    var repeatWeeks = null, repeatDays = null;
    take(/连续\s*(\d{1,2})\s*周/, function (m) { repeatWeeks = +m[1]; });
    take(/连续\s*(\d{1,3})\s*天/, function (m) { repeatDays = +m[1]; });
    take(/每个?工作日|工作日每天/, function () {
      result.repeat = { frequency: 'weekdays' }; found.repeat = true;
    });
    if (!found.repeat) take(/每天|每日|天天/, function () {
      result.repeat = { frequency: 'daily' }; found.repeat = true;
    });
    if (!found.repeat) take(/每周(?:[一二三四五六日天])?/, function (m) {
      result.repeat = { frequency: 'weekly' }; found.repeat = true;
      var w = /每周([一二三四五六日天])/.exec(m[0]);
      if (w && !found.date) {
        var target = WEEKDAYS[w[1]];
        result.date = dateKey(addDays(today, (target - today.getDay() + 7) % 7));
        found.date = true;
      }
    });
    if (result.repeat) {
      var base = result.date ? new Date(result.date + 'T12:00:00') : today;
      if (result.repeat.frequency === 'weekly') {
        var weeks = repeatWeeks || 12;
        result.repeat.until = dateKey(addDays(base, (weeks - 1) * 7));
      } else if (result.repeat.frequency === 'daily' && repeatDays) {
        result.repeat.until = dateKey(addDays(base, repeatDays - 1));
      } else if (result.repeat.frequency === 'daily') {
        result.repeat.until = dateKey(addDays(base, 29));
      } else if (result.repeat.frequency === 'weekdays') {
        result.repeat.until = dateKey(addDays(base, 29));
      }
    }

    result.title = first.replace(/\s+/g, ' ').trim();
    return result;
  }

  return { parse: parse, applyModifier: applyModifier, nearestReminder: nearestReminder };
});
