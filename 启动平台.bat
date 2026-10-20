@echo off
chcp 936 >nul 2>nul
setlocal EnableExtensions DisableDelayedExpansion

REM 内部辅助模式：等待 Flask 监听后打开浏览器。
if /i "%~1"=="--open-browser" goto :openbrowser

REM 双击批处理时，工作目录可能不是项目目录。
cd /d "%~dp0"

title 代码练习平台 - 启动器

echo ==========================================
echo    代码练习平台 - 启动器
echo ==========================================
echo.
echo  [目录] %CD%
echo.

REM ---------- 0. 检查平台文件 ----------
if not exist "app.py" goto :nofiles
if not exist "challenges.py" goto :nofiles
if not exist "templates\" goto :nofiles
if not exist "requirements.txt" goto :nofiles

REM ---------- 1. 探测 Python ----------
set "PYEXE="
set "VPY=%CD%\.venv\Scripts\python.exe"

REM 优先使用本项目虚拟环境；不仅检查文件，还检查它能否真正运行。
if exist "%VPY%" (
    "%VPY%" -c "import sys" >nul 2>nul
    if not errorlevel 1 (
        set "PYEXE=%VPY%"
        goto :pydone
    )
)

REM 其次使用 Windows Python Launcher，避开损坏的 PATH 或商店占位程序。
where py >nul 2>nul
if not errorlevel 1 (
    for /f "delims=" %%i in ('py -3 -c "import sys; print(sys.executable)" 2^>nul') do (
        if not defined PYEXE set "PYEXE=%%i"
    )
)
if defined PYEXE goto :pydone

REM 兼容本机常见安装路径。
for %%P in (
    "C:\Users\y1572\.workbuddy\binaries\python\versions\3.13.12\python.exe"
    "C:\Users\y1572\AppData\Local\Programs\Python\Python312\python.exe"
    "C:\Python313\python.exe"
    "C:\Python312\python.exe"
) do (
    if exist %%P if not defined PYEXE set "PYEXE=%%~P"
)
if defined PYEXE goto :pydone

REM 最后检查 PATH，并排除 Microsoft Store 占位程序。
where python >nul 2>nul
if not errorlevel 1 (
    for /f "delims=" %%i in ('where python') do (
        if not defined PYEXE (
            echo %%i | findstr /i "WindowsApps" >nul
            if errorlevel 1 set "PYEXE=%%i"
        )
    )
)
if not defined PYEXE goto :nopython

:pydone
echo [1/6] Python 就绪
echo       %PYEXE%

REM ---------- 2. 准备虚拟环境 ----------
if not exist "%VPY%" (
    echo [2/6] 首次运行，正在创建虚拟环境 .venv ...
    "%PYEXE%" -m venv "%CD%\.venv"
    if errorlevel 1 goto :venvfail
    if not exist "%VPY%" goto :venvfail
) else (
    "%VPY%" -c "import sys" >nul 2>nul
    if errorlevel 1 goto :venvbroken
    echo [2/6] 虚拟环境已存在，继续使用
)

REM ---------- 3. 检查项目依赖 ----------
echo [3/6] 检查 Flask 依赖 ...
"%VPY%" -c "import flask" >nul 2>nul
if errorlevel 1 (
    echo       缺少依赖，正在按 requirements.txt 安装 ...
    "%VPY%" -m pip install --disable-pip-version-check -r requirements.txt
    if errorlevel 1 goto :pipfail
) else (
    echo       依赖已就绪，无需重复安装
)

REM ---------- 4. 选择空闲端口 ----------
echo [4/6] 检查端口 ...
set "PORT="
for /l %%p in (5000,1,5008) do (
    netstat -ano | findstr ":%%p " | findstr /i "LISTENING" >nul 2>nul
    if errorlevel 1 (
        set "PORT=%%p"
        goto :portok
    )
)
if not defined PORT goto :portfail

:portok
echo       使用端口 %PORT%

REM ---------- 5. 服务就绪后打开浏览器 ----------
if /i "%NO_BROWSER%"=="1" (
    echo [5/6] 已按 NO_BROWSER=1 跳过自动打开浏览器
) else (
    echo [5/6] 正在等待服务启动并打开浏览器 ...
    start "" /b cmd /d /c call "%~f0" --open-browser %PORT% >nul 2>nul
)

REM ---------- 6. 启动 Flask ----------
echo [6/6] 启动平台 ...
echo ------------------------------------------
echo   访问地址 : http://127.0.0.1:%PORT%
echo   平台首页 : http://127.0.0.1:%PORT%/
echo   停止服务 : 在本窗口按 Ctrl+C
echo ------------------------------------------
echo.

"%VPY%" app.py
set "APP_EXIT=%ERRORLEVEL%"
if not "%APP_EXIT%"=="0" goto :appfail

echo.
echo [提示] 平台已停止。
echo.
pause
endlocal
exit /b 0

REM ================= 浏览器等待逻辑 =================
:openbrowser
set "PORT=%~2"
for /l %%i in (1,1,30) do (
    netstat -ano | findstr ":%PORT% " | findstr /i "LISTENING" >nul 2>nul
    if not errorlevel 1 (
        start "" "http://127.0.0.1:%PORT%/"
        exit /b 0
    )
    timeout /t 1 /nobreak >nul
)
REM 服务若启动较慢，30 秒后仍打开预定地址，方便查看浏览器错误。
start "" "http://127.0.0.1:%PORT%/"
exit /b 0

REM ================= 错误处理 =================
:nopython
echo.
echo [错误] 没有找到可用的 Python。
echo        请安装 Python 3.8 或更高版本，安装时勾选 "Add Python to PATH"。
echo        官网: https://www.python.org/downloads/
echo.
pause
exit /b 1

:venvfail
echo.
echo [错误] 创建虚拟环境失败。
echo        常见原因：磁盘空间不足、杀毒软件拦截或 Python 安装损坏。
echo        可手动执行: "%PYEXE%" -m venv .venv
echo.
pause
exit /b 1

:venvbroken
echo.
echo [错误] .venv 存在，但其中的 Python 已损坏或无法运行。
echo        请把 .venv 文件夹改名为 .venv_old，再重新运行本启动器。
echo.
pause
exit /b 1

:pipfail
echo.
echo [错误] 依赖安装失败，通常是网络问题。
echo        可尝试国内镜像，手动执行:
echo        "%VPY%" -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
echo.
pause
exit /b 1

:portfail
echo.
echo [错误] 端口 5000 到 5008 都被占用，平台未启动。
echo        请关闭占用端口的程序，或扩大本脚本中的端口范围后重试。
echo.
pause
exit /b 1

:appfail
echo.
echo [错误] 平台异常退出，退出码: %APP_EXIT%
echo        请保留上方错误信息以便排查。
echo.
pause
exit /b %APP_EXIT%

:nofiles
echo.
echo [错误] 当前目录缺少平台文件（app.py / challenges.py / templates / requirements.txt）。
echo        当前目录: %CD%
echo        请把本 bat 放回平台项目目录再运行。
echo.
pause
exit /b 1
