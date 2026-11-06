#!/bin/sh
# concat-engine 容器入口：headless MCP 桥接(18190) 常驻 + VNC GUI(18790) 常驻
# - 18190: bridge.py → concat --mcp headless（工作台编辑台的 API 通道）
# - 18790: noVNC 网页 → x11vnc → Xvfb :99 上的 Concat 原版 GUI（浏览器直接操作）
set -e
HOME_DIR=/data/concat-home
XDG_DIR=/tmp/concat-xdg
LOG=/tmp/concat-gui.log
mkdir -p "$HOME_DIR" "$XDG_DIR"
chmod 700 "$XDG_DIR" 2>/dev/null || true

# Mesa ICD：跳过硬件驱动枚举，只用 lavapipe 软渲染（Xvfb 无 DRI3，硬件 ICD 必黑屏）
LVP=$(ls /usr/share/vulkan/icd.d/lvp_icd*.json 2>/dev/null | head -1)

start_gui_stack() {
  Xvfb :99 -screen 0 1600x900x24 -nolisten tcp >/dev/null 2>&1 &
  sleep 1
  DISPLAY=:99 HOME="$HOME_DIR" XDG_RUNTIME_DIR="$XDG_DIR" \
    VK_DRIVER_FILES="$LVP" WORKING_DIR="$HOME_DIR" \
    /opt/concat/concat >"$LOG" 2>&1 &
  sleep 3
  DISPLAY=:99 x11vnc -display :99 -forever -shared -nopw -listen localhost -rfbport 5900 \
    -quiet >/dev/null 2>&1 &
  websockify --web /usr/share/novnc 18790 localhost:5900 >/dev/null 2>&1 &
}

start_gui_stack
exec python3 /opt/concat-bridge/bridge.py
