#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""工单1（w10）：弱点扫描用的刁钻用例生成器。

与 make_cases.py 的区别：
1. 内置一个极简的数学排版渲染器（分式、上下标、根号、求和、矩阵、分段），
   图片不是「一行 Unicode 文本」，而是真正带二维结构的公式图，更接近真实截图；
2. 支持多种劣化风格（模拟手写、低分辨率模糊、噪声干扰），用于压测 OCR；
3. 每个用例同时定义图片、参考 LaTeX、参考 C++ 与预期结论，保证标注自洽；
4. --update-annotations 会把本文件的用例合并进 annotations.json（先备份）。

用法：
    python tests/benchmark/make_hard_cases.py                 # 生成全部图片与代码
    python tests/benchmark/make_hard_cases.py --list          # 只看清单
    python tests/benchmark/make_hard_cases.py --only hw_newton --force
    python tests/benchmark/make_hard_cases.py --update-annotations
"""
from __future__ import annotations

import argparse
import io
import json
import math
import random
import shutil
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

try:
    from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps
except ImportError as exc:  # pragma: no cover
    print(f"[失败] 需要 Pillow：{exc}", file=sys.stderr)
    raise SystemExit(2)

BASE = Path(__file__).resolve().parent
ANNOTATIONS = BASE / "annotations.json"

FONT_MATH = "C:/Windows/Fonts/cambria.ttc"
FONT_HAND = "C:/Windows/Fonts/Inkfree.ttf"
FONT_CN = "C:/Windows/Fonts/msyh.ttc"
FONT_MONO = "C:/Windows/Fonts/consola.ttf"

INK = (18, 18, 24)
HAND_INK = (28, 34, 74)
PAPER = (252, 251, 246)

_FONT_CACHE: dict[tuple[str, int], ImageFont.FreeTypeFont] = {}


def font(path: str, size: int) -> ImageFont.FreeTypeFont:
    key = (path, size)
    if key not in _FONT_CACHE:
        _FONT_CACHE[key] = ImageFont.truetype(path, size)
    return _FONT_CACHE[key]


def _is_cjk(ch: str) -> bool:
    return "\u4e00" <= ch <= "\u9fff" or "\u3000" <= ch <= "\u303f"


# ---------------------------------------------------------------------------
# 极简排版：每个元素是一个带基线的透明图片
# ---------------------------------------------------------------------------
@dataclass
class Box:
    img: Image.Image
    baseline: int

    @property
    def w(self) -> int:
        return self.img.width

    @property
    def h(self) -> int:
        return self.img.height


def _trim(img: Image.Image, baseline: int) -> Box:
    bbox = img.split()[3].getbbox()
    if bbox is None:
        return Box(img, baseline)
    left, top, right, bottom = bbox
    return Box(img.crop((left, top, right, bottom)), baseline - top)


class Renderer:
    """把公式拆成 Box 再拼装，支持二维结构。"""

    def __init__(self, size: int = 46, style: str = "clean") -> None:
        self.size = size
        self.style = style
        self.ink = HAND_INK if style == "hand" else INK

    # ---- 基础文本 ----
    def _run_font(self, text: str) -> ImageFont.FreeTypeFont:
        if self.style == "hand" and all(ord(c) < 128 for c in text):
            return font(FONT_HAND, int(self.size * 1.05))
        return font(FONT_MATH, self.size)

    def t(self, text: str, *, scale: float = 1.0,
          color: tuple[int, int, int] | None = None,
          font_path: str | None = None) -> Box:
        size = max(10, int(round(self.size * scale)))
        fill = color or self.ink
        parts: list[Box] = []
        # 中文与 ASCII 分开选字体，避免缺字方块
        runs: list[tuple[str, bool]] = []
        for ch in text:
            cjk = _is_cjk(ch)
            if runs and runs[-1][1] == cjk:
                runs[-1] = (runs[-1][0] + ch, cjk)
            else:
                runs.append((ch, cjk))
        for run_text, cjk in runs:
            if font_path:
                f = font(font_path, size)
            elif cjk:
                f = font(FONT_CN, size)
            else:
                f = font(FONT_MATH, size)
            ascent, descent = f.getmetrics()
            pad = 3
            width = int(f.getlength(run_text)) + 2 * pad
            img = Image.new("RGBA", (max(width, 2), ascent + descent + 2 * pad),
                            (0, 0, 0, 0))
            draw = ImageDraw.Draw(img)
            draw.text((pad, pad + ascent), run_text, font=f, fill=fill,
                      anchor="ls")
            parts.append(_trim(img, pad + ascent))
        if not parts:
            return Box(Image.new("RGBA", (2, self.size), (0, 0, 0, 0)),
                       self.size // 2)
        return parts[0] if len(parts) == 1 else self.cat(*parts)

    # ---- 水平拼接 ----
    def cat(self, *items, gap: int = 2) -> Box:
        boxes = [it if isinstance(it, Box) else self.t(it) for it in items]
        baseline = max(b.baseline for b in boxes)
        bottom = max(b.h - b.baseline for b in boxes)
        width = sum(b.w for b in boxes) + gap * (len(boxes) - 1)
        img = Image.new("RGBA", (max(width, 1), baseline + bottom),
                        (0, 0, 0, 0))
        x = 0
        for b in boxes:
            img.alpha_composite(b.img, (x, baseline - b.baseline))
            x += b.w + gap
        return Box(img, baseline)

    # ---- 分式 ----
    def frac(self, num, den, *, scale: float = 0.92, gap: int = 5) -> Box:
        n = num if isinstance(num, Box) else self.t(num, scale=scale)
        d = den if isinstance(den, Box) else self.t(den, scale=scale)
        pad = 8
        bar = 3
        width = max(n.w, d.w) + 2 * pad
        height = n.h + gap + bar + gap + d.h
        img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        draw.rectangle([pad // 2, n.h + gap, width - pad // 2,
                        n.h + gap + bar - 1], fill=self.ink)
        img.alpha_composite(n.img, ((width - n.w) // 2, 0))
        img.alpha_composite(d.img, ((width - d.w) // 2, n.h + gap + bar + gap))
        return Box(img, n.h + gap + bar // 2)

    # ---- 上下标 ----
    def prime(self, base, count: int = 1) -> Box:
        """画导数撇号。

        说明：Pillow 直接放一个 "'" 字符在 52pt 手写体下只有几像素，视觉模型
        会把它和括号连在一起读丢。实测 f'(x) 的撇号必须画成明确的斜线笔画
        （真实手写也是这么写的），否则该用例测的就不是 OCR 能力而是像素运气。
        """
        b = base if isinstance(base, Box) else self.t(base)
        height = max(b.h, int(self.size * 0.9))
        top_h = int(height * 0.42)
        slash_w = max(4, int(self.size * 0.12))
        dash_h = int(height * 0.30)
        img = Image.new("RGBA", (b.w + slash_w * count + 6, height + top_h),
                        (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        for index in range(count):
            x0 = b.w + index * slash_w + 2
            draw.line([(x0, top_h - dash_h + 2),
                       (x0 + slash_w // 2, top_h - 2)],
                      fill=self.ink, width=max(3, self.size // 16))
        img.alpha_composite(b.img, (0, top_h))
        return Box(img, top_h + b.baseline)

    def sub(self, base, idx) -> Box:
        b = base if isinstance(base, Box) else self.t(base)
        s = idx if isinstance(idx, Box) else self.t(idx, scale=0.64)
        drop = int(b.h * 0.22)
        width = b.w + s.w - 2
        height = max(b.h, b.baseline + drop + (s.h - s.baseline))
        img = Image.new("RGBA", (width, max(height, b.h)), (0, 0, 0, 0))
        img.alpha_composite(b.img, (0, 0))
        img.alpha_composite(s.img, (b.w - 2, b.baseline + drop - s.baseline))
        return Box(img, b.baseline)

    def sup(self, base, exp) -> Box:
        b = base if isinstance(base, Box) else self.t(base)
        s = exp if isinstance(exp, Box) else self.t(exp, scale=0.64)
        raise_by = int(b.h * 0.38)
        width = b.w + s.w - 2
        top = max(0, raise_by - s.baseline)
        img = Image.new("RGBA", (width, b.h + top), (0, 0, 0, 0))
        img.alpha_composite(b.img, (0, top))
        img.alpha_composite(s.img, (b.w - 2, top + b.baseline - raise_by - s.baseline))
        return Box(img, b.baseline + top)

    def subsup(self, base, idx, exp) -> Box:
        b = base if isinstance(base, Box) else self.t(base)
        i = idx if isinstance(idx, Box) else self.t(idx, scale=0.64)
        e = exp if isinstance(exp, Box) else self.t(exp, scale=0.64)
        drop = int(b.h * 0.24)
        raise_by = int(b.h * 0.36)
        top = max(0, raise_by - e.baseline)
        width = max(b.w + e.w, b.w + i.w) - 2
        height = top + b.h + drop + (i.h - i.baseline)
        img = Image.new("RGBA", (max(width, 1), max(height, top + b.h)),
                        (0, 0, 0, 0))
        img.alpha_composite(b.img, (0, top))
        img.alpha_composite(e.img, (b.w - 2, top + b.baseline - raise_by - e.baseline))
        img.alpha_composite(i.img, (b.w - 2, top + b.baseline + drop - i.baseline))
        return Box(img, top + b.baseline)

    # ---- 根号 ----
    def sqrt(self, body, *, scale: float = 0.95) -> Box:
        b = body if isinstance(body, Box) else self.t(body, scale=scale)
        thick = max(2, self.size // 22)
        rad_w = int(self.size * 0.55)
        bar_y = thick
        height = b.h + thick * 2
        width = rad_w + b.w + 6
        img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        bottom = height - 1
        draw.line([(2, int(height * 0.55)), (rad_w * 0.42, bottom)],
                  fill=self.ink, width=thick)
        draw.line([(rad_w * 0.42, bottom), (rad_w * 0.85, bar_y + thick)],
                  fill=self.ink, width=thick)
        draw.line([(rad_w * 0.8, bar_y + thick // 2), (width - 1, bar_y + thick // 2)],
                  fill=self.ink, width=thick)
        img.alpha_composite(b.img, (rad_w, thick * 2 - 1))
        body_baseline = thick * 2 - 1 + b.baseline
        return Box(img, body_baseline)

    # ---- 求和/连乘等大算子（上下限堆叠） ----
    def bigop(self, symbol: str, *, lower=None, upper=None, body=None,
              scale: float = 1.75, gap: int = 4) -> Box:
        sym = self.t(symbol, scale=scale)
        parts: list[Box] = []
        if upper is not None:
            parts.append(upper if isinstance(upper, Box)
                         else self.t(upper, scale=0.55))
        parts.append(sym)
        if lower is not None:
            parts.append(lower if isinstance(lower, Box)
                         else self.t(lower, scale=0.55))
        width = max(p.w for p in parts)
        height = sum(p.h for p in parts) + gap * (len(parts) - 1)
        img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        y = 0
        for p in parts:
            img.alpha_composite(p.img, ((width - p.w) // 2, y))
            y += p.h + gap
        op_box = Box(img, parts[0].baseline if upper is not None
                     else sym.baseline)
        if body is None:
            return op_box
        body_box = body if isinstance(body, Box) else self.t(body)
        return self.cat(op_box, body_box, gap=8)

    # ---- 矩阵 ----
    def matrix(self, rows: list[list], *, left: str = "[", right: str = "]",
               pad: int = 10, vgap: int = 8, hgap: int = 16) -> Box:
        grid: list[list[Box]] = []
        for row in rows:
            cells = [c if isinstance(c, Box) else self.t(c, scale=0.85)
                     for c in row]
            grid.append(cells)
        n_cols = max(len(r) for r in grid)
        col_w = [0] * n_cols
        for row in grid:
            for j, cell in enumerate(row):
                col_w[j] = max(col_w[j], cell.w)
        row_h = [max(c.h for c in row) for row in grid]
        row_base = [max(c.baseline for c in row) for row in grid]
        content_w = sum(col_w) + hgap * (n_cols - 1)
        content_h = sum(row_h) + vgap * (len(grid) - 1)
        thick = max(2, self.size // 20)
        arm = max(6, self.size // 5)
        total_w = content_w + 2 * pad + 2 * arm
        total_h = content_h + 2 * pad
        img = Image.new("RGBA", (total_w, total_h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        y = pad
        for row, h, base in zip(grid, row_h, row_base):
            x = pad + arm
            for j, cell in enumerate(row):
                img.alpha_composite(cell.img, (x, y + base - cell.baseline))
                x += col_w[j] + hgap
            y += h + vgap
        top, bottom = pad, total_h - pad
        left_x, right_x = pad, total_w - pad
        # 只画竖线 + 上下短横，形成方括号
        draw.line([(left_x, top), (left_x, bottom)], fill=self.ink, width=thick)
        draw.line([(left_x, top), (left_x + arm, top)], fill=self.ink, width=thick)
        draw.line([(left_x, bottom), (left_x + arm, bottom)],
                  fill=self.ink, width=thick)
        draw.line([(right_x, top), (right_x, bottom)], fill=self.ink, width=thick)
        draw.line([(right_x - arm, top), (right_x, top)],
                  fill=self.ink, width=thick)
        draw.line([(right_x - arm, bottom), (right_x, bottom)],
                  fill=self.ink, width=thick)
        return Box(img, total_h // 2)

    # ---- 分段函数 ----
    def cases(self, rows: list[tuple], *, gap: int = 18) -> Box:
        cells = [(e if isinstance(e, Box) else self.t(e, scale=0.9),
                  c if isinstance(c, Box) else self.t(c, scale=0.9))
                 for e, c in rows]
        expr_w = max(a.w for a, _ in cells)
        cond_w = max(b.w for _, b in cells)
        row_h = [max(a.h, b.h) for a, b in cells]
        row_base = [max(a.baseline, b.baseline) for a, b in cells]
        content_w = expr_w + gap + cond_w
        content_h = sum(row_h) + 6 * (len(cells) - 1)
        brace_w = max(10, int(content_h * 0.28))
        pad = 6
        total_w = brace_w + content_w + 2 * pad
        total_h = content_h + 2 * pad
        img = Image.new("RGBA", (total_w, total_h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        # 左花括号：用多段折线近似
        mid = total_h / 2
        draw.line([(pad + brace_w, pad), (pad + 2, pad + total_h * 0.12)],
                  fill=self.ink, width=3)
        draw.line([(pad + 2, pad + total_h * 0.12),
                   (pad + brace_w * 0.35, mid)], fill=self.ink, width=3)
        draw.line([(pad + brace_w * 0.35, mid),
                   (pad + 2, total_h - pad - total_h * 0.12)],
                  fill=self.ink, width=3)
        draw.line([(pad + 2, total_h - pad - total_h * 0.12),
                   (pad + brace_w, total_h - pad)], fill=self.ink, width=3)
        y = pad
        for (a, b), h, base in zip(cells, row_h, row_base):
            img.alpha_composite(a.img, (pad + brace_w + 2, y + base - a.baseline))
            img.alpha_composite(b.img, (pad + brace_w + 2 + expr_w + gap,
                                        y + base - b.baseline))
            y += h + 6
        return Box(img, total_h // 2)


# ---------------------------------------------------------------------------
# 风格劣化
# ---------------------------------------------------------------------------
def compose(box: Box, *, margin: int = 64, bg: tuple[int, int, int] = (255, 255, 255),
            min_width: int = 0) -> Image.Image:
    width = max(box.w + 2 * margin, min_width)
    height = box.h + 2 * margin
    img = Image.new("RGB", (width, height), bg)
    img.paste(box.img, ((width - box.w) // 2, margin), box.img)
    return img


def style_hand(img: Image.Image, seed: int) -> Image.Image:
    rng = random.Random(seed)
    angle = rng.uniform(-2.2, 2.2)
    img = img.rotate(angle, resample=Image.BICUBIC, expand=True, fillcolor=PAPER)
    img = img.filter(ImageFilter.GaussianBlur(0.5))
    arr = bytearray(img.tobytes())
    for i in range(0, len(arr), 3):
        if rng.random() < 0.02:
            delta = rng.randint(-10, 10)
            for k in range(3):
                arr[i + k] = max(0, min(255, arr[i + k] + delta))
    return Image.frombytes("RGB", img.size, bytes(arr))


def style_blur(img: Image.Image, seed: int) -> Image.Image:
    rng = random.Random(seed)
    scale = rng.uniform(0.32, 0.42)
    small = img.resize((max(80, int(img.width * scale)),
                        max(40, int(img.height * scale))), Image.LANCZOS)
    blurry = small.resize(img.size, Image.BILINEAR)
    buf = io.BytesIO()
    blurry.save(buf, format="JPEG", quality=rng.randint(34, 44))
    blurry = Image.open(buf).convert("RGB")
    arr = bytearray(blurry.tobytes())
    for i in range(0, len(arr), 3):
        if rng.random() < 0.004:
            v = rng.choice((0, 255))
            arr[i] = arr[i + 1] = arr[i + 2] = v
    return Image.frombytes("RGB", blurry.size, bytes(arr))


def style_noise(img: Image.Image, seed: int) -> Image.Image:
    rng = random.Random(seed)
    arr = bytearray(img.tobytes())
    for i in range(0, len(arr), 3):
        n = rng.gauss(0, 9)
        for k in range(3):
            arr[i + k] = max(0, min(255, int(arr[i + k] + n)))
        if rng.random() < 0.003:
            v = rng.choice((0, 255))
            arr[i] = arr[i + 1] = arr[i + 2] = v
    img = Image.frombytes("RGB", img.size, bytes(arr))
    draw = ImageDraw.Draw(img)
    for _ in range(2):
        y = rng.randint(10, max(11, img.height - 10))
        draw.line([(rng.randint(0, 20), y), (img.width - rng.randint(0, 20), y)],
                  fill=(200, 200, 198), width=1)
    return img


# ---------------------------------------------------------------------------
# 用例定义
# ---------------------------------------------------------------------------
def build_formulas() -> dict[str, Box]:
    """集中构造所有公式图片，避免 CASE 定义里堆嵌套调用。"""
    f: dict[str, Box] = {}

    # ---- 手写体 ----
    r = Renderer(52, "hand")
    f["hw_newton"] = r.cat(
        r.sub("x", "k+1"), " = ", r.sub("x", "k"), " - ",
        r.frac(r.cat(r.t("f", scale=0.92), r.t("(", scale=0.92),
                     r.sub(r.t("x", scale=0.92), r.t("k", scale=0.7)),
                     r.t(")", scale=0.92)),
               r.cat(r.prime(r.t("f", scale=0.92)),
                     r.t("(", scale=0.92),
                     r.sub(r.t("x", scale=0.92), r.t("k", scale=0.7)),
                     r.t(")", scale=0.92))),
    )
    f["hw_trace"] = r.cat(
        r.t("tr", scale=1.05), "(", "A", ") = ",
        r.bigop("∑", lower="i=1", upper="n"),
        r.sub(r.t("a", scale=1.05), "ii"),
    )
    f["hw_sigmoid"] = r.cat(
        "f", "(", "x", ") = ",
        r.frac("1", r.cat("1 + ", r.sup("e", r.t("-x", scale=0.8)))),
    )

    # ---- 模糊/低分辨率/噪声 ----
    r = Renderer(48)
    f["blur_hinge"] = r.cat(
        "L = ", r.frac("1", "n"),
        r.bigop("∑", lower="i=1", upper="n"),
        r.cat("max(0, 1 - ",
              r.sub("y", "i"),
              r.cat("(", r.cat("w", r.t("·", scale=0.9), r.sub("x", "i")), ")"),
              ")"),
    )
    f["blur_cosine"] = r.cat(
        "cos", "θ", " = ",
        r.frac(r.cat("a", r.t("·", scale=0.9), "b"),
               r.cat("‖a‖", r.t(" ", scale=0.6), "‖b‖")),
    )
    f["noise_l1"] = r.cat(
        r.sub("‖x‖", "1"),
        " = ", r.bigop("∑", lower="i=1", upper="n"),
        r.cat("|", r.sub("x", "i"), "|"),
    )

    # ---- 复杂排版 ----
    f["layout_piecewise"] = r.cat(
        "f", "(", "x", ") = ",
        r.cases([(r.t("x,", scale=0.9), r.t("x > 0", scale=0.9)),
                 (r.t("0,", scale=0.9), r.t("x ≤ 0", scale=0.9))]),
    )
    f["layout_nested_frac"] = r.cat(
        "g", "(", "x", ") = ",
        r.frac("1", r.cat("1 + ", r.frac("1", r.cat("1 + ", "x")))),
    )
    f["layout_matrix_frac"] = r.cat(
        r.matrix([["a", "b"], ["c", "d"]]),
        r.matrix([["e", "f"], ["g", "h"]]),
        " = ",
        r.matrix([[r.cat("ae + bg"), r.cat("af + bh")],
                  [r.cat("ce + dg"), r.cat("cf + dh")]]),
    )
    # 多行公式：渲染成两行再竖直拼接
    line1 = r.cat("r = b - A x")
    line2 = r.cat("x = x + α r")
    f["layout_multiline_update"] = _vstack([line1, line2], gap=22)
    # ---- 边界 ----
    r_small = Renderer(26)
    f["edge_short"] = r_small.cat("y = a x")
    r = Renderer(46)
    f["edge_mlp_chain"] = _vstack([
        r.cat(r.sub("h", "1"), " = ", r.sub("W", "1"), " x + ", r.sub("b", "1")),
        r.cat(r.sub("a", "1"), " = ReLU(", r.sub("h", "1"), ")"),
        r.cat(r.sub("h", "2"), " = ", r.sub("W", "2"), " ",
              r.sub("a", "1"), " + ", r.sub("b", "2")),
        r.cat("y = softmax(", r.sub("h", "2"), ")"),
    ], gap=16)
    f["edge_mixed_lang"] = r.cat(
        r.sub("w", "k+1"), " = ", r.sub("w", "k"), " - η ∇L(",
        r.sub("w", "k"), ")",
        r.t("   (η 为学习率)", scale=0.8),
    )

    # ---- 变体表示 ----
    r = Renderer(42)
    f["var_sum_sigma"] = _vstack([
        r.t("s = \\sum_{i=0}^{n-1} a_i b_i", font_path=FONT_MONO, scale=0.85),
    ], gap=10)
    r = Renderer(46)
    f["var_norm_squared"] = r.cat(
        r.subsup("‖x‖", "2", "2"), " = ",
        r.bigop("∑", lower="i=1", upper="n"),
        r.sup(r.sub("x", "i"), "2"),
    )
    r = Renderer(42)
    f["var_offbyone_variant"] = r.cat(
        "s = ", r.bigop("∑", lower="i=0", upper="n-1"), r.sub("a", "i"),
    )
    r = Renderer(46)
    f["var_avg_vs_sum"] = r.cat(
        r.sup("x", r.t("¯", scale=0.9)), " = ",
        r.frac("1", "n"),
        r.bigop("∑", lower="i=1", upper="n"),
        r.sub("x", "i"),
    )
    return f


def _vstack(boxes: list[Box], gap: int = 18) -> Box:
    width = max(b.w for b in boxes)
    height = sum(b.h for b in boxes) + gap * (len(boxes) - 1)
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    y = 0
    for b in boxes:
        img.alpha_composite(b.img, ((width - b.w) // 2, y))
        y += b.h + gap
    return Box(img, boxes[0].baseline)


CPP_NEWTON = """#include <cmath>

