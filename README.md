# 🛸 DJI 素材工作台（dji-workbench）

面向 DJI Osmo Pocket 4 Pro / Osmo Action 6 等**支持 SMB 自动上传**的大疆相机的 NAS 素材工作台。
相机在「充电 + 连接 Wi-Fi」时会把素材自动上传到 NAS 的 `DJI Device/<相机名>/<日期>/` 目录，
本工具监听该目录自动入库，提供浏览、挑选、视频代理、DNG 预览与 SD 卡导入。

> 已支持 SMB 直传 NAS 的机型（2026-10 调研）：Osmo Pocket 4 / 4 Pro、Osmo Action 6（固件 V01.02.0521+）。
> Pocket 3 / Action 5 Pro / Mini 无人机暂无此功能，走「SD 卡导入」通道即可。

## 功能

- **自动入库监听**：轮询监听 SMB 落盘目录，「size 连续多次不变 + ftyp/moov/EOF 完整性嗅探」双条件判定上传完成，杜绝拿半个文件抽帧
- **素材库**：日期分组网格、缩略图（照片 PIL / 视频服务端海报帧串行生成）、类型/设备/日期/星级/淘汰/重复筛选、搜索、排序、分页
- **挑选**：五星评分（卡片快捷键 + 灯箱）、一键淘汰、内容级 SHA-256 去重（重传自动标记重复）
- **预览**：照片原片、DNG 内嵌预览提取（纯 Python TIFF 解析，避免 16-bit Bayer 直解偏色）、等距柱状全景 WebGL 球面查看（宽高比 ≥2.2 自动识别）、视频 H.264 代理播放（H.265/10-bit 浏览器播不动时的兜底）
- **视频代理**：后台串行 ffmpeg 转码（不放大、`+faststart`），支持挂 `.cube` LUT（D-Log M 素材建议挂官方 Rec.709 转换 LUT，预览色≈成片色）
- **SD 卡导入**：任意目录扫描导入，路径级去重 + 入库后内容级去重；覆盖无 SMB 直传的机型和读卡器场景
- **元数据**：EXIF（含 GPS）、ffprobe 编码/时长、MP4 `©xyz` GPS 原子（ISO 6709）、DJI 遥测字幕 sidecar 关联
- **素材安全**：入库即算 SHA-256 存档、可选自动备份目录（稳定入库后复制一份）

## 快速开始

### Docker Compose（推荐）

```bash
git clone <repo> dji-workbench && cd dji-workbench
# 修改 docker-compose.yml 里的素材目录挂载路径
docker compose up -d --build
# 打开 http://NAS_IP:8920 → 设置页确认监听根
```

### 本地运行

```bash
pip3 install -r requirements.txt   # 需要 ffmpeg 在 PATH 里
python3 -m app.web_app             # 默认 127.0.0.1:8920，数据目录 ./data
```

环境变量：`DJI_WB_DATA`（数据目录）、`DJI_WB_ROOTS`（冒号分隔的监听根）、`DJI_WB_HOST/PORT`。
所有配置也可在 Web 设置页修改（落盘 `data/config.json`）。

### 相机端配置

1. DJI Mimo App 连上相机 →「我的」→ 相机云服务 / 上传渠道
2. 选择「自定义 NAS」，填 NAS 的 SMB 地址、共享路径与账号密码
3. 开启「自动备份」开关；相机保持充电 + 同一 Wi-Fi 即自动上传

## 测试

```bash
python3 -m unittest discover -s tests    # 31 个用例：完整性嗅探/扫描稳定流/去重/DNG/代理/API
```

## 设计决策与已知边界

- **上传完成判定**：相机经 SMB 边传边写，MP4 的 moov box 在尾部最后落盘。判定 = size 连续 `stable_scans` 次不变 + 文件龄 > `stable_age` 秒 + 头部 `ftyp` + 尾部窗口含 `moov`（MP4）/ `FFD9`（JPEG）。拿到真机后需实测：DJI 是否有临时文件名（`.tmp`/`.part`）、中断续传行为，若有再针对性加规则
- **DNG 预览**：取内嵌 JPEG（IFD0 缩略图 / SubIFD JPEG 压缩条带里最大的），DNG 直解 16-bit Bayer 会压黑偏色（luna-sync 已踩过）
- **代理不带 LUT 时**：HLG/D-Log M 素材代理颜色发灰属预期（未套转换），到设置页挂 LUT 即可
- **GPS**：JPEG 走 EXIF，MP4 扫头尾 8MB 窗口找 `©xyz`；无人机素材 GPS 覆盖率高，手持相机看固件是否写入
- **待真机实测清单**（Pocket 4 Pro 到手后第一件事）：自动备份是否包含 DNG RAW / 日期子文件夹时区 / 遥测字幕是否随传 / SMB 临时文件名 / 中断续传行为 / `©xyz` 实际样例

## 路线图

- [ ] 地图视图（GPS 点聚合）
- [ ] 相似照片挑选 / 模糊度自动选片
- [ ] AI 场景打标 + 自然语言搜索
- [ ] 时间轴视图、项目/相册、画质管线（预设/裁切/水印）、自动小片
- [ ] UGOS UPK 商店包（合规四件套：HTTPS/隐私弹窗/免责声明/升级策略）
