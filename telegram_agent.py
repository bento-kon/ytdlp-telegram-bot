import telebot
from telebot import types
import os
import glob
import shutil
import sys
import time
import re
import threading
import queue
import uuid
from datetime import datetime
from dotenv import load_dotenv
import yt_dlp
import requests
from faster_whisper import WhisperModel

# Paksa stdout pakai UTF-8 supaya emoji tidak error di console Windows (cp1252/cp437)
try:
    sys.stdout.reconfigure(encoding="utf-8")
except AttributeError:
    pass

# Default timeout telebot cuma 15 detik, kurang buat kirim video/audio berukuran besar
# atau koneksi yang lagi lambat -> naikkan supaya tidak gampang "Read timed out".
telebot.apihelper.READ_TIMEOUT = 60
telebot.apihelper.CONNECT_TIMEOUT = 60

load_dotenv()

# Token diambil dari file .env (jangan hardcode di sini)
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
if not TOKEN:
    raise RuntimeError(
        "TELEGRAM_BOT_TOKEN tidak ditemukan. Salin .env.example menjadi .env "
        "lalu isi TELEGRAM_BOT_TOKEN dengan token dari @BotFather."
    )

# 🔴 MASUKKAN ID TELEGRAM KAMU DI SINI (Hasil dari @userinfobot)
HANEEN_ID = 7329283917  # Ganti dengan angka ID kamu (tanpa tanda kutip)


def cek_dependensi():
    if not shutil.which("ffmpeg"):
        print("⚠️  ffmpeg belum terpasang.")
        print("   Install ffmpeg: winget install Gyan.FFmpeg   (atau: choco install ffmpeg)")
        print("   Setelah install, tutup dan buka ulang terminal, lalu jalankan lagi bot ini.")


cek_dependensi()

bot = telebot.TeleBot(TOKEN)
BASE_DOWNLOAD_DIR = os.path.join(os.path.expanduser("~"), "Downloads", "youtube-tl")

# Domain link yang didukung (YouTube, Instagram, TikTok)
DOMAIN_DIDUKUNG = ("youtube.com", "youtu.be", "instagram.com", "tiktok.com")
DOMAIN_YOUTUBE = ("youtube.com", "youtu.be")


def link_didukung(url):
    return any(domain in url for domain in DOMAIN_DIDUKUNG)


def link_youtube(url):
    return any(domain in url for domain in DOMAIN_YOUTUBE)


# --- Pilihan kualitas video lewat inline keyboard ---
# Catatan: keyboard kualitas cuma dipakai untuk YouTube. Metadata height dari
# Instagram/TikTok sering tidak akurat (video 720p bisa dilabeli height=640 dsb),
# jadi filter height<=X malah bisa memilih stream yang salah/lebih rendah dari
# yang seharusnya. Untuk Instagram/TikTok, download langsung pakai "best".
FORMAT_PER_KUALITAS = {
    "360": "bestvideo[height<=360]+bestaudio/best[height<=360]",
    "720": "bestvideo[height<=720]+bestaudio/best[height<=720]",
    "1080": "bestvideo[height<=1080]+bestaudio/best[height<=1080]",
    "best": "bestvideo+bestaudio/best",
}
LABEL_KUALITAS = {"360": "360p", "720": "720p", "1080": "1080p", "best": "Best"}

# Link yang sedang menunggu user pilih kualitas (request_id -> url)
pending_links = {}
pending_lock = threading.Lock()

# --- Queue system: semua download diproses berurutan oleh 1 worker thread ---
download_queue = queue.Queue()
is_processing = False
processing_lock = threading.Lock()


def posisi_antrian():
    with processing_lock:
        extra = 1 if is_processing else 0
    return download_queue.qsize() + extra


def buat_progress_bar(percent, panjang=12):
    percent = max(0, min(100, percent))
    terisi = int(panjang * percent / 100)
    return "█" * terisi + "░" * (panjang - terisi)


