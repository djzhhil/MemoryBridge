# Shortcut Server — A behavioral telemetry infrastructure for autonomous agents.

> A ruleless event stream system for agent perception. Mobile behaviors → structured event sourcing → LLM-driven state interpretation. No hardcoded thresholds, no push rules, no if-else.

---

## Architecture

```
┌────────────────────┐     event stream     ┌──────────────────────────────────────┐
│  Behavior Sensors   │ ───────────────────→ │      Event Ingestion Gateway        │
│  (mobile devices,   │   POST /hook        │      (Flask)                         │
│   browsers, IoT)    │                     │                                      │
│                     │                     │  /hook   ← event ingestion endpoint  │
│  app:open           │                     │  /stats  ← temporal state query API  │
│  app:close          │                     │  /health ← liveness probe            │
│  screen:on/off      │                     │                                      │
└────────────────────┘                     └─────────────┬────────────────────────┘
                                                         │
                              ┌──────────────────────────┼──────────────────────────┐
                              │                          │                          │
                              ▼                          ▼                          ▼
                     ┌─────────────────┐     ┌───────────────────┐     ┌───────────────────┐
                     │ Event Stream    │     │ Temporal State    │     │ LLM Reasoning     │
                     │ Store           │     │ API               │     │ Layer             │
                     │                 │     │                   │     │                   │
                     │ Append-only     │     │ Time-window query │     │ Agent heartbeat   │
                     │ events.json     │     │ Session reconstr. │     │ Memory injection  │
                     │ 7-day retention │     │ Behavioral agg.   │     │ Context-aware     │
                     │                 │     │ Trend comparison  │     │ Anomaly detection │
                     └─────────────────┘     └───────────────────┘     └────────┬──────────┘
                                                                                │
                                                                                ▼
                                                                     ┌───────────────────┐
                                                                     │  Action Dispatcher │
                                                                     │                   │
                                                                     │  Telegram · Bark   │
                                                                     │  Silent vs Alert   │
                                                                     └───────────────────┘
```

**Key design separation**: the system separates **sensing** (deterministic, event ingestion) from **reasoning** (probabilistic, AI-driven). The ingestion layer guarantees data integrity and temporal consistency. The reasoning layer owns all interpretation.

---

## Core Concepts

### 1. Agent Perception Layer

This is the **reality-world input surface** for autonomous agents. Unlike prompt-only interaction, this system provides a continuous behavioral event stream — your agent can perceive what a user does across mobile apps, not just what they type.

-   **Behavior Sensors** → mobile automations (iOS Shortcuts, Android Tasker) that emit structured events on app lifecycle transitions
-   **Event Ingestion Gateway** → a lightweight HTTP endpoint that accepts, validates, and timestamps events
-   **Agent-consumable output** → time-series behavioral data ready for LLM context injection

### 2. Event Sourcing for Agent Memory

The system provides structured event sourcing primitives for agent state reconstruction:

-   **Append-only event log** → every event is immutable, timestamped, and traceable
-   **Session reconstruction** → open/close event pairs resolve into measurable behavioral sessions with duration, time-of-day, and sequence metadata
-   **Time-window queries** → `?period=today | week` returns aggregated telemetry sliced by any temporal boundary
-   **Behavioral aggregation** → opens, closes, session count, total duration, per-session breakdown, and hourly distribution — all derived from raw events, never stored as pre-computed state

### 3. Ruleless AI Decision Layer

There is **no rule engine**. No YAML conditions. No time-window triggers. No threshold if-else blocks.

-   **System responsibility**: record events faithfully, compute correct temporal state, expose queryable data
-   **Agent responsibility**: consume behavioral telemetry through a periodic heartbeat, cross-reference with long-term memory, and decide whether any pattern warrants human attention

This is the core philosophical shift from traditional monitoring systems:

| Traditional | This project |
|---|---|
| `if opens > 5 then notify` | Agent reads opens over baseline, evaluates trend direction |
| Hardcoded cooldown timers | Agent decides when to speak based on conversation context |
| Rule YAML → push | Raw telemetry → LLM reasoning → natural language alert |
| Developer-defined thresholds | Model-inferred behavioral norms |

### 4. Agent Heartbeat / Observability Stream

The heartbeat is not a cron job — it is a **periodic behavioral telemetry stream for autonomous agents**.

Every 30 minutes, the agent:

1.  Pulls today's telemetry (`/stats?period=today`)
2.  Pulls the week's trend (`/stats?period=week`)
3.  Analyzes against personal baselines stored in agent memory
4.  Decides: silent acknowledgment (`HEARTBEAT_OK`) or a natural-language alert

