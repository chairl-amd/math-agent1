#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""工单2：批量生成基准用例（公式图片 + C++ 代码）。

设计原则：
1. 用例定义集中在 CASES 里，图片与代码由同一份定义生成，避免图文不一致；
2. 公式图使用白底黑字的 Unicode 数学排版（∑ ∂ ∇ √ α η 等），
   与真实截图风格接近，便于视觉 OCR 识别；
3. 支持 --only 只生成指定用例，便于先做单张探针再批量铺开；
4. 已存在的图片默认不覆盖，除非显式传 --force。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError as exc:  # pragma: no cover
    print(f"[失败] 需要 Pillow：{exc}", file=sys.stderr)
    raise SystemExit(2)

BASE = Path(__file__).resolve().parent
FONT_CANDIDATES = [
    Path("C:/Windows/Fonts/cambria.ttc"),
    Path("C:/Windows/Fonts/seguisym.ttf"),
    Path("C:/Windows/Fonts/arial.ttf"),
]
FONT_SIZE = 40
IMAGE_SIZE = (1500, 520)
MARGIN = 70
LINE_SPACING = 26


def load_font() -> "ImageFont.FreeTypeFont | ImageFont.ImageFont":
    """按顺序挑选支持数学符号的字体。"""
    for candidate in FONT_CANDIDATES:
        if candidate.is_file():
            try:
                return ImageFont.truetype(str(candidate), FONT_SIZE)
            except OSError:
                continue
    print("[警告] 未找到可用 TrueType 字体，改用 Pillow 默认字体")
    return ImageFont.load_default(size=FONT_SIZE)


def draw_formula_image(path: Path, lines: list[str],
                       font) -> Path:
    """把若干行公式文本画成白底图片。"""
    image = Image.new("RGB", IMAGE_SIZE, "white")
    draw = ImageDraw.Draw(image)
    y = MARGIN
    for line in lines:
        draw.text((MARGIN, y), line, fill="black", font=font)
        y += FONT_SIZE + LINE_SPACING
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, format="PNG")
    return path


# ---------------------------------------------------------------------------
# 用例定义
# format: dir 目录 | category 统计类别 | expected 期望结论 | formula 图片文本
#         cpp C++ 代码 | note 标注说明
# ---------------------------------------------------------------------------
CASES: list[dict] = [
    # ---------------- correct：图与代码数学等价 ----------------
    {
        "id": "dot_product",
        "dir": "correct",
        "category": "correct",
        "expected": "pass",
        "formula": ["s = \u03a3_i a_i \u00b7 b_i"],
        "cpp": """#include <vector>

double dot(const std::vector<double>& a, const std::vector<double>& b) {
    double s = 0.0;
    for (std::size_t i = 0; i < a.size(); ++i) {
        s += a[i] * b[i];
    }
    return s;
}
""",
        "note": "点积 s=Σ_i a_i·b_i，C++ 单层循环完整遍历",
    },
    {
        "id": "l2_norm",
        "dir": "correct",
        "category": "correct",
        "expected": "pass",
        "formula": ["||x|| = \u221a( \u03a3_i x_i\u00b2 )"],
        "cpp": """#include <cmath>
#include <vector>

double l2_norm(const std::vector<double>& x) {
    double acc = 0.0;
    for (std::size_t i = 0; i < x.size(); ++i) {
        acc += x[i] * x[i];
    }
    return std::sqrt(acc);
}
""",
        "note": "L2 范数，先累加平方再开方",
    },
    {
        "id": "softmax",
        "dir": "correct",
        "category": "correct",
        "expected": "pass",
        "formula": ["s_i = exp(z_i) / \u03a3_j exp(z_j)"],
        "cpp": """#include <cmath>
#include <vector>

std::vector<double> softmax(const std::vector<double>& z) {
    std::vector<double> s(z.size());
    double total = 0.0;
    for (std::size_t j = 0; j < z.size(); ++j) {
        total += std::exp(z[j]);
    }
    for (std::size_t i = 0; i < z.size(); ++i) {
        s[i] = std::exp(z[i]) / total;
    }
    return s;
}
""",
        "note": "softmax：分子逐元素指数，分母全量求和",
    },
    {
        "id": "gradient_descent",
        "dir": "correct",
        "category": "correct",
        "expected": "pass",
        "formula": ["w_{t+1} = w_t - \u03b7 \u00b7 g_t"],
        "cpp": """#include <vector>

void gd_step(std::vector<double>& w,
             const std::vector<double>& g,
             double eta) {
    for (std::size_t i = 0; i < w.size(); ++i) {
        w[i] -= eta * g[i];
    }
}
""",
        "note": "梯度下降更新：沿负梯度方向，步长 η",
    },
    {
        "id": "cg_alpha",
        "dir": "correct",
        "category": "correct",
        "expected": "pass",
        "formula": [
            "\u03b1_k = ( r_k \u00b7 r_k ) / ( p_k \u00b7 A p_k )",
        ],
        "cpp": """#include <vector>

double cg_alpha(const std::vector<double>& r,
                const std::vector<double>& p,
                const std::vector<double>& Ap) {
    double rr = 0.0;
    double pAp = 0.0;
    for (std::size_t i = 0; i < r.size(); ++i) {
        rr += r[i] * r[i];
        pAp += p[i] * Ap[i];
    }
    return rr / pAp;
}
""",
        "note": "共轭梯度步长：残差内积除以 A-内积",
    },

    # ---------------- incorrect：图与代码存在实质差异 ----------------
    {
        "id": "sign_flip",
        "dir": "incorrect",
        "category": "incorrect",
        "expected": "fail",
        "formula": ["w_{t+1} = w_t - \u03b7 \u00b7 g_t"],
        "cpp": """#include <vector>

void step(std::vector<double>& w,
          const std::vector<double>& g,
          double eta) {
    for (std::size_t i = 0; i < w.size(); ++i) {
        w[i] += eta * g[i];
    }
}
""",
        "note": "图是减号（梯度下降），代码写成加号（梯度上升）",
    },
    {
        "id": "off_by_one_sum",
        "dir": "incorrect",
        "category": "incorrect",
        "expected": "fail",
        "formula": ["s = \u03a3_{i=0}^{n-1} a_i"],
        "cpp": """#include <vector>

double sum_except_last(const std::vector<double>& a) {
    double s = 0.0;
    for (std::size_t i = 0; i + 1 < a.size(); ++i) {
        s += a[i];
    }
    return s;
}
""",
        "note": "图上界 n-1（含最后一项），代码少累加最后一项",
    },
    {
        "id": "transpose_index",
        "dir": "incorrect",
        "category": "incorrect",
        "expected": "fail",
        "formula": ["c_{ij} = \u03a3_k a_{ik} b_{kj}"],
        "cpp": """#include <vector>
#include <cstddef>

void matmul(const std::vector<std::vector<double>>& a,
            const std::vector<std::vector<double>>& b,
            std::vector<std::vector<double>>& c) {
    for (std::size_t i = 0; i < a.size(); ++i) {
        for (std::size_t j = 0; j < b.size(); ++j) {
            double acc = 0.0;
            for (std::size_t k = 0; k < b.size(); ++k) {
                acc += a[k][j] * b[k][i];
            }
            c[i][j] = acc;
        }
    }
}
""",
        "note": "代码把 a、b 的下标互换，等价于 A^T B^T 而非 AB",
    },
    {
        "id": "lu_div_index",
        "dir": "incorrect",
        "category": "incorrect",
        "expected": "fail",
        "formula": ["L_{ij} = ( a_{ij} - \u03a3_{k<j} L_{ik} U_{kj} ) / U_{jj}"],
        "cpp": """#include <vector>
#include <cstddef>

void lu_factor(std::vector<std::vector<double>>& a,
               std::vector<std::vector<double>>& L,
               std::vector<std::vector<double>>& U) {
    for (std::size_t i = 0; i < a.size(); ++i) {
        for (std::size_t j = 0; j < a.size(); ++j) {
            double acc = a[i][j];
            for (std::size_t k = 0; k < j; ++k) {
                acc -= L[i][k] * U[k][j];
            }
            if (j > i) {
                U[i][j] = acc;
            } else if (i == j) {
                L[i][i] = 1.0;
                U[i][i] = acc;
            } else {
                L[i][j] = acc / U[i][i];
            }
        }
    }
}
""",
        "note": "代码用 U_ii 作除数，标准定义应除以 U_jj",
    },
    {
        "id": "missing_sqrt",
        "dir": "incorrect",
        "category": "incorrect",
        "expected": "fail",
        "formula": ["d = \u221a( \u03a3_i ( x_i - y_i )\u00b2 )"],
        "cpp": """#include <vector>

double distance(const std::vector<double>& x,
                const std::vector<double>& y) {
    double acc = 0.0;
    for (std::size_t i = 0; i < x.size(); ++i) {
        double d = x[i] - y[i];
        acc += d * d;
    }
    return acc;
}
""",
        "note": "代码返回平方和，缺少最外层开方",
    },

    # ---------------- structural_errors：图与代码都偏离标准定义 ----------------
    {
        "id": "strict_upper_bound",
        "dir": "structural_errors",
        "category": "incorrect",
        "expected": "fail",
        "formula": ["U_{ij} = a_{ij} - \u03a3_{k<i} L_{ik} U_{kj}"],
        "cpp": """#include <vector>
#include <cstddef>

void lu_upper(const std::vector<std::vector<double>>& a,
              const std::vector<std::vector<double>>& L,
              std::vector<std::vector<double>>& U) {
    for (std::size_t i = 0; i < a.size(); ++i) {
        for (std::size_t j = 0; j < a.size(); ++j) {
            double acc = a[i][j];
            for (std::size_t k = 0; k <= i; ++k) {
                acc -= L[i][k] * U[k][j];
            }
            U[i][j] = acc;
        }
    }
}
""",
        "note": "标准定义 k<i，图与代码都用了 k<=i（多算对角线项）",
    },
    {
        "id": "conv_stride",
        "dir": "structural_errors",
        "category": "incorrect",
        "expected": "fail",
        "formula": ["y_i = \u03a3_{k=0}^{2} w_k x_{i+k}"],
        "cpp": """#include <vector>
#include <cstddef>

std::vector<double> conv(const std::vector<double>& x,
                         const std::vector<double>& w) {
    std::vector<double> y;
    for (std::size_t i = 0; i + 2 < x.size(); i += 2) {
        double acc = 0.0;
        for (std::size_t k = 0; k < w.size(); ++k) {
            acc += w[k] * x[i + k];
        }
        y.push_back(acc);
    }
    return y;
}
""",
        "note": "图上步长 1 的卷积，代码每隔 2 个位置采样（stride=2）",
    },
    {
        "id": "jacobi_scope",
        "dir": "structural_errors",
        "category": "incorrect",
        "expected": "fail",
        "formula": ["x_i^{new} = ( b_i - \u03a3_{j\u2260i} a_{ij} x_j ) / a_{ii}"],
        "cpp": """#include <vector>
#include <cstddef>

std::vector<double> jacobi(const std::vector<std::vector<double>>& a,
                           const std::vector<double>& b,
                           const std::vector<double>& x) {
    std::vector<double> next(x.size());
    for (std::size_t i = 0; i < x.size(); ++i) {
        double acc = b[i];
        for (std::size_t j = 0; j < x.size(); ++j) {
            acc -= a[i][j] * x[j];
        }
        next[i] = acc / a[i][i];
    }
    return next;
}
""",
        "note": "标准 Jacobi 求和排除 j=i，代码把 j=i 也减掉",
    },
    {
        "id": "power_iter_norm",
        "dir": "structural_errors",
        "category": "incorrect",
        "expected": "fail",
        "formula": ["v_{k+1} = A v_k / ||A v_k||_2"],
        "cpp": """#include <vector>
#include <cmath>
#include <cstddef>

void power_iter(const std::vector<std::vector<double>>& A,
                std::vector<double>& v) {
    std::vector<double> w(v.size(), 0.0);
    for (std::size_t i = 0; i < v.size(); ++i) {
        for (std::size_t j = 0; j < v.size(); ++j) {
            w[i] += A[i][j] * v[j];
        }
    }
    double norm = 0.0;
    for (std::size_t i = 0; i < w.size(); ++i) {
        norm += w[i];
    }
    for (std::size_t i = 0; i < v.size(); ++i) {
        v[i] = w[i] / norm;
    }
}
""",
        "note": "图用 L2 范数归一化，代码用元素和归一化",
    },
    {
        "id": "pde_residual_scope",
        "dir": "structural_errors",
        "category": "incorrect",
        "expected": "fail",
        "formula": ["u_i^{n+1} = u_i^{n} + \u03b1 \u0394t ( u_{i+1}^{n} - 2u_i^{n} + u_{i-1}^{n} ) / \u0394x\u00b2"],
        "cpp": """#include <vector>
#include <cstddef>

void heat_step(std::vector<double>& u, double alpha,
               double dt, double dx) {
    std::vector<double> next(u.size());
    for (std::size_t i = 1; i + 1 < u.size(); ++i) {
        double lap = u[i + 1] - 2.0 * u[i];
        next[i] = u[i] + alpha * dt * lap / (dx * dx);
    }
    u = next;
}
""",
        "note": "图上二阶差分含 u_{i-1} 项，代码漏掉左邻点",
    },

    # ---------------- pde：偏微分方程（能力边界用例） ----------------
    {
        "id": "heat_1d",
        "dir": "pde",
        "category": "incorrect",
        "expected": "fail",
        "formula": [
            "\u2202u/\u2202t = \u03b1 \u00b7 \u2202\u00b2u/\u2202x\u00b2",
        ],
        "cpp": """#include <vector>
#include <cstddef>

void heat_step(std::vector<double>& u, double alpha,
               double dt, double dx) {
    std::vector<double> next = u;
    for (std::size_t i = 1; i + 1 < u.size(); ++i) {
        double lap = (u[i + 1] - 2.0 * u[i] + u[i - 1]) / (dx * dx);
        next[i] = u[i] + alpha * dt * lap;
    }
    u = next;
}
""",
        "note": "一维热方程显式差分：离散与连续形式语义等价，但 PDE 无法做严格符号证明",
    },
    {
        "id": "wave_1d",
        "dir": "pde",
        "category": "incorrect",
        "expected": "fail",
        "formula": [
            "\u2202\u00b2u/\u2202t\u00b2 = c\u00b2 \u00b7 \u2202\u00b2u/\u2202x\u00b2",
        ],
        "cpp": """#include <vector>
#include <cstddef>

void wave_step(std::vector<double>& u, std::vector<double>& u_prev,
               double c, double dt, double dx) {
    std::vector<double> next = u;
    double r = (c * dt / dx) * (c * dt / dx);
    for (std::size_t i = 1; i + 1 < u.size(); ++i) {
        double lap = u[i + 1] - 2.0 * u[i] + u[i - 1];
        next[i] = 2.0 * u[i] - u_prev[i] + r * lap;
    }
    u_prev = u;
    u = next;
}
""",
        "note": "一维波动方程中心差分：同样属于 PDE 能力边界",
    },
    {
        "id": "poisson_2d",
        "dir": "pde",
        "category": "incorrect",
        "expected": "fail",
        "formula": ["\u2207\u00b2\u03c6 = \u03c1 / \u03b5\u2080"],
        "cpp": """#include <vector>
#include <cstddef>

void poisson_sweep(std::vector<std::vector<double>>& phi,
                   const std::vector<std::vector<double>>& rho,
                   double eps0, double h) {
    for (std::size_t i = 1; i + 1 < phi.size(); ++i) {
        for (std::size_t j = 1; j + 1 < phi[i].size(); ++j) {
            double rhs = rho[i][j] / eps0;
            double lap = (phi[i + 1][j] + phi[i - 1][j]
                          + phi[i][j + 1] + phi[i][j - 1]
                          - 4.0 * phi[i][j]) / (h * h);
            phi[i][j] += 0.25 * (h * h * rhs - lap * h * h) + lap * h * h * 0.0;
        }
    }
}
""",
        "note": "二维泊松方程 Jacobi 扫描；代码写法与离散残差有关，PDE 不作严格证明",
    },
    {
        "id": "ns_incompressible",
        "dir": "pde",
        "category": "incorrect",
        "expected": "fail",
        "formula": [
            "\u2202u/\u2202t + (u\u00b7\u2207)u = -\u2207p/\u03c1 + \u03bd\u2207\u00b2u",
            "\u2207\u00b7u = 0",
        ],
        "cpp": """#include <vector>
#include <cstddef>

void ns_momentum(std::vector<double>& ux, std::vector<double>& uy,
                 const std::vector<double>& p,
                 double rho, double nu, double dt, double dx) {
    std::vector<double> next_x = ux;
    std::vector<double> next_y = uy;
    for (std::size_t i = 1; i + 1 < ux.size(); ++i) {
        double adv = ux[i] * (ux[i + 1] - ux[i - 1]) / (2.0 * dx);
        double diff = nu * (ux[i + 1] - 2.0 * ux[i] + ux[i - 1]) / (dx * dx);
        double grad_p = (p[i + 1] - p[i - 1]) / (2.0 * dx);
        next_x[i] = ux[i] + dt * (-adv - grad_p / rho + diff);
        next_y[i] = uy[i];
    }
    ux = next_x;
    uy = next_y;
}
""",
        "note": "不可压 N-S 动量方程（忽略对流项 y 分量）；PDE 需人工确认",
    },

    # ---------------- matrix_chain：矩阵链乘 ----------------
    {
        "id": "chain3",
        "dir": "matrix_chain",
        "category": "correct",
        "expected": "pass",
        "formula": ["D = A B C", "d_{ij} = \u03a3_k \u03a3_l a_{ik} b_{kl} c_{lj}"],
        "cpp": """#include <vector>
#include <cstddef>

using Mat = std::vector<std::vector<double>>;

Mat mul(const Mat& x, const Mat& y) {
    Mat z(x.size(), std::vector<double>(y[0].size(), 0.0));
    for (std::size_t i = 0; i < x.size(); ++i) {
        for (std::size_t j = 0; j < y[0].size(); ++j) {
            for (std::size_t k = 0; k < y.size(); ++k) {
                z[i][j] += x[i][k] * y[k][j];
            }
        }
    }
    return z;
}

Mat chain3(const Mat& a, const Mat& b, const Mat& c) {
    return mul(mul(a, b), c);
}
""",
        "note": "三个矩阵连乘 (AB)C，按行-列-内积三重循环展开",
    },
    {
        "id": "chain4",
        "dir": "matrix_chain",
        "category": "correct",
        "expected": "pass",
        "formula": ["E = A B C D"],
        "cpp": """#include <vector>
#include <cstddef>

using Mat = std::vector<std::vector<double>>;

Mat mul(const Mat& x, const Mat& y) {
    Mat z(x.size(), std::vector<double>(y[0].size(), 0.0));
    for (std::size_t i = 0; i < x.size(); ++i) {
        for (std::size_t j = 0; j < y[0].size(); ++j) {
            for (std::size_t k = 0; k < y.size(); ++k) {
                z[i][j] += x[i][k] * y[k][j];
            }
        }
    }
    return z;
}

Mat chain4(const Mat& a, const Mat& b, const Mat& c, const Mat& d) {
    return mul(mul(mul(a, b), c), d);
}
""",
        "note": "四个矩阵左结合连乘 (((AB)C)D)",
    },
    {
        "id": "assoc_paren",
        "dir": "matrix_chain",
        "category": "correct",
        "expected": "pass",
        "formula": ["( A B ) C = A ( B C )"],
        "cpp": """#include <vector>
#include <cstddef>

using Mat = std::vector<std::vector<double>>;

Mat mul(const Mat& x, const Mat& y) {
    Mat z(x.size(), std::vector<double>(y[0].size(), 0.0));
    for (std::size_t i = 0; i < x.size(); ++i) {
        for (std::size_t j = 0; j < y[0].size(); ++j) {
            for (std::size_t k = 0; k < y.size(); ++k) {
                z[i][j] += x[i][k] * y[k][j];
            }
        }
    }
    return z;
}

bool same(const Mat& a, const Mat& b, const Mat& c) {
    Mat left = mul(mul(a, b), c);
    Mat right = mul(a, mul(b, c));
    for (std::size_t i = 0; i < left.size(); ++i) {
        for (std::size_t j = 0; j < left[i].size(); ++j) {
            if (left[i][j] != right[i][j]) return false;
        }
    }
    return true;
}
""",
        "note": "矩阵乘法结合律：(AB)C 与 A(BC) 等价",
    },
    {
        "id": "chain_wrong_order",
        "dir": "matrix_chain",
        "category": "incorrect",
        "expected": "fail",
        "formula": ["D = A B C"],
        "cpp": """#include <vector>
#include <cstddef>

using Mat = std::vector<std::vector<double>>;

Mat mul(const Mat& x, const Mat& y) {
    Mat z(x.size(), std::vector<double>(y[0].size(), 0.0));
    for (std::size_t i = 0; i < x.size(); ++i) {
        for (std::size_t j = 0; j < y[0].size(); ++j) {
            for (std::size_t k = 0; k < y.size(); ++k) {
                z[i][j] += x[i][k] * y[k][j];
            }
        }
    }
    return z;
}

Mat chain3(const Mat& a, const Mat& b, const Mat& c) {
    return mul(mul(b, c), a);
}
""",
        "note": "代码把乘法顺序写成 (BC)A，一般不等于 ABC",
    },
]


