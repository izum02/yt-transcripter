import os
import re
import json
import threading
import itertools
import urllib.parse
from urllib.parse import urlparse, parse_qs
import subprocess
import tempfile

import requests
import yt_dlp
from flask import Flask, jsonify, request
from flask_cors import CORS
from sudachipy import Dictionary

# --------------------------------
# テキスト読み付与
# --------------------------------
tokenizer = Dictionary().create()

IGNORE_SPACES = {" ", "　"}
KEEP_SYMBOLS = set('?!"#$%&()-=~^@＠*+;:[]{}\\/,.ー、。・「」（）［］｛｝！？：；　…―‐‒–—＋＃＄％＆＊／＼，．“”‘’ ')

def text_to_yomi(text: str) -> str:
    result = []
    for m in tokenizer.tokenize(text):
        surface = m.surface()
        if surface in IGNORE_SPACES:
            continue
        if surface in KEEP_SYMBOLS:
            result.append(surface)
            continue
        reading = m.reading_form()
        pos = m.part_of_speech()
        if (
            reading == "キゴウ"
            and pos[0] == "補助記号"
            and surface not in {"記号", "キゴウ", "きごう", "揮毫", "几号", "騎劫", "揮ごう"}
        ):
            continue
        result.append(reading)
    return "".join(result)

# --------------------------------
# Deno パスの読み込み
# --------------------------------
def load_deno_config():
    """deno_config.json から Deno のパスを読み込む"""
    try:
        with open('deno_config.json', 'r') as f:
            config = json.load(f)
            deno_path = config.get('deno_path')
            if deno_path and os.path.exists(deno_path):
                return deno_path
            elif deno_path:
                print(f"Warning: Deno path '{deno_path}' does not exist")
    except FileNotFoundError:
        print("Warning: deno_config.json not found")
    except json.JSONDecodeError:
        print("Warning: deno_config.json is invalid JSON")
    
    # デフォルトでは PATH から deno を探す
    return "deno"

DENO_PATH = load_deno_config()
print(f"Using Deno path: {DENO_PATH}")

# --------------------------------
# Webshare プロキシ管理
# --------------------------------
WEBSHARE_API_TOKEN = "73z2sf8gniy33wwoq7jeo1c2jwm0qt3dmoishc8z"
WEBSHARE_API_URL = "https://proxy.webshare.io/api/v2/proxy/list/"

# グローバルなプロキシプールとスレッドセーフな巡回インデックス
proxy_pool = []          # プロキシ情報のリスト
proxy_dict = {}          # "address-port" -> プロキシ情報
proxy_cycle = None       # itertools.cycle のイテレータ
proxy_lock = threading.Lock()

