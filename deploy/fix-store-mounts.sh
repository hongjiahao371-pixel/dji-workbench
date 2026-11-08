#!/bin/sh
# UGOS Pro 商店版（UPK）升级后挂载修复脚本
# 背景：通过 API 侧载（分片上传 + v1/app/inst/manual）升级后，appmgr 重建容器时
#       使用它内部记录的挂载（相对路径解析到 @appstore/<app>/ = 空目录），导致
#       素材库 items:0 / 引擎工程丢失。本脚本把 compose 指回 @appdata 持久目录，
#       重建双容器，并补 concat-engine 的网络与沙箱对齐。
# 用法：在 NAS 上以 root 跑（或 sudo）：sh fix-store-mounts.sh [APP_ID] [DATA_DIR]
#   APP_ID   默认 com.jvsheng.djiworkbench
#   DATA_DIR 默认 /volume1/@appdata/dji-workbench（工作台数据目录）
# 升级后执行一次即可；重复执行幂等。

APP_ID="${1:-com.jvsheng.djiworkbench}"
DATA_DIR="${2:-/volume1/@appdata/dji-workbench}"
APP_DIR="/volume1/@appstore/$APP_ID"

[ -d "$APP_DIR" ] || { echo "ERR: $APP_DIR 不存在"; exit 1; }
command -v docker >/dev/null || { echo "ERR: 需要 docker"; exit 1; }

echo "== 1. compose 挂载改为 @appdata 绝对路径（幂等）"
cd "$APP_DIR" || exit 1
python3 - "$APP_DIR" "$DATA_DIR" <<'PY'
import sys
app_dir, data_dir = sys.argv[1], sys.argv[2]
s = open("docker-compose.yaml").read()
s = s.replace("- ./data:/data", f"- {data_dir}/data:/data")
s = s.replace("- ./inbox:/media/inbox", f"- {data_dir}/inbox:/media/inbox")
s = s.replace("- ./concat:/data", f"- {data_dir}/concat:/opt/concat-home")
s = s.replace("- ./concat:/opt/concat-home", f"- {data_dir}/concat:/opt/concat-home")
open("docker-compose.yaml", "w").write(s)
print("compose ok")
PY

echo "== 2. 数据目录就绪"
mkdir -p "$DATA_DIR/data" "$DATA_DIR/inbox" "$DATA_DIR/concat"

echo "== 3. 重建双容器"
docker rm -f dji-workbench concat-engine 2>/dev/null
docker compose up -d 2>&1 | tail -2

echo "== 4. concat-engine 接入工作台网络（appmgr 只建主容器）"
sleep 10
NET=$(docker inspect dji-workbench --format '{{range $k,$v := .NetworkSettings.Networks}}{{$k}}{{end}}' 2>/dev/null)
[ -n "$NET" ] && docker network connect --alias concat-engine "$NET" concat-engine 2>/dev/null
echo "network: $NET"

echo "== 5. 验证"
sleep 5
docker exec dji-workbench python3 -c "import sqlite3; print('db-items:', sqlite3.connect('/data/workbench.db').execute('select count(*) from items').fetchone()[0])" 2>/dev/null
curl -s --max-time 8 "http://127.0.0.1:8920/api/health" | head -c 200; echo
curl -s --max-time 8 "http://127.0.0.1:8920/api/editor/health"; echo
echo "== 完成（items 与 editor ok 即为成功）"
