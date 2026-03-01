from flask import Flask, render_template, request, jsonify, send_file
import yt_dlp
import os
import tempfile
import threading
import uuid
from datetime import datetime, timedelta
from werkzeug.utils import secure_filename
import glob
import shutil
import re
import time
import json
import mimetypes
from urllib.parse import urlparse, parse_qs, urlunparse
import requests
import subprocess
import trabox

app = Flask(__name__)

# Store download progress and file information
download_progress = {}

# Configure cookies folder
COOKIES_FOLDER = 'cookies'
app.config['COOKIES_FOLDER'] = COOKIES_FOLDER

# Create cookies directory
os.makedirs(COOKIES_FOLDER, exist_ok=True)

# Create downloads directory
DOWNLOADS_DIR = 'downloads'
os.makedirs(DOWNLOADS_DIR, exist_ok=True)

# FFmpeg arguments for maximum compatibility
FASTSTART_ARGS = ['-movflags', '+faststart']
MP4_COMPATIBLE_ARGS = [
    '-c:v', 'libx264', 
    '-profile:v', 'baseline',  # Use baseline profile for maximum compatibility
    '-level', '3.0',            # Use level 3.0 for wider device support
    '-pix_fmt', 'yuv420p',      # Ensure pixel format compatibility
    '-c:a', 'aac', 
    '-b:a', '128k',              # Slightly lower bitrate for better compatibility
    '-ac', '2',                   # Stereo audio
    '-ar', '44100'                # Standard sample rate
]

# Additional args for social media platforms
SOCIAL_MEDIA_ARGS = [
    '-vf', 'scale=trunc(iw/2)*2:trunc(ih/2)*2',  # Ensure dimensions are even
    '-preset', 'fast',                            # Faster encoding
    '-crf', '23'                                   # Good quality/size balance
]

PLATFORMS_REQUIRE_H264 = {'facebook', 'instagram', 'tiktok', 'twitter'}

def platform_requires_h264(platform):
    return platform in PLATFORMS_REQUIRE_H264

ALLOWED_MIME_TYPES = {
    'mp4': 'video/mp4',
    'mkv': 'video/x-matroska',
    'mov': 'video/quicktime',
    'webm': 'video/webm',
    'flv': 'video/x-flv',
    '3gp': 'video/3gpp',
    'avi': 'video/x-msvideo',
    'mp3': 'audio/mpeg',
    'm4a': 'audio/mp4',
    'flac': 'audio/flac',
    'wav': 'audio/wav',
    'ogg': 'audio/ogg',
    'aac': 'audio/aac',
    'opus': 'audio/opus',
    'wma': 'audio/x-ms-wma'
}

def parse_cookie_file(cookiefile):
    cookies = {}
    if not cookiefile or not os.path.exists(cookiefile):
        return cookies
    with open(cookiefile, 'r') as fp:
        for line in fp:
            if line.startswith('#') or not line.strip():
                continue
            line_fields = line.strip().split('\t')
            if len(line_fields) >= 7:
                cookies[line_fields[5]] = line_fields[6]
    return cookies

def get_terabox_file_info(url, cookiefile):
    info = trabox.get_file_info(url, cookiefile=cookiefile)
    if not info:
        return None
    if not info.get('filename'):
        info['filename'] = 'terabox_file'
    return info

def get_platform_from_url(url):
    """Detect which platform the URL is from"""
    parsed = urlparse(url)
    host = (parsed.hostname or parsed.netloc or url).lower().split(':')[0]
    if host.startswith('www.'):
        host = host[4:]
    if host in ('youtube.com', 'youtu.be') or host.endswith('.youtube.com'):
        return 'youtube'
    elif host == 'facebook.com' or host.endswith('.facebook.com'):
        return 'facebook'
    elif host == 'instagram.com' or host.endswith('.instagram.com'):
        return 'instagram'
    elif host in ('terabox.com', 'teraboxapp.com', 'nephobox.com') or host.endswith('.terabox.com') or host.endswith('.teraboxapp.com') or host.endswith('.nephobox.com'):
        return 'terabox'
    elif host == 'tiktok.com' or host.endswith('.tiktok.com'):
        return 'tiktok'
    elif host in ('twitter.com', 'x.com') or host.endswith('.twitter.com') or host.endswith('.x.com'):
        return 'twitter'
    elif host == 'vimeo.com' or host.endswith('.vimeo.com'):
        return 'vimeo'
    elif host == 'dailymotion.com' or host.endswith('.dailymotion.com'):
        return 'dailymotion'
    else:
        return 'all'

