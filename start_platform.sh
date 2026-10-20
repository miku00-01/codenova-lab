#!/usr/bin/env bash
# ============================================================
#  CloudOps Forge - Linux/macOS 启动脚本
#  （对应 Windows 下的 启动平台.bat）
#  - 自动检测并创建 .venv，避免污染系统 Python
#  - 自动检测端口 5000 占用，被占用时切换 5001
#  - 依赖已装则跳过 pip install
#  - 打开浏览器失败不阻塞主流程
#
#  用法：
#    chmod +x start_platform.sh
#    ./start_platform.sh
#  首次使用建议：sudo apt install python3 python3-venv    (Ubuntu/Debian)
# ============================================================
set -u
cd "$(dirname "$0")" || { echo "[错误] 无法进入脚本所在目录"; exit 1; }

# ---------- 1. 探测 Python3 ----------
PYTHON_BIN=""
for cand in python3 python; do
    if command -v "$cand" >/dev/null 2>&1; then
        PYTHON_BIN="$cand"
        break
    fi
done
if [ -z "$PYTHON_BIN" ]; then
    echo "[错误] 未检测到 Python3，请先安装:"
    echo "      Ubuntu/Debian: sudo apt install python3 python3-venv python3-pip"
    echo "      CentOS:        sudo yum install python3 python3-pip"
    exit 1
fi
echo "使用 Python: $($PYTHON_BIN --version 2>&1)"

# ---------- 2. 创建 / 复用 .venv ----------
if [ ! -x ".venv/bin/python" ]; then
    echo "[1/5] 首次启动，正在创建虚拟环境..."
    "$PYTHON_BIN" -m venv .venv || { echo "[错误] 创建 .venv 失败"; exit 1; }
else
    echo "[1/5] 检测到现有 .venv，跳过创建"
fi

PY=".venv/bin/python"
PIP=".venv/bin/pip"

# ---------- 3. 安装依赖（仅在 Flask 缺失时） ----------
echo "[2/5] 检查依赖..."
if "$PY" -c "import flask" >/dev/null 2>&1; then
    echo "      Flask 已安装，跳过 pip install"
else
    echo "      未检测到 Flask，正在安装依赖（首次约 10~30 秒）..."
    "$PIP" install -r requirements.txt || { echo "[错误] 依赖安装失败，请手动执行: $PIP install -r requirements.txt"; exit 1; }
fi

# ---------- 4. 检查端口 5000 是否被占用 ----------
echo "[3/5] 检查端口..."
PORT=5000
if command -v ss >/dev/null 2>&1; then
    if ss -ltn | awk '{print $4}' | grep -q ':5000$'; then PORT=5001; fi
elif command -v netstat >/dev/null 2>&1; then
    if netstat -ltn | awk '{print $4}' | grep -q ':5000$'; then PORT=5001; fi
fi
if [ "$PORT" = "5001" ]; then
    echo "      [提示] 5000 端口已被占用，自动切换到 5001"
fi

# ---------- 5. 打开浏览器（不阻塞启动） ----------
echo "[4/5] 正在打开浏览器..."
if command -v xdg-open >/dev/null 2>&1; then
    xdg-open "http://127.0.0.1:${PORT}/" >/dev/null 2>&1 || echo "      [提示] 自动打开浏览器失败，请手动访问下方地址"
elif command -v open >/dev/null 2>&1; then   # macOS
    open "http://127.0.0.1:${PORT}/" >/dev/null 2>&1 || echo "      [提示] 自动打开浏览器失败，请手动访问下方地址"
else
    echo "      [提示] 未找到浏览器打开命令，请手动访问下方地址"
fi

# ---------- 6. 启动 Flask ----------
echo "[5/5] 启动 CloudOps Forge..."
echo "------------------------------------------"
echo "  访问地址：http://127.0.0.1:${PORT}"
echo "  默认落地：代码练习首页"
echo "  按 Ctrl+C 停止服务"
echo "------------------------------------------"

export PORT
exec "$PY" app.py