double newton_step(double x, double fx, double dfx) {
    return x - fx / dfx;
}
"""

CPP_TRACE = """#include <vector>
#include <cstddef>

double trace(const std::vector<std::vector<double>>& a) {
    double s = 0.0;
    for (std::size_t i = 0; i < a.size(); ++i) {
        s += a[i][i];
    }
    return s;
}
"""

CPP_SIGMOID = """#include <cmath>

double sigmoid(double x) {
    return 1.0 / (1.0 + std::exp(x));
}
"""

CPP_HINGE = """#include <vector>
#include <cstddef>

double hinge_loss(const std::vector<double>& y,
                  const std::vector<double>& wx) {
    double total = 0.0;
    for (std::size_t i = 0; i < y.size(); ++i) {
        total += 1.0 - y[i] * wx[i];
    }
    return total / static_cast<double>(y.size());
}
"""

CPP_COSINE = """#include <cmath>
#include <vector>
#include <cstddef>

double cosine(const std::vector<double>& a,
              const std::vector<double>& b) {
    double dot = 0.0;
    double na = 0.0;
    double nb = 0.0;
    for (std::size_t i = 0; i < a.size(); ++i) {
        dot += a[i] * b[i];
        na += a[i] * a[i];
        nb += b[i] * b[i];
    }
    return dot / (std::sqrt(na) + std::sqrt(nb));
}
"""

CPP_L1 = """#include <cmath>
#include <vector>
#include <cstddef>

