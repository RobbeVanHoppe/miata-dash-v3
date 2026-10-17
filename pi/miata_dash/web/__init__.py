"""Flask app: the dashboard page, a settings page and a JSON state API.

Everything is served locally; the page loads no external resources, so it
works without internet.
"""

from __future__ import annotations

from dataclasses import replace

from flask import Flask, jsonify, redirect, render_template, request, url_for

from ..gauges import DEFAULT_GAUGES, Gauge
from ..settings import SettingsStore
from ..state import VehicleState


def create_app(
    state: VehicleState,
    store: SettingsStore,
    gauges: tuple[Gauge, ...] = DEFAULT_GAUGES,
) -> Flask:
    app = Flask(__name__)

    @app.get("/")
    def dashboard():
        settings = store.settings
        visible = [g.to_dict() for g in gauges if settings.gauge_visible(g.id)]
        return render_template("dashboard.html", gauges=visible, stale_after=settings.stale_after_s)

    @app.get("/api/state")
    def api_state():
        return jsonify(state.snapshot())

    @app.get("/settings")
    def settings_page():
        return render_template("settings.html", gauges=gauges, settings=store.settings)

    @app.post("/settings")
    def settings_save():
        shown = set(request.form.getlist("shown"))
        hidden = [g.id for g in gauges if g.id not in shown]
        store.save(replace(store.settings, hidden_gauges=hidden))
        return redirect(url_for("dashboard"))

    return app
