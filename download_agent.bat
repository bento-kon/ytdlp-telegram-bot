@echo off
setlocal

where yt-dlp >nul 2>nul
if errorlevel 1 (
    echo yt-dlp belum terpasang.
    echo Install dengan: winget install yt-dlp.yt-dlp
    echo atau: pip install -U yt-dlp
    pause
    exit /b 1
)

where ffmpeg >nul 2>nul
if errorlevel 1 (
    echo [PERINGATAN] ffmpeg belum terpasang, proses gabung video/audio bisa gagal.
    echo Install dengan: winget install Gyan.FFmpeg
    echo.
)

cd /d "%USERPROFILE%\Downloads"

echo Agent: Halo! Siap membantu download.
set /p link="Masukkan link video YouTube lalu tekan Enter: "

echo --------------------------------
echo Format apa yang kamu mau?
echo 1) Video MP4 (Kualitas Terbaik)
echo 2) Audio MP3 (Hanya Suara)
echo --------------------------------
set /p opsi="Pilihan (1/2): "

if "%opsi%"=="1" (
    echo Mengunduh Video MP4...
    yt-dlp --merge-output-format mp4 "%link%"
) else if "%opsi%"=="2" (
    echo Mengunduh Audio MP3...
    yt-dlp -x --audio-format mp3 "%link%"
) else (
    echo Pilihan salah, proses dibatalkan.
    pause
    exit /b 1
)

echo Selesai! File ada di folder Downloads kamu.
pause
