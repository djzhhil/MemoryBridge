#!/usr/bin/env python3
"""
Shortcut Server — 统一 JSONL 存储（按设备/日期分片）
完全兼容旧 /hook 接口（open/close 事件）
"""

import json
import os
import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from flask import Flask, request, jsonify

# ---------- 常量 ----------
CST = timezone(timedelta(hours=8))
BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

EVENTS_JSONL_ROOT = DATA_DIR / "events"
EVENTS_JSONL_ROOT.mkdir(exist_ok=True, parents=True)

API_KEY = os.getenv("API_KEY", "").strip()
LEGACY_DEVICE_ID = "legacy"
ALLOWED_EVENT_TYPES = {
    "scheduled", "geofence", "power", "connectivity",
    "focus_change", "sound_recognition", "app_opened", "legacy"
}

# ---------- 辅助函数 ----------
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
    """解析 ISO 8601 时间字符串，返回 datetime（带时区）"""
    if not isinstance(timestamp_str, str):
        raise ValueError("timestamp must be a string")
    ts = timestamp_str.strip()
    if ts.endswith("Z"):
        ts = ts[:-1] + "+00:00"
    return datetime.fromisoformat(ts)

def append_event_jsonl(event):
    """写入一条事件到 JSONL 文件（按设备+日期分片）"""
    device_id = sanitize_device_id(event.get("device_id"))
    timestamp = event.get("timestamp")
    if not timestamp:
        raise ValueError("event.timestamp is required")
    # 支持 timestamp 为 ISO 字符串或 Unix 时间戳（整数）
    if isinstance(timestamp, (int, float)):
        dt = datetime.fromtimestamp(timestamp, tz=CST)
        date_str = dt.date().isoformat()
    else:
        date_str = parse_iso_timestamp(timestamp).date().isoformat()
    path = get_jsonl_path(device_id, date_str)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")

def read_events_jsonl(device_id, from_date=None, to_date=None):
    """查询某个设备指定日期范围内的事件（返回列表）"""
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
    """读取某个设备最新的一条事件（可筛选 event_type）"""
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
                # 解析 timestamp（可能是 ISO 字符串或 Unix 时间戳）
                ts_raw = event.get("timestamp")
                try:
                    if isinstance(ts_raw, (int, float)):
                        ts = datetime.fromtimestamp(ts_raw, tz=CST)
                    else:
                        ts = parse_iso_timestamp(ts_raw)
                except Exception:
                    continue
                if latest is None or ts > latest_ts:
                    latest = event
                    latest_ts = ts
        if latest is not None:
            break
    return latest

def compute_stats_from_jsonl(device_id, app_name=None, period="today"):
    """
    从 JSONL 中统计指定 app 的使用情况（兼容 open/close 和 opened/closed）
    """
    now = datetime.now(CST)
    if period == "today":
        start_date = now.date()
    elif period == "week":
        start_date = (now - timedelta(days=now.weekday())).date()
    else:
        start_date = None

    events = read_events_jsonl(device_id, from_date=start_date.isoformat() if start_date else None)
    
    # 过滤出 app 事件（legacy 类型且 data 中有 app）
    app_events = []
    for ev in events:
        if ev.get("event_type") not in ("legacy", "app_opened"):
            continue
        data = ev.get("data", {})
        app = data.get("app")
        if not app:
            continue
        if app_name and app != app_name:
            continue
        ts_raw = ev.get("timestamp")
        if isinstance(ts_raw, (int, float)):
            dt = datetime.fromtimestamp(ts_raw, tz=CST)
        else:
            try:
                dt = parse_iso_timestamp(ts_raw)
            except Exception:
                continue
        app_events.append({
            "app": app,
            "event": data.get("event"),  # 可能是 "open"/"close" 或 "opened"/"closed"
            "timestamp": dt,
            "iso_time": dt.isoformat()
        })
    
    from collections import defaultdict
    grouped = defaultdict(lambda: {"opens": [], "closes": []})
    for e in app_events:
        ev = e["event"]
        if ev in ("open", "opened"):
            grouped[e["app"]]["opens"].append(e)
        elif ev in ("close", "closed"):
            grouped[e["app"]]["closes"].append(e)
        # 其他值忽略
    
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
                dur = (closes[ci]["timestamp"] - o["timestamp"]).total_seconds()
                if 0 < dur < 43200:
                    sessions.append({
                        "time": o["iso_time"],
                        "dur_sec": dur,
                    })
                ci += 1
        total_sec = sum(s["dur_sec"] for s in sessions)
        stats[app] = {
            "date": today_str(),
            "opens": len(opens),
            "closes": len(closes),
            "sessions": len(sessions),
            "total_sec": total_sec,
            "total_min": round(total_sec / 60, 1),
            "session_details": sessions,
        }
    return stats

