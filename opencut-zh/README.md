# OpenCut 汉化包与源码级补丁

## 运行时汉化
- `zh-engine.js`：MutationObserver 引擎，词典 `/zh-dict.json` 三层 `{text, placeholder, value}`
  （文本节点 / placeholder+title+aria-label / input 值 900ms 轮询），v4 含活锁修复
  （observer 按根去重 + 同值守卫）。
- `zh-dict.json`：561 条（text 523 + placeholder 37 + value 1）。
- 部署：覆盖到 OpenCut 容器 `/app/apps/web/public/` 刷新即生效；重打镜像必须携带。

## 源码级补丁（已在 Mac 构建注入两台，重打镜像需重放）
1. `nas-import-dialog.tsx`：
   - name 映射：`rel_path || file_name` 取最后一段（workbench 列表 API 无 file_name 字段！）
   - saved URL 自动升级：https 页面里 http:8920 → https:8921（混合内容拦截规避）
   - fallback 按页面协议选 8921/8920
2. `timeline-toolbar.tsx`：ToolbarButton 的 Button 加 `aria-label={tooltip}`（无障碍 + 自动化可定位）
3. 分割快捷键 = S（keybindings 持久化迁移文件实锤，无需改动）

## 构建（Mac）
全量解 fork tar（自带 node_modules）→ 应用补丁 → `cd apps/web && bun run build`
（env: FREESOUND_CLIENT_ID= FREESOUND_API_KEY= NEXT_PUBLIC_MARBLE_API_URL=https://api.marblecms.com MARBLE_WORKSPACE_KEY=placeholder）
产物注入：`.next/standalone` → 容器 `/app`；`.next/static` → `/app/apps/web/.next/static`；
`public` → `/app/apps/web/public`；`docker restart`。

## 已知未修
- 0002 商店包里的 OpenCut 无 rel_path 修复（0003 再带）
- 键盘按键名（Space/Shift）、ID3 曲风表尾/国家列表、库内部错误串：刻意不翻