double l1_norm(const std::vector<double>& x) {
    double s = 0.0;
    for (std::size_t i = 0; i < x.size(); ++i) {
        s += std::fabs(x[i]);
    }
    return s;
}
"""

CPP_RELU = """double relu(double x) {
    return x > 0.0 ? x : 0.0;
}
"""

CPP_NESTED = """double g(double x) {
    return 1.0 / (1.0 + 1.0 / (1.0 + x));
}
"""

CPP_MATMUL2 = """void matmul2(const double a[2][2], const double b[2][2],
             double c[2][2]) {
    c[0][0] = a[0][0] * b[0][0] + a[0][1] * b[1][0];
    c[0][1] = a[0][0] * b[0][1] + a[0][1] * b[1][1];
    c[1][0] = a[1][0] * b[0][0] + a[1][1] * b[1][0];
    c[1][1] = a[1][0] * b[0][1] + a[1][1] * b[1][1];
}
"""

CPP_CG_STEP = """#include <vector>
#include <cstddef>

void cg_step(const std::vector<std::vector<double>>& A,
             const std::vector<double>& b,
             std::vector<double>& x,
             double alpha) {
    std::vector<double> r(b.size(), 0.0);
    for (std::size_t i = 0; i < b.size(); ++i) {
        double ax = 0.0;
        for (std::size_t j = 0; j < b.size(); ++j) {
            ax += A[i][j] * x[j];
        }
        r[i] = b[i] - ax;
    }
    for (std::size_t i = 0; i < x.size(); ++i) {
        x[i] = x[i] + alpha * r[i];
    }
}
"""

CPP_SHORT = """double scale(double a, double x) {
    return a * x;
}
"""

CPP_MLP = """#include <cmath>
#include <vector>
#include <cstddef>