def fetch_proxies_from_api():
    """Webshare API から全プロキシを取得し、プールを更新する"""
    headers = {"Authorization": f"Token {WEBSHARE_API_TOKEN}"}
    proxies = []
    page = 1
    while True:
        params = {"page": page, "page_size": 100, "mode": "direct"}
        resp = requests.get(WEBSHARE_API_URL, headers=headers, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        results = data.get("results", [])
        proxies.extend(results)
        if data.get("next") is None:
            break
        page += 1

    return proxies

def initialize_proxy_pool():
    """起動時にプロキシプールを初期化（スレッドセーフ）"""
    global proxy_pool, proxy_dict, proxy_cycle
    raw_proxies = fetch_proxies_from_api()
    if not raw_proxies:
        raise RuntimeError("No proxies available from Webshare API")

    with proxy_lock:
        proxy_pool = raw_proxies
        # アドレス:ポート をキーにして辞書を作成
        proxy_dict = {
            f"{p['proxy_address']}-{p['port']}": p
            for p in proxy_pool
        }
        # 巡回イテレータを再生成
        proxy_cycle = itertools.cycle(proxy_pool)

def get_next_proxy():
    """次のプロキシをスレッドセーフに取得"""
    with proxy_lock:
        if proxy_cycle is None:
            raise RuntimeError("Proxy pool not initialized")
        return next(proxy_cycle)

def make_proxy(proxy_info: dict) -> str:
    """プロキシ情報からプロキシURL文字列を生成"""
    return (
        f"http://{proxy_info['username']}:{proxy_info['password']}"
        f"@{proxy_info['proxy_address']}:{proxy_info['port']}"
    )

def get_proxy_info_from_session(session: str) -> dict:
    """セッション文字列 (address-port) からプロキシ情報を辞書から引く"""
    with proxy_lock:
        return proxy_dict.get(session)

# --------------------------------
# Deno が利用可能か確認
# --------------------------------
def check_deno_available():
    """Deno 実行可能ファイルが利用可能か確認する"""
    try:
        result = subprocess.run(
            [DENO_PATH, "--version"],
            capture_output=True,
            text=True,
            timeout=5
        )
        if result.returncode == 0:
            print(f"Deno is available: {result.stdout.splitlines()[0]}")
            return True
    except (subprocess.SubprocessError, FileNotFoundError):
        pass
    print("Warning: Deno is not available. EJS support may not work.")
    return False

DENO_AVAILABLE = check_deno_available()

# --------------------------------
# 字幕パース (SRT / VTT / JSON3)
# --------------------------------
def parse_srt(text: str):
    """SRT形式のテキストをセグメントのリストに変換"""
    pattern = re.compile(
        r'(\d+)\n(\d{1,2}:\d{2}:\d{2}[.,]\d{3}) --> (\d{1,2}:\d{2}:\d{2}[.,]\d{3})\n(.*?)(?=\n\n|\Z)',
        re.DOTALL
    )
    segments = []
    for m in pattern.finditer(text):
        start_str = m.group(2).replace(',', '.')
        end_str = m.group(3).replace(',', '.')
        start = time_to_seconds(start_str)
        end = time_to_seconds(end_str)
        txt = m.group(4).strip().replace('\n', ' ')
        segments.append({
            'text': txt,
            'start': start,
            'duration': end - start
        })
    return segments

def parse_vtt(text: str):
    """WebVTT形式のテキストをセグメントのリストに変換（YouTubeの形式に対応）"""
    text = re.sub(r'^WEBVTT.*?\n', '', text, flags=re.MULTILINE)
    lines = text.split('\n')
    segments = []
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if not line:
            i += 1
            continue
        if '-->' in line:
            time_match = re.match(r'(\d{1,2}:\d{2}:\d{2}[.,]\d{3})\s*-->\s*(\d{1,2}:\d{2}:\d{2}[.,]\d{3})', line)
            if time_match:
                start_str = time_match.group(1).replace(',', '.')
                end_str = time_match.group(2).replace(',', '.')
                start = time_to_seconds(start_str)
                end = time_to_seconds(end_str)
                i += 1
                text_lines = []
                while i < len(lines):
                    current_line = lines[i].strip()
                    if not current_line:
                        break
                    clean_line = re.sub(r'<[^>]+>', '', current_line)
                    text_lines.append(clean_line)
                    i += 1
                txt = ' '.join(text_lines).strip()
                if txt:
                    segments.append({
                        'text': txt,
                        'start': start,
                        'duration': end - start
                    })
        i += 1
    return segments

def parse_json3(data: dict):
    """JSON3形式の字幕をセグメントのリストに変換"""
    segments = []
    events = data.get('events', [])
    for event in events:
        start_ms = event.get('tStartMs', 0)
        duration_ms = event.get('dDurationMs', 0)
        segs = event.get('segs', [])
        text_parts = []
        for seg in segs:
            if 'utf8' in seg:
                text_parts.append(seg['utf8'])
        text = ''.join(text_parts).strip()
        text = text.replace('\n', ' ')
        text = re.sub(r'\s+', ' ', text)
        if text:
            segments.append({
                'text': text,
                'start': start_ms / 1000.0,
                'duration': duration_ms / 1000.0
            })
    return segments

def time_to_seconds(ts: str) -> float:
    parts = ts.split(':')
    h = int(parts[0])
    m = int(parts[1])
    s = float(parts[2])
    return h * 3600 + m * 60 + s

def parse_subtitle(text: str, format_hint: str = None) -> list:
    if format_hint == 'json3':
        try:
            data = json.loads(text)
            if 'events' in data:
                return parse_json3(data)
        except:
            pass
    if format_hint == 'vtt' or ('WEBVTT' in text[:100]):
        return parse_vtt(text)
    return parse_srt(text)

# --------------------------------
# 言語名の解決
# --------------------------------
try:
    import langcodes
    def get_language_name(code: str) -> str:
        try:
            return langcodes.Language.get(code).display_name()
        except Exception:
            return code
except ImportError:
    def get_language_name(code: str) -> str:
        return code

# --------------------------------
# Flask アプリ
# --------------------------------
app = Flask(__name__)
CORS(app)

with app.app_context():
    initialize_proxy_pool()

@app.route("/")
def health():
    return jsonify({"status": "ok"})

@app.route("/captions")
def captions():
    video_id = request.args.get("video_id")
    if not video_id:
        return jsonify({"error": "video_id is required"}), 400

    # プールから次のプロキシを取得
    try:
        proxy_info = get_next_proxy()
    except Exception as e:
        return jsonify({"error": f"Proxy error: {str(e)}"}), 500

    proxy_url = make_proxy(proxy_info)
    # セッション文字列は「アドレス-ポート」
    session_token = f"{proxy_info['proxy_address']}-{proxy_info['port']}"

    # yt-dlp のオプション設定
    ydl_opts = {
        'proxy': proxy_url,
        'quiet': True,
        'no_warnings': True,
        'extract_flat': False,
        'skip_download': True,
        'writesubtitles': False,
        'writeautomaticsub': False,
        'geo_bypass': True,
        'geo_bypass_country': 'US',
        'cookiefile': 'cookiex.txt',
    }
    
    # Deno が利用可能な場合、EJS を有効化
    if DENO_AVAILABLE:
        # 正しいフォーマット: 辞書形式 {runtime: {config}}
        ydl_opts['js_runtimes'] = {
            'deno': {
                'path': DENO_PATH
            }
        }
        print(f"EJS enabled with Deno at: {DENO_PATH}")
    else:
        print("EJS disabled: Deno not available")

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=False)

        manual_subs = info.get('subtitles') or {}
        auto_subs = info.get('automatic_captions') or {}

        captions_list = []

        def add_tracks(sub_dict, is_auto: bool):
            for lang, formats in sub_dict.items():
                if not formats:
                    continue
                fmt = None
                format_type = None
                for ext in ('vtt', 'json3', 'srv1', 'srt'):
                    for f in formats:
                        if f.get('ext') == ext:
                            fmt = f
                            format_type = ext
                            break
                    if fmt:
                        break
                if not fmt:
                    fmt = formats[0]
                    format_type = fmt.get('ext', 'unknown')

                sub_url = fmt['url']
                caption_url = f"/caption?url={urllib.parse.quote(sub_url, safe='')}&session={session_token}&format={format_type}"

                if is_auto:
                    language_display = f"{get_language_name(lang)} (Auto Generated)"
                else:
                    language_display = get_language_name(lang)

                captions_list.append({
                    "id": f"{lang}:{'auto' if is_auto else 'manual'}",
                    "language": language_display,
                    "language_code": lang,
                    "is_generated": is_auto,
                    "is_translatable": False,
                    "caption_url": caption_url
                })

        add_tracks(manual_subs, False)
        add_tracks(auto_subs, True)

        return jsonify({
            "video_id": video_id,
            "session": session_token,
            "captions": captions_list,
            "ejs_enabled": DENO_AVAILABLE
        })

    except Exception as e:
        return jsonify({
            "error": str(e),
            "captions": [],
            "ejs_enabled": DENO_AVAILABLE
        }), 500

