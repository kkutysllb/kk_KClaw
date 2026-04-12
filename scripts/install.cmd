@echo off
REM ============================================================================
REM KClaw Agent Windows 安装程序 (CMD 包装器)
REM ============================================================================
REM 此批处理文件为使用 CMD 的用户启动 PowerShell 安装程序。
REM
REM 使用方法:
REM   curl -fsSL https://raw.githubusercontent.com/kkutysllb/kk_KClaw/main/scripts/install.cmd -o install.cmd && install.cmd && del install.cmd
REM
REM 或者如果您已经在 PowerShell 中，请直接使用以下命令:
REM   irm https://raw.githubusercontent.com/kkutysllb/kk_KClaw/main/scripts/install.ps1 | iex
REM ============================================================================

echo.
echo  KClaw Agent 安装程序
echo  正在启动 PowerShell 安装程序...
echo.

powershell -ExecutionPolicy ByPass -NoProfile -Command "irm https://raw.githubusercontent.com/kkutysllb/kk_KClaw/main/scripts/install.ps1 | iex"

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo  安装失败。请尝试直接运行 PowerShell:
    echo    powershell -ExecutionPolicy ByPass -c "irm https://raw.githubusercontent.com/kkutysllb/kk_KClaw/main/scripts/install.ps1 | iex"
    echo.
    pause
    exit /b 1
)
