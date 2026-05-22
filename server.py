#!/usr/bin/env python3
"""
Shortcut Server — 纯事件存储 + 统计查询

接收 iOS Shortcut POST → 存 events.json + JSONL
供智能体查询与分析
"""

import json
import os
import re
import time
import uuid
from datetime import datetime, date, timedelta, timezone
from pathlib import Path

from flask import Flask, request, jsonify

CST = timezone(timedelta(hours=8))
BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)
EVENTS_FILE = DATA_DIR / "events.json"
EVENTS_JSONL_ROOT = DATA_DIR / "events"
EVENTS_JSONL_ROOT.mkdir(exist_ok=True, parents=True)

API_KEY = os.getenv("API_KEY", "").strip()
LEGACY_DEVICE_ID = "legacy"
ALLOWED_EVENT_TYPES = {
    "scheduled",
    "geofence",
    "power",
    "connectivity",
    "focus_change",
    "sound_recognition",
    "app_opened",
    "legacy",
}

# ============================================================
# 存储
# ============================================================

def load_events():
    try:
        with open(EVENTS_FILE, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"events": []}


def save_events(data):
    with open(EVENTS_FILE, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def today_str():
    return datetime.now(CST).strftime("%Y-%m-%d")


def sanitize_device_id(device_id):
    device_id = str(device_id or "").strip()
    return re.sub(r"[^A-Za-z0-9._-]", "_", device_id) or LEGACY_DEVICE_ID


def get_device_events_dir(device_id):
    safe_id = sanitize_device_id(device_id)
    path = EVENTS_JSONL_ROOT / safe_id
    path.mkdir(exist_ok=True, parents=True)
    return path


def get_jsonl_path(device_id, date_str):
    return get_device_events_dir(device_id) / f"{date_str}.jsonl"


def parse_iso_timestamp(timestamp_str):
    if not isinstance(timestamp_str, str):
        raise ValueError("timestamp must be a string")
    ts = timestamp_str.strip()
    if ts.endswith("Z"):
        ts = ts[:-1] + "+00:00"
    return datetime.fromisoformat(ts)


def event_date_str_from_timestamp(timestamp_str):
    return parse_iso_timestamp(timestamp_str).date().isoformat()


def append_event_jsonl(event):
    device_id = sanitize_device_id(event.get("device_id"))
    timestamp = event.get("timestamp")
    if not timestamp:
        raise ValueError("event.timestamp is required")
    date_str = event_date_str_from_timestamp(timestamp)
    path = get_jsonl_path(device_id, date_str)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")


def read_events_jsonl(device_id, from_date=None, to_date=None):
    path = get_device_events_dir(device_id)
    if from_date:
        from_date = datetime.strptime(from_date, "%Y-%m-%d").date()
    if to_date:
        to_date = datetime.strptime(to_date, "%Y-%m-%d").date()

    events = []
    for jsonl_file in sorted(path.glob("*.jsonl")):
        file_date = datetime.strptime(jsonl_file.stem, "%Y-%m-%d").date()
        if from_date and file_date < from_date:
            continue
        if to_date and file_date > to_date:
            continue
        with open(jsonl_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return events


def read_latest_event(device_id, event_type=None):
    path = get_device_events_dir(device_id)
    files = sorted(path.glob("*.jsonl"), reverse=True)
    latest = None
    latest_ts = None
    for jsonl_file in files:
        with open(jsonl_file, "r", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if event_type and event.get("event_type") != event_type:
                    continue
                try:
                    ts = parse_iso_timestamp(event.get("timestamp"))
                except Exception:
                    continue
                if latest is None or ts > latest_ts:
                    latest = event
                    latest_ts = ts
        if latest is not None:
            break
    return latest


def require_api_key():
    if not API_KEY:
        return None
    header_key = request.headers.get("X-API-Key", "").strip()
    if header_key != API_KEY:
        return jsonify({"status": "error", "message": "Unauthorized"}), 401
    return None

# ============================================================
# 统计
# ============================================================

def compute_stats(app_filter=None, period="today"):
    events = load_events()["events"]
    now = datetime.now(CST)
    today = today_str()

    if period == "today":
        start_ts = int(datetime(now.year, now.month, now.day, tzinfo=CST).timestamp())
    elif period == "week":
        start_ts = int((now - timedelta(days=now.weekday())).replace(
            hour=0, minute=0, second=0, microsecond=0).timestamp())
    else:
        start_ts = 0

    filtered = [e for e in events if e.get("timestamp", 0) >= start_ts]
    if app_filter and app_filter != "all":
        filtered = [e for e in filtered if e.get("app") == app_filter]

    from collections import defaultdict

    grouped = defaultdict(lambda: {"opens": [], "closes": []})
    for e in filtered:
        grouped[e.get("app", "unknown")][f"{e['event']}s"].append(e)

    stats = {}
    for app, data in grouped.items():
        opens = sorted(data["opens"], key=lambda x: x["timestamp"])
        closes = sorted(data["closes"], key=lambda x: x["timestamp"])

        sessions = []
        ci = 0
        for o in opens:
            while ci < len(closes) and closes[ci]["timestamp"] < o["timestamp"]:
                ci += 1
            if ci < len(closes):
                dur = closes[ci]["timestamp"] - o["timestamp"]
                if 0 < dur < 43200:
                    sessions.append({
                        "time": o.get("iso_time", ""),
                        "dur_sec": dur,
                    })
                ci += 1

        total_sec = sum(s["dur_sec"] for s in sessions)
        stats[app] = {
            "date": today,
            "opens": len(opens),
            "closes": len(closes),
            "sessions": len(sessions),
            "total_sec": total_sec,
            "total_min": round(total_sec / 60, 1),
            "session_details": sessions,
        }

    return stats

# ============================================================
# HTTP 端点
# ============================================================

app = Flask(__name__)

@app.route("/hook", methods=["POST"])
def hook():
    """接收 Shortcut 事件 → 存盘"""
    data = request.get_json(force=True, silent=True)
    if not data:
        return jsonify({"status": "error", "message": "无效 JSON"}), 400

    app_name = data.get("app", "").strip()
    event_type = data.get("event", "").strip()
    iso_time = data.get("time", "")

    if not app_name or not event_type:
        return jsonify({"status": "error", "message": "缺少 app / event"}), 400

    if not iso_time:
        iso_time = datetime.now(CST).isoformat()

    ts = int(time.time())
    evt = {
        "id": f"evt_{datetime.now(CST).strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}",
        "app": app_name,
        "event": event_type,
        "timestamp": ts,
        "iso_time": iso_time,
        "date": today_str(),
    }

    events = load_events()
    events["events"].append(evt)

    cutoff = time.time() - 7 * 86400
    events["events"] = [e for e in events["events"] if e.get("timestamp", 0) >= cutoff]
    save_events(events)

    legacy_device_id = data.get("device_id") or LEGACY_DEVICE_ID
    jsonl_event = {
        "device_id": legacy_device_id,
        "event_type": "legacy",
        "timestamp": iso_time,
        "data": {
            "app": app_name,
            "event": event_type,
            "iso_time": iso_time,
            "date": today_str(),
        },
    }
    try:
        append_event_jsonl(jsonl_event)
    except Exception:
        pass

    return jsonify({"status": "ok", "event_id": evt["id"]})

@app.route("/api/events", methods=["POST"])
def api_events():
    auth_err = require_api_key()
    if auth_err:
        return auth_err

    data = request.get_json(force=True, silent=True)
    if not data:
        return jsonify({"status": "error", "message": "无效 JSON"}), 400

    device_id = data.get("device_id", "").strip()
    event_type = data.get("event_type", "").strip()
    timestamp = data.get("timestamp", "").strip()
    payload = data.get("data")

    if not device_id or not event_type or not timestamp or payload is None:
        return jsonify({"status": "error", "message": "缺少 device_id/event_type/timestamp/data"}), 400

    if event_type not in ALLOWED_EVENT_TYPES:
        return jsonify({"status": "error", "message": "不支持的 event_type"}), 400

    try:
        parse_iso_timestamp(timestamp)
    except Exception:
        return jsonify({"status": "error", "message": "timestamp 格式无效"}), 400

    event = {
        "device_id": device_id,
        "event_type": event_type,
        "timestamp": timestamp,
        "data": payload,
    }
    try:
        append_event_jsonl(event)
    except Exception as exc:
        return jsonify({"status": "error", "message": f"写入失败: {exc}"}), 500

    return jsonify({"status": "ok", "event": event})

@app.route("/api/events/<device_id>", methods=["GET"])
def get_device_events(device_id):
    from_date = request.args.get("from")
    to_date = request.args.get("to")
    try:
        events = read_events_jsonl(device_id, from_date=from_date, to_date=to_date)
    except ValueError:
        return jsonify({"status": "error", "message": "from/to 日期格式应为 YYYY-MM-DD"}), 400

    return jsonify({"status": "ok", "events": events})

@app.route("/api/events/<device_id>/latest", methods=["GET"])
def get_latest_device_event(device_id):
    event_type = request.args.get("event_type")
    if event_type and event_type not in ALLOWED_EVENT_TYPES:
        return jsonify({"status": "error", "message": "不支持的 event_type"}), 400

    latest = read_latest_event(device_id, event_type=event_type)
    if not latest:
        return jsonify({"status": "error", "message": "未找到事件"}), 404
    return jsonify({"status": "ok", "event": latest})

@app.route("/stats", methods=["GET"])
def stats():
    return jsonify(compute_stats(
        app_filter=request.args.get("app", "all"),
        period=request.args.get("period", "today"),
    ))

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "time": datetime.now(CST).isoformat()})

# ============================================================
# 启动
# ============================================================

if __name__ == "__main__":
    host = "0.0.0.0"
    port = 5000
    print(f"[shortcut-server] http://{host}:{port}")
    app.run(host=host, port=port)
