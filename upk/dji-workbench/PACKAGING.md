# UPK 打包记录（dji-workbench）

## v1.0.0 build 0024（2026-11-13，amd64，**首个 WebUI 安装验证版**）

- 版本内核：v0.16.0+（main=1caaf08）：0023 全部 + 前端导出失败显示引擎 error 详情 + 编辑台文案对齐 native（134 特效目录/自研引擎语境，js v51）
- 产物：`amd64_nasync_com.jvsheng.djiworkbench_1.0.0.0024.upk`（919467978B，md5 549b92d1b4c20c1609b3020f7665f584）
- **WebUI 手动安装（用户真实路径）全流程自动化实锤**：Playwright 真登录（UGOS web 账号=690/1q1q1Q1Q，与 SSH 同名同密码）→桌面→应用中心→左下 ⬇ 手动安装→iframe 内 `input[type=file]` 用 DataTransfer 注入（919MB 经 690 本地 CORS http.server 回源 fetch 构造 File，**919MB blob 浏览器内存可行**）→下一步（UI 自动上传 919MB 到 /volume1/@upload）→应用详细信息页（**此页的确认按钮也是「下一步」不是「安装」**）→CUA 坐标点击→安装
- **⚠️ 升级/修复/卸载被「撕裂状态」全挡死的大坑（0023 时代 API 侧载后遗症）**：API 侧载升级后 appmgr 元数据=repair flag+not online，而实际容器半死；此后 WebUI ①升级报「**更新失败，关联应用异常**」（埋点 System_appcenter_install_fail，服务端零动作=前端本地状态机拒绝）②「修复」按钮点了也 repair_fail（apprunner 状态查询 record not found）③API 卸载返回 200 但只删元数据（容器/appstore 目录残留，已安装列表消失）。**唯一解法=手动清场**：`docker rm -f` 双容器+`mv /volume1/@appstore/<app>` 走备份 →WebUI 全新安装一路通过。**教训：API 侧载升级一次就会把 appmgr 状态搞脏；后续版本升级尽量走 WebUI（先手动清场）**
- WebUI 安装质量对比 API 侧载：**appmgr 自己建双容器（无需 fix-store-mounts.sh）**、挂载 @appdata 绝对路径正确（包内 compose 本就写绝对路径）、items 24 数据完好
- 安装后真浏览器验证：库 24 items、编辑台完整渲染（native-1.0、历史工程、HDR/Alpha 面板）、打开工程→预览渲染→1080p 导出完成→下载按钮就绪
- 排障工具链：Playwright response 监听抓安装 API 链（upload→is_running→depends→inst/manual→inst/after，**响应体全加密**）；埋点 grep `System_appcenter_*`；appmgr 与 docker 实际状态撕裂靠 SSH 直查容器
- 自动化坑合集：桌面恢复的旧窗口（文本编辑器）会盖住点击目标（**窗口状态按账号服务端持久化**，关一次以后不再弹）；恢复窗口登录 ~10s 后才弹（检测要轮询等待）；Vue 列表按钮（如「修复」）对 element.click() 合成事件不响应→必须 CUA 坐标点击；对话框按钮合成 click 可用（STEP1 证据）；「下一步」按钮查找必须 `offsetParent && rect.width>0`（详情页有隐藏同名旧按钮 rect=0,0 陷阱）

## v1.0.0 build 0023（2026-11-12，amd64，基于 5.10 工作区 pack-0023.sh）

- 版本内核：v0.16.0（GitHub main 511f1f9）：HDR 导出修复（`_zscale_ok` 运行时真跑一帧探测 + setparams 先定义输入色度 + 去 gbrpf32le 前置）+ 导出失败透出 ffmpeg stderr（export_status.error）
- 产物：`amd64_nasync_com.jvsheng.djiworkbench_1.0.0.0023.upk`（919467669B，md5 b9eefeee1a1084e48）
- 690 侧载升级验证全过：catalog 134 / zscale_ok=True / 转场+变速+HDR e2e done（输出 hevc/yuv420p10le/bt2020nc/smpte2084/bt2020+aac）/ alpha prores yuva444p12le；升级后照例跑 deploy/fix-store-mounts.sh 重建双容器
- **HDR zscale 坑（ffmpeg 7.1.5 + zimg 3.0.5，5.10/690 同款镜像）**：滤镜在、纯 npl 可跑，但 ①喂 gbrpf32le/gbrp14le（GBR 系）必 EINVAL ②输入无色彩空间元数据时带 transfer/matrix/primaries 参数必 EINVAL。修法=`setparams=colorspace=bt709:...` 先打标再 `zscale=transfer=smpte2084:matrix=bt2020nc:primaries=bt2020:npl=100`；`_zscale_ok()` 启动探测缓存，坏则自动降级 10bit+元数据。libplacebo 滤镜虽在但无 GPU 容器里 init 即 Invalid argument，不可用
- **docker cp 热补丁路径坑**：镜像内代码在 `/app/app/`（不是 `/app/`）——cp 到 /app/ 静默无效（应用照跑旧代码），曾据此误判修复未生效

## v1.0.0 build 0001（2026-10-28，amd64）

