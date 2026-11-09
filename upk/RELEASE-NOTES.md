# DJI 素材工作台 v1.0.0.0016 更新说明

## 中文（ReleaseNote，≤5000 字）

本次更新把「剪辑」能力整体升级为完整版，并随包搭载 **Concat 0.2.9 引擎**（音频持久化修复、导出提速约 2.4 倍@4K、时间轴联动、标题富文本词样式）：

1. **原版 Concat 剪辑界面开箱即用**：引擎容器内置 Xvfb 虚拟屏 + Concat 原版 GUI + VNC 网页流（端口 18790），无需任何宿主机配置。在工作台「剪辑」页点击「原版界面」即可在浏览器里直接操作完整剪辑软件（多轨时间轴、分割、变速、关键帧、遮罩、防抖、文字/贴纸/转场、特效层等全部能力）。
2. **编辑台（引擎直控）继续增强**：分割/多轨/关键帧/127 款特效/遮罩/防抖/裁剪/变换/混合/背景/转场与模板，全部通过引擎 MCP 接口直控，工程自动落盘。
3. **稳定性与体验**：排序、缩略图、灯箱、地图/时间轴视图、导出管线（调色/水印/自动小片）沿用 v0.8.x 全部能力；本版本为容器整合与 GUI 直通版本，无破坏性变更。

升级说明：直接覆盖安装，数据（素材库/工程/导出）保留。首次升级后会自动重建引擎容器（约 1-2 分钟）。

## English

1. Full original Concat editor UI, zero host setup: the engine container now ships the complete Concat GUI on a virtual display with noVNC streaming (port 18790). Open "Original UI" from the Edit page and edit multi-track timelines, split, speed, keyframes, masks, stabilization, text/stickers/transitions and 127 effects right in your browser.
2. The built-in Edit console keeps full engine control: split / multi-track / keyframes / 127 effects / masks / stabilization / crop / transform / blend / background / transitions & templates.
3. Non-breaking release: media library, projects and exports are preserved on upgrade; the engine container rebuilds automatically on first start.


## v1.0.0.0016（2026-11-09）增量

- 引擎升级 Concat 0.2.9：45 个编辑工具（新增时间轴联动/标题词样式），导出 4K 提速约 2.4 倍
- **背景音乐（BGM）持久化修复**：0.2.8 引擎保存工程会丢失音频片段，0.2.9 已修复——加 BGM→保存→重开工程，音乐完整保留
- 编辑台新增：工程打开/关闭切换、画幅帧率设置（横竖屏/4K/24fps）、导出取消、标题富文本（按字符范围设颜色/字号/字重）
- 工程画幅与总长常显（如 1920×1080@30）