def format_ukuran(byte_count):
    if not byte_count:
        return "?"
    return f"{byte_count / (1024 * 1024):.1f}MB"


def format_kecepatan(bytes_per_s):
    if not bytes_per_s:
        return "?"
    return f"{bytes_per_s / (1024 * 1024):.1f}MB/s"


def buat_progress_hook(chat_id, message_id, judul_singkat):
    # Throttle state per-download: jangan edit pesan Telegram tiap tick,
    # cukup tiap >=3 detik ATAU kalau persennya naik >=5%, biar tidak kena rate limit.
    state = {"last_edit": 0.0, "last_percent": -100}

    def hook(d):
        try:
            status = d.get("status")
            if status == "downloading":
                total = d.get("total_bytes") or d.get("total_bytes_estimate")
                downloaded = d.get("downloaded_bytes", 0)
                percent = (downloaded / total * 100) if total else None
                now = time.monotonic()

                cukup_waktu = (now - state["last_edit"]) >= 3
                cukup_persen = percent is not None and (percent - state["last_percent"]) >= 5
                if not (cukup_waktu or cukup_persen):
                    return

                info = d.get("info_dict") or {}
                idx, n = info.get("playlist_index"), info.get("n_entries")
                prefix = f"[{idx}/{n}] " if idx and n else ""

                if percent is not None:
                    bar = buat_progress_bar(percent)
                    teks = (
                        f"📥 {prefix}{judul_singkat}\n"
                        f"{bar} {percent:.0f}%\n"
                        f"{format_ukuran(downloaded)}/{format_ukuran(total)} • {format_kecepatan(d.get('speed'))}"
                    )
                    state["last_percent"] = percent
                else:
                    teks = f"📥 {prefix}{judul_singkat}\nMengunduh... {format_ukuran(downloaded)}"

                state["last_edit"] = now
                try:
                    bot.edit_message_text(teks, chat_id, message_id)
                except Exception:
                    pass  # abaikan error spt "message is not modified", jangan sampai proses download ikut gagal

            elif status == "finished":
                try:
                    bot.edit_message_text(
                        f"⚙️ {judul_singkat}\nDownload selesai, memproses file...",
                        chat_id, message_id
                    )
                except Exception:
                    pass
        except Exception:
            pass  # progress hook tidak boleh sampai menghentikan proses download

    return hook


def ambil_judul(url):
    try:
        with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "skip_download": True}) as ydl:
            info = ydl.extract_info(url, download=False)
            return info.get("title", url)
    except Exception:
        return url


# --- Fitur transcript: cari caption dulu (gratis/instan), fallback Whisper lokal ---

BAHASA_PRIORITAS = ("id", "en", "en-US", "en-GB", "en-orig")
FORMAT_SUBTITLE_PRIORITAS = ("vtt", "srv1", "srv3", "ttml", "json3")

_whisper_model = None
_whisper_lock = threading.Lock()


def ambil_model_whisper():
    # Lazy-load, model "base" cuma dimuat sekali lalu dipakai ulang (thread-safe).
    global _whisper_model
    with _whisper_lock:
        if _whisper_model is None:
            _whisper_model = WhisperModel("base", device="cpu", compute_type="int8")
        return _whisper_model


def pilih_track_subtitle(sub_dict):
    if not sub_dict:
        return None
    for lang in BAHASA_PRIORITAS:
        if lang in sub_dict:
            return lang, sub_dict[lang]
    lang = next(iter(sub_dict), None)
    return (lang, sub_dict[lang]) if lang else None


def pilih_url_subtitle(entries):
    for fmt in FORMAT_SUBTITLE_PRIORITAS:
        for entry in entries:
            if entry.get("ext") == fmt:
                return entry["url"], fmt
    if entries:
        return entries[0].get("url"), entries[0].get("ext")
    return None, None


