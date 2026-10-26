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

### v0.2 全家桶（2026-10-25）

- **批量选择模式**：全选/反选、批量打星、批量淘汰/恢复、批量加相册——旅行回来一次性整理
- **自动选片**：同目录 2 秒连拍自动分组，清晰度+曝光评分建议保留（弹窗预览分组，一键应用：建议项打 ★、其余淘汰）
- **照片质量分**：入库时自动评分（边缘能量 65% + 曝光中性度 35%，0-100），支持质量筛选与排序、卡片 Q 徽章
- **相册**：引用式相册（删除不动原文件），素材库按相册筛选，批量加入
- **地图视图**：EXIF/©xyz 坐标画点（等距柱状投影 canvas，无外网依赖），点位 hover 提示、点击列出附近素材一键跳灯箱
- **时间轴视图**：按天 × 24 小时热力条带，含缩略图，点某小时筛选该时段素材
- **视频悬停刮擦**：12 帧服务端雪碧图（串行生成），鼠标横扫卡片快速预览整段内容
- **导出管线**：亮度/对比/饱和/暖冷色温/暗角/缩放/JPEG-PNG、右下左下右上左下四角中文字水印（自动探测系统字体）、打包 zip 下载——RAW 导出自动走内嵌预览
- **自动小片**：选中照片渲染 1080p Ken Burns 幻灯片（交替推近/拉远、无损拼接、可选背景音乐循环对齐）
- **AI 打标**：OpenAI 兼容多模态接口（设置页配 base_url/key/model），批量生成中文标签，标签参与搜索
- **后台任务系统**：导出/小片/AI 打标统一任务队列（jobs 表持久化状态），顶栏任务入口 + 完成下载 + 历史列表

### v0.3（2026-10-26）

- **视频导出**：导出弹窗勾选「视频一并导出」，ffmpeg 滤镜链与照片调色语义对齐（官方 LUT→色温 colortemperature→eq 亮度对比饱和→暗角→缩放→水印 drawtext），H.264 重编码不放大；视频水印需 ffmpeg 带 drawtext（运行时探测，缺失自动跳过不阻塞导出）
- **相似素材**：dhash 64bit 感知哈希（入库/回填自动计算）+ 4×4 颜色签名双门控——灯箱「🔍 查找相似」弹窗展示距离徽章，点击跳转。颜色门控防 dhash 盲区（同结构异色误报），阈值实测标定：连拍色距 <10、同场景 10-25、跨场景 40+，默认门 35
- **任务历史**：顶栏任务弹窗显示最近 30 条（含失败原因与产物下载）

### v0.4（2026-10-26）

- **灯箱快捷键**：`1-5` 打星、`0` 清除、`X` 淘汰/恢复、`←→` 切换、`Esc` 关闭——批量挑片不用碰鼠标
- **相似对比**：相似弹窗点卡片直接进左右并排对比（基准/距离标注、分辨率体积、两侧独立打星/淘汰）——发现相似→并排比→留一张闭环
- **重复文件清理**：🧹 重复入口按 SHA-256 分组展示，每组默认保留最早一张（可点选切换），「回收其余」把文件移入同目录 `.djiwb-trash/`（同卷 rename 原子、扫描自动跳过隐藏目录、可随时人工捞回），统计可回收空间
- **EXIF 拍摄参数**：照片光圈/快门/ISO/焦距（灯箱参数行），视频帧率/码率/编码 profile；入库时提取 + 老库启动回填
- **存储统计**：设置页底部——素材总量/设备分布条形图、回收站计数、五类缓存占用（海报/雪碧图/导出产物可一键清理；代理与缩略图只显示防误删）

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

