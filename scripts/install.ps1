# ============================================================================
# KClaw Agent Windows 安装程序
# ============================================================================
# 适用于 Windows 的安装脚本 (PowerShell)。
# 使用 uv 进行快速 Python 环境配置和包管理。
#
# 使用方式:
#   irm https://raw.githubusercontent.com/kkutysllb/kk_KClaw/main/scripts/install.ps1 | iex
#
# 或下载后带选项运行:
#   .\install.ps1 -NoVenv -SkipSetup
#
# ============================================================================
param(
    [switch]$NoVenv,
    [switch]$SkipSetup,
    [string]$Branch = "main",
    [string]$KClawHome = "$env:LOCALAPPDATA\kclaw",
    [string]$InstallDir = "$env:LOCALAPPDATA\kclaw\kclaw"
)

$ErrorActionPreference = "Stop"

# ============================================================================
# Configuration
# ============================================================================

$RepoUrlSsh = "git@github.com:kkutysllb/kk_KClaw.git"
$RepoUrlHttps = "https://github.com/kkutysllb/kk_KClaw.git"
$PythonVersion = "3.11"
$NodeVersion = "22"

# ============================================================================
# Helper functions
# ============================================================================

function Write-Banner {
    Write-Host ""
    Write-Host "┌─────────────────────────────────────────────────────────┐" -ForegroundColor Magenta
    Write-Host "│             ⚕ KClaw Agent 安装程序                    │" -ForegroundColor Magenta
    Write-Host "├─────────────────────────────────────────────────────────┤" -ForegroundColor Magenta
    Write-Host "│  由 kkutysllb 独立开发的开源 AI Agent。              │" -ForegroundColor Magenta
    Write-Host "└─────────────────────────────────────────────────────────┘" -ForegroundColor Magenta
    Write-Host ""
}

function Write-Info {
    param([string]$Message)
    Write-Host "→ $Message" -ForegroundColor Cyan
}

function Write-Success {
    param([string]$Message)
    Write-Host "✓ $Message" -ForegroundColor Green
}

function Write-Warn {
    param([string]$Message)
    Write-Host "⚠ $Message" -ForegroundColor Yellow
}

function Write-Err {
    param([string]$Message)
    Write-Host "✗ $Message" -ForegroundColor Red
}

# ============================================================================
# Dependency checks
# ============================================================================

function Install-Uv {
    Write-Info "正在检查 uv 包管理器..."
    
    # Check if uv is already available
    if (Get-Command uv -ErrorAction SilentlyContinue) {
        $version = uv --version
        $script:UvCmd = "uv"
        Write-Success "已找到 uv ($version)"
        return $true
    }
    
    # Check common install locations
    $uvPaths = @(
        "$env:USERPROFILE\.local\bin\uv.exe",
        "$env:USERPROFILE\.cargo\bin\uv.exe"
    )
    foreach ($uvPath in $uvPaths) {
        if (Test-Path $uvPath) {
            $script:UvCmd = $uvPath
            $version = & $uvPath --version
                Write-Success "已在 $uvPath 找到 uv ($version)"
            return $true
        }
    }
    
    # Install uv
    Write-Info "正在安装 uv (快速 Python 包管理器)..."
    try {
        powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex" 2>&1 | Out-Null
        
        # Find the installed binary
        $uvExe = "$env:USERPROFILE\.local\bin\uv.exe"
        if (-not (Test-Path $uvExe)) {
            $uvExe = "$env:USERPROFILE\.cargo\bin\uv.exe"
        }
        if (-not (Test-Path $uvExe)) {
            # Refresh PATH and try again
            $env:Path = [Environment]::GetEnvironmentVariable("Path", "User") + ";" + [Environment]::GetEnvironmentVariable("Path", "Machine")
            if (Get-Command uv -ErrorAction SilentlyContinue) {
                $uvExe = (Get-Command uv).Source
            }
        }
        
        if (Test-Path $uvExe) {
            $script:UvCmd = $uvExe
            $version = & $uvExe --version
            Write-Success "uv 安装成功 ($version)"
            return $true
        }
        
        Write-Err "uv 已安装但未在 PATH 中找到"
        Write-Info "请重启终端后重新运行"
        return $false
    } catch {
        Write-Err "uv 安装失败"
        Write-Info "手动安装: https://docs.astral.sh/uv/getting-started/installation/"
        return $false
    }
}

