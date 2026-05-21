#!/usr/bin/env python3
"""
Shortcut Server — 纯事件存储 + 统计查询

接收 iOS Shortcut POST → 存 events.json → 供心跳脚本消费
无规则引擎、无推送、无 AI。
"""

import json
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from flask import Flask, request, jsonify

CST = timezone(timedelta(hours=8))
BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

EVENTS_FILE = DATA_DIR / "events.json"

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

    # 按 app 分组 open/close 事件
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

    # 保留 7 天
    cutoff = time.time() - 7 * 86400
    events["events"] = [e for e in events["events"] if e.get("timestamp", 0) >= cutoff]

    save_events(events)

    return jsonify({"status": "ok", "event_id": evt["id"]})

@app.route("/stats", methods=["GET"])
def stats():
    """查询统计"""
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
