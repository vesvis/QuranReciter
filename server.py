import os
import asyncio
import json
import uvicorn
from groq import Groq
import yt_dlp
import requests
import static_ffmpeg
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from rapidfuzz import process, fuzz
from urllib.parse import quote
from dotenv import load_dotenv
import re
import subprocess
import math
import http.cookies


# Load environment variables
load_dotenv()

# Initialize static-ffmpeg to ensure binaries are in PATH
static_ffmpeg.add_paths()

# --- 1. CONFIGURATION & SETUP ---

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://quran-reciter.onrender.com",
        "https://quranreciter.onrender.com",
        "http://localhost:8000",
        "http://127.0.0.1:8000"
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

os.makedirs("cache", exist_ok=True)
app.mount("/cache", StaticFiles(directory="cache"), name="cache")

@app.get("/service-worker.js")
async def get_service_worker():
    return FileResponse("service-worker.js", media_type="application/javascript")

@app.get("/")
async def read_root():
    return FileResponse("index.html")

print("--- SERVER STARTUP ---")
print("Initializing Groq API client...")

# Initialize Groq client with API key from environment
api_key = os.getenv("GROQ_API_KEY")
if not api_key:
    print("[ERROR] GROQ_API_KEY not found in environment variables!")
    print("Please set GROQ_API_KEY in .env file or environment")
    groq_client = None
else:
    groq_client = Groq(api_key=api_key)
    print("[OK] Groq client initialized successfully")

# Setup YouTube Cookies - Check multiple sources
# Priority: 1. Render secret file, 2. Local cookies.txt, 3. Environment variable
COOKIE_FILE_PATH = None

# Check for Render secret file first
if os.path.exists("/etc/secrets/cookies.txt"):
    COOKIE_FILE_PATH = "/etc/secrets/cookies.txt"
    print("[INIT] Found cookies at /etc/secrets/cookies.txt (Render secret file)")
# Check for local cookies.txt
elif os.path.exists("cookies.txt"):
    COOKIE_FILE_PATH = "cookies.txt"
    print("[INIT] Found local cookies.txt")
# Fall back to environment variable
else:
    cookies_content = os.getenv("YOUTUBE_COOKIES")
    if cookies_content:
        print("[INIT] Found YOUTUBE_COOKIES env var, processing...")
        
        # Check if it's already in Netscape format
        if "# Netscape HTTP Cookie File" in cookies_content or "# HTTP Cookie File" in cookies_content:
            print("[INIT] Detected Netscape format.")
            final_cookies = cookies_content
        else:
            print("[INIT] Detected raw cookie string, converting to Netscape format...")
            try:
                cookie = http.cookies.SimpleCookie()
                cookie.load(cookies_content)
                
                lines = ["# Netscape HTTP Cookie File"]
                for key, morsel in cookie.items():
                    lines.append(f".youtube.com\tTRUE\t/\tTRUE\t2147483647\t{key}\t{morsel.value}")
                
                final_cookies = "\n".join(lines)
                print(f"[INIT] Converted {len(cookie)} cookies to Netscape format.")
            except Exception as e:
                print(f"[ERROR] Failed to convert cookies: {e}")
                final_cookies = cookies_content

        with open("cookies.txt", "w", encoding="utf-8") as f:
            f.write(final_cookies)
        COOKIE_FILE_PATH = "cookies.txt"
        print("[INIT] cookies.txt created from env var")

def get_ydl_opts(base_opts=None):
    """Returns yt-dlp options with cookie file if available."""
    opts = base_opts or {}
    if COOKIE_FILE_PATH and os.path.exists(COOKIE_FILE_PATH):
        opts['cookiefile'] = COOKIE_FILE_PATH
        print(f"[YT-DLP] Using cookie file: {COOKIE_FILE_PATH}")
    
    # Add a common browser User-Agent to help bypass bot detection
    opts['user_agent'] = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    
    return opts

# --- 2. HELPER FUNCTIONS ---

def normalize_arabic(text):
    """Normalizes Arabic text by removing diacritics and standardizing characters."""
    # Remove Tashkeel
    text = re.sub(r'[\u064B-\u065F\u0670]', '', text)
    # Normalize Alifs
    text = re.sub(r'[ٱإأآ]', 'ا', text)
    # Normalize Taa Marbuta
    text = re.sub(r'ة', 'ه', text)
    # Normalize Ya
    text = re.sub(r'ى', 'ي', text)
    return text