function Test-Python {
    Write-Info "正在检查 Python $PythonVersion..."
    
    # Let uv find or install Python
    try {
        $pythonPath = & $UvCmd python find $PythonVersion 2>$null
        if ($pythonPath) {
            $ver = & $pythonPath --version 2>$null
            Write-Success "已找到 Python: $ver"
            return $true
        }
    } catch { }
    
    # Python not found — use uv to install it (no admin needed!)
    Write-Info "未找到 Python $PythonVersion，正在通过 uv 安装..."
    try {
        $uvOutput = & $UvCmd python install $PythonVersion 2>&1
        if ($LASTEXITCODE -eq 0) {
            $pythonPath = & $UvCmd python find $PythonVersion 2>$null
            if ($pythonPath) {
                $ver = & $pythonPath --version 2>$null
                Write-Success "Python 安装成功: $ver"
                return $true
            }
        } else {
            Write-Warn "uv python install output:"
            Write-Host $uvOutput -ForegroundColor DarkGray
        }
    } catch {
        Write-Warn "uv python install error: $_"
    }

    # Fallback: check if ANY Python 3.10+ is already available on the system
    Write-Info "正在尝试查找已有的 Python 3.10+..."
    foreach ($fallbackVer in @("3.12", "3.13", "3.10")) {
        try {
            $pythonPath = & $UvCmd python find $fallbackVer 2>$null
            if ($pythonPath) {
                $ver = & $pythonPath --version 2>$null
                Write-Success "找到备选版本: $ver"
                $script:PythonVersion = $fallbackVer
                return $true
            }
        } catch { }
    }

    # Fallback: try system python
    if (Get-Command python -ErrorAction SilentlyContinue) {
        $sysVer = python --version 2>$null
        if ($sysVer -match "3\.(1[0-9]|[1-9][0-9])") {
            Write-Success "使用系统 Python: $sysVer"
            return $true
        }
    }
    
    Write-Err "Python $PythonVersion 安装失败"
    Write-Info "请手动安装 Python 3.11，然后重新运行此脚本:"
    Write-Info "  https://www.python.org/downloads/"
    Write-Info "  或: winget install Python.Python.3.11"
    return $false
}

function Test-Git {
    Write-Info "正在检查 Git..."
    
    if (Get-Command git -ErrorAction SilentlyContinue) {
        $version = git --version
        Write-Success "已找到 Git ($version)"
        return $true
    }
    
    Write-Err "未找到 Git"
    Write-Info "请从以下地址安装 Git:"
    Write-Info "  https://git-scm.com/download/win"
    return $false
}

function Test-Node {
    Write-Info "正在检查 Node.js (浏览器工具所需)..."

    if (Get-Command node -ErrorAction SilentlyContinue) {
        $version = node --version
        Write-Success "已找到 Node.js $version"
        $script:HasNode = $true
        return $true
    }

    # Check our own managed install from a previous run
    $managedNode = "$KClawHome\node\node.exe"
    if (Test-Path $managedNode) {
        $version = & $managedNode --version
        $env:Path = "$KClawHome\node;$env:Path"
        Write-Success "已找到 Node.js $version (KClaw 管理)"
        $script:HasNode = $true
        return $true
    }

    Write-Info "未找到 Node.js — 正在安装 Node.js $NodeVersion LTS..."

    # Try winget first (cleanest on modern Windows)
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        Write-Info "正在通过 winget 安装..."
        try {
            winget install OpenJS.NodeJS.LTS --silent --accept-package-agreements --accept-source-agreements 2>&1 | Out-Null
            # Refresh PATH
            $env:Path = [Environment]::GetEnvironmentVariable("Path", "User") + ";" + [Environment]::GetEnvironmentVariable("Path", "Machine")
            if (Get-Command node -ErrorAction SilentlyContinue) {
                $version = node --version
                Write-Success "Node.js $version 已通过 winget 安装"
                $script:HasNode = $true
                return $true
            }
        } catch { }
    }

    # Fallback: download binary zip to ~/.kclaw/node/
    Write-Info "正在下载 Node.js $NodeVersion 二进制包..."
    try {
        $arch = if ([Environment]::Is64BitOperatingSystem) { "x64" } else { "x86" }
        $indexUrl = "https://nodejs.org/dist/latest-v${NodeVersion}.x/"
        $indexPage = Invoke-WebRequest -Uri $indexUrl -UseBasicParsing
        $zipName = ($indexPage.Content | Select-String -Pattern "node-v${NodeVersion}\.\d+\.\d+-win-${arch}\.zip" -AllMatches).Matches[0].Value

        if ($zipName) {
            $downloadUrl = "${indexUrl}${zipName}"
            $tmpZip = "$env:TEMP\$zipName"
            $tmpDir = "$env:TEMP\kclaw-node-extract"

            Invoke-WebRequest -Uri $downloadUrl -OutFile $tmpZip -UseBasicParsing
            if (Test-Path $tmpDir) { Remove-Item -Recurse -Force $tmpDir }
            Expand-Archive -Path $tmpZip -DestinationPath $tmpDir -Force

            $extractedDir = Get-ChildItem $tmpDir -Directory | Select-Object -First 1
            if ($extractedDir) {
                if (Test-Path "$KClawHome\node") { Remove-Item -Recurse -Force "$KClawHome\node" }
                Move-Item $extractedDir.FullName "$KClawHome\node"
                $env:Path = "$KClawHome\node;$env:Path"

                $version = & "$KClawHome\node\node.exe" --version
                Write-Success "Node.js $version 已安装到 ~/.kclaw/node/"
                $script:HasNode = $true

                Remove-Item -Force $tmpZip -ErrorAction SilentlyContinue
                Remove-Item -Recurse -Force $tmpDir -ErrorAction SilentlyContinue
                return $true
            }
        }
    } catch {
        Write-Warn "Download failed: $_"
    }

    Write-Warn "无法自动安装 Node.js"
    Write-Info "手动安装: https://nodejs.org/en/download/"
    $script:HasNode = $false
    return $true
}