def bersihkan_subtitle(teks):
    # Ubah VTT/SRT/TTML jadi teks polos: buang nomor cue, timestamp, tag markup,
    # dan baris duplikat berturut-turut (umum di auto-caption yang rolling).
    baris_bersih = []
    terakhir = None
    for baris in teks.splitlines():
        baris = baris.strip()
        if not baris or baris.upper().startswith("WEBVTT"):
            continue
        if "-->" in baris:
            continue
        if re.fullmatch(r"\d+", baris):
            continue
        if re.fullmatch(r"[A-Za-z\-]+:.*", baris) and len(baris) < 30:
            continue
        baris = re.sub(r"<[^>]+>", "", baris).strip()
        if baris and baris != terakhir:
            baris_bersih.append(baris)
            terakhir = baris
    return " ".join(baris_bersih)


def cari_caption(url):
    # Coba subtitle manual dulu, baru auto-generated caption. Return None kalau tidak ada sama sekali.
    try:
        with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "skip_download": True}) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception:
        return None

    for sumber in (info.get("subtitles"), info.get("automatic_captions")):
        pilihan = pilih_track_subtitle(sumber)
        if not pilihan:
            continue
        _, entries = pilihan
        sub_url, _ = pilih_url_subtitle(entries)
        if not sub_url:
            continue
        try:
            resp = requests.get(sub_url, timeout=30)
            resp.raise_for_status()
            teks = bersihkan_subtitle(resp.text)
            if teks.strip():
                return teks
        except Exception:
            continue
    return None


def download_audio_untuk_transkrip(url, target_folder, hook):
    outtmpl = os.path.join(target_folder, "%(title)s [audio-transkrip].%(ext)s")
    ydl_opts = {
        "format": "bestaudio/best",
        "outtmpl": outtmpl,
        "progress_hooks": [hook],
        "quiet": True,
        "no_warnings": True,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        return ydl.prepare_filename(info)


def transkrip_dengan_whisper(audio_path):
    model = ambil_model_whisper()
    segments, _ = model.transcribe(audio_path)
    return " ".join(segment.text.strip() for segment in segments).strip()


def nama_file_aman(judul, ekstensi):
    terlarang = '\\/:*?"<>|'
    nama = "".join(c for c in judul[:80].strip() if c not in terlarang).strip()
    return f"{nama or 'transkrip'}.{ekstensi}"


def kirim_transkrip(chat_id, teks, path_txt):
    batas = 4096
    potongan = [teks[i:i + batas] for i in range(0, len(teks), batas)] or [""]
    for i, bagian in enumerate(potongan):
        prefix = f"📝 Transkrip ({i + 1}/{len(potongan)}):\n\n" if len(potongan) > 1 else "📝 Transkrip:\n\n"
        try:
            bot.send_message(chat_id, prefix + bagian)
        except Exception:
            pass

    if len(teks) > batas and path_txt and os.path.exists(path_txt):
        try:
            with open(path_txt, "rb") as f:
                bot.send_document(chat_id, f)
        except Exception:
            pass


def proses_transcript(job):
    chat_id = job["chat_id"]
    url = job["url"]
    message_id = job["message_id"]

    tanggal_hari_ini = datetime.now().strftime("%d-%m-%Y")
    target_folder = os.path.join(BASE_DOWNLOAD_DIR, tanggal_hari_ini)
    os.makedirs(target_folder, exist_ok=True)

    judul = ambil_judul(url)
    judul_singkat = (judul[:50] + "...") if len(judul) > 50 else judul

    try:
        bot.edit_message_text(f"🔍 Mencari caption...\n📌 {judul_singkat}", chat_id, message_id)
    except Exception:
        pass

    transkrip = None
    try:
        transkrip = cari_caption(url)
    except Exception:
        transkrip = None

    audio_path = None
    if not transkrip:
        try:
            bot.edit_message_text(
                f"🎙️ Tidak ada caption, transkrip pakai AI lokal, mohon tunggu...\n📌 {judul_singkat}",
                chat_id, message_id
            )
        except Exception:
            pass

        try:
            hook = buat_progress_hook(chat_id, message_id, f"[audio] {judul_singkat}")
            audio_path = download_audio_untuk_transkrip(url, target_folder, hook)

            try:
                bot.edit_message_text(
                    f"🧠 Transkrip audio pakai Whisper (lokal), bisa beberapa menit...\n📌 {judul_singkat}",
                    chat_id, message_id
                )
            except Exception:
                pass

            transkrip = transkrip_dengan_whisper(audio_path)
        except Exception as e:
            pesan_error = f"❌ Waduh, gagal transkrip: {e}"
            try:
                bot.edit_message_text(pesan_error, chat_id, message_id)
            except Exception:
                try:
                    bot.send_message(chat_id, pesan_error)
                except Exception:
                    pass
            return
        finally:
            if audio_path and os.path.exists(audio_path):
                try:
                    os.remove(audio_path)
                except Exception:
                    pass

    if not transkrip or not transkrip.strip():
        try:
            bot.edit_message_text(
                f"⚠️ Tidak berhasil mendapatkan transkrip untuk video ini.\n📌 {judul_singkat}",
                chat_id, message_id
            )
        except Exception:
            pass
        return

    path_txt = os.path.join(target_folder, nama_file_aman(judul, "txt"))
    try:
        with open(path_txt, "w", encoding="utf-8") as f:
            f.write(transkrip)
    except Exception:
        path_txt = None

    try:
        bot.edit_message_text(f"✅ Transkrip selesai!\n📌 {judul_singkat}", chat_id, message_id)
    except Exception:
        pass

    kirim_transkrip(chat_id, transkrip, path_txt)


def jalankan_download(url, is_mp3, kualitas, target_folder, hook):
    if is_mp3:
        ydl_opts = {
            "format": "bestaudio/best",
            "outtmpl": os.path.join(target_folder, "%(title)s.%(ext)s"),
            "postprocessors": [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3"}],
            "progress_hooks": [hook],
            "quiet": True,
            "no_warnings": True,
        }
    else:
        ydl_opts = {
            "format": FORMAT_PER_KUALITAS.get(kualitas, FORMAT_PER_KUALITAS["best"]),
            "merge_output_format": "mp4",
            "outtmpl": os.path.join(target_folder, "%(title)s.%(ext)s"),
            "progress_hooks": [hook],
            "quiet": True,
            "no_warnings": True,
        }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])