def search_api(text):
    """Searches the Al Quran Cloud Search API for the given Arabic text."""
    try:
        encoded_text = quote(text)
        url = f"http://api.alquran.cloud/v1/search/{encoded_text}/all/ar"
        
        response = requests.get(url, timeout=10)
        if response.status_code == 200:
            data = response.json()
            
            if data.get("data") and data["data"].get("matches") and len(data["data"]["matches"]) > 0:
                first_match = data["data"]["matches"][0]
                surah_number = first_match["surah"]["number"]
                surah_name = first_match["surah"]["englishName"]
                print(f"[OK] Identified: Surah {surah_number} ({surah_name})")
                return (surah_number, surah_name)
        
        return None
    except Exception as e:
        print(f"[ERROR] Request Error: {e}")
        return None

def identify_surah_via_api(segments, full_text):
    """Iterates through transcribed segments to find a match in the API."""
    print("Identifying Surah...")
    
    # Try the first few segments individually
    max_attempts = min(len(segments), 10)
    
    for i in range(max_attempts):
        segment_text = segments[i]["text"].strip()
        if len(segment_text) < 10:
            continue
        
        # Clean up Bismillah
        bismillah = "بسم الله الرحمن الرحيم"
        if segment_text.startswith(bismillah):
            segment_text = segment_text[len(bismillah):].strip()
            
        if len(segment_text) < 5:
            continue
        result = search_api(segment_text[2:])
        if result:
            return result
            
    # Fallback: Try a chunk of the full text
    return search_api(full_text[:100])

def fetch_surah_text(surah_number):
    """Downloads the specific Surah text from the API."""
    try:
        url = f"http://api.alquran.cloud/v1/surah/{surah_number}/quran-uthmani"
        print(f"[FETCH] Fetching text for Surah {surah_number}...")
        
        response = requests.get(url, timeout=30)
        if response.status_code == 200:
            data = response.json()
            
            surah_data = []
            for ayah in data["data"]["ayahs"]:
                surah_data.append({
                    "surah": surah_number,
                    "ayah": ayah["numberInSurah"],
                    "text": ayah["text"]
                })
            return surah_data
            
        print(f"[ERROR] Error fetching Surah text: Status {response.status_code}")
        return []
        
    except Exception as e:
        print(f"[ERROR] Error fetching Surah text: {e}")
        return []

def download_audio(youtube_url):
    """Downloads audio from YouTube using yt-dlp."""
    ydl_opts = get_ydl_opts({
        'format': 'bestaudio/best',
        'outtmpl': 'cache/%(id)s.%(ext)s',
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '192'
        }],
        'quiet': True,
        'no_warnings': True,
    })
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(youtube_url, download=True)
        return f"{info['id']}.mp3", info['title']

def get_video_id(youtube_url):
    """Gets YouTube video ID and title without downloading."""
    ydl_opts = get_ydl_opts({
        'quiet': True,
        'no_warnings': True,
    })
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(youtube_url, download=False)
        return info['id'], info['title']

def get_audio_duration(audio_filepath):
    """Get duration of audio file in seconds using ffmpeg."""
    try:
        result = subprocess.run(
            ['ffprobe', '-v', 'error', '-show_entries', 'format=duration', 
             '-of', 'default=noprint_wrappers=1:nokey=1', audio_filepath],
            capture_output=True,
            text=True,
            check=True
        )
        return float(result.stdout.strip())
    except Exception as e:
        print(f"[ERROR] Could not get audio duration: {e}")
        return 0

def split_audio_chunks(audio_filepath, chunk_duration_minutes=10):
    """
    Split audio file into chunks using ffmpeg to stay under Groq's 25MB limit.
    Returns list of chunk file paths.
    """
    print(f"[SPLIT] Splitting audio into chunks...")
    
    duration = get_audio_duration(audio_filepath)
    chunk_duration_sec = chunk_duration_minutes * 60
    num_chunks = math.ceil(duration / chunk_duration_sec)
    
    chunks = []
    
    for i in range(num_chunks):
        start_time = i * chunk_duration_sec
        chunk_path = audio_filepath.replace('.mp3', f'_chunk_{i}.mp3')
        
        # Use ffmpeg to extract chunk with lower bitrate
        subprocess.run(
            ['ffmpeg', '-i', audio_filepath, '-ss', str(start_time), 
             '-t', str(chunk_duration_sec), '-b:a', '64k', '-y', chunk_path],
            capture_output=True,
            check=True
        )
        
        chunks.append(chunk_path)
        print(f"   Created chunk {i+1}/{num_chunks}: {chunk_path}")
    
    return chunks

