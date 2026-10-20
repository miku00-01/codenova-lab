# Codenova 实验室 部署说明

Codenova 实验室在服务器上以独立 Docker 容器运行，只监听 `127.0.0.1:18083`，再由现有 Nginx 的一个子路径（示例用 `/cloudops/`，可以改成 `/codenova/`）反向代理。这样不会覆盖网站首页。

容器里会写入两类数据，建议挂载成卷，避免重建容器时丢失：

- `data/`：题库与 `progress.sqlite3` 进度数据库；
- `sandbox/`、`scripts/`、`python_scripts/`：练习文件与用户脚本（开启登录保护后按账号分目录）。

## 1. 设置登录账号

```bash
cp .env.example .env
chmod 600 .env
# 编辑 .env，设置 APP_USERNAME 和强密码 APP_PASSWORD
```

未设置账号和密码时，本地双击启动仍保持原来的免登录方式；服务器 Compose 部署会强制要求这两个变量存在。公开部署一定要开启登录保护，否则任何人都能写入脚本目录。

## 2. 启动容器

```bash
docker compose up -d --build
docker compose ps
curl http://127.0.0.1:18083/api/health
```

## 3. 接入现有 Nginx

把 `deploy/nginx-cloudops.conf` 中的两个 `location` 块加入现有 `server` 块（路径前缀按需改名），然后执行：

```bash
sudo nginx -t
sudo systemctl reload nginx
```

访问地址：`http://127.0.0.1:18082/cloudops/`（通过现有 Windows SSH 隧道）。浏览器会要求输入 `.env` 中的账号和密码。

## 4. 验证与回滚

验证：

```bash
curl -I http://127.0.0.1:18080/cloudops/
curl -s http://127.0.0.1:18083/api/health
docker compose logs --tail=50
```

回滚：先恢复 Nginx 配置备份并重新加载，再在项目目录运行：

```bash
docker compose down
```

## 5. 常用环境变量

| 变量 | 说明 |
| --- | --- |
| `APP_AUTH_ENABLED` | `1` 开启整站登录保护（默认），`0` 关闭 |
| `APP_USERNAME` / `APP_PASSWORD` | Basic 登录账号与密码 |
| `SITE_NAME` | 站点名称，默认 `Codenova 实验室` |
| `PROGRESS_DB` | 进度数据库路径，默认 `data/progress.sqlite3` |
| `TERMINAL_RATE_LIMIT` | 每 10 秒允许的命令条数，默认 `60` |

装了 `flask-compress`（可选依赖）时，静态资源会自动 gzip，Pyodide 运行时那十几 MB 能省下不少流量。
