from __future__ import annotations

import importlib
import json
import re
import sys
from pathlib import Path

import pytest


SCRIPTS = Path(__file__).parents[1] / "scripts"
FIXTURE = Path(__file__).parent / "fixtures" / "review-ready.md"
SVG = '<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"><rect width="10" height="10"/></svg>'


@pytest.fixture(scope="module")
def page():
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    return importlib.import_module("questionnaire_page")


@pytest.fixture
def text() -> str:
    return FIXTURE.read_text(encoding="utf-8")


def _render(page, text: str, live: bool = True, images=None, answers=None) -> str:
    return page.render_page(
        page.parse_packet(text),
        answers or {"items": {}, "completed_at": None, "apply_now": None, "packet_sha256": "x"},
        api="/q/t/api",
        live=live,
        packet_sha256="x",
        images=images,
    )


# ── parsing ──────────────────────────────────────────────────────────────────


def test_parse_reads_matrix_item_sections_and_recommendation(page, text):
    packet = page.parse_packet(text)
    [item] = packet.items
    assert (item.id, item.title, item.priority, item.state) == ("dec-auth", "choose credential authority", "P1", "Review-ready")
    assert item.direction == "Choose A: retain the single authority."
    assert [o.label for o in item.options] == ["A", "B"]
    assert item.recommended == "A"
    assert item.reviewed and not packet.drafts
    assert item.evidence_counts() == {"Observed": 1, "Inferred": 0, "Unknown": 1}


def test_parse_collects_preface_sections_before_the_first_item(page, text):
    text = text.replace("### `dec-auth`", "## Shared background\n\nThe vault is canonical.\n\n### `dec-auth`")
    assert page.parse_packet(text).preface == [("Shared background", "The vault is canonical.")]


def test_reading_time_ignores_audit_sections(page, text):
    padded = text.replace("- `spec.md:42`", "- `spec.md:42`\n" + "- padding evidence line\n" * 2000)
    assert page.parse_packet(padded).reading_minutes == page.parse_packet(text).reading_minutes


# ── markdown ─────────────────────────────────────────────────────────────────


def test_markdown_renders_blocks(page):
    out = page.markdown(
        "- **[Observed]** fact `x`\n  continued\n  - nested\n- second\n\n"
        "| a | b |\n|---|---|\n| 1 | 2 |\n\n"
        "```\n<b>raw</b>\n```\n\npara **bold** and *em*"
    )
    assert '<span class="ev ev-observed">Observed</span> fact <code>x</code> continued<ul><li>nested</li></ul>' in out
    assert "<li>second</li>" in out
    assert '<div class="table-wrap"><table>' in out and "<td>2</td>" in out
    assert "<pre><code>&lt;b&gt;raw&lt;/b&gt;</code></pre>" in out
    assert "<p>para <strong>bold</strong> and <em>em</em></p>" in out


def test_inline_keeps_code_literal_and_neutralizes_markup(page):
    assert page.inline("`**not bold**`") == "<code>**not bold**</code>"
    assert page.inline("globs a/*.py and b/*.md") == "globs a/*.py and b/*.md"
    assert "<script>" not in page.inline("<script>alert(1)</script>")
    link = page.inline('[x](https://e.com/"onmouseover="alert(1))')
    assert 'href="https://e.com/&quot;onmouseover=&quot;alert(1"' in link
    assert page.inline("[x](https://e.com)", links=False) == "x"
    assert "href" not in page.inline("[x](javascript:alert(1))")


def test_images_inline_only_from_inside_the_packet_directory(page, tmp_path):
    (tmp_path / "packet").mkdir()
    (tmp_path / "packet" / "delta.svg").write_text(SVG, encoding="utf-8")
    (tmp_path / "secret.svg").write_text(SVG, encoding="utf-8")
    (tmp_path / "packet" / "notes.txt").write_text("x", encoding="utf-8")
    images = page.ImageResolver(tmp_path / "packet")
    out = page.markdown("![Auth delta](delta.svg)\n\n![x](../secret.svg)\n\n![x](notes.txt)\n\n![x](https://e.com/a.svg)", images)
    assert out.count('src="data:image/svg+xml;base64,') == 1
    assert "<figcaption>Auth delta</figcaption>" in out
    assert out.count('class="figure missing"') == 3
    assert images.missing == ["../secret.svg", "notes.txt", "https://e.com/a.svg"]


