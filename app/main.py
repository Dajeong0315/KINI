from __future__ import annotations

import os
import tempfile
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request, send_from_directory
from werkzeug.utils import secure_filename

from app.services.errors import ServiceError
from app.services.fact_responder import fact_answer
from app.services.food_safety import nutrition_for_food, recipes_for_inventory
from app.services.inventory import inventory_context, load_inventory, matching_items
from app.services.local_stt import transcribe_audio
from app.services.local_tts import OUTPUT_DIR, synthesize_speech
from app.services.ollama import ask_ollama, configured_model
from app.services.response_policy import FALLBACK_REPLY, enforce_response_policy


PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_ROOT / ".env")

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 25 * 1024 * 1024
# Keep the page template in sync with static assets during local UI edits.
app.config["TEMPLATES_AUTO_RELOAD"] = True
ALLOWED_AUDIO_EXTENSIONS = {".mp3", ".wav", ".m4a", ".webm", ".ogg", ".mp4"}


def _answer_question(question: str) -> dict[str, object]:
    question = question.strip()
    if not question:
        raise ServiceError("질문을 입력해 주세요.")
    if len(question) > 500:
        raise ServiceError("질문은 500자 이내로 입력해 주세요.")

    inventory = load_inventory()
    confirmed = fact_answer(question, inventory)
    if confirmed:
        if not confirmed.get("llm_rephrase"):
            return {"question": question, "model": None, **confirmed}
        try:
            raw_answer, _metadata = ask_ollama(
                question,
                inventory_context(confirmed["selected_items"]),
                confirmed.get("decision_facts"),
            )
            answer = enforce_response_policy(
                raw_answer,
                [item["name"] for item in confirmed["selected_items"]],
                question,
                required_phrases=confirmed.get("required_phrases"),
            )
        except ServiceError:
            answer = confirmed["answer"]
        return {
            "question": question,
            "answer": answer if answer != FALLBACK_REPLY else confirmed["answer"],
            "selected_items": confirmed["selected_items"],
            "model": configured_model(),
            "answer_source": confirmed["answer_source"],
        }

    selected_items = matching_items(question, inventory)
    raw_answer, _metadata = ask_ollama(question, inventory_context(selected_items))
    answer = enforce_response_policy(
        raw_answer, [item["name"] for item in selected_items], question
    )
    return {
        "question": question,
        "answer": answer,
        "selected_items": selected_items,
        "model": configured_model(),
        "answer_source": "ollama",
    }


@app.get("/")
@app.get("/fridge")
@app.get("/mobile")
def index():
    device_mode = {"/fridge": "fridge", "/mobile": "mobile"}.get(request.path, "auto")
    return render_template("index.html", model=configured_model(), device_mode=device_mode)


@app.get("/api/inventory")
def get_inventory():
    try:
        return jsonify(load_inventory())
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 500


@app.get("/api/recipes")
def get_recipes():
    try:
        inventory = load_inventory()
        return jsonify({"recipes": recipes_for_inventory(inventory["items"]), "source": "COOKRCP01"})
    except (ServiceError, ValueError) as exc:
        return jsonify({"error": str(exc)}), 400


@app.get("/api/nutrition")
def get_nutrition():
    food_name = request.args.get("food_name", "").strip()
    if not food_name:
        return jsonify({"error": "조회할 식품명을 선택해 주세요."}), 400
    try:
        nutrition = nutrition_for_food(food_name)
        if nutrition is None:
            return jsonify({"error": "일치하는 영양성분 데이터를 찾지 못했습니다."}), 404
        return jsonify(nutrition)
    except ServiceError as exc:
        return jsonify({"error": str(exc)}), 400


@app.post("/api/chat")
def chat():
    payload = request.get_json(silent=True) or {}
    try:
        return jsonify(_answer_question(str(payload.get("question", ""))))
    except (ServiceError, ValueError) as exc:
        return jsonify({"error": str(exc)}), 400


@app.post("/api/voice")
def voice_chat():
    uploaded = request.files.get("audio")
    if not uploaded or not uploaded.filename:
        return jsonify({"error": "음성 파일을 선택해 주세요."}), 400
    suffix = Path(uploaded.filename).suffix.lower()
    if suffix not in ALLOWED_AUDIO_EXTENSIONS:
        return jsonify({"error": "지원하지 않는 파일 형식입니다."}), 400

    filename = secure_filename(uploaded.filename) or f"audio{suffix}"
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as temp_file:
            temp_path = Path(temp_file.name)
            uploaded.save(temp_file)
        transcript = transcribe_audio(temp_path)
        result = _answer_question(transcript)
        result["transcript"] = transcript
        return jsonify(result)
    except (ServiceError, ValueError) as exc:
        return jsonify({"error": str(exc)}), 400
    finally:
        if temp_path and temp_path.exists():
            temp_path.unlink()


@app.post("/api/tts")
def text_to_speech():
    payload = request.get_json(silent=True) or {}
    text = str(payload.get("text", "")).strip()
    if not text:
        return jsonify({"error": "음성으로 만들 답변이 없습니다."}), 400
    try:
        filename = synthesize_speech(text)
        return jsonify({"audio_url": f"/audio/{filename}"})
    except ServiceError as exc:
        return jsonify({"error": str(exc)}), 400


@app.get("/audio/<path:filename>")
def get_audio(filename: str):
    mime_types = {".wav": "audio/wav", ".mp3": "audio/mpeg"}
    mimetype = mime_types.get(Path(filename).suffix.lower(), "application/octet-stream")
    return send_from_directory(OUTPUT_DIR, filename, mimetype=mimetype, as_attachment=False)


@app.errorhandler(413)
def file_too_large(_error):
    return jsonify({"error": "음성 파일은 25MB 이하여야 합니다."}), 413


from app.services.recommendation_api import api as recommendation_api
app.register_blueprint(recommendation_api)
from app.services.data_status_api import api as data_status_api
app.register_blueprint(data_status_api)
from app.services.local_auth_api import api as local_auth_api
app.register_blueprint(local_auth_api)
from app.services.family_api import api as family_api
app.register_blueprint(family_api)
from app.services.meal_reporting_api import api as meal_reports_api
app.register_blueprint(meal_reports_api)

if os.getenv('KINI_B_MEAL_FEEDBACK_ENABLED', '0') == '1':
    from app.meal_feedback_app import load_feedback_options
    from app.services.meal_feedback import MealFeedbackService
    from app.services.meal_feedback_api import create_meal_feedback_blueprint
    from app.services.meal_feedback_store import SqlFeedbackStore
    app.register_blueprint(create_meal_feedback_blueprint(MealFeedbackService(
        SqlFeedbackStore().transaction,
        options=load_feedback_options(os.environ['KINI_MEAL_FEEDBACK_OPTIONS_FILE']),
    )))


if __name__ == "__main__":
    debug = os.getenv("FLASK_DEBUG") == "1"
    host = os.getenv("APP_HOST", "0.0.0.0")
    port = int(os.getenv("APP_PORT", "5000"))
    app.run(host=host, port=port, debug=debug)
