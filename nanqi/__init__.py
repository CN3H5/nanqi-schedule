"""南七日程（Python 重编译版）应用包。

模块分工与 Electron 版一一对应：

    jsjson.py           —— JSON.stringify 兼容的序列化（数据文件互通、令牌可复现）
    paths.py            —— 资源目录 / 数据目录（源码运行与打包运行统一）
    core.py             ← src/core.cjs              计划校验、提醒判定、重复展开
    task_series.py      ← src/task-series.cjs       重复分组与批量操作确认
    widget_model.py     ← src/widget-model.cjs      便签偏好与范围选取
    reminder_copy.py    ← src/reminder-copy.cjs     名句文案与轮换
    data_path.py        ← src/data-path.cjs         数据目录解析
    login_settings.py   ← src/login-settings.cjs    开机自动启动（注册表）
    window_branding.py  ← src/window-branding.cjs   任务栏身份与图标
    window_layer.py     ← src/window-layer.cjs      便签沉底（SetWindowPos）
    store.py            ← src/main.cjs 的状态层     持久化、快照、变更广播
    bridge.py           ← src/preload.cjs + ipcMain 前端与 Python 之间的通道
    notify.py           ← Notification             系统通知
    webengine.py        ← （来自 ustc-assistant）   QtWebEngine 启动环境修复
    browser/            ← （来自 ustc-assistant）   内置浏览器
    windows/            ← 主窗口 / 便签窗口 / 提醒窗口
"""

__all__ = ["__version__"]

# 南七日程自己的版本号。它与上游「橙子日程」的 1.1.x 不是同一条版本线——
# 本版是 Python 重编译 + 更名后的第一个正式版本，因此从 1.0.0 起算。
__version__ = "2.0.0"
