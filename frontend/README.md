<!-- Documentation header: local development, API proxying, testing, and production build guide. -->
# Favlist 前端

这是 Favlist 的 React + TypeScript 浏览器界面。它假定 FastAPI 后端提供同源的 `/api` 接口和基于 Cookie 的会话认证。所有非 GET API 请求由统一客户端附加 `X-Favlist-CSRF: 1`。

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
