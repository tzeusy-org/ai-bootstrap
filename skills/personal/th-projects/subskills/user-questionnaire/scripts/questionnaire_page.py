# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Render an owner-questionnaire packet as one self-contained review page.

Pure functions, stdlib only: ``parse_packet`` turns packet markdown into a
view model, ``render_page`` turns that model plus recorded answers into HTML
with the stylesheet, script, and any diagram images inlined, so the page works
offline and from a file. ``questionnaire_web.py`` owns serving and state;
this module is imported, not run.
"""

from __future__ import annotations

import base64
import html
import json
import re
from dataclasses import dataclass
from pathlib import Path
from string import Template
from typing import Any

import validate_questionnaire as vq

ASSET_DIR = Path(__file__).resolve().parents[1] / "assets" / "web"
IMAGE_TYPES = {".svg": "image/svg+xml", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}
MAX_IMAGE_BYTES = 2 * 1024 * 1024
EVIDENCE_LABELS = ("Observed", "Inferred", "Unknown")
WORDS_PER_MINUTE = 230


# ── view model ────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Option:
    label: str
    outcome: str
    pros: str
    cons: str


@dataclass(frozen=True)
class Item:
    id: str
    title: str
    priority: str
    direction: str
    state: str
    review: str
    decision_needed: str
    background: str
    options: list[Option]
    recommendation: str
    recommended: str
    diagram: str
    boundary: str
    review_record: str
    evidence: str

    @property
    def reviewed(self) -> bool:
        return self.state in vq.READY_STATES

    def evidence_counts(self) -> dict[str, int]:
        return {label: self.background.count(f"**[{label}]**") for label in EVIDENCE_LABELS}


@dataclass(frozen=True)
class Packet:
    title: str
    snapshot: str
    boundary: str
    preface: list[tuple[str, str]]
    items: list[Item]

    @property
    def reading_minutes(self) -> int:
        """Read time of what the owner reads to decide; audit sections excluded."""
        words = sum(
            len(re.findall(r"\w+", " ".join([i.decision_needed, i.recommendation, i.background, *(f"{o.outcome} {o.pros} {o.cons}" for o in i.options)])))
            for i in self.items
        )
        return max(1, round(words / WORDS_PER_MINUTE))

    @property
    def drafts(self) -> list[Item]:
        return [item for item in self.items if not item.reviewed]


def _cells(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def _matrix(text: str) -> dict[str, dict[str, str]]:
    """Decision-matrix rows keyed by decision id, columns keyed by header."""
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if line.startswith("|") and "Decision ID" in line:
            headers = [h.lower() for h in _cells(line)]
            rows: dict[str, dict[str, str]] = {}
            for row in lines[index + 2 :]:
                if not row.startswith("|"):
                    break
                values = dict(zip(headers, _cells(row)))
                rows[values.get("decision id", "").strip("`")] = values
            return rows
    return {}


def _options(body: str) -> list[Option]:
    options = []
    for line in body.splitlines():
        cells = _cells(line)
        match = re.fullmatch(r"([A-D])\.\s*(.*)", cells[0]) if len(cells) == 3 else None
        if match:
            options.append(Option(match.group(1), match.group(2), cells[1], cells[2]))
    return options


def _preface(text: str) -> list[tuple[str, str]]:
    """``## `` sections that precede the first decision item."""
    first_item = vq.SECTION_RE.search(text)
    head = text[: first_item.start()] if first_item else text
    parts = re.split(r"^## (.+)$", head, flags=re.MULTILINE)
    return [(parts[i].strip(), parts[i + 1].strip()) for i in range(1, len(parts) - 1, 2)]


