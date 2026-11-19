#!/bin/bash
# 一键：准备本地 ffmpeg（如缺失）-> 生成素材 -> 全量分析 -> 跑测试。
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
export PATH="$HOME/.local/bin:$PATH"

if ! command -v ffmpeg >/dev/null 2>&1; then
    echo "[run_all] 未找到 ffmpeg，请先执行 fixtures/setup_ffmpeg.sh"
    exit 1
fi

echo "==> 1/4 生成测试素材"
bash "${ROOT}/fixtures/make_fixtures.sh"

echo "==> 2/4 重封装并校验 sample.flv（无 B 帧）"
python3 -B "${ROOT}/src/main.py" \
    "${ROOT}/fixtures/sample.flv" \
    -o "${ROOT}/out/sample.mp4" \
    --json "${ROOT}/out/report_sample.json" >/dev/null

echo "==> 3/4 重封装并校验 sample2.flv（含 B 帧 / 非零 CTS）"
python3 -B "${ROOT}/src/main.py" \
    "${ROOT}/fixtures/sample2.flv" \
    -o "${ROOT}/out/sample2.mp4" \
    --json "${ROOT}/out/report_sample2.json" >/dev/null

echo "==> 4/4 单元测试"
python3 -B -m unittest discover -s "${ROOT}/tests" -v

echo
echo "全部完成。报告见 out/report_sample.json 与 out/report_sample2.json"
