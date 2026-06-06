from sudachipy import Dictionary

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


from flask import Flask, jsonify, request
from flask_cors import CORS
from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api.proxies import WebshareProxyConfig
import os

app = Flask(__name__)
CORS(app)

ytt_api = YouTubeTranscriptApi(
    proxy_config=WebshareProxyConfig(
        proxy_username="hsdlmspx",
        proxy_password="w1bhmbj3ghmr",
    )
)


@app.route("/")
def health():
    return jsonify({"status": "ok"})


@app.route("/captions")
def captions():
    video_id = request.args.get("video_id")

    if not video_id:
        return jsonify({"error": "video_id is required"}), 400

    try:
        transcript_list = ytt_api.list(video_id)

        captions = []

        for transcript in transcript_list:
            # ★ 修正②：一意IDを作成
            track_id = f"{transcript.language_code}:{'auto' if transcript.is_generated else 'manual'}"

            captions.append({
                "id": track_id,
                "language": transcript.language,
                "language_code": transcript.language_code,
                "is_generated": transcript.is_generated,
                "is_translatable": transcript.is_translatable,
                "caption_url": (
                    f"/caption?"
                    f"video_id={video_id}"
                    f"&lang={transcript.language_code}"
                    f"&gen={'1' if transcript.is_generated else '0'}"
                )
            })

        return jsonify({
            "video_id": video_id,
            "captions": captions
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/caption")
def caption():
    video_id = request.args.get("video_id")
    lang = request.args.get("lang", "en")

    # auto/manual識別
    gen = request.args.get("gen", "0") == "1"

    if not video_id:
        return jsonify({"error": "video_id is required"}), 400

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

            # 読みがな生成
            if request.args.get("gen_yomi", "0") == "1":
                row["yomi"] = text_to_yomi(item["text"])

            result.append(row)

        return jsonify({
            "video_id": transcript.video_id,
            "language": transcript.language,
            "language_code": transcript.language_code,
            "is_generated": transcript.is_generated,
            "requested_gen": gen,
            "transcript": result
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 5000))
    )