def parse_packet(text: str) -> Packet:
    """View model of a packet; section boundaries follow the validator's model."""
    title = re.search(r"^# (.+)$", text, re.MULTILINE)
    snapshot = vq.labeled_field(text, "Evidence snapshot") or ""
    boundary = vq.labeled_field(text, "Artifact boundary") or ""
    matrix = _matrix(text)
    blocks, _ = vq.section_blocks(text)
    items = []
    for decision_id, block in blocks:
        heading = block.splitlines()[0]
        row = matrix.get(decision_id, {})
        recommendation = vq.subsection(block, "Recommendation") or ""
        recommended = re.search(r"\b(?:Choose|Recommend)\s+\*\*([A-D])\*\*", recommendation)
        items.append(
            Item(
                id=decision_id,
                title=heading.split("—", 1)[1].strip() if "—" in heading else decision_id,
                priority=row.get("priority", ""),
                direction=row.get("recommended direction", ""),
                state=vq.labeled_field(block, "Decision state") or "",
                review=vq.labeled_field(block, "Adversarial review") or "",
                decision_needed=vq.subsection(block, "Decision needed") or "",
                background=vq.subsection(block, "Background and freshness") or "",
                options=_options(vq.subsection(block, "Options") or ""),
                recommendation=recommendation,
                recommended=recommended.group(1) if recommended else "",
                diagram=vq.subsection(block, "Diagram") or "",
                boundary=vq.subsection(block, "Authorization boundary") or "",
                review_record=vq.subsection(block, "Adversarial review record") or "",
                evidence=vq.subsection(block, "Evidence") or "",
            )
        )
    return Packet(
        title=title.group(1).strip() if title else "Owner decision questionnaire",
        snapshot=snapshot,
        boundary=boundary,
        preface=_preface(text),
        items=items,
    )


# ── images ────────────────────────────────────────────────────────────────────


class ImageResolver:
    """Inline packet-relative images as data URIs; never read outside the packet dir."""

    def __init__(self, base_dir: Path | None):
        self.base_dir = base_dir.resolve() if base_dir else None
        self.missing: list[str] = []

    def __call__(self, ref: str) -> str | None:
        path = self._locate(ref)
        if path is None:
            self.missing.append(ref)
            return None
        data = base64.b64encode(path.read_bytes()).decode("ascii")
        return f"data:{IMAGE_TYPES[path.suffix.lower()]};base64,{data}"

    def _locate(self, ref: str) -> Path | None:
        if self.base_dir is None or re.match(r"^[a-z]+:", ref, re.IGNORECASE):
            return None
        path = (self.base_dir / ref).resolve()
        if (
            not path.is_relative_to(self.base_dir)
            or path.suffix.lower() not in IMAGE_TYPES
            or not path.is_file()
            or path.stat().st_size > MAX_IMAGE_BYTES
        ):
            return None
        return path


# ── markdown subset → html ────────────────────────────────────────────────────

_LABEL_RE = re.compile(r"\*\*\[(Observed|Inferred|Unknown)\]\*\*")
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
_ITALIC_RE = re.compile(r"(?<![\w*/])\*(?![\s*])(.+?)(?<![\s*/])\*(?![\w*/])")
_LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")
_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\(([^)\s]+)\)")
_LIST_RE = re.compile(r"^(\s*)([-*]|\d+\.)\s+(.*)$")
_HEADING_RE = re.compile(r"^#{1,6}\s+(.+)$")


def inline(text: str, *, links: bool = True) -> str:
    """Inline markdown: code spans, evidence labels, bold, italic, http(s) links."""
    out = []
    for part in re.split(r"(`[^`]+`)", text):
        if len(part) > 1 and part.startswith("`") and part.endswith("`"):
            out.append(f"<code>{html.escape(part[1:-1])}</code>")
            continue
        part = html.escape(part)
        part = _LABEL_RE.sub(lambda m: f'<span class="ev ev-{m.group(1).lower()}">{m.group(1)}</span>', part)
        part = _BOLD_RE.sub(r"<strong>\1</strong>", part)
        part = _ITALIC_RE.sub(r"<em>\1</em>", part)
        part = _LINK_RE.sub(r'<a href="\2" rel="noopener noreferrer">\1</a>' if links else r"\1", part)
        out.append(part)
    return "".join(out)


def plain(text: str) -> str:
    """Inline markdown reduced to its words, for text the page script sets as textContent."""
    out = []
    for part in re.split(r"(`[^`]+`)", text):
        if len(part) > 1 and part.startswith("`") and part.endswith("`"):
            out.append(part[1:-1])
            continue
        out.append(_LINK_RE.sub(r"\1", _ITALIC_RE.sub(r"\1", _BOLD_RE.sub(r"\1", part))))
    return "".join(out)


def _starts_block(line: str) -> bool:
    stripped = line.strip()
    return bool(
        stripped.startswith(("|", "```"))
        or _LIST_RE.match(line)
        or _HEADING_RE.match(stripped)
        or _IMAGE_RE.fullmatch(stripped)
    )