function Install-SystemPackages {
    $script:HasRipgrep = $false
    $script:HasFfmpeg = $false
    $needRipgrep = $false
    $needFfmpeg = $false

    Write-Info "正在检查 ripgrep (快速文件搜索)..."
    if (Get-Command rg -ErrorAction SilentlyContinue) {
        $version = rg --version | Select-Object -First 1
        Write-Success "$version found"
        $script:HasRipgrep = $true
    } else {
        $needRipgrep = $true
    }

    Write-Info "正在检查 ffmpeg (TTS 语音消息)..."
    if (Get-Command ffmpeg -ErrorAction SilentlyContinue) {
        Write-Success "已找到 ffmpeg"
        $script:HasFfmpeg = $true
    } else {
        $needFfmpeg = $true
    }

    if (-not $needRipgrep -and -not $needFfmpeg) { return }

    # Build description and package lists for each package manager
    $descParts = @()
    $wingetPkgs = @()
    $chocoPkgs = @()
    $scoopPkgs = @()

    if ($needRipgrep) {
        $descParts += "ripgrep 用于加速文件搜索"
        $wingetPkgs += "BurntSushi.ripgrep.MSVC"
        $chocoPkgs += "ripgrep"
        $scoopPkgs += "ripgrep"
    }
    if ($needFfmpeg) {
        $descParts += "ffmpeg 用于 TTS 语音消息"
        $wingetPkgs += "Gyan.FFmpeg"
        $chocoPkgs += "ffmpeg"
        $scoopPkgs += "ffmpeg"
    }

    $description = $descParts -join " and "
    $hasWinget = Get-Command winget -ErrorAction SilentlyContinue
    $hasChoco = Get-Command choco -ErrorAction SilentlyContinue
    $hasScoop = Get-Command scoop -ErrorAction SilentlyContinue

    # Try winget first (most common on modern Windows)
    if ($hasWinget) {
        Write-Info "正在通过 winget 安装 $description..."
        foreach ($pkg in $wingetPkgs) {
            try {
                winget install $pkg --silent --accept-package-agreements --accept-source-agreements 2>&1 | Out-Null
            } catch { }
        }
        # Refresh PATH and recheck
        $env:Path = [Environment]::GetEnvironmentVariable("Path", "User") + ";" + [Environment]::GetEnvironmentVariable("Path", "Machine")
        if ($needRipgrep -and (Get-Command rg -ErrorAction SilentlyContinue)) {
            Write-Success "ripgrep 安装成功"
            $script:HasRipgrep = $true
            $needRipgrep = $false
        }
        if ($needFfmpeg -and (Get-Command ffmpeg -ErrorAction SilentlyContinue)) {
            Write-Success "ffmpeg 安装成功"
            $script:HasFfmpeg = $true
            $needFfmpeg = $false
        }
        if (-not $needRipgrep -and -not $needFfmpeg) { return }
    }

    # Fallback: choco
    if ($hasChoco -and ($needRipgrep -or $needFfmpeg)) {
        Write-Info "正在尝试 Chocolatey..."
        foreach ($pkg in $chocoPkgs) {
            try { choco install $pkg -y 2>&1 | Out-Null } catch { }
        }
        if ($needRipgrep -and (Get-Command rg -ErrorAction SilentlyContinue)) {
            Write-Success "ripgrep 已通过 Chocolatey 安装"
            $script:HasRipgrep = $true
            $needRipgrep = $false
        }
        if ($needFfmpeg -and (Get-Command ffmpeg -ErrorAction SilentlyContinue)) {
            Write-Success "ffmpeg 已通过 Chocolatey 安装"
            $script:HasFfmpeg = $true
            $needFfmpeg = $false
        }
    }

    # Fallback: scoop
    if ($hasScoop -and ($needRipgrep -or $needFfmpeg)) {
        Write-Info "正在尝试 Scoop..."
        foreach ($pkg in $scoopPkgs) {
            try { scoop install $pkg 2>&1 | Out-Null } catch { }
        }
        if ($needRipgrep -and (Get-Command rg -ErrorAction SilentlyContinue)) {
            Write-Success "ripgrep 已通过 Scoop 安装"
            $script:HasRipgrep = $true
            $needRipgrep = $false
        }
        if ($needFfmpeg -and (Get-Command ffmpeg -ErrorAction SilentlyContinue)) {
            Write-Success "ffmpeg 已通过 Scoop 安装"
            $script:HasFfmpeg = $true
            $needFfmpeg = $false
        }
    }

    # Show manual instructions for anything still missing
    if ($needRipgrep) {
        Write-Warn "ripgrep 未安装 (文件搜索将使用 findstr 备选方案)"
        Write-Info "  winget install BurntSushi.ripgrep.MSVC"
    }
    if ($needFfmpeg) {
        Write-Warn "ffmpeg 未安装 (TTS 语音消息功能将受限)"
        Write-Info "  winget install Gyan.FFmpeg"
    }
}

