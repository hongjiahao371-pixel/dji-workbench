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

## 待办

- [ ] 真机安装验证（侧载到测试机/本机）
- [ ] 开发者门户提交（ReleaseNote ≤5000 字，双语模板 upk/RELEASE-NOTES.md 待写）