def _figure(alt: str, ref: str, images: ImageResolver | None) -> str:
    src = images(ref) if images else None
    caption = f"<figcaption>{inline(alt)}</figcaption>" if alt else ""
    if src is None:
        return f'<figure class="figure missing"><p>Diagram unavailable: <code>{html.escape(ref)}</code></p>{caption}</figure>'
    return f'<figure class="figure"><img src="{src}" alt="{html.escape(alt)}">{caption}</figure>'


def _table(rows: list[list[str]]) -> str:
    head, *body = rows
    thead = "".join(f"<th>{inline(c)}</th>" for c in head)
    tbody = "".join("<tr>" + "".join(f"<td>{inline(c)}</td>" for c in row) + "</tr>" for row in body)
    return f'<div class="table-wrap"><table><thead><tr>{thead}</tr></thead><tbody>{tbody}</tbody></table></div>'


def _list(lines: list[str], i: int) -> tuple[str, int]:
    """Render the list starting at ``lines[i]``; nested deeper indents recurse."""
    first = _LIST_RE.match(lines[i])
    assert first is not None
    indent = len(first.group(1))
    tag = "ol" if first.group(2)[0].isdigit() else "ul"
    entries: list[str] = []
    while i < len(lines):
        match = _LIST_RE.match(lines[i])
        if match and len(match.group(1)) == indent:
            entries.append(inline(match.group(3)))
            i += 1
        elif match and len(match.group(1)) > indent and entries:
            nested, i = _list(lines, i)
            entries[-1] += nested
        elif not match and lines[i].strip() and lines[i].startswith(" " * (indent + 2)) and entries:
            entries[-1] += " " + inline(lines[i].strip())
            i += 1
        else:
            break
    return f"<{tag}>" + "".join(f"<li>{e}</li>" for e in entries) + f"</{tag}>", i


def markdown(body: str, images: ImageResolver | None = None) -> str:
    """Block markdown subset used by packets: paragraphs, lists, tables, fences, headings, images."""
    lines = body.splitlines()
    out: list[str] = []
    i = 0
    while i < len(lines):
        stripped = lines[i].strip()
        if not stripped:
            i += 1
        elif stripped.startswith("```"):
            end = next((j for j in range(i + 1, len(lines)) if lines[j].strip().startswith("```")), len(lines))
            out.append(f"<pre><code>{html.escape(chr(10).join(lines[i + 1 : end]))}</code></pre>")
            i = end + 1
        elif stripped.startswith("|"):
            rows = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                cells = _cells(lines[i])
                if not all(re.fullmatch(r":?-{2,}:?", c) for c in cells):
                    rows.append(cells)
                i += 1
            if rows:
                out.append(_table(rows))
        elif image := _IMAGE_RE.fullmatch(stripped):
            out.append(_figure(image.group(1), image.group(2), images))
            i += 1
        elif heading := _HEADING_RE.match(stripped):
            out.append(f'<h3 class="md-h">{inline(heading.group(1))}</h3>')
            i += 1
        elif _LIST_RE.match(lines[i]):
            rendered, i = _list(lines, i)
            out.append(rendered)
        else:
            para = [stripped]
            i += 1
            while i < len(lines) and lines[i].strip() and not _starts_block(lines[i]):
                para.append(lines[i].strip())
                i += 1
            out.append(f"<p>{inline(' '.join(para))}</p>")
    return "\n".join(out)


# ── page ──────────────────────────────────────────────────────────────────────

KIND_STATUS = {"pick": "Chose {choice}", "edit": "Edited", "hold": "Held", "reject": "Rejected"}


SHORT_NAME_CHARS = 60


def _outcome_parts(outcome: str) -> tuple[str, str]:
    """``(name, detail)`` for an option outcome; name is empty when there is no short one.

    ``Retire: close the epic…`` names itself; a short outcome is its own name;
    a long unnamed outcome stays body text rather than becoming a bold paragraph.
    """
    match = re.match(r"([^:`]{2,40}):\s+(.+)$", outcome)
    if match:
        detail = match.group(2)
        return match.group(1), detail[0].upper() + detail[1:]
    if len(outcome) <= SHORT_NAME_CHARS:
        return outcome, ""
    return "", outcome


