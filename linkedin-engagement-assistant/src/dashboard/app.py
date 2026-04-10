"""
Flask review dashboard for LinkedIn Engagement Assistant.
Provides a web interface for approving/rejecting messages and managing automation.
"""

import json
import os
from datetime import datetime, timedelta
from functools import wraps
from pathlib import Path

from flask import Flask, render_template, request, jsonify, redirect, url_for, session

# Resolve paths relative to this file
_BASE_DIR = Path(__file__).resolve().parent.parent.parent
_CONFIG_PATH = _BASE_DIR / "config" / "settings.json"
_EXCLUSION_PATH = _BASE_DIR / "config" / "exclusion_list.json"
_STATE_PATH = _BASE_DIR / "config" / "global_state.json"

# Add src to path for imports
import sys
sys.path.insert(0, str(_BASE_DIR / "src"))

from utils.database import Database

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "change-me-in-production")

# Initialize database
db = Database(str(_BASE_DIR / "data" / "linkedin_assistant.db"))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_settings() -> dict:
    try:
        with open(_CONFIG_PATH, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"operating_mode": "review_first"}


def _load_state() -> dict:
    try:
        with open(_STATE_PATH, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"paused": False}


def _save_state(state: dict) -> None:
    _STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(_STATE_PATH, "w") as f:
        json.dump(state, f, indent=2)


def _load_exclusions() -> list:
    try:
        with open(_EXCLUSION_PATH, "r") as f:
            data = json.load(f)
            return data.get("excluded_individuals", [])
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def require_auth(f):
    """Simple password-based auth guard."""
    @wraps(f)
    def decorated(*args, **kwargs):
        pwd = os.getenv("DASHBOARD_PASSWORD")
        if not pwd:
            return f(*args, **kwargs)
        if session.get("authenticated"):
            return f(*args, **kwargs)
        if request.method == "POST" and request.form.get("password") == pwd:
            session["authenticated"] = True
            return redirect(request.referrer or url_for("dashboard"))
        return render_template("dashboard.html", login_required=True), 401
    return decorated


def _get_stats() -> dict:
    """Compute live stats from the database."""
    summary = db.get_stats_summary(days=1)
    pending = db.get_pending_messages(limit=9999)
    flagged = db.get_messages_for_review(limit=9999)
    return {
        "messages_sent_today": db.get_daily_send_count(),
        "pending_review": len(pending),
        "blocked": summary.get("blocked", 0),
        "flagged": len(flagged),
    }


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/")
@require_auth
def dashboard():
    """Dashboard home."""
    stats = _get_stats()
    settings = _load_settings()
    state = _load_state()

    # Combine pending + flagged for the review queue
    queue = db.get_pending_messages(limit=50) + db.get_messages_for_review(limit=50)
    logs = db.get_audit_logs(limit=30)

    return render_template(
        "dashboard.html",
        stats=stats,
        queue=queue,
        logs=logs,
        mode=settings.get("operating_mode", "review_first"),
        is_paused=state.get("paused", False),
        exclusions=_load_exclusions(),
        current_page="home",
    )


@app.route("/queue")
@require_auth
def review_queue():
    """Review queue page."""
    queue = db.get_pending_messages(limit=100) + db.get_messages_for_review(limit=100)
    return render_template(
        "dashboard.html",
        stats=_get_stats(),
        queue=queue,
        logs=[],
        mode=_load_settings().get("operating_mode", "review_first"),
        is_paused=_load_state().get("paused", False),
        exclusions=_load_exclusions(),
        current_page="queue",
    )


@app.route("/approve/<int:message_id>", methods=["POST"])
@require_auth
def approve_message(message_id: int):
    """Approve a single message."""
    try:
        db.update_message_status(message_id, "approved")
        db.log_audit("message_approved", details=json.dumps({"message_id": message_id}))
        return jsonify({"success": True}), 200
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 400


@app.route("/reject/<int:message_id>", methods=["POST"])
@require_auth
def reject_message(message_id: int):
    """Reject a single message."""
    try:
        reason = request.form.get("reason", "Rejected via dashboard")
        db.update_message_status(message_id, "rejected")
        db.log_audit("message_rejected", details=json.dumps({"message_id": message_id, "reason": reason}))
        return jsonify({"success": True}), 200
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 400


