<!-- Documentation header: installation, configuration, deployment, security, and verification guide. -->
# Favlist

单用户、自托管的收藏清单：React 前端、FastAPI 后端和 SQLite 持久化。它只保存外部服务元数据及 WebP 封面，不下载条目正文。

## 快速开始（Docker，推荐）

1. 复制环境模板：`Copy-Item .env.example .env`（PowerShell）或 `cp .env.example .env`。
2. 生成管理员密码哈希并将输出完整填入 `.env` 的 `ADMIN_PASSWORD_HASH`：

   ```powershell
   docker compose run --rm --no-deps api python -c "import getpass; from argon2 import PasswordHasher; print(PasswordHasher().hash(getpass.getpass('Admin password: ')))"
   ```

3. 在 `.env` 中填入至少 32 个字符的随机 `SESSION_SECRET`。例如可用 `python -c "import secrets; print(secrets.token_urlsafe(48))"` 生成。
4. 可选地编辑 `config.yaml`，在两个 YAML 列表中按优先级填写喜欢和不喜欢的标签；同一标签不能同时存在于两组。
5. 构建并启动：`docker compose up -d --build`。
6. 打开 [http://127.0.0.1:8080](http://127.0.0.1:8080)，使用 `.env` 中的管理员用户名和原始密码登录。

Compose 将网页和 API 分别限制在 `127.0.0.1:${WEB_PORT}` 与 `127.0.0.1:${API_PORT}`，SQLite 数据库和封面保存在名为 `favlist-data` 的 Docker 卷中。正常重建容器不会删除此卷；若要有意清空所有数据，执行 `docker compose down -v`（不可恢复）。查看日志用 `docker compose logs -f api web`。

## 本地开发

需要 Python 3.12+、Node.js 22+ 与 npm。后端和前端分别在两个终端运行：

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
$env:TAG_CONFIG_PATH = "../config.yaml"
$env:DATABASE_URL = "sqlite+aiosqlite:///./data/favlist.db"
$env:ADMIN_USERNAME = "admin"
$env:ADMIN_PASSWORD_HASH = "<replace-with-argon2-hash>"
$env:SESSION_SECRET = "<at-least-32-random-characters>"
$env:CORS_ALLOWED_ORIGINS = "http://localhost:5173,http://127.0.0.1:5173"
uvicorn app.main:app --reload --port 8000
```

前端开发服务器默认将 `/api` 代理到 `http://localhost:8000`。如后端运行在其他地址，可先设置 `VITE_API_PROXY_TARGET`，再运行：

```powershell
cd frontend
npm ci
npm run dev
```

然后访问 Vite 输出的地址（通常是 `http://localhost:5173`）。生产使用 Docker 的同源 Nginx 代理，不依赖开发代理配置。

## 配置


Docker 的同源部署应保持 `CORS_ALLOWED_ORIGINS` 为空；仅当前后端分开运行时填写精确 origin，不允许 `*`。Compose 使用固定内部网段，API 默认只信任固定的 `web` 代理地址 `172.30.55.10/32`。项目内 Nginx 只接受固定 `caddy` 地址和 `TRUSTED_EDGE_PROXY` 提供的客户端 IP，并在转发到 API 前覆写外部转发头。默认 `TRUSTED_EDGE_PROXY=172.30.55.1` 是宿主机在默认 Docker 网络内的网关地址。脱离 Compose 运行时，`TRUSTED_PROXY_CIDRS` 只填写确定受信任的直连反向代理地址；为空时不采信可伪造的转发 IP 头。



## 公网 HTTPS（可选 Caddy profile）

准备已解析到本机公网 IP 的域名后，在 `.env` 设置 `APP_DOMAIN=example.com` 和 `COOKIE_SECURE=true`，并允许 80/443 入站。启动：

```powershell
docker compose --profile caddy up -d --build
```

Caddy 会自动申请并续期证书。此模式仍保留默认的 localhost 端口用于本机维护；不要把 API 端口暴露到公网。部署在反向代理后时，也应确保浏览器通过 HTTPS 访问，且 `COOKIE_SECURE=true`。

## 公网 HTTPS（已有宿主机 Nginx）

推荐链路是“宿主机 Nginx → `127.0.0.1:8080` 的 `web` 服务 → `api`”，不要让宿主机 Nginx 直接访问或公开 API 端口。在 `.env` 设置 `COOKIE_SECURE=true`，保持 `CORS_ALLOWED_ORIGINS` 为空，并使用如下站点配置（替换域名和证书路径）：

```nginx
server {
    listen 80;
    server_name favlist.example.com;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl http2;
    server_name favlist.example.com;

    ssl_certificate     /etc/nginx/certs/favlist.fullchain.pem;
    ssl_certificate_key /etc/nginx/certs/favlist.key;

    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $remote_addr;
        proxy_set_header X-Forwarded-Proto https;
    }
}
```

这里故意用 `$remote_addr` 覆写而不是追加浏览器传入的 `X-Forwarded-For`。`web` 端口必须继续只绑定 `127.0.0.1`，否则远程请求可能绕过宿主机可信边界。若修改 Compose 的 `172.30.55.0/24` 网络或宿主代理并非从默认网关进入容器，请把 `.env` 的 `TRUSTED_EDGE_PROXY` 同步改成项目内 Nginx 实际看到的单个代理 IP 或最小 CIDR；不要填写任意大网段。API 的 `TRUSTED_PROXY_CIDRS` 仍应只信任 `web` 容器 `/32`。

## 安全说明


## 测试与验证

后端：`cd backend; pytest`。

前端：`cd frontend; npm ci; npm test`。生产构建：`cd frontend; npm run build`。

部署配置可先检查：`docker compose config`；启动后确认 `docker compose ps` 显示 `api` 和 `web` 正常运行。测试不会调用真实外部服务，使用注入的模拟客户端。
