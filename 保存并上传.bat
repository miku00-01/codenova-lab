@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ==========================================
echo   保存这次改动并上传到 GitHub
echo ==========================================
echo.

git rev-parse --is-inside-work-tree >nul 2>&1
if errorlevel 1 (
    echo [错误] 当前目录不是 git 仓库。
    echo 请确认这个文件还在 codenova-lab 文件夹里。
    echo.
    pause
    exit /b 1
)

git add -A
git diff --cached --quiet
if not errorlevel 1 (
    echo 没有需要上传的新改动。
    echo.
    git status -sb
    echo.
    pause
    exit /b 0
)

echo 这次会提交下面这些文件：
echo ------------------------------------------
git diff --cached --name-status
echo ------------------------------------------
echo.

set "msg="
set /p "msg=提交说明（直接回车，默认用「更新平台」）："
if "%msg%"=="" set "msg=更新平台"

git commit -m "%msg%"
if errorlevel 1 (
    echo.
    echo [错误] 提交失败，请把上面的提示发给 AI 看看。
    echo.
    pause
    exit /b 1
)

echo.
echo 正在推送到 GitHub...
git push
if errorlevel 1 (
    echo.
    echo [提示] 推送没有成功，常见原因：网络不通、登录过期。
    echo 可以先运行 查看改动.bat 确认本地已经提交成功。
    echo.
    pause
    exit /b 1
)

echo.
echo [完成] 已经上传到 https://github.com/miku00-01/codenova-lab
echo.
pause