def proses_job(job):
    chat_id = job["chat_id"]
    url = job["url"]
    is_mp3 = job["is_mp3"]
    kualitas = job.get("kualitas")
    message_id = job["message_id"]

    # Fitur Manajemen Folder: folder berdasarkan tanggal hari ini (Contoh: 14-06-2026)
    tanggal_hari_ini = datetime.now().strftime("%d-%m-%Y")
    target_folder = os.path.join(BASE_DOWNLOAD_DIR, tanggal_hari_ini)
    os.makedirs(target_folder, exist_ok=True)

    judul = ambil_judul(url)
    judul_singkat = (judul[:50] + "...") if len(judul) > 50 else judul
    label_format = "MP3 (Audio)" if is_mp3 else f"MP4 ({LABEL_KUALITAS.get(kualitas, 'Best')})"

    # Pesan status ini cuma kosmetik: kalau timeout/network hiccup ke Telegram di sini,
    # jangan sampai bikin seluruh job dianggap gagal padahal downloadnya sendiri belum tentu gagal.
    try:
        bot.edit_message_text(
            f"📥 Sedang Mengunduh ({label_format}):\n📌 {judul_singkat}",
            chat_id, message_id
        )
    except Exception:
        pass

    # Ini bagian yang benar-benar bisa gagal (yt-dlp/ffmpeg) -> baru dilaporkan sebagai error
    try:
        hook = buat_progress_hook(chat_id, message_id, judul_singkat)
        jalankan_download(url, is_mp3, kualitas, target_folder, hook)
    except Exception as e:
        pesan_error = f"❌ Waduh, gagal download: {e}"
        try:
            bot.edit_message_text(pesan_error, chat_id, message_id)
        except Exception:
            try:
                bot.send_message(chat_id, pesan_error)
            except Exception:
                pass
        return

    try:
        bot.edit_message_text(
            f"✅ Selesai! File sudah masuk ke komputer di folder youtube-tl/{tanggal_hari_ini}",
            chat_id, message_id
        )
    except Exception:
        pass

    # Cari file untuk dikirim balik ke HP jika ukuran memungkinkan
    try:
        files = glob.glob(os.path.join(target_folder, "*.*"))
        if files:
            latest_file = max(files, key=os.path.getctime)
            file_size = os.path.getsize(latest_file) / (1024 * 1024)

            if file_size < 50:
                try:
                    bot.send_message(chat_id, "📱 Mengirimkan file ke HP kamu...")
                    with open(latest_file, "rb") as f:
                        if is_mp3:
                            bot.send_audio(chat_id, f)
                        else:
                            bot.send_video(chat_id, f)
                except Exception as e:
                    try:
                        bot.send_message(
                            chat_id,
                            f"⚠️ File sudah selesai didownload tapi gagal dikirim ke HP ({e}). "
                            f"File tetap ada di folder youtube-tl/{tanggal_hari_ini}."
                        )
                    except Exception:
                        pass
            else:
                bot.send_message(
                    chat_id,
                    f"⚠️ Info HP: File berukuran {file_size:.1f}MB (di atas limit 50MB Telegram). "
                    f"File tetap tersimpan aman di laptop kamu!"
                )
    except Exception:
        pass


