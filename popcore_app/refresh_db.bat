@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo 从Excel重新导入数据...
python init_db.py
echo.
echo 完成！重启服务器后生效。
pause
