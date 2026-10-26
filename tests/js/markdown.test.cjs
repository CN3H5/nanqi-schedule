/*
 * markdown.js 的 node --test 测试。
 * 运行：node --test tests/js/
 */
'use strict';

const { test } = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');

const md = require(path.join(__dirname, '..', '..', 'assets', 'web', 'markdown.js'));

test('标题 h1-h4', () => {
  assert.equal(md.render('# 大标题'), '<h1>大标题</h1>');
  assert.equal(md.render('### 小标题'), '<h3>小标题</h3>');
  assert.equal(md.render('##### 五级不渲染'), '<p>##### 五级不渲染</p>');
});

test('加粗/斜体/删除线', () => {
  assert.equal(md.render('**重点**'), '<p><strong>重点</strong></p>');
  assert.equal(md.render('*斜体*'), '<p><em>斜体</em></p>');
  assert.equal(md.render('~~删掉~~'), '<p><del>删掉</del></p>');
});

test('行内代码不会被再次解析，且 HTML 转义', () => {
  assert.equal(md.render('用 `**not bold**` 表示'), '<p>用 <code>**not bold**</code> 表示</p>');
  assert.equal(md.render('`<b>x</b>`'), '<p><code>&lt;b&gt;x&lt;/b&gt;</code></p>');
});

test('代码块', () => {
  const out = md.render('```\nif (a < b) {}\n```');
  assert.equal(out, '<pre><code>if (a &lt; b) {}</code></pre>');
});

test('列表：无序、有序、中文顿号编号', () => {
  assert.equal(md.render('- 一\n- 二'), '<ul><li>一</li><li>二</li></ul>');
  assert.equal(md.render('1. 一\n2. 二'), '<ol><li>一</li><li>二</li></ol>');
  assert.equal(md.render('1、一\n2、二'), '<ol><li>一</li><li>二</li></ol>');
});

test('引用与分割线', () => {
  assert.equal(md.render('> 引用内容'), '<blockquote>引用内容</blockquote>');
  assert.equal(md.render('---'), '<hr>');
});

test('段落内的换行变成 <br>', () => {
  assert.equal(md.render('第一行\n第二行'), '<p>第一行<br>第二行</p>');
});

test('链接：http/https/mailto 放行，其余协议降级为纯文本', () => {
  assert.equal(
    md.render('[官网](https://example.com)'),
    '<p><a href="https://example.com" target="_blank" rel="noreferrer">官网</a></p>'
  );
  const unsafe = md.render('[x](javascript:alert(1))');
  assert.ok(!unsafe.includes('href'), '不允许的协议不得生成链接');
  assert.ok(unsafe.includes('x'));
  // 括号配对的 URL 不多吃字符
  assert.equal(
    md.render('[x](javascript:alert(1)) 完 [ok](https://a.b)'),
    '<p>x 完 <a href="https://a.b" target="_blank" rel="noreferrer">ok</a></p>'
  );
});

test('输入的 HTML 一律转义（XSS 防线）', () => {
  assert.equal(md.render('<script>alert(1)</script>'), '<p>&lt;script&gt;alert(1)&lt;/script&gt;</p>');
  assert.equal(md.render('<img src=x onerror=alert(1)>'), '<p>&lt;img src=x onerror=alert(1)&gt;</p>');
});

test('空输入', () => {
  assert.equal(md.render(''), '');
  assert.equal(md.render(null), '');
  assert.equal(md.render(undefined), '');
});

test('数字混排不误判为列表以外的结构', () => {
  assert.equal(md.render('纯文本 3 点 `x` 完'), '<p>纯文本 3 点 <code>x</code> 完</p>');
});
