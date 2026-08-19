<!-- Documentation header: installation, configuration, deployment, security, and verification guide. -->
# Favlist

单用户、自托管的收藏清单：React 前端、FastAPI 后端和 SQLite 持久化。它只保存外部服务元数据及 WebP 封面，不下载条目正文。

> [!IMPORTANT]
> Favlist 是私人收藏索引，不是下载器、阅读器或多用户服务。请只在你有权访问外部服务内容的网络环境中使用。

## 功能

- 批量导入 `ID123`、纯数字编号或混合分隔文本，自动去重并异步补全元数据。
- 首页快速记录可将一句话中的所有 ASCII 数字按顺序拼成一个编号并直接保存。
- 按编号、标题、作者和标签搜索；支持多标签交集筛选、分页与多种排序。
- 缓存并按需恢复 WebP 封面，展示作者、简介、页数、发布日期和互动统计。
- 单条或批量刷新元数据，批量删除记录，并将全部编号导出为 UTF-8 文本。
- 用 `config.yaml` 配置喜欢/不喜欢标签的显示强调和优先顺序。
- 默认隐私模式不渲染封面、标题、作者或标签；可从“更多”菜单临时显示，页面进入后台后自动隐藏。
- 响应式明暗主题，适配桌面与移动端 Firefox。
- 单管理员登录、可撤销服务端会话、CSRF 校验和登录限流。

## 技术栈与架构

| 层 | 实现 |
| --- | --- |
| Web | React、TypeScript、Vite；生产环境由 Nginx 提供静态文件和同源 `/api` 代理 |
| API | FastAPI、SQLAlchemy asyncio、后台任务队列 |
| 数据 | SQLite 数据库与本地 WebP 封面缓存 |
| 部署 | Docker Compose；可选 Caddy 自动 HTTPS |

默认请求链路为：`浏览器 → web (Nginx) → api (FastAPI) → SQLite / 封面缓存`。启用 Caddy profile 后，Caddy 位于 `web` 前方负责公网 TLS。数据库和封面都存放在 `favlist-data` Docker 卷中。

## 使用

登录后，首页默认只显示编号和处理状态，不会创建封面图片请求，也不会把标题、作者或标签放入页面。需要浏览完整馆藏时，打开右上角“更多”，选择“显示敏感内容”；切换标签页、让浏览器进入后台、离开或刷新页面后会恢复隐私模式。

“快速记录”适合从普通句子中保存一个约定编号。它只提取 ASCII 数字 `0-9`，并按出现顺序拼接：`小明12天做了3本作业，错了45道题，用掉6支笔` 会记录为 `ID123456`。中文数字和全角数字不会被提取；一次需要保存多个独立编号时，使用“更多”菜单中的“批量导入”。

## 快速开始（Docker，推荐）

需要 Docker Engine 或 Docker Desktop，并启用 Compose v2（`docker compose`）。

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

### 常用运维命令

```powershell
# 查看状态与日志
docker compose ps
docker compose logs -f api web

# 拉取代码后重建升级（不会删除数据卷）
git pull --ff-only
docker compose up -d --build

# 停止或重新启动
docker compose stop
docker compose start
```

升级前建议备份 `favlist-data`。为了获得一致的 SQLite 快照，先停止 API，再将卷归档到当前目录：

```powershell
docker compose stop api
docker run --rm -v favlist_favlist-data:/data -v "${PWD}:/backup" alpine tar -czf /backup/favlist-data.tar.gz -C /data .
docker compose start api
```

Compose 项目名固定为 `favlist`，所以默认卷名是 `favlist_favlist-data`；如曾用外部工具改名，请先运行 `docker volume ls` 确认。备份文件包含收藏数据库和封面，应按敏感数据保管。恢复会覆盖现有数据，操作前请先停止服务并再次备份当前卷。

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



常用环境变量：

| 变量 | 必填 | 说明 |
| --- | --- | --- |
| `ADMIN_PASSWORD_HASH` | 是 | 管理员密码的 Argon2 哈希，不能填写明文密码 |
| `SESSION_SECRET` | 是 | 至少 32 个字符的独立随机密钥 |
| `ADMIN_USERNAME` | 否 | 管理员用户名，默认 `admin` |
| `COOKIE_SECURE` | 公网 HTTPS 时 | 公网部署必须设为 `true` |
| `WEB_PORT` / `API_PORT` | 否 | 仅绑定宿主机回环地址的本地端口 |
| `APP_DOMAIN` | Caddy profile 时 | Caddy 申请证书所用的公网域名 |

完整默认值和封面安全限制以 [`.env.example`](.env.example) 为准。

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

```powershell
# 后端
cd backend
pytest

# 前端
cd ../frontend
npm ci
npm test
npm run build

# 回到项目根目录检查部署配置
cd ..
docker compose config
```

启动后确认 `docker compose ps` 显示 `api` 和 `web` 正常运行。自动化测试不会调用真实外部服务，而是使用注入的模拟客户端。

## 项目结构

```text
backend/app/       FastAPI、数据库、认证、外部服务客户端、任务与封面处理
backend/tests/     后端单元及 API 测试
frontend/src/      React 界面、API 客户端与前端测试
config.yaml        有序标签强调配置
docker-compose.yml 本地部署与可选 Caddy profile
```