def get_cookie_file_for_url(url):
    """Get the appropriate cookie file for the given URL"""
    platform = get_platform_from_url(url)
    
    # Check if platform-specific cookie file exists
    platform_cookie = os.path.join(COOKIES_FOLDER, f"{platform}.txt")
    if os.path.exists(platform_cookie):
        return f"{platform}.txt"
    
    # Fall back to all.txt if it exists
    all_cookie = os.path.join(COOKIES_FOLDER, "all.txt")
    if os.path.exists(all_cookie):
        return "all.txt"
    
    # No cookie file available
    return None

class ProgressHook:
    def __init__(self, download_id):
        self.download_id = download_id
    
    def hook(self, d):
        if d['status'] == 'downloading':
            percent = d.get('_percent_str', '0%').strip()
            speed = d.get('_speed_str', 'N/A').strip()
            download_progress[self.download_id] = {
                'status': 'downloading',
                'percent': percent,
                'speed': speed
            }
        elif d['status'] == 'finished':
            download_progress[self.download_id] = {
                'status': 'processing',
                'message': 'Download completed, processing file...'
            }

def sanitize_filename(filename, max_length=100):
    """Sanitize filename and limit its length more aggressively"""
    if not filename:
        return "video"
    
    # Remove invalid characters
    filename = re.sub(r'[<>:"/\\|?*]', '', filename)
    
    # Remove emojis and special characters (keep basic alphanumeric, spaces, hyphens, underscores)
    filename = re.sub(r'[^\w\s\-_\.]', '', filename)
    
    # Replace multiple spaces with single space
    filename = re.sub(r'\s+', ' ', filename)
    
    # Trim leading/trailing spaces
    filename = filename.strip()
    
    # Replace spaces with underscores
    filename = filename.replace(' ', '_')
    
    # If filename is still too long, truncate it
    if len(filename) > max_length:
        # Keep extension if present
        name, ext = os.path.splitext(filename)
        # Truncate the name part
        truncated_name = name[:max_length - len(ext)]
        filename = truncated_name + ext
    
    return filename

def get_safe_filename(title, format_type, format_ext, max_length=150):
    """Generate a safe filename with proper extension"""
    if not title:
        title = "video"
    
    # Basic sanitization
    safe_title = re.sub(r'[<>:"/\\|?*]', '', title)
    safe_title = re.sub(r'[^\w\s\-_\.]', '', safe_title)
    safe_title = safe_title.replace(' ', '_')[:100]
    
    # Set proper extension based on format type and requested format
    if format_type == 'audio':
        if format_ext in ['mp3', 'flac', 'm4a', 'wav', 'ogg', 'aac', 'wma', 'opus']:
            extension = format_ext
        else:
            extension = 'm4a'  # Default audio format
    else:
        if format_ext in ['mp4', 'mkv', 'webm', 'avi', 'mov', 'flv', '3gp']:
            extension = format_ext
        else:
            extension = 'mp4'  # Default video format
    
    filename = f"{safe_title}.{extension}"
    
    # Final length check
    if len(filename) > max_length:
        name_part = safe_title[:max_length - len(extension) - 1]
        filename = f"{name_part}.{extension}"
    
    return filename

def get_format_sort_for_platform(platform):
    if platform_requires_h264(platform):
        return ['vcodec:avc1', 'acodec:aac', 'ext:mp4:m4a', 'proto:https', 'res', 'fps']
    return None

def needs_h264_conversion(platform, format_type):
    return platform_requires_h264(platform) and format_type != 'audio'

