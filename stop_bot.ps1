# Menghentikan bot dengan bersih:
# - Bikin stop.flag supaya run_bot.ps1 (kalau lagi jeda retry) tidak restart lagi
# - Force-kill proses python yang menjalankan telegram_agent.py
#
# Catatan: python.exe alias WindowsApps kadang me-relaunch proses baru
# (python3.13.exe) yang tidak ikut mati kalau cuma parent-nya yang di-kill,
# jadi di sini kita cari & matikan berdasarkan command line, bukan cuma PID induk.

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$StopFlag  = Join-Path $ScriptDir "stop.flag"

Write-Host "Menghentikan bot..."
New-Item -ItemType File -Path $StopFlag -Force | Out-Null

$proses = Get-CimInstance Win32_Process -Filter "Name = 'python3.13.exe' or Name = 'python.exe'" |
    Where-Object { $_.CommandLine -like "*telegram_agent.py*" }

if (-not $proses) {
    Write-Host "Tidak ada proses bot yang sedang jalan."
} else {
    foreach ($p in $proses) {
        Write-Host "Menghentikan PID $($p.ProcessId)..."
        taskkill /PID $p.ProcessId /F /T | Out-Null
    }
    Write-Host "Bot dihentikan."
}
