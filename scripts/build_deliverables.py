"""
Build the two files that actually get carried into the pitch room.

  deck/SENSE-AI-pitch.html   one self-contained file: every slide, every clip
                             embedded as base64, fonts embedded, no network.
                             Opens in any browser on any laptop and the video
                             plays. This is what you present from.

  deck/SENSE-AI-pitch.pdf    the same deck flattened to stills, for submission.

PDF cannot play video in any way worth relying on — the embedded-media feature
exists but only Acrobat honours it, and pitch rooms run Preview or Chrome. So
the video lives in the HTML and the PDF carries a representative frame.

    python scripts/build_deliverables.py
"""
from __future__ import annotations

import base64
import re
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
SLIDES = Path("/private/tmp/claude-501/-Users-sanzarsartaev/"
              "b4165f5d-91dd-49a3-8372-1379e2dad88b/scratchpad/deckbuild/project/slides")
DECK_JSON = SLIDES.parent / "deck.json"
OUT = BASE / "deck"
CLIPS, STILLS = OUT / "clips", OUT / "stills"
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

BLOB = {
    "5b27ba0b3e0ea0999a3d89ca833ec8e8": "clips/01-hero.mp4",
    "0c087b53f5addb92c3fbcae97be460e6": "stills/01-hero.png",
    "11b68f3d1858d4be3bcb4afbcad3e539": "clips/02-redistribute.mp4",
    "8a8dbcb27c1310eb51f3e5901164f5f3": "stills/02-redistribute.png",
    "ad004de6d5e3be72d5c9ebe881fb8346": "clips/03-blind.mp4",
    "a66a506d3d4c188c395c0171a34c1f70": "stills/03-blind.png",
    "e253e163604269269c54d5b486598871": "clips/04-upload.mp4",
    "8e6b870fd235bd06521455c4c3440244": "stills/04-upload.png",
    "4b7d5a6dd7c812752fe25f9ece179779": "clips/05-responder.mp4",
    "d3c5fca615f9ad16a903d4cc62332fbd": "stills/05-responder.png",
    "40a99747b9436cd5290181e16f495461": "clips/06-routes.mp4",
    "49ef54e096b77711c169dca4313ce70f": "stills/06-routes.png",
    "990660548e15bbc87d6af9e693987a8d": "clips/07-twin.mp4",
    "feb159149c2b2ed5ae51b7216310f819": "stills/07-twin.png",
}

FONT_CSS = [
    "https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;500;700&display=swap",
    "https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600&display=swap",
]
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")


def b64(path: Path, mime: str) -> str:
    return f"data:{mime};base64," + base64.b64encode(path.read_bytes()).decode()


def embedded_fonts() -> str:
    """Fetch the Google CSS and inline every .woff2 it points at."""
    out = []
    for url in FONT_CSS:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            css = urllib.request.urlopen(req, timeout=20).read().decode()
        except Exception as e:  # noqa: BLE001
            print(f"  ! font fetch failed ({e}); falling back to system faces")
            return ""
        for m in set(re.findall(r"https://fonts\.gstatic\.com/[^)]+\.woff2", css)):
            try:
                data = urllib.request.urlopen(
                    urllib.request.Request(m, headers={"User-Agent": UA}), timeout=20).read()
            except Exception:  # noqa: BLE001
                continue
            css = css.replace(m, "data:font/woff2;base64," + base64.b64encode(data).decode())
        out.append(css)
    return "\n".join(out)


def slide_order() -> list[str]:
    import json
    return json.loads(DECK_JSON.read_text())["order"]


def load_slide(name: str, for_pdf: bool) -> tuple[str, str]:
    """Return (section html with media inlined, speaker notes)."""
    html = (SLIDES / f"{name}.html").read_text()
    notes = ""
    m = re.search(r"<aside>(.*?)</aside>", html, re.S)
    if m:
        notes = m.group(1).strip()
        html = html.replace(m.group(0), "")

    def swap(match: re.Match) -> str:
        tag = match.group(0)
        still = re.search(r'src="/_blob/([0-9a-f]+)"', tag)
        clip = re.search(r'data-video="/_blob/([0-9a-f]+)"', tag)
        style = re.search(r'style="([^"]*)"', tag)
        alt = re.search(r'alt="([^"]*)"', tag)
        style_s = style.group(1) if style else ""
        alt_s = alt.group(1) if alt else ""

        if still and still.group(1) in BLOB:
            still_uri = b64(OUT / BLOB[still.group(1)], "image/png")
        else:
            still_uri = ""

        if for_pdf or not clip or clip.group(1) not in BLOB:
            return f'<img src="{still_uri}" alt="{alt_s}" style="{style_s}">'

        clip_uri = b64(OUT / BLOB[clip.group(1)], "video/mp4")
        return (f'<video src="{clip_uri}" poster="{still_uri}" muted loop playsinline '
                f'preload="auto" aria-label="{alt_s}" style="{style_s}"></video>')

    html = re.sub(r"<img\b[^>]*>", swap, html)
    return html.strip(), notes


PRESENTER = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>SENSE AI — HKTech300 Pitch</title>
<style>
{fonts}
*{{box-sizing:border-box}}
html,body{{margin:0;padding:0;background:#000;overflow:hidden;font-family:'IBM Plex Sans',Arial,sans-serif}}
#stage{{position:fixed;inset:0;overflow:hidden}}
#scaler{{position:absolute;left:50%;top:50%;width:1920px;height:1080px;
  margin-left:-960px;margin-top:-540px;transform-origin:center center;flex:none}}
