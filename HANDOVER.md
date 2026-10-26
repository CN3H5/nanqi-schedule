# 南七日程 v2.0.0 开发交接

日期：2026-10-26。目标：修复现有问题，整理代码，不增加新业务功能。

## 环境

Windows x64 / Python 3.12.4 / PySide6 6.10.0 / PyInstaller 6.16.0 / pytest 8.3.5。完整依赖见 requirements-lock.txt。用 .venv 运行测试及 build.py。

## 核心边界

- Store：写入前做无副作用全局校验；损坏加载锁定普通写入，restore_backup 是显式恢复入口。
- ImportService：课程、考试和二课的任务构造、分类与去重；AppController 保留浏览器抓取、窗口和 UI 结果通知。
- ReminderService：通知候选与持久化阶段；控制器只触发扫描、显示系统通知和窗口。
- MailService：单 SMTP 队列；MailRequests：单 IMAP 工作线程及请求代次。
- WebPageView：只允许对应应用 HTML；浏览器页面没有宿主通道。
- IPC：显式白名单；save-task 第三参数为可选考试日期，仅用于原子创建现有复习安排。

## 数据兼容与安全

日程结构版本保持 1；任务可选 deliveryState 保存 schedule/stages；改期清除失效阶段。邮件新增 lastDigestAttemptDate 区分尝试与成功，失败当天不自动重试。旧 checkUpdates 被忽略，无更新联网代码。不要恢复启动时 prune_past。

文件损坏时不自动清空、不自动覆盖；恢复走设置导入。不要清理用户备份。附件只允许登记的本机安全类型，邮件资源加载关闭，打开外链限定协议。不要为解决白屏重新全局关闭 Chromium 沙箱。

## 删除和暂缓

已移除：旧更新实现、相关 IPC/页面资源/测试、无调用邮件 IPC、旧增删/导入包装、过期删除函数、失效调试脚本和死样式。性能测试已经改用批量操作入口。

暂缓：学期起点设置、重复规则编辑、考试日期编辑/复习联动、邮件搜索/附件下载/新邮通知/转任务、SQLite/LLM/同步。保留历史审计资料，旧审计结论应结合本次修复清单判断。

发布前应核对 V2_RELEASE_NOTES.md 和 build/verification 中的报告；用户数据目录不得用于测试。使用 Computer Use 验收独立实例，禁止发送真实邮件或登录真实账号。
