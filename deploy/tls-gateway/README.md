# TLS 网关（690 实测配置）

给 HTTP 服务套自签 https 外壳（OpenCut 媒体持久化依赖安全上下文 API）。

## 结构（NAS /volume1/docker/tls-gateway/）
- certs/cert.pem + key.pem：自签证书（10 年，SAN=IP）
- conf/default.conf：3101→3100（OpenCut）、8921→8920（工作台），WebSocket 透传 + 2G 上传
- docker-compose.yml：nginx:latest，restart=unless-stopped

## 证书生成
openssl req -x509 -nodes -newkey rsa:2048 -days 3650 \
  -keyout certs/key.pem -out certs/cert.pem \
  -subj "/CN=192.168.5.69" \
  -addext "subjectAltName=IP:192.168.5.69,DNS:oc.local"

用户首次访问每个 https 端口点一次「高级→继续访问」即可。