def transcribe_with_groq(audio_filepath):
    """Transcribe audio using Groq's Whisper API with chunking for large files."""
    if not groq_client:
        raise Exception("Groq client not initialized. Please set GROQ_API_KEY")
    
    # Check file size - Groq limit is 25MB
    file_size_mb = os.path.getsize(audio_filepath) / (1024 * 1024)
    print(f"[TRANSCRIBE] Audio file size: {file_size_mb:.2f}MB")
    
    all_segments = []
    chunk_files = []
    
    try:
        # If file is too large, split into chunks
        if file_size_mb > 20:  # Use 20MB threshold to be safe
            print(f"[TRANSCRIBE] File too large, splitting into chunks...")
            chunk_files = split_audio_chunks(audio_filepath, chunk_duration_minutes=10)
            
            # Transcribe each chunk
            for i, chunk_path in enumerate(chunk_files):
                print(f"[TRANSCRIBE] Processing chunk {i+1}/{len(chunk_files)}...")
                
                with open(chunk_path, "rb") as audio_file:
                    transcription = groq_client.audio.transcriptions.create(
                        file=audio_file,
                        model="whisper-large-v3",
                        language="ar",
                        response_format="verbose_json",
                        temperature=0.0
                    )
                
                # Calculate time offset for this chunk
                chunk_offset = i * 10 * 60  # 10 minutes per chunk in seconds
                
                # Extract segments with adjusted timestamps
                if hasattr(transcription, 'segments') and transcription.segments:
                    for seg in transcription.segments:
                        # Handle both dict and object formats
                        seg_text = seg.get('text', '') if isinstance(seg, dict) else seg.text
                        seg_start = seg.get('start', 0) if isinstance(seg, dict) else seg.start
                        seg_end = seg.get('end', 0) if isinstance(seg, dict) else seg.end
                        
                        all_segments.append({
                            "text": seg_text,
                            "start": seg_start + chunk_offset,
                            "end": seg_end + chunk_offset
                        })
        else:
            # File is small enough, transcribe directly
            print(f"[TRANSCRIBE] Sending to Groq API...")
            
            with open(audio_filepath, "rb") as audio_file:
                transcription = groq_client.audio.transcriptions.create(
                    file=audio_file,
                    model="whisper-large-v3",
                    language="ar",
                    response_format="verbose_json",
                    temperature=0.0
                )
            
            # Extract segments
            if hasattr(transcription, 'segments') and transcription.segments:
                for seg in transcription.segments:
                    # Handle both dict and object formats
                    seg_text = seg.get('text', '') if isinstance(seg, dict) else seg.text
                    seg_start = seg.get('start', 0) if isinstance(seg, dict) else seg.start
                    seg_end = seg.get('end', 0) if isinstance(seg, dict) else seg.end
                    
                    all_segments.append({
                        "text": seg_text,
                        "start": seg_start,
                        "end": seg_end
                    })
        
        # Clean up chunk files if created
        for chunk_file in chunk_files:
            try:
                os.remove(chunk_file)
            except:
                pass
        
        # Create a response object similar to Groq's response
        class TranscriptionResult:
            def __init__(self, segments):
                self.segments = segments
                self.text = " ".join([s["text"] for s in segments])
        
        return TranscriptionResult(all_segments)
        
    except Exception as e:
        # Clean up chunk files on error
        for chunk_file in chunk_files:
            try:
                os.remove(chunk_file)
            except:
                pass
        print(f"[ERROR] Groq transcription failed: {e}")
        raise

