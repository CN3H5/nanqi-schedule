# 南七日程 v2.0.0

Windows 桌面日历、待办、桌面便签、提醒、内置浏览器和基础邮件客户端。技术栈为 Python + PySide6/QtWebEngine，日程保存在本机 JSON 文件。

## 下载

在 [GitHub Releases](https://github.com/CN3H5/nanqi-schedule/releases) 获取发布文件：

- **文件夹版（推荐）**：下载 `nanqi_schedule-v2.0.0-windows-x64.zip`，完整解压后运行 `nanqi_schedule.exe`。
- **单文件版**：下载 `nanqi_schedule.exe`，直接运行，首次启动需要解压运行库。
- **源码**：克隆本仓库，或下载 `nanqi_schedule-v2.0.0-source.zip`。
- 可使用同一发布页的 `SHA256SUMS.txt` 校验文件。

## 版本更新历史

### v2.0.0 · 2026-10-26 · 稳定性与代码整理

- 修复分类超过 20 类导致重启加载失败，增加保存前整体校验和损坏文件写保护。
- 修复跨天/全天任务编辑时长截断、便签快捷新增参数错误、自然语言识别字段残留。
- 统一普通任务、课程与考试的稍后提醒；持久化课程阶段去重，避免重启重复提醒。
- 修复写信窗口；SMTP 发送改为队列，IMAP 读取串行化并过滤过期响应。
- 新建考前复习安排改为一次原子保存；停止启动时自动删除过期日程。
- 恢复默认浏览器沙箱，限制宿主页面导航、IPC、附件及邮件链接，停止保存完整网页快照。
- 拆分导入/提醒服务；删除旧更新通道、冗余入口、死样式及失效调试代码。
- 固定测试和打包依赖，修复打包时被外部 PATH 污染的 ICU DLL 来源。
- 验证结果：284 项 Python、29 项 JavaScript、39 项端到端检查通过；两种 exe 均通过发布自查。

详见 [v2.0.0 发布说明](V2_RELEASE_NOTES.md)。本版不增加新的业务功能。

### v1.0.0 开发期迭代 · 未单独编号

- 增加自定义分类、标签、Markdown 预览及本机附件路径引用。
- 增加基础 IMAP/SMTP 邮件模块和 Windows DPAPI 授权码保存。
- 增加教务课表、二课、考试导入，统一“一键添加”入口，并增加二课接口全量读取及回落处理。
- 调整早八、午休、换教室和天气提醒，修复导入标题、日期面板排版和待办用时统计。
- 增加真实 WebEngine 冒烟测试、导入测试、打包自检及旧课程数据修复。
- 实现四周/周/清单日历、待办、优先级、重复任务、提醒中心和桌面便签。
- 增加托盘常驻、声音及独立窗口提醒、开机启动、JSON 备份导入导出和旧目录迁移。
- 集成多标签内置浏览器、书签与持久登录状态。
- 增加硬件渲染、自动软件渲染回退、按帧合并重绘和文件夹/单文件两种打包形式。

## 运行

- 推荐：`dist\nanqi_schedule\nanqi_schedule.exe`。分发时复制整个文件夹。
- 单文件：`dist\nanqi_schedule.exe`，启动时需要解压运行库。
- 源码：Python 3.12.4、Windows x64；创建 `.venv` 后安装 `requirements.txt`，执行 `python main.py`。
- 完整开发环境：`python -m pip install -r requirements-lock.txt`。其中包含本次验证的运行、测试及打包依赖；`requirements-dev.txt` 是直接开发依赖。

## v2.0 的变更

本版以修复和代码整理为主，未加入新业务功能：

- 分类上限统一为 20；非法写入整体失败，损坏文件不会被空状态覆盖，仍可从设置导入备份恢复。
- 修复跨午夜/24 小时任务编辑后时长缩短、便签三种提醒无法新增、自动识别字段残留。
- 课程、考试支持现有的稍后提醒操作；阶段去重保存到日程，重启后不会重复同一阶段，改期后重新判定。
- 修复写信窗口；SMTP 改为队列，IMAP 改为串行请求并忽略过期结果。
- 新建复习安排改为一次保存，失败整批回滚。
- 停止启动时自动删除过期记录，导入仍跳过过期条目。
- 移除指向另一产品的更新通道；版本更新通过重新分发安装包完成。
- 收紧宿主页面导航、IPC 和附件权限；默认保留浏览器沙箱，不再保存整页 HTML，仅写脱敏导入摘要。
- 将导入和提醒业务从窗口控制器分离。

## 数据与备份

默认目录：`%APPDATA%\nanqi_schedule`，可通过 `NANQI_DATA_DIR` 覆盖；兼容旧名 `WEEKLIGHT_DATA_DIR`。

日程仍使用 `version=1`，可读取旧备份。`plans.json` 保存日程；`.bak` 和 `before-*` 是恢复副本。v2 新增可选内部提醒状态，不要求手工迁移。加载失败时禁止普通保存，请使用设置中的导入备份恢复。

邮件配置为 `mail.json`，授权码为 Windows DPAPI 加密的 `mail.secret`，不随日程备份导出。附件仅保存本机路径，导出不会复制附件内容；危险文件类型和网络路径不能直接打开。

升级前的源码与发布包保存在 `release_backups/`。旧日程、凭据、备份和网页快照不会被清理。不要把含个人数据的目录作为错误报告直接上传。

## 验证与打包

```powershell
.venv\Scripts\python.exe -m pytest tests -q --basetemp=.work_v2/pytest-run
node --test tests/js/*.test.cjs
.venv\Scripts\python.exe tests/ui_smoke.py
.venv\Scripts\python.exe tests/ui_v2_smoke.py
.venv\Scripts\python.exe tests/ui_import_smoke.py
.venv\Scripts\python.exe tests/ui_erc_api_smoke.py
.venv\Scripts\python.exe build.py
```

测试仅使用合成数据和本机模拟接口。请在可访问 127.0.0.1 且具备桌面会话的环境运行完整测试。`build.py` 默认生成两种 exe，并在独立数据目录运行静态复查、`--selfcheck`、`--doctor`、`--selftest-ui`。

命令行保留 `--hidden`、`--widget`、`--browser URL`、`--doctor`、`--selfcheck`、`--selftest-ui`、`--reset-webengine`、`--version`。

## 本版未解决的功能限制

- 课表缺少学期起点时仍使用原有当前周推断，可通过已有整体平移校正；本版没有新增学期设置。
- 不支持重复规则编辑/停止重复、考试改期联动复习、考试日期再次编辑。
- 邮件尚无搜索、附件下载、新邮通知、邮件转任务和新增账号设置页；Gmail/Outlook 等 OAuth 场景仍需网页登录。
- 日程与备份是明文 JSON；尚未实现 SQLite、大模型、同步或完整多日事件模型，导入时长仍不超过 1440 分钟。

本项目部分参考橙子日程的前端与逻辑迁移，并复用了作者未完成项目 ustc-assistant 的浏览器实现；本 README 的早期版本历史依据本地留存文档整理；早期性能记录不代表 v2 当前测试结果。
