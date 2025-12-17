import os
import json
import yt_dlp
import re
# Attempt import
try:
    from aeneas.executetask import ExecuteTask
    from aeneas.task import Task
except ImportError:
    print("Aeneas not installed!")
    exit(1)

# 1. Setup Data
# Surah Al-Fatiha (Normalized Arabic for better matching possibility)
surah_text_ar = [
    "بِسْمِ ٱللَّهِ ٱلرَّحْمَـٰنِ ٱلرَّحِيمِ",
    "ٱلْحَمْدُ لِلَّهِ رَبِّ ٱلْعَـٰلَمِينَ",
    "ٱلرَّحْمَـٰنِ ٱلرَّحِيمِ",
    "مَـٰلِكِ يَوْمِ ٱلدِّينِ",
    "إِيَّاكَ نَعْبُدُ وَإِيَّاكَ نَسْتَعِينُ",
    "ٱهْدِنَا ٱلصِّرَٰطَ ٱلْمُسْتَقِيمَ",
    "صِرَٰطَ ٱلَّذِينَ أَنْعَمْتَ عَلَيْهِمْ غَيْرِ ٱلْمَغْضُوبِ عَلَيْهِمْ وَلَا ٱلضَّالِّينَ"
]

def normalize_arabic(text):
    text = re.sub(r'[\u064B-\u065F\u0670]', '', text)
    text = re.sub(r'[ٱإأآ]', 'ا', text)
    text = re.sub(r'ة', 'ه', text)
    text = re.sub(r'ى', 'ي', text)
    return text

# Write text to file (Aeneas needs a text file)
# We strip diacritics for Aeneas usually? 
# Aeneas can handle unicode but TTS usually expects plain text or close to it.
# Let's try raw text first.
text_file = "test_fatiha.txt"
with open(text_file, "w", encoding="utf-8") as f:
    for line in surah_text_ar:
        # line = normalize_arabic(line) # Try removing diacritics?
        f.write(line + "\n")

# 2. Download Audio
video_url = "https://www.youtube.com/watch?v=MEEaNnF5D9w" # Al Fatiha - Mishary
audio_file = "test_fatiha.mp3"

if not os.path.exists(audio_file):
    print("Downloading audio...")
    ydl_opts = {
        'format': 'bestaudio/best',
        'outtmpl': 'test_fatiha.%(ext)s',
        'postprocessors': [{'key': 'FFmpegExtractAudio','preferredcodec': 'mp3','preferredquality': '192'}],
        'quiet': True
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([video_url])

# 3. Run Aeneas
print("Running Aeneas Forced Alignment...")
# Configuration
# "task_language=ar" might fail if espeak doesn't support 'ar' well or isn't installed.
config_string = u"task_language=ar|is_text_type=plain|os_task_file_format=json"
task = Task(config_string=config_string)
task.audio_file_path_absolute = os.path.abspath(audio_file)
task.text_file_path_absolute = os.path.abspath(text_file)
task.sync_map_file_path_absolute = os.path.abspath("test_fatiha.json")

# Process
try:
    ExecuteTask(task).execute()
    task.output_sync_map_file()
    print("[SUCCESS] Alignment generated at test_fatiha.json")
    
    # Print results
    with open("test_fatiha.json", "r", encoding="utf-8") as f:
        data = json.load(f)
        for fragment in data["fragments"]:
            print(f"Time: {fragment['begin']} - {fragment['end']} | Text: {fragment['lines'][0][:30]}...")
            
except Exception as e:
    print(f"[ERROR] Aeneas failed: {e}")
    print("Check if 'espeak' is installed and in PATH.")