@app.route("/caption")
def caption():
    sub_url = request.args.get("url")
    session = request.args.get("session")
    format_hint = request.args.get("format", "")
    gen_yomi = request.args.get("gen_yomi", "0") == "1"

    if not sub_url or not session:
        return jsonify({"error": "url and session are required"}), 400

    # セッション文字列からプロキシ情報を復元
    proxy_info = get_proxy_info_from_session(session)
    if not proxy_info:
        return jsonify({"error": f"Invalid session: proxy not found for {session}"}), 400

    proxy_url = make_proxy(proxy_info)
    decoded_url = urllib.parse.unquote(sub_url)

    # 字幕ファイルをプロキシ経由で取得
    try:
        resp = requests.get(
            decoded_url,
            proxies={"http": proxy_url, "https": proxy_url},
            timeout=30,
            headers={
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            }
        )
        resp.raise_for_status()
    except Exception as e:
        return jsonify({"error": f"Failed to fetch subtitle: {str(e)}"}), 502

    raw_text = resp.text
    segments = parse_subtitle(raw_text, format_hint)

    # 言語コードの抽出
    parsed = urlparse(decoded_url)
    qs = parse_qs(parsed.query)
    lang_code = qs.get('lang', [None])[0] or qs.get('tl', [None])[0] or "unknown"

    result_transcript = []
    for seg in segments:
        row = {
            "text": seg["text"],
            "start": seg["start"],
            "duration": seg["duration"]
        }
        if gen_yomi:
            row["yomi"] = text_to_yomi(seg["text"])
        result_transcript.append(row)

    language_display = get_language_name(lang_code) if lang_code != "unknown" else lang_code

    return jsonify({
        "language": language_display,
        "language_code": lang_code,
        "is_generated": False,
        "transcript": result_transcript
    })

if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 5000)),
        debug=True
    )