std::vector<double> mlp(const std::vector<std::vector<double>>& W1,
                        const std::vector<double>& b1,
                        const std::vector<std::vector<double>>& W2,
                        const std::vector<double>& b2,
                        const std::vector<double>& x) {
    std::vector<double> h1(b1.size(), 0.0);
    for (std::size_t i = 0; i < b1.size(); ++i) {
        h1[i] = b1[i];
        for (std::size_t j = 0; j < x.size(); ++j) {
            h1[i] += W1[i][j] * x[j];
        }
    }
    std::vector<double> a1(h1.size(), 0.0);
    for (std::size_t i = 0; i < h1.size(); ++i) {
        a1[i] = h1[i] > 0.0 ? h1[i] : 0.0;
    }
    std::vector<double> h2(b2.size(), 0.0);
    for (std::size_t i = 0; i < b2.size(); ++i) {
        h2[i] = b2[i];
        for (std::size_t j = 0; j < a1.size(); ++j) {
            h2[i] += W2[i][j] * a1[j];
        }
    }
    double total = 0.0;
    for (std::size_t i = 0; i < h2.size(); ++i) {
        total += std::exp(h2[i]);
    }
    std::vector<double> y(h2.size(), 0.0);
    for (std::size_t i = 0; i < h2.size(); ++i) {
        y[i] = std::exp(h2[i]) / total;
    }
    return y;
}
"""

CPP_MIXED = """#include <vector>
#include <cstddef>