def build_video_format_string(format_id, output_format, platform=None):
    """Build a resilient yt-dlp format string that keeps audio/video together"""
    if format_id in ['best', 'worst']:
        return format_id

    prefers_h264 = platform_requires_h264(platform)

    if prefers_h264:
        # For social media platforms, download the user-selected format with best audio.
        # H264 conversion is handled post-download by ensure_compatible_video so we
        # must NOT filter by codec here – that would silently ignore the user's choice.
        return (
            f"{format_id}+bestaudio[ext=m4a]/"
            f"{format_id}+bestaudio[acodec^=mp4a]/"
            f"{format_id}+bestaudio/"
            f"{format_id}/"
            f"bestvideo[ext=mp4][vcodec^=avc1]+bestaudio[ext=m4a]/"
            f"bestvideo[vcodec^=avc1]+bestaudio/"
            f"best"
        )

    if output_format == 'mp4':
        # Prefer the exact user-selected format combined with best audio
        return (
            f"{format_id}+bestaudio[ext=m4a]/"
            f"{format_id}+bestaudio[acodec^=mp4a]/"
            f"{format_id}+bestaudio/"
            f"{format_id}/"
            f"bestvideo[ext=mp4]+bestaudio[ext=m4a]/"
            f"best[ext=mp4]/"
            f"best"
        )

    fallback_combo = f"bestvideo[ext={output_format}]+bestaudio/bestvideo+bestaudio"
    return f'{format_id}+bestaudio/{fallback_combo}'

def deduplicate_video_formats_by_height(video_formats, platform):
    """For social media platforms, keep only the best format per resolution height."""
    if not platform_requires_h264(platform):
        return video_formats

    seen = {}
    for fmt in video_formats:
        height = fmt.get('height') or 0
        if height not in seen:
            seen[height] = fmt
        else:
            # Prefer the format with the larger filesize (better quality)
            if (fmt.get('filesize_approx') or 0) > (seen[height].get('filesize_approx') or 0):
                seen[height] = fmt

    return sorted(seen.values(), key=lambda x: x.get('height') or 0, reverse=True)

def ensure_compatible_video(input_path, output_path, platform):
    """
    Use ffmpeg to ensure video is compatible with all players
    Especially important for Facebook/Instagram videos
    """
    try:
        # Build ffmpeg command for maximum compatibility
        cmd = ['ffmpeg', '-i', input_path]
        
        # Limit to 2 threads to keep CPU usage low
        cmd.extend(['-threads', '2'])

        # Add video codec settings for H264 baseline profile
        cmd.extend([
            '-c:v', 'libx264',
            '-profile:v', 'baseline',
            '-level', '3.0',
            '-pix_fmt', 'yuv420p',
            '-preset', 'fast',
            '-crf', '23'
        ])
        
        # Ensure dimensions are even (required for H264)
        cmd.extend(['-vf', 'scale=trunc(iw/2)*2:trunc(ih/2)*2'])
        
        # Add audio codec settings
        cmd.extend([
            '-c:a', 'aac',
            '-b:a', '128k',
            '-ac', '2',
            '-ar', '44100'
        ])
        
        # Add faststart for web optimization
        cmd.extend(['-movflags', '+faststart'])
        
        # Overwrite output file if exists
        cmd.extend(['-y', output_path])
        
        # Run ffmpeg
        result = subprocess.run(cmd, capture_output=True, text=True)
        
        if result.returncode == 0 and os.path.exists(output_path):
            return True
        else:
            print(f"FFmpeg error: {result.stderr}")
            return False
            
    except Exception as e:
        print(f"Error during video compatibility conversion: {e}")
        return False

def get_mime_type(file_path):
    ext = os.path.splitext(file_path)[1].lower().lstrip('.')
    return ALLOWED_MIME_TYPES.get(ext) or (mimetypes.guess_type(file_path)[0] or 'application/octet-stream')

