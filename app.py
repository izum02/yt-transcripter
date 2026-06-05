from flask import Flask, jsonify, request
from youtube_transcript_api import YouTubeTranscriptApi
import requests

app = Flask(__name__)


@app.route("/captions")
def get_caption_list():
    video_id = request.args.get("video_id")

    if not video_id:
        return jsonify({
            "error": "video_id is required"
        }), 400

    try:
        transcript_list = YouTubeTranscriptApi.list_transcripts(video_id)

        captions = []

        for transcript in transcript_list:
            captions.append({
                "language": transcript.language,
                "language_code": transcript.language_code,
                "is_generated": transcript.is_generated,
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
def get_caption():
    video_id = request.args.get("video_id")
    lang = request.args.get("lang")

    if not video_id:
        return jsonify({
            "error": "video_id is required"
        }), 400

    try:
        transcript = YouTubeTranscriptApi.get_transcript(
            video_id,
            languages=[lang] if lang else None
        )

        return jsonify({
            "video_id": video_id,
            "language": lang,
            "transcript": transcript
        })

    except Exception as e:
        return jsonify({
            "error": str(e)
        }), 500


@app.route("/caption_from_url")
def caption_from_url():
    url = request.args.get("url")

    if not url:
        return jsonify({
            "error": "url is required"
        }), 400

    try:
        response = requests.get(url, timeout=30)

        return response.text, 200, {
            "Content-Type": response.headers.get(
                "Content-Type",
                "text/xml"
            )
        }

    except Exception as e:
        return jsonify({
            "error": str(e)
        }), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))

    app.run(
        host="0.0.0.0",
        port=port,
        debug=False
    )
