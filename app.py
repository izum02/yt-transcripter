import os
import random
import re
import urllib.parse
from urllib.parse import urlparse, parse_qs

import requests
import yt_dlp
from flask import Flask, jsonify, request
from flask_cors import CORS
from sudachipy import Dictionary

# --------------------------------
# テキスト読み付与（変更なし）
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
# プロキシ設定（環境変数から取得）
# --------------------------------
PROXY_USERNAME = os.environ.get("PROXY_USERNAME", "hsdlmspx")
PROXY_PASSWORD = os.environ.get("PROXY_PASSWORD", "w1bhmbj3ghmr")
PROXY_ENDPOINT = os.environ.get("PROXY_ENDPOINT", "pr.webshare.io")  # 例: your.endpoint.com
PROXY_PORT = os.environ.get("PROXY_PORT", "30000")

def make_proxy(session_id: int) -> str:
    """セッションID付きのプロキシURLを生成"""
    return f"http://{PROXY_USERNAME}-{session_id}:{PROXY_PASSWORD}@{PROXY_ENDPOINT}:{PROXY_PORT}"

# --------------------------------
# 字幕パース (SRT / VTT)
# --------------------------------
def parse_srt(text: str):
    """SRT形式のテキストをセグメントのリストに変換（開始秒、テキスト、終了秒）"""
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
    """WebVTT形式のテキストをセグメントのリストに変換（開始秒、テキスト、終了秒）"""
    # WEBVTTヘッダとスタイルブロックを除去
    text = re.sub(r'^WEBVTT.*\n', '', text)
    text = re.sub(r'STYLE\n.*?\n\n', '', text, flags=re.DOTALL)
    pattern = re.compile(
        r'(\d{1,2}:\d{2}:\d{2}[.,]\d{3}) --> (\d{1,2}:\d{2}:\d{2}[.,]\d{3}).*?\n(.*?)(?=\n\n|\Z)',
        re.DOTALL
    )
    segments = []
    for m in pattern.finditer(text):
        start_str = m.group(1).replace(',', '.')
        end_str = m.group(2).replace(',', '.')
        start = time_to_seconds(start_str)
        end = time_to_seconds(end_str)
        txt = m.group(3).strip().replace('\n', ' ')
        # VTTではタグを除去（簡易的に）
        txt = re.sub(r'<[^>]+>', '', txt)
        segments.append({
            'text': txt,
            'start': start,
            'duration': end - start
        })
    return segments

def time_to_seconds(ts: str) -> float:
    """HH:MM:SS.mmm → 秒数"""
    parts = ts.split(':')
    h = int(parts[0])
    m = int(parts[1])
    s = float(parts[2])
    return h * 3600 + m * 60 + s

# --------------------------------
# 言語名の解決（できれば）
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

    # セッションIDを生成（1～100000）
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
                # 利用可能な字幕形式を選ぶ（例：vtt → srv1 → srt → 先頭のもの）
                fmt = None
                for ext in ('vtt', 'srv1', 'srt', 'json3'):
                    for f in formats:
                        if f.get('ext') == ext:
                            fmt = f
                            break
                    if fmt:
                        break
                if not fmt:
                    fmt = formats[0]  # フォールバック
                sub_url = fmt['url']
                caption_url = f"/caption?url={urllib.parse.quote(sub_url, safe='')}&session={session_id}"
                captions_list.append({
                    "id": f"{lang}:{'auto' if is_auto else 'manual'}",
                    "language": get_language_name(lang),
                    "language_code": lang,
                    "is_generated": is_auto,
                    "is_translatable": False,  # yt-dlp からは取得できないため一律 false
                    "caption_url": caption_url
                })

        add_tracks(manual_subs, False)
        add_tracks(auto_subs, True)

        return jsonify({
            "video_id": video_id,
            "session": session_id,          # 今回のプロキシセッションID
            "captions": captions_list
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/caption")
def caption():
    sub_url = request.args.get("url")
    session = request.args.get("session")
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
            timeout=30
        )
        resp.raise_for_status()
    except Exception as e:
        return jsonify({"error": f"Failed to fetch subtitle: {str(e)}"}), 502

    raw_text = resp.text
    # SRT / VTT 判定（拡張子やコンテンツから推測）
    if 'WEBVTT' in raw_text[:100]:
        segments = parse_vtt(raw_text)
    else:
        segments = parse_srt(raw_text)

    # 字幕URLから video_id と言語を抽出（yt-dlpが生成する典型的なURL）
    parsed = urlparse(decoded_url)
    qs = parse_qs(parsed.query)
    video_id = qs.get('v', [None])[0] or "unknown"
    lang_code = qs.get('lang', [None])[0] or "unknown"

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

    return jsonify({
        "video_id": video_id,
        "language": get_language_name(lang_code) if lang_code != "unknown" else lang_code,
        "language_code": lang_code,
        "is_generated": False,  # 個別字幕リクエストでは判別不可
        "transcript": result_transcript
    })

if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 5000))
    )
