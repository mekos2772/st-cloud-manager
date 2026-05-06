<#
.SYNOPSIS
  ST Cloud Manager — Windows 一键安装脚本
  PowerShell 5.1+ (管理员运行)
#>
param(
    [string]$InstallDir = "$env:USERPROFILE\st-cloud-manager",
    [switch]$SkipDocker,
    [switch]$SkipTraefik
)

$ErrorActionPreference = "Stop"
$Host.UI.RawUI.WindowTitle = "ST Cloud Manager Installer"

function Write-Step($msg) { Write-Host "[>>>] $msg" -ForegroundColor Cyan }
function Write-OK($msg)   { Write-Host "[ OK] $msg" -ForegroundColor Green }
function Write-Warn($msg) { Write-Host "[WARN] $msg" -ForegroundColor Yellow }
function Write-Err($msg)  { Write-Host "[ERR] $msg" -ForegroundColor Red }

Write-Host "========================================" -ForegroundColor Blue
Write-Host "  ST Cloud Manager — Windows Installer" -ForegroundColor Blue
Write-Host "========================================" -ForegroundColor Blue
Write-Host ""

# ── 1. 检查管理员权限 ──
Write-Step "检查管理员权限"
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Warn "建议以管理员身份运行。部分功能（Docker 安装）需要管理员权限。"
    Write-Warn "右键 PowerShell → 以管理员身份运行"
}

# ── 2. 环境检测 ──
Write-Step "环境检测"

$os = Get-CimInstance Win32_OperatingSystem
Write-OK "操作系统: $($os.Caption) ($($os.Version))"

# Python
$python = $null
foreach ($cmd in @("python3", "python")) {
    try { $v = & $cmd --version 2>&1; $python = $cmd; Write-OK "Python: $v ($(Get-Command $cmd).Source)"; break }
    catch { }
}
if (-not $python) {
    Write-Err "Python 未安装。请从 https://www.python.org/downloads/ 下载安装 Python 3.10+"
    Write-Warn "安装时请勾选 'Add Python to PATH'"
    exit 1
}

# Git
try { $v = & git --version 2>&1; Write-OK "Git: $v" }
catch { Write-Warn "Git 未安装。请从 https://git-scm.com/download/win 下载" }

# ── 3. 安装 Docker Desktop ──
if (-not $SkipDocker) {
    Write-Step "检查 Docker"
    try {
        $dv = & docker version --format '{{.Client.Version}}' 2>&1
        Write-OK "Docker: v$dv"
    }
    catch {
        Write-Warn "Docker Desktop 未安装或未运行"
        Write-Warn "请从 https://www.docker.com/products/docker-desktop/ 下载安装 Docker Desktop"
        Write-Warn "安装后启动 Docker Desktop 并等待引擎就绪"
        Write-Warn "或使用 --SkipDocker 跳过（Docker 需手动安装）"
        if (-not $SkipDocker) {
            $answer = Read-Host "是否继续安装其他组件？(y/n)"
            if ($answer -ne "y") { exit 0 }
        }
    }
}

# ── 4. 下载 Traefik ──
if (-not $SkipTraefik) {
    Write-Step "下载 Traefik"
    $traefikExe = "$InstallDir\traefik.exe"
    if (Test-Path $traefikExe) {
        Write-OK "Traefik 已存在: $traefikExe"
    }
    else {
        $tVer = "v3.2.5"
        $tUrl = "https://github.com/traefik/traefik/releases/download/$tVer/traefik_$($tVer)_windows_amd64.zip"
        Write-Warn "下载 Traefik $tVer ..."
        $zip = "$env:TEMP\traefik.zip"
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        Invoke-WebRequest -Uri $tUrl -OutFile $zip -UseBasicParsing
        New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
        Expand-Archive -Path $zip -DestinationPath $InstallDir -Force
        Remove-Item $zip
        Write-OK "Traefik 安装完成"
    }
}

# ── 5. 安装 Python 依赖 ──
Write-Step "安装 Python 依赖"
& $python -m pip install --upgrade pip -q 2>&1 | Out-Null
& $python -m pip install -r "$PSScriptRoot\..\manager\requirements.txt" -q
Write-OK "Python 依赖安装完成"

# ── 6. 初始化项目 ──
Write-Step "初始化项目"
Set-Location "$PSScriptRoot\.."

if (-not (Test-Path ".env") -and (Test-Path ".env.example")) {
    Copy-Item ".env.example" ".env"
    # Generate random Admin Key
    $randomKey = "sk-admin-" + (-join ((48..57)+(65..90)+(97..122) | Get-Random -Count 32 | %{[char]$_}))
    (Get-Content ".env") -replace "ST_ADMIN_API_KEY=.*", "ST_ADMIN_API_KEY=$randomKey" | Set-Content ".env"
    Write-Warn "已创建 .env，Admin Key: $randomKey"
    Write-Warn "请编辑 .env 填入 API 配置"
}

# Create network
try { docker network create st_proxy 2>&1 | Out-Null; Write-OK "Docker 网络 st_proxy 已创建" }
catch { Write-OK "Docker 网络 st_proxy 已存在" }

# Init DB
& $python scripts/init_db.py
Write-OK "数据库已初始化"

# Create dirs
New-Item -ItemType Directory -Force -Path users,archive,backups | Out-Null
Write-OK "数据目录已就绪"

Write-OK "项目初始化完成: $(Get-Location)"

# ── 7. 启动服务 ──
Write-Step "启动服务"

# Start Traefik
$traefikExe = "$(Get-Location)\traefik.exe"
$traefikYml = "$(Get-Location)\traefik.yml"
if (Test-Path $traefikExe) {
    Start-Process -FilePath $traefikExe -ArgumentList "--configFile=`"$traefikYml`"" -WindowStyle Hidden
    Write-OK "Traefik 已启动 (port 80)"
}

# Start Manager
Start-Process -FilePath $python -ArgumentList "-m uvicorn manager.app:app --host 0.0.0.0 --port 5000" -WindowStyle Hidden
Start-Sleep -Seconds 2
Write-OK "Manager 已启动 (port 5000)"

# ── done ──
Write-Host ""
Write-Host "========================================" -ForegroundColor Yellow
Write-Host "  安装完成！请按以下步骤配置：" -ForegroundColor Yellow
Write-Host "========================================" -ForegroundColor Yellow
Write-Host "  1. 编辑 .env 填入 API 配置" -ForegroundColor Yellow
Write-Host "  2. 打开后台设置 API 配置中心" -ForegroundColor Yellow
Write-Host "     http://localhost:5000/admin" -ForegroundColor Green
Write-Host "  3. 手动配一次酒馆，导出 API 模板" -ForegroundColor Yellow
Write-Host "     python scripts/export_api_template.py" -ForegroundColor Green
Write-Host "  4. 生成激活 Key" -ForegroundColor Yellow
Write-Host "     python scripts/create_key.py" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Yellow
Write-Host ""

# Open browser
Start-Process "http://localhost:5000/admin"