# ============================================================================
# Installation
# ============================================================================

function Install-Repository {
    Write-Info "正在安装到 $InstallDir..."
    
    if (Test-Path $InstallDir) {
        if (Test-Path "$InstallDir\.git") {
            Write-Info "发现已有安装，正在更新..."
            Push-Location $InstallDir
            git -c windows.appendAtomically=false fetch origin
            git -c windows.appendAtomically=false checkout $Branch
            git -c windows.appendAtomically=false pull origin $Branch
            Pop-Location
        } else {
            Write-Err "目录已存在但不是 Git 仓库: $InstallDir"
            Write-Info "请删除该目录或使用 -InstallDir 指定其他目录"
            throw "目录已存在但不是 Git 仓库: $InstallDir"
        }
    } else {
        $cloneSuccess = $false

        # Fix Windows git "copy-fd: write returned: Invalid argument" error.
        # Git for Windows can fail on atomic file operations (hook templates,
        # config lock files) due to antivirus, OneDrive, or NTFS filter drivers.
        # The -c flag injects config before any file I/O occurs.
        Write-Info "正在配置 Windows Git 兼容性..."
        $env:GIT_CONFIG_COUNT = "1"
        $env:GIT_CONFIG_KEY_0 = "windows.appendAtomically"
        $env:GIT_CONFIG_VALUE_0 = "false"
        git config --global windows.appendAtomically false 2>$null

        # Try SSH first, then HTTPS, with -c flag for atomic write fix
        Write-Info "正在尝试 SSH 克隆..."
        $env:GIT_SSH_COMMAND = "ssh -o BatchMode=yes -o ConnectTimeout=5"
        try {
            git -c windows.appendAtomically=false clone --branch $Branch --recurse-submodules $RepoUrlSsh $InstallDir
            if ($LASTEXITCODE -eq 0) { $cloneSuccess = $true }
        } catch { }
        $env:GIT_SSH_COMMAND = $null
        
        if (-not $cloneSuccess) {
            if (Test-Path $InstallDir) { Remove-Item -Recurse -Force $InstallDir -ErrorAction SilentlyContinue }
            Write-Info "SSH 失败，正在尝试 HTTPS..."
            try {
                git -c windows.appendAtomically=false clone --branch $Branch --recurse-submodules $RepoUrlHttps $InstallDir
                if ($LASTEXITCODE -eq 0) { $cloneSuccess = $true }
            } catch { }
        }

        # Fallback: download ZIP archive (bypasses git file I/O issues entirely)
        if (-not $cloneSuccess) {
            if (Test-Path $InstallDir) { Remove-Item -Recurse -Force $InstallDir -ErrorAction SilentlyContinue }
            Write-Warn "Git 克隆失败 — 正在下载 ZIP 压缩包..."
            try {
                $zipUrl = "https://github.com/kkutysllb/kk_KClaw/archive/refs/heads/$Branch.zip"
                $zipPath = "$env:TEMP\kclaw-$Branch.zip"
                $extractPath = "$env:TEMP\kclaw-extract"
                
                Invoke-WebRequest -Uri $zipUrl -OutFile $zipPath -UseBasicParsing
                if (Test-Path $extractPath) { Remove-Item -Recurse -Force $extractPath }
                Expand-Archive -Path $zipPath -DestinationPath $extractPath -Force
                
                # GitHub ZIPs extract to repo-branch/ subdirectory
                $extractedDir = Get-ChildItem $extractPath -Directory | Select-Object -First 1
                if ($extractedDir) {
                    New-Item -ItemType Directory -Force -Path (Split-Path $InstallDir) -ErrorAction SilentlyContinue | Out-Null
                    Move-Item $extractedDir.FullName $InstallDir -Force
                    Write-Success "下载并解压完成"
                    
                    # Initialize git repo so updates work later
                    Push-Location $InstallDir
                    git -c windows.appendAtomically=false init 2>$null
                    git -c windows.appendAtomically=false config windows.appendAtomically false 2>$null
                    git remote add origin $RepoUrlHttps 2>$null
                    Pop-Location
                    Write-Success "Git 仓库已初始化，支持后续更新"
                    
                    $cloneSuccess = $true
                }
                
                # Cleanup temp files
                Remove-Item -Force $zipPath -ErrorAction SilentlyContinue
                Remove-Item -Recurse -Force $extractPath -ErrorAction SilentlyContinue
            } catch {
                Write-Err "ZIP 下载也失败: $_"
            }
        }

        if (-not $cloneSuccess) {
            throw "下载仓库失败 (已尝试 SSH、HTTPS 克隆和 ZIP 下载)"
        }
    }
    
    # Set per-repo config (harmless if it fails)
    Push-Location $InstallDir
    git -c windows.appendAtomically=false config windows.appendAtomically false 2>$null

    # Ensure submodules are initialized and updated
    Write-Info "正在初始化子模块..."
    git -c windows.appendAtomically=false submodule update --init --recursive 2>$null
    if ($LASTEXITCODE -ne 0) {
        Write-Warn "子模块初始化失败 (终端/RL 工具可能需要手动设置)"
    } else {
        Write-Success "子模块就绪"
    }
    Pop-Location
    
    Write-Success "仓库就绪"
}

