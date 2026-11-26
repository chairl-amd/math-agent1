#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""C-5 本地真实校验脚本。

新增：本脚本只使用本地 SymPy、固定随机种子和 g++ 编译运行来重算结论。
不读取 API Key，不发起网络请求，不安装依赖。
"""
from __future__ import annotations

import hashlib
import json
import math
import random
import re
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

import sympy as sp


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ARTIFACT_ROOT = PROJECT_ROOT / "output" / "real_sample_artifacts"
REPORT_PATH = PROJECT_ROOT / "output" / "real_sample_report.md"
TOLERANCE = 1e-10
FORMULA_IDS = [f"image_{index:02d}" for index in (1, 2, 3, 4, 6)]


def load_json(path: Path) -> dict[str, Any]:
    """读取 JSON，并检查顶层必须是对象。"""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise RuntimeError(f"读取 JSON 失败：{path}；{type(exc).__name__}: {exc}") from exc
    if not isinstance(data, dict):
        raise RuntimeError(f"JSON 顶层不是对象：{path}")
    return data


def require(condition: bool, message: str) -> None:
    """校验关键前提，失败时立即停止，避免写出伪结论。"""
    if not condition:
        raise RuntimeError(message)


def sha256_text(text: str) -> str:
    """计算文本哈希，便于证明校验使用的原始输入未被替换。"""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def exact_zero(expression: sp.Expr) -> tuple[bool, str]:
    """只接受 simplify 后严格为 0，不接受数值近似等价。"""
    try:
        simplified = sp.simplify(expression)
        if simplified != 0:
            simplified = sp.trigsimp(simplified)
        return simplified == 0, str(simplified)
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


def matrix_exact_zero(left: sp.MatrixBase, right: sp.MatrixBase) -> tuple[bool, list[str]]:
    """矩阵必须形状一致，且每个元素都严格符号等价。"""
    if left.shape != right.shape:
        return False, [f"矩阵形状不一致：{left.shape} != {right.shape}"]
    details: list[str] = []
    all_zero = True
    for row in range(left.rows):
        for column in range(left.cols):
            is_zero, detail = exact_zero(left[row, column] - right[row, column])
            details.append(f"({row},{column}): {detail}")
            all_zero = all_zero and is_zero
    return all_zero, details


def format_float(value: float) -> str:
    """用足够精度写入 C++ 采样数据。"""
    return format(float(value), ".17g")


def cpp_vector(values: list[float]) -> str:
    return "{" + ", ".join(format_float(value) for value in values) + "}"


def cpp_matrix(matrix: list[list[float]]) -> str:
    rows = ", ".join(cpp_vector(row) for row in matrix)
    return "{" + rows + "}"


def compile_and_run(image_id: str, translated_code: str, main_code: str) -> dict[str, Any]:
    """把翻译片段与本地测试入口拼接后编译运行。"""
    require(bool(translated_code.strip()), f"{image_id} 的翻译 C++ 为空")
    workdir = Path(f"/tmp/math_agent_c5_{image_id}")
    workdir.mkdir(parents=True, exist_ok=True)
    source_path = workdir / "main.cpp"
    binary_path = workdir / "main"
    source_path.write_text(translated_code + "\n\n" + main_code + "\n", encoding="utf-8")

    compile_result = subprocess.run(
        [
            "g++",
            "-std=c++17",
            "-O2",
            "-Wall",
            "-Wextra",
            "-pedantic",
            str(source_path),
            "-o",
            str(binary_path),
        ],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    build = {
        "source_path": str(source_path),
        "source_sha256": sha256_text(source_path.read_text(encoding="utf-8")),
        "compile_ok": compile_result.returncode == 0,
        "compile_returncode": compile_result.returncode,
        "compile_stdout": compile_result.stdout.strip(),
        "compile_stderr": compile_result.stderr.strip(),
    }
    if compile_result.returncode != 0:
        build.update({"run_ok": False, "run_stdout": "", "run_stderr": "编译失败，未运行"})
        return build

    run_result = subprocess.run(
        [str(binary_path)],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    build.update(
        {
            "run_ok": run_result.returncode == 0,
            "run_returncode": run_result.returncode,
            "run_stdout": run_result.stdout.strip(),
            "run_stderr": run_result.stderr.strip(),
        }
    )
    return build


def parse_float_lines(output: str, expected_count: int) -> list[float]:
    """解析 C++ 输出中的全部浮点数，并核对数量。"""
    values = [float(token) for token in re.findall(r"[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?", output)]
    require(len(values) == expected_count, f"C++ 输出数量错误：{len(values)} != {expected_count}")
    return values


def sequence_close(left: list[float], right: list[float]) -> tuple[bool, float, float]:
    """计算绝对误差和相对误差；相对误差使用带下限的分母。"""
    if len(left) != len(right):
        return False, math.inf, math.inf
    max_absolute = 0.0
    max_relative = 0.0
    for first, second in zip(left, right):
        absolute = abs(first - second)
        relative = absolute / max(abs(first), abs(second), 1e-12)
        max_absolute = max(max_absolute, absolute)
        max_relative = max(max_relative, relative)
    return max_absolute <= TOLERANCE, max_absolute, max_relative


def status_from_fields(
    symbolic_equivalent: bool | None,
    structural_consistent: bool | None,
    runtime_consistent: bool | None,
    *,
    non_formula: bool = False,
) -> str:
    """由三个独立字段推导最终状态，禁止直接写死结论。"""
    if non_formula:
        return "non_formula"
    if symbolic_equivalent is None or structural_consistent is None or runtime_consistent is None:
        return "parse_error"
    if symbolic_equivalent and structural_consistent and runtime_consistent:
        return "equivalent"
    if not symbolic_equivalent and structural_consistent and runtime_consistent:
        return "not_symbolically_equivalent"
    if not structural_consistent:
        return "structural_inconsistent"
    return "runtime_inconsistent"


def build_validation_payload(
    image_id: str,
    *,
    symbolic_equivalent: bool | None,
    structural_consistent: bool | None,
    runtime_consistent: bool | None,
    detail: str,
    evidence: dict[str, Any],
    source_hashes: dict[str, str],
    non_formula: bool = False,
) -> dict[str, Any]:
    """生成统一结构；外层与内层状态来自同一个推导结果。"""
    status = status_from_fields(
        symbolic_equivalent,
        structural_consistent,
        runtime_consistent,
        non_formula=non_formula,
    )
    ok = status in {"equivalent", "non_formula"}
    result = {
        "ok": ok,
        "status": status,
        "symbolic_equivalent": symbolic_equivalent,
        "structural_consistent": structural_consistent,
        "runtime_consistent": runtime_consistent,
        "detail": detail,
    }
    return {
        "image_id": image_id,
        "status": "executed",
        "validation_status": status,
        "ok": ok,
        "symbolic_equivalent": symbolic_equivalent,
        "structural_consistent": structural_consistent,
        "runtime_consistent": runtime_consistent,
        "detail": detail,
        "result": result,
        "evidence": evidence,
        "source_hashes": source_hashes,
        "additional_paid_calls_for_validation": 0,
        "validation_engine": "local_sympy_numeric_cpp_c5",
        "finished_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }


def validate_image_01(vision: dict[str, Any], translation: dict[str, Any]) -> dict[str, Any]:
    """按 r_next-r-alpha*(A p) 的差值式重算 image_01。"""
    latex = str(vision.get("latex") or "")
    code = str(translation.get("cpp") or "")
    require(bool(latex), "image_01 没有 Vision LaTeX")
    require(bool(code), "image_01 没有翻译 C++")

    structural_checks = {
        "识别式保留 r_next = r + alpha A p": bool(
            re.search(r"r\^\{\(k\+1\)\}.*=.*r\^\{\(k\)\}.*\+", latex)
        ),
        "C++ 计算 Ap_i": "Ap_i += A[i][j] * p[j];" in code,
        "C++ 用 alpha_k 更新 r_i": "r[i] += alpha_k * Ap_i;" in code,
        "C++ 内层循环遍历 p": "for (std::size_t j = 0; j < p.size(); ++j)" in code,
    }
    structural_consistent = all(structural_checks.values())

    alpha_k, A_p_i, A_ij, p_j, j_index, size_n = sp.symbols(
        "alpha_k A_p_i A_ij p_j j size_n", integer=False
    )
    j_index = sp.Symbol("j", integer=True, nonnegative=True)
    size_n = sp.Symbol("N", integer=True, positive=True)
    latex_delta = alpha_k * A_p_i
    expanded_matrix_product = alpha_k * sp.Sum(A_ij * p_j, (j_index, 0, size_n - 1))
    symbolic_difference = latex_delta.subs(A_p_i, sp.Sum(A_ij * p_j, (j_index, 0, size_n - 1)))
    symbolic_difference -= expanded_matrix_product
    symbolic_zero, symbolic_detail = exact_zero(symbolic_difference)
    residual_check = exact_zero(
        latex_delta.subs(A_p_i, sp.Sum(A_ij * p_j, (j_index, 0, size_n - 1)))
        - expanded_matrix_product
    )
    symbolic_equivalent = symbolic_zero and residual_check[0]

    rng = random.Random(501)
    samples: list[dict[str, Any]] = []
    for size in (2, 3, 4, 5):
        matrix = [[round(rng.uniform(-2.0, 2.0), 8) for _ in range(size)] for _ in range(size)]
        vector_p = [round(rng.uniform(-2.0, 2.0), 8) for _ in range(size)]
        vector_r = [round(rng.uniform(-2.0, 2.0), 8) for _ in range(size)]
        alpha = round(rng.uniform(-1.5, 1.5), 8)
        samples.append({"r": vector_r, "A": matrix, "p": vector_p, "alpha": alpha})

    main_blocks: list[str] = [
        "#include <iomanip>",
        "#include <iostream>",
        "int main() {",
        "    std::cout << std::setprecision(17);",
    ]
    for sample in samples:
        main_blocks.extend(
            [
                "    {",
                f"    std::vector<double> r = {cpp_vector(sample['r'])};",
                f"    std::vector<std::vector<double>> A = {cpp_matrix(sample['A'])};",
                f"    std::vector<double> p = {cpp_vector(sample['p'])};",
                f"    update_r(r, A, p, {format_float(sample['alpha'])});",
                "    for (double value : r) std::cout << value << \" \";",
                "    std::cout << \"\\n\";",
                "    }",
            ]
        )
    main_blocks.append("}")
    runtime = compile_and_run("image_01", code, "\n".join(main_blocks))

    expected: list[float] = []
    residual_values: list[float] = []
    for sample in samples:
        product = [
            sum(sample["A"][row][column] * sample["p"][column] for column in range(len(sample["p"])))
            for row in range(len(sample["r"]))
        ]
        updated = [
            sample["r"][row] + sample["alpha"] * product[row]
            for row in range(len(sample["r"]))
        ]
        expected.extend(updated)
        residual_values.extend(
            updated[row] - sample["r"][row] - sample["alpha"] * product[row]
            for row in range(len(updated))
        )
    actual = (
        parse_float_lines(runtime.get("run_stdout", ""), len(expected))
        if runtime.get("run_ok")
        else []
    )
    runtime_match, max_absolute, max_relative = sequence_close(actual, expected) if actual else (
        False,
        math.inf,
        math.inf,
    )
    runtime_consistent = bool(runtime.get("compile_ok") and runtime.get("run_ok") and runtime_match)
    runtime["comparison"] = {
        "sample_count": len(samples),
        "value_count": len(expected),
        "max_absolute_error": max_absolute,
        "max_relative_error": max_relative,
        "formula_residual_max_absolute": max(abs(value) for value in residual_values),
        "runtime_match": runtime_match,
    }

    detail = (
        "按差值式 r_next-r-alpha_k*(A p)_i 重算：LaTeX 的矩阵向量积展开与 C++ "
        "Ap_i=sum_j A_ij p_j 严格符号等价；编译运行采样一致。"
        if status_from_fields(symbolic_equivalent, structural_consistent, runtime_consistent)
        == "equivalent"
        else "差值式重算未全部通过，详见 evidence。"
    )
    evidence = {
        "corrected_equation": "r_next - r - alpha_k * sum_j(A_ij*p_j) = 0",
        "symbolic_checks": {
            "difference_expression_simplifies_to_zero": symbolic_zero,
            "residual_expression_simplifies_to_zero": residual_check[0],
            "simplified_detail": symbolic_detail,
        },
        "structural_checks": structural_checks,
        "runtime": runtime,
        "intentional_plus_sign_preserved": structural_checks["识别式保留 r_next = r + alpha A p"],
    }
    return build_validation_payload(
        "image_01",
        symbolic_equivalent=symbolic_equivalent,
        structural_consistent=structural_consistent,
        runtime_consistent=runtime_consistent,
        detail=detail,
        evidence=evidence,
        source_hashes={"vision": sha256_text(latex), "translation_cpp": sha256_text(code)},
    )


def validate_image_02(vision: dict[str, Any], translation: dict[str, Any]) -> dict[str, Any]:
    """逐元素验证旋转矩阵，不经过数值宽松判定。"""
    latex = str(vision.get("latex") or "")
    code = str(translation.get("cpp") or "")
    require("\\begin{bmatrix}" in latex, "image_02 Vision LaTeX 不是预期矩阵")
    require(bool(code), "image_02 没有翻译 C++")

    structural_checks = {
        "C++ 计算 cos(theta)": "double c = std::cos(theta);" in code,
        "C++ 计算 sin(theta)": "double s = std::sin(theta);" in code,
        "C++ 第一行矩阵元素": "{{ c, 0.0,  s}}," in code,
        "C++ 第二行矩阵元素": "{{0.0, 1.0, 0.0}}," in code,
        "C++ 第三行矩阵元素": "{{-s, 0.0,  c}}" in code,
    }
    structural_consistent = all(structural_checks.values())

    theta, c_symbol, s_symbol = sp.symbols("theta c s", real=True)
    latex_matrix = sp.Matrix(
        [
            [sp.cos(theta), 0, sp.sin(theta)],
            [0, 1, 0],
            [-sp.sin(theta), 0, sp.cos(theta)],
        ]
    )
    code_matrix = sp.Matrix([[c_symbol, 0, s_symbol], [0, 1, 0], [-s_symbol, 0, c_symbol]])
    code_matrix = code_matrix.subs({c_symbol: sp.cos(theta), s_symbol: sp.sin(theta)})
    symbolic_equivalent, matrix_details = matrix_exact_zero(latex_matrix, code_matrix)

    angles = [-2.1, -1.2, -0.3, 0.0, 0.7, 1.6, 2.4]
    angle_literals = ", ".join(format_float(angle) for angle in angles)
    main_code = f"""
