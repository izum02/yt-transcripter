from flask import Flask, jsonify, request
from youtube_transcript_api import YouTubeTranscriptApi
import os

app = Flask(__name__)

ytt_api = YouTubeTranscriptApi()


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

    if not video_id:
        return jsonify({
            "error": "video_id is required"
        }), 400

    try:
        transcript = ytt_api.fetch(
            video_id,
            languages=[lang]
        )

        return jsonify({
            "video_id": transcript.video_id,
            "language": transcript.language,
            "language_code": transcript.language_code,
            "is_generated": transcript.is_generated,
            "transcript": transcript.to_raw_data()
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
