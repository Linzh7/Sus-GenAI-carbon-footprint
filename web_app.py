from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from flask import Flask, abort, redirect, render_template_string, request, url_for

LEARNING_DB_PATH = Path(__file__).with_name("learning.db")
DOTENV_PATH = Path(__file__).with_name(".env")

app = Flask(__name__)


def _load_dotenv(path: Path) -> None:
  if not path.exists():
    return

  for raw_line in path.read_text(encoding="utf-8").splitlines():
    line = raw_line.strip()
    if not line or line.startswith("#") or "=" not in line:
      continue

    key, value = line.split("=", 1)
    key = key.strip()
    value = value.strip()

    if not key:
      continue

    if (
      len(value) >= 2
      and ((value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")))
    ):
      value = value[1:-1]

    os.environ.setdefault(key, value)


_load_dotenv(DOTENV_PATH)


def _run_command(args: List[str]) -> str:
    try:
        output = subprocess.check_output(args, stderr=subprocess.STDOUT, text=True, timeout=5)
        return output.strip()
    except Exception:
        return ""


def _boot_timestamp() -> float:
    output = _run_command(["sysctl", "-n", "kern.boottime"])
    match = re.search(r"sec\s*=\s*(\d+)", output)
    if match:
        return float(match.group(1))
    return datetime.now(timezone.utc).timestamp()


def _sleep_count() -> int:
    stats_output = _run_command(["pmset", "-g", "stats"])
    match = re.search(r"Sleep Count:(\d+)", stats_output)
    if match:
        return int(match.group(1))

    log_output = _run_command(["pmset", "-g", "log"])
    match = re.search(r"Total Sleep/Wakes since boot.*:(\d+)", log_output)
    if match:
        return int(match.group(1))

    return 0


def _machine_metrics(avg_sleep_minutes: float) -> Dict[str, float | str]:
    boot_ts = _boot_timestamp()
    now_ts = datetime.now(timezone.utc).timestamp()

    uptime_hours = max(0.0, (now_ts - boot_ts) / 3600.0)
    sleep_count = _sleep_count()
    est_sleep_hours = max(0.0, sleep_count * (max(avg_sleep_minutes, 0.0) / 60.0))
    est_awake_hours = max(0.0, uptime_hours - est_sleep_hours)

    boot_local = datetime.fromtimestamp(boot_ts).strftime("%Y-%m-%d %H:%M:%S")

    return {
        "boot_local": boot_local,
        "uptime_hours": uptime_hours,
        "sleep_count": float(sleep_count),
        "sleep_hours_est": est_sleep_hours,
        "awake_hours_est": est_awake_hours,
    }


def _compute_footprint(machine: Dict[str, float | str], active_watts: float, sleep_watts: float, ci_g_per_kwh: float) -> Dict[str, float]:
    awake_hours = float(machine["awake_hours_est"])
    sleep_hours = float(machine["sleep_hours_est"])

    energy_kwh = ((awake_hours * max(0.0, active_watts)) + (sleep_hours * max(0.0, sleep_watts))) / 1000.0
    co2_kg = energy_kwh * max(0.0, ci_g_per_kwh) / 1000.0

    return {
        "energy_kwh": energy_kwh,
        "co2_kg": co2_kg,
    }


def _safe_float(raw: str | None, fallback: float) -> float:
    if raw is None:
        return fallback
    try:
        return float(raw)
    except ValueError:
        return fallback


def _parse_zones(raw: str) -> List[str]:
    zones: List[str] = []
    for chunk in raw.split(","):
        zone = chunk.strip().upper()
        if not zone:
            continue
        if zone not in zones:
            zones.append(zone)
    return zones


def _em_api_get(path: str, params: Dict[str, str], token: str) -> Dict[str, Any]:
    query = urlencode(params)
    url = f"https://api.electricitymap.org{path}?{query}"
    req = Request(url, headers={"auth-token": token, "Accept": "application/json"})
    with urlopen(req, timeout=8) as response:
        payload = response.read().decode("utf-8")
        return json.loads(payload)


def _extract_float(payload: Dict[str, Any], candidates: List[str]) -> float | None:
    for key in candidates:
        value = payload.get(key)
        if isinstance(value, (int, float)):
            return float(value)

    history = payload.get("history")
    if isinstance(history, list) and history:
        head = history[0]
        if isinstance(head, dict):
            for key in candidates:
                value = head.get(key)
                if isinstance(value, (int, float)):
                    return float(value)

    return None


def _fetch_world_stats(zone: str, token: str) -> Dict[str, Any]:
    ci_payload = _em_api_get("/v3/carbon-intensity/latest", {"zone": zone}, token)
    re_payload = _em_api_get("/v3/renewable-energy/latest", {"zone": zone}, token)

    return {
        "zone": zone,
        "carbon_intensity": _extract_float(ci_payload, ["carbonIntensity", "carbonIntensityAvg"]),
        "renewable_pct": _extract_float(re_payload, ["renewablePercentage", "renewablePercentageAvg"]),
        "carbon_payload": ci_payload,
        "renewable_payload": re_payload,
    }


# --- Sustainability learning: persistence helpers (SQLite) ---

def _get_learning_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(LEARNING_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_learning_db() -> None:
    with _get_learning_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS lessons (
                id INTEGER PRIMARY KEY,
                title TEXT NOT NULL,
                category TEXT NOT NULL,
                description TEXT NOT NULL,
                youtube_id TEXT NOT NULL,
                duration_minutes INTEGER NOT NULL,
                points INTEGER NOT NULL,
                difficulty TEXT NOT NULL,
                order_index INTEGER NOT NULL
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS completions (
                id INTEGER PRIMARY KEY,
                lesson_id INTEGER NOT NULL UNIQUE,
                completed_at TEXT NOT NULL,
                FOREIGN KEY (lesson_id) REFERENCES lessons(id)
            )
            """
        )
        conn.commit()

        cur.execute("SELECT COUNT(*) FROM lessons")
        count = int(cur.fetchone()[0])

        if count == 0:
            seed_lessons: List[Tuple[str, str, str, str, int, int, str, int]] = [
                (
                    "Climate change foundations for business",
                    "Foundations",
                    "Understand key climate terms and why climate action matters for every team.",
                    "i9AaQvFaypk",
                    15,
                    30,
                    "Beginner",
                    1,
                ),
                (
                    "Scope 1, 2 and 3 emissions explained",
                    "Foundations",
                    "Learn the greenhouse-gas protocol scopes and practical examples.",
                    "0Z_X7iZdLEk",
                    18,
                    35,
                    "Beginner",
                    2,
                ),
                (
                    "Building a credible net-zero roadmap",
                    "Corporate Strategy",
                    "Set milestones, governance, and a realistic delivery plan.",
                    "2RkCBlwRie4",
                    20,
                    40,
                    "Intermediate",
                    3,
                ),
                (
                    "Digital infrastructure and data-center efficiency",
                    "AI & Digital",
                    "Understand how infrastructure choices affect electricity and emissions.",
                    "UFK4hqeRhIc",
                    14,
                    25,
                    "Intermediate",
                    4,
                ),
            ]

            cur.executemany(
                """
                INSERT INTO lessons (
                    title,
                    category,
                    description,
                    youtube_id,
                    duration_minutes,
                    points,
                    difficulty,
                    order_index
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                seed_lessons,
            )
            conn.commit()


def _fetch_lessons_with_progress() -> List[sqlite3.Row]:
    _ensure_learning_db()
    with _get_learning_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT
                l.*,
                CASE
                    WHEN EXISTS (
                        SELECT 1 FROM completions c WHERE c.lesson_id = l.id
                    ) THEN 1
                    ELSE 0
                END AS is_completed
            FROM lessons AS l
            ORDER BY l.order_index ASC, l.id ASC
            """
        )
        return cur.fetchall()


def _fetch_learning_stats() -> Dict[str, float]:
    _ensure_learning_db()
    with _get_learning_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*), COALESCE(SUM(points), 0), COALESCE(SUM(duration_minutes), 0) FROM lessons")
        total_lessons, total_points_available, total_minutes_available = cur.fetchone()

        cur.execute(
            """
            SELECT
                COALESCE(SUM(l.points), 0) AS points_earned,
                COALESCE(SUM(l.duration_minutes), 0) AS minutes_completed,
                COUNT(*) AS completed_lessons
            FROM lessons l
            WHERE EXISTS (SELECT 1 FROM completions c WHERE c.lesson_id = l.id)
            """
        )
        points_earned, minutes_completed, completed_lessons = cur.fetchone()

    return {
        "total_lessons": float(total_lessons or 0),
        "total_points_available": float(total_points_available or 0),
        "total_minutes_available": float(total_minutes_available or 0),
        "points_earned": float(points_earned or 0),
        "minutes_completed": float(minutes_completed or 0),
        "completed_lessons": float(completed_lessons or 0),
    }


def _fetch_lesson(lesson_id: int) -> sqlite3.Row | None:
    _ensure_learning_db()
    with _get_learning_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT
                l.*,
                CASE
                    WHEN EXISTS (
                        SELECT 1 FROM completions c WHERE c.lesson_id = l.id
                    ) THEN 1
                    ELSE 0
                END AS is_completed
            FROM lessons AS l
            WHERE l.id = ?
            """,
            (lesson_id,),
        )
        return cur.fetchone()


def _mark_lesson_completed(lesson_id: int) -> None:
    _ensure_learning_db()
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    with _get_learning_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "INSERT OR IGNORE INTO completions (lesson_id, completed_at) VALUES (?, ?)",
            (lesson_id, now),
        )
        conn.commit()


@app.route("/", methods=["GET", "POST"])
def index() -> str:
    active_watts = _safe_float(request.form.get("active_watts"), 35.0)
    sleep_watts = _safe_float(request.form.get("sleep_watts"), 2.5)
    ci_g_per_kwh = _safe_float(request.form.get("ci_g_per_kwh"), 475.0)
    avg_sleep_minutes = _safe_float(request.form.get("avg_sleep_minutes"), 45.0)

    machine = _machine_metrics(avg_sleep_minutes=avg_sleep_minutes)
    footprint = _compute_footprint(
        machine=machine,
        active_watts=active_watts,
        sleep_watts=sleep_watts,
        ci_g_per_kwh=ci_g_per_kwh,
    )

    return render_template_string(
        MACHINE_TEMPLATE,
        machine=machine,
        footprint=footprint,
        active_watts=active_watts,
        sleep_watts=sleep_watts,
        ci_g_per_kwh=ci_g_per_kwh,
        avg_sleep_minutes=avg_sleep_minutes,
    )


@app.route("/world-stats", methods=["GET", "POST"])
def world_stats() -> str:
    token = os.getenv("ELECTRICITYMAPS_API_TOKEN", "").strip()
    zones_input = (request.form.get("zones") or request.args.get("zones") or "IN,DE,FR,US").strip()
    zones = _parse_zones(zones_input)

    stats = None
    chart_rows: List[Dict[str, Any]] = []
    error = ""

    if token and zones:
        zone_errors: List[str] = []
        for zone in zones:
            try:
                row = _fetch_world_stats(zone=zone, token=token)
                chart_rows.append(row)
            except Exception as exc:
                zone_errors.append(f"{zone}: {exc}")

        if chart_rows:
            stats = chart_rows[0]

            max_ci = max(
                (float(r["carbon_intensity"]) for r in chart_rows if r.get("carbon_intensity") is not None),
                default=0.0,
            )
            for row in chart_rows:
                ci = row.get("carbon_intensity")
                re = row.get("renewable_pct")
                row["ci_bar_pct"] = 0.0 if ci is None or max_ci <= 0 else max(0.0, min(100.0, float(ci) / max_ci * 100.0))
                row["re_bar_pct"] = 0.0 if re is None else max(0.0, min(100.0, float(re)))

        if zone_errors:
            error = "Some zones failed: " + " | ".join(zone_errors)
    elif token and not zones:
        error = "Please provide at least one country/zone code."

    return render_template_string(
        WORLD_TEMPLATE,
        zone=zones[0] if zones else "",
        zones_input=zones_input,
        chart_rows=chart_rows,
        has_token=bool(token),
        stats=stats,
        error=error,
    )


@app.route("/learn", methods=["GET"])
def learn_dashboard() -> str:
    lessons = _fetch_lessons_with_progress()
    stats = _fetch_learning_stats()
    return render_template_string(LEARN_INDEX_TEMPLATE, lessons=lessons, stats=stats)


@app.route("/learn/<int:lesson_id>", methods=["GET"])
def learn_lesson(lesson_id: int) -> str:
    lesson = _fetch_lesson(lesson_id)
    if lesson is None:
        abort(404)
    return render_template_string(LEARN_DETAIL_TEMPLATE, lesson=lesson)


@app.route("/learn/complete/<int:lesson_id>", methods=["POST"])
def learn_complete(lesson_id: int):
    lesson = _fetch_lesson(lesson_id)
    if lesson is None:
        abort(404)
    _mark_lesson_completed(lesson_id)
    return redirect(url_for("learn_lesson", lesson_id=lesson_id))


MACHINE_TEMPLATE = """
<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <title>Machine Carbon Tracker</title>
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <style>
      body { margin: 0; background: #0b1220; color: #e5e7eb; font-family: system-ui, -apple-system, sans-serif; }
      .wrap { max-width: 980px; margin: 0 auto; padding: 24px 16px 40px; }
      .nav { display: flex; gap: 10px; margin-bottom: 18px; }
      .nav a { color: #93c5fd; text-decoration: none; }
      .grid { display: grid; grid-template-columns: 1.1fr 1fr; gap: 16px; }
      @media (max-width: 900px) { .grid { grid-template-columns: 1fr; } }
      .card { background: #111827; border: 1px solid #1f2937; border-radius: 12px; padding: 14px; }
      h1, h2, h3, p { margin-top: 0; }
      .metric { font-size: 1.4rem; font-weight: 700; }
      form { display: grid; gap: 8px; }
      label { display: grid; gap: 4px; font-size: 0.9rem; color: #cbd5e1; }
      input { border: 1px solid #374151; border-radius: 8px; padding: 8px; background: #0f172a; color: #e5e7eb; }
      button { border: none; border-radius: 8px; padding: 10px; cursor: pointer; background: #22c55e; color: #052e16; font-weight: 700; }
      .muted { color: #94a3b8; font-size: 0.9rem; }
    </style>
  </head>
  <body>
    <div class="wrap">
      <div class="nav">
        <a href="{{ url_for('index') }}">Machine tracker</a>
        <a href="{{ url_for('world_stats') }}">World stats</a>
        <a href="{{ url_for('learn_dashboard') }}">Learn</a>
      </div>

      <h1>Local machine carbon footprint</h1>
      <p class="muted">This page reads uptime and sleep count from your macOS machine and estimates energy/emissions.</p>

      <div class="grid">
        <section class="card">
          <h2>Machine signals</h2>
          <p>Boot time: <strong>{{ machine.boot_local }}</strong></p>
          <p>Uptime since boot: <span class="metric">{{ '%.2f'|format(machine.uptime_hours) }} h</span></p>
          <p>Sleep/wake count since boot: <span class="metric">{{ '%.0f'|format(machine.sleep_count) }}</span></p>
          <p>Estimated sleep time: <strong>{{ '%.2f'|format(machine.sleep_hours_est) }} h</strong></p>
          <p>Estimated awake time: <strong>{{ '%.2f'|format(machine.awake_hours_est) }} h</strong></p>
          <p class="muted">Sleep duration is estimated using sleep count × average sleep minutes per cycle.</p>
        </section>

        <section class="card">
          <h2>Power assumptions</h2>
          <form method="post" action="{{ url_for('index') }}">
            <label>
              Active power draw (W)
              <input type="number" step="0.1" name="active_watts" value="{{ active_watts }}" required />
            </label>
            <label>
              Sleep power draw (W)
              <input type="number" step="0.1" name="sleep_watts" value="{{ sleep_watts }}" required />
            </label>
            <label>
              Grid carbon intensity (gCO₂e/kWh)
              <input type="number" step="1" name="ci_g_per_kwh" value="{{ ci_g_per_kwh }}" required />
            </label>
            <label>
              Avg minutes per sleep cycle
              <input type="number" step="1" name="avg_sleep_minutes" value="{{ avg_sleep_minutes }}" required />
            </label>
            <button type="submit">Recalculate</button>
          </form>
        </section>
      </div>

      <section class="card" style="margin-top:16px;">
        <h2>Estimated footprint since boot</h2>
        <p>Electricity use: <span class="metric">{{ '%.3f'|format(footprint.energy_kwh) }} kWh</span></p>
        <p>Carbon footprint: <span class="metric">{{ '%.3f'|format(footprint.co2_kg) }} kgCO₂e</span></p>
      </section>
    </div>
  </body>
</html>
"""


WORLD_TEMPLATE = """
<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <title>World Carbon Stats</title>
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <style>
      body { margin: 0; background: #0b1220; color: #e5e7eb; font-family: system-ui, -apple-system, sans-serif; }
      .wrap { max-width: 1100px; margin: 0 auto; padding: 24px 16px 40px; }
      .nav { display: flex; gap: 10px; margin-bottom: 18px; }
      .nav a { color: #93c5fd; text-decoration: none; }
      .card { background: #111827; border: 1px solid #1f2937; border-radius: 12px; padding: 14px; margin-bottom: 16px; }
      .map-link {
        display: inline-block;
        border: none;
        border-radius: 8px;
        padding: 10px 14px;
        cursor: pointer;
        background: #22c55e;
        color: #052e16;
        font-weight: 700;
        text-decoration: none;
      }
      form { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
      input { border: 1px solid #374151; border-radius: 8px; padding: 8px; background: #0f172a; color: #e5e7eb; min-width: 160px; }
      button { border: none; border-radius: 8px; padding: 9px 12px; cursor: pointer; background: #22c55e; color: #052e16; font-weight: 700; }
      .muted { color: #94a3b8; }
      .warn { color: #fda4af; }
      .metric { font-size: 1.25rem; font-weight: 700; }
      .chart { display: grid; gap: 10px; margin-top: 12px; }
      .row { border: 1px solid #253047; border-radius: 10px; padding: 10px; background: #0f172a; }
      .row-head { display: flex; justify-content: space-between; gap: 10px; margin-bottom: 8px; }
      .zone-name { font-weight: 700; }
      .metric-label { font-size: 0.85rem; color: #94a3b8; margin-bottom: 4px; }
      .track { width: 100%; height: 10px; border-radius: 999px; background: #1f2937; overflow: hidden; }
      .bar-ci { height: 100%; background: linear-gradient(90deg, #f59e0b, #ef4444); }
      .bar-re { height: 100%; background: linear-gradient(90deg, #34d399, #22c55e); }
      .value { font-size: 0.85rem; margin-top: 4px; color: #cbd5e1; }
    </style>
  </head>
  <body>
    <div class="wrap">
      <div class="nav">
        <a href="{{ url_for('index') }}">Machine tracker</a>
        <a href="{{ url_for('world_stats') }}">World stats</a>
        <a href="{{ url_for('learn_dashboard') }}">Learn</a>
      </div>

      <h1>World carbon map and zone stats</h1>

      <section class="card">
        <h2>Live map (Electricity Maps)</h2>
        <p class="muted">The Electricity Maps website blocks third-party iframe embedding. Open it directly below:</p>
        <a class="map-link" href="https://app.electricitymaps.com/map/live/fifteen_minutes" target="_blank" rel="noopener">
          Open live map in new tab
        </a>
      </section>

      <section class="card">
        <h2>Optional API-powered zone metrics</h2>
        <form method="post" action="{{ url_for('world_stats') }}">
          <label>
            Country/zone codes (comma-separated)
            <input type="text" name="zones" value="{{ zones_input }}" placeholder="IN, DE, FR, US" />
          </label>
          <button type="submit">Show comparison</button>
        </form>

        {% if not has_token %}
          <p class="muted">Set <strong>ELECTRICITYMAPS_API_TOKEN</strong> in your environment to enable API values on this page.</p>
        {% endif %}

        {% if error %}
          <p class="warn">{{ error }}</p>
        {% endif %}

        {% if stats %}
          <div class="chart">
            {% for row in chart_rows %}
              <div class="row">
                <div class="row-head">
                  <span class="zone-name">{{ row.zone }}</span>
                  <span class="metric">{{ '%.1f'|format(row.carbon_intensity) if row.carbon_intensity is not none else 'N/A' }} gCO₂e/kWh</span>
                </div>

                <div class="metric-label">Carbon intensity (relative to highest selected)</div>
                <div class="track">
                  <div class="bar-ci" style="width: {{ '%.1f'|format(row.ci_bar_pct) }}%;"></div>
                </div>
                <div class="value">{{ '%.1f'|format(row.carbon_intensity) if row.carbon_intensity is not none else 'N/A' }} gCO₂e/kWh</div>

                <div class="metric-label" style="margin-top:8px;">Renewable share</div>
                <div class="track">
                  <div class="bar-re" style="width: {{ '%.1f'|format(row.re_bar_pct) }}%;"></div>
                </div>
                <div class="value">{{ '%.1f'|format(row.renewable_pct) if row.renewable_pct is not none else 'N/A' }}%</div>
              </div>
            {% endfor %}
          </div>
        {% endif %}
      </section>
    </div>
  </body>
</html>
"""


LEARN_INDEX_TEMPLATE = """
<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <title>Learn Sustainability</title>
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <style>
      body { margin: 0; background: #0b1220; color: #e5e7eb; font-family: system-ui, -apple-system, sans-serif; }
      .wrap { max-width: 980px; margin: 0 auto; padding: 24px 16px 40px; }
      .nav { display: flex; gap: 10px; margin-bottom: 18px; }
      .nav a { color: #93c5fd; text-decoration: none; }
      .card { background: #111827; border: 1px solid #1f2937; border-radius: 12px; padding: 14px; margin-bottom: 12px; }
      .muted { color: #94a3b8; }
      a.lesson { color: #86efac; text-decoration: none; font-weight: 600; }
    </style>
  </head>
  <body>
    <div class="wrap">
      <div class="nav">
        <a href="{{ url_for('index') }}">Machine tracker</a>
        <a href="{{ url_for('world_stats') }}">World stats</a>
        <a href="{{ url_for('learn_dashboard') }}">Learn</a>
      </div>

      <h1>Learn sustainability</h1>
      <p class="muted">Progress: {{ '%.0f'|format(stats.completed_lessons) }}/{{ '%.0f'|format(stats.total_lessons) }} lessons completed</p>

      {% for lesson in lessons %}
      <section class="card">
        <a class="lesson" href="{{ url_for('learn_lesson', lesson_id=lesson.id) }}">{{ lesson.title }}</a>
        <p class="muted">{{ lesson.category }} • {{ lesson.duration_minutes }} min • +{{ lesson.points }} pts {% if lesson.is_completed %}• Completed{% endif %}</p>
        <p>{{ lesson.description }}</p>
      </section>
      {% endfor %}
    </div>
  </body>
</html>
"""


LEARN_DETAIL_TEMPLATE = """
<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <title>{{ lesson.title }}</title>
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <style>
      body { margin: 0; background: #0b1220; color: #e5e7eb; font-family: system-ui, -apple-system, sans-serif; }
      .wrap { max-width: 980px; margin: 0 auto; padding: 24px 16px 40px; }
      .nav { display: flex; gap: 10px; margin-bottom: 18px; }
      .nav a { color: #93c5fd; text-decoration: none; }
      .card { background: #111827; border: 1px solid #1f2937; border-radius: 12px; padding: 14px; }
      iframe { width: 100%; min-height: 460px; border: 0; border-radius: 10px; }
      button { border: none; border-radius: 8px; padding: 10px 12px; cursor: pointer; background: #22c55e; color: #052e16; font-weight: 700; }
      .muted { color: #94a3b8; }
    </style>
  </head>
  <body>
    <div class="wrap">
      <div class="nav">
        <a href="{{ url_for('index') }}">Machine tracker</a>
        <a href="{{ url_for('world_stats') }}">World stats</a>
        <a href="{{ url_for('learn_dashboard') }}">Learn</a>
      </div>

      <h1>{{ lesson.title }}</h1>
      <p class="muted">{{ lesson.category }} • {{ lesson.duration_minutes }} min • +{{ lesson.points }} pts</p>

      <section class="card">
        <iframe src="https://www.youtube.com/embed/{{ lesson.youtube_id }}" allowfullscreen></iframe>
        <p>{{ lesson.description }}</p>
        <form method="post" action="{{ url_for('learn_complete', lesson_id=lesson.id) }}">
          <button type="submit">Mark as completed</button>
        </form>
      </section>
    </div>
  </body>
</html>
"""


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)