def worker_loop():
    global is_processing
    while True:
        job = download_queue.get()
        with processing_lock:
            is_processing = True
        try:
            if job.get("jenis") == "transcript":
                proses_transcript(job)
            else:
                proses_job(job)
        finally:
            with processing_lock:
                is_processing = False
            download_queue.task_done()


threading.Thread(target=worker_loop, daemon=True).start()


def tambahkan_ke_antrian(job):
    posisi = posisi_antrian()
    if posisi > 0:
        try:
            bot.edit_message_text(
                f"⏳ Ditambahkan ke antrian (posisi ke-{posisi + 1})...",
                job["chat_id"], job["message_id"]
            )
        except Exception:
            pass
    download_queue.put(job)


@bot.message_handler(commands=['start', 'help'])
def send_welcome(message):
    # Fitur Keamanan: Cek apakah yang chat benar-benar Haneen
    if message.chat.id != HANEEN_ID:
        bot.reply_to(message, "❌ Akses Ditolak! Bot ini terkunci khusus untuk Haneen.")
        return

    pesan = (
        "🤖 **Selamat Datang di Mega-Bot Haneen!**\n\n"
        "🔒 *Status:* Aman & Terkunci untuk Haneen.\n"
        "📅 *Folder otomatis:* Berdasarkan tanggal hari ini.\n\n"
        "🌐 **Platform yang didukung:** YouTube, Instagram, TikTok\n\n"
        "🎥 **Cara Download Video (MP4):**\n"
        "Kirim link video/reel/Shorts/playlist.\n"
        "- YouTube: bisa pilih kualitas (360p/720p/1080p/Best)\n"
        "- Instagram/TikTok: otomatis kualitas terbaik\n\n"
        "🎵 **Cara Download Lagu (MP3):**\n"
        "Ketik `mp3 ` (spasi) lalu tempel link video.\n"
        "Contoh: `mp3 https://youtube.com/...`\n\n"
        "📝 **Cara Transkrip Video:**\n"
        "Ketik `transcript ` (spasi) lalu tempel link video.\n"
        "Contoh: `transcript https://youtube.com/...`\n"
        "Bot coba ambil caption dulu (instan); kalau video tidak punya caption "
        "(umum di TikTok/Instagram), otomatis transkrip pakai AI lokal (bisa agak lama)."
    )
    bot.reply_to(message, pesan, parse_mode="Markdown")