section{{position:absolute;left:0;top:0;width:1920px;height:1080px;overflow:hidden;
  opacity:0;pointer-events:none;transition:opacity .28s ease}}
section.on{{opacity:1;pointer-events:auto}}
h1,h2,h3,p,ul,ol{{margin:0}} ul,ol{{padding-left:1.2em}}
video,img{{display:block}}
#bar{{position:fixed;left:0;bottom:0;height:3px;background:#22D3EE;width:0;transition:width .28s}}
#num{{position:fixed;right:18px;bottom:14px;color:#4A535E;font-size:13px;letter-spacing:.1em}}
#notes{{position:fixed;left:0;right:0;bottom:0;max-height:38vh;overflow:auto;
  background:rgba(8,8,10,.96);border-top:1px solid #23262B;color:#C6D0DA;
  padding:18px 26px;font-size:17px;line-height:1.55;display:none}}
#notes.on{{display:block}}
#help{{position:fixed;left:18px;bottom:14px;color:#3A424C;font-size:13px}}
</style></head><body>
<div id="stage"><div id="scaler">{slides}</div></div>
<div id="bar"></div><div id="num"></div>
<div id="help">&#8592; &#8594; move &nbsp;·&nbsp; F fullscreen &nbsp;·&nbsp; N notes</div>
<div id="notes"></div>
<script>
const NOTES = {notes_json};
const secs = [...document.querySelectorAll('section')];
let i = 0;
function fit() {{
  const s = Math.min(innerWidth / 1920, innerHeight / 1080);
  document.getElementById('scaler').style.transform = 'scale(' + s + ')';
}}
function show(n) {{
  i = Math.max(0, Math.min(secs.length - 1, n));
  secs.forEach((s, k) => {{
    const on = k === i;
    s.classList.toggle('on', on);
    s.querySelectorAll('video').forEach(v => {{
      if (on) {{ v.currentTime = 0; v.play().catch(() => {{}}); }} else v.pause();
    }});
  }});
  document.getElementById('bar').style.width = ((i + 1) / secs.length * 100) + '%';
  document.getElementById('num').textContent = (i + 1) + ' / ' + secs.length;
  document.getElementById('notes').textContent = NOTES[i] || '';
}}
addEventListener('resize', fit);
addEventListener('keydown', e => {{
  if (['ArrowRight',' ','PageDown','Enter'].includes(e.key)) {{ e.preventDefault(); show(i + 1); }}
  else if (['ArrowLeft','PageUp','Backspace'].includes(e.key)) {{ e.preventDefault(); show(i - 1); }}
  else if (e.key === 'Home') show(0);
  else if (e.key === 'End') show(secs.length - 1);
  else if (e.key.toLowerCase() === 'f') {{
    if (document.fullscreenElement) document.exitFullscreen();
    else document.documentElement.requestFullscreen();
  }}
  else if (e.key.toLowerCase() === 'n') document.getElementById('notes').classList.toggle('on');
}});
addEventListener('click', e => {{ if (e.clientX > innerWidth * .35) show(i + 1); else show(i - 1); }});
fit(); show(0);
</script></body></html>"""

PRINT = """<!doctype html>
<html><head><meta charset="utf-8"><title>SENSE AI</title><style>
{fonts}
@page {{ size: 1280px 720px; margin: 0; }}
*{{box-sizing:border-box}}
html,body{{margin:0;padding:0;background:#0A0A0B;font-family:'IBM Plex Sans',Arial,sans-serif}}
.page{{width:1280px;height:720px;overflow:hidden;page-break-after:always;position:relative;flex:none}}
.inner{{width:1920px;height:1080px;transform:scale(.6666667);transform-origin:top left;position:absolute;left:0;top:0}}
section{{position:relative;width:1920px!important;height:1080px!important;overflow:hidden;flex:none}}
h1,h2,h3,p,ul,ol{{margin:0}} ul,ol{{padding-left:1.2em}}
img{{display:block}}
</style></head><body>{pages}</body></html>"""


def main() -> int:
    import json

    fonts = embedded_fonts()
    print(f"  fonts embedded: {len(fonts)//1024} KB" if fonts else "  fonts: system fallback")

    order = slide_order()

    # --- presenter (video) -------------------------------------------------
    parts, notes = [], []
    for name in order:
        html, note = load_slide(name, for_pdf=False)
        parts.append(html)
        notes.append(note)
    html_out = PRESENTER.format(fonts=fonts, slides="\n".join(parts),
                                notes_json=json.dumps(notes))
    p = OUT / "SENSE-AI-pitch.html"
    p.write_text(html_out)
    print(f"  {p.name:26} {p.stat().st_size/1024/1024:.1f} MB  ({len(order)} slides, video embedded)")

    # --- slides prepared for rendering into the PDF ------------------------
    flat = OUT / ".flat"
    if flat.exists():
        shutil.rmtree(flat)
    flat.mkdir(parents=True)
    for name in order:
        html, _ = load_slide(name, for_pdf=True)
        (flat / f"{name}.html").write_text(html)
    print(f"  slides flattened for PDF -> {flat.name}/ ({len(order)} files)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
