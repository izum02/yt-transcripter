import os
import random
import re
import json
import urllib.parse
from urllib.parse import urlparse, parse_qs

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
# プロキシ設定
# --------------------------------
PROXY_USERNAME = os.environ.get("PROXY_USERNAME", "hsdlmspx")
PROXY_PASSWORD = os.environ.get("PROXY_PASSWORD", "w1bhmbj3ghmr")
PROXY_HOST = os.environ.get("PROXY_HOST", "38.154.203.95")
PROXY_PORT = os.environ.get("PROXY_PORT", "5863")

def make_proxy(session_id: int = None) -> str:
    if session_id:
        # セッションID付きの場合（ドキュメントが何故か効かず）
        return f"http://{PROXY_USERNAME}:{PROXY_PASSWORD}@{PROXY_HOST}:{PROXY_PORT}"
    else:
        return f"http://{PROXY_USERNAME}:{PROXY_PASSWORD}@{PROXY_HOST}:{PROXY_PORT}"

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
    # WEBVTTヘッダーを削除
    text = re.sub(r'^WEBVTT.*?\n', '', text, flags=re.MULTILINE)
    
    # 空行やスタイルブロックなどを削除
    lines = text.split('\n')
    segments = []
    i = 0
    
    while i < len(lines):
        line = lines[i].strip()
        
        # 空行はスキップ
        if not line:
            i += 1
            continue
        
        # タイムスタンプ行を検出（--> を含む）
        if '-->' in line:
            # タイムスタンプをパース（align:start position:0% などの属性を無視）
            time_match = re.match(r'(\d{1,2}:\d{2}:\d{2}[.,]\d{3})\s*-->\s*(\d{1,2}:\d{2}:\d{2}[.,]\d{3})', line)
            if time_match:
                start_str = time_match.group(1).replace(',', '.')
                end_str = time_match.group(2).replace(',', '.')
                start = time_to_seconds(start_str)
                end = time_to_seconds(end_str)
                
                # 次の行から空行までが字幕テキスト
                i += 1
                text_lines = []
                while i < len(lines):
                    current_line = lines[i].strip()
                    if not current_line:
                        break
                    # HTMLタグを削除（<c>タグなど）
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
    
    # JSON3の構造: {"events": [{"tStartMs": 1000, "dDurationMs": 2000, "segs": [{"utf8": "text"}]}]}
    events = data.get('events', [])
    
    for event in events:
        start_ms = event.get('tStartMs', 0)
        duration_ms = event.get('dDurationMs', 0)
        
        # テキストを抽出
        segs = event.get('segs', [])
        text_parts = []
        for seg in segs:
            if 'utf8' in seg:
                text_parts.append(seg['utf8'])
        
        text = ''.join(text_parts).strip()
        
        # 改行や特殊文字をクリーンアップ
        text = text.replace('\n', ' ')
        text = re.sub(r'\s+', ' ', text)
        
        if text:
            segments.append({
                'text': text,
                'start': start_ms / 1000.0,  # ミリ秒→秒
                'duration': duration_ms / 1000.0
            })
    
    return segments

def time_to_seconds(ts: str) -> float:
    """HH:MM:SS.mmm → 秒数"""
    parts = ts.split(':')
    h = int(parts[0])
    m = int(parts[1])
    s = float(parts[2])
    return h * 3600 + m * 60 + s

def parse_subtitle(text: str, format_hint: str = None) -> list:
    """字幕の形式を自動判定してパース"""
    # JSON3の場合（format_hintがjson3またはテキストがJSONとしてパース可能）
    if format_hint == 'json3':
        try:
            data = json.loads(text)
            if 'events' in data:
                return parse_json3(data)
        except:
            pass
    
    # VTTの場合
    if format_hint == 'vtt' or ('WEBVTT' in text[:100]):
        return parse_vtt(text)
    
    # SRTの場合（デフォルト）
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

@app.route("/")
def health():
    return jsonify({"status": "ok"})

@app.route("/captions")
def captions():
    video_id = request.args.get("video_id")
    if not video_id:
        return jsonify({"error": "video_id is required"}), 400

    # セッションIDを生成
    session_id = random.randint(1, 100000)
    proxy = make_proxy(session_id)

    ydl_opts = {
        'proxy': proxy,
        'quiet': True,
        'no_warnings': True,
        'extract_flat': False,
        'skip_download': True,
        'writesubtitles': False,
        'writeautomaticsub': False,
        'geo_bypass': True,
        'geo_bypass_country': 'US',
    }

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
                # 利用可能な字幕形式を選ぶ（優先順位: vtt, json3, srv1, srt）
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
                caption_url = f"/caption?url={urllib.parse.quote(sub_url, safe='')}&session={session_id}&format={format_type}"
                
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
            "session": session_id,
            "captions": captions_list
        })

    except Exception as e:
        return jsonify({
            "error": str(e),
            "captions": []
        }), 500

@app.route("/caption")
def caption():
    sub_url = request.args.get("url")
    session = request.args.get("session")
    format_hint = request.args.get("format", "")
    gen_yomi = request.args.get("gen_yomi", "0") == "1"

    if not sub_url or not session:
        return jsonify({"error": "url and session are required"}), 400

    # URLデコード
    decoded_url = urllib.parse.unquote(sub_url)

    # プロキシを構築
    try:
        session_id = int(session)
    except ValueError:
        return jsonify({"error": "session must be an integer"}), 400
    
    proxy = make_proxy(session_id)

    # 字幕ファイルをプロキシ経由で取得
    try:
        resp = requests.get(
            decoded_url,
            proxies={"http": proxy, "https": proxy},
            timeout=30,
            headers={
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            }
        )
        resp.raise_for_status()
    except Exception as e:
        return jsonify({"error": f"Failed to fetch subtitle: {str(e)}"}), 502

    raw_text = resp.text
    
    # 字幕をパース（書式は適用せず、テキストのみ抽出）
    segments = parse_subtitle(raw_text, format_hint)

    # 字幕URLから言語コードを抽出
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