@bot.message_handler(func=lambda message: True)
def process_mega_download(message):
    # Fitur Keamanan
    if message.chat.id != HANEEN_ID:
        return

    teks = message.text.strip()
    is_transcript = teks.lower().startswith("transcript")
    is_mp3 = teks.lower().startswith("mp3")

    if is_transcript:
        url = teks[len("transcript"):].strip()
    elif is_mp3:
        url = teks[3:].strip()
    else:
        url = teks

    if is_transcript:
        # Transcript lebih longgar: terima link apapun yang mungkin didukung yt-dlp,
        # bukan cuma domain yang di-whitelist buat fitur download.
        if not (url.startswith("http://") or url.startswith("https://")):
            bot.reply_to(message, "❓ Kirim link video yang valid ya, Haneen!")
            return

        sent = bot.reply_to(message, "🔍 Mencari caption...")
        job = {
            "chat_id": message.chat.id,
            "message_id": sent.message_id,
            "url": url,
            "jenis": "transcript",
        }
        tambahkan_ke_antrian(job)
        return

    if not link_didukung(url):
        bot.reply_to(message, "❓ Kirim link YouTube, Instagram, atau TikTok yang valid ya, Haneen!")
        return

    if is_mp3:
        sent = bot.reply_to(message, "🔍 Menganalisis link... Mengunduh format MP3 (Audio).")
        job = {
            "chat_id": message.chat.id,
            "message_id": sent.message_id,
            "url": url,
            "is_mp3": True,
            "kualitas": None,
            "jenis": "download",
        }
        tambahkan_ke_antrian(job)
        return

    if not link_youtube(url):
        # Instagram/TikTok: skip keyboard kualitas, langsung ambil yang terbaik
        # (lihat catatan di FORMAT_PER_KUALITAS soal metadata height yang tidak akurat)
        sent = bot.reply_to(message, "🔍 Menganalisis link... Mengunduh kualitas terbaik yang tersedia.")
        job = {
            "chat_id": message.chat.id,
            "message_id": sent.message_id,
            "url": url,
            "is_mp3": False,
            "kualitas": "best",
            "jenis": "download",
        }
        tambahkan_ke_antrian(job)
        return

    # Video YouTube: tanya kualitas dulu lewat inline keyboard
    request_id = uuid.uuid4().hex[:8]
    with pending_lock:
        pending_links[request_id] = url

    markup = types.InlineKeyboardMarkup(row_width=4)
    markup.add(*[
        types.InlineKeyboardButton(LABEL_KUALITAS[k], callback_data=f"q|{request_id}|{k}")
        for k in ("360", "720", "1080", "best")
    ])
    bot.reply_to(message, "🎬 Pilih kualitas video:", reply_markup=markup)


@bot.callback_query_handler(func=lambda call: call.data.startswith("q|"))
def pilih_kualitas(call):
    if call.message.chat.id != HANEEN_ID:
        bot.answer_callback_query(call.id)
        return

    _, request_id, kualitas = call.data.split("|", 2)
    with pending_lock:
        url = pending_links.pop(request_id, None)

    bot.answer_callback_query(call.id, f"Kualitas {LABEL_KUALITAS.get(kualitas, kualitas)} dipilih")

    if not url:
        bot.edit_message_text(
            "⚠️ Link sudah kedaluwarsa, kirim ulang linknya ya.",
            call.message.chat.id, call.message.message_id
        )
        return

    bot.edit_message_text(
        f"🔍 Menganalisis link... Mengunduh format MP4 ({LABEL_KUALITAS.get(kualitas, 'Best')}).",
        call.message.chat.id, call.message.message_id
    )

    job = {
        "chat_id": call.message.chat.id,
        "message_id": call.message.message_id,
        "url": url,
        "is_mp3": False,
        "kualitas": kualitas,
        "jenis": "download",
    }
    tambahkan_ke_antrian(job)


print("🤖 Mega-Bot Haneen sudah aktif dan siap tempur...")
bot.infinity_polling()
