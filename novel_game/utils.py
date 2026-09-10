"""共享工具函数"""

from pathlib import Path


def read_text_auto(file_path: Path) -> str:
    """自动检测编码读取文本（UTF-8优先，回退GBK/GB2312/UTF-16）"""
    raw = Path(file_path).read_bytes()
    for enc in ("utf-8", "gbk", "gb2312", "utf-16"):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="ignore")
