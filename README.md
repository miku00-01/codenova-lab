# codenova-lab

一个能在浏览器里练习 Python、Shell 和 Linux 命令的小平台，可以做代码任务、编写脚本并查看运行结果。

## 可以做什么

- **代码任务**：按语言筛选任务，打开带起始代码的编辑器，运行后对照预期输出获得提示。
- **Shell 编辑器**：保存和运行脚本，练习变量、循环、管道与重定向。
- **Python 编辑器**：在浏览器内的 Pyodide 中运行 Python，适合学习基础语法、数据处理和文本解析。
- **Linux 沙箱**：练习文件、文本、日志与常见命令。
- **浅色和深色界面**：右上角按钮切换，浏览器会记住选择。

## 运行边界

Shell 和 Linux 命令由平台内置的模拟器解释，只访问项目的 `sandbox/` 练习区，不会调用 CMD、PowerShell、Git Bash、WSL 或系统 Shell。Python 在浏览器 WebAssembly 环境中运行，不能直接读取电脑文件。这里是练习环境，并非完整 Linux 虚拟机或生产服务器。

脚本保存在项目的 `scripts/` 和 `python_scripts/` 目录。请不要把个人脚本、`sandbox/` 中的练习数据或 `.env` 上传到公开仓库。

## 启动

Windows 双击 `启动平台.bat`。启动器优先使用项目自己的 `.venv`，自动选择 5000—5008 之间的空闲端口，并打开首页。

手动启动：

```powershell
.\.venv\Scripts\python.exe app.py
```

默认地址：`http://127.0.0.1:5000/`，健康检查：`http://127.0.0.1:5000/api/health`。

## 本地版本管理

双击 `版本管理.bat`，或直接调用脚本，可为当前项目打本地快照、查看版本列表、校验完整性或回滚：

```powershell
pwsh -NoProfile -File .\version-manager.ps1 -Action save -Note "说明"
pwsh -NoProfile -File .\version-manager.ps1 -Action list
pwsh -NoProfile -File .\version-manager.ps1 -Action restore -Version v001
```

快照存放在与项目同级的 `_versions/` 目录（不纳入版本库），会自动跳过 `.git`、`__pycache__`、`.venv*`、`.env` 以及 `sandbox/`、`scripts/`、`python_scripts/` 等个人数据。

服务器部署说明见 `DEPLOY.md`。通过 Nginx 挂载到 `/cloudops/` 子路径时，可用 `APP_USERNAME`、`APP_PASSWORD` 启用整站登录保护。公开部署前应配置访问控制，因为脚本保存和删除接口会修改平台数据。

## 项目结构

```text
app.py                 Flask 页面与 API
challenges.py          代码任务及起始代码
sandbox_shell.py       平台内置 Shell 模拟器
templates/             页面模板
static/style.css        界面样式
static/vendor/pyodide/ 浏览器 Python 运行组件
sandbox/               Linux/Shell 练习文件区
scripts/               Shell 脚本
python_scripts/        Python 脚本
tests/                 自动化测试
启动平台.bat           Windows 启动器
版本管理.bat           Windows 版本管理入口
version-manager.ps1    本地快照、校验与回滚
```

## 下一步适合添加的功能

1. 为任务加入多组输入和预期结果，避免只凭单一输出判断通过。
2. 在浏览器本地保存任务进度和最近打开的脚本。
3. 增加逐步提示和示例答案，但让学习者先独立尝试。
4. 加入代码格式化和键盘快捷键；若以后要支持更多语言，需要先设计隔离运行边界。