def is_valid_download_id(download_id):
    return bool(re.fullmatch(r'[a-f0-9\-]{32,36}', str(download_id)))

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/get_formats', methods=['POST'])
def get_formats():
    try:
        data = request.json
        url = data.get('url')
        
        if not url:
            return jsonify({'error': 'URL is required'}), 400
        
        # Get appropriate cookie file for this URL
        cookies_file = get_cookie_file_for_url(url)
        platform = get_platform_from_url(url)
        
        if platform == 'terabox' and not cookies_file:
            return jsonify({'error': 'TeraBox downloads require a valid cookies file in the cookies directory'}), 400

        if platform == 'terabox':
            cookies_path = os.path.join(app.config['COOKIES_FOLDER'], cookies_file)
            info = get_terabox_file_info(url, cookies_path)
            if not info:
                return jsonify({'error': 'Could not extract TeraBox file information'}), 400
            ext = os.path.splitext(info['filename'])[1].lstrip('.')
            if not ext:
                ext = 'mp4'
            video_formats = [{
                'format_id': 'terabox_direct',
                'ext': ext,
                'filesize_approx': info.get('size'),
                'format_note': 'Direct download',
                'quality': 0
            }]
            return jsonify({
                'title': info.get('filename', 'TeraBox File'),
                'duration': None,
                'uploader': 'TeraBox',
                'video_formats': video_formats,
                'audio_formats': [],
                'thumbnail': None,
                'view_count': None,
                'description': ''
            })
        
        # Configure yt-dlp options for format extraction
        ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'extract_flat': False,
            'http_headers': {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
            },
        }
        
        # Add cookies if available
        if cookies_file:
            cookies_path = os.path.join(app.config['COOKIES_FOLDER'], cookies_file)
            if os.path.exists(cookies_path):
                ydl_opts['cookiefile'] = cookies_path
        
        if platform == 'terabox':
            ydl_opts['http_headers']['Referer'] = url
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            try:
                # Extract info without downloading
                info = ydl.extract_info(url, download=False)
                
                if not info:
                    return jsonify({'error': 'Could not extract video information'}), 400
                
                # Organize formats
                video_formats = []
                audio_formats = []
                
                if 'formats' in info:
                    for fmt in info['formats']:
                        # Skip formats without proper IDs
                        if not fmt.get('format_id'):
                            continue
                        
                        format_info = {
                            'format_id': fmt['format_id'],
                            'ext': fmt.get('ext', ''),
                            'filesize_approx': fmt.get('filesize_approx') or fmt.get('filesize'),
                            'format_note': fmt.get('format_note', ''),
                            'quality': fmt.get('quality', 0)
                        }
                        
                        # Check if it's a video format (has video codec and height)
                        if fmt.get('vcodec') != 'none' and fmt.get('height'):
                            format_info.update({
                                'height': fmt.get('height'),
                                'width': fmt.get('width'),
                                'fps': fmt.get('fps'),
                                'vcodec': fmt.get('vcodec'),
                                'acodec': fmt.get('acodec', 'none')
                            })
                            video_formats.append(format_info)
                        
                        # Check if it's an audio format (has audio codec but no video)
                        elif fmt.get('acodec') != 'none' and fmt.get('vcodec') == 'none':
                            format_info.update({
                                'acodec': fmt.get('acodec'),
                                'abr': fmt.get('abr'),  # Audio bitrate
                                'asr': fmt.get('asr')   # Audio sample rate
                            })
                            audio_formats.append(format_info)
                
                # Sort video formats by quality (height) descending
                video_formats.sort(key=lambda x: (x.get('height', 0), x.get('fps', 0)), reverse=True)
                
                # Sort audio formats by bitrate descending
                audio_formats.sort(key=lambda x: x.get('abr') or 0, reverse=True)
                
                # For social media platforms, deduplicate by resolution to reduce confusion
                video_formats = deduplicate_video_formats_by_height(video_formats, platform)
                
                # If no separate audio formats found, create some basic options
                if not audio_formats and video_formats:
                    audio_formats = [
                        {
                            'format_id': 'bestaudio',
                            'ext': 'best',
                            'format_note': 'Best Available Audio',
                            'abr': None,
                            'acodec': 'best'
                        }
                    ]
                
                return jsonify({
                    'title': info.get('title', 'Unknown'),
                    'duration': info.get('duration'),
                    'uploader': info.get('uploader'),
                    'video_formats': video_formats[:15],  # Limit to top 15 formats
                    'audio_formats': audio_formats[:10],  # Limit to top 10 formats
                    'thumbnail': info.get('thumbnail'),
                    'view_count': info.get('view_count'),
                    'description': info.get('description', '')[:500] if info.get('description') else ''
                })
                
            except yt_dlp.utils.ExtractorError as e:
                error_msg = str(e)
                if "Private video" in error_msg:
                    error_msg = "This video is private. Try using a cookies file to access it."
                elif "Video unavailable" in error_msg:
                    error_msg = "This video is unavailable or has been removed."
                elif "Sign in to confirm your age" in error_msg:
                    error_msg = "Age-restricted content. Please use a cookies file from a logged-in session."
                return jsonify({'error': f'Extraction failed: {error_msg}'}), 400
                
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/download', methods=['POST'])
def download_video():
    try:
        data = request.json
        url = data.get('url')
        format_type = data.get('format_type')  # 'video' or 'audio'
        format_id = data.get('format_id')  # The specific format ID selected by user
        output_format = data.get('output_format') or data.get('format') or 'mp4'  # Final output format
        
        if not url:
            return jsonify({'error': 'URL is required'}), 400
        
        if not format_id:
            return jsonify({'error': 'Format ID is required'}), 400
        
        # Get appropriate cookie file for this URL
        cookies_file = get_cookie_file_for_url(url)
        platform = get_platform_from_url(url)
        
        if platform == 'terabox' and not cookies_file:
            return jsonify({'error': 'TeraBox downloads need a cookies file in the cookies directory'}), 400
        
        print(f"Using cookie file: {cookies_file} for URL: {url}")
        print(f"Platform detected: {platform}")
        
        download_id = str(uuid.uuid4())
        
        # Start download in background
        thread = threading.Thread(
            target=perform_download,
            args=(download_id, url, format_type, format_id, output_format, cookies_file)
        )
        thread.start()
        
        return jsonify({
            'download_id': download_id,
            'message': 'Download started'
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

def perform_download(download_id, url, format_type, format_id, output_format, cookies_file):
    # Use a shorter temp directory
    temp_dir = tempfile.mkdtemp(dir='/tmp')
    converted_file = None
    
    try:
        platform = get_platform_from_url(url)
        
        # For social media platforms, ensure we output MP4 for maximum compatibility
        if needs_h264_conversion(platform, format_type) and output_format != 'mp4':
            output_format = 'mp4'
            print(f"Forcing MP4 output for {platform} to maintain playback compatibility")
        
        if platform == 'terabox':
            cookies_path = os.path.join(app.config['COOKIES_FOLDER'], cookies_file) if cookies_file else None
            info = get_terabox_file_info(url, cookies_path)
            if not info or not info.get('direct_link'):
                download_progress[download_id] = {
                    'status': 'error',
                    'error': 'Unable to fetch TeraBox download link'
                }
                shutil.rmtree(temp_dir, ignore_errors=True)
                return
                
            session = requests.Session()
            if info.get('cookies'):
                session.cookies.update(info['cookies'])
            headers = info.get('headers') or {}
            headers.setdefault('Referer', url)
            
            response = session.get(info['direct_link'], headers=headers, stream=True)
            if response.status_code != 200:
                download_progress[download_id] = {
                    'status': 'error',
                    'error': 'Failed to download TeraBox file'
                }
                shutil.rmtree(temp_dir, ignore_errors=True)
                return
                
            original_name = info.get('filename') or 'terabox_file'
            safe_filename = sanitize_filename(original_name)
            temp_file_path = os.path.join(temp_dir, safe_filename)
            
            total = int(response.headers.get('content-length') or 0)
            downloaded = 0
            start_time = time.time()
            
            with open(temp_file_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=1024 * 64):
                    if not chunk:
                        continue
                    f.write(chunk)
                    downloaded += len(chunk)
                    percent = f"{(downloaded / total * 100):.1f}%" if total else '...'
                    
                    elapsed = time.time() - start_time
                    speed = ''
                    if elapsed > 0:
                        speed_val = downloaded / elapsed
                        if speed_val >= 1024 * 1024:
                            speed = f"{speed_val / (1024 * 1024):.2f} MB/s"
                        elif speed_val >= 1024:
                            speed = f"{speed_val / 1024:.2f} KB/s"
                    
                    download_progress[download_id] = {
                        'status': 'downloading',
                        'percent': percent,
                        'speed': speed
                    }
            
            # For TeraBox files on social platforms, ensure compatibility
            if platform_requires_h264(platform):
                converted_path = os.path.join(temp_dir, f"converted_{safe_filename}")
                if ensure_compatible_video(temp_file_path, converted_path, platform):
                    temp_file_path = converted_path
                    safe_filename = f"converted_{safe_filename}"
            
            target_path = os.path.join(DOWNLOADS_DIR, safe_filename)
            if os.path.exists(target_path):
                name, ext = os.path.splitext(safe_filename)
                counter = 1
                while os.path.exists(target_path):
                    target_path = os.path.join(DOWNLOADS_DIR, f"{name}_{download_id}_{counter}{ext}")
                    counter += 1
            
            moved = False
            try:
                shutil.move(temp_file_path, target_path)
                safe_filename = os.path.basename(target_path)
                moved = True
            except (OSError, shutil.Error):
                target_path = temp_file_path
                safe_filename = os.path.basename(temp_file_path)
            
            if moved and temp_dir and os.path.exists(temp_dir):
                shutil.rmtree(temp_dir, ignore_errors=True)
                temp_dir = None
            
            download_progress[download_id] = {
                'status': 'finished',
                'filename': safe_filename,
                'file_path': target_path,
                'temp_dir': temp_dir,
                'format_id': format_id,
                'completed_at': time.time()
            }
            return
        
        # Use simple filename pattern for download to avoid long paths
        simple_pattern = os.path.join(temp_dir, f'dl_{download_id[:8]}.%(ext)s')
        
        # Configure yt-dlp options
        ydl_opts = {
            'outtmpl': simple_pattern,
            'progress_hooks': [ProgressHook(download_id).hook],
            'extract_flat': False,
            'writethumbnail': False,
            'writeinfojson': False,
            'ignoreerrors': False,
            'no_warnings': True,
            'merge_output_format': output_format,
            'prefer_ffmpeg': True,
            'concurrent_fragment_downloads': 1,  # Limit parallel fragment downloads (saves RAM/CPU)
            'http_headers': {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
            },
            'extractor_retries': 3,
            'retries': 3,
        }
        
        sort_rules = get_format_sort_for_platform(platform)
        if sort_rules and format_type != 'audio':
            ydl_opts['format_sort'] = sort_rules
        
        # Add cookies if available
        if cookies_file:
            cookies_path = os.path.join(app.config['COOKIES_FOLDER'], cookies_file)
            if os.path.exists(cookies_path):
                ydl_opts['cookiefile'] = cookies_path
                print(f"Using cookies from: {cookies_path}")
        
        if platform == 'terabox':
            ydl_opts['http_headers']['Referer'] = url
        
        # Set the format based on user selection
        if format_type == 'audio':
            if format_id == 'bestaudio':
                ydl_opts['format'] = 'bestaudio/best'
            else:
                ydl_opts['format'] = format_id
            
            # Set up audio post-processing
            postprocessors = []
            if output_format in ['mp3', 'flac', 'm4a', 'wav', 'ogg', 'aac', 'wma', 'opus']:
                postprocessors.append({
                    'key': 'FFmpegExtractAudio',
                    'preferredcodec': output_format,
                    'preferredquality': '192',
                })
            
            if postprocessors:
                ydl_opts['postprocessors'] = postprocessors
                
        else:  # video
            # For video downloads, ensure we get both video and audio
            ydl_opts['format'] = build_video_format_string(format_id, output_format, platform)
            
            # Set up video post-processing for format conversion
            postprocessors = []
            
            # Always add metadata
            postprocessors.append({
                'key': 'FFmpegMetadata',
                'add_metadata': True,
            })
            
            # Add video convertor if needed
            if output_format in ['mp4', 'mkv', 'avi', 'mov', 'webm', 'flv', '3gp']:
                postprocessors.append({
                    'key': 'FFmpegVideoConvertor',
                    'preferedformat': output_format,
                })
            
            if postprocessors:
                ydl_opts['postprocessors'] = postprocessors
                
                # Add FFmpeg args for web optimisation (faststart only).
                # H264 re-encoding for social media platforms is handled after
                # download by ensure_compatible_video to avoid double-encoding.
                if output_format == 'mp4':
                    ydl_opts['postprocessor_args'] = {'FFmpegVideoConvertor': FASTSTART_ARGS}
        
        # Download the video
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            try:
                # Perform the download in a single pass (avoids a redundant
                # extract_info(download=False) call that wastes CPU/bandwidth
                # and can cause format-ID expiration on time-limited platforms).
                result = ydl.extract_info(url, download=True)
                if not result:
                    download_progress[download_id] = {
                        'status': 'error',
                        'error': 'Could not extract video information'
                    }
                    return

                # Get the title for filename from the download result
                title = result.get('title', 'video')
                safe_filename = get_safe_filename(title, format_type, output_format)

                # Brief wait for ffmpeg post-processing to finish writing the file
                time.sleep(1)

                # Find the downloaded file
                files = [f for f in os.listdir(temp_dir) if os.path.isfile(os.path.join(temp_dir, f))]
                if files:
                    # Sort by modification time to get the latest file
                    files.sort(key=lambda x: os.path.getmtime(os.path.join(temp_dir, x)), reverse=True)
                    downloaded_file = files[0]
                    original_file_path = os.path.join(temp_dir, downloaded_file)

                    # For social media platforms, ensure maximum compatibility
                    final_file_path = original_file_path
                    current_filename = downloaded_file

                    if platform_requires_h264(platform) and format_type == 'video':
                        # Create a compatibility-converted version
                        converted_path = os.path.join(temp_dir, f"compat_{safe_filename}")
                        if ensure_compatible_video(original_file_path, converted_path, platform):
                            final_file_path = converted_path
                            current_filename = f"compat_{safe_filename}"
                            # Clean up original if conversion succeeded
                            try:
                                os.remove(original_file_path)
                            except:
                                pass
                        else:
                            # If conversion fails, try to use the original
                            final_file_path = original_file_path
                            current_filename = downloaded_file
                    else:
                        # Rename to the proper filename if needed
                        if original_file_path != os.path.join(temp_dir, safe_filename):
                            try:
                                os.rename(original_file_path, os.path.join(temp_dir, safe_filename))
                                final_file_path = os.path.join(temp_dir, safe_filename)
                                current_filename = safe_filename
                            except OSError:
                                final_file_path = original_file_path
                                current_filename = downloaded_file

                    # Verify the file exists and is accessible
                    if os.path.exists(final_file_path) and os.path.getsize(final_file_path) > 0:
                        target_path = os.path.join(DOWNLOADS_DIR, current_filename)

                        # Handle duplicate filenames
                        if os.path.exists(target_path):
                            name, ext = os.path.splitext(current_filename)
                            counter = 1
                            while os.path.exists(target_path):
                                target_path = os.path.join(DOWNLOADS_DIR, f"{name}_{download_id}_{counter}{ext}")
                                counter += 1

                        target_name = os.path.basename(target_path)
                        moved = False

                        try:
                            shutil.move(final_file_path, target_path)
                            final_file_path = target_path
                            current_filename = target_name
                            moved = True
                        except Exception as move_err:
                            print(f"Move failed for {final_file_path} -> {target_path}: {move_err}")
                            current_filename = os.path.basename(final_file_path)

                        if moved and temp_dir and os.path.exists(temp_dir):
                            shutil.rmtree(temp_dir, ignore_errors=True)
                            temp_dir = None

                        download_progress[download_id] = {
                            'status': 'finished',
                            'filename': current_filename,
                            'file_path': final_file_path,
                            'temp_dir': temp_dir,
                            'format_id': format_id,
                            'completed_at': time.time()
                        }
                    else:
                        download_progress[download_id] = {
                            'status': 'error',
                            'error': 'File was created but is empty or inaccessible'
                        }
                else:
                    download_progress[download_id] = {
                        'status': 'error',
                        'error': 'Download completed but no files found'
                    }
            except yt_dlp.utils.ExtractorError as e:
                error_msg = str(e)
                if "Private video" in error_msg:
                    error_msg = "This video is private. Try using a cookies file to access it."
                elif "Video unavailable" in error_msg:
                    error_msg = "This video is unavailable or has been removed."
                elif "Sign in to confirm your age" in error_msg:
                    error_msg = "Age-restricted content. Please use a cookies file from a logged-in session."
                elif "format is not available" in error_msg.lower():
                    error_msg = f"Requested format is not available for this video."
                download_progress[download_id] = {
                    'status': 'error',
                    'error': f'Extraction failed: {error_msg}'
                }
                shutil.rmtree(temp_dir, ignore_errors=True)
                
            except Exception as e:
                error_msg = str(e)
                download_progress[download_id] = {
                    'status': 'error',
                    'error': f'Download error: {error_msg}'
                }
                shutil.rmtree(temp_dir, ignore_errors=True)
            
    except Exception as e:
        download_progress[download_id] = {
            'status': 'error',
            'error': str(e)
        }
        if 'temp_dir' in locals() and os.path.exists(temp_dir):
            shutil.rmtree(temp_dir, ignore_errors=True)

@app.route('/progress/<download_id>')
def get_progress(download_id):
    if not is_valid_download_id(download_id):
        return jsonify({'status': 'error', 'error': 'Invalid download id'}), 400
    progress = download_progress.get(download_id, {'status': 'not_found'})
    return jsonify(progress)

@app.route('/download_file/<download_id>')
def download_file(download_id):
    try:
        if not is_valid_download_id(download_id):
            return jsonify({'error': 'Invalid download id'}), 400
        progress = download_progress.get(download_id)
        
        if not progress or progress['status'] != 'finished':
            return jsonify({'error': 'File not ready for download'}), 404
        
        file_path = progress.get('file_path')
        temp_dir = progress.get('temp_dir')
        
        if not file_path or not os.path.exists(file_path):
            return jsonify({'error': 'File not found'}), 404
        
        # Verify file is not empty
        if os.path.getsize(file_path) == 0:
            return jsonify({'error': 'File is empty'}), 404
        
        # Get filename for download
        filename = progress.get('filename', 'download')
        
        # Create response
        response = send_file(
            file_path, 
            as_attachment=True,
            download_name=filename
        )
        
        # Clean up the temporary directory after sending the file
        @response.call_on_close
        def cleanup():
            try:
                if temp_dir and os.path.exists(temp_dir):
                    shutil.rmtree(temp_dir)
                if download_id in download_progress:
                    download_progress[download_id]['temp_dir'] = None
            except Exception as cleanup_err:
                print(f"Cleanup error for {download_id}: {cleanup_err}")
        
        return response
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/play_file/<download_id>')
def play_file(download_id):
    try:
        if not is_valid_download_id(download_id):
            return jsonify({'error': 'Invalid download id'}), 400
        progress = download_progress.get(download_id)
        
        if not progress or progress.get('status') != 'finished':
            return jsonify({'error': 'File not ready for playback'}), 404
        
        file_path = progress.get('file_path')
        
        if not file_path or not os.path.exists(file_path):
            return jsonify({'error': 'File not found'}), 404
        
        mime_type = get_mime_type(file_path)
        
        return send_file(
            file_path,
            as_attachment=False,
            download_name=progress.get('filename', os.path.basename(file_path)),
            mimetype=mime_type,
            conditional=True
        )
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# Cleanup function to remove old temporary files
def cleanup_old_files():
    """Clean up old files from progress tracking"""
    while True:
        time.sleep(3600)  # Run every hour
        try:
            current_time = time.time()
            to_remove = []
            
            for download_id in list(download_progress.keys()):
                progress = download_progress.get(download_id)
                # Remove entries older than 24 hours
                if progress and progress.get('status') == 'finished':
                    file_path = progress.get('file_path')
                    if file_path and os.path.exists(file_path):
                        file_age = current_time - os.path.getctime(file_path)
                        if file_age > 86400:  # 24 hours
                            to_remove.append(download_id)
                            try:
                                os.remove(file_path)
                            except Exception:
                                pass
                            temp_dir = progress.get('temp_dir')
                            if temp_dir and os.path.exists(temp_dir):
                                shutil.rmtree(temp_dir, ignore_errors=True)
            
            for download_id in to_remove:
                if download_id in download_progress:
                    del download_progress[download_id]
                    
        except Exception as e:
            print(f"Cleanup error: {e}")

# Start cleanup thread
cleanup_thread = threading.Thread(target=cleanup_old_files, daemon=True)
cleanup_thread.start()

if __name__ == '__main__':
    # Print available cookie files
    print("Available cookie files:")
    for file_path in glob.glob(os.path.join(COOKIES_FOLDER, '*.txt')):
        filename = os.path.basename(file_path)
        print(f"  - {filename}")
    
    app.run(debug=True, host='0.0.0.0', port=5003)