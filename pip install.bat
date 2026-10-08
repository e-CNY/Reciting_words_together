@echo off
setlocal
chcp 65001 >nul
title 六语背单词 - 依赖安装

echo ================================================
echo   六语背单词 依赖安装脚本
echo ================================================
echo.

python --version >nul 2>nul
if errorlevel 1 (
    echo [错误] 未找到 python，请先安装 Python 3.9+ 并勾选 "Add to PATH"
    goto end
)
python --version

echo.
echo [1/4] 升级 pip
python -m pip install --upgrade pip

echo.
echo [2/4] 安装 Pillow（图片支持）
python -m pip install pillow

echo.
echo [3/4] 安装 edge-tts（在线发音）
python -m pip install edge-tts

echo.
echo [4/4] 安装 MeloTTS（本地发音，体积较大，请耐心等待）
where git >nul 2>nul
if errorlevel 1 (
    echo   [提示] 未检测到 git，尝试用 pip 直接从 GitHub 安装...
    python -m pip install git+https://github.com/myshell-ai/MeloTTS.git
) else (
    if not exist "%~dp0MeloTTS_src\.git" (
        echo   克隆 MeloTTS 源码到 MeloTTS_src 文件夹...
        git clone https://github.com/myshell-ai/MeloTTS.git "%~dp0MeloTTS_src"
    ) else (
        echo   已存在 MeloTTS_src，跳过克隆
    )
    python -m pip install -e "%~dp0MeloTTS_src"
)

echo.
echo [可选] 下载日文词典（仅 MeloTTS 日文需要）
python -m unidic download 2>nul
if errorlevel 1 echo   [提示] 失败可忽略，仅影响 MeloTTS 日文发音

echo.
echo ================================================
echo   安装完成！
echo.
echo   提醒：
echo   - 图片支持已安装 Pillow
echo   - MeloTTS 的模型文件（config.json + checkpoint.pth）
echo     还需单独下载，放进 MeloTTS 文件夹（详见 MeloTTS\说明.txt）
echo   - 若 MeloTTS 安装失败，通常是 torch 或编译依赖问题
echo ================================================

:end
echo.
pause
endlocal
