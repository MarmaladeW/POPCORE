@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo ========================================
echo  POPCORE 库存系统
echo ========================================
echo.

if not exist popcore.db (
    echo 初始化数据库...
    python init_db.py
    if errorlevel 1 (
        echo 数据库初始化失败，请检查Excel文件路径
        pause
        exit /b
    )
    echo.
)

echo 正在获取本机IP地址...
for /f "tokens=2 delims=:" %%a in ('ipconfig ^| findstr /i "IPv4" ^| findstr /v "169.254"') do (
    set LOCAL_IP=%%a
    goto :found
)
:found
set LOCAL_IP=%LOCAL_IP: =%

echo.
echo ========================================
echo  手机访问地址（同一WiFi下）:
echo  http://%LOCAL_IP%:5000
echo ========================================
echo.
echo 启动服务器... 按 Ctrl+C 退出
echo.
start "" http://localhost:5000
python app.py
pause
