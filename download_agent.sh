#!/bin/bash

# Pindah ke folder Downloads otomatis
cd ~/Downloads

echo "🤖 Agent: Halo Haneen! Siap membantu download."
echo "🔗 Masukkan link video YouTube (lalu tekan Enter):"
read link

echo "--------------------------------"
echo "Format apa yang kamu mau?"
echo "1) Video MP4 (Kualitas Terbaik)"
echo "2) Audio MP3 (Hanya Suara)"
echo "--------------------------------"
read opsi

if [ "$opsi" == "1" ]; then
    echo "📥 Mengunduh Video MP4..."
    yt-dlp --merge-output-format mp4 "$link"
elif [ "$opsi" == "2" ]; then
    echo "📥 Mengunduh Audio MP3..."
    yt-dlp -x --audio-format mp3 "$link"
else
    echo "❌ Pilihan salah, proses dibatalkan."
fi

echo "✅ Selesai! File ada di folder Downloads kamu."