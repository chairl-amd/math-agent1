#!/usr/bin/env bash
# 一键演示：识别图片、按方向翻译、执行校验并保存 Markdown 报告。
set -u
set -o pipefail

PROJECT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_ROOT" || exit 2

usage() {
    printf '%s\n' \
        "用法: bash demo.sh [选项] [图片路径]" \
        "  --image <路径>                   指定公式或代码图片" \
        "  --mode <vision|pix|both>         选择 OCR 链路" \
        "  --direction <formula2code|code2formula>" \
        "                                    选择翻译方向，默认 formula2code" \
        "  --verbose                        打印三段式中间结果" \
        "  --help                           显示帮助"
}

mode=""
direction="formula2code"
verbose=0
image_path=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --image)
            if [[ $# -lt 2 ]]; then
                printf '错误: --image 缺少路径\n' >&2
                exit 2
            fi
            image_path="$2"
            shift 2
            ;;
        --mode)
            if [[ $# -lt 2 ]]; then
                printf '错误: --mode 缺少值\n' >&2
                exit 2
            fi
            mode="$2"
            shift 2
            ;;
        --direction)
            if [[ $# -lt 2 ]]; then
                printf '错误: --direction 缺少值\n' >&2
                exit 2
            fi
            direction="$2"
            shift 2
            ;;
        --verbose)
            verbose=1
            shift
            ;;
        --help|-h)
            usage
            exit 0
            ;;
        --)
            shift
            if [[ $# -gt 1 ]]; then
                printf '错误: 只允许传入一个图片路径\n' >&2
                exit 2
            fi
            if [[ $# -eq 1 ]]; then
                image_path="$1"
            fi
            break
            ;;
        -*)
            printf '错误: 不支持的参数 %s\n' "$1" >&2
            usage >&2
            exit 2
            ;;
        *)
            if [[ -n "$image_path" ]]; then
                printf '错误: 只允许传入一个图片路径\n' >&2
                exit 2
            fi
            image_path="$1"
            shift
            ;;
    esac
done

case "$mode" in
    ""|vision|pix|both) ;;
    *)
        printf '错误: --mode 只能是 vision、pix 或 both\n' >&2
        exit 2
        ;;
esac

case "$direction" in
    formula2code|code2formula) ;;
    *)
        printf '错误: --direction 只能是 formula2code 或 code2formula\n' >&2
        exit 2
        ;;
esac

if [[ -z "$image_path" ]]; then
    image_path="$(
        find tests/formula -maxdepth 1 -type f -name 'formula_*.png' \
            -printf '%T@ %p\n' 2>/dev/null \
            | sort -nr \
            | head -n 1 \
            | cut -d' ' -f2-
    )"
fi

if [[ -z "$image_path" || ! -f "$image_path" ]]; then
    printf '错误: 图片不存在或不是文件：%s\n' "${image_path:-<未找到 tests/formula/formula_*.png>}" >&2
    exit 2
fi

if [[ ! -x venv/bin/python ]]; then
    printf '错误: 找不到虚拟环境解释器 venv/bin/python\n' >&2
    exit 2
fi

if ! mkdir -p output; then
    printf '错误: 无法创建 output 目录\n' >&2
    exit 2
fi
run_timestamp="$(date +%Y%m%d_%H%M%S)"
cli_arguments=(
    agent/main.py
    --image "$image_path"
    --direction "$direction"
    --out output
)
if [[ -n "$mode" ]]; then
    cli_arguments+=(--mode "$mode")
fi
if [[ "$verbose" -eq 1 ]]; then
    cli_arguments+=(--verbose)
fi

venv/bin/python "${cli_arguments[@]}"
cli_exit_code=$?

latest_report="$(
    find output -maxdepth 1 -type f -name 'report_*.md' \
        -printf '%T@ %p\n' 2>/dev/null \
        | sort -nr \
        | head -n 1 \
        | cut -d' ' -f2-
)"
if [[ -z "$latest_report" || ! -f "$latest_report" ]]; then
    printf '错误: 未找到本次运行生成的 Markdown 报告\n' >&2
    exit "${cli_exit_code:-1}"
fi

demo_report="output/demo_${run_timestamp}.md"
demo_json="output/demo_${run_timestamp}.json"
if ! cp -- "$latest_report" "$demo_report"; then
    printf '错误: 无法写入演示报告 %s\n' "$demo_report" >&2
    exit 2
fi
latest_json="${latest_report%.md}.json"
if [[ -f "$latest_json" ]]; then
    if ! cp -- "$latest_json" "$demo_json"; then
        printf '警告: 无法写入演示 JSON %s\n' "$demo_json" >&2
    fi
fi

printf '\n[demo] 演示报告: %s\n' "$demo_report"
printf '[demo] 输入图片: %s\n' "$image_path"
printf '[demo] CLI 退出码: %s\n' "$cli_exit_code"
exit "$cli_exit_code"