void step(std::vector<double>& w, const std::vector<double>& grad,
          double eta) {
    for (std::size_t i = 0; i < w.size(); ++i) {
        w[i] += eta * grad[i];
    }
}
"""

CPP_DOT = """#include <vector>
#include <cstddef>

double dot(const std::vector<double>& a, const std::vector<double>& b) {
    double s = 0.0;
    for (std::size_t i = 0; i < a.size(); ++i) {
        s += a[i] * b[i];
    }
    return s;
}
"""

CPP_NORM_SQ = """#include <vector>
#include <cstddef>

double norm_sq(const std::vector<double>& x) {
    double s = 0.0;
    for (std::size_t i = 0; i < x.size(); ++i) {
        s += x[i] * x[i];
    }
    return s;
}
"""

CPP_SKIP_FIRST = """#include <vector>
#include <cstddef>

double sum_skip_first(const std::vector<double>& a) {
    double s = 0.0;
    for (std::size_t i = 1; i < a.size(); ++i) {
        s += a[i];
    }
    return s;
}
"""

CPP_MEAN_SUMLESS = """#include <vector>
#include <cstddef>

double mean(const std::vector<double>& x) {
    double s = 0.0;
    for (std::size_t i = 0; i < x.size(); ++i) {
        s += x[i];
    }
    return s;
}
"""

CASES: list[dict] = [
    # ---------------- 手写体 ----------------
    {
        "id": "hw_newton",
        "suite": "hard_hand",
        "dir": "hard_hand",
        "category": "correct",
        "expected": "pass",
        "expected_status": "equivalent",
        "error_type": "none",
        "style": "hand",
        "formula_key": "hw_newton",
        "latex": "x_{k+1} = x_k - \\frac{f(x_k)}{f'(x_k)}",
        "cpp": CPP_NEWTON,
        "note": "手写体牛顿迭代；图与代码均为 x - f/f'，应判等价",
    },
    {
        "id": "hw_trace",
        "suite": "hard_hand",
        "dir": "hard_hand",
        "category": "correct",
        "expected": "pass",
        "expected_status": "equivalent",
        "error_type": "none",
        "style": "hand",
        "formula_key": "hw_trace",
        "latex": "\\mathrm{tr}(A) = \\sum_{i=1}^{n} a_{ii}",
        "cpp": CPP_TRACE,
        "note": "手写体迹公式，带上下界的求和；代码遍历对角线",
    },
    {
        "id": "hw_sigmoid",
        "suite": "hard_hand",
        "dir": "hard_hand",
        "category": "incorrect",
        "expected": "fail",
        "expected_status": "not_symbolically_equivalent",
        "error_type": "sign_error",
        "style": "hand",
        "formula_key": "hw_sigmoid",
        "latex": "f(x) = \\frac{1}{1+e^{-x}}",
        "cpp": CPP_SIGMOID,
        "note": "手写体 sigmoid；代码指数符号相反（e^{+x}），应判不等价",
    },

    # ---------------- 模糊 / 低分辨率 / 噪声 ----------------
    {
        "id": "blur_hinge",
        "suite": "hard_noise",
        "dir": "hard_noise",
        "category": "incorrect",
        "expected": "fail",
        "expected_status": "not_symbolically_equivalent",
        "error_type": "missing_operation",
        "style": "blur",
        "formula_key": "blur_hinge",
        "latex": "L = \\frac{1}{n}\\sum_{i=1}^{n}\\max(0, 1-y_i(w\\cdot x_i))",
        "cpp": CPP_HINGE,
        "note": "模糊低分辨率；代码缺少 max(0,·) 铰链截断，应判不等价",
    },
    {
        "id": "noise_l1",
        "suite": "hard_noise",
        "dir": "hard_noise",
        "category": "correct",
        "expected": "pass",
        "expected_status": "equivalent",
        "error_type": "none",
        "style": "noise",
        "formula_key": "noise_l1",
        "latex": "\\|x\\|_1 = \\sum_{i=1}^{n}|x_i|",
        "cpp": CPP_L1,
        "note": "噪声干扰；L1 范数定义，代码累加绝对值",
    },
    {
        "id": "blur_cosine",
        "suite": "hard_noise",
        "dir": "hard_noise",
        "category": "incorrect",
        "expected": "fail",
        "expected_status": "not_symbolically_equivalent",
        "error_type": "operator_error",
        "style": "blur",
        "formula_key": "blur_cosine",
        "latex": "\\cos\\theta = \\frac{a\\cdot b}{\\|a\\|\\,\\|b\\|}",
        "cpp": CPP_COSINE,
        "note": "模糊+噪声；代码分母用范数之和而非乘积，应判不等价",
    },

    # ---------------- 复杂排版 ----------------
    {
        "id": "layout_piecewise",
        "suite": "hard_layout",
        "dir": "hard_layout",
        "category": "correct",
        "expected": "pass",
        "expected_status": "equivalent",
        "error_type": "none",
        "style": "clean",
        "formula_key": "layout_piecewise",
        "latex": "f(x) = \\begin{cases} x, & x>0 \\\\ 0, & x\\le 0\\end{cases}",
        "cpp": CPP_RELU,
        "note": "分段函数（cases 环境）；与三元表达式 ReLU 等价",
    },
    {
        "id": "layout_nested_frac",
        "suite": "hard_layout",
        "dir": "hard_layout",
        "category": "correct",
        "expected": "pass",
        "expected_status": "equivalent",
        "error_type": "none",
        "style": "clean",
        "formula_key": "layout_nested_frac",
        "latex": "g(x) = \\frac{1}{1+\\frac{1}{1+x}}",
        "cpp": CPP_NESTED,
        "note": "三层嵌套分式；与代码逐层除法等价",
    },
    {
        "id": "layout_matrix_frac",
        "suite": "hard_layout",
        "dir": "hard_layout",
        "category": "correct",
        "expected": "pass",
        "expected_status": "equivalent",
        "error_type": "none",
        "style": "clean",
        "formula_key": "layout_matrix_frac",
        "latex": (
            "\\begin{bmatrix}a&b\\\\c&d\\end{bmatrix}"
            "\\begin{bmatrix}e&f\\\\g&h\\end{bmatrix}="
            "\\begin{bmatrix}ae+bg&af+bh\\\\ce+dg&cf+dh\\end{bmatrix}"
        ),
        "cpp": CPP_MATMUL2,
        "note": "2x2 矩阵乘法的元素展开式，9 个符号严格对照",
    },
    {
        "id": "layout_multiline_update",
        "suite": "hard_layout",
        "dir": "hard_layout",
        "category": "correct",
        "expected": "pass",
        "expected_status": "equivalent",
        "error_type": "none",
        "style": "clean",
        "formula_key": "layout_multiline_update",
        "latex": "r = b - A x \\\\ x = x + \\alpha r",
        "cpp": CPP_CG_STEP,
        "note": "两行对齐更新式（残差 + 步进），带中文标注，需逐行配对",
    },

    # ---------------- 边界 ----------------
    {
        "id": "edge_short",
        "suite": "hard_edge",
        "dir": "hard_edge",
        "category": "correct",
        "expected": "pass",
        "expected_status": "equivalent",
        "error_type": "none",
        "style": "tiny",
        "formula_key": "edge_short",
        "latex": "y = a x",
        "cpp": CPP_SHORT,
        "note": "极短公式 + 小字号大留白；考察裁剪与最简公式识别",
    },
    {
        "id": "edge_mlp_chain",
        "suite": "hard_edge",
        "dir": "hard_edge",
        "category": "correct",
        "expected": "pass",
        "expected_status": "equivalent",
        "error_type": "none",
        "style": "clean",
        "formula_key": "edge_mlp_chain",
        "latex": (
            "h_1 = W_1 x + b_1 \\\\ a_1 = \\mathrm{ReLU}(h_1) \\\\"
            " h_2 = W_2 a_1 + b_2 \\\\ y = \\mathrm{softmax}(h_2)"
        ),
        "cpp": CPP_MLP,
        "note": "四行推导链（MLP 前向）；考察长链多行配对",
    },
    {
        "id": "edge_mixed_lang",
        "suite": "hard_edge",
        "dir": "hard_edge",
        "category": "incorrect",
        "expected": "fail",
        "expected_status": "not_symbolically_equivalent",
        "error_type": "sign_error",
        "style": "clean",
        "formula_key": "edge_mixed_lang",
        "latex": "w_{k+1} = w_k - \\eta \\nabla L(w_k)",
        "cpp": CPP_MIXED,
        "note": "中英混排标注；代码用加号（梯度上升），应判不等价",
    },

    # ---------------- 变体表示 ----------------
    {
        "id": "var_sum_sigma",
        "suite": "hard_variant",
        "dir": "hard_variant",
        "category": "correct",
        "expected": "pass",
        "expected_status": "equivalent",
        "error_type": "none",
        "style": "mono_clean",
        "formula_key": "var_sum_sigma",
        "latex": "s = \\sum_{i=0}^{n-1} a_i b_i",
        "cpp": CPP_DOT,
        "note": "直接截图 LaTeX 源码（\\sum 写法），与点积代码等价",
    },
    {
        "id": "var_norm_squared",
        "suite": "hard_variant",
        "dir": "hard_variant",
        "category": "correct",
        "expected": "pass",
        "expected_status": "equivalent",
        "error_type": "none",
        "style": "clean",
        "formula_key": "var_norm_squared",
        "latex": "\\|x\\|_2^2 = \\sum_{i=1}^{n} x_i^2",
        "cpp": CPP_NORM_SQ,
        "note": "范数平方：无开方也等价，考察「缺开方」误报",
    },
    {
        "id": "var_offbyone_variant",
        "suite": "hard_variant",
        "dir": "hard_variant",
        "category": "incorrect",
        "expected": "fail",
        "expected_status": "not_symbolically_equivalent",
        "error_type": "off_by_one",
        "style": "clean",
        "formula_key": "var_offbyone_variant",
        "latex": "s = \\sum_{i=0}^{n-1} a_i",
        "cpp": CPP_SKIP_FIRST,
        "note": "代码跳过首项（i=1 起），与求和定义不符，应判不等价",
    },
    {
        "id": "var_avg_vs_sum",
        "suite": "hard_variant",
        "dir": "hard_variant",
        "category": "incorrect",
        "expected": "fail",
        "expected_status": "not_symbolically_equivalent",
        "error_type": "missing_operation",
        "style": "clean",
        "formula_key": "var_avg_vs_sum",
        "latex": "\\bar{x} = \\frac{1}{n}\\sum_{i=1}^{n} x_i",
        "cpp": CPP_MEAN_SUMLESS,
        "note": "均值定义 vs 只求和（漏 1/n 系数），应判不等价",
    },
]


def render_case(case: dict, formulas: dict[str, Box], force: bool) -> tuple[Path, Path]:
    target = BASE / case["dir"]
    target.mkdir(parents=True, exist_ok=True)
    image_path = target / f"{case['id']}.png"
    cpp_path = target / f"{case['id']}.cpp"
    if force or not image_path.is_file():
        box = formulas[case["formula_key"]]
        style = case["style"]
        if style == "hand":
            img = compose(box, bg=PAPER, margin=80)
            img = style_hand(img, seed=abs(hash(case["id"])) % 10000)
        elif style == "blur":
            img = compose(box, margin=56)
            img = style_blur(img, seed=abs(hash(case["id"])) % 10000)
        elif style == "noise":
            img = compose(box, margin=60)
            img = style_noise(img, seed=abs(hash(case["id"])) % 10000)
        elif style == "tiny":
            # 小公式放在大画布左上角，留大量空白，制造裁剪考验
            img = Image.new("RGB", (1400, 820), "white")
            img.paste(compose(box, margin=40), (90, 120))
        elif style == "mono_clean":
            # 先按白底渲染，再整体反色，模拟深色编辑器里的 LaTeX 源码截图
            img = compose(box, margin=60)
            img = ImageOps.invert(img.convert("RGB"))
        else:
            img = compose(box, margin=60)
        img.save(image_path, format="PNG")
    if force or not cpp_path.is_file():
        cpp_path.write_text(case["cpp"], encoding="utf-8")
    return image_path, cpp_path


def update_annotations(force: bool = False) -> int:
    """把本文件的用例合并进 annotations.json（保留原有条目）。"""
    data = json.loads(ANNOTATIONS.read_text(encoding="utf-8"))
    existing = data.get("cases", [])
    new_ids = {c["id"] for c in CASES}
    kept = [c for c in existing if c.get("id") not in new_ids]
    appended = [
        {
            "id": c["id"],
            "category": c["category"],
            "suite": c["suite"],
            "batch": "stress",
            "image": f"{c['dir']}/{c['id']}.png",
            "cpp": f"{c['dir']}/{c['id']}.cpp",
            "latex": c["latex"],
            "expected": c["expected"],
            "expected_status": c["expected_status"],
            "error_type": c["error_type"],
            "note": c["note"],
        }
        for c in CASES
    ]
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    shutil.copy2(ANNOTATIONS, ANNOTATIONS.with_suffix(f".json.bak.w10_{stamp}"))
    data["cases"] = kept + appended
    data["updated_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
    notes = data.setdefault("notes", [])
    marker = "w10：新增压力测试集（手写/模糊/复杂排版/边界/变体表示）"
    if marker not in notes:
        notes.append(marker)
    ANNOTATIONS.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"[完成] annotations.json 已更新：保留 {len(kept)} 条，新增 {len(appended)} 条")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="生成压力测试用例")
    parser.add_argument("--only", default="", help="只生成指定 id，逗号分隔")
    parser.add_argument("--force", action="store_true", help="覆盖已存在文件")
    parser.add_argument("--list", action="store_true", help="只列出用例")
    parser.add_argument("--update-annotations", action="store_true",
                        help="把用例合并进 annotations.json（先备份）")
    args = parser.parse_args()

    if args.list:
        for case in CASES:
            print(f"{case['suite']:12s} {case['id']:26s} "
                  f"{case['category']:10s} expected={case['expected']:5s} "
                  f"style={case['style']}")
        print(f"共 {len(CASES)} 条")
        return 0

    only = {item.strip() for item in args.only.split(",") if item.strip()}
    unknown = only - {c["id"] for c in CASES}
    if unknown:
        print(f"[错误] 未知用例 id：{sorted(unknown)}", file=sys.stderr)
        return 2
    selected = [c for c in CASES if not only or c["id"] in only]

    formulas = build_formulas()
    for case in selected:
        image_path, cpp_path = render_case(case, formulas, args.force)
        print(f"[完成] {case['dir']}/{case['id']}.png + .cpp")
    print(f"共生成 {len(selected)} 个用例")

    if args.update_annotations:
        return update_annotations(force=args.force)
    return 0


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    raise SystemExit(main())
