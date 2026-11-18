import os

from dotenv import load_dotenv
from flask import Flask, jsonify, request
from postgrest import APIError
from supabase import Client, create_client

load_dotenv()

app = Flask(__name__)

supabase_url = os.environ.get("SUPABASE_URL")
supabase_key = os.environ.get("SUPABASE_PUBLISHABLE_KEY")
if not supabase_url or not supabase_key:
    raise RuntimeError("SUPABASE_URL and SUPABASE_PUBLISHABLE_KEY must be set")

supabase: Client = create_client(supabase_url, supabase_key)


@app.after_request
def allow_cross_origin_requests(response):
    """Allow browser clients from any origin to call this public API."""
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
    return response


def api_error(message, status):
    return jsonify({"error": message}), status


def database_error(error):
    app.logger.exception("Supabase request failed: %s", error)
    return api_error("Database request failed", 500)


@app.get("/health")
def health():
    return jsonify({"status": "ok"}), 200


@app.get("/")
def index():
    return jsonify({"message": "Events API", "endpoints": [
        "GET /api/events",
        "GET /api/events/<id>",
        "POST /api/registrations",
        "GET /api/me/registrations?user_id=<uuid>",
        "GET /api/events/<id>/registrations",
    ]})


@app.get("/api/events")
def list_events():
    try:
        result = supabase.table("events").select("*").execute()
        return jsonify(result.data), 200
    except APIError as error:
        return database_error(error)


@app.get("/api/events/<event_id>")
def get_event(event_id):
    try:
        result = supabase.table("events").select("*").eq("id", event_id).limit(1).execute()
        if not result.data:
            return api_error("Event not found", 404)
        return jsonify(result.data[0]), 200
    except APIError as error:
        return database_error(error)


@app.post("/api/registrations")
def create_registration():
    payload = request.get_json(silent=True) or {}
    user_id = payload.get("user_id")
    event_id = payload.get("event_id")
    if not user_id or not event_id:
        return api_error("user_id and event_id are required", 400)

    try:
        event_result = supabase.table("events").select("id, capacity").eq("id", event_id).limit(1).execute()
        if not event_result.data:
            return api_error("Event not found", 404)
        event = event_result.data[0]

        duplicate = (supabase.table("registrations").select("id")
                     .eq("user_id", user_id).eq("event_id", event_id).limit(1).execute())
        if duplicate.data:
            return api_error("User is already registered for this event", 409)

        if event.get("capacity") is not None:
            current = supabase.table("registrations").select("id", count="exact").eq("event_id", event_id).execute()
            if current.count is not None and current.count >= event["capacity"]:
                return api_error("Event is full", 409)

        created = supabase.table("registrations").insert({
            "user_id": user_id,
            "event_id": event_id,
        }).execute()
        return jsonify(created.data[0] if created.data else {"user_id": user_id, "event_id": event_id}), 201
    except APIError as error:
        # A unique constraint on (user_id, event_id) also protects against
        # simultaneous duplicate requests.
        if getattr(error, "code", None) == "23505":
            return api_error("User is already registered for this event", 409)
        return database_error(error)


@app.get("/api/me/registrations")
def my_registrations():
    # In this unauthenticated API the caller identifies themselves with user_id.
    user_id = request.args.get("user_id")
    if not user_id:
        return api_error("user_id query parameter is required", 400)
    try:
        result = (supabase.table("registrations")
                  .select("*, events(*)").eq("user_id", user_id).execute())
        return jsonify(result.data), 200
    except APIError as error:
        return database_error(error)


@app.get("/api/events/<event_id>/registrations")
def event_registrations(event_id):
    try:
        event_result = (supabase.table("events").select("id")
                        .eq("id", event_id).limit(1).execute())
        if not event_result.data:
            return api_error("Event not found", 404)
        attendees = (supabase.table("registrations")
                     .select("*, users(id, email, role)")
                     .eq("event_id", event_id).execute())
        return jsonify(attendees.data), 200
    except APIError as error:
        return database_error(error)


if __name__ == "__main__":
    app.run(debug=os.environ.get("FLASK_DEBUG", "false").lower() == "true")