def _headline(outcome: str) -> str:
    """Short label for the recommended option: its name, else its first short clause."""
    name, _ = _outcome_parts(outcome)
    if name:
        return name
    for separator in ("; ", ", "):
        clause = outcome.split(separator, 1)[0]
        if len(clause) <= SHORT_NAME_CHARS + 10 and clause.count("`") % 2 == 0:
            return clause
    return outcome[:SHORT_NAME_CHARS].rsplit(" ", 1)[0].replace("`", "") + "…"


def _rationale(item: Item) -> str:
    """Recommendation prose without the ``Choose **X** because`` lead the headline already says."""
    match = re.match(rf"^(?:Choose|Recommend)\s+\*\*{item.recommended}\*\*\s+because\s+(.)", item.recommendation)
    if not match:
        return item.recommendation
    return match.group(1).upper() + item.recommendation[match.end() :]


def _status_pill(item_id: str) -> str:
    return f'<span class="pill" data-status-for="{html.escape(item_id)}">Unanswered</span>'


def _evidence_profile(item: Item) -> str:
    counts = item.evidence_counts()
    parts = [
        f'<span class="ev-count ev-{label.lower()}" title="{label} claims">{n} {label.lower()}</span>'
        for label, n in counts.items()
        if n
    ]
    return f'<span class="ev-profile">{"".join(parts)}</span>' if parts else ""


def _option_html(item: Item, option: Option) -> str:
    name, detail = _outcome_parts(option.outcome)
    recommended = option.label == item.recommended
    badge = '<span class="badge">Recommended</span>' if recommended else ""
    uid = html.escape(f"{item.id}-{option.label}")
    label = f'<span class="sr-only">Option {option.label}: </span>'
    if name:
        lead = f'<span class="opt-name" id="name-{uid}">{label}{inline(name, links=False)}{badge}</span>'
        if detail:
            lead += f'<span class="opt-detail">{inline(detail, links=False)}</span>'
    else:
        lead = f'<span class="opt-text" id="name-{uid}">{label}{inline(detail, links=False)} {badge}</span>'
    return (
        f'<button type="button" class="opt{" is-rec" if recommended else ""}" data-label="{option.label}" aria-pressed="false"'
        f' aria-labelledby="name-{uid}" aria-describedby="tradeoffs-{uid}">'
        f'<span class="key" aria-hidden="true">{option.label}</span>'
        f'<span class="opt-body">{lead}'
        f'<span class="tradeoffs" id="tradeoffs-{uid}">'
        f'<span class="pro"><span class="sym" aria-label="Pros">+</span><span>{inline(option.pros, links=False)}</span></span>'
        f'<span class="con"><span class="sym" aria-label="Cons">−</span><span>{inline(option.cons, links=False)}</span></span>'
        f"</span></span></button>"
    )


