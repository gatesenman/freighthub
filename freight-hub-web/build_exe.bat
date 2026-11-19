@echo off
rem 在有 Python 3.10+ 的 Windows 上运行，生成 dist\FreightHub\（含 FreightHub.exe，无控制台窗口，免装 Python）
chcp 65001 >nul
cd /d "%~dp0"
python -m venv .venv || goto :err
call .venv\Scripts\activate.bat
pip install -r requirements.txt pyinstaller || goto :err
pyinstaller --noconfirm --name FreightHub --noconsole ^
  --add-data "app\static;app\static" ^
  --collect-submodules uvicorn --collect-submodules app --collect-all webview ^
  --hidden-import lxml.etree --hidden-import app.main run.py || goto :err
echo.
echo 完成：dist\FreightHub\FreightHub.exe  （data 目录会生成在 exe 同级）
pause
exit /b 0
:err
echo 构建失败
pause