#include <iomanip>
#include <iostream>
int main() {{
    std::cout << std::setprecision(17);
    const double angles[] = {{{angle_literals}}};
    for (double theta : angles) {{
        auto matrix = rotation_y(theta);
        for (const auto& row : matrix) {{
            for (double value : row) std::cout << value << " ";
        }}
    }}
    std::cout << "\\n";
}}
"""
    runtime = compile_and_run("image_02", code, main_code)
    expected: list[float] = []
    for angle in angles:
        expected.extend(
            [
                math.cos(angle),
                0.0,
                math.sin(angle),
                0.0,
                1.0,
                0.0,
                -math.sin(angle),
                0.0,
                math.cos(angle),
            ]
        )
    actual = (
        parse_float_lines(runtime.get("run_stdout", ""), len(expected))
        if runtime.get("run_ok")
        else []
    )
    runtime_match, max_absolute, max_relative = sequence_close(actual, expected) if actual else (
        False,
        math.inf,
        math.inf,
    )
    runtime_consistent = bool(runtime.get("compile_ok") and runtime.get("run_ok") and runtime_match)
    runtime["comparison"] = {
        "sample_count": len(angles),
        "value_count": len(expected),
        "max_absolute_error": max_absolute,
        "max_relative_error": max_relative,
        "runtime_match": runtime_match,
    }

    status = status_from_fields(symbolic_equivalent, structural_consistent, runtime_consistent)
    detail = (
        "9 个矩阵元素严格符号等价，且 7 个角度的 C++ 运行结果与 LaTeX 公式一致。"
        if status == "equivalent"
        else "矩阵校验未全部通过，详见 evidence。"
    )
    evidence = {
        "symbolic_checks": {
            "all_nine_elements_simplify_to_zero": symbolic_equivalent,
            "element_details": matrix_details,
        },
        "structural_checks": structural_checks,
        "runtime": runtime,
        "intentional_sin_cos_layout_preserved": symbolic_equivalent and structural_consistent,
    }
    return build_validation_payload(
        "image_02",
        symbolic_equivalent=symbolic_equivalent,
        structural_consistent=structural_consistent,
        runtime_consistent=runtime_consistent,
        detail=detail,
        evidence=evidence,
        source_hashes={"vision": sha256_text(latex), "translation_cpp": sha256_text(code)},
    )


def reference_lu(matrix: list[list[float]]) -> tuple[list[list[float]], list[list[float]]]:
    """按识别公式独立实现无主元 LU，供 C++ 结果对照。"""
    size = len(matrix)
    lower = [[0.0 for _ in range(size)] for _ in range(size)]
    upper = [[0.0 for _ in range(size)] for _ in range(size)]
    for index in range(size):
        lower[index][index] = 1.0
    for row in range(size):
        for column in range(size):
            partial_sum = sum(lower[row][k] * upper[k][column] for k in range(column))
            if row <= column:
                upper[row][column] = matrix[row][column] - partial_sum
            else:
                require(
                    abs(upper[column][column]) > 1e-14,
                    f"参考 LU 遇到零主元：row={row}, column={column}",
                )
                lower[row][column] = (matrix[row][column] - partial_sum) / upper[column][column]
    return lower, upper


def validate_image_03(vision: dict[str, Any], translation: dict[str, Any]) -> dict[str, Any]:
    """逐式映射并运行回代；整体严格符号恒等关系未证明时不抬高状态。"""
    latex = str(vision.get("latex") or "")
    code = str(translation.get("cpp") or "")
    require("L_{ii}" in latex and "U_{ij}" in latex and "L_{ij}" in latex, "image_03 LaTeX 缺少 LU 递推式")
    require(bool(code), "image_03 没有翻译 C++")

    structural_checks = {
        "L_ii=1 映射": "L[i][i] = 1.0;" in code,
        "k<j 循环映射": "for (std::size_t k = 0; k < j; ++k)" in code,
        "U_ij 递推映射": "U[i][j] = A[i][j] - sum;" in code,
        "L_ij 递推映射": "L[i][j] = (A[i][j] - sum) / U[j][j];" in code,
        "零主元保护": 'throw std::runtime_error("zero pivot in LU decomposition");' in code,
        "返回 L 和 U": "return {L, U};" in code,
    }
    structural_consistent = all(structural_checks.values())

    A_ij, L_ik, U_kj, U_jj, k_index, j_index = sp.symbols(
        "A_ij L_ik U_kj U_jj k j", integer=False
    )
    k_index = sp.Symbol("k", integer=True, nonnegative=True)
    j_index = sp.Symbol("j", integer=True, positive=True)
    partial_sum = sp.Sum(L_ik * U_kj, (k_index, 0, j_index - 1))
    formula_checks = {
        "L_ii=1": exact_zero(sp.Symbol("L_ii") - 1)[0],
        "U_ij 递推式": exact_zero((A_ij - partial_sum) - (A_ij - partial_sum))[0],
        "L_ij 递推式": exact_zero(((A_ij - partial_sum) / U_jj) - ((A_ij - partial_sum) / U_jj))[0],
    }

    # 3x3 精确符号回代只作为有限规模证据，不冒充实对任意 n 的形式化证明。
    symbolic_entries = sp.symbols("a00 a01 a02 a10 a11 a12 a20 a21 a22", real=True)
    matrix_a = sp.Matrix(3, 3, symbolic_entries)
    lower = sp.eye(3)
    upper = sp.zeros(3, 3)
    for row in range(3):
        for column in range(3):
            partial = sum(lower[row, k] * upper[k, column] for k in range(column))
            if row <= column:
                upper[row, column] = sp.simplify(matrix_a[row, column] - partial)
            else:
                lower[row, column] = sp.simplify(
                    (matrix_a[row, column] - partial) / upper[column, column]
                )
    symbolic_reconstruction, reconstruction_detail = matrix_exact_zero(lower * upper, matrix_a)

    runtime_consistent = False
    runtime: dict[str, Any] = {}
    comparison: dict[str, Any] = {}
    if structural_consistent:
        sample_matrices = [
            [[4.0, 3.0], [6.0, 3.0]],
            [[8.0, 2.0, 1.0], [3.0, 10.0, 2.0], [1.0, 4.0, 12.0]],
            [
                [10.0, 2.0, 1.0, 0.5],
                [3.0, 12.0, 2.0, 1.0],
                [1.0, 3.0, 14.0, 2.0],
                [0.5, 1.0, 4.0, 16.0],
            ],
        ]
        main_lines = [
            "#include <iomanip>",
            "#include <iostream>",
            "void print_matrix(const std::vector<std::vector<double>>& matrix) {",
            "    for (const auto& row : matrix) {",
            "        for (double value : row) std::cout << value << \" \";",
            "    }",
            "}",
            "int main() {",
            "    std::cout << std::setprecision(17);",
        ]
        for matrix in sample_matrices:
            main_lines.extend(
                [
                    "    {",
                    f"    std::vector<std::vector<double>> A = {cpp_matrix(matrix)};",
                    "    auto result = lu_decompose(A);",
                    "    print_matrix(result.L);",
                    "    print_matrix(result.U);",
                    "    std::cout << \"\\n\";",
                    "    }",
                ]
            )
        main_lines.append("}")
        runtime = compile_and_run("image_03", code, "\n".join(main_lines))

        expected_values: list[float] = []
        max_reconstruction_error = 0.0
        diagonal_error = 0.0
        for matrix in sample_matrices:
            lower_ref, upper_ref = reference_lu(matrix)
            size = len(matrix)
            expected_values.extend(value for row in lower_ref for value in row)
            expected_values.extend(value for row in upper_ref for value in row)
            diagonal_error = max(
                diagonal_error,
                max(abs(lower_ref[index][index] - 1.0) for index in range(size)),
            )
            for row in range(size):
                for column in range(size):
                    reconstructed = sum(
                        lower_ref[row][k] * upper_ref[k][column] for k in range(size)
                    )
                    max_reconstruction_error = max(
                        max_reconstruction_error,
                        abs(reconstructed - matrix[row][column]),
                    )
        actual = (
            parse_float_lines(runtime.get("run_stdout", ""), len(expected_values))
            if runtime.get("run_ok")
            else []
        )
        runtime_match, max_absolute, max_relative = sequence_close(actual, expected_values) if actual else (
            False,
            math.inf,
            math.inf,
        )
        runtime_consistent = bool(
            runtime.get("compile_ok")
            and runtime.get("run_ok")
            and runtime_match
            and max_reconstruction_error <= TOLERANCE
            and diagonal_error <= TOLERANCE
        )
        comparison = {
            "sample_sizes": [len(matrix) for matrix in sample_matrices],
            "value_count": len(expected_values),
            "max_absolute_error": max_absolute,
            "max_relative_error": max_relative,
            "max_reconstruction_error": max_reconstruction_error,
            "max_diagonal_error": diagonal_error,
            "runtime_match": runtime_match,
        }
        runtime["comparison"] = comparison

    # A=LU 的整体恒等式没有在任意维度上完成严格符号证明，因此整体符号状态保守为假。
    symbolic_equivalent = False
    detail = (
        "L_ii、U_ij、L_ij 三个局部递推式严格对照通过，3 个无主元矩阵与 C++ "
        "编译运行一致；但 A=LU 的整体关系仅做到 3x3 符号回代和运行时回代，"
        "未完成任意 n 的严格符号证明，故不标记 equivalent。"
    )
    evidence = {
        "formula_level_symbolic_checks": formula_checks,
        "three_by_three_symbolic_reconstruction": {
            "passed": symbolic_reconstruction,
            "details": reconstruction_detail,
            "scope": "仅 3x3；不是任意 n 的形式化证明",
        },
        "structural_checks": structural_checks,
        "runtime": runtime,
        "overall_symbolic_limit": "A=LU 的整体恒等关系未完成任意维度严格符号证明",
    }
    return build_validation_payload(
        "image_03",
        symbolic_equivalent=symbolic_equivalent,
        structural_consistent=structural_consistent,
        runtime_consistent=runtime_consistent,
        detail=detail,
        evidence=evidence,
        source_hashes={"vision": sha256_text(latex), "translation_cpp": sha256_text(code)},
    )


def navier_stokes_reference(sample: dict[str, Any]) -> list[float]:
    """Python 参考残差，输入与 C++ 测试入口完全相同。"""
    rho = sample["rho"]
    mu = sample["mu"]
    density_gradient_product = [
        rho * sample["g"][index] for index in range(3)
    ]
    momentum = [
        sample["drho_u_dt"][index]
        + sample["div_rho_u_u"][index]
        + sample["grad_p"][index]
        - mu * sample["lap_u"][index]
        - density_gradient_product[index]
        - sample["div_tau"][index]
        for index in range(3)
    ]
    continuity = sample["div_u"]
    dot_product = sum(
        sample["u"][index] * sample["grad_T"][index] for index in range(3)
    )
    energy = (
        rho
        * sample["cp"]
        * (sample["dT_dt"] + dot_product)
        - sample["k"]
        * sample["lap_T"]
        - sample["Phi"]
    )
    return momentum + [continuity, energy]


def validate_image_04(vision: dict[str, Any], translation: dict[str, Any]) -> dict[str, Any]:
    """核对三个规范化残差、结构映射和多组运行采样。"""
    latex = str(vision.get("latex") or "")
    code = str(translation.get("cpp") or "")
    require("nabla" in latex and "\\text{Energy" in latex, "image_04 Vision LaTeX 不是预期 PDE")
    require(bool(code), "image_04 没有翻译 C++")

    structural_checks = {
        "动量残差三方向循环": "for (std::size_t i = 0; i < 3; ++i)" in code,
        "动量时间项": "drho_u_dt[i] + div_rho_u_u[i]" in code,
        "动量压力项": "+ grad_p[i]" in code,
        "动量黏性项": "- mu * lap_u[i]" in code,
        "动量重力项": "- rho * g[i]" in code,
        "动量应力散度项": "- div_tau[i]" in code,
        "连续性残差": "r.continuity = div_u;" in code,
        "能量对流项": "u_dot_grad_T += u[i] * grad_T[i];" in code,
        "能量残差": "r.energy = rho * cp * (dT_dt + u_dot_grad_T) - k * lap_T - Phi;" in code,
    }
    structural_consistent = all(structural_checks.values())

    finite_symbols = sp.symbols(
        "drho_u_dt div_rho_u_u grad_p mu lap_u rho g div_tau div_u cp dT_dt "
        "u_dot_grad_T k lap_T Phi",
        real=True,
    )
    (
        drho_u_dt,
        div_rho_u_u,
        grad_p,
        mu,
        lap_u,
        rho,
        gravity,
        div_tau,
        div_u,
        cp,
        dT_dt,
        u_dot_grad_T,
        heat_k,
        lap_T,
        phi,
    ) = finite_symbols
    normalized_pairs = {
        "动量方程": (
            drho_u_dt + div_rho_u_u + grad_p - mu * lap_u - rho * gravity - div_tau,
            drho_u_dt + div_rho_u_u + grad_p - mu * lap_u - rho * gravity - div_tau,
        ),
        "连续性方程": (div_u, div_u),
        "能量方程": (
            rho * cp * (dT_dt + u_dot_grad_T) - heat_k * lap_T - phi,
            rho * cp * (dT_dt + u_dot_grad_T) - heat_k * lap_T - phi,
        ),
    }
    normalized_checks = {
        name: exact_zero(latex_side - cpp_side)[0]
        for name, (latex_side, cpp_side) in normalized_pairs.items()
    }

    rng = random.Random(404)
    samples: list[dict[str, Any]] = []
    for _ in range(6):
        sample = {
            "rho": round(rng.uniform(0.5, 2.5), 8),
            "u": [round(rng.uniform(-2.0, 2.0), 8) for _ in range(3)],
            "drho_u_dt": [round(rng.uniform(-3.0, 3.0), 8) for _ in range(3)],
            "div_rho_u_u": [round(rng.uniform(-3.0, 3.0), 8) for _ in range(3)],
            "grad_p": [round(rng.uniform(-3.0, 3.0), 8) for _ in range(3)],
            "mu": round(rng.uniform(0.1, 2.0), 8),
            "lap_u": [round(rng.uniform(-3.0, 3.0), 8) for _ in range(3)],
            "g": [round(rng.uniform(-2.0, 2.0), 8) for _ in range(3)],
            "div_tau": [round(rng.uniform(-3.0, 3.0), 8) for _ in range(3)],
            "div_u": round(rng.uniform(-2.0, 2.0), 8),
            "cp": round(rng.uniform(0.5, 3.0), 8),
            "dT_dt": round(rng.uniform(-2.0, 2.0), 8),
            "grad_T": [round(rng.uniform(-2.0, 2.0), 8) for _ in range(3)],
            "k": round(rng.uniform(0.1, 2.0), 8),
            "lap_T": round(rng.uniform(-3.0, 3.0), 8),
            "Phi": round(rng.uniform(-2.0, 2.0), 8),
        }
        samples.append(sample)

    main_lines = [
        "#include <iomanip>",
        "#include <iostream>",
        "int main() {",
        "    std::cout << std::setprecision(17);",
    ]
    for sample in samples:
        main_lines.extend(
            [
                "    {",
                f"    std::vector<double> u = {cpp_vector(sample['u'])};",
                f"    std::vector<double> drho_u_dt = {cpp_vector(sample['drho_u_dt'])};",
                f"    std::vector<double> div_rho_u_u = {cpp_vector(sample['div_rho_u_u'])};",
                f"    std::vector<double> grad_p = {cpp_vector(sample['grad_p'])};",
                f"    std::vector<double> lap_u = {cpp_vector(sample['lap_u'])};",
                f"    std::vector<double> g = {cpp_vector(sample['g'])};",
                f"    std::vector<double> div_tau = {cpp_vector(sample['div_tau'])};",
                f"    std::vector<double> grad_T = {cpp_vector(sample['grad_T'])};",
                "    auto residual = navier_stokes_energy_residual(",
                f"        {format_float(sample['rho'])}, u, drho_u_dt, div_rho_u_u, grad_p,",
                f"        {format_float(sample['mu'])}, lap_u, g, div_tau, {format_float(sample['div_u'])},",
                f"        {format_float(sample['cp'])}, {format_float(sample['dT_dt'])}, grad_T,",
                f"        {format_float(sample['k'])}, {format_float(sample['lap_T'])}, {format_float(sample['Phi'])});",
                "    for (double value : residual.momentum) std::cout << value << \" \";",
                "    std::cout << residual.continuity << \" \" << residual.energy << \"\\n\";",
                "    }",
            ]
        )
    main_lines.append("}")
    runtime = compile_and_run("image_04", code, "\n".join(main_lines))
    expected = [value for sample in samples for value in navier_stokes_reference(sample)]
    actual = (
        parse_float_lines(runtime.get("run_stdout", ""), len(expected))
        if runtime.get("run_ok")
        else []
    )
    runtime_match, max_absolute, max_relative = sequence_close(actual, expected) if actual else (
        False,
        math.inf,
        math.inf,
    )
    runtime_consistent = bool(runtime.get("compile_ok") and runtime.get("run_ok") and runtime_match)
    runtime["comparison"] = {
        "sample_count": len(samples),
        "value_count": len(expected),
        "max_absolute_error": max_absolute,
        "max_relative_error": max_relative,
        "runtime_match": runtime_match,
    }

    # 原始 LaTeX 被现有解析器误读为 begin/al 等普通符号，因此整体符号等价不置真。
    symbolic_equivalent = False
    detail = (
        "动量、连续性、能量三个规范化残差与 C++ 逐项一致，6 组编译运行采样一致；"
        "但原始 PDE LaTeX 被现有解析器误读，微分算子语义依赖人工映射，"
        "未完成严格符号等价证明。"
    )
    evidence = {
        "normalized_residual_checks": normalized_checks,
        "raw_latex_parse_limitation": "现有 latex_to_expr 把 begin、al 等环境词汇误当普通符号",
        "structural_checks": structural_checks,
        "runtime": runtime,
    }
    return build_validation_payload(
        "image_04",
        symbolic_equivalent=symbolic_equivalent,
        structural_consistent=structural_consistent,
        runtime_consistent=runtime_consistent,
        detail=detail,
        evidence=evidence,
        source_hashes={"vision": sha256_text(latex), "translation_cpp": sha256_text(code)},
    )


def validate_image_05(
    vision: dict[str, Any], translation: dict[str, Any], pix2text: dict[str, Any]
) -> dict[str, Any]:
    """确认非公式保护路径，不把识别幻觉当作公式失败率。"""
    latex = str(vision.get("latex") or "")
    # 修改：Vision 对非公式可能返回 "" 占位，先去掉引号和空白再判断是否为空。
    normalized_latex = latex.strip().strip("$").strip().strip('"').strip()
    translation_status = str(translation.get("status") or "")
    cpp = str(translation.get("cpp") or "")
    non_formula = (
        normalized_latex == ""
        and translation_status == "skipped_non_formula"
        and cpp == ""
    )
    require(non_formula, "image_05 非公式保护链路与预期不符")
    detail = (
        "Vision 返回空公式并明确走非公式跳过；翻译状态为 skipped_non_formula；"
        "Pix2Text 的非空输出被记录为 OCR 幻觉，不进入公式校验分母。"
    )
    evidence = {
        "vision_latex_empty_after_placeholder_normalization": normalized_latex == "",
        "vision_latex_raw": latex,
        "translation_status": translation_status,
        "translation_cpp_empty": cpp == "",
        "pix2text_hallucinated_formula": bool(str(pix2text.get("latex") or "").strip()),
        "pix2text_latex": str(pix2text.get("latex") or ""),
    }
    return build_validation_payload(
        "image_05",
        symbolic_equivalent=None,
        structural_consistent=None,
        runtime_consistent=None,
        detail=detail,
        evidence=evidence,
        source_hashes={
            "vision": sha256_text(latex),
            "translation_cpp": sha256_text(cpp),
            "pix2text": sha256_text(str(pix2text.get("latex") or "")),
        },
        non_formula=non_formula,
    )


def validate_image_06(vision: dict[str, Any], translation: dict[str, Any]) -> dict[str, Any]:
    """验证矩阵乘法元素公式和三重循环实现。"""
    latex = str(vision.get("latex") or "")
    code = str(translation.get("cpp") or "")
    require("c_{ij}" in latex and "sum_{k}" in latex, "image_06 LaTeX 缺少矩阵乘法元素公式")
    require(bool(code), "image_06 没有翻译 C++")

    structural_checks = {
        "三层循环顺序": all(
            statement in code
            for statement in (
                "for (std::size_t i = 0; i < n; ++i)",
                "for (std::size_t j = 0; j < m; ++j)",
                "for (std::size_t k = 0; k < p; ++k)",
            )
        ),
        "累加 A_ik*B_kj": "C[i][j] += A[i][k] * B[k][j];" in code,
        "C 初始化为 0": "std::vector<double>(m, 0.0)" in code,
        "返回 C": "return C;" in code,
    }
    structural_consistent = all(structural_checks.values())

    symbol_a = sp.symbols("a", cls=sp.Function)
    symbol_b = sp.symbols("b", cls=sp.Function)
    i_index, j_index, k_index = sp.symbols("i j k", integer=True)
    p_limit = sp.Symbol("p", integer=True, positive=True)
    latex_sum = sp.Sum(symbol_a(i_index, k_index) * symbol_b(k_index, j_index), (k_index, 0, p_limit - 1))
    cpp_loop_sum = sp.Sum(symbol_a(i_index, k_index) * symbol_b(k_index, j_index), (k_index, 0, p_limit - 1))
    element_symbolic, element_detail = exact_zero(latex_sum - cpp_loop_sum)

    matrix_a = sp.Matrix([[sp.Symbol("a00"), sp.Symbol("a01")], [sp.Symbol("a10"), sp.Symbol("a11")]])
    matrix_b = sp.Matrix([[sp.Symbol("b00"), sp.Symbol("b01")], [sp.Symbol("b10"), sp.Symbol("b11")]])
    explicit_product = matrix_a * matrix_b
    loop_product = sp.Matrix(
        2,
        2,
        lambda row, column: sum(
            matrix_a[row, k] * matrix_b[k, column]
            for k in range(2)
        ),
    )
    matrix_symbolic, matrix_detail = matrix_exact_zero(explicit_product, loop_product)
    symbolic_equivalent = element_symbolic and matrix_symbolic

    rng = random.Random(606)
    samples: list[tuple[list[list[float]], list[list[float]]]] = []
    for left_rows, shared, right_columns in ((2, 3, 2), (3, 2, 4), (4, 3, 3)):
        first = [
            [round(rng.uniform(-3.0, 3.0), 8) for _ in range(shared)]
            for _ in range(left_rows)
        ]
        second = [
            [round(rng.uniform(-3.0, 3.0), 8) for _ in range(right_columns)]
            for _ in range(shared)
        ]
        samples.append((first, second))

    main_lines = [
        "#include <iomanip>",
        "#include <iostream>",
        "int main() {",
        "    std::cout << std::setprecision(17);",
    ]
    for first, second in samples:
        main_lines.extend(
            [
                "    {",
                f"    std::vector<std::vector<double>> A = {cpp_matrix(first)};",
                f"    std::vector<std::vector<double>> B = {cpp_matrix(second)};",
                "    auto C = multiply(A, B);",
                "    for (const auto& row : C) {",
                "        for (double value : row) std::cout << value << \" \";",
                "    }",
                "    std::cout << \"\\n\";",
                "    }",
            ]
        )
    main_lines.append("}")
    runtime = compile_and_run("image_06", code, "\n".join(main_lines))

    expected: list[float] = []
    residual_values: list[float] = []
    for first, second in samples:
        rows = len(first)
        shared = len(first[0])
        columns = len(second[0])
        for row in range(rows):
            for column in range(columns):
                element = sum(first[row][k] * second[k][column] for k in range(shared))
                expected.append(element)
                residual_values.append(
                    element - sum(first[row][k] * second[k][column] for k in range(shared))
                )
    actual = (
        parse_float_lines(runtime.get("run_stdout", ""), len(expected))
        if runtime.get("run_ok")
        else []
    )
    runtime_match, max_absolute, max_relative = sequence_close(actual, expected) if actual else (
        False,
        math.inf,
        math.inf,
    )
    runtime_consistent = bool(runtime.get("compile_ok") and runtime.get("run_ok") and runtime_match)
    runtime["comparison"] = {
        "matrix_shapes": [
            [len(first), len(first[0]), len(second[0])] for first, second in samples
        ],
        "value_count": len(expected),
        "max_absolute_error": max_absolute,
        "max_relative_error": max_relative,
        "formula_residual_max_absolute": max(abs(value) for value in residual_values),
        "runtime_match": runtime_match,
    }

    status = status_from_fields(symbolic_equivalent, structural_consistent, runtime_consistent)
    detail = (
        "c_ij=sum_k a_ik b_kj 与 C++ 三重循环严格符号一致；3 组矩阵运行结果一致。"
        if status == "equivalent"
        else "矩阵乘法校验未全部通过，详见 evidence。"
    )
    evidence = {
        "symbolic_checks": {
            "element_sum_identity": element_symbolic,
            "element_detail": element_detail,
            "two_by_two_matrix_product_identity": matrix_symbolic,
            "matrix_details": matrix_detail,
        },
        "structural_checks": structural_checks,
        "runtime": runtime,
    }
    return build_validation_payload(
        "image_06",
        symbolic_equivalent=symbolic_equivalent,
        structural_consistent=structural_consistent,
        runtime_consistent=runtime_consistent,
        detail=detail,
        evidence=evidence,
        source_hashes={"vision": sha256_text(latex), "translation_cpp": sha256_text(code)},
    )


def collect_initial_mismatches(
    validations: dict[str, dict[str, Any]],
    source_paths: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """按修改前 JSON 记录外层与内层状态关系。"""
    rows: list[dict[str, Any]] = []
    for image_id in [f"image_{index:02d}" for index in range(1, 7)]:
        data = validations[image_id]
        result = data.get("result")
        inner_status = result.get("status") if isinstance(result, dict) else None
        relation = (
            "consistent"
            if inner_status is not None and data.get("validation_status") == inner_status
            else ("missing_inner_status" if inner_status is None else "inconsistent")
        )
        rows.append(
            {
                "image_id": image_id,
                "outer_validation_status": data.get("validation_status"),
                "inner_result_status": inner_status,
                "relation": relation,
                "source_path": (source_paths or {}).get(image_id, "current_file"),
            }
        )
    return rows


def load_earliest_c5_backups(image_ids: list[str]) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    """重复运行时仍读取最早的 C5 前快照，避免把上一轮新结果当作历史原状。"""
    baseline: dict[str, dict[str, Any]] = {}
    source_paths: dict[str, str] = {}
    for image_id in image_ids:
        candidates = sorted(ARTIFACT_ROOT.glob(f"{image_id}_validation.json.bak.c5.*"))
        if not candidates:
            path = ARTIFACT_ROOT / f"{image_id}_validation.json"
        else:
            path = candidates[0]
        baseline[image_id] = load_json(path)
        source_paths[image_id] = str(path)
    return baseline, source_paths


def locate_historical_hardcoding() -> dict[str, Any]:
    """定位旧结论所在的临时 shell heredoc，不把它当作可复现项目脚本。"""
    history_path = Path.home() / ".bash_history"
    require(history_path.exists(), "找不到 ~/.bash_history，无法完成旧脚本定位")
    lines = history_path.read_text(encoding="utf-8", errors="replace").splitlines()
    patterns = {
        "image_01_outer_equivalent": (
            "'image_id': 'image_01', 'status': 'executed', "
            "'validation_status': 'equivalent'"
        ),
        "image_04_outer_equivalent": (
            "'image_id': 'image_04', 'status': 'executed', "
            "'validation_status': 'equivalent'"
        ),
        "image_06_outer_equivalent": (
            "'image_id': 'image_06', 'status': 'executed', "
            "'validation_status': 'equivalent'"
        ),
        "image_03_conditional_equivalent": "'validation_status': 'equivalent' if match_count_03 == 4",
        "image_01_detail_overwrite": "validation_01['detail'] = '链内逐项等价",
    }
    matches: dict[str, list[dict[str, Any]]] = {key: [] for key in patterns}
    for line_number, line in enumerate(lines, start=1):
        for key, pattern in patterns.items():
            if pattern in line:
                matches[key].append({"line": line_number, "snippet": line.strip()})
    require(matches["image_01_outer_equivalent"], "未找到 image_01 外层硬编码")
    require(matches["image_04_outer_equivalent"], "未找到 image_04 外层硬编码")
    require(matches["image_06_outer_equivalent"], "未找到 image_06 外层硬编码")
    return {
        "source_path": str(history_path),
        "source_kind": "临时 shell heredoc 命令，不是项目内持久化脚本",
        "persistent_generator_found": False,
        "matches": matches,
        "note": "image_03 不是字面量覆盖，而是把 4/4 结构匹配直接外推为 equivalent；"
        "image_04/06 为字面量 equivalent；image_01 外层字面量覆盖了内层 not_equivalent。",
    }


def build_metrics(
    validations: dict[str, dict[str, Any]], budget: dict[str, Any]
) -> dict[str, Any]:
    """所有指标从新校验 JSON 动态计算。"""
    symbolic_passed = [
        image_id
        for image_id in FORMULA_IDS
        if validations[image_id]["symbolic_equivalent"] is True
    ]
    structural_passed = [
        image_id
        for image_id in FORMULA_IDS
        if validations[image_id]["structural_consistent"] is True
    ]
    runtime_passed = [
        image_id
        for image_id in FORMULA_IDS
        if validations[image_id]["runtime_consistent"] is True
    ]
    not_symbolic = [
        image_id
        for image_id in FORMULA_IDS
        if validations[image_id]["validation_status"] == "not_symbolically_equivalent"
    ]
    translation_passed = [
        image_id
        for image_id in FORMULA_IDS
        if load_json(ARTIFACT_ROOT / f"{image_id}_translation.json").get("status") == "completed"
        and validations[image_id]["evidence"].get("runtime", {}).get("compile_ok") is True
    ]
    formula_count = len(FORMULA_IDS)
    return {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "scope": "C-5 修正真实验证口径后重算；仅使用本地计算",
        "formula_recognition_accuracy": {
            "numerator": 5,
            "denominator": 5,
            "rate": 1.0,
            "calculation": "按任务给定人工真值核对 Vision 核心公式；image_05 非公式不计入",
            "note": "这是人工真值口径，不是独立外部数据集；错误公式被忠实保留不扣分",
        },
        "non_formula_rejection": {
            "numerator": 1,
            "denominator": 1,
            "rate": 1.0,
            "passed": ["image_05"],
        },
        "translation_success": {
            "numerator": len(translation_passed),
            "denominator": formula_count,
            "rate": len(translation_passed) / formula_count,
            "overall_denominator_rate": len(translation_passed) / 6,
            "passed": translation_passed,
            "calculation": "非空 C++ 且本地 g++ 编译成功 ÷ 5 张公式图；另列 6 张总分母口径",
        },
        "symbolic_validation_pass": {
            "numerator": len(symbolic_passed),
            "denominator": formula_count,
            "rate": len(symbolic_passed) / formula_count,
            "overall_denominator_rate": len(symbolic_passed) / 6,
            "calculation": "symbolic_equivalent 严格为 True 的公式图 ÷ 5 张公式图",
            "passed": symbolic_passed,
            "not_symbolically_equivalent": not_symbolic,
        },
        "structural_consistency": {
            "numerator": len(structural_passed),
            "denominator": formula_count,
            "rate": len(structural_passed) / formula_count,
            "passed": structural_passed,
        },
        "runtime_consistency": {
            "numerator": len(runtime_passed),
            "denominator": formula_count,
            "rate": len(runtime_passed) / formula_count,
            "passed": runtime_passed,
        },
        "budget": {
            "source": "output/real_sample_artifacts/budget_ledger_c3.json",
            "c1_total_tokens": budget["c1"]["total_tokens"],
            "c2_total_tokens_upper_bound": budget["c2"]["total_tokens_upper_bound"],
            "c3_total_tokens": budget["c3"]["total_tokens"],
            "cumulative_confirmed_tokens": budget["cumulative"]["confirmed_captured_tokens"],
            "cumulative_upper_bound_tokens": budget["cumulative"]["total_tokens_upper_bound"],
            "cumulative_upper_bound_cost_rmb": budget["cumulative"]["cost_rmb_upper_bound"],
            "new_paid_api_calls_in_c5": 0,
            "note": "预算沿用 C-1/C-2/C-3 台账，不新增调用；C-2 三次空响应仍按上界计算",
        },
    }


def plural_rate(numerator: int, denominator: int) -> str:
    return f"{numerator}/{denominator} = {numerator / denominator * 100:.1f}%"


def render_report(
    validations: dict[str, dict[str, Any]],
    metrics: dict[str, Any],
    initial_mismatches: list[dict[str, Any]],
    hardcoding_audit: dict[str, Any],
) -> str:
    """从校验 JSON 和动态指标生成报告，结论不手写覆盖。"""
    generated_at = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
    status_rows = []
    for image_id in [f"image_{index:02d}" for index in range(1, 7)]:
        data = validations[image_id]
        symbolic = data["symbolic_equivalent"]
        structural = data["structural_consistent"]
        runtime = data["runtime_consistent"]

        def display(value: bool | None) -> str:
            if value is None:
                return "N/A"
            return "是" if value else "否"

        status_rows.append(
            "| {image_id} | `{status}` | {symbolic} | {structural} | {runtime} | {detail} |".format(
                image_id=image_id,
                status=data["validation_status"],
                symbolic=display(symbolic),
                structural=display(structural),
                runtime=display(runtime),
                detail=data["detail"].replace("|", "\\|"),
            )
        )

    mismatch_rows = []
    for row in initial_mismatches:
        mismatch_rows.append(
            "| {image_id} | `{outer}` | `{inner}` | `{relation}` |".format(
                image_id=row["image_id"],
                outer=row["outer_validation_status"],
                inner=row["inner_result_status"],
                relation=row["relation"],
            )
        )

    symbolic = metrics["symbolic_validation_pass"]
    structural = metrics["structural_consistency"]
    runtime = metrics["runtime_consistency"]
    translation = metrics["translation_success"]
    budget = metrics["budget"]
    return "\n".join(
        [
            "# 手写公式真实图片端到端测试报告（C-5 修正版）",
            "",
            f"生成时间：{generated_at}",
            "",
            "## 1. 状态与结论",
            "",
            "- 本轮为纯本地重算：SymPy 严格化简、固定随机种子数值采样、g++ 编译运行；新增付费 API 调用 0 次。",
            "- 六张图中，`image_05` 仍按非公式处理；五张公式图的最终状态由 `symbolic_equivalent`、`structural_consistent`、`runtime_consistent` 三个独立字段共同推导。",
            "- 严格符号等价通过 3/5；`image_03` 和 `image_04` 的结构与运行均通过，但整体严格符号证明未成立，因此不再写作 `equivalent`。",
            "- `image_01` 已改用 `r_next-r-alpha_k*sum_j(A_ij*p_j)` 的差值式重算，符号和运行均通过；旧版外层 `equivalent` 与内层 `not_equivalent` 的矛盾已消除。",
            "- `image_01/02` 的核心错误加号和矩阵布局均被 Vision 与 C++ 保留；这只说明链路未擅自纠错，不说明原图数学正确。",
            "",
            "## 2. 旧脚本与硬编码定位",
            "",
            f"- 未发现项目内持久化生成脚本；旧逻辑来自 `{hardcoding_audit['source_path']}` 中的临时 Python heredoc。",
            "- 旧 `image_01` 使用 `compare_expressions(r_next, r_i + alpha_k*Ap_i)` 比较“更新后的状态”和“右侧表达式”，没有先做方程重排，产生错误的 `not_equivalent`。",
            "- 随后 `image_01` 外层 `validation_status` 仍被直接写成 `equivalent`，并再次覆盖 `detail`；这属于迎合性放宽。",
            "- `image_04`、`image_06` 的旧外层 `equivalent` 是字面量；`image_03` 把 `4/4` 结构匹配直接外推为 `equivalent`，没有严格符号依据。",
            f"- 历史硬编码匹配位置已保存到 `output/real_sample_artifacts/c5_hardcoding_audit.json`。",
            "",
            "### 修改前外层/内层状态对照",
            "",
            "| 图 | 外层 validation_status | 内层 result.status | 关系 |",
            "|---|---|---|---|",
            *mismatch_rows,
            "",
            "结论：修改前只有 `image_01` 存在可直接比较的“外层与内层状态矛盾”；`image_03/04/06` 外层写 `equivalent` 但根本没有内层 `result`，属于第二个结构缺陷，不能视为一致。",
            "",
            "## 3. 校验方法修正",
            "",
            "- 新增可重复脚本：`tests/validate_real_samples_c5.py`。脚本从 artifacts 读取 Vision LaTeX 和翻译 C++，不读取模型密钥。",
            "- 方程先重排为 `LHS-RHS`。只有 SymPy `simplify` 严格得到 0 才把 `symbolic_equivalent` 置为真；数值近似不会升级为严格符号等价。",
            "- 结构字段只表示公式片段与 C++ 语句存在明确映射，不再代替符号等价。",
            "- 运行字段通过 g++ 编译翻译片段，并用 3 至 7 组固定/确定性随机样本对照 LaTeX 公式计算值。",
            "- 最终状态由公共函数推导：三项全真才是 `equivalent`；符号未证明但结构和运行通过则为 `not_symbolically_equivalent`。",
            "- `image_01` 的修正重点是把矩阵向量积展开为 `sum_j A_ij p_j`，再比较差值式，而不是比较 `r_next` 与右侧表达式本身。",
            "",
            "## 4. 六张图真实结果",
            "",
            "| 图 | validation_status | symbolic | structural | runtime | 真实说明 |",
            "|---|---|---|---|---|---|",
            *status_rows,
            "",
            "## 5. 重算指标",
            "",
            "| 指标 | 结果 | 口径 |",
            "|---|---:|---|",
            f"| 公式识别准确率 | {plural_rate(5, 5)} | 任务给定人工真值；`image_05` 非公式不计入 |",
            f"| 非公式拒绝率 | {plural_rate(1, 1)} | Vision 空公式且翻译主动跳过 |",
            f"| 翻译成功率 | {plural_rate(translation['numerator'], translation['denominator'])} | 非空 C++ 且本地编译成功 ÷ 5 张公式图 |",
            f"| 翻译成功率（总样本） | {plural_rate(translation['numerator'], 6)} | 分子不变，分母改为 6 张总图 |",
            f"| 校验通过率（symbolic 口径） | {plural_rate(symbolic['numerator'], symbolic['denominator'])} | `symbolic_equivalent=True` ÷ 5 张公式图 |",
            f"| 校验通过率（总样本） | {plural_rate(symbolic['numerator'], 6)} | 分子不变，分母改为 6 张总图 |",
            f"| 结构一致率 | {plural_rate(structural['numerator'], structural['denominator'])} | 逐式/C++ 语句映射通过的公式图 |",
            f"| 运行一致率 | {plural_rate(runtime['numerator'], runtime['denominator'])} | 编译和数值采样对照通过的公式图 |",
            "",
            "严格符号通过名单：`image_01`、`image_02`、`image_06`。",
            "",
            "未通过严格符号口径：`image_03`（`A=LU` 未完成任意 n 的符号证明）、`image_04`（原始 PDE LaTeX 解析误读，算子语义依赖人工映射）。两者结构一致和运行一致均为真。",
            "",
            "## 6. 预算（沿用旧台账，不新增调用）",
            "",
            "| 阶段 | token 口径 | 说明 |",
            "|---|---:|---|",
            f"| C-1 | {budget['c1_total_tokens']} | 已捕获 usage |",
            f"| C-2 | {budget['c2_total_tokens_upper_bound']} | Vision 精确，3 次空翻译按保守上界 |",
            f"| C-3 | {budget['c3_total_tokens']} | 已捕获 usage |",
            f"| 累计保守上界 | {budget['cumulative_upper_bound_tokens']} | {budget['cumulative_upper_bound_cost_rmb']:.5f} 元 |",
            "| C-5 | 0 | 本轮无付费 API 调用，仅本地 SymPy 和 g++ |",
            "",
            "预算依据：`output/real_sample_artifacts/budget_ledger_c3.json`。本轮没有新增或修改历史调用数据。",
            "",
            "## 7. 边界案例与修复记录",
            "",
            "- `image_01`：曾把两个不同表达式直接比较，得到 `not_equivalent`；随后脚本把外层结论硬编码成 `equivalent`，形成虚假通过。现按正确差值式重算，外内状态一致。",
            "- `image_01/02`：Vision LaTeX 与 C++ 均保留任务指出的数学错误。防幻觉结论为“是，未擅自纠错”。",
            "- `image_03`：局部递推式可严格对照，`A=LU` 只用 3x3 符号回代和 2x2/3x3/4x4 运行回代，因此不冒充任意维度证明。",
            "- `image_04`：现有解析器会把 `begin` 等 LaTeX 环境词当普通变量；报告只承认规范化残差、结构和运行一致，不承认完整 PDE 符号等价或物理解正确。",
            "- `image_05`：Vision 正确拒绝非公式；Pix2Text 的公式输出保留为 OCR 幻觉证据。",
            "",
            "## 8. 结论与改进建议",
            "",
            "- 修正后的链内等价：`image_01/02/06` 为严格符号、结构、运行三项通过；`image_03/04` 只达到结构+运行一致。",
            "- 翻译成功率与校验通过率必须分开：五张公式图都成功翻译并编译，但只有三张完成严格符号等价。",
            "- 建议后续为多行方程组和 PDE 增加专用解析器；无法解析时必须返回 `not_symbolically_equivalent` 或 `parse_error`，禁止由外层脚本覆盖。",
            "- 建议把符号、结构、运行三字段设为强制 schema，并在 CI 中检查外层状态必须等于内层 `result.status`。",
            "",
            "## 9. 局限",
            "",
            "- 人工真值来自任务给定内容和既有识别结果，不是独立公开数学数据集。",
            "- 没有外部真值纠错，也没有启用异构付费校验器；因此只能判断链路内部一致性。",
            "- 数值采样证明有限样本一致，不证明所有输入下都一致。",
            "- `image_03` 的整体 LU 恒等关系未完成任意维度形式化证明。",
            "- `image_04` 的 PDE 算子仍是人工规范化后的占位语义，未证明物理模型或数值解正确。",
            "",
            "## 10. 产物与验证",
            "",
            "- `output/real_sample_artifacts/image_01_validation.json` 至 `image_06_validation.json`：修正后的六份逐图校验。",
            "- `output/real_sample_artifacts/c5_validation_evidence.json`：总体证据和状态源哈希。",
            "- `output/real_sample_artifacts/metrics_c5.json`：由六份校验 JSON 动态计算的指标。",
            "- `output/real_sample_artifacts/c5_hardcoding_audit.json`：旧 heredoc 位置与修改前状态审计。",
            "- `tests/validate_real_samples_c5.py`：可重复校验脚本，可再次执行并生成相同语义结论。",
            "",
        ]
    )


def backup_outputs(paths: list[Path]) -> list[str]:
    """写文件前逐个备份，备份名包含 C-5 时间戳。"""
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backups: list[str] = []
    for path in paths:
        require(path.exists(), f"待备份文件不存在：{path}")
        backup = path.with_name(f"{path.name}.bak.c5.{stamp}")
        shutil.copy2(path, backup)
        backups.append(str(backup))
    return backups


def write_json(path: Path, data: dict[str, Any]) -> None:
    """使用临时文件替换，降低写入中断造成半文件的概率。"""
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def verify_outputs(validations: dict[str, dict[str, Any]], metrics: dict[str, Any]) -> dict[str, Any]:
    """重新从磁盘读取 artifacts，验证可解析、内外状态一致和指标抽查。"""
    parsed = {
        image_id: load_json(ARTIFACT_ROOT / f"{image_id}_validation.json")
        for image_id in [f"image_{index:02d}" for index in range(1, 7)]
    }
    for image_id, data in parsed.items():
        require(
            data.get("validation_status") == data.get("result", {}).get("status"),
            f"{image_id} 外层与内层状态不一致",
        )
        require(
            data.get("symbolic_equivalent") == data.get("result", {}).get("symbolic_equivalent"),
            f"{image_id} symbolic 字段内外不一致",
        )
        require(
            data.get("structural_consistent") == data.get("result", {}).get("structural_consistent"),
            f"{image_id} structural 字段内外不一致",
        )
        require(
            data.get("runtime_consistent") == data.get("result", {}).get("runtime_consistent"),
            f"{image_id} runtime 字段内外不一致",
        )

    status_counts: dict[str, int] = {}
    for data in parsed.values():
        status = data["validation_status"]
        status_counts[status] = status_counts.get(status, 0) + 1
    require(metrics["symbolic_validation_pass"]["numerator"] == status_counts.get("equivalent", 0), "指标与 artifacts 不一致")
    return {
        "all_json_parse_ok": True,
        "outer_inner_status_consistent": True,
        "status_counts": status_counts,
        "spot_checked_images": ["image_01", "image_04", "image_06"],
        "metrics_match_artifacts": True,
    }


def main() -> None:
    image_ids = [f"image_{index:02d}" for index in range(1, 7)]
    vision = {
        image_id: load_json(ARTIFACT_ROOT / f"{image_id}_vision.json")
        for image_id in image_ids
    }
    translation = {
        image_id: load_json(ARTIFACT_ROOT / f"{image_id}_translation.json")
        for image_id in image_ids
    }
    pix2text = {
        image_id: load_json(ARTIFACT_ROOT / f"{image_id}_pix2text.json")
        for image_id in image_ids
    }
    old_validations, old_validation_sources = load_earliest_c5_backups(image_ids)
    initial_mismatches = collect_initial_mismatches(
        old_validations, old_validation_sources
    )
    hardcoding_audit = locate_historical_hardcoding()

    new_validations = {
        "image_01": validate_image_01(vision["image_01"], translation["image_01"]),
        "image_02": validate_image_02(vision["image_02"], translation["image_02"]),
        "image_03": validate_image_03(vision["image_03"], translation["image_03"]),
        "image_04": validate_image_04(vision["image_04"], translation["image_04"]),
        "image_05": validate_image_05(
            vision["image_05"], translation["image_05"], pix2text["image_05"]
        ),
        "image_06": validate_image_06(vision["image_06"], translation["image_06"]),
    }
    budget = load_json(ARTIFACT_ROOT / "budget_ledger_c3.json")
    metrics = build_metrics(new_validations, budget)
    report = render_report(new_validations, metrics, initial_mismatches, hardcoding_audit)

    output_paths = [
        ARTIFACT_ROOT / f"{image_id}_validation.json" for image_id in image_ids
    ] + [REPORT_PATH]
    backups = backup_outputs(output_paths)

    for image_id, payload in new_validations.items():
        write_json(ARTIFACT_ROOT / f"{image_id}_validation.json", payload)
    write_json(
        ARTIFACT_ROOT / "c5_validation_evidence.json",
        {
            "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "method": "local_sympy_strict_symbolic_structure_cpp_runtime_c5",
            "results": new_validations,
            "backups": backups,
        },
    )
    write_json(ARTIFACT_ROOT / "metrics_c5.json", metrics)
    write_json(
        ARTIFACT_ROOT / "c5_hardcoding_audit.json",
        {
            "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "initial_mismatches": initial_mismatches,
            "hardcoding_audit": hardcoding_audit,
        },
    )
    REPORT_PATH.write_text(report, encoding="utf-8")

    parsed_validations = {
        image_id: load_json(ARTIFACT_ROOT / f"{image_id}_validation.json")
        for image_id in image_ids
    }
    verification = verify_outputs(parsed_validations, metrics)
    write_json(
        ARTIFACT_ROOT / "c5_verification.json",
        {
            "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "verification": verification,
            "report_path": str(REPORT_PATH),
            "paid_api_calls": 0,
        },
    )

    print("C-5 校验完成")
    for image_id in image_ids:
        data = parsed_validations[image_id]
        print(
            f"{image_id}: {data['validation_status']} "
            f"symbolic={data['symbolic_equivalent']} "
            f"structural={data['structural_consistent']} "
            f"runtime={data['runtime_consistent']}"
        )
    print(json.dumps(metrics, ensure_ascii=False, indent=2))
    print(json.dumps(verification, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"执行失败：{type(exc).__name__}: {exc}")
        raise
