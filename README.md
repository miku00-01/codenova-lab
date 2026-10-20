# codenova-lab

一个能在浏览器里练习 Python、Shell 和 Linux 命令的小平台，可以做代码任务、编写脚本并查看运行结果。

## 可以做什么

- **代码任务**：13 道 Shell / Python 任务，支持按语言和完成状态筛选；做题时带起始代码、目标说明与提示。
- **服务端判题**：运行结果由服务端判定，支持一题多解、正则匹配和输出归一化（忽略行尾空白、空行、CRLF 差异），不再依赖前端字符串相等。
- **进度会保存**：每道题的通过状态、尝试次数、最近一次源码与输出都存进 SQLite，刷新或换设备重新打开都能看到自己做到哪一步。
- **Shell 编辑器**：保存和运行脚本，练习变量、循环、管道与重定向、`&&`/`||`、通配符、`$(命令)`、for/while/if 语句块。
- **Python 编辑器**：在浏览器内的 Pyodide 中运行 Python，适合学习基础语法、数据处理和文本解析。
- **Linux 沙箱**：练习文件、文本、日志、服务与容器相关的常见命令。
- **浅色和深色界面**：右上角按钮切换，浏览器会记住选择。

## 运行边界

Shell 和 Linux 命令由平台内置的模拟器解释，只访问项目的 `sandbox/` 练习区，不会调用 CMD、PowerShell、Git Bash、WSL 或系统 Shell。Python 在浏览器 WebAssembly 环境中运行，不能直接读取电脑文件。这里是练习环境，并非完整 Linux 虚拟机或生产服务器。

脚本保存在项目的 `scripts/` 和 `python_scripts/` 目录，进度保存在 `data/progress.sqlite3`。开启登录保护后，每个账号的脚本与沙箱目录互相隔离。请不要把个人脚本、`sandbox/` 中的练习数据、`data/` 里的数据库或 `.env` 上传到公开仓库（`.gitignore` 已默认排除）。

## 启动

Windows 双击 `启动平台.bat`。启动器优先使用项目自己的 `.venv`，自动选择 5000—5008 之间的空闲端口，并打开首页。

手动启动：

```powershell
python -m pip install -r requirements.txt
python app.py
```

默认地址：`http://127.0.0.1:5000/`，健康检查：`http://127.0.0.1:5000/api/health`。

## 常用配置

| 环境变量 | 作用 | 默认值 |
| --- | --- | --- |
| `SITE_NAME` | 站点名称，同时用于登录提示与浏览器标题 | `Codenova 实验室` |
| `APP_AUTH_ENABLED` | 是否启用整站 HTTP Basic 登录保护 | `1` |
| `APP_USERNAME` / `APP_PASSWORD` | 登录账号和密码，两者都为空时免登录 | 空 |
| `MULTI_USER` | 设为 `1` 时每个浏览器各自一份进度、脚本目录和 sandbox（多人共用 / 上课用） | `0` |
| `PROGRESS_DB` | 进度数据库路径 | `data/progress.sqlite3` |
| `TERMINAL_RATE_LIMIT` | 每个用户 10 秒内允许的命令条数 | `60` |
| `PORT` | 监听端口 | `5000` |

## 接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/health` | 健康检查 |
| GET | `/api/challenges` | 任务列表（不含标准答案） |
| POST | `/api/check` | 判题并记录进度 |
| GET | `/api/progress` | 当前用户进度 |
| POST | `/api/progress/reset` | 清空当前用户进度 |
| POST | `/api/terminal` | 在沙箱中执行命令 |
| POST | `/api/sandbox/reset` | 重置当前用户的沙箱目录 |
| GET/POST | `/api/scripts/*` | Shell 脚本的列表、保存、读取、删除、运行 |
| GET/POST | `/api/python-scripts/*` | Python 脚本的列表、保存、读取、删除 |

## 本地版本管理

双击 `版本管理.bat`，或直接调用脚本，可为当前项目打本地快照、查看版本列表、校验完整性或回滚：

```powershell
pwsh -NoProfile -File .\version-manager.ps1 -Action save -Note "说明"
pwsh -NoProfile -File .\version-manager.ps1 -Action list
pwsh -NoProfile -File .\version-manager.ps1 -Action restore -Version v001
```

快照存放在与项目同级的 `_versions/` 目录（不纳入版本库），会自动跳过 `.git`、`__pycache__`、`.venv*`、`.env` 以及 `sandbox/`、`scripts/`、`python_scripts/` 等个人数据。

## 改完代码怎么保存到 GitHub

改完代码后双击 `保存并上传.bat`：它会把你这次的修改提交成一次 git 记录，然后推送到这个仓库。第一次使用会提示输入提交说明，直接回车就用默认说明。想先看看改了什么，可以双击 `查看改动.bat`。

命令行等价操作：

```powershell
git status
git add -A
git commit -m "说明这次改了什么"
git push
```

服务器部署说明见 `DEPLOY.md`。通过 Nginx 挂载到子路径时，可用 `APP_USERNAME`、`APP_PASSWORD` 启用整站登录保护。公开部署前应配置访问控制，因为脚本保存和删除接口会修改平台数据。

## 项目结构

```text
app.py                 Flask 页面与 API
challenges.py          任务加载与顺序
judge.py               服务端判题（多答案 / 正则 / 归一化）
progress.py            SQLite 进度与提交记录
sandbox_shell.py       平台内置 Shell 模拟器
data/challenges.json   任务题库（可直接编辑 JSON 增删题）
templates/             页面模板
static/style.css       界面样式
static/vendor/pyodide/ 浏览器 Python 运行组件
sandbox/               Linux/Shell 练习文件区（不入库）
scripts/               Shell 脚本（不入库）
python_scripts/        Python 脚本（不入库）
data/                  题库与进度数据库（数据库不入库）
tests/                 自动化测试
.github/workflows/     GitHub Actions 持续集成
启动平台.bat           Windows 启动器
保存并上传.bat         一键提交并推送到 GitHub
查看改动.bat           查看当前未提交的修改
版本管理.bat           Windows 版本管理入口
version-manager.ps1    本地快照、校验与回滚
```

## 下一步适合添加的功能

1. 为任务加入多组输入与测试用例（不只是单个输出），支持带参数的函数级判题。
2. 增加错题本与逐题统计页面，按语言、难度汇总通过率。
3. 加入代码格式化和键盘快捷键；若以后要支持更多语言，需要先设计隔离运行边界。
4. 把 Shell 模拟器换成真正的容器沙箱（nsjail / Docker），前提是做好资源与网络限制。
