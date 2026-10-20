# CloudOps Forge 部署说明

CloudOps Forge 在服务器上以独立 Docker 容器运行，只监听 `127.0.0.1:18083`，再由现有 Nginx 的 `/cloudops/` 路径反向代理。这样不会覆盖网站首页。

## 1. 设置登录账号

```bash
cp .env.example .env
chmod 600 .env
# 编辑 .env，设置 APP_USERNAME 和强密码 APP_PASSWORD
```

未设置账号和密码时，本地双击启动仍保持原来的免登录方式；服务器 Compose 部署会强制要求这两个变量存在。

## 2. 启动容器

```bash
docker compose up -d --build
docker compose ps
curl http://127.0.0.1:18083/api/health
```

## 3. 接入现有 Nginx

把 `deploy/nginx-cloudops.conf` 中的两个 `location` 块加入现有 `server` 块，然后执行：

```bash
sudo nginx -t
sudo systemctl reload nginx
```

访问地址：`http://127.0.0.1:18082/cloudops/`（通过现有 Windows SSH 隧道）。浏览器会要求输入 `.env` 中的账号和密码。

## 4. 验证与回滚

验证：

```bash
curl -I http://127.0.0.1:18080/cloudops/
docker compose logs --tail=50
```

回滚：先恢复 Nginx 配置备份并重新加载，再在项目目录运行：

```bash
docker compose down
```