def repair_cache():
    """Scans cache files and attempts to identify Surahs and fetch titles for files missing metadata."""
    print("[REPAIR] Scanning cache for missing metadata...")
    cache_dir = "cache"
    if not os.path.exists(cache_dir):
        return

    files = [f for f in os.listdir(cache_dir) if f.endswith(".json")]
    
    for filename in files:
        filepath = os.path.join(cache_dir, filename)
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
            
            needs_save = False
            video_id = filename.replace(".json", "")
            
            # Check Surah Name
            if "surah_name" not in data or not data["surah_name"] or data["surah_name"] == "Unknown Surah":
                print(f"   Repairing Surah for {filename}...")
                full_text = data.get("text", "")
                segments = data.get("segments", [])
                
                if segments or full_text:
                    result = identify_surah_via_api(segments, full_text)
                    if result:
                        data["surah_number"] = result[0]
                        data["surah_name"] = result[1]
                        needs_save = True

            # Check Title
            if "title" not in data or not data["title"]:
                print(f"   Fetching title for {filename}...")
                try:
                    ydl_opts = get_ydl_opts({
                        'quiet': True,
                        'extractor_args': {
                            'youtube': {
                                'player_client': ['web']
                            }
                        }
                    })
                    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                        info = ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=False)
                        data["title"] = info['title']
                        needs_save = True
                        print(f"   [OK] Found title: {data['title']}")
                except Exception as e:
                    print(f"   [WARN] Could not fetch title for {video_id}: {e}")
            
            if needs_save:
                with open(filepath, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False)
                    
        except Exception as e:
            print(f"   [ERROR] Failed to repair {filename}: {e}")

# --- 3. ENDPOINTS ---

class VideoRequest(BaseModel):
    url: str

@app.get("/recitation/{video_id}")
async def get_recitation(video_id: str):
    """Get cached recitation data."""
    cache_file = f"cache/{video_id}.json"
    if not os.path.exists(cache_file):
        raise HTTPException(status_code=404, detail="Recitation not found in cache")
    
    with open(cache_file, "r", encoding="utf-8") as f:
        data = json.load(f)
    
    # Add the video_id and ensure audio_url is relative
    data["id"] = video_id
    audio_filename = f"{video_id}.mp3"
    data["audio_url"] = f"/cache/{audio_filename}"
    
    # Fetch the full Quran text if not already in cache
    if "surah_text" not in data or "timeline" not in data:
        surah_id = data.get("surah_id")
        if surah_id:
            # Fetch the full Surah text
            surah_db = fetch_surah_text(surah_id)
            if surah_db:
                data["surah_text"] = surah_db
                data["surah_number"] = surah_id
                
                # Create timeline from segments (if we have segments)
                segments = data.get("segments", [])
                matched_timeline = []
                if segments and surah_db:
                    # Build normalized texts for matching
                    surah_texts_normalized = [normalize_arabic(entry["text"]) for entry in surah_db]
                    
                    for segment in segments:
                        heard_text = segment.get("text", "") if isinstance(segment, dict) else segment
                        if len(heard_text.strip()) < 5:
                            continue
                        
                        norm_heard_text = normalize_arabic(heard_text)
                        match = process.extractOne(norm_heard_text, surah_texts_normalized, scorer=fuzz.partial_ratio, score_cutoff=70)
                        
                        if match:
                            _, score, index = match
                            db_entry = surah_db[index]
                            matched_timeline.append({
                                "surah": db_entry["surah"],
                                "ayah": db_entry["ayah"],
                                "text": db_entry["text"],
                                "start": segment.get("start", 0),
                                "end": segment.get("end", 0)
                            })
                    
                    matched_timeline.sort(key=lambda x: x["start"])
                data["timeline"] = matched_timeline
    
    return data

