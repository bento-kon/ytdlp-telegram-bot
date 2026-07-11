"""Smoke test manual: pastikan refactor (edit_aman, siapkan_job, delete download_agent.*)
tidak mengubah perilaku download YouTube, progress bar, dan transcript.
Tidak ada network beneran: bot & yt_dlp di-mock. Jalankan: python test_telegram_agent.py
"""
import os
import shutil
import tempfile
from unittest.mock import MagicMock, patch

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "123456:test-token")

import telebot
telebot.TeleBot.infinity_polling = lambda self: None  # jangan sampai import module nge-poll beneran

import telegram_agent as ta

ta.bot = MagicMock()
ta.HANEEN_ID = 123

tmp_downloads = tempfile.mkdtemp()
ta.BASE_DOWNLOAD_DIR = tmp_downloads

try:
    # --- edit_aman: sukses & swallow error ---
    ta.edit_aman(123, 1, "halo")
    ta.bot.edit_message_text.assert_called_with("halo", 123, 1)

    ta.bot.edit_message_text.side_effect = Exception("message is not modified")
    ta.edit_aman(123, 1, "halo lagi")  # tidak boleh raise
    ta.bot.edit_message_text.side_effect = None

    # --- siapkan_job: folder harian dibuat + judul dipotong ---
    with patch.object(ta, "ambil_judul", return_value="J" * 100):
        target_folder, judul, judul_singkat = ta.siapkan_job("https://youtu.be/xyz")
    assert os.path.isdir(target_folder)
    assert judul_singkat == "J" * 50 + "..."

    # --- progress bar / progress hook: dipakai proses_job & proses_transcript ---
    assert ta.buat_progress_bar(0) == "░" * 12
    assert ta.buat_progress_bar(50) == "█" * 6 + "░" * 6
    assert ta.buat_progress_bar(100) == "█" * 12

    ta.bot.reset_mock()
    hook = ta.buat_progress_hook(123, 1, "Judul Video")
    hook({"status": "downloading", "total_bytes": 1000, "downloaded_bytes": 500, "speed": 1024 * 1024})
    teks_terkirim = ta.bot.edit_message_text.call_args[0][0]
    assert "50%" in teks_terkirim and "█" in teks_terkirim

    hook({"status": "finished"})
    assert "Download selesai" in ta.bot.edit_message_text.call_args[0][0]

    # --- proses_job end-to-end (yt_dlp & filesystem di-mock) ---
    with patch.object(ta, "ambil_judul", return_value="Video Test"), \
         patch.object(ta, "jalankan_download") as mock_download, \
         patch.object(ta.glob, "glob", return_value=[]):
        job = {"chat_id": 123, "url": "https://youtu.be/xyz", "is_mp3": False,
               "kualitas": "720", "message_id": 1}
        ta.proses_job(job)
        assert mock_download.called
        pesan_akhir = ta.bot.edit_message_text.call_args_list[-1][0][0]
        assert "Selesai" in pesan_akhir and "youtube-tl" in pesan_akhir

    # --- proses_transcript: caption ditemukan langsung (tanpa fallback Whisper) ---
    with patch.object(ta, "ambil_judul", return_value="Video Test"), \
         patch.object(ta, "cari_caption", return_value="ini transkrip hasil caption"), \
         patch.object(ta, "kirim_transkrip") as mock_kirim:
        job = {"chat_id": 123, "url": "https://youtu.be/xyz", "message_id": 1, "jenis": "transcript"}
        ta.proses_transcript(job)
        mock_kirim.assert_called_once()
        assert mock_kirim.call_args[0][1] == "ini transkrip hasil caption"

    # --- bersihkan_subtitle: VTT jadi teks polos ---
    vtt = "WEBVTT\n\n1\n00:00:00.000 --> 00:00:02.000\nHalo <b>dunia</b>\nHalo <b>dunia</b>\n"
    assert ta.bersihkan_subtitle(vtt) == "Halo dunia"

    print("OK - semua smoke test lolos (progress bar, download job, transcript, edit_aman, siapkan_job).")
finally:
    shutil.rmtree(tmp_downloads, ignore_errors=True)
