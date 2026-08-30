"""Renderers for the ``pyimgtag insights`` document (terminal + standalone HTML).

Both renderers take the dict produced by
:meth:`pyimgtag.db.insights_db.InsightsDB.compute` and never touch the
database. The HTML renderer is stdlib-only (no Jinja2) so the CLI report
works with the core install; the *only* filesystem reads it performs are
the thumbnails for the top-scored photos, and those paths come straight
out of the DB (never from user input) and are capped by ``max_thumbs``.
"""

from __future__ import annotations

import base64
import html
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

TERMINAL_WIDTH = 100
DEFAULT_MAX_THUMBS = 10
THUMB_SIZE = 320

ThumbLoader = Callable[[str, int], "bytes | None"]


# --- formatting helpers ----------------------------------------------------


def format_bytes(n: int | float | None) -> str:
    """Human-readable size (``1.2 GB``); ``0 B`` for ``None``."""
    size = float(n or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"  # pragma: no cover — loop always returns


def _date(value: str | None) -> str:
    return (value or "")[:10] or "–"


def _pct(value: float | None) -> str:
    return f"{value:.1f}%" if value is not None else "–"


# --- terminal --------------------------------------------------------------


def _bar(count: int, total: int, width: int = 24) -> str:
    if total <= 0:
        return ""
    filled = round(width * count / total)
    return "█" * filled + "·" * (width - filled)


def _term_section(title: str) -> list[str]:
    return ["", title, "-" * len(title)]


def _term_top_list(rows: list[dict], label_key: str = "value", width: int = 28) -> list[str]:
    if not rows:
        return ["  (none)"]
    top = max(r["count"] for r in rows)
    out = []
    for r in rows:
        label = str(r[label_key])
        if len(label) > width:
            label = label[: width - 1] + "…"
        out.append(f"  {label:<{width}} {r['count']:>7}  {_bar(r['count'], top)}")
    return out


def render_terminal(doc: dict) -> str:
    """Render the insights document as a compact ≤100-column text report."""
    lines: list[str] = ["pyimgtag library insights", "=" * 25]
    ov = doc["overview"]
    if doc.get("empty"):
        lines += [
            "",
            "Nothing tagged yet — run `pyimgtag run --input-dir <folder>` first,",
            "then come back for the report.",
        ]
        return "\n".join(lines) + "\n"

    lines += _term_section("Overview")
    lines.append(f"  Photos in DB      {ov['total']:>9}   ({ov['ok']} ok, {ov['error']} error)")
    lines.append(f"  Size on disk      {format_bytes(ov['size_bytes']):>9}")
    lines.append(f"  Date span         {_date(ov['oldest'])} → {_date(ov['newest'])}")
    if len(ov["by_status"]) > 2:
        extra = ", ".join(
            f"{k}={v}" for k, v in ov["by_status"].items() if k not in ("ok", "error")
        )
        lines.append(f"  Other statuses    {extra}")

    time_ = doc.get("time")
    if time_:
        lines += _term_section("Time")
        lines.append(f"  Dated photos      {time_['dated']}")
        if time_["busiest_month"]:
            bm = time_["busiest_month"]
            lines.append(f"  Busiest month     {bm['period']} ({bm['count']} photos)")
        if time_["busiest_day"]:
            bd = time_["busiest_day"]
            lines.append(f"  Busiest day       {bd['period']} ({bd['count']} photos)")
        lines.append("  Per year:")
        lines += _term_top_list(
            [{"value": r["period"], "count": r["count"]} for r in time_["per_year"]], width=10
        )

    places = doc.get("places")
    if places:
        lines += _term_section("Places")
        lines.append(
            f"  GPS/location coverage  {_pct(places['coverage_pct'])} ({places['located']} photos)"
        )
        lines.append("  Top countries:")
        lines += _term_top_list(places["countries"])
        lines.append("  Top cities:")
        lines += _term_top_list(places["cities"])

    content = doc.get("content")
    if content:
        lines += _term_section("Content")
        lines.append(f"  Unique tags       {content['unique_tags']}")
        lines.append(f"  Photos with text  {content['has_text']} ({_pct(content['has_text_pct'])})")
        lines.append("  Top tags:")
        lines += _term_top_list(content["top_tags"])
        if content["scene_categories"]:
            lines.append("  Scene categories:")
            lines += _term_top_list(content["scene_categories"])
        if content["emotional_tones"]:
            lines.append("  Emotional tones:")
            lines += _term_top_list(content["emotional_tones"])
        if content["event_hints"]:
            lines.append("  Event hints:")
            lines += _term_top_list(content["event_hints"])

    people = doc.get("people")
    if people:
        lines += _term_section("People")
        lines.append(f"  Named people      {people['named_persons']}   ({people['faces']} faces)")
        lines += _term_top_list(
            [{"value": p["label"], "count": p["photos"]} for p in people["top_people"]]
        )

    quality = doc.get("quality")
    if quality:
        lines += _term_section("Quality")
        lines.append(
            f"  Judged photos     {quality['judged']} ({_pct(quality['coverage_pct'])} coverage)"
        )
        if quality["average"] is not None:
            lines.append(f"  Average score     {quality['average']:.2f} / 10")
        lines.append("  Score distribution:")
        hist = quality["histogram"]
        lines += _term_top_list(
            [{"value": f"{k}/10", "count": v} for k, v in hist.items()], width=6
        )
        lines.append("  Top photos:")
        for i, p in enumerate(quality["top_photos"], 1):
            name = Path(p["file_path"]).name
            score = p["score"]
            score_str = f"{score:g}" if isinstance(score, (int, float)) else str(score)
            lines.append(f"  {i:>2}. {score_str:>4}/10  {name[:70]}")

    hk = doc.get("housekeeping")
    if hk:
        lines += _term_section("Housekeeping")
        dc, rc = hk["delete_candidates"], hk["review_candidates"]
        lines.append(
            f"  Cleanup: delete   {dc['count']:>7}  ({format_bytes(dc['bytes'])} reclaimable)"
        )
        lines.append(f"  Cleanup: review   {rc['count']:>7}  ({format_bytes(rc['bytes'])})")
        lines.append(f"  Untagged (ok)     {hk['untagged']:>7}")
        lines.append(f"  Errors            {hk['errors']:>7}")

    return "\n".join(line[:TERMINAL_WIDTH] for line in lines) + "\n"


# --- HTML ------------------------------------------------------------------

_HTML_CSS = """
:root{--bg:#f5f5f7;--surface:#fff;--border:rgba(0,0,0,.08);--accent:#0071e3;
      --text:#1d1d1f;--muted:#86868b;--ok:#34c759;--warn:#ff9f0a;--danger:#ff3b30}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:system-ui,-apple-system,'Segoe UI',sans-serif;background:var(--bg);
     color:var(--text);padding:32px;max-width:1100px;margin:0 auto}
h1{font-size:28px;letter-spacing:-.5px;margin-bottom:4px}
.sub{color:var(--muted);font-size:13px;margin-bottom:28px}
h2{font-size:18px;margin:32px 0 12px;letter-spacing:-.3px}
.cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:12px}
.card{background:var(--surface);border-radius:12px;padding:16px 20px;
      box-shadow:0 1px 4px rgba(0,0,0,.06)}
.val{font-size:28px;font-weight:700;letter-spacing:-.5px}
.lbl{font-size:12px;color:var(--muted);margin-top:2px}
.grid2{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:16px}
table{width:100%;border-collapse:collapse;background:var(--surface);border-radius:12px;
      overflow:hidden;box-shadow:0 1px 4px rgba(0,0,0,.06)}
th{padding:8px 14px;text-align:left;font-size:11px;color:var(--muted);text-transform:uppercase;
   letter-spacing:.5px;border-bottom:1px solid var(--border)}
td{padding:7px 14px;font-size:13px;border-bottom:1px solid rgba(0,0,0,.04)}
td.n{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.bar{height:6px;background:rgba(0,0,0,.06);border-radius:3px;overflow:hidden;min-width:80px}
.bar i{display:block;height:100%;background:var(--accent);border-radius:3px}
.hist{display:flex;align-items:flex-end;gap:6px;height:120px;padding:8px 0}
.hist div{flex:1;background:var(--accent);border-radius:4px 4px 0 0;position:relative;
          min-height:2px}
.hist div span{position:absolute;bottom:-18px;left:0;right:0;text-align:center;
               font-size:11px;color:var(--muted)}
.hist div b{position:absolute;top:-16px;left:0;right:0;text-align:center;font-size:10px;
            color:var(--muted);font-weight:500}
.photos{display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:12px}
.photo{background:var(--surface);border-radius:12px;overflow:hidden;
       box-shadow:0 1px 4px rgba(0,0,0,.06)}
.photo img{width:100%;aspect-ratio:4/3;object-fit:cover;display:block;background:#e5e5ea}
.photo .ph{width:100%;aspect-ratio:4/3;display:flex;align-items:center;justify-content:center;
           color:var(--muted);font-size:11px;background:#ececf0;padding:8px;text-align:center;
           word-break:break-all}
.photo .body{padding:8px 10px 10px;font-size:12px}
.photo .score{font-weight:700;color:var(--accent)}
.photo .name{color:var(--muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.empty{background:var(--surface);border-radius:12px;padding:40px;text-align:center;
       color:var(--muted)}
footer{margin-top:40px;font-size:11px;color:var(--muted)}
"""


def _e(value: Any) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def _card(val: Any, label: str) -> str:
    return (
        f'<div class="card"><div class="val">{_e(val)}</div>'
        f'<div class="lbl">{_e(label)}</div></div>'
    )


def _table(title: str, rows: list[dict], label_key: str = "value") -> str:
    if not rows:
        return ""
    top = max(r["count"] for r in rows) or 1
    body = "".join(
        f"<tr><td>{_e(r[label_key])}</td><td class='n'>{r['count']}</td>"
        f"<td><div class='bar'><i style='width:{100 * r['count'] / top:.0f}%'></i></div></td></tr>"
        for r in rows
    )
    return (
        f"<div><h3 style='font-size:13px;color:var(--muted);margin:0 0 6px'>{_e(title)}</h3>"
        f"<table><tbody>{body}</tbody></table></div>"
    )


def _histogram(hist: dict[str, int]) -> str:
    top = max(hist.values()) or 1
    bars = "".join(
        f"<div style='height:{100 * v / top:.0f}%'><b>{v}</b><span>{_e(k)}</span></div>"
        for k, v in hist.items()
    )
    return f'<div class="hist">{bars}</div><div style="height:20px"></div>'


def _thumb_data_uri(path: str, loader: ThumbLoader | None) -> str | None:
    if loader is None:
        return None
    try:
        data = loader(path, THUMB_SIZE)
    except Exception as exc:  # noqa: BLE001 — a bad thumbnail must never break the report
        logger.debug("insights thumbnail failed for %s: %s", path, exc)
        return None
    if not data:
        return None
    return "data:image/jpeg;base64," + base64.b64encode(data).decode("ascii")


def render_html(
    doc: dict,
    *,
    thumb_loader: ThumbLoader | None = None,
    max_thumbs: int = DEFAULT_MAX_THUMBS,
    generated_at: str | None = None,
) -> str:
    """Render the insights document as a single self-contained HTML page.

    Args:
        doc: Output of :meth:`InsightsDB.compute`.
        thumb_loader: Optional ``(path, size) -> jpeg bytes | None`` used to
            inline thumbnails for the top-scored photos. Only paths present
            in ``doc["quality"]["top_photos"]`` (i.e. DB-known files) are
            ever passed to it. ``None`` renders placeholders instead.
        max_thumbs: Cap on inlined thumbnails so the report stays small.
        generated_at: Timestamp shown in the footer (defaults to now, UTC).

    Returns:
        HTML text with all CSS inline and no external URLs.
    """
    from datetime import datetime, timezone

    ov = doc["overview"]
    stamp = generated_at or datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    parts: list[str] = [
        "<!DOCTYPE html><html lang='en'><head><meta charset='utf-8'>",
        "<meta name='viewport' content='width=device-width, initial-scale=1'>",
        "<title>pyimgtag library insights</title>",
        f"<style>{_HTML_CSS}</style></head><body>",
        "<h1>Library insights</h1>",
        f"<div class='sub'>Generated {_e(stamp)} by pyimgtag</div>",
    ]

    if doc.get("empty"):
        parts.append(
            "<div class='empty'><p style='font-size:17px;margin-bottom:8px'>Nothing tagged yet"
            "</p><p>Run <code>pyimgtag run --input-dir &lt;folder&gt;</code> and come back "
            "for the report.</p></div>"
        )
        parts.append(f"<footer>pyimgtag insights · schema v{doc['schema_version']}</footer>")
        parts.append("</body></html>")
        return "".join(parts)

    parts.append("<h2>Overview</h2><div class='cards'>")
    parts.append(_card(f"{ov['total']:,}", "photos in database"))
    parts.append(_card(f"{ov['ok']:,}", "tagged ok"))
    parts.append(_card(f"{ov['error']:,}", "errors"))
    parts.append(_card(format_bytes(ov["size_bytes"]), "size on disk"))
    parts.append(_card(_date(ov["oldest"]), "oldest photo"))
    parts.append(_card(_date(ov["newest"]), "newest photo"))
    parts.append("</div>")

    time_ = doc.get("time")
    if time_:
        parts.append("<h2>Time</h2><div class='cards'>")
        parts.append(_card(f"{time_['dated']:,}", "photos with a capture date"))
        if time_["busiest_month"]:
            bm = time_["busiest_month"]
            parts.append(_card(bm["period"], f"busiest month ({bm['count']} photos)"))
        if time_["busiest_day"]:
            bd = time_["busiest_day"]
            parts.append(_card(bd["period"], f"busiest day ({bd['count']} photos)"))
        parts.append("</div><div class='grid2' style='margin-top:16px'>")
        parts.append(
            _table(
                "Photos per year",
                [{"value": r["period"], "count": r["count"]} for r in time_["per_year"]],
            )
        )
        months = time_["per_month"]
        parts.append(
            _table(
                "Photos per month (last 24)",
                [{"value": r["period"], "count": r["count"]} for r in months[-24:]],
            )
        )
        parts.append("</div>")

    places = doc.get("places")
    if places:
        parts.append("<h2>Places</h2><div class='cards'>")
        parts.append(_card(_pct(places["coverage_pct"]), "location coverage"))
        parts.append(_card(f"{places['located']:,}", "photos with a place"))
        parts.append("</div><div class='grid2' style='margin-top:16px'>")
        parts.append(_table("Top countries", places["countries"]))
        parts.append(_table("Top regions", places["regions"]))
        parts.append(_table("Top cities", places["cities"]))
        parts.append("</div>")

    content = doc.get("content")
    if content:
        parts.append("<h2>Content</h2><div class='cards'>")
        parts.append(_card(f"{content['unique_tags']:,}", "unique tags"))
        parts.append(_card(_pct(content["has_text_pct"]), "photos containing text"))
        parts.append("</div><div class='grid2' style='margin-top:16px'>")
        parts.append(_table("Top tags", content["top_tags"]))
        parts.append(_table("Scene categories", content["scene_categories"]))
        parts.append(_table("Emotional tones", content["emotional_tones"]))
        parts.append(_table("Event hints", content["event_hints"]))
        parts.append("</div>")

    people = doc.get("people")
    if people:
        parts.append("<h2>People</h2><div class='cards'>")
        parts.append(_card(f"{people['named_persons']:,}", "named people"))
        parts.append(_card(f"{people['faces']:,}", "faces detected"))
        parts.append("</div><div class='grid2' style='margin-top:16px'>")
        parts.append(
            _table(
                "Top people by photo count",
                [{"value": p["label"], "count": p["photos"]} for p in people["top_people"]],
            )
        )
        parts.append("</div>")

    quality = doc.get("quality")
    if quality:
        parts.append("<h2>Quality</h2><div class='cards'>")
        parts.append(_card(f"{quality['judged']:,}", "judged photos"))
        parts.append(_card(_pct(quality["coverage_pct"]), "judge coverage"))
        if quality["average"] is not None:
            parts.append(_card(f"{quality['average']:.2f}", "average score / 10"))
        parts.append("</div>")
        parts.append(
            "<h3 style='font-size:13px;color:var(--muted);margin:16px 0 4px'>"
            "Score distribution</h3>"
        )
        parts.append(_histogram(quality["histogram"]))
        parts.append(
            "<h3 style='font-size:13px;color:var(--muted);margin:16px 0 8px'>Top photos</h3>"
            "<div class='photos'>"
        )
        for idx, p in enumerate(quality["top_photos"]):
            name = Path(p["file_path"]).name
            uri = _thumb_data_uri(p["file_path"], thumb_loader) if idx < max_thumbs else None
            img = (
                f"<img src='{uri}' alt='{_e(name)}'>"
                if uri
                else f"<div class='ph'>{_e(name)}</div>"
            )
            score = p["score"]
            score_str = f"{score:g}" if isinstance(score, (int, float)) else _e(score)
            parts.append(
                f"<div class='photo'>{img}<div class='body'>"
                f"<span class='score'>{score_str}/10</span> "
                f"<span class='name' title='{_e(p['file_path'])}'>{_e(name)}</span>"
                + (f"<div>{_e(p['reason'])}</div>" if p.get("reason") else "")
                + "</div></div>"
            )
        parts.append("</div>")

    hk = doc.get("housekeeping")
    if hk:
        dc, rc = hk["delete_candidates"], hk["review_candidates"]
        parts.append("<h2>Housekeeping</h2><div class='cards'>")
        parts.append(_card(f"{dc['count']:,}", f"delete candidates · {format_bytes(dc['bytes'])}"))
        parts.append(_card(f"{rc['count']:,}", f"review candidates · {format_bytes(rc['bytes'])}"))
        parts.append(_card(f"{hk['untagged']:,}", "tagged ok but no tags"))
        parts.append(_card(f"{hk['errors']:,}", "processing errors"))
        parts.append("</div>")

    parts.append(f"<footer>pyimgtag insights · schema v{doc['schema_version']}</footer>")
    parts.append("</body></html>")
    return "".join(parts)
