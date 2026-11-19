"""FLV -> MP4 重封装（stream copy，不重新编码）。

优先使用本地 ffmpeg；输出后由 mp4_probe 独立校验，而非信任 ffmpeg 日志。
"""
from __future__ import annotations

import os
import shutil
import subprocess


class RemuxError(Exception):
    pass


def find_ffmpeg() -> str:
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    candidate = os.path.expanduser("~/.local/bin/ffmpeg")
    if os.path.exists(candidate):
        return candidate
    raise RemuxError("未找到 ffmpeg，请先安装或放到 ~/.local/bin/ffmpeg")


def remux_flv_to_mp4(flv_path: str, mp4_path: str,
                     ffmpeg_bin: str | None = None) -> str:
    """用 -c copy 重封装，保留编码与原始时间戳。"""
    ffmpeg = ffmpeg_bin or find_ffmpeg()
    env = os.environ.copy()
    local_lib = "/tmp/fflocal/usr/lib/x86_64-linux-gnu"
    if os.path.isdir(local_lib):
        env["LD_LIBRARY_PATH"] = local_lib + os.pathsep + env.get("LD_LIBRARY_PATH", "")
    cmd = [
        ffmpeg, "-y", "-loglevel", "error",
        "-i", flv_path,
        "-c", "copy",
        "-movflags", "+faststart",
        mp4_path,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, env=env)
    if proc.returncode != 0 or not os.path.exists(mp4_path):
        raise RemuxError(f"ffmpeg 重封装失败: {proc.stderr.strip()}")
    return mp4_path
