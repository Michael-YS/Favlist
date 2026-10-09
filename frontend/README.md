<!-- Documentation header: local development, API proxying, testing, and production build guide. -->
# Favlist 前端

这是 Favlist 的 React + TypeScript + Material UI (MUI) 浏览器界面。它假定 FastAPI 后端提供同源的 `/api` 接口和基于 Cookie 的会话认证。所有非 GET API 请求由统一客户端附加 `X-Favlist-CSRF: 1`。

## 本地运行

在本目录执行：

```powershell
npm ci
npm run dev
```

开发服务器默认将 `/api` 代理到 `http://localhost:8000`。若前端在 Docker 网络内运行，可在启动前设置 `VITE_API_PROXY_TARGET=http://api:8000`。

```powershell
npm test
npm run build
```

`npm test` 运行 Vitest 组件测试，`npm run build` 执行 TypeScript 检查并生成生产构建。

界面组件使用 `@mui/material` 与 Emotion。`App.tsx` 中的 `ThemeProvider` 同步本地保存的深浅色偏好；`styles.css` 保留目录布局、移动端折叠与标签强调样式。

## 渲染与部署约定

采用 Vite SPA 与 React `createRoot` 的 CSR。页面内容和 MUI 样式在浏览器生成，数据来自 FastAPI。生产运行时仅需 Nginx 静态服务，不需要 Node SSR 进程。后续开发优先保持 CSR；仅在明确需求下引入 SSR。