def _item_html(item: Item, index: int, total: int, images: ImageResolver) -> str:
    iid = html.escape(item.id)
    rec_option = next((o for o in item.options if o.label == item.recommended), None)
    rec_name = inline(_headline(rec_option.outcome), links=False) if rec_option else ""
    review = (
        '<span class="tag tag-ok" title="{0}">Reviewed</span>'.format(html.escape(item.review))
        if item.reviewed
        else f'<span class="tag tag-warn">{html.escape(item.state or "No state")} · not reviewed</span>'
    )
    if item.state not in ("Review-ready", "") and item.reviewed:
        review += f'<span class="tag">{html.escape(item.state)}</span>'
    priority = f'<span class="tag tag-pri">{html.escape(item.priority)}</span>' if item.priority else ""
    diagram = f'<div class="diagram">{markdown(item.diagram, images)}</div>' if item.diagram else ""
    rec = (
        f'<aside class="rec" aria-label="Recommendation"><p class="rec-head"><span class="rec-tag">Recommended</span>'
        f'<span class="key key-sm" aria-hidden="true">{item.recommended}</span><strong>{rec_name}</strong></p>'
        f'<div class="rec-why">{markdown(_rationale(item), images)}</div></aside>'
        if item.recommended
        else f'<aside class="rec" aria-label="Recommendation">{markdown(item.recommendation, images)}</aside>'
    )
    return f"""
<article class="item" id="item-{iid}" data-id="{iid}" aria-labelledby="title-{iid}">
  <header class="item-head">
    <p class="eyebrow"><span class="count">Decision {index} of {total}</span>{priority}{review}<code class="iid">{iid}</code></p>
    <h2 id="title-{iid}" tabindex="-1">{inline(item.title)}</h2>
    <div class="ask">{markdown(item.decision_needed, images)}</div>
  </header>
  {rec}
  {diagram}
  <details class="bg" open>
    <summary><span class="summary-label">Background</span>{_evidence_profile(item)}</summary>
    <div class="prose">{markdown(item.background, images)}</div>
  </details>
  <fieldset class="answer">
    <legend>Your answer</legend>
    <div class="opts">{"".join(_option_html(item, o) for o in item.options)}</div>
    <div class="alts">
      <button type="button" class="chip-btn" data-kind="edit" aria-pressed="false"><span class="kbd" aria-hidden="true">e</span>Edit an option</button>
      <button type="button" class="chip-btn" data-kind="hold" aria-pressed="false"><span class="kbd" aria-hidden="true">h</span>Hold</button>
      <button type="button" class="chip-btn" data-kind="reject" aria-pressed="false"><span class="kbd" aria-hidden="true">r</span>Reject all</button>
      <span class="save" role="status" aria-live="polite"></span>
      <button type="button" class="chip-btn retry" hidden>Retry</button>
    </div>
    <div class="fld fld-edit" hidden>
      <label for="edit-{iid}">Your edited option <span class="hint">returns the item to review before anything is routed</span></label>
      <textarea id="edit-{iid}" rows="3" placeholder="State the option you want instead"></textarea>
    </div>
    <div class="fld fld-note" hidden>
      <label for="note-{iid}">Note</label>
      <textarea id="note-{iid}" rows="2"></textarea>
    </div>
  </fieldset>
  <div class="more">
    <details><summary>Authorization boundary</summary><div class="prose">{markdown(item.boundary, images)}</div></details>
    <details><summary>Review record</summary><div class="prose">{markdown(item.review_record, images)}</div></details>
    <details><summary>Evidence</summary><div class="prose">{markdown(item.evidence, images)}</div></details>
  </div>
</article>"""


def _overview_html(packet: Packet) -> str:
    rows = []
    for index, item in enumerate(packet.items, 1):
        iid = html.escape(item.id)
        direction = inline(item.direction, links=False) if item.direction else ""
        priority = f'<span class="tag tag-pri">{html.escape(item.priority)}</span>' if item.priority else ""
        rows.append(
            f'<li><a class="ov-row" href="#item-{iid}">'
            f'<span class="ov-num">{index}</span>'
            f'<span class="ov-main"><span class="ov-title">{inline(item.title, links=False)}</span>'
            f'<span class="ov-dir">{direction}</span></span>'
            f'<span class="ov-meta">{priority}{_status_pill(item.id)}</span>'
            f"</a></li>"
        )
    return f'<section class="overview" aria-labelledby="ov-h"><h2 id="ov-h" class="section-h">At a glance</h2><ol class="ov-list">{"".join(rows)}</ol></section>'


def _nav_html(packet: Packet) -> str:
    links = "".join(
        f'<li><a href="#item-{html.escape(i.id)}" data-nav-for="{html.escape(i.id)}">'
        f'<span class="dot" data-dot-for="{html.escape(i.id)}"></span><span class="nav-title">{inline(i.title, links=False)}</span></a></li>'
        for i in packet.items
    )
    return f'<nav class="toc" aria-label="Decisions"><p class="toc-h">Decisions</p><ol>{links}</ol><a class="toc-final" href="#final">Submit review</a></nav>'


def _banners(packet: Packet, answers: dict[str, Any], live: bool, packet_sha256: str) -> str:
    out = []
    if not live:
        out.append('<p class="banner banner-info"><strong>Preview.</strong> Read-only rendering; answers are recorded only on the served page.</p>')
    if packet.drafts:
        which = (
            "No item has passed adversarial review yet."
            if len(packet.drafts) == len(packet.items)
            else "Not yet through adversarial review: " + ", ".join(f"<code>{html.escape(i.id)}</code>" for i in packet.drafts) + "."
        )
        out.append(f'<p class="banner banner-warn"><strong>Not ready for signoff.</strong> {which}</p>')
    if answers.get("items") and answers.get("packet_sha256") not in (None, packet_sha256):
        out.append('<p class="banner banner-warn" id="changed"><strong>The packet changed after you started answering.</strong> Re-read the items you answered before submitting.</p>')
    return "".join(out)


