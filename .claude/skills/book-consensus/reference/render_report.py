#!/usr/bin/env python3
"""Render a book-consensus markdown report into a readable standalone HTML page.

Usage: render_report.py REPORT.md [OUT.html]

Handles the subset of markdown the report schema uses: headings, paragraphs,
bold/italic, inline links, bullet/ordered lists, blockquotes, hr, and GFM
tables. No third-party dependencies.
"""
import html
import json
import re
import sys
from datetime import datetime
from pathlib import Path

INLINE = re.compile(
    r"(\*\*.+?\*\*"          # bold
    r"|\*[^*]+?\*"           # italic
    r"|\[[^\]]+\]\([^)]+\)"  # link
    r"|`[^`]+?`)"            # code
)


def inline(text: str) -> str:
    out, pos = [], 0
    for m in INLINE.finditer(text):
        out.append(html.escape(text[pos:m.start()]))
        tok = m.group(0)
        if tok.startswith("**"):
            out.append(f"<strong>{html.escape(tok[2:-2])}</strong>")
        elif tok.startswith("`"):
            out.append(f"<code>{html.escape(tok[1:-1])}</code>")
        elif tok.startswith("["):
            label, url = tok[1:-1].split("](", 1)
            out.append(
                f'<a href="{html.escape(url, quote=True)}" target="_blank" '
                f'rel="noopener">{html.escape(label)}</a>'
            )
        else:
            out.append(f"<em>{html.escape(tok[1:-1])}</em>")
        pos = m.end()
    out.append(html.escape(text[pos:]))
    return "".join(out)


def split_row(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def convert(md: str) -> str:
    lines = md.split("\n")
    body: list[str] = []
    i, n = 0, len(lines)
    while i < n:
        line = lines[i]
        raw = line.strip()

        if not raw:
            i += 1
            continue

        # table: header row followed by a |---| separator
        if raw.startswith("|") and i + 1 < n and re.match(r"^\|[\s:|-]+\|$", lines[i + 1].strip()):
            heads = split_row(raw)
            i += 2
            rows = []
            while i < n and lines[i].strip().startswith("|"):
                rows.append(split_row(lines[i].strip()))
                i += 1
            t = ["<div class='tw'><table><thead><tr>"]
            t += [f"<th>{inline(h)}</th>" for h in heads]
            t.append("</tr></thead><tbody>")
            for r in rows:
                r += [""] * (len(heads) - len(r))
                cls = " class='tdeep'" if "deep" in r[3].lower() else ""
                t.append(f"<tr{cls}>")
                t += [f"<td>{inline(c)}</td>" for c in r[: len(heads)]]
                t.append("</tr>")
            t.append("</tbody></table></div>")
            body.append("".join(t))
            continue

        if raw == "---":
            body.append("<hr>")
            i += 1
            continue

        m = re.match(r"^(#{1,4})\s+(.*)$", raw)
        if m:
            lvl = len(m.group(1))
            body.append(f"<h{lvl}>{inline(m.group(2))}</h{lvl}>")
            i += 1
            continue

        if raw.startswith(">"):
            body.append(f"<blockquote>{inline(raw.lstrip('> ').strip())}</blockquote>")
            i += 1
            continue

        if re.match(r"^[-*]\s+", raw):
            items = []
            while i < n and re.match(r"^[-*]\s+", lines[i].strip()):
                items.append(f"<li>{inline(lines[i].strip()[2:])}</li>")
                i += 1
            body.append("<ul>" + "".join(items) + "</ul>")
            continue

        if re.match(r"^\d+[.)]\s+", raw):
            items = []
            while i < n and re.match(r"^\d+[.)]\s+", lines[i].strip()):
                txt = re.sub(r"^\d+[.)]\s+", "", lines[i].strip())
                items.append(f"<li>{inline(txt)}</li>")
                i += 1
            body.append("<ol>" + "".join(items) + "</ol>")
            continue

        # paragraph (may span lines)
        para = [raw]
        i += 1
        while i < n and lines[i].strip() and not re.match(
            r"^(#|>|\||[-*]\s|\d+[.)]\s|---)", lines[i].strip()
        ):
            para.append(lines[i].strip())
            i += 1
        body.append(f"<p>{inline(' '.join(para))}</p>")
    return "\n".join(body)


TEMPLATE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
  :root {{ --ink:#1b1a18; --muted:#6d6a63; --rule:#e0ddd4; --bg:#fbfaf7;
           --accent:#7a5c2e; --deep:#f4f1e8; }}
  @media (prefers-color-scheme: dark) {{
    :root {{ --ink:#e8e4dc; --muted:#9c978c; --rule:#33312c; --bg:#161514;
             --accent:#d0ab6b; --deep:#1e1d1a; }}
  }}
  * {{ box-sizing: border-box; }}
  body {{ margin:0; background:var(--bg); color:var(--ink);
    font:16px/1.65 -apple-system, BlinkMacSystemFont, "Iowan Old Style", Georgia, serif;
    -webkit-font-smoothing:antialiased; }}
  main {{ max-width: 46rem; margin:0 auto; padding: 3.5rem 1.5rem 6rem; }}
  h1 {{ font-size:1.9rem; line-height:1.25; letter-spacing:-0.01em; margin:0 0 .4rem; }}
  h2 {{ font-size:1.02rem; text-transform:uppercase; letter-spacing:.09em;
        color:var(--accent); margin:2.8rem 0 .9rem; padding-bottom:.4rem;
        border-bottom:1px solid var(--rule); font-family:ui-sans-serif,system-ui,sans-serif; }}
  h3 {{ font-size:1.05rem; margin:1.8rem 0 .5rem; }}
  p, li {{ margin:.65rem 0; }}
  em {{ color:var(--ink); }}
  strong {{ font-weight:600; }}
  a {{ color:var(--accent); text-decoration-thickness:1px; text-underline-offset:2px; }}
  a:hover {{ text-decoration-style:dotted; }}
  code {{ font:0.86em ui-monospace,SFMono-Regular,Menlo,monospace;
          background:var(--deep); padding:.1em .35em; border-radius:3px; }}
  blockquote {{ margin:1rem 0; padding:.2rem 0 .2rem 1rem;
                border-left:2px solid var(--accent); color:var(--muted); }}
  hr {{ border:0; border-top:1px solid var(--rule); margin:2.5rem 0; }}
  .tw {{ overflow-x:auto; margin:1.1rem 0; }}
  table {{ border-collapse:collapse; width:100%; table-layout:fixed;
           font-size:.86rem; font-family:ui-sans-serif,system-ui,sans-serif; }}
  th, td {{ overflow-wrap:normal; }}
  td:last-child, th:last-child {{ overflow-wrap:anywhere; }}
  th:nth-child(1), td:nth-child(1) {{ width:13%; }}
  th:nth-child(2), td:nth-child(2) {{ width:15%; }}
  th:nth-child(3), td:nth-child(3) {{ width:12%; }}
  th:nth-child(4), td:nth-child(4) {{ width:8%; }}
  th:nth-child(5), td:nth-child(5) {{ width:52%; }}
  th {{ text-align:left; font-size:.72rem; text-transform:uppercase;
        letter-spacing:.07em; color:var(--muted); font-weight:600;
        border-bottom:1px solid var(--ink); padding:.5rem .7rem .5rem 0; }}
  td {{ vertical-align:top; padding:.75rem .7rem .75rem 0;
        border-bottom:1px solid var(--rule); line-height:1.5; }}
  tr.tdeep td {{ background:var(--deep); }}
  td:nth-child(3) {{ white-space:normal; }}
  .foot {{ margin-top:4rem; padding-top:1rem; border-top:1px solid var(--rule);
           color:var(--muted); font-size:.78rem;
           font-family:ui-sans-serif,system-ui,sans-serif; }}
</style></head>
<body><main>
{body}
<p class="foot">Rendered from {src} · book-consensus skill</p>
</main></body></html>
"""


def stats(md: str, out: Path) -> dict:
    """Source counts + title, written alongside the report for the index."""
    rows = re.findall(r"^\|(?![-|]).*$", md, re.M)
    rows = [r for r in rows if not re.match(r"^\|\s*(outlet|title)\b", r.strip(), re.I)]
    deep = sum(1 for r in rows if re.search(r"\|\s*\**deep\b", r, re.I))
    h1 = re.search(r"^#\s+(.*)$", md, re.M)
    title = re.sub(r"[*`]", "", h1.group(1)) if h1 else out.stem
    # drop the leading "Critical Consensus: " so the index stays scannable
    title = re.sub(r"^Critical Consensus:\s*", "", title)
    return {"title": title, "sources": len(rows), "deep": deep,
            "file": out.name, "rendered_at": datetime.now().isoformat(timespec="seconds")}


def write_index(outdir: Path) -> Path:
    """Regenerate index.html — the one page to open in the desktop preview pane."""
    metas = []
    for p in sorted(outdir.glob("*.meta.json")):
        try:
            m = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if (outdir / m.get("file", "")).exists():
            metas.append(m)
    metas.sort(key=lambda m: m.get("rendered_at", ""), reverse=True)
    items = "\n".join(
        f'<li><a href="{html.escape(m["file"], quote=True)}">{html.escape(m["title"])}</a>'
        f'<span class="n">{m.get("sources", "?")} sources · {m.get("deep", "?")} deep · '
        f'{html.escape(str(m.get("rendered_at", ""))[:10])}</span></li>'
        for m in metas) or '<li class="empty">No reports yet.</li>'
    out = outdir / "index.html"
    out.write_text(INDEX_TEMPLATE.format(count=len(metas), items=items), encoding="utf-8")
    return out


INDEX_TEMPLATE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Critical consensus reports</title>
<style>
  :root {{ --ink:#1b1a18; --muted:#6d6a63; --rule:#e0ddd4; --bg:#fbfaf7; --accent:#7a5c2e; }}
  @media (prefers-color-scheme: dark) {{ :root {{ --ink:#e8e4dc; --muted:#9c978c;
    --rule:#33312c; --bg:#161514; --accent:#d0ab6b; }} }}
  body {{ margin:0; background:var(--bg); color:var(--ink); -webkit-font-smoothing:antialiased;
    font:15px/1.6 ui-sans-serif,system-ui,-apple-system,sans-serif; }}
  main {{ max-width:40rem; margin:0 auto; padding:3rem 1.5rem 5rem; }}
  h1 {{ font-size:1.3rem; margin:0 0 .3rem; }}
  p.sub {{ color:var(--muted); font-size:.85rem; margin:0 0 2rem; }}
  ul {{ list-style:none; margin:0; padding:0; }}
  li {{ border-bottom:1px solid var(--rule); padding:1rem 0;
        display:flex; flex-direction:column; gap:.25rem; }}
  li.empty {{ color:var(--muted); }}
  a {{ color:var(--accent); text-decoration:none; font-family:Georgia,serif; font-size:1.05rem; }}
  a:hover {{ text-decoration:underline; text-underline-offset:2px; }}
  .n {{ color:var(--muted); font-size:.8rem; }}
</style></head>
<body><main>
<h1>Critical consensus reports</h1>
<p class="sub">{count} report(s) · book-consensus skill · newest first</p>
<ul>
{items}
</ul></main></body></html>
"""


def main() -> None:
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    src = Path(sys.argv[1]).expanduser()
    md = src.read_text(encoding="utf-8")
    out = Path(sys.argv[2]).expanduser() if len(sys.argv) > 2 else src.with_suffix(".html")
    out.parent.mkdir(parents=True, exist_ok=True)
    h1 = re.search(r"^#\s+(.*)$", md, re.M)
    title = re.sub(r"[*`]", "", h1.group(1)) if h1 else src.stem
    out.write_text(
        TEMPLATE.format(title=html.escape(title), body=convert(md), src=html.escape(src.name)),
        encoding="utf-8",
    )
    out.with_suffix(".meta.json").write_text(
        json.dumps(stats(md, out), indent=2), encoding="utf-8")
    print(out)
    print(write_index(out.parent))


if __name__ == "__main__":
    main()