The agent evaluates five dimensions before speaking:

-   **Trend direction** — is usage climbing or declining?
-   **Anomaly scoring** — is today's session duration outside the personal z-score range?
-   **Temporal distribution** — are sessions clustering in late-night hours?
-   **Novelty detection** — has a previously unseen app appeared in the stream?
-   **Absence signal** — has a normally active app gone completely silent?

---

## API Design

### Event Ingestion

```
POST /hook
```

**Event Schema:**

```json
{
  "app": "douyin",
  "event": "open",
  "time": "2026-05-21T12:00:00+08:00"
}
```

**Event Types:**

| Type | Semantics |
|---|---|
| `open` | Application foreground transition — begins a behavioral session |
| `close` | Application background transition — terminates the current session |
| `lock` | Device lock — terminates all active sessions |
| `unlock` | Device unlock — resets session context |
| `heartbeat` | Liveness pulse from a behavior sensor |

**Response:**

```json
{
  "status": "ok",
  "event_id": "evt_20260521_120000_a1b2c3"
}
```

The ingestion endpoint is **fire-and-forget** — no rules are evaluated, no notifications are dispatched. It records and acknowledges.

### Temporal State Query

```
GET /stats?period=today
GET /stats?period=week
GET /stats?app=douyin&period=today
```

**Response:**

```json
{
  "douyin": {
    "date": "2026-05-21",
    "opens": 8,
    "closes": 6,
    "sessions": 5,
    "total_sec": 7200,
    "total_min": 120.0,
    "session_details": [
      {"time": "2026-05-21T12:30", "dur_sec": 1800},
      {"time": "2026-05-21T14:05", "dur_sec": 1200}
    ]
  }
}
```

Every session duration is derived from raw open/close event pairs — no pre-aggregated metrics, no estimation heuristics.

### Health Check

```
GET /health
```

---

## Event Stream Model

The event store is an **append-only time-series log**, not application state.

```json
{
  "events": [
    {
      "id": "evt_20260521_120000_a1b2c3",
      "app": "douyin",
      "event": "open",
      "timestamp": 1747800000,
      "iso_time": "2026-05-21T12:00:00+08:00",
      "date": "2026-05-21"
    }
  ]
}
```

**Design properties:**

-   **Immutable** — events are never modified after ingestion
-   **Replayable** — the full stream can reconstruct any past behavioral state
-   **Self-pruning** — 7-day retention window, automatic roll-off
-   **Stateless server** — the ingestion gateway holds no in-memory application state; the event file is the single source of truth

---

## AI Agent Integration

### With OpenClaw (Native)

The system is designed to run as a companion to the OpenClaw agent runtime. The heartbeat pulls telemetry via `curl /stats`, cross-references against `MEMORY.md` for personal baselines, and produces natural-language observations only when warranted.

### As a Tool Adapter (Extensible)

The ingestion + query pattern can be adapted for other agent frameworks:

-   **MCP (Model Context Protocol)** — expose `/stats` as a `tool` with `period` and `app` parameters
-   **LangChain** — wrap the query API as a `Tool` for chain-of-thought behavioral analysis
-   **Custom agents** — any HTTP-speaking agent can consume the telemetry endpoint

### Event Replay / Dataset Capability

The append-only event stream doubles as a behavioral dataset. Historical events can be replayed into an agent for:

-   Behavioral pattern discovery
-   Baseline calibration
-   Anomaly detection model training

---

## Future Directions

-   **Multi-device telemetry mesh** — merge event streams from multiple sensors (phone, tablet, watch) into a unified behavioral timeline
-   **Agent plugin ingestion format** — standardize the event schema for broader agent ecosystem adoption
-   **Session vectorization** — embed behavioral sessions for semantic similarity search across time
-   **Cohort baselines** — compare individual telemetry against anonymized population norms (privacy-preserving)

---

## Project Structure

```
shortcut-server/
├── server.py              # Event Ingestion Gateway (Flask)
├── data/
│   └── events.json        # Event Stream Store (append-only)
├── deploy/                # Deployment configs
├── requirements.txt       # Flask
└── README.md
```

---

## Quick Start

```bash
cd shortcut-server
source .venv/bin/activate
pip install flask
python server.py           # Listening on :5000
```

```bash
# Ingest an event
curl -X POST http://localhost:5000/hook \
  -H "Content-Type: application/json" \
  -d '{"app":"douyin","event":"open","time":"2026-05-21T12:00:00+08:00"}'

# Query temporal state
curl http://localhost:5000/stats?period=today
```

---

> **A lightweight agent memory ingestion layer from mobile devices.**
