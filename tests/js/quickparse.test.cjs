/*
 * quickparse 的 node --test 测试。
 * 运行：node --test tests/js/
 */
'use strict';

const { test } = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');

const NanqiQuick = require(path.join(__dirname, '..', '..', 'assets', 'web', 'quickparse.js'));

const NOW = new Date(2026, 9, 19, 12, 0, 0); // 2026-10-19 周一 中午
const CATS = [
  { id: 'work', name: '工作' },
  { id: 'study', name: '学习' },
  { id: 'life', name: '生活' },
];

function parse(text) {
  return NanqiQuick.parse(text, { now: NOW, categories: CATS });
}

test('完整的一句话：日期+时间段+标签+分类+重复+提醒', () => {
  const r = parse('明天下午3点到5点 组会 #项目 @工作 每周 提前10分钟');
  assert.equal(r.date, '2026-10-20');
  assert.equal(r.time, '15:00');
  assert.equal(r.endTime, '17:00');
  assert.deepEqual(r.tags, ['项目']);
  assert.equal(r.category, 'work');
  assert.equal(r.repeat.frequency, 'weekly');
  assert.equal(r.reminder, 10);
  assert.equal(r.title, '组会');
});

test('无修饰的 1~6 点按下午计，7~11 按上午计', () => {
  assert.equal(parse('3点开会').time, '15:00');
  assert.equal(parse('9点晨跑').time, '09:00');
  assert.equal(parse('中午1点吃饭').time, '13:00');
  assert.equal(parse('晚上11点半睡觉').time, '23:30');
  assert.equal(parse('早上6点起床').time, '06:00');
});

test('时间段：上午9:30-11:00 与跨午夜的纠正', () => {
  const a = parse('上午9:30到11:00 讨论');
  assert.equal(a.time, '09:30');
  assert.equal(a.endTime, '11:00');
  const b = parse('晚上8点~10点 健身');
  assert.equal(b.time, '20:00');
  assert.equal(b.endTime, '22:00');
});

test('日期：今天/后天/大后天/周X/下周X/M月D日/ISO', () => {
  assert.equal(parse('今天交作业').date, '2026-10-19');
  assert.equal(parse('后天交作业').date, '2026-10-21');
  assert.equal(parse('大后天交作业').date, '2026-10-22');
  assert.equal(parse('周三例会').date, '2026-10-21'); // 周一说"周三"=本周三
  assert.equal(parse('下周一例会').date, '2026-10-26');
  assert.equal(parse('下下周三例会').date, '2026-11-04');
  assert.equal(parse('11月5日考试').date, '2026-11-05');
  assert.equal(parse('2026-12-31 跨年').date, '2026-12-31');
  // 已过去的 M月D日 滚到明年
  assert.equal(parse('1月1日元旦').date, '2027-01-01');
});

test('重复：每天/工作日/每周X 与 连续N周', () => {
  const daily = parse('每天背20个单词');
  assert.equal(daily.repeat.frequency, 'daily');
  assert.equal(daily.repeat.until, '2026-11-17'); // 默认 30 天

  const weekdays = parse('每个工作日站会 9:30');
  assert.equal(weekdays.repeat.frequency, 'weekdays');

  const pinned = parse('每周三羽毛球');
  assert.equal(pinned.repeat.frequency, 'weekly');
  assert.equal(pinned.date, '2026-10-21'); // 自动定到最近的周三

  const limited = parse('每周五 组会 连续16周');
  assert.equal(limited.repeat.frequency, 'weekly');
  // 2026-10-23（周五）起 16 周 → 最后一天 = 起日 + 15*7
  assert.equal(limited.repeat.until, '2027-02-05');
});

test('提醒：不提醒/准时/提前N分钟小时天', () => {
  assert.equal(parse('不提醒 随便记').reminder, -1);
  assert.equal(parse('准时提醒 抢票').reminder, 0);
  assert.equal(parse('提前15分钟提醒 出门').reminder, 15);
  assert.equal(parse('提前1小时 高铁').reminder, 60);
  assert.equal(parse('提前2天 缴费').reminder, 1440); // 2880 → 最近的可选项是 1440
});

test('优先级 !! 与 ！重要', () => {
  assert.equal(parse('交报告!!').priority, 'high');
  assert.equal(parse('！重要 答辩').priority, 'high');
  assert.equal(parse('普通任务').priority, null);
});

test('类型：复习/备考 → review；考试 保留在标题里', () => {
  const a = parse('明天开始复习数学分析');
  assert.equal(a.kind, 'review');
  // “复习”被剥掉，“明天”被剥掉，标题保留内容
  assert.ok(!a.title.includes('复习'));
  assert.ok(a.title.includes('数学分析'));

  const b = parse('期末考试 12月28日');
  assert.equal(b.kind, 'review');
  assert.ok(b.title.includes('期末考试'));
});

test('标签：多个、去重、中文与英文', () => {
  const r = parse('写周报 #工作 #project #工作');
  assert.deepEqual(r.tags, ['工作', 'project']);
});

test('分类：按 id 或名称匹配，认不出就不剥（保留 @原文 之外的标题）', () => {
  assert.equal(parse('健身 @生活').category, 'life');
  assert.equal(parse('健身 @life').category, 'life');
  const unknown = parse('健身 @不存在的分类');
  assert.equal(unknown.category, null);
});

test('第二行起是备注，原样保留', () => {
  const r = parse('明天 组会\n- 议题一\n- 议题二');
  assert.equal(r.notes, '- 议题一\n- 议题二');
});

test('认不出的片段留在标题里，不抛错', () => {
  const r = parse('和甲方那个啥对接一下');
  assert.equal(r.title, '和甲方那个啥对接一下');
  assert.equal(r.date, null);
  assert.equal(r.time, null);
});

test('空输入安全返回', () => {
  const r = parse('');
  assert.equal(r.title, '');
  assert.equal(r.date, null);
});

test('applyModifier 边界', () => {
  assert.equal(NanqiQuick.applyModifier(12, '上午'), 0);
  assert.equal(NanqiQuick.applyModifier(12, '下午'), 12);
  assert.equal(NanqiQuick.applyModifier(1, '凌晨'), 1);
  assert.equal(NanqiQuick.applyModifier(7, null), 7);
  assert.equal(NanqiQuick.applyModifier(6, null), 18);
});

test('nearestReminder 就近取允许值（等距取小）', () => {
  assert.equal(NanqiQuick.nearestReminder(12), 10);
  assert.equal(NanqiQuick.nearestReminder(45), 30); // 与 30/60 等距，取先遍历到的小值
  assert.equal(NanqiQuick.nearestReminder(50), 60);
  assert.equal(NanqiQuick.nearestReminder(2000), 1440);
});
