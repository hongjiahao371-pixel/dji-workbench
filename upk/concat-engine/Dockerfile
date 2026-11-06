# Concat 引擎容器（headless MCP 桥接 + 可选 VNC GUI）
# 构建机需有 concat-linux.deb（Concat 官方 releases 下载）：
#   cp concat-linux.deb /tmp/ && docker build -t concat-engine:0.2.9 .
FROM ubuntu:24.04
ENV DEBIAN_FRONTEND=noninteractive
COPY concat-linux.deb /tmp/
RUN sed -i 's|archive.ubuntu.com|mirrors.aliyun.com|g; s|security.ubuntu.com|mirrors.aliyun.com|g' /etc/apt/sources.list.d/ubuntu.sources 2>/dev/null; \
    apt-get update && \
    apt-get install -y --no-install-recommends \
    ffmpeg python3 ca-certificates procps \
    libgtk-3-0t64 libfontconfig1 libfreetype6 libxkbcommon0 libxkbcommon-x11-0 \
    libgl1 libasound2t64 libvulkan1 mesa-vulkan-drivers \
    xvfb x11vnc novnc && \
    dpkg -i /tmp/concat-linux.deb && \
    rm -f /tmp/concat-linux.deb && ldconfig && \
    rm -rf /var/lib/apt/lists/*
COPY bridge.py /opt/concat-bridge/bridge.py
COPY config.json /opt/concat-bridge/config.json
COPY entrypoint.sh /opt/concat-bridge/entrypoint.sh
ENV LD_LIBRARY_PATH=/opt/concat/lib
EXPOSE 18190 18790
ENTRYPOINT ["/bin/sh", "/opt/concat-bridge/entrypoint.sh"]
