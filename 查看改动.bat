@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ==========================================
echo   看看现在改了什么（还没上传的改动）
echo ==========================================
echo.
git rev-parse --is-inside-work-tree >nul 2>&1
if errorlevel 1 (
    echo [错误] 当前目录不是 git 仓库。
    pause
    exit /b 1
)
echo 【当前分支与领先/落后情况】
git status -sb
echo.
echo 【改动的文件统计】
git diff --stat
echo.
echo 【还没纳入 git 的新文件】
git ls-files --others --exclude-standard
echo.
echo 想上传就双击 保存并上传.bat
pause