@app.route("/approve-all", methods=["POST"])
@require_auth
def approve_all():
    """Approve every pending message."""
    try:
        pending = db.get_pending_messages(limit=9999)
        count = 0
        for msg in pending:
            db.update_message_status(msg["id"], "approved")
            count += 1
        db.log_audit("batch_approve", details=json.dumps({"count": count}))
        return jsonify({"success": True, "count": count}), 200
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 400


@app.route("/log")
@require_auth
def audit_log():
    """Audit log viewer."""
    action_filter = request.args.get("action") or None
    date_filter = request.args.get("date") or None
    logs = db.get_audit_logs(limit=200, action_filter=action_filter, date_filter=date_filter)
    return render_template(
        "dashboard.html",
        stats=_get_stats(),
        queue=[],
        logs=logs,
        mode=_load_settings().get("operating_mode", "review_first"),
        is_paused=_load_state().get("paused", False),
        exclusions=_load_exclusions(),
        current_page="log",
    )


@app.route("/stats")
@require_auth
def stats_page():
    """Weekly stats page."""
    summary = db.get_stats_summary(days=7)
    return render_template(
        "dashboard.html",
        stats=_get_stats(),
        weekly_summary=summary,
        queue=[],
        logs=[],
        mode=_load_settings().get("operating_mode", "review_first"),
        is_paused=_load_state().get("paused", False),
        exclusions=_load_exclusions(),
        current_page="stats",
    )


@app.route("/pause", methods=["POST"])
@require_auth
def pause_automation():
    """Pause all automation."""
    state = _load_state()
    state["paused"] = True
    state["paused_at"] = datetime.now().isoformat()
    _save_state(state)
    db.log_audit("automation_paused", details="Paused via dashboard")
    return jsonify({"success": True}), 200


@app.route("/resume", methods=["POST"])
@require_auth
def resume_automation():
    """Resume automation."""
    state = _load_state()
    state["paused"] = False
    state["resumed_at"] = datetime.now().isoformat()
    _save_state(state)
    db.log_audit("automation_resumed", details="Resumed via dashboard")
    return jsonify({"success": True}), 200


@app.route("/exclusions")
@require_auth
def view_exclusions():
    """View the hard exclusion list."""
    return render_template(
        "dashboard.html",
        stats=_get_stats(),
        queue=[],
        logs=[],
        exclusions=_load_exclusions(),
        mode=_load_settings().get("operating_mode", "review_first"),
        is_paused=_load_state().get("paused", False),
        current_page="exclusions",
    )


@app.route("/settings", methods=["GET", "POST"])
@require_auth
def settings_page():
    """View / update operating mode."""
    if request.method == "POST":
        mode = request.form.get("operating_mode", "review_first")
        if mode not in ("review_first", "auto_send", "digest"):
            return jsonify({"success": False, "error": "Invalid mode"}), 400
        cfg = _load_settings()
        cfg["operating_mode"] = mode
        _CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(_CONFIG_PATH, "w") as f:
            json.dump(cfg, f, indent=2)
        db.log_audit("settings_changed", details=json.dumps({"operating_mode": mode}))
        return jsonify({"success": True}), 200

    return render_template(
        "dashboard.html",
        stats=_get_stats(),
        queue=[],
        logs=[],
        exclusions=_load_exclusions(),
        settings_data=_load_settings(),
        mode=_load_settings().get("operating_mode", "review_first"),
        is_paused=_load_state().get("paused", False),
        current_page="settings",
    )


# ---------------------------------------------------------------------------
# Error handlers
# ---------------------------------------------------------------------------

@app.errorhandler(404)
def not_found(_):
    return jsonify({"error": "Not found"}), 404


@app.errorhandler(500)
def server_error(_):
    return jsonify({"error": "Internal server error"}), 500


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    debug = os.getenv("FLASK_DEBUG", "false").lower() == "true"
    app.run(
        host=os.getenv("FLASK_HOST", "127.0.0.1"),
        port=int(os.getenv("DASHBOARD_PORT", "5000")),
        debug=debug,
    )
