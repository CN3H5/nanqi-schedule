"""test_webengine.py —— 渲染模式的选择与回退（性能与「能不能打开」的平衡点）。

【为什么这组测试重要】
本版的性能提升有一半来自「默认用硬件加速」：实测切换视图从约 470 ms 降到约 33 ms
（软件渲染下每次重绘都要 CPU 逐像素画一遍）。但 ustc-assistant 排查过一类机器
（独显 / 虚拟化 / 远程桌面）在创建 GL 上下文时会卡住，那种机器上必须能退回软件渲染。
所以「默认 GPU + 可回退 + 用户显式指定优先」这三条规则必须被钉住，否则要么失去性能，
要么在个别机器上打不开网页。
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from nanqi import webengine


@pytest.fixture(autouse=True)
def isolated_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """把数据目录指到临时目录，并清掉可能影响判断的环境变量。"""
    monkeypatch.setenv("NANQI_DATA_DIR", str(tmp_path))
    for name in (
        webengine.WEBENGINE_SOFTWARE_ENV,
        "NANQI_WEBENGINE_GPU",
        "ORANGESCHEDULE_WEBENGINE_GPU",
    ):
        monkeypatch.delenv(name, raising=False)
    yield tmp_path


def test_default_is_hardware_acceleration(isolated_data_dir: Path) -> None:
    """没有任何记录、也没有环境变量时，默认走硬件加速（性能优先）。"""
    assert webengine.render_mode() == "gpu"
    assert webengine.render_mode_forced() is False


def test_recorded_software_mode_wins_over_the_default(isolated_data_dir: Path) -> None:
    """看门狗写下 software 之后，后续启动都走软件渲染。"""
    assert webengine.set_render_mode("software") is True
    assert webengine.render_mode() == "software"
    assert (isolated_data_dir / "webengine_mode.txt").read_text(encoding="utf-8").strip() == "software"
    # 再切回 gpu 也要生效（用户手动改文件/再次探测成功的情况）
    assert webengine.set_render_mode("gpu") is True
    assert webengine.render_mode() == "gpu"


def test_explicit_environment_variables_override_the_recorded_mode(
    isolated_data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """用户用环境变量显式指定时，优先于记录值，并且不再自动回退。"""
    webengine.set_render_mode("software")

    monkeypatch.setenv("NANQI_WEBENGINE_GPU", "1")
    assert webengine.render_mode() == "gpu"
    assert webengine.render_mode_forced() is True

    monkeypatch.delenv("NANQI_WEBENGINE_GPU")
    monkeypatch.setenv(webengine.WEBENGINE_SOFTWARE_ENV, "1")
    assert webengine.render_mode() == "software"
    assert webengine.render_mode_forced() is True

    # 上一代用过的名字继续认（老脚本/老文档不会失效）
    monkeypatch.delenv(webengine.WEBENGINE_SOFTWARE_ENV)
    monkeypatch.setenv("ORANGESCHEDULE_WEBENGINE_GPU", "1")
    assert webengine.render_mode() == "gpu"


def test_rejects_unknown_modes(isolated_data_dir: Path) -> None:
    with pytest.raises(ValueError):
        webengine.set_render_mode("fastest")


def test_chromium_flags_follow_the_mode(
    isolated_data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """两种模式必须给出不同的开关：GPU 模式不能带 --disable-gpu。"""
    monkeypatch.delenv("QTWEBENGINE_CHROMIUM_FLAGS", raising=False)

    webengine.set_render_mode("gpu")
    gpu_flags = webengine.prepare_webengine()["flags"].split()
    assert "--no-sandbox" not in gpu_flags
    assert "--disable-gpu" not in gpu_flags

    webengine.set_render_mode("software")
    monkeypatch.delenv("QTWEBENGINE_CHROMIUM_FLAGS", raising=False)
    software_flags = webengine.prepare_webengine()["flags"].split()
    assert "--disable-gpu" in software_flags
    assert "--disable-gpu-compositing" in software_flags


def test_existing_chromium_flags_are_never_overwritten(
    isolated_data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """用户/系统已经设过的开关要保留，只补充缺的那些。"""
    monkeypatch.setenv("QTWEBENGINE_CHROMIUM_FLAGS", "--enable-logging --disable-gpu")
    webengine.set_render_mode("gpu")
    flags = webengine.prepare_webengine()["flags"].split()
    assert "--enable-logging" in flags
    # 用户显式关掉了 GPU，就不该再被我们的默认值改回去
    assert flags.count("--disable-gpu") == 1
    assert "--no-sandbox" not in flags


def test_prepare_webengine_reports_where_the_engine_lives() -> None:
    """体检结果要能说清渲染子进程与资源包在哪（白屏排查全靠它）。"""
    info = webengine.prepare_webengine()
    report = webengine.diagnose()
    assert info["flags"]
    assert report["process"], "找不到 QtWebEngineProcess.exe，内置浏览器会白屏"
    assert report["resources"]
    assert report["render_mode"] in ("gpu", "software")
    assert os.environ.get("QTWEBENGINEPROCESS_PATH")