- **上传完成判定**：相机经 SMB 边传边写，MP4 的 moov box 在尾部最后落盘。判定 = size 连续 `stable_scans` 次不变 + 文件龄 > `stable_age` 秒 + 头部 `ftyp` + 尾部窗口含 `moov`（MP4）/ `FFD9`（JPEG）
- **DNG 预览**：取内嵌 JPEG（IFD0 缩略图 / SubIFD JPEG 压缩条带里最大的），DNG 直解 16-bit Bayer 会压黑偏色（luna-sync 已踩过）
- **代理不带 LUT 时**：HLG/D-Log M 素材代理颜色发灰属预期（未套转换），到设置页挂 LUT 即可；官方 LUT 从 [DJI LUT 汇总页](https://www.dji.com/cn/lut) 下载（Pocket 4 / Pocket 4P / Action 6 均有官方 Rec.709 转换 LUT）
- **GPS**：JPEG 走 EXIF，MP4 扫头尾 8MB 窗口找 `©xyz`，ISO 6709 三种格式（十进制度/度分/度分秒）对齐 ExifTool `ConvertISO6709` 参考实现
- **音频**：无线麦「机身收音备份」会另存独立 `.aac` 音轨（官方手册确认），已按音频类型入库

## 已做的公开素材验证（2026-10-25）

- **官方手册**（[Action 6](https://www.dji.com/osmo-action-6/downloads) / [Pocket 4](https://www.dji.com/osmo-pocket-4/downloads) v1.0 PDF）：两本均无 NAS 章节 → NAS 备份为固件+Mimo App 侧功能；确认 Pocket 4 支持 D-Log 10bit、Action 6 机身收音备份另存 .aac
- **官方 FAQ**（[Action 6](https://www.dji.com/cn/osmo-action-6/faq)）：上传渠道=百度网盘/夸克/OneDrive/**NAS**，走 Mimo App 配置
- **实测文章**（[zelpio](https://www.zelpio.com/zh-my/blog/dji-osmo-action-6-firmware-8k-nas-backup)）：NAS 备份目录=`DJI Device/<相机名>/<按天分夹>`（含历史素材补传）；触发=**插电即传**（与 GoPro/Insta360 同逻辑）；协议=**SMB**（明确非 WebDAV）；实测速度 ~13 MB/s（Wi-Fi）；NAS/GDrive/OneDrive 三选一
- **真实样例文件**（[raw.pixls.us DJI Osmo Action DNG](https://raw.pixls.us/)）：`extract_dng_preview` 1ms 提取 960×720 内嵌预览 ✅，导入服务端产出缩略图 ✅
- **官方 LUT 文件**（DJI 官方 CDN Phantom 4 D-Log cube）：发现并修复兼容性 bug——DJI 官方 LUT 带 `LUT_3D_INPUT_RANGE` 行，ffmpeg lut3d 解析到该行**整体初始化失败**；现已自动净化剥离（按 mtime 缓存净化副本），套官方 LUT 转码实测生效（平灰片亮度 -18.5 / 饱和度 +3.4）✅
- **GPS 解析对齐**：与 ExifTool QuickTime.pm 的 `ConvertISO6709` 逐格式核对并补齐三格式支持 ✅（34 个单测全绿）

### 仍需真机确认（Pocket 4 Pro 到手）

1. 自动备份是否包含 DNG RAW（公开资料未明确）
2. `DJI Device` 日期子夹用相机时钟（时区）还是 NAS 时区
3. SMB 写入是否用临时文件名（`.tmp`/`.part`）、中断后续传行为——当前判定逻辑对两种情况都鲁棒，但可针对性优化
4. Pocket 4 Pro 的 MP4 是否写 `©xyz` GPS 及其实际格式（解析已按 ExifTool 三格式兜底）

## 路线图

- [x] ~~地图视图（GPS 点位+联动）~~ v0.2
- [x] ~~连拍自动选片~~ v0.2
- [x] ~~AI 场景打标 + 标签搜索~~ v0.2（需自配 API）
- [x] ~~时间轴视图、相册、画质管线（调色/水印）、自动小片~~ v0.2
- [x] ~~视频导出转码~~ v0.3
- [x] ~~相似照片聚类（dhash+颜色签名双门控）~~ v0.3；标签搜索已支持（AI 打标后 q 参数覆盖标签）
- [ ] 自然语言语义搜索（需向量化，等部署后接 NAS embedding 服务）
- [ ] UGOS UPK 商店包（合规四件套：HTTPS/隐私弹窗/免责声明/升级策略）——待 push 后走 CI 镜像
- [ ] NAS 实机部署（compose 就绪，等定机器与素材目录）