function Install-Venv {
    if ($NoVenv) {
        Write-Info "跳过虚拟环境 (-NoVenv)"
        return
    }
    
    Write-Info "正在创建 Python $PythonVersion 虚拟环境..."
    
    Push-Location $InstallDir
    
    if (Test-Path "venv") {
        Write-Info "虚拟环境已存在，正在重新创建..."
        Remove-Item -Recurse -Force "venv"
    }
    
    # uv creates the venv and pins the Python version in one step
    & $UvCmd venv venv --python $PythonVersion
    
    Pop-Location
    
    Write-Success "虚拟环境就绪 (Python $PythonVersion)"
}

function Install-Dependencies {
    Write-Info "正在安装依赖..."
    
    Push-Location $InstallDir
    
    if (-not $NoVenv) {
        # Tell uv to install into our venv (no activation needed)
        $env:VIRTUAL_ENV = "$InstallDir\venv"
    }
    
    # Install main package with all extras
    try {
        & $UvCmd pip install -e ".[all]" 2>&1 | Out-Null
    } catch {
        & $UvCmd pip install -e "." | Out-Null
    }
    
    Write-Success "主包安装成功"
    
    # Install optional submodules
    Write-Info "正在安装 tinker-atropos (RL 训练后端)..."
    if (Test-Path "tinker-atropos\pyproject.toml") {
        try {
            & $UvCmd pip install -e ".\tinker-atropos" 2>&1 | Out-Null
            Write-Success "tinker-atropos 安装成功"
        } catch {
            Write-Warn "tinker-atropos 安装失败 (RL 工具可能不可用)"
        }
    } else {
        Write-Warn "未找到 tinker-atropos (运行: git submodule update --init)"
    }
    
    Pop-Location
    
    Write-Success "所有依赖安装完成"
}

