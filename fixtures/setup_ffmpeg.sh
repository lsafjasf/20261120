#!/bin/bash
# 无 root 权限时，把 ffmpeg/ffprobe 及其递归依赖下载并解包到用户目录，
# 再在 ~/.local/bin 生成包装脚本（通过 LD_LIBRARY_PATH 指向本地库）。
set -euo pipefail

PREFIX="${FFMPEG_LOCAL_PREFIX:-/tmp/fflocal}"
WRAPPER_DIR="$HOME/.local/bin"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

echo "[setup] 计算 ffmpeg 递归依赖..."
apt-cache depends --recurse --no-recommends --no-suggests \
    --no-conflicts --no-breaks --no-replaces --no-enhances ffmpeg \
    2>/dev/null | grep '^\w' | sort -u > "$WORK/deps.txt"

echo "[setup] 下载 $(wc -l < "$WORK/deps.txt") 个包到 $WORK"
(cd "$WORK" && apt-get download $(cat "$WORK/deps.txt" | tr '\n' ' '))

echo "[setup] 解包到 $PREFIX"
mkdir -p "$PREFIX"
for deb in "$WORK"/*.deb; do
    dpkg-deb -x "$deb" "$PREFIX"
done

LIB_DIR="$PREFIX/usr/lib/x86_64-linux-gnu"
mkdir -p "$WRAPPER_DIR"
for tool in ffmpeg ffprobe; do
    cat > "$WRAPPER_DIR/$tool" <<EOF
#!/bin/sh
export LD_LIBRARY_PATH="$LIB_DIR:\${LD_LIBRARY_PATH:-}"
exec "$PREFIX/usr/bin/$tool" "\$@"
EOF
    chmod +x "$WRAPPER_DIR/$tool"
done

echo "[setup] 验证："
"$WRAPPER_DIR/ffmpeg" -version | head -1
"$WRAPPER_DIR/ffprobe" -version | head -1