def compute_stats_for_all_apps(device_id, period):
    """一次性统计所有 app，兼容 open/close 和 opened/closed"""
    now = datetime.now(CST)
    if period == "today":
        start_date = now.date()
    elif period == "week":
        start_date = (now - timedelta(days=now.weekday())).date()
    else:
        start_date = None

    events = read_events_jsonl(device_id, from_date=start_date.isoformat() if start_date else None)
    from collections import defaultdict
    app_events = defaultdict(list)
    for ev in events:
        if ev.get("event_type") not in ("legacy", "app_opened"):
            continue
        data = ev.get("data", {})
        app = data.get("app")
        if not app:
            continue
        ts_raw = ev.get("timestamp")
        if isinstance(ts_raw, (int, float)):
            dt = datetime.fromtimestamp(ts_raw, tz=CST)
        else:
            try:
                dt = parse_iso_timestamp(ts_raw)
            except Exception:
                continue
        app_events[app].append({
            "event": data.get("event"),
            "timestamp": dt,
            "iso_time": dt.isoformat()
        })
    
    result = {}
    for app, evs in app_events.items():
        opens = sorted([e for e in evs if e["event"] in ("open", "opened")], key=lambda x: x["timestamp"])
        closes = sorted([e for e in evs if e["event"] in ("close", "closed")], key=lambda x: x["timestamp"])
        sessions = []
        ci = 0
        for o in opens:
            while ci < len(closes) and closes[ci]["timestamp"] < o["timestamp"]:
                ci += 1
            if ci < len(closes):
                dur = (closes[ci]["timestamp"] - o["timestamp"]).total_seconds()
                if 0 < dur < 43200:
                    sessions.append({"time": o["iso_time"], "dur_sec": dur})
                ci += 1
        total_sec = sum(s["dur_sec"] for s in sessions)
        result[app] = {
            "date": today_str(),
            "opens": len(opens),
            "closes": len(closes),
            "sessions": len(sessions),
            "total_sec": total_sec,
            "total_min": round(total_sec / 60, 1),
            "session_details": sessions,
        }
    return result

def require_api_key():
    if not API_KEY:
        return None
    header_key = request.headers.get("X-API-Key", "").strip()
    if header_key != API_KEY:
        return jsonify({"status": "error", "message": "Unauthorized"}), 401
    return None

# ---------- Flask 路由 ----------
app = Flask(__name__)

@app.route("/hook", methods=["POST"])
def hook():
    """兼容旧版 iOS 快捷指令：接收 {app, event, time, device_id} 并写入 JSONL"""
    data = request.get_json(force=True, silent=True)
    if not data:
        return jsonify({"status": "error", "message": "无效 JSON"}), 400

    app_name = data.get("app", "").strip()
    event_type = data.get("event", "").strip()  # "open" / "close"
    iso_time = data.get("time", "")
    device_id = data.get("device_id", LEGACY_DEVICE_ID)

    if not app_name or not event_type:
        return jsonify({"status": "error", "message": "缺少 app / event"}), 400

    if not iso_time:
        iso_time = datetime.now(CST).isoformat()

    # 构造新格式的事件，存入 JSONL
    jsonl_event = {
        "device_id": device_id,
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
    except Exception as e:
        return jsonify({"status": "error", "message": f"写入失败: {e}"}), 500

    return jsonify({"status": "ok", "event_id": str(uuid.uuid4())})

@app.route("/api/events", methods=["POST"])
def api_events():
    """新接口：直接写入标准事件（需要 API_KEY 验证）"""
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
    device_id = request.args.get("device_id", LEGACY_DEVICE_ID)
    app_name = request.args.get("app", "all")
    period = request.args.get("period", "today")
    
    if app_name == "all":
        stats_data = compute_stats_for_all_apps(device_id, period)
        return jsonify(stats_data)
    else:
        stats_data = compute_stats_from_jsonl(device_id, app_name, period)
        return jsonify(stats_data)

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "time": datetime.now(CST).isoformat()})

if __name__ == "__main__":
    host = "0.0.0.0"
    port = 5000
    print(f"[shortcut-server] http://{host}:{port}")
    app.run(host=host, port=port)