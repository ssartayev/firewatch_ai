"""
Build the two working documents for the team.

  deck/SPEAKER-SCRIPT.(html|pdf)   who says what, in what order, with a clock.
  deck/WEBSITE-MANUAL.(html|pdf)   what every page of the product does.

Both are produced twice: an HTML with the clips embedded for reading on a
laptop, and a PDF with stills for printing or submitting.

    python scripts/build_docs.py
"""
from __future__ import annotations

import base64
import subprocess
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
OUT = BASE / "deck"
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

INK, MUTE, DIM = "#F2F4F6", "#8A95A1", "#5A6470"
BG, CARD, LINE = "#0A0A0B", "#131316", "#23262B"
CY, EMBER = "#22D3EE", "#FF5A1F"


def b64(p: Path, mime: str) -> str:
    return f"data:{mime};base64," + base64.b64encode(p.read_bytes()).decode()


def media(clip: str | None, still: str, w: str = "100%", video: bool = True) -> str:
    s = b64(OUT / "shots" / still, "image/png") if still.startswith("shot:") is False and (OUT / "shots" / still).exists() \
        else b64(OUT / "stills" / still, "image/png")
    if video and clip and (OUT / "clips" / clip).exists():
        return (f'<video src="{b64(OUT/"clips"/clip, "video/mp4")}" poster="{s}" muted loop autoplay '
                f'playsinline style="width:{w};border-radius:10px;border:1px solid {LINE};display:block"></video>')
    return f'<img src="{s}" style="width:{w};border-radius:10px;border:1px solid {LINE};display:block">'


def shot(name: str, w: str = "100%") -> str:
    return (f'<img src="{b64(OUT/"shots"/name, "image/png")}" '
            f'style="width:{w};border-radius:10px;border:1px solid {LINE};display:block">')


SHELL = """<!doctype html><html><head><meta charset="utf-8"><title>{title}</title><style>
@page {{ size: A4; margin: 14mm 13mm; }}
*{{box-sizing:border-box}}
html,body{{margin:0;padding:0;background:{bg};color:{ink};
  font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;
  font-size:13.5px;line-height:1.55;-webkit-print-color-adjust:exact;print-color-adjust:exact}}
.wrap{{max-width:940px;margin:0 auto;padding:30px 26px 50px}}
h1{{font-size:34px;line-height:1.1;margin:0 0 6px;letter-spacing:-0.01em}}
h2{{font-size:21px;margin:34px 0 12px;padding-bottom:7px;border-bottom:1px solid {line};
   page-break-after:avoid}}
h3{{font-size:15.5px;margin:18px 0 6px;color:{cy};page-break-after:avoid}}
p{{margin:0 0 10px;color:{mute}}}
.lead{{font-size:16px;color:{ink};margin-bottom:18px}}
b,strong{{color:{ink}}}
.card{{background:{card};border:1px solid {line};border-radius:10px;padding:16px 18px;margin:0 0 14px;
  page-break-inside:avoid}}
.grid{{display:flex;gap:14px;margin:0 0 14px}}
.grid>*{{flex:1;min-width:0}}
table{{width:100%;border-collapse:collapse;margin:0 0 14px;font-size:12.5px}}
th{{text-align:left;color:{dim};font-weight:600;padding:7px 9px;border-bottom:1px solid {line};
   font-size:11.5px;letter-spacing:.06em;text-transform:uppercase}}
td{{padding:8px 9px;border-bottom:1px solid #1A1D22;vertical-align:top;color:{mute}}}
td.t{{color:{ink};white-space:nowrap;font-variant-numeric:tabular-nums}}
.who{{display:inline-block;padding:1px 8px;border-radius:20px;font-size:11px;font-weight:600;
  letter-spacing:.04em;white-space:nowrap}}
.a{{background:rgba(34,211,238,.14);color:{cy};border:1px solid rgba(34,211,238,.35)}}
.b{{background:rgba(255,90,31,.14);color:{ember};border:1px solid rgba(255,90,31,.35)}}
.say{{color:{ink};font-size:14px;line-height:1.6}}
.note{{border-left:3px solid {line};padding:2px 0 2px 12px;color:{dim};font-size:12.5px;margin:8px 0 0}}
.warn{{border-left-color:{ember}}}
.pb{{page-break-before:always}}
.cap{{font-size:11.5px;color:{dim};margin:6px 0 16px}}
ul{{margin:0 0 10px;padding-left:18px;color:{mute}}} li{{margin:3px 0}}
.kbd{{background:{card};border:1px solid {line};border-radius:4px;padding:1px 6px;font-size:12px;color:{ink}}}
</style></head><body><div class="wrap">{body}</div></body></html>"""


def page(title: str, body: str) -> str:
    return SHELL.format(title=title, body=body, bg=BG, ink=INK, mute=MUTE, dim=DIM,
                        card=CARD, line=LINE, cy=CY, ember=EMBER)


def to_pdf(html_path: Path, pdf_path: Path) -> None:
    subprocess.run([
        CHROME, "--headless=new", "--disable-gpu", "--no-pdf-header-footer",
        f"--print-to-pdf={pdf_path}", "--virtual-time-budget=20000",
        html_path.resolve().as_uri(),
    ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


# ===================================================================== SCRIPT
A = '<span class="who a">BEKARYS</span>'
B = '<span class="who b">SANZHAR</span>'

SCRIPT_ROWS = [
    ("0:00", "0:10", A, "Cover — <i>Who is still inside?</i>",
     "Two beats of silence. Then: &ldquo;On the 26th of November last year, in Tai Po, nobody could answer this question.&rdquo;",
     "Do not rush the silence. It buys you the room."),
    ("0:10", "0:28", A, "168",
     "&ldquo;One hundred and sixty-eight people died at Wang Fuk Court. Forty minutes from this room. It was Hong Kong&rsquo;s deadliest fire since 1948.&rdquo;",
     "Say the number slowly, once. Do not add adjectives &mdash; the number is the adjective."),
    ("0:28", "0:50", A, "Three failures",
     "&ldquo;Three things failed. The alarms &mdash; not one of the eight towers sounded. The information &mdash; 467 missing-person reports, because nobody could say who was inside. And the escape &mdash; most of the dead were found inside their own flats.&rdquo;",
     "CRITICAL: then say &ldquo;we cannot fix an alarm nobody maintained.&rdquo; Never imply your product would have saved them. Judges will respect the restraint."),
    ("0:50", "1:05", A, "Most vertical city",
     "&ldquo;Hong Kong has 570 towers over 150 metres &mdash; more than Shenzhen, more than New York. Nine thousand high-rises in total. Nowhere on earth stacks more people vertically.&rdquo;",
     "One sentence per bar. Do not read the whole chart."),
    ("1:05", "1:27", A, "People do not run",
     "&ldquo;And here is what fire engineers know and the public does not. People do not run. The median delay before anyone in the World Trade Center even started moving was three minutes. Then they walk to the staircase they came in by &mdash; so one jams and two stand empty.&rdquo;",
     "This is your credibility moment. Then hand over: &ldquo;Sanzhar will show you what we built.&rdquo;"),
    ("1:27", "1:59", B, "What we built &mdash; <b>live demo</b>",
     "&ldquo;This is our system running. 84 people, three staircases. Watch the top line.&rdquo; <b>Wait for the red bar.</b> &ldquo;There. It just noticed people are not going where they were sent, and it is rewriting the plan. It did that 74 times in three and a half minutes.&rdquo;",
     "THE MONEY MOMENT. Stop talking and let them watch for three seconds before you explain. Everyone else stops at detection; this argues back."),
    ("1:59", "2:25", B, "Two sensors",
     "&ldquo;Cameras count people until smoke blinds them. Radar keeps counting through smoke. On the right is our own footage, run through the real model &mdash; seven people, and those boxes are its actual output.&rdquo;",
     "Do not say &lsquo;sensor fusion&rsquo;. If asked for depth: a blinded camera can only miss people, never invent them, so we use its count as a floor, never an average."),
    ("2:25", "2:41", B, "What gets installed",
     "&ldquo;Per floor: one camera, one radar sensor, speakers. The smoke and heat detectors are already in the building &mdash; we read the existing panel instead of replacing it. That is what makes a retrofit realistic.&rdquo;",
     "This slide justifies the per-floor price later. Keep it short."),
    ("2:41", "3:03", B, "Firefighter view",
     "&ldquo;That night there were 467 missing-person reports and 600 people searching. This is the alternative. Room 314 &mdash; eight people, camera blind, radar holding the count &mdash; and a way in that avoids the crowd coming out.&rdquo;",
     "End with: &ldquo;It gives information, not orders.&rdquo; Then hand back to Bekarys."),
    ("3:03", "3:23", A, "Market",
     "&ldquo;A thousand Hong Kong buildings are tall enough to need this. At HK$120,000 a building a year, that is a HK$120 million market. We are targeting the 570 towers that already employ fire-safety staff, and 3% of those by year three.&rdquo;",
     "Say the arithmetic out loud. Judges forgive an estimate they can follow."),
    ("3:23", "3:39", A, "Competition",
     "&ldquo;Alarm panels know where the fire is. Occupancy sensors know where people are &mdash; on a Monday, for desk planning. Nobody is in this corner.&rdquo;",
     "Point at the empty top-right quadrant. That gesture does more than the words."),
    ("3:39", "3:59", A, "Business model",
     "&ldquo;Two ways in. Direct to owners: HK$22,000 a floor to fit out, HK$120,000 a year after. Or we license to the fire-safety firms who already hold the customer, at HK$45,000 a building. We have no revenue yet &mdash; we are testing which one works.&rdquo;",
     "Say &lsquo;no revenue yet&rsquo; before a judge says it for you. It reads as honesty, not weakness."),
    ("3:59", "4:14", B, "Team",
     "&ldquo;Four of us. I wrote the routing and fusion engine. Bekarys runs the company, Yerdos the numbers, Rauan the pilots. None of us is a certified fire engineer &mdash; that is our first hire, because we will not guess at life safety.&rdquo;",
     "Do not read the cards. The last sentence is the one that lands."),
    ("4:14", "4:32", A, "Three years",
     "&ldquo;Year one: a fire-safety advisor and two pilot buildings. Year two: ten buildings and one installer reselling. Year three: fifteen to twenty buildings and a certification pathway.&rdquo;",
     "If pushed on revenue: this is arithmetic from our price, not a forecast."),
    ("4:32", "4:44", A, "Close",
     "&ldquo;Fire safety has spent a century getting better at finding fire. We think the next one is about finding the people.&rdquo;",
     "Stop talking. Let the clip run behind you. Do not add a thank-you slide."),
]

rows = "".join(
    f'<tr><td class="t">{a}</td><td class="t">{w}</td><td class="t"><b>{t}</b></td>'
    f'<td><div class="say">{say}</div><div class="note {"warn" if "CRITICAL" in n or "MONEY" in n else ""}">{n}</div></td></tr>'
    for a, _b, w, t, say, n in SCRIPT_ROWS)

script_body = f"""
<h1>SENSE AI &mdash; speaker script</h1>
<p class="lead">HKTech300, 21st Cohort &middot; 5 minutes &middot; two speakers.
Total scripted time <b>4:44</b>, leaving 16 seconds of slack. If you are running late, cut the
Competition slide &mdash; it is the only one the deck survives without.</p>

<div class="grid">
  <div class="card"><h3 style="margin-top:0">{A} &mdash; the why</h3>
  <p>Opens and closes. Owns the story, the market and the money. Nine slides.</p></div>
  <div class="card"><h3 style="margin-top:0">{B} &mdash; the how</h3>
  <p>Owns the demo and the technology. Six slides, including the two that carry video.</p></div>
</div>

<div class="card warn" style="border-left:3px solid {EMBER}">
<h3 style="margin-top:0;color:{EMBER}">Before you open your mouth</h3>
<p>This deck opens on a disaster that killed 168 people ten months ago, in this city.
Delivered plainly it is the strongest opening in the room. Delivered with any theatricality
it will offend people who lost someone. <b>Do not perform it. State it.</b></p>
<p>And never claim your product would have saved them. The alarms failing was a maintenance
and compliance failure. Your claim is narrower and true: nobody could say who was inside.</p>
</div>

<h2>The run sheet</h2>
<table>
<tr><th style="width:8%">From</th><th style="width:8%">To</th><th style="width:20%">Slide</th><th>What to say</th></tr>
{rows}
</table>

<h2 class="pb">Handovers</h2>
<p>Two handovers only. Rehearse the exact words &mdash; a fumbled handover costs five seconds
and looks unprepared.</p>
<div class="card"><p><b>1:27 &mdash; Bekarys to Sanzhar.</b> &ldquo;&hellip;so one jams and two stand empty.
Sanzhar will show you what we built.&rdquo; Sanzhar takes the clicker and starts on the
demo slide already running.</p></div>
<div class="card"><p><b>3:03 &mdash; Sanzhar to Bekarys.</b> &ldquo;&hellip;it gives information, not orders.&rdquo;
Bekarys picks up on Market without a pause.</p></div>
<div class="card"><p><b>4:14 &mdash; Sanzhar to Bekarys</b> after Team, for the last two slides.</p></div>

<h2>Questions you will get</h2>
<table>
<tr><th style="width:38%">Question</th><th>Answer</th></tr>
<tr><td class="t">Would this have saved Tai Po?</td><td><b>No, and we will not claim it.</b> The alarms never sounded &mdash; that is a maintenance failure we cannot fix. What we address is the second failure: nobody could say who was inside.</td></tr>
<tr><td class="t">Do you have customers?</td><td>No revenue and no signed pilot yet. We have a working system and a price we intend to test. Year one is about finding the first building.</td></tr>
<tr><td class="t">Is this certified?</td><td>No. It is a decision-support prototype with no regulatory approval, and the deck says so. Certification is a year-three milestone, and our first hire is a fire-safety advisor.</td></tr>
<tr><td class="t">Can radar really see people?</td><td>It detects motion and micro-motion through smoke. It is not a people counter and we never treat it as exact &mdash; that is why we publish a range and a confidence.</td></tr>
<tr><td class="t">Why would a building switch?</td><td>They do not switch. We sit on top of the alarm panel they already own and read it. That is why the retrofit is HK$22k a floor and not a rip-out.</td></tr>
<tr><td class="t">What stops Honeywell doing this?</td><td>Nothing, eventually. Our lead is the occupancy model &mdash; the confidence-aware fusion and the reallocation loop &mdash; which is the part we wrote ourselves and keep improving.</td></tr>
</table>

<h2>Running the deck</h2>
<p>Present from <b>SENSE-AI-pitch.html</b>. It needs no internet and no install &mdash; double-click
it and any browser opens it. Press <span class="kbd">F</span> for fullscreen.
<span class="kbd">&rarr;</span> and <span class="kbd">&larr;</span> move.
<span class="kbd">N</span> shows these notes on screen.</p>
<p class="note">The PDF is for submission only. PDFs cannot play video on a normal laptop, so the
clips appear as still frames there. Present from the HTML.</p>
"""

(OUT / "SPEAKER-SCRIPT.html").write_text(page("SENSE AI — Speaker script", script_body))
to_pdf(OUT / "SPEAKER-SCRIPT.html", OUT / "SPEAKER-SCRIPT.pdf")
print(f"  SPEAKER-SCRIPT.pdf         {(OUT/'SPEAKER-SCRIPT.pdf').stat().st_size/1024:.0f} KB")


# ===================================================================== MANUAL
def manual(video: bool) -> str:
    return f"""
<h1>SENSE AI &mdash; what the product does</h1>
<p class="lead">Six pages. This is our own reference, so we can answer anything a judge asks
about the software. Run it with:
<span class="kbd">.venv/bin/python -m uvicorn app.main:app --port 8010</span>
then open <b>127.0.0.1:8010</b>.</p>

<h2>1 &middot; Live view &mdash; the whole story in one screen</h2>
{media("01-hero.mp4", "live.png", video=video)}
<p class="cap">Press <b>Run emergency simulation</b>. Everything else on this page follows from it.</p>
<div class="grid">
  <div class="card"><h3 style="margin-top:0">The sentence at the top</h3>
  <p>Plain English, changes as the fire does. It is the only thing a non-technical
  viewer needs to read.</p></div>
  <div class="card"><h3 style="margin-top:0">Three numbers</h3>
  <p>People still inside &middot; time to get everyone out &middot; whether people are
  following the plan.</p></div>
</div>
<p><b>Tabs under the map:</b> how busy each stair is, what people are being told through the
speakers, and the event log. <b>Speed slider</b> runs the incident faster for a demo.</p>
<p class="note">The whole incident is 14 scripted beats and takes about 3 minutes 40 seconds
at normal speed. 84 people, every one accounted for at the end.</p>

<h2 class="pb">2 &middot; Who is inside &mdash; counting people</h2>
{media("03-blind.mp4", "occupancy.png", video=video)}
<p class="cap">Each room, how many people, and how sure we are.</p>
<h3>The controls that matter in a demo</h3>
<ul>
<li><b>Fill with smoke</b> &mdash; drag the slider up. Camera confidence collapses, the numbers
turn into ranges like &ldquo;4&ndash;6&rdquo;, and the source switches from Camera to Radar.</li>
<li><b>Switch off three cameras</b> &mdash; proves the radar layer alone still holds a count.</li>
<li><b>Click any room</b> &mdash; shows exactly how that number was reached: what each sensor said,
how sure it was, and how it was used.</li>
</ul>
<div class="card"><h3 style="margin-top:0">The one technical idea worth knowing</h3>
<p>A camera blinded by smoke can only <b>miss</b> people, never invent them. So when it is
obscured we use its count as a <b>minimum</b>, not an average. Averaging would drag the
estimate below the truth exactly when a life depends on it.</p></div>

<h2>3 &middot; What the camera actually sees</h2>
{media("04-upload.mp4", "04-upload.png", video=video)}
<p class="cap">Real YOLO detection on our own uploaded footage &mdash; seven people, 84% confidence.</p>
<p><b>Play the example</b> runs a bundled clip. <b>Use my own video</b> takes any MP4 with real
people in it. The boxes are the model&rsquo;s actual output. Cartoons and animations will find
nothing &mdash; it is trained on real people.</p>

<h2 class="pb">4 &middot; Escape routes &mdash; break it on purpose</h2>
{media("06-routes.mp4", "evacuation.png", video=video)}
<p class="cap">Press the buttons on the left and watch the coloured routes move.</p>
<table>
<tr><th style="width:32%">Button</th><th>What it proves</th></tr>
<tr><td class="t">Start a fire in Room 305</td><td>Routes bend away from the smoke as it spreads.</td></tr>
<tr><td class="t">Jam up Stair A</td><td>The allocation moves people to the other two staircases.</td></tr>
<tr><td class="t">Block the corridor</td><td>Whole sections reroute. Push it far enough and it will tell you people are trapped.</td></tr>
<tr><td class="t">Five ready-made situations</td><td>Normal, fire near an exit, stair crowding, camera failure, two fires at once.</td></tr>
</table>
<p><b>The tab that wins arguments:</b> <i>Who goes where, and why</i> &mdash; every route has a
written reason, like &ldquo;Stair A at capacity, C is faster despite the distance&rdquo;.</p>

<h2>5 &middot; Firefighter view</h2>
{media("05-responder.mp4", "command.png", video=video)}
<p class="cap">The 3-D building, who is left, and two ways in.</p>
<p>Only Level 3 is mapped room by room &mdash; the other floors show an estimated headcount,
because that is genuinely all the model knows. Saying so is the point.</p>
<p><b>Room 314</b> is a refuge point: people who cannot use stairs alone. A headcount in the
car park never finds them. The <b>best way in</b> avoids the staircase carrying the crowd out.</p>

<h2 class="pb">6 &middot; Building &amp; sensors</h2>
{media("07-twin.mp4", "twin.png", video=video)}
<p class="cap">Click any room to see what is installed in it.</p>
<p>The system holds a model of the building: every room, corridor and staircase, how wide each
door is, and therefore how many people per minute can get through it. 23 spaces, 70 devices.</p>
<p><b>Marked &ldquo;in the demo&rdquo;:</b> cameras, radar, smoke and heat detectors, speakers.
<b>Marked &ldquo;not built yet&rdquo;:</b> ceiling arrows and the fire-panel link. We do not pretend
otherwise anywhere in the product.</p>

<h2>7 &middot; How it works</h2>
{shot("tech.png")}
<p class="cap">The architecture page &mdash; for the judge who asks whether anything real is underneath.</p>
<p>The loop runs continuously: <b>sense &rarr; estimate &rarr; plan &rarr; guide &rarr; observe
&rarr; compare &rarr; recalculate</b>. This page also lists, in plain terms, everything the
prototype <i>cannot</i> do &mdash; no certification, no guaranteed detection, no seeing through walls.</p>

<div class="card warn" style="border-left:3px solid {EMBER}">
<h3 style="margin-top:0;color:{EMBER}">If the laptop has no internet</h3>
<p>The website needs the server running locally, so it works offline &mdash; but only on a machine
with the project installed. <b>For the pitch, present from SENSE-AI-pitch.html instead.</b>
The recorded clips in it are the same software, and nothing can crash mid-demo.</p>
</div>
"""


(OUT / "WEBSITE-MANUAL.html").write_text(page("SENSE AI — website manual", manual(video=True)))
tmp = OUT / ".manual-print.html"
tmp.write_text(page("SENSE AI — website manual", manual(video=False)))
to_pdf(tmp, OUT / "WEBSITE-MANUAL.pdf")
tmp.unlink(missing_ok=True)
print(f"  WEBSITE-MANUAL.html        {(OUT/'WEBSITE-MANUAL.html').stat().st_size/1024/1024:.1f} MB (with video)")
print(f"  WEBSITE-MANUAL.pdf         {(OUT/'WEBSITE-MANUAL.pdf').stat().st_size/1024:.0f} KB")
sys.exit(0)
