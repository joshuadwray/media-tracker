"""Local web app: the friendly layer over the same engine and files.

    python -m tracker web            # http://localhost:8765

Binds 0.0.0.0 so a phone on the same wifi can use it too (find your
computer's LAN IP and open http://<ip>:8765). It edits the same
watchlist.yaml and state files the CLI and scheduled runs use.
"""
from __future__ import annotations

import threading
import webbrowser
from pathlib import Path

from flask import Flask, jsonify, request, send_file

from .cli import candidate_to_entry, search_book_candidates
from .config import load_config
from .engine import CheckRun, run_check
from .sources import build_sources
from .watchlist_io import append_entry, remove_entry

_STATIC = Path(__file__).resolve().parent / "web_static"


def create_app(config_path: str | None = None) -> Flask:
    app = Flask(__name__)
    check_lock = threading.Lock()
    check_status: dict = {"running": False, "last": None}

    def cfg():
        return load_config(config_path)

    def watchlist_path() -> Path:
        return Path(config_path) if config_path else \
            Path(__file__).resolve().parent.parent / "watchlist.yaml"

    @app.get("/")
    def index():
        return send_file(_STATIC / "index.html")

    @app.get("/api/watchlist")
    def get_watchlist():
        c = cfg()
        return jsonify({
            "books": [{"title": b.title, "author": b.author, "isbn": b.isbn,
                       "bib_id": b.bib_id} for b in c.books],
            "movies": [{"title": m.title, "year": m.year} for m in c.movies],
            "sources": [{"id": sid, "kind": s.get("kind"),
                         "enabled": s.get("enabled", True)}
                        for sid, s in c.sources.items()],
        })

    @app.post("/api/watchlist/<section>")
    def add_to_watchlist(section: str):
        if section not in ("books", "movies"):
            return jsonify({"error": "section must be books or movies"}), 400
        data = request.get_json(silent=True) or {}
        title = (data.get("title") or "").strip()
        if not title:
            return jsonify({"error": "title is required"}), 400
        if data.get("candidate"):
            entry = candidate_to_entry(data["candidate"])
        else:
            entry = {"title": title}
            for k in ("author", "isbn", "bib_id"):
                if data.get(k):
                    entry[k] = str(data[k]).strip()
            if section == "movies" and data.get("year"):
                entry = {"title": title, "year": int(data["year"])}
        existing = [b.title for b in cfg().books] if section == "books" \
            else [m.title for m in cfg().movies]
        if entry["title"] in existing:
            return jsonify({"error": f'"{entry["title"]}" is already on the list'}), 409
        if not append_entry(watchlist_path(), section, entry):
            return jsonify({"error": f"no '{section}:' section in watchlist.yaml"}), 500
        return jsonify({"added": entry})

    @app.delete("/api/watchlist/<section>")
    def delete_from_watchlist(section: str):
        if section not in ("books", "movies"):
            return jsonify({"error": "section must be books or movies"}), 400
        title = (request.get_json(silent=True) or {}).get("title", "")
        if not remove_entry(watchlist_path(), section, title):
            return jsonify({"error": f'"{title}" not found in {section}'}), 404
        return jsonify({"removed": title})

    @app.get("/api/search/books")
    def search_books():
        q = (request.args.get("q") or "").strip()
        if not q:
            return jsonify({"error": "q is required"}), 400
        return jsonify({"candidates": search_book_candidates(cfg(), q)})

    @app.post("/api/check")
    def start_check():
        if not check_lock.acquire(blocking=False):
            return jsonify({"error": "a check is already running"}), 409

        def work():
            try:
                run: CheckRun = run_check(cfg())
                check_status["last"] = {
                    "ok": True,
                    "new": [{"item": o.item_label, "summary": o.summary,
                             "url": o.url} for o in run.new],
                    "errors": [{"source": r.source,
                                "error": r.error.strip().splitlines()[0]}
                               for r in run.results if r.error],
                    "observations": sum(len(r.observations) for r in run.results),
                    "pushed": run.pushed,
                    "push_error": run.push_error,
                }
            except Exception as exc:  # noqa: BLE001
                check_status["last"] = {"ok": False, "error": str(exc)}
            finally:
                check_status["running"] = False
                check_lock.release()

        check_status["running"] = True
        threading.Thread(target=work, daemon=True).start()
        return jsonify({"started": True})

    @app.get("/api/check/status")
    def get_check_status():
        return jsonify(check_status)

    @app.get("/api/report")
    def get_report():
        report_path = cfg().state_path.parent / "report.md"
        return jsonify({
            "report": report_path.read_text() if report_path.exists() else None,
        })

    @app.get("/api/probe/<source_id>")
    def probe_source(source_id: str):
        c = cfg()
        matches = [s for s in build_sources(c) if s.source_id == source_id]
        if not matches:
            return jsonify({"error": f"no enabled source '{source_id}'"}), 404
        try:
            return jsonify({"source": source_id, "output": matches[0].probe(c)})
        except Exception as exc:  # noqa: BLE001
            return jsonify({"source": source_id,
                            "output": f"probe failed: {type(exc).__name__}: {exc}"})

    return app


def run_web(config_path: str | None = None, port: int = 8765,
            open_browser: bool = True) -> int:
    load_config(config_path)  # fail fast on a broken watchlist
    app = create_app(config_path)
    url = f"http://localhost:{port}"
    print(f"media-tracker web app: {url}")
    print("(phones on your wifi can use http://<this computer's IP>:%d)" % port)
    if open_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    app.run(host="0.0.0.0", port=port, debug=False)
    return 0