function Set-PathVariable {
    Write-Info "正在设置 kclaw 命令..."
    
    if ($NoVenv) {
        $kclawBin = "$InstallDir"
    } else {
        $kclawBin = "$InstallDir\venv\Scripts"
    }
    
    # Add the venv Scripts dir to user PATH so kclaw is globally available
    # On Windows, the kclaw.exe in venv\Scripts\ has the venv Python baked in
    $currentPath = [Environment]::GetEnvironmentVariable("Path", "User")
    
    if ($currentPath -notlike "*$kclawBin*") {
        [Environment]::SetEnvironmentVariable(
            "Path",
            "$kclawBin;$currentPath",
            "User"
        )
        Write-Success "已添加到用户 PATH: $kclawBin"
    } else {
        Write-Info "PATH 已配置"
    }
    
    # Set KCLAW_HOME so the Python code finds config/data in the right place.
    # Only needed on Windows where we install to %LOCALAPPDATA%\kclaw instead
    # of the Unix default ~/.kclaw
    $currentKClawHome = [Environment]::GetEnvironmentVariable("KCLAW_HOME", "User")
    if (-not $currentKClawHome -or $currentKClawHome -ne $KClawHome) {
        [Environment]::SetEnvironmentVariable("KCLAW_HOME", $KClawHome, "User")
        Write-Success "已设置 KCLAW_HOME=$KClawHome"
    }
    $env:KCLAW_HOME = $KClawHome
    
    # Update current session
    $env:Path = "$kclawBin;$env:Path"
    
    Write-Success "kclaw 命令就绪"
}

function Copy-ConfigTemplates {
    Write-Info "正在设置配置文件..."
    
    # Create ~/.kclaw directory structure
    New-Item -ItemType Directory -Force -Path "$KClawHome\cron" | Out-Null
    New-Item -ItemType Directory -Force -Path "$KClawHome\sessions" | Out-Null
    New-Item -ItemType Directory -Force -Path "$KClawHome\logs" | Out-Null
    New-Item -ItemType Directory -Force -Path "$KClawHome\pairing" | Out-Null
    New-Item -ItemType Directory -Force -Path "$KClawHome\hooks" | Out-Null
    New-Item -ItemType Directory -Force -Path "$KClawHome\image_cache" | Out-Null
    New-Item -ItemType Directory -Force -Path "$KClawHome\audio_cache" | Out-Null
    New-Item -ItemType Directory -Force -Path "$KClawHome\memories" | Out-Null
    New-Item -ItemType Directory -Force -Path "$KClawHome\skills" | Out-Null
    New-Item -ItemType Directory -Force -Path "$KClawHome\whatsapp\session" | Out-Null
    
    # Create .env
    $envPath = "$KClawHome\.env"
    if (-not (Test-Path $envPath)) {
        $examplePath = "$InstallDir\.env.example"
        if (Test-Path $examplePath) {
            Copy-Item $examplePath $envPath
            Write-Success "已从模板创建 ~/.kclaw/.env"
        } else {
            New-Item -ItemType File -Force -Path $envPath | Out-Null
            Write-Success "已创建 ~/.kclaw/.env"
        }
    } else {
        Write-Info "~/.kclaw/.env 已存在，保留不变"
    }
    
    # Create config.yaml
    $configPath = "$KClawHome\config.yaml"
    if (-not (Test-Path $configPath)) {
        $examplePath = "$InstallDir\cli-config.yaml.example"
        if (Test-Path $examplePath) {
            Copy-Item $examplePath $configPath
            Write-Success "已从模板创建 ~/.kclaw/config.yaml"
        }
    } else {
        Write-Info "~/.kclaw/config.yaml 已存在，保留不变"
    }
    
    # Create SOUL.md if it doesn't exist (global persona file)
    $soulPath = "$KClawHome\SOUL.md"
    if (-not (Test-Path $soulPath)) {
        @"
# KClaw Agent Persona

<!-- 
This file defines the agent's personality and tone.
The agent will embody whatever you write here.
Edit this to customize how KClaw communicates with you.

Examples:
  - "You are a warm, playful assistant who uses kaomoji occasionally."
  - "You are a concise technical expert. No fluff, just facts."
  - "You speak like a friendly coworker who happens to know everything."

This file is loaded fresh each message -- no restart needed.
Delete the contents (or this file) to use the default personality.
-->
"@ | Set-Content -Path $soulPath -Encoding UTF8
        Write-Success "已创建 ~/.kclaw/SOUL.md (编辑以自定义个性)"
    }
    
    Write-Success "配置目录就绪: ~/.kclaw/"
    
    # Seed bundled skills into ~/.kclaw/skills/ (manifest-based, one-time per skill)
    Write-Info "正在同步内置技能到 ~/.kclaw/skills/ ..."
    $pythonExe = "$InstallDir\venv\Scripts\python.exe"
    if (Test-Path $pythonExe) {
        try {
            & $pythonExe "$InstallDir\tools\skills_sync.py" 2>$null
            Write-Success "技能已同步到 ~/.kclaw/skills/"
        } catch {
            # Fallback: simple directory copy
            $bundledSkills = "$InstallDir\skills"
            $userSkills = "$KClawHome\skills"
            if ((Test-Path $bundledSkills) -and -not (Get-ChildItem $userSkills -Exclude '.bundled_manifest' -ErrorAction SilentlyContinue)) {
                Copy-Item -Path "$bundledSkills\*" -Destination $userSkills -Recurse -Force -ErrorAction SilentlyContinue
                Write-Success "技能已复制到 ~/.kclaw/skills/"
            }
        }
    }
}

