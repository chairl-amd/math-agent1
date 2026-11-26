"""根据 structural_errors/cases.json 重复生成白底 LaTeX 源码图片。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError as exc:
    print(f"[失败] Pillow 或图片扩展加载失败：{exc}", file=sys.stderr)
    raise SystemExit(2) from exc


BASE_DIR = Path(__file__).resolve().parent
CASES_FILE = BASE_DIR / "cases.json"
FONT_FILE = BASE_DIR / "DejaVuSans.ttf"
IMAGE_SIZE = (1200, 300)
IMAGE_MARGIN = 40
FONT_SIZE = 32


def _load_font() -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """优先加载同目录 DejaVuSans；缺字体时回退并提示。"""
    if FONT_FILE.is_file():
        return ImageFont.truetype(str(FONT_FILE), FONT_SIZE)
    print(f"[警告] 未找到字体文件：{FONT_FILE}，本次使用 Pillow 默认字体")
    return ImageFont.load_default(size=FONT_SIZE)


def _read_cases() -> list[dict]:
    """读取并校验用例清单，避免路径越出当前目录。"""
    data = json.loads(CASES_FILE.read_text(encoding="utf-8"))
    if not isinstance(data, list) or not data:
        raise ValueError("cases.json 必须是非空数组")
    return data


def _resolve_member(file_name: str) -> Path:
    """解析用例文件路径，并确认仍位于 structural_errors 目录内。"""
    path = (BASE_DIR / file_name).resolve()
    if path.parent != BASE_DIR:
        raise ValueError(f"用例文件必须位于当前目录：{file_name}")
    return path


def make_image(case: dict, font: ImageFont.FreeTypeFont | ImageFont.ImageFont) -> Path:
    """把一个 .tex 源码文件绘制成清晰白底图片。"""
    latex_path = _resolve_member(str(case["latex_file"]))
    image_path = _resolve_member(str(case["image"]))
    latex_text = latex_path.read_text(encoding="utf-8").strip()
    if not latex_text:
        raise ValueError(f"LaTeX 文件为空：{latex_path.name}")

    image = Image.new("RGB", IMAGE_SIZE, "white")
    draw = ImageDraw.Draw(image)
    draw.text(
        (IMAGE_MARGIN, IMAGE_MARGIN),
        latex_text,
        fill="black",
        font=font,
        spacing=10,
    )
    image.save(image_path, format="PNG")
    return image_path


def main() -> int:
    """生成全部 structural 用例图片。"""
    font = _load_font()
    generated = []
    for case in _read_cases():
        try:
            generated.append(make_image(case, font))
        except (KeyError, OSError, TypeError, ValueError) as exc:
            print(f"[失败] {case.get('id', '<未知>')}：{exc}")
            return 1
    for path in generated:
        print(f"[完成] {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
