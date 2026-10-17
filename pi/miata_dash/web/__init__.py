"""Flask app: dashboard (switchable layouts), radio, drive logs, settings and JSON APIs.

Everything is served locally; the pages load no external resources, so they
work without internet.
"""

from __future__ import annotations

import re
from dataclasses import replace
from pathlib import Path

from flask import Flask, abort, jsonify, redirect, render_template, request, send_from_directory, url_for

from ..gauges import DEFAULT_GAUGES, LAYOUTS, Gauge
from ..radio import BANDS_BY_ID, RadioController
from ..settings import SettingsStore
from ..state import VehicleState

_LOG_NAME = re.compile(r"^\d{4,}_[\w-]+\.csv$")
_AXES = ("+x", "-x", "+y", "-y", "+z", "-z")


def create_app(
    state: VehicleState,
    store: SettingsStore,
    gauges: tuple[Gauge, ...] = DEFAULT_GAUGES,
    radio: RadioController | None = None,
    log_dir: str | Path | None = None,
) -> Flask:
    app = Flask(__name__)
    logs = Path(log_dir) if log_dir else None

    def save(**changes) -> None:
        store.save(replace(store.settings, **changes))

    # -- dashboard ----------------------------------------------------------
    @app.get("/")
    def dashboard():
        settings = store.settings
        visible = [g.to_dict() for g in gauges if settings.gauge_visible(g.id)]
        layout = settings.layout if settings.layout in LAYOUTS else "digital"
        return render_template(
            "dashboard.html",
            layout=layout,
            gauges=visible,
            stale_after=settings.stale_after_s,
            shift_rpm=settings.shift_rpm,
        )

    @app.get("/api/state")
    def api_state():
        return jsonify(state.snapshot())

    @app.post("/api/layout")
    def api_layout():
        """Body {"layout": name} or {"step": ±1} (knob / touch cycling)."""
        body = request.get_json(silent=True) or {}
        current = store.settings.layout if store.settings.layout in LAYOUTS else LAYOUTS[0]
        if body.get("layout") in LAYOUTS:
            layout = body["layout"]
        else:
            step = int(body.get("step", 1))
            layout = LAYOUTS[(LAYOUTS.index(current) + step) % len(LAYOUTS)]
        save(layout=layout)
        return jsonify({"layout": layout})

    # -- radio --------------------------------------------------------------
    @app.get("/radio")
    def radio_page():
        return render_template("radio.html", presets=store.settings.radio_presets)

    @app.get("/api/radio")
    def api_radio():
        if radio is None:
            return jsonify({"available": False, "error": "radio disabled"})
        return jsonify({**radio.status(), "presets": store.settings.radio_presets})

    @app.post("/api/radio")
    def api_radio_action():
        if radio is None:
            abort(404)
        body = request.get_json(silent=True) or {}
        action = body.get("action")
        result: dict = {}
        try:
            if action == "tune":
                freq = body.get("freq_mhz")
                radio.tune(body.get("band"), float(freq) if freq is not None else None)
            elif action == "step":
                radio.step(1 if int(body.get("direction", 1)) > 0 else -1)
            elif action == "stop":
                radio.stop()
            elif action == "squelch":
                radio.set_squelch(int(body.get("level", 0)))
            elif action == "scan":
                result["stations"] = radio.scan_fm()
            elif action == "save_preset":
                preset = {"band": radio.band.id, "freq_mhz": radio.freq_mhz}
                presets = [p for p in store.settings.radio_presets if p != preset]
                save(radio_presets=[*presets, preset])
            elif action == "delete_preset":
                preset = {"band": body.get("band"), "freq_mhz": body.get("freq_mhz")}
                save(radio_presets=[p for p in store.settings.radio_presets if p != preset])
            else:
                return jsonify({"error": f"unknown action {action!r}"}), 400
        except (ValueError, TypeError) as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify({**radio.status(), "presets": store.settings.radio_presets, **result})

    # -- drive logs ---------------------------------------------------------
    @app.get("/logs")
    def logs_page():
        files = []
        if logs is not None and logs.is_dir():
            for p in sorted(logs.glob("*.csv"), reverse=True):
                if _LOG_NAME.match(p.name):
                    files.append({"name": p.name, "kb": max(1, p.stat().st_size // 1024)})
        return render_template("logs.html", files=files)

    @app.get("/logs/<name>")
    def log_download(name: str):
        if logs is None or not _LOG_NAME.match(name):
            abort(404)
        return send_from_directory(logs, name, as_attachment=True)

    # -- settings -----------------------------------------------------------
    @app.get("/settings")
    def settings_page():
        return render_template(
            "settings.html", gauges=gauges, settings=store.settings, layouts=LAYOUTS, axes=_AXES
        )

    @app.post("/settings")
    def settings_save():
        form = request.form
        shown = set(form.getlist("shown"))
        current = store.settings
        changes = dict(
            hidden_gauges=[g.id for g in gauges if g.id not in shown],
            send_gps_speed_to_ecu=form.get("send_gps_speed_to_ecu") == "on",
            logging_enabled=form.get("logging_enabled") == "on",
        )
        if form.get("layout") in LAYOUTS:
            changes["layout"] = form["layout"]
        if form.get("shift_rpm", "").isdigit():
            changes["shift_rpm"] = min(max(int(form["shift_rpm"]), 1000), 9000)
        for key in ("imu_forward_axis", "imu_up_axis"):
            if form.get(key) in _AXES:
                changes[key] = form[key]
        if form.get("gps_rate_hz") in ("1", "2", "5"):
            changes["gps_rate_hz"] = float(form["gps_rate_hz"])
        store.save(replace(current, **changes))
        return redirect(url_for("dashboard"))

    app.jinja_env.globals["bands"] = BANDS_BY_ID
    return app
