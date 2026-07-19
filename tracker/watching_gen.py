"""Film diary pages: docs/watching/<slug>.html from watching/log.json.

Built as part of `python -m tracker reading` (reading_gen.build_all
late-imports this module) so the calendar and the film pages always
ship together. One page per Letterboxd slug covers ALL viewings of
that film (rewatches share a slug); the newest viewing supplies the
poster and headline rating. The log is additive (one-way RSS sync), so
no stale-page cleanup is needed.
"""
from __future__ import annotations

import html
from datetime import date
from pathlib import Path

from . import site
from .letterboxd_sync import load_log, LOG_PATH

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "docs" / "watching"


def films_by_date(films: list) -> dict:
    """{date: [film dict, ...]} for the diary calendar."""
    out: dict = {}
    for f in films:
        if not f.get("watched"):
            continue
        out.setdefault(date.fromisoformat(f["watched"]), []).append(f)
    return out


def films_by_slug(films: list) -> dict:
    """{slug: [viewings sorted by watched asc]}."""
    out: dict = {}
    for f in films:
        if not f.get("slug"):
            continue
        out.setdefault(f["slug"], []).append(f)
    for viewings in out.values():
        viewings.sort(key=lambda f: f.get("watched") or "")
    return out


def _markers(viewing: dict) -> str:
    e = html.escape
    bits = [f"watched {e(viewing.get('watched') or '?')}"]
    if viewing.get("rewatch"):
        bits.append("\u21bb rewatch")
    if viewing.get("liked"):
        bits.append("<span class='heart'>\u2665</span>")
    return " &middot; ".join(bits)


def _review_paras(review: str) -> str:
    e = html.escape
    return "".join(f"<p>{e(p)}</p>" for p in review.split("\n\n") if p.strip())


def render_film(slug: str, viewings: list) -> str:
    from .reading_gen import _page_head, _stars
    e = html.escape
    latest = viewings[-1]
    title = latest.get("title") or slug
    year = latest.get("year")
    heading = f"{title} ({year})" if year else title
    parts = _page_head(heading)
    parts.append(site.nav(None, 1))

    poster = latest.get("poster_url")
    img = (f"<img class='cover' src='{e(poster)}' alt='{e(title)} poster'>"
           if poster else f"<div class='bignoimg'>{e(title)}</div>")
    bits = [f"<h1>{e(heading)}</h1>"]
    if latest.get("rating") is not None:
        bits.append(
            f"<div style='margin-top:6px'>{_stars(latest['rating'])}</div>")
    bits.append(f"<div class='meta' style='margin-top:6px'>"
                f"{_markers(latest)}</div>")
    if latest.get("letterboxd_uri"):
        bits.append(f"<div class='meta' style='margin-top:6px'>"
                    f"<a href='{e(latest['letterboxd_uri'])}'>"
                    "letterboxd \u2197</a></div>")
    parts.append(f"<div class='head'>{img}<div>{''.join(bits)}</div></div>")

    if latest.get("review"):
        parts.append(_review_paras(latest["review"]))

    if len(viewings) > 1:
        parts.append("<h2>Viewings</h2>")
        for v in viewings:
            row = [_markers(v)]
            if v.get("rating") is not None:
                row.append(_stars(v["rating"]))
            parts.append(f"<div style='margin:8px 0'>"
                         f"<div class='meta'>{' &middot; '.join(row)}</div>")
            if v.get("review") and v is not latest:
                parts.append(_review_paras(v["review"]))
            parts.append("</div>")

    parts.append("</body></html>")
    return "".join(parts)


def build_all(log_path: Path = LOG_PATH, out_dir: Path = OUT_DIR,
              log=print) -> list:
    _, films = load_log(log_path) if log_path.exists() else (None, [])
    by_slug = films_by_slug(films)
    written = []
    if by_slug:
        out_dir.mkdir(parents=True, exist_ok=True)
    for slug, viewings in by_slug.items():
        if slug == "index":
            if log:
                log(f"  WARNING: skipping film slug 'index' (reserved)")
            continue
        out = out_dir / f"{slug}.html"
        out.write_text(render_film(slug, viewings), encoding="utf-8")
        written.append(out)
    if log:
        log(f"watching: {len(films)} viewing(s), "
            f"{len(written)} film page(s) -> {out_dir}")
    return written
