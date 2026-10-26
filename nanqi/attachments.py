"""Shared attachment policy. No shell execution or network paths."""
import os
from pathlib import Path

SAFE_EXTENSIONS = frozenset(".pdf .txt .md .csv .tsv .xlsx .docx .pptx .odt .ods .odp .png .jpg .jpeg .gif .webp .bmp .mp3 .wav .ogg .mp4 .mkv .zip .7z".split())


def validate_attachment(path, registered):
    if not isinstance(path, str) or not path or path.startswith(("\\\\", "//")) or ":" in path[2:]:
        raise ValueError("附件必须是本机普通文件，不允许网络路径")
    target = Path(path)
    if not target.is_absolute() or target.suffix.lower() not in SAFE_EXTENSIONS:
        raise ValueError("此附件类型不允许直接打开")
    target = target.resolve()
    if str(target).startswith(("\\\\", "//")) or target.suffix.lower() not in SAFE_EXTENSIONS or not target.is_file():
        raise ValueError("附件文件不存在或不是本机文件")
    allowed = {os.path.normcase(str(Path(p).resolve())) for p in registered if isinstance(p, str) and p and not p.startswith(("\\\\", "//"))}
    if os.path.normcase(str(target)) not in allowed:
        raise ValueError("附件尚未登记，请先选择附件")
    return target