def _hero_html(packet: Packet) -> str:
    snapshot_head, _, snapshot_rest = packet.snapshot.partition("; ")
    rest = f'<details class="snap"><summary>Sources checked</summary><p>{inline(snapshot_rest)}</p></details>' if snapshot_rest else ""
    return f"""
<section class="hero">
  <p class="eyebrow">Owner decision packet</p>
  <h1>{inline(packet.title, links=False)}</h1>
  <p class="lede">{len(packet.items)} decisions · about {packet.reading_minutes} min read · answers save as you go</p>
  <p class="snapshot">Evidence: {inline(snapshot_head)}</p>{rest}
  <aside class="boundary" aria-label="What submitting does">
    <p class="boundary-head">Nothing takes effect when you answer.</p>
    <p>Answers are recorded locally. A later agent session routes them through the owning workflows, and only if you say so at the end.</p>
    <details><summary>Packet boundary</summary><p>{inline(packet.boundary)}</p></details>
  </aside>
</section>"""


def _preface_html(packet: Packet, images: ImageResolver) -> str:
    return "".join(
        f'<details class="preface" open><summary><h2 class="section-h">{inline(heading)}</h2></summary>'
        f'<div class="prose">{markdown(body, images)}</div></details>'
        for heading, body in packet.preface
        if body
    )


FINAL_HTML = """
<section class="final" id="final" aria-labelledby="final-h">
  <h2 id="final-h" tabindex="-1">Submit your review</h2>
  <ul class="tally" id="tally"></ul>
  <div class="todo" id="todo"></div>
  <fieldset class="apply">
    <legend>Apply the recorded decisions when the next session ingests them?</legend>
    <label class="choice"><input type="radio" name="apply" value="true"><span><strong>Yes, apply</strong>Route exactly the chosen and held items in this packet through their owning workflows.</span></label>
    <label class="choice"><input type="radio" name="apply" value="false"><span><strong>No, record only</strong>Keep the answers on file; nothing is routed until you ask.</span></label>
    <p class="fine">Neither choice approves any external, live, deployment, or merge action.</p>
  </fieldset>
  <div class="submit-row">
    <button type="button" class="primary" id="submit">Submit review</button>
    <button type="button" class="chip-btn" id="reopen" hidden>Reopen to change answers</button>
    <span class="save" id="final-status" role="status" aria-live="polite"></span>
  </div>
</section>"""


def _script_safe_json(value: Any) -> str:
    """JSON for a <script> element: no ``<``, ``>``, or ``&`` can end or comment it out."""
    return json.dumps(value).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")


def _asset(name: str) -> str:
    return (ASSET_DIR / name).read_text(encoding="utf-8")


def render_page(
    packet: Packet,
    answers: dict[str, Any],
    *,
    api: str,
    live: bool,
    packet_sha256: str,
    images: ImageResolver | None = None,
) -> str:
    """Self-contained HTML page: content server-rendered, state painted by page.js."""
    images = images or ImageResolver(None)
    total = len(packet.items)
    boot = {
        "api": api,
        "live": live,
        "packetSha256": packet_sha256,
        "answers": answers,
        "items": [
            {"id": i.id, "title": plain(i.title), "options": [o.label for o in i.options], "recommended": i.recommended}
            for i in packet.items
        ],
        "statusText": KIND_STATUS,
    }
    main = (
        _hero_html(packet)
        + _overview_html(packet)
        + _preface_html(packet, images)
        + "".join(_item_html(item, n, total, images) for n, item in enumerate(packet.items, 1))
        + FINAL_HTML
    )
    return Template(_asset("page.html")).substitute(
        title=html.escape(packet.title),
        css=_asset("page.css"),
        segments="".join(f'<i data-seg-for="{html.escape(i.id)}"></i>' for i in packet.items),
        total=total,
        banners=_banners(packet, answers, live, packet_sha256),
        nav=_nav_html(packet),
        main=main,
        boot=_script_safe_json(boot),
        js=_asset("page.js"),
    )