function Install-NodeDeps {
    if (-not $HasNode) {
        Write-Info "跳过 Node.js 依赖 (未安装 Node.js)"
        return
    }
    
    Push-Location $InstallDir
    
    if (Test-Path "package.json") {
        Write-Info "正在安装 Node.js 依赖 (浏览器工具)..."
        try {
            npm install --silent 2>&1 | Out-Null
            Write-Success "Node.js 依赖安装完成"
        } catch {
            Write-Warn "npm install 失败 (浏览器工具可能不可用)"
        }
    }
    
    # Install WhatsApp bridge dependencies
    $bridgeDir = "$InstallDir\scripts\whatsapp-bridge"
    if (Test-Path "$bridgeDir\package.json") {
        Write-Info "正在安装 WhatsApp 桥接依赖..."
        Push-Location $bridgeDir
        try {
            npm install --silent 2>&1 | Out-Null
            Write-Success "WhatsApp 桥接依赖安装完成"
        } catch {
            Write-Warn "WhatsApp 桥接 npm install 失败 (WhatsApp 可能不可用)"
        }
        Pop-Location
    }
    
    Pop-Location
}

function Invoke-SetupWizard {
    if ($SkipSetup) {
        Write-Info "跳过设置向导 (-SkipSetup)"
        return
    }
    
    Write-Host ""
    Write-Info "正在启动设置向导..."
    Write-Host ""
    
    Push-Location $InstallDir
    
    # Run kclaw setup using the venv Python directly (no activation needed)
    if (-not $NoVenv) {
        & ".\venv\Scripts\python.exe" -m kclaw_cli.main setup
    } else {
        python -m kclaw_cli.main setup
    }
    
    Pop-Location
}

