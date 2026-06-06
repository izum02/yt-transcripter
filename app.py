from sudachipy import Dictionary

tokenizer = Dictionary().create()

# 空白は無視
IGNORE_SPACES = {" ", "　"}

# そのまま残す記号
KEEP_SYMBOLS = set('?!"#$%&()-=~^@*+;:[{]}/\\,.')


def text_to_yomi(text: str) -> str:
    result = []

    for m in tokenizer.tokenize(text):
        surface = m.surface()

        # スペースは削除
        if surface in IGNORE_SPACES:
            continue

        # 記号はそのまま残す
        if surface in KEEP_SYMBOLS:
            result.append(surface)
            continue

        reading = m.reading_form()

        # Sudachiが記号扱いしたものは基本スキップ
        pos = m.part_of_speech()

        if (
            reading == "キゴウ"
            and pos[0] == "補助記号"
            and surface not in {"記号", "キゴウ", "きごう", "揮毫", "几号", "騎劫", "揮ごう"}
        ):
            continue

        result.append(reading)

    return "".join(result)

from flask import Flask, jsonify, request
from youtube_transcript_api import YouTubeTranscriptApi
import os

app = Flask(__name__)

ytt_api = YouTubeTranscriptApi(
    proxy_config=WebshareProxyConfig(
        proxy_username="hsdlmspx",
        proxy_password="w1bhmbj3ghmr",
    )
    
                              )


@app.route("/")
def health():
    return jsonify({
        "status": "ok"
    })


@app.route("/captions")
def captions():
    video_id = request.args.get("video_id")

    if not video_id:
        return jsonify({
            "error": "video_id is required"
        }), 400

    try:
        transcript_list = ytt_api.list(video_id)

        captions = []

        for transcript in transcript_list:
            captions.append({
                "language": transcript.language,
                "language_code": transcript.language_code,
                "is_generated": transcript.is_generated,
                "is_translatable": transcript.is_translatable,
                "caption_url": (
                    f"/caption?"
                    f"video_id={video_id}"
                    f"&lang={transcript.language_code}"
                )
            })

        return jsonify({
            "video_id": video_id,
            "captions": captions
        })

    except Exception as e:
        return jsonify({
            "error": str(e)
        }), 500


@app.route("/caption")
def caption():
    video_id = request.args.get("video_id")
    lang = request.args.get("lang", "en")

    # ★追加：0 or 1
    gen_yomi = request.args.get("gen_yomi", "0") == "1"

    if not video_id:
        return jsonify({
            "error": "video_id is required"
        }), 400

    try:
        transcript = ytt_api.fetch(
            video_id,
            languages=[lang]
        )

        result = []

        for item in transcript.to_raw_data():
            row = {
                "text": item["text"],
                "start": item["start"],
                "duration": item["duration"]
            }

            # ★追加：読みがな生成
            if gen_yomi:
                row["yomi"] = text_to_yomi(item["text"])

            result.append(row)

        return jsonify({
            "video_id": transcript.video_id,
            "language": transcript.language,
            "language_code": transcript.language_code,
            "is_generated": transcript.is_generated,
            "gen_yomi": gen_yomi,
            "transcript": result
        })

    except Exception as e:
        return jsonify({
            "error": str(e)
        }), 500


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 5000))
    )
