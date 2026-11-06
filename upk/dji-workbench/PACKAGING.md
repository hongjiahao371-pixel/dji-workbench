# UPK 打包记录（dji-workbench）

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