- 产物：`amd64_nasync_com.jvsheng.djiworkbench_1.0.0.0001.upk`（186MB，sha256 前 16 位见桌面）
- 打包机：4800 Pro（192.168.5.10，本机自打包，ugcli v1.1.0.25 在 /home/jvsheng/ugcli-v1.1.0.25-linux-amd64）
- 工作区：/volume1/docker/dji-workbench-upk/（pack.sh=全流程 / pack-resume.sh=镜像就绪后续跑）
- 源：GitHub main（打包脚本先拉源+project.yaml 断言，杜绝本地 tar 时序坑）

## schema 实测（ugcli v1.1.0.25，check 与 pack 校验不同）

- **check 阶段**（宽松）：必填 app_id/version/open_type/spec_version(major>1，写 2.0)/support_arch/depend_fw_version/depend_docker_version/tag_types(oneof=system utility media driver download backup security devtool)/is_docker_app/i18n[locale].author；rootfs_common 只许 icon.png+docker-compose.yaml；rootfs_<arch> 只许 images/；**app_url 为空时要求 rootfs_common/www 目录**
- **pack 阶段**（严格 unmarshal）：顶层只认这些字段——app_id/version/open_type/spec_version/support_arch/depend_docker_version/depend_fw_version/is_docker_app/tag_types/start_cmd(可空 when is_docker_app)/i18n[zh-CN,en-US].{name,description,author}；**name/description/author/website/support_url/logo/app_url/doc_url/developer/protocol/port/category/edition/permission/docker/chinese_name/description_en/services 全部不在顶层**（大部分进 i18n，services 在 compose 里，permission 商店侧配置）
- entrypoint.sh 从 rootfs_common 移到 rootfs_amd64 根再 append 层（rootfs_common 不允许额外文件）
- --file 清单（本应用）：entrypoint.sh:entrypoint.sh:0755 + app/{web_app.py,static/*} 五个文件
- 商店 compose 数据路径用 /volume1/@appdata/<app>/（相对推荐），监听根默认 /data/inbox（用户设置页改）

## v1.0.0 build 0008（2026-11-07，amd64）

- 版本内核：v0.8.4（GitHub main 7899050）：编辑台补齐撤销/重做、标题、多行字幕、特效层、片段重排、720p/1080p/4K 导出面板（v2 重写时丢失的处理器全数找回+新增）
- 产物：`amd64_nasync_com.jvsheng.djiworkbench_1.0.0.0008.upk`（742533250B，md5 c71118f279dc6c464a3f687b4fd4056b，sha256 前16=875570669d6248bf）；抽层终验过（0.8.4 补丁层+0.2.9+compose 18790）
- **⚠️ ugcli `--build 0008` 会报 strconv.ParseInt invalid——ParseInt 按 base=0 解析，"0001"~"0007" 是合法八进制、"0008" 含 8 直接炸。必须传十进制 `--build 8`（文件名内部自动补零为 0008）**
- 5.10 直连 GitHub 拉源会挂起：源 tar 由 Mac curl 后 scp（main.tar.gz ~130KB），打包脚本去 curl 步骤
- append_image_layer 的 --file 相对路径跟随 CWD：脚本里统一 `cd $WORK` 后用 `src/` 前缀

## v1.0.0 build 0007（2026-11-06，amd64）

- 版本内核：v0.8.3（GitHub main 7e37408）
- **concat-engine 0.2.9：VNC GUI 栈进容器**（Xvfb :99 + Concat GUI + x11vnc + noVNC 18790，entrypoint 一体托管）——UPK 用户零宿主依赖，剪辑页「原版界面」iframe 直连 `http://<NAS>:18790/vnc.html`
- compose 增 concat-engine 端口映射 18790:18790；镜像 1.39GB（+xvfb/x11vnc/novnc）
- 打包脚本 pack-0007.sh（5.10:/volume1/docker/dji-workbench-upk/）；坑：脚本在第 5 步前必须 `cd $WORK`（曾在 src/ 里 cp 相对路径失败）；ugcli 用绝对路径 /home/jvsheng/ugcli-...（sudo 下 ~ = /root）
- concat-engine 0.2.9 构建文件入库：upk/concat-engine/{Dockerfile,entrypoint.sh,bridge.py,config.json}，构建机需 concat-linux.deb（官方 releases）

## Mesa/呈现实锤（宿主 GUI 栈，两台 NAS 验证）

- **Xvfb 无 DRI3**：Mesa 22.3（bookworm stock）llvmpipe/ANV 均无法呈现 → 黑屏；Mesa ≥25（bpo12+1 或 Ubuntu 24.04）软路径呈现正常
- 690 宿主修法 = 从 5.10 搬 deb：libdrm-common/libdrm2/libdrm-amdgpu1 2.4.123-bpo12 + mesa-vulkan-drivers 25.0.7-bpo12，concat-gui.service 加 `VK_DRIVER_FILES=lvp_icd.json`（Mesa 25 去了架构后缀名）+ XDG_RUNTIME_DIR
- 690 宿主 concat-vnc 用 5901（5999 被 firefox-app 容器占用；noVNC「持续弹密码框」的真凶就是 websockify 转发到了该容器的带密码 VNC）

## 待办

- [ ] 真机安装验证（侧载到测试机/本机）
- [ ] 开发者门户提交（ReleaseNote ≤5000 字，双语模板 upk/RELEASE-NOTES.md 待写）