def write_case(case: dict, font, force: bool) -> tuple[Path, Path]:
    """写出单个用例的图片与 C++ 文件。"""
    target_dir = BASE / case["dir"]
    image_path = target_dir / f"{case['id']}.png"
    cpp_path = target_dir / f"{case['id']}.cpp"
    target_dir.mkdir(parents=True, exist_ok=True)
    if force or not image_path.is_file():
        draw_formula_image(image_path, case["formula"], font)
    if force or not cpp_path.is_file():
        cpp_path.write_text(case["cpp"], encoding="utf-8")
    return image_path, cpp_path


def main() -> int:
    parser = argparse.ArgumentParser(description="生成基准用例图片与代码")
    parser.add_argument("--only", default="", help="只生成指定 id，逗号分隔")
    parser.add_argument("--force", action="store_true", help="覆盖已存在文件")
    parser.add_argument("--list", action="store_true", help="只列出用例")
    args = parser.parse_args()

    if args.list:
        for case in CASES:
            print(f"{case['dir']:20s} {case['id']:24s} "
                  f"{case['category']:10s} expected={case['expected']}")
        return 0

    only_ids = {item.strip() for item in args.only.split(",") if item.strip()}
    selected = [case for case in CASES
                if not only_ids or case["id"] in only_ids]
    if only_ids:
        unknown = only_ids - {case["id"] for case in CASES}
        if unknown:
            print(f"[错误] 未知用例 id：{sorted(unknown)}", file=sys.stderr)
            return 2

    font = load_font()
    generated = []
    for case in selected:
        image_path, cpp_path = write_case(case, font, args.force)
        generated.append(case["id"])
        print(f"[完成] {case['dir']}/{case['id']}.png + .cpp")
    print(f"共生成 {len(generated)} 个用例")
    return 0


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    raise SystemExit(main())