function Start-GatewayIfConfigured {
    $envPath = "$KClawHome\.env"
    if (-not (Test-Path $envPath)) { return }

    $hasMessaging = $false
    $content = Get-Content $envPath -ErrorAction SilentlyContinue
    foreach ($var in @("TELEGRAM_BOT_TOKEN", "DISCORD_BOT_TOKEN", "SLACK_BOT_TOKEN", "SLACK_APP_TOKEN", "WHATSAPP_ENABLED")) {
        $match = $content | Where-Object { $_ -match "^${var}=.+" -and $_ -notmatch "your-token-here" }
        if ($match) { $hasMessaging = $true; break }
    }

    if (-not $hasMessaging) { return }

    $kclawCmd = "$InstallDir\venv\Scripts\kclaw.exe"
    if (-not (Test-Path $kclawCmd)) {
        $kclawCmd = "kclaw"
    }

    # If WhatsApp is enabled but not yet paired, run foreground for QR scan
    $whatsappEnabled = $content | Where-Object { $_ -match "^WHATSAPP_ENABLED=true" }
    $whatsappSession = "$KClawHome\whatsapp\session\creds.json"
    if ($whatsappEnabled -and -not (Test-Path $whatsappSession)) {
        Write-Host ""
        Write-Info "WhatsApp 已启用但尚未配对。"
        Write-Info "正在运行 'kclaw whatsapp' 以通过二维码配对..."
        Write-Host ""
        $response = Read-Host "是否现在配对 WhatsApp? [Y/n]"
        if ($response -eq "" -or $response -match "^[Yy]") {
            try {
                & $kclawCmd whatsapp
            } catch {
                # Expected after pairing completes
            }
        }
    }

    Write-Host ""
    Write-Info "检测到消息平台令牌!"
    Write-Info "网关负责处理消息平台和定时任务执行。"
    Write-Host ""
    $response = Read-Host "是否现在启动网关? [Y/n]"

    if ($response -eq "" -or $response -match "^[Yy]") {
        Write-Info "正在后台启动网关..."
        try {
            $logFile = "$KClawHome\logs\gateway.log"
            Start-Process -FilePath $kclawCmd -ArgumentList "gateway" `
                -RedirectStandardOutput $logFile `
                -RedirectStandardError "$KClawHome\logs\gateway-error.log" `
                -WindowStyle Hidden
            Write-Success "网关已启动! 您的机器人现在在线。"
            Write-Info "日志: $logFile"
            Write-Info "停止: 从任务管理器关闭网关进程"
        } catch {
            Write-Warn "网关启动失败。手动运行: kclaw gateway"
        }
    } else {
        Write-Info "已跳过。稍后启动网关: kclaw gateway"
    }
}

function Write-Completion {
    Write-Host ""
    Write-Host "┌─────────────────────────────────────────────────────────┐" -ForegroundColor Green
    Write-Host "│              ✓ 安装完成!                              │" -ForegroundColor Green
    Write-Host "└─────────────────────────────────────────────────────────┘" -ForegroundColor Green
    Write-Host ""
    
    # Show file locations
    Write-Host "📁 您的文件:" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "   配置文件:    " -NoNewline -ForegroundColor Yellow
    Write-Host "$KClawHome\config.yaml"
    Write-Host "   API 密钥:  " -NoNewline -ForegroundColor Yellow
    Write-Host "$KClawHome\.env"
    Write-Host "   数据:      " -NoNewline -ForegroundColor Yellow
    Write-Host "$KClawHome\cron\, sessions\, logs\"
    Write-Host "   代码:      " -NoNewline -ForegroundColor Yellow
    Write-Host "$KClawHome\kclaw\"
    Write-Host ""
    
    Write-Host "─────────────────────────────────────────────────────────" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "🚀 常用命令:" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "   kclaw              " -NoNewline -ForegroundColor Green
    Write-Host "开始对话"
    Write-Host "   kclaw setup        " -NoNewline -ForegroundColor Green
    Write-Host "配置 API 密钥和设置"
    Write-Host "   kclaw config       " -NoNewline -ForegroundColor Green
    Write-Host "查看/编辑配置"
    Write-Host "   kclaw config edit  " -NoNewline -ForegroundColor Green
    Write-Host "在编辑器中打开配置"
    Write-Host "   kclaw gateway      " -NoNewline -ForegroundColor Green
    Write-Host "启动消息网关 (Telegram、Discord 等)"
    Write-Host "   kclaw update       " -NoNewline -ForegroundColor Green
    Write-Host "更新到最新版本"
    Write-Host ""
    
    Write-Host "─────────────────────────────────────────────────────────" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "⚡ 请重启终端以使 PATH 更改生效" -ForegroundColor Yellow
    Write-Host ""
    
    if (-not $HasNode) {
        Write-Host "注意: 无法自动安装 Node.js。" -ForegroundColor Yellow
        Write-Host "浏览器工具需要 Node.js。手动安装:" -ForegroundColor Yellow
        Write-Host "  https://nodejs.org/en/download/" -ForegroundColor Yellow
        Write-Host ""
    }
    
    if (-not $HasRipgrep) {
        Write-Host "注意: ripgrep (rg) 未安装。如需更快的文件搜索:" -ForegroundColor Yellow
        Write-Host "  winget install BurntSushi.ripgrep.MSVC" -ForegroundColor Yellow
        Write-Host ""
    }
}

# ============================================================================
# Main
# ============================================================================

function Main {
    Write-Banner
    
    if (-not (Install-Uv)) { throw "uv installation failed — cannot continue" }
    if (-not (Test-Python)) { throw "Python $PythonVersion not available — cannot continue" }
    if (-not (Test-Git)) { throw "Git not found — install from https://git-scm.com/download/win" }
    Test-Node              # Auto-installs if missing
    Install-SystemPackages  # ripgrep + ffmpeg in one step
    
    Install-Repository
    Install-Venv
    Install-Dependencies
    Install-NodeDeps
    Set-PathVariable
    Copy-ConfigTemplates
    Invoke-SetupWizard
    Start-GatewayIfConfigured
    
    Write-Completion
}

# 使用 try/catch 包装，以防止错误导致终端崩溃，当通过以下方式运行时：
#   irm https://...install.ps1 | iex
# （在 iex 中使用 exit/throw 会终止整个 PowerShell 会话）
try {
    Main
} catch {
    Write-Host ""
    Write-Err "安装失败: $_"
    Write-Host ""
    Write-Info "如果错误信息不明确，请尝试直接下载并运行脚本:"
    Write-Host "  Invoke-WebRequest -Uri 'https://raw.githubusercontent.com/kkutysllb/kk_KClaw/main/scripts/install.ps1' -OutFile install.ps1" -ForegroundColor Yellow
    Write-Host "  .\install.ps1" -ForegroundColor Yellow
    Write-Host ""
}