@app.post("/process")
async def process_video(request: VideoRequest):
    print(f"[START] Processing: {request.url}")
    try:
        # 1. Get Video ID & Check Cache
        video_id, title = get_video_id(request.url)
        cache_file = f"cache/{video_id}.json"
        audio_filename = f"{video_id}.mp3"
        audio_filepath = f"cache/{audio_filename}"
        
        transcription_data = None
        
        if os.path.exists(cache_file):
            print("[CACHE] Found cached transcription.")
            with open(cache_file, "r", encoding="utf-8") as f:
                transcription_data = json.load(f)
        
        # 2. Download Audio if missing
        if not os.path.exists(audio_filepath):
            print("Step 1: Downloading Audio...")
            # Run blocking download in thread pool
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, download_audio, request.url)
            
        # 3. Transcribe if not cached
        if not transcription_data:
            print("Step 2: Transcribing with Groq API...")
            
            # Run blocking transcription in thread pool
            loop = asyncio.get_event_loop()
            transcription = await loop.run_in_executor(None, transcribe_with_groq, audio_filepath)
            
            # Extract segments from Groq response
            segments = []
            full_text_parts = []
            
            # Groq returns segments in the response
            if hasattr(transcription, 'segments') and transcription.segments:
                for seg in transcription.segments:
                    # Segments are now dictionaries from TranscriptionResult
                    seg_text = seg["text"] if isinstance(seg, dict) else seg.text
                    seg_start = seg["start"] if isinstance(seg, dict) else seg.start
                    seg_end = seg["end"] if isinstance(seg, dict) else seg.end
                    
                    segments.append({
                        "text": seg_text,
                        "start": seg_start,
                        "end": seg_end
                    })
                    full_text_parts.append(seg_text)
            else:
                # Fallback: use full text
                segments.append({
                    "text": transcription.text,
                    "start": 0,
                    "end": 0
                })
                full_text_parts.append(transcription.text)
            
            full_text = " ".join(full_text_parts)
            
            print(f"[OK] Transcription complete: {len(segments)} segments")
            
            # Identify Surah
            result = identify_surah_via_api(segments, full_text)
            if not result:
                raise HTTPException(status_code=404, detail="Could not identify Surah from audio.")
            
            surah_id, surah_name = result
            
            transcription_data = {
                "surah_id": surah_id,
                "surah_name": surah_name,
                "segments": segments,
                "text": full_text,
                "title": title
            }
            
            # Save to cache
            with open(cache_file, "w", encoding="utf-8") as f:
                json.dump(transcription_data, f, ensure_ascii=False)
        else:
            print("[SKIP] Skipping transcription (Cached)")
            surah_id = transcription_data.get("surah_id")
            surah_name = transcription_data.get("surah_name")
            
            # Backwards compatibility for old cache format
            if not surah_id:
                full_text = transcription_data.get("text", "")
                segments = transcription_data.get("segments", [])
                result = identify_surah_via_api(segments, full_text)
                if result:
                    surah_id, surah_name = result

        # 4. Fetch Specific Text
        surah_db = fetch_surah_text(surah_id)
        
        # Prepare normalized texts for matching
        surah_texts_normalized = [normalize_arabic(entry["text"]) for entry in surah_db]
        
        # 5. Match & Sync
        print("Step 3: Syncing Timestamps...")
        matched_timeline = []
        
        segments = transcription_data["segments"]
        
        for segment in segments:
            heard_text = segment["text"]
            if len(heard_text.strip()) < 5:
                continue
            
            norm_heard_text = normalize_arabic(heard_text)

            # Fuzzy match against the ayahs of this Surah
            match = process.extractOne(norm_heard_text, surah_texts_normalized, scorer=fuzz.partial_ratio, score_cutoff=70)
            
            if match:
                _, score, index = match
                db_entry = surah_db[index]
                
                print(f"   Matched: {score:.1f}% -> Ayah {db_entry['ayah']}")
                
                matched_timeline.append({
                    "surah": db_entry["surah"],
                    "ayah": db_entry["ayah"],
                    "text": db_entry["text"],
                    "start": segment["start"],
                    "end": segment["end"]
                })
            else:
                print(f"   [NO_MATCH] No match for segment: {heard_text[:30]}...")
        
        # Sort by time
        matched_timeline.sort(key=lambda x: x["start"])

        return {
            "id": video_id,
            "title": title,
            "surah_name": surah_name,
            "surah_number": surah_id,
            "audio_url": f"/cache/{audio_filename}",
            "surah_text": surah_db,
            "timeline": matched_timeline
        }

    except Exception as e:
        print(f"ERROR: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/history")
def get_history():
    """Returns a list of cached recitations, sorted by most recent."""
    try:
        history = []
        cache_dir = "cache"
        
        files = [f for f in os.listdir(cache_dir) if f.endswith(".json")]
        
        for filename in files:
            filepath = os.path.join(cache_dir, filename)
            try:
                mtime = os.path.getmtime(filepath)
                
                with open(filepath, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    
                    surah_name = data.get("surah_name", "Unknown Surah")
                    surah_number = data.get("surah_id") or data.get("surah_number", 0)
                    title = data.get("title", filename.replace(".json", ""))

                    history.append({
                        "id": filename.replace(".json", ""),
                        "title": title,
                        "surah_name": surah_name,
                        "surah_number": surah_number,
                        "timestamp": mtime
                    })
            except Exception as e:
                print(f"[WARN] Error reading cache file {filename}: {e}")
                continue
        
        history.sort(key=lambda x: x["timestamp"], reverse=True)
        
        return history
    except Exception as e:
        print(f"[ERROR] Error fetching history: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# Run repair on startup
repair_cache()

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