# ── page ─────────────────────────────────────────────────────────────────────


def test_page_is_self_contained_and_escapes_packet_content(page, text):
    html = _render(page, text.replace("Choose the sole credential authority.", "Choose <script>alert(1)</script> authority."))
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert not re.search(r'<(?:script|link|img)[^>]+(?:src|href)="(?!data:)', html), "every resource is inline"
    assert "prefers-color-scheme: dark" in html and "use strict" in html
    assert 'id="item-dec-auth"' in html and 'data-label="A"' in html and 'data-label="B"' in html


def test_boot_payload_cannot_end_or_comment_out_its_script(page, text):
    answers = {"items": {"dec-auth": {"kind": "hold", "note": "<!--<script> & </script>"}}, "completed_at": None, "apply_now": None, "packet_sha256": "x"}
    html = _render(page, text.replace("choose credential authority", "evil </script><b>x"), answers=answers)
    boot = html.split('<script id="q-boot" type="application/json">', 1)[1].split("</script>", 1)[0]
    assert "<" not in boot and ">" not in boot
    assert json.loads(boot)["answers"]["items"]["dec-auth"]["note"] == "<!--<script> & </script>"


def test_boot_titles_are_plain_text_for_the_script(page, text):
    # page.js sets titles as textContent (the still-to-answer list); raw markdown would show its backticks.
    html = _render(page, text.replace("choose credential authority", "pick the `auth` **owner** for [docs](https://e.com)"))
    boot = json.loads(html.split('<script id="q-boot" type="application/json">', 1)[1].split("</script>", 1)[0])
    assert boot["items"][0]["title"] == "pick the auth owner for docs"
    assert page.plain("keep `**x**` and globs a/*.py, b/*.md") == "keep **x** and globs a/*.py, b/*.md"


def test_recommendation_leads_with_the_option_name(page, text):
    html = _render(page, text)
    assert '<span class="rec-tag">Recommended</span><span class="key key-sm" aria-hidden="true">A</span><strong>Shared authority</strong>' in html
    assert "It preserves the governing contract." in html  # "Choose **A** because" lead removed
    assert 'class="opt is-rec" data-label="A"' in html
    assert 'aria-labelledby="name-dec-auth-A" aria-describedby="tradeoffs-dec-auth-A"' in html
    assert '<span class="opt-name" id="name-dec-auth-A"><span class="sr-only">Option A: </span>Shared authority' in html


def test_long_unnamed_option_is_body_text_not_a_heading(page, text):
    long_outcome = "Bind the server to the tailnet address by default, falling back to loopback when absent"
    html = _render(page, text.replace("A. Shared authority", f"A. {long_outcome}"))
    assert f'<span class="sr-only">Option A: </span>{long_outcome} <span class="badge">' in html
    assert "<strong>Bind the server to the tailnet address by default</strong>" in html  # headline: first clause


def test_banners_flag_preview_drafts_and_packet_drift(page, text):
    clean = _render(page, text)
    visible = re.findall(r'<p class="banner[^"]*"(?![^>]*\bhidden\b)', clean)
    assert visible == []
    preview = _render(page, text, live=False)
    assert "Read-only rendering" in preview
    draft = text.replace("**Decision state:** Review-ready", "**Decision state:** Draft")
    assert "No item has passed adversarial review yet." in _render(page, draft)
    drifted = {"items": {"dec-auth": {"kind": "pick"}}, "packet_sha256": "older", "completed_at": None, "apply_now": None}
    assert "The packet changed after you started answering." in _render(page, text, answers=drifted)


def test_diagram_subsection_renders_as_a_figure(page, text, tmp_path):
    (tmp_path / "delta.svg").write_text(SVG, encoding="utf-8")
    text = text.replace("#### Authorization boundary", "#### Diagram\n\n![Before and after](delta.svg)\n\n#### Authorization boundary")
    html = _render(page, text, images=page.ImageResolver(tmp_path))
    diagram = html.split('<div class="diagram">', 1)[1]
    assert diagram.startswith('<figure class="figure"><img src="data:image/svg+xml;base64,')
