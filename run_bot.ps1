# Menjalankan telegram_agent.py terus-menerus di background:
# - Auto-restart kalau crash (loop retry sederhana, jeda 10 detik)
# - Log ke file logs\bot-YYYY-MM-DD.log (bukan cuma console)
# - Cegah 2 instance jalan bersamaan (mutex + cek proses lain yang pakai telegram_agent.py)
#
# Cara pakai manual : klik dua kali run_bot.bat, atau jalankan:
#   powershell -ExecutionPolicy Bypass -File run_bot.ps1
# Cara stop          : jalankan stop_bot.bat (atau stop_bot.ps1)

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ScriptDir

$LogDir    = Join-Path $ScriptDir "logs"
New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
$StopFlag  = Join-Path $ScriptDir "stop.flag"
$MutexName = "Global\YtdlpTelegramBot_Haneen_SingleInstance"

function Get-LogFile {
    Join-Path $LogDir ("bot-{0}.log" -f (Get-Date -Format "yyyy-MM-dd"))
}

function Write-Log {
    param([string]$Pesan)
    $baris = "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') | $Pesan"
    Add-Content -Path (Get-LogFile) -Value $baris -Encoding utf8
    Write-Host $baris
}

# --- Cegah dobel instance (1): proses lain yang sudah menjalankan telegram_agent.py,
#     misalnya kalau sebelumnya dijalankan manual langsung lewat "python telegram_agent.py" ---
$prosesLain = Get-CimInstance Win32_Process -Filter "Name = 'python3.13.exe' or Name = 'python.exe'" |
    Where-Object { $_.CommandLine -like "*telegram_agent.py*" }

if ($prosesLain) {
    $pidList = ($prosesLain | Select-Object -ExpandProperty ProcessId) -join ", "
    Write-Log "Sudah ada proses bot lain jalan (PID: $pidList). Tidak menjalankan instance baru, keluar."
    exit 1
}

# --- Cegah dobel instance (2): mutex, jaga-jaga kalau run_bot.ps1 sendiri di-start 2x
#     hampir bersamaan (race condition yang tidak akan kena cek proses di atas) ---
$mutex = New-Object System.Threading.Mutex($false, $MutexName)
if (-not $mutex.WaitOne(0)) {
    Write-Log "run_bot.ps1 sudah jalan di sesi lain (mutex terpakai). Keluar."
    exit 1
}

if (Test-Path $StopFlag) {
    Remove-Item $StopFlag -Force
}

# Pastikan PATH terbaru (ffmpeg/yt-dlp) kebaca meskipun baru saja diinstall di sesi lain
$machinePath = [System.Environment]::GetEnvironmentVariable("Path", "Machine")
$userPath    = [System.Environment]::GetEnvironmentVariable("Path", "User")
$env:Path    = "$machinePath;$userPath"

Write-Log "=== run_bot.ps1 dimulai (PID $PID) ==="

try {
    $percobaan = 0
    while ($true) {
        if (Test-Path $StopFlag) {
            Write-Log "stop.flag ditemukan sebelum start, bot dihentikan dengan sengaja."
            break
        }

        $percobaan++
        Write-Log "Menjalankan bot (percobaan ke-$percobaan)..."

        # py -3.13 dipakai (bukan 'python' alias WindowsApps) supaya proses child
        # benar-benar bisa dikelola/dihentikan dengan bersih dari stop_bot.ps1.
        & py -3.13 -u telegram_agent.py 2>&1 | ForEach-Object {
            Add-Content -Path (Get-LogFile) -Value "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') | $_" -Encoding utf8
        }

        $exitCode = $LASTEXITCODE
        Write-Log "Bot berhenti (exit code: $exitCode)."

        if (Test-Path $StopFlag) {
            Write-Log "stop.flag ditemukan, tidak restart. Keluar dari loop."
            break
        }

        Write-Log "Restart otomatis dalam 10 detik... (Ctrl+C atau stop_bot.ps1 untuk membatalkan)"
        Start-Sleep -Seconds 10
    }
} finally {
    $mutex.ReleaseMutex()
    $mutex.Dispose()
    Write-Log "=== run_bot.ps1 berhenti ==="
}
