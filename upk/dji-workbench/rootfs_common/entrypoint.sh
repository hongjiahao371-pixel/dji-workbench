#!/bin/sh
set -e
DATA=/data
mkdir -p "$DATA/thumbs" "$DATA/posters" "$DATA/proxies" "$DATA/sprites" "$DATA/exports" "$DATA/luts" "$DATA/preview_cache" "$DATA/inbox"
# 商店首启：默认监听根 = /data/inbox（用户可在设置页改成 SMB 落盘目录）
if [ ! -f "$DATA/config.json" ]; then
  cat > "$DATA/config.json" <<CFG
{
  "host": "0.0.0.0",
  "port": 8920,
  "watch_roots": ["/data/inbox"],
  "poll_interval": 20,
  "stable_age": 10,
  "stable_scans": 2,
  "proxy_enabled": true,
  "proxy_height": 1080,
  "proxy_crf": 23,
  "lut_path": "",
  "thumb_size": 640,
  "backup_dir": "",
  "ai_base_url": "",
  "ai_api_key": "",
  "ai_model": "",
  "export_font_path": "",
  "access_password": "",
  "opencut_url": "",
  "ai_embedding_url": "",
  "ai_vision_url": "",
  "ai_embedding_model": "",
  "ai_vision_model": "",
  "ai_semantic_enabled": false
}
CFG
fi
cd /app
exec python3 -m app.web_app
