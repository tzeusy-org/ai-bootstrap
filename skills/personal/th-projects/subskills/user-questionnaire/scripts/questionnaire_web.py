#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Serve an owner-questionnaire packet as a local web form and ingest answers.

Deterministic, stdlib-only. One session publishes the packet; the owner
answers in a browser; any later session ingests the answers back into the
packet markdown and continues the normal apply workflow.

Subcommands
  serve   <packet.md>  start (or reuse) a background server; print the URL
  render  <packet.md>  write the HTML page to stdout or --out (no server)
  status  <packet.md>  print answer progress; exit 0 only when submitted
  ingest  <packet.md>  write recorded answers into the packet markdown
  stop    <packet.md>  stop the background server for this packet

State lives under $QUESTIONNAIRE_STATE_ROOT (default ~/.tmp/user-questionnaire)
in a directory derived from the packet's absolute path, so any session can
find it from the packet alone: answers.json, server.json, server.log.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import secrets
import signal
import socket
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.request import urlopen

sys.path.insert(0, str(Path(__file__).resolve().parent))
import validate_questionnaire as vq  # noqa: E402

DEFAULT_STATE_ROOT = Path("~/.tmp/user-questionnaire")
ANSWER_KINDS = {"pick", "edit", "defer", "reject"}
PENDING_OWNER_STATES = {"Review-ready"}


# ── packet model ──────────────────────────────────────────────────────────────


def _option_rows(body: str) -> list[dict[str, str]]:
    rows = []
    for line in body.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) != 3:
            continue
        match = re.fullmatch(r"([A-D])\.\s*(.*)", cells[0])
        if match:
            rows.append(
                {"label": match.group(1), "outcome": match.group(2), "pros": cells[1], "cons": cells[2]}
            )
    return rows


def parse_packet(text: str) -> dict[str, Any]:
    """Structured view of a packet; mirrors the validator's section model."""
    title_match = re.search(r"^# (.+)$", text, re.MULTILINE)
    snapshot = re.search(r"^\*\*Evidence snapshot:\*\*\s*(.+)$", text, re.MULTILINE)
    boundary = re.search(r"^\*\*Artifact boundary:\*\*\s*(.+)$", text, re.MULTILINE)
    summary, _ = vq._summary_rows(text)
    priorities: dict[str, str] = {}
    for line in text.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) >= 5 and cells[1].startswith("`"):
            priorities[cells[1].strip("`")] = cells[2]
    blocks, _ = vq._section_blocks(text)
    items = []
    for decision_id, block in blocks:
        heading = vq.SECTION_RE.search(block)
        title = ""
        if heading:
            first_line = block.splitlines()[0]
            title = first_line.split("—", 1)[1].strip() if "—" in first_line else ""
        rec_body = vq._subsection(block, "Recommendation") or ""
        rec_match = re.search(r"\b(?:Choose|Recommend)\s+\*\*([A-D])\*\*", rec_body)
        items.append(
            {
                "id": decision_id,
                "title": title,
                "priority": priorities.get(decision_id, ""),
                "state": vq._field(block, "Decision state") or "",
                "review": vq._field(block, "Adversarial review") or "",
                "decision_needed": vq._subsection(block, "Decision needed") or "",
                "background": vq._subsection(block, "Background and freshness") or "",
                "options": _option_rows(vq._subsection(block, "Options") or ""),
                "recommendation": rec_body,
                "recommended": rec_match.group(1) if rec_match else "",
                "boundary": vq._subsection(block, "Authorization boundary") or "",
                "review_record": vq._subsection(block, "Adversarial review record") or "",
                "owner_record": vq._subsection(block, "Owner decision record") or "",
                "evidence": vq._subsection(block, "Evidence") or "",
            }
        )
    return {
        "title": title_match.group(1).strip() if title_match else "Owner decision questionnaire",
        "snapshot": snapshot.group(1).strip() if snapshot else "",
        "boundary": boundary.group(1).strip() if boundary else "",
        "items": items,
    }


# ── minimal markdown → html ───────────────────────────────────────────────────

_LABEL_CLASS = {"Observed": "obs", "Inferred": "inf", "Unknown": "unk"}


def _inline(text: str) -> str:
    out = html.escape(text, quote=False)
    out = re.sub(r"`([^`]+)`", r"<code>\1</code>", out)
    out = re.sub(
        r"\*\*\[(Observed|Inferred|Unknown)\]\*\*",
        lambda m: f'<span class="lbl {_LABEL_CLASS[m.group(1)]}">{m.group(1)}</span>',
        out,
    )
    out = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", out)
    out = re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", r'<a href="\2" rel="noopener">\1</a>', out)
    return out


def md_to_html(body: str) -> str:
    """Paragraphs, bullet/numbered lists, pipe tables, inline bold/code/links."""
    lines = body.splitlines()
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if not stripped:
            i += 1
            continue
        if stripped.startswith("|"):
            rows = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                if not all(re.fullmatch(r":?-{2,}:?", c) for c in cells):
                    rows.append(cells)
                i += 1
            if rows:
                head, *rest = rows
                out.append("<table><thead><tr>" + "".join(f"<th>{_inline(c)}</th>" for c in head) + "</tr></thead>")
                out.append("<tbody>" + "".join("<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in r) + "</tr>" for r in rest) + "</tbody></table>")
            continue
        list_match = re.match(r"^(\s*)([-*]|\d+\.)\s+(.*)$", line)
        if list_match:
            tag = "ol" if list_match.group(2)[0].isdigit() else "ul"
            out.append(f"<{tag}>")
            while i < len(lines):
                m = re.match(r"^(\s*)([-*]|\d+\.)\s+(.*)$", lines[i])
                if not m:
                    # continuation line of the previous bullet
                    if lines[i].startswith("  ") and lines[i].strip() and out[-1].startswith("<li>"):
                        out[-1] = out[-1][:-5] + " " + _inline(lines[i].strip()) + "</li>"
                        i += 1
                        continue
                    break
                out.append(f"<li>{_inline(m.group(3))}</li>")
                i += 1
            out.append(f"</{tag}>")
            continue
        para = [stripped]
        i += 1
        while i < len(lines) and lines[i].strip() and not re.match(r"^\s*([-*|]|\d+\.)\s", lines[i]):
            para.append(lines[i].strip())
            i += 1
        out.append(f"<p>{_inline(' '.join(para))}</p>")
    return "\n".join(out)


# ── state ─────────────────────────────────────────────────────────────────────


def state_root() -> Path:
    return Path(os.environ.get("QUESTIONNAIRE_STATE_ROOT", str(DEFAULT_STATE_ROOT))).expanduser()


def state_dir_for(packet: Path, override: str | None = None) -> Path:
    if override:
        return Path(override).expanduser().resolve()
    resolved = packet.expanduser().resolve()
    digest = hashlib.sha1(str(resolved).encode()).hexdigest()[:8]
    return state_root() / f"{resolved.stem}-{digest}"


def packet_digest(packet: Path) -> str:
    return hashlib.sha256(packet.read_bytes()).hexdigest()


def atomic_write(path: Path, data: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(data, encoding="utf-8")
    os.replace(tmp, path)


def empty_answers(packet: Path) -> dict[str, Any]:
    return {
        "schema": 1,
        "packet": str(packet.expanduser().resolve()),
        "packet_sha256": packet_digest(packet),
        "items": {},
        "completed_at": None,
        "apply_now": None,
    }


def load_answers(state_dir: Path, packet: Path) -> dict[str, Any]:
    path = state_dir / "answers.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return empty_answers(packet)


def save_answers(state_dir: Path, answers: dict[str, Any]) -> None:
    atomic_write(state_dir / "answers.json", json.dumps(answers, indent=2, sort_keys=True) + "\n")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ── html page ─────────────────────────────────────────────────────────────────

CSS = """
:root{--bg:#f7f7f5;--card:#fff;--ink:#1b1b1a;--mute:#5f5e5a;--line:#e3e2dd;--acc:#2456c7;--acc-ink:#fff;--ok:#1d7a4a;--warn:#a85d00;--bad:#b3261e;--chip:#eeede8;--sel:#e8effc;--sel-line:#9fb6ea;--mono:ui-monospace,SFMono-Regular,Menlo,monospace;--sans:system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#141414;--card:#1e1e1e;--ink:#ececea;--mute:#a7a6a1;--line:#2e2e2c;--acc:#7ea2ff;--acc-ink:#0b1430;--ok:#5fcf8f;--warn:#ffb55c;--bad:#ff7b72;--chip:#2a2a28;--sel:#1b2742;--sel-line:#3d5fb5}}
:root[data-theme="dark"]{--bg:#141414;--card:#1e1e1e;--ink:#ececea;--mute:#a7a6a1;--line:#2e2e2c;--acc:#7ea2ff;--acc-ink:#0b1430;--ok:#5fcf8f;--warn:#ffb55c;--bad:#ff7b72;--chip:#2a2a28;--sel:#1b2742;--sel-line:#3d5fb5}
*{box-sizing:border-box}html{scroll-padding-top:5rem}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 var(--sans)}
a{color:var(--acc)}code{font-family:var(--mono);font-size:.9em;background:var(--chip);padding:.05em .3em;border-radius:4px}
header{position:sticky;top:0;z-index:5;background:var(--bg);border-bottom:1px solid var(--line);padding:.6rem 16px}
.hd{max-width:1180px;margin:0 auto;display:flex;gap:1rem;align-items:center;flex-wrap:wrap}
.hd h1{font-size:1.05rem;margin:0;flex:1 1 auto}
.prog{display:flex;align-items:center;gap:.5rem;color:var(--mute);font-size:.85rem;white-space:nowrap}
.bar{width:140px;height:6px;background:var(--chip);border-radius:3px;overflow:hidden}.bar i{display:block;height:100%;background:var(--acc);width:0}
.wrap{max-width:1180px;margin:0 auto;padding:1rem 16px 6rem;display:grid;grid-template-columns:220px 1fr;gap:1.5rem}
@media (max-width:860px){.wrap{grid-template-columns:1fr}nav.toc{position:static;display:flex;flex-wrap:wrap;gap:.35rem}nav.toc a{border:1px solid var(--line);border-radius:999px;padding:.15rem .6rem}}
nav.toc{position:sticky;top:4.2rem;align-self:start;font-size:.85rem}
nav.toc a{display:flex;gap:.45rem;align-items:center;color:var(--ink);text-decoration:none;padding:.25rem .3rem;border-radius:6px}
nav.toc a:hover{background:var(--chip)}nav.toc a.cur{background:var(--sel)}
.dot{width:8px;height:8px;border-radius:50%;background:var(--line);flex:none}.dot.on{background:var(--ok)}.dot.held{background:var(--warn)}.dot.rej{background:var(--bad)}.dot.edit{background:var(--acc)}
.meta{color:var(--mute);font-size:.85rem;margin:.2rem 0 1rem}
.note{border-left:3px solid var(--warn);padding:.4rem .8rem;background:var(--card);border-radius:0 8px 8px 0;margin:0 0 1.2rem;font-size:.9rem}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:1.1rem 1.25rem;margin-bottom:1.25rem}
.card:focus-within,.card.cur{border-color:var(--sel-line)}
.card h2{font-size:1.1rem;margin:0 0 .15rem;display:flex;gap:.6rem;align-items:baseline;flex-wrap:wrap}
.card h2 code{font-size:.8rem}.chip{font-size:.72rem;padding:.1rem .5rem;border-radius:999px;background:var(--chip);color:var(--mute);font-weight:500}
.lead{font-size:1rem;margin:.6rem 0}.lead p{margin:0}
.rec{border:1px dashed var(--sel-line);background:var(--sel);border-radius:8px;padding:.5rem .8rem;margin:.6rem 0;font-size:.92rem}.rec p{margin:0}
details{margin:.5rem 0;font-size:.92rem}summary{cursor:pointer;color:var(--mute);user-select:none}
details[open]>summary{margin-bottom:.3rem}
.lbl{font-size:.7rem;font-weight:600;letter-spacing:.03em;text-transform:uppercase;padding:.05em .4em;border-radius:4px;margin-right:.3em;background:var(--chip)}
.lbl.obs{color:var(--ok)}.lbl.inf{color:var(--warn)}.lbl.unk{color:var(--bad)}
table{border-collapse:collapse;width:100%;font-size:.9rem;margin:.4rem 0}th,td{text-align:left;padding:.3rem .5rem;border-bottom:1px solid var(--line);vertical-align:top}th{color:var(--mute);font-weight:600}
.opts{display:grid;gap:.5rem;margin:.6rem 0}
.opt{display:grid;grid-template-columns:2rem 1fr;gap:.6rem;border:1px solid var(--line);border-radius:10px;padding:.6rem .75rem;cursor:pointer;background:var(--bg)}
.opt:hover{border-color:var(--sel-line)}.opt.sel{background:var(--sel);border-color:var(--sel-line)}
.opt .k{font:600 .95rem var(--mono);width:1.6rem;height:1.6rem;border-radius:6px;display:grid;place-items:center;background:var(--chip)}
.opt.sel .k{background:var(--acc);color:var(--acc-ink)}
.opt .o{font-weight:600}.opt .pc{display:grid;grid-template-columns:1fr 1fr;gap:.5rem;font-size:.85rem;color:var(--mute);margin-top:.2rem}
@media (max-width:600px){.opt .pc{grid-template-columns:1fr}}
.opt .pc b{color:var(--ink);font-weight:600}
.rc{font-size:.7rem;color:var(--acc);margin-left:.4rem;font-weight:600}
.acts{display:flex;gap:.5rem;flex-wrap:wrap;margin:.6rem 0 .3rem;align-items:center}
button{font:inherit;font-size:.86rem;border:1px solid var(--line);background:var(--card);color:var(--ink);padding:.35rem .75rem;border-radius:8px;cursor:pointer}
button:hover{border-color:var(--sel-line)}button.on{background:var(--acc);color:var(--acc-ink);border-color:var(--acc)}
button.pri{background:var(--acc);color:var(--acc-ink);border-color:var(--acc);font-weight:600}
button:disabled{opacity:.5;cursor:default}
textarea{width:100%;font:inherit;font-size:.9rem;border:1px solid var(--line);border-radius:8px;padding:.45rem .6rem;background:var(--bg);color:var(--ink);min-height:2.6rem;resize:vertical}
textarea:focus{outline:2px solid var(--sel-line);outline-offset:1px}
.fld{margin:.4rem 0}.fld label{display:block;font-size:.8rem;color:var(--mute);margin-bottom:.2rem}
.saved{font-size:.78rem;color:var(--mute);margin-left:auto}.saved.ok{color:var(--ok)}.saved.err{color:var(--bad)}
.final{border:2px solid var(--acc);}
.final h2{font-size:1.05rem}
.kbd{font-family:var(--mono);font-size:.75rem;background:var(--chip);border:1px solid var(--line);border-radius:4px;padding:0 .3em}
.help{font-size:.8rem;color:var(--mute);margin-top:1rem}
.done{color:var(--ok);font-weight:600}
.ro .opt,.ro button:not(.nav){pointer-events:none;opacity:.7}
"""

JS = r"""
const S=window.__Q;const items=S.packet.items;const api=S.api;
let answers=S.answers;let cur=0;const $=(s,r=document)=>r.querySelector(s);const $$=(s,r=document)=>[...r.querySelectorAll(s)];
function kindOf(a){return a?a.kind:null}
async function post(path,body){const r=await fetch(api+path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});if(!r.ok)throw new Error(await r.text());return r.json()}
function paintItem(id){const a=answers.items[id];const card=$('#item-'+CSS.escape(id));if(!card)return;
 $$('.opt',card).forEach(o=>o.classList.toggle('sel',!!a&&a.kind==='pick'&&a.choice===o.dataset.label));
 $$('.acts button[data-kind]',card).forEach(b=>b.classList.toggle('on',!!a&&a.kind===b.dataset.kind));
 const ed=$('.edit-fld',card);ed.hidden=!(a&&a.kind==='edit');
 const why=$('.why-fld',card);why.hidden=!(a&&(a.kind==='defer'||a.kind==='reject'));
 if(a){ if(a.kind==='edit')$('textarea',ed).value=a.choice||''; if(a.kind==='defer'||a.kind==='reject')$('textarea',why).value=a.note||''; }
 const sv=$('.saved',card);if(a&&a.recorded_at){sv.textContent='recorded '+a.recorded_at.replace('T',' ').slice(0,16);sv.className='saved ok'}else{sv.textContent='unanswered';sv.className='saved'}
 const dot=$('#toc-'+CSS.escape(id)+' .dot');dot.className='dot '+(a?({pick:'on',edit:'edit',defer:'held',reject:'rej'})[a.kind]:'');
 paintProgress()}
function paintProgress(){const n=Object.keys(answers.items).length;const t=items.length;$('.bar i').style.width=(t?100*n/t:0)+'%';$('#prog-n').textContent=n+'/'+t+' answered';
 const fin=$('#final');const done=!!answers.completed_at;$('#final-state').innerHTML=done?'<span class="done">Submitted '+answers.completed_at.replace('T',' ').slice(0,16)+'</span>':(n<t?'Answer every item to submit.':'All items answered.');
 $('#btn-submit').disabled=n<t||done;$('#btn-reopen').hidden=!done;document.body.classList.toggle('ro',done);
 $$('#final .apply button').forEach(b=>b.classList.toggle('on',String(answers.apply_now)===b.dataset.v))}
async function record(id,kind,choice,note){const card=$('#item-'+CSS.escape(id));const sv=$('.saved',card);sv.textContent='saving…';sv.className='saved';
 try{const r=await post('/answer',{id,kind,choice,note});answers=r;paintItem(id)}catch(e){sv.textContent='save failed: '+e.message;sv.className='saved err'}}
function current(id){return answers.items[id]||{}}
document.addEventListener('click',e=>{const opt=e.target.closest('.opt');if(opt){const id=opt.closest('.card').dataset.id;record(id,'pick',opt.dataset.label,current(id).note||'');return}
 const b=e.target.closest('button[data-kind]');if(b){const id=b.closest('.card').dataset.id;const k=b.dataset.kind;const a=current(id);
  if(k==='edit'){record(id,'edit',a.kind==='edit'?a.choice:'',a.note||'');setTimeout(()=>$('.edit-fld textarea',b.closest('.card')).focus(),50)}
  else record(id,k,'',a.kind===k?a.note||'':'');return}
 const ap=e.target.closest('#final .apply button');if(ap){post('/complete',{apply_now:ap.dataset.v==='true'}).then(r=>{answers=r;paintProgress()});return}
 if(e.target.id==='btn-submit'){post('/complete',{completed:true}).then(r=>{answers=r;paintProgress()});return}
 if(e.target.id==='btn-reopen'){post('/complete',{completed:false}).then(r=>{answers=r;paintProgress()});return}
 if(e.target.closest('nav.toc a')){setTimeout(()=>{cur=items.findIndex(i=>'#item-'+i.id===e.target.closest('a').getAttribute('href'));mark()},50)}});
let tmr={};document.addEventListener('input',e=>{const ta=e.target;if(ta.tagName!=='TEXTAREA')return;const card=ta.closest('.card');const id=card.dataset.id;const a=current(id);
 clearTimeout(tmr[id]);tmr[id]=setTimeout(()=>{if(ta.closest('.edit-fld'))record(id,'edit',ta.value,a.note||'');else if(ta.closest('.why-fld'))record(id,a.kind,'',ta.value);else record(id,a.kind||'pick',a.choice||'',ta.value)},500)});
function mark(){$$('.card[data-id]').forEach((c,i)=>c.classList.toggle('cur',i===cur));$$('nav.toc a').forEach((a,i)=>a.classList.toggle('cur',i===cur))}
function go(i){cur=Math.max(0,Math.min(items.length-1,i));mark();$('#item-'+CSS.escape(items[cur].id)).scrollIntoView({block:'start'})}
document.addEventListener('keydown',e=>{if(e.target.tagName==='TEXTAREA'||e.metaKey||e.ctrlKey||e.altKey)return;const k=e.key;const id=items[cur]&&items[cur].id;
 if(k==='j'||k==='ArrowDown'&&e.shiftKey)go(cur+1);else if(k==='k'||k==='ArrowUp'&&e.shiftKey)go(cur-1);
 else if(/^[a-d]$/.test(k)&&id){const L=k.toUpperCase();if($('#item-'+CSS.escape(id)+' .opt[data-label="'+L+'"]'))record(id,'pick',L,current(id).note||'')}
 else if(k==='e'&&id){record(id,'edit',current(id).kind==='edit'?current(id).choice:'',current(id).note||'')}
 else if(k==='h'&&id)record(id,'defer','',current(id).kind==='defer'?current(id).note||'':'');
 else if(k==='r'&&id)record(id,'reject','',current(id).kind==='reject'?current(id).note||'':'');
 else if(k==='?')alert('j / k  next / previous item\na-d     choose option\ne       edit the choice\nh       hold (defer)\nr       reject\n?       this help');else return;e.preventDefault()});
const io=new IntersectionObserver(es=>{es.forEach(x=>{if(x.isIntersecting){cur=items.findIndex(i=>'item-'+i.id===x.target.id);mark()}})},{rootMargin:'-40% 0px -55% 0px'});
$$('.card[data-id]').forEach(c=>io.observe(c));
items.forEach(i=>paintItem(i.id));paintProgress();
if(!S.live)document.body.classList.add('ro');
"""


def _item_html(item: dict[str, Any]) -> str:
    opts = []
    for o in item["options"]:
        rec = '<span class="rc">recommended</span>' if o["label"] == item["recommended"] else ""
        opts.append(
            f'<div class="opt" data-label="{o["label"]}" role="button" tabindex="0">'
            f'<span class="k">{o["label"]}</span><div><div class="o">{_inline(o["outcome"])}{rec}</div>'
            f'<div class="pc"><div><b>Pros</b> {_inline(o["pros"])}</div><div><b>Cons</b> {_inline(o["cons"])}</div></div></div></div>'
        )
    title = _inline(item["title"]) if item["title"] else ""
    pri = f'<span class="chip">{html.escape(item["priority"])}</span>' if item["priority"] else ""
    state = f'<span class="chip">{html.escape(item["state"])}</span>'
    review = item["review"]
    review_chip = f'<span class="chip" title="{html.escape(review)}">{"reviewed" if review.lower().startswith("passed") else html.escape(review[:24])}</span>' if review else ""
    return f"""
<section class="card" id="item-{html.escape(item['id'])}" data-id="{html.escape(item['id'])}">
  <h2><code>{html.escape(item['id'])}</code> {title} {pri}{state}{review_chip}</h2>
  <div class="lead">{md_to_html(item['decision_needed'])}</div>
  <div class="rec">{md_to_html(item['recommendation'])}</div>
  <details open><summary>Background and freshness</summary>{md_to_html(item['background'])}</details>
  <div class="opts">{''.join(opts)}</div>
  <div class="acts">
    <button type="button" data-kind="edit" title="e">Edit choice</button>
    <button type="button" data-kind="defer" title="h">Hold / defer</button>
    <button type="button" data-kind="reject" title="r">Reject all</button>
    <span class="saved">unanswered</span>
  </div>
  <div class="fld edit-fld" hidden><label>Your edited option (triggers re-review before routing)</label><textarea placeholder="State the option you want instead"></textarea></div>
  <div class="fld why-fld" hidden><label>Reason or re-ask trigger</label><textarea placeholder="Why hold or reject? What would change your answer?"></textarea></div>
  <details><summary>Authorization boundary</summary>{md_to_html(item['boundary'])}</details>
  <details><summary>Adversarial review record</summary>{md_to_html(item['review_record'])}</details>
  <details><summary>Evidence</summary>{md_to_html(item['evidence'])}</details>
</section>"""


def render_html(packet: dict[str, Any], answers: dict[str, Any], api_prefix: str, live: bool) -> str:
    toc = "".join(
        f'<a href="#item-{html.escape(i["id"])}" id="toc-{html.escape(i["id"])}"><span class="dot"></span><span>{html.escape(i["id"])}</span></a>'
        for i in packet["items"]
    )
    body = "".join(_item_html(i) for i in packet["items"])
    payload = json.dumps({"packet": packet, "answers": answers, "api": api_prefix, "live": live}).replace("</", "<\\/")
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(packet['title'])}</title><style>{CSS}</style></head>
<body>
<header><div class="hd"><h1>{html.escape(packet['title'])}</h1><div class="prog"><span id="prog-n">0/0 answered</span><span class="bar"><i></i></span></div></div></header>
<div class="wrap">
<nav class="toc">{toc}</nav>
<main>
  <p class="meta">{_inline(packet['snapshot'])}</p>
  <div class="note"><strong>Boundary.</strong> {_inline(packet['boundary'])} Your answers are recorded locally and routed only by a later explicit apply step.</div>
  {body}
  <section class="card final" id="final">
    <h2>Submit your review</h2>
    <p id="final-state"></p>
    <p>Apply the recorded decisions now? This releases routing of exactly the agreed and held items in this packet through their owning workflows. It approves no external, live, deployment, or merge action.</p>
    <div class="acts apply"><button type="button" data-v="true">Yes, apply when ingested</button><button type="button" data-v="false">No, record only</button></div>
    <div class="acts"><button type="button" class="pri" id="btn-submit">Submit review</button><button type="button" id="btn-reopen" hidden>Reopen</button></div>
    <p class="help">Keys: <span class="kbd">j</span>/<span class="kbd">k</span> move, <span class="kbd">a</span>–<span class="kbd">d</span> choose, <span class="kbd">e</span> edit, <span class="kbd">h</span> hold, <span class="kbd">r</span> reject, <span class="kbd">?</span> help. Answers save as you go.</p>
  </section>
</main>
</div>
<script id="q-data" type="application/json">{payload}</script>
<script>window.__Q=JSON.parse(document.getElementById('q-data').textContent);{JS}</script>
</body></html>"""


# ── server ────────────────────────────────────────────────────────────────────


class QuestionnaireServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, bind: tuple[str, int], packet: Path, state_dir: Path, token: str):
        super().__init__(bind, Handler)
        self.packet = packet
        self.state_dir = state_dir
        self.token = token
        self.prefix = f"/q/{token}"
        self.answers = load_answers(state_dir, packet)
        self.packet_text = packet.read_text(encoding="utf-8")
        self.model = parse_packet(self.packet_text)
        self.item_ids = {i["id"] for i in self.model["items"]}
        self.lock = threading.Lock()


class Handler(BaseHTTPRequestHandler):
    server: QuestionnaireServer

    def log_message(self, fmt: str, *args: Any) -> None:  # quieter log
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, obj: Any) -> None:
        self._send(code, json.dumps(obj).encode(), "application/json")

    def _route(self) -> str | None:
        path = self.path.split("?", 1)[0]
        if path == "/health":
            return "/health"
        if not path.startswith(self.server.prefix):
            return None
        return path[len(self.server.prefix) :] or "/"

    def do_GET(self) -> None:  # noqa: N802
        route = self._route()
        if route == "/health":
            return self._json(200, {"ok": True, "packet": str(self.server.packet)})
        if route is None:
            return self._send(404, b"not found", "text/plain")
        with self.server.lock:
            answers = self.server.answers
        if route in ("/", "/index.html"):
            page = render_html(self.server.model, answers, self.server.prefix + "/api", live=True)
            return self._send(200, page.encode(), "text/html; charset=utf-8")
        if route in ("/api/state", "/answers.json"):
            return self._json(200, answers)
        return self._send(404, b"not found", "text/plain")

    def do_POST(self) -> None:  # noqa: N802
        route = self._route()
        if route not in ("/api/answer", "/api/complete"):
            return self._send(404, b"not found", "text/plain")
        length = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            return self._json(400, {"error": "invalid json"})
        actor = f"owner via questionnaire web form (token-gated, from {self.client_address[0]})"
        with self.server.lock:
            answers = self.server.answers
            if route == "/api/answer":
                if answers.get("completed_at"):
                    return self._json(409, {"error": "review already submitted; reopen first"})
                item_id = body.get("id")
                kind = body.get("kind")
                if item_id not in self.server.item_ids:
                    return self._json(400, {"error": f"unknown item {item_id!r}"})
                if kind not in ANSWER_KINDS:
                    return self._json(400, {"error": f"kind must be one of {sorted(ANSWER_KINDS)}"})
                choice = str(body.get("choice") or "").strip()
                if kind == "pick" and not re.fullmatch(r"[A-D]", choice):
                    return self._json(400, {"error": "pick needs choice A-D"})
                answers["items"][item_id] = {
                    "kind": kind,
                    "choice": choice,
                    "note": str(body.get("note") or "").strip(),
                    "recorded_at": now_iso(),
                    "actor": actor,
                }
            else:
                if "completed" in body:
                    answers["completed_at"] = now_iso() if body["completed"] else None
                if "apply_now" in body:
                    answers["apply_now"] = bool(body["apply_now"])
                answers["completed_actor"] = actor
            save_answers(self.server.state_dir, answers)
            return self._json(200, answers)


def make_server(packet: Path, state_dir: Path, bind: str, port: int, token: str | None = None) -> QuestionnaireServer:
    return QuestionnaireServer((bind, port), packet, state_dir, token or secrets.token_urlsafe(12))


def public_host(override: str | None = None) -> str:
    if override:
        return override
    if os.environ.get("QUESTIONNAIRE_HOST"):
        return os.environ["QUESTIONNAIRE_HOST"]
    try:
        out = subprocess.run(["tailscale", "status", "--self", "--json"], capture_output=True, text=True, timeout=5)
        if out.returncode == 0:
            name = json.loads(out.stdout).get("Self", {}).get("DNSName", "").rstrip(".")
            if name:
                return name
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        pass
    fqdn = socket.getfqdn()
    return fqdn if "." in fqdn else socket.gethostname()


def _pid_alive(pid: int) -> bool:
    try:  # reap if it is our own exited child, otherwise a zombie still answers kill(0)
        if os.waitpid(pid, os.WNOHANG)[0] == pid:
            return False
    except ChildProcessError:
        pass
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _terminate(pid: int, timeout: float = 5.0) -> bool:
    """SIGTERM, wait, then SIGKILL; True when the process is gone."""
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        return True
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not _pid_alive(pid):
            return True
        time.sleep(0.05)
    try:
        os.kill(pid, signal.SIGKILL)
    except OSError:
        return True
    time.sleep(0.1)
    return not _pid_alive(pid)


def read_server_info(state_dir: Path) -> dict[str, Any] | None:
    path = state_dir / "server.json"
    if not path.exists():
        return None
    info = json.loads(path.read_text(encoding="utf-8"))
    if not _pid_alive(int(info.get("pid", -1))):
        return None
    try:
        with urlopen(f"http://127.0.0.1:{info['port']}/health", timeout=2) as r:
            if json.load(r).get("ok"):
                return info
    except OSError:
        return None
    return None


# ── commands ──────────────────────────────────────────────────────────────────


def cmd_render(args: argparse.Namespace) -> int:
    packet = Path(args.packet)
    state_dir = state_dir_for(packet, args.state_dir)
    page = render_html(parse_packet(packet.read_text(encoding="utf-8")), load_answers(state_dir, packet), "/q/preview/api", live=False)
    if args.out:
        atomic_write(Path(args.out), page)
        print(args.out)
    else:
        sys.stdout.write(page)
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    packet = Path(args.packet).expanduser().resolve()
    if not packet.exists():
        print(f"ERROR: packet not found: {packet}", file=sys.stderr)
        return 2
    state_dir = state_dir_for(packet, args.state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    if not (state_dir / "answers.json").exists():
        save_answers(state_dir, empty_answers(packet))

    if args.foreground:
        httpd = make_server(packet, state_dir, args.bind, args.port, args.token)
        port = httpd.server_address[1]
        host = public_host(args.host)
        info = {
            "pid": os.getpid(),
            "port": port,
            "bind": args.bind,
            "host": host,
            "token": httpd.token,
            "url": f"http://{host}:{port}{httpd.prefix}/",
            "packet": str(packet),
            "state_dir": str(state_dir),
            "started_at": now_iso(),
        }
        atomic_write(state_dir / "server.json", json.dumps(info, indent=2) + "\n")
        print(info["url"], flush=True)
        # shutdown() blocks until serve_forever() returns, so it must not run on
        # the thread that is inside serve_forever(); hand it to a helper thread.
        signal.signal(
            signal.SIGTERM,
            lambda *_: threading.Thread(target=httpd.shutdown, daemon=True).start(),
        )
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            httpd.server_close()
        return 0

    existing = read_server_info(state_dir)
    if existing and not args.restart:
        _print_serve_result(existing, state_dir, reused=True)
        return 0
    if existing and not _terminate(int(existing["pid"])):
        print(f"ERROR: could not stop running server pid {existing['pid']}", file=sys.stderr)
        return 1
    (state_dir / "server.json").unlink(missing_ok=True)
    cmd = [sys.executable, str(Path(__file__).resolve()), "serve", str(packet), "--foreground",
           "--state-dir", str(state_dir), "--bind", args.bind, "--port", str(args.port)]
    if args.host:
        cmd += ["--host", args.host]
    if args.token:
        cmd += ["--token", args.token]
    log = open(state_dir / "server.log", "ab")
    subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, start_new_session=True, close_fds=True)
    deadline = time.time() + 15
    while time.time() < deadline:
        info = read_server_info(state_dir)
        if info:
            _print_serve_result(info, state_dir, reused=False)
            return 0
        time.sleep(0.2)
    print(f"ERROR: server did not start; see {state_dir / 'server.log'}", file=sys.stderr)
    return 1


def _print_serve_result(info: dict[str, Any], state_dir: Path, reused: bool) -> None:
    print(json.dumps({"url": info["url"], "port": info["port"], "pid": info["pid"], "state_dir": str(state_dir),
                      "answers": str(state_dir / "answers.json"), "reused": reused}))
    print(f"QUESTIONNAIRE_URL={info['url']}")


def progress(packet: Path, state_dir: Path) -> dict[str, Any]:
    model = parse_packet(packet.read_text(encoding="utf-8"))
    answers = load_answers(state_dir, packet)
    ids = [i["id"] for i in model["items"]]
    answered = [i for i in ids if i in answers["items"]]
    return {
        "packet": str(packet), "state_dir": str(state_dir), "total": len(ids), "answered": len(answered),
        "unanswered": [i for i in ids if i not in answers["items"]],
        "completed_at": answers.get("completed_at"), "apply_now": answers.get("apply_now"),
        "packet_changed": answers.get("packet_sha256") != packet_digest(packet),
        "server": read_server_info(state_dir) is not None,
        "kinds": {k: sum(1 for i in answered if answers["items"][i]["kind"] == k) for k in sorted(ANSWER_KINDS)},
    }


def cmd_status(args: argparse.Namespace) -> int:
    packet = Path(args.packet).expanduser().resolve()
    state_dir = state_dir_for(packet, args.state_dir)
    deadline = time.time() + (args.wait or 0)
    while True:
        p = progress(packet, state_dir)
        if p["completed_at"] or time.time() >= deadline:
            break
        time.sleep(min(30, max(1, deadline - time.time())))
    print(json.dumps(p, indent=2))
    return 0 if p["completed_at"] else 3


STATE_FOR_KIND = {"pick": "Agreed", "edit": "Needs-rework", "defer": "Held", "reject": "Rejected"}


def _owner_record(kind: str, ans: dict[str, Any]) -> dict[str, str]:
    note = ans.get("note") or ""
    if kind == "pick":
        choice = ans["choice"] + (f" — {note}" if note else "")
        review = "Not required — plain selection of a presented option"
        edits = "None"
    elif kind == "edit":
        choice = f"Edited: {ans['choice'] or 'owner-specified option'}"
        review = "Pending — fresh scope and recommendation review required before routing"
        edits = ans["choice"] or "owner-specified option"
    elif kind == "defer":
        choice = f"Deferred: {note or 'owner deferred without a stated trigger'}"
        review = "Not required — deferral"
        edits = "None"
    else:
        choice = f"Rejected: {note or 'owner rejected every presented option'}"
        review = "Not required — rejection"
        edits = "None"
    return {
        "Status": STATE_FOR_KIND[kind],
        "Actor/channel": ans.get("actor", "owner via questionnaire web form"),
        "Recorded at": ans["recorded_at"],
        "Choice": choice,
        "Owner edits": edits,
        "Post-answer review": review,
        "Final authorization boundary": "As stated in this item's Authorization boundary; unchanged by the owner",
        "Canonical destination": "Unrouted — the applying agent names the owning workflow at apply time",
        "Routing evidence": "Not routed",
    }


def apply_answers_to_packet(text: str, answers: dict[str, Any]) -> tuple[str, list[str]]:
    """Return packet text with owner records written in, plus a change log."""
    blocks, _ = vq._section_blocks(text)
    log: list[str] = []
    for decision_id, block in blocks:
        ans = answers["items"].get(decision_id)
        if not ans:
            continue
        kind = ans["kind"]
        new_state = STATE_FOR_KIND[kind]
        new_block = re.sub(r"^(\*\*Decision state:\*\*)\s*.+$", rf"\1 {new_state}", block, count=1, flags=re.MULTILINE)
        record = _owner_record(kind, ans)

        def _rewrite_record(m: re.Match[str]) -> str:
            body = m.group("body")
            for label, value in record.items():
                body, n = re.subn(rf"^- {re.escape(label)}:.*$", f"- {label}: {value}", body, count=1, flags=re.MULTILINE)
                if n == 0:
                    body = body.rstrip("\n") + f"\n- {label}: {value}\n"
            return m.group("head") + body

        new_block, n = re.subn(
            r"(?P<head>^#### Owner decision record\s*$\n)(?P<body>.*?)(?=^#### |\Z)",
            _rewrite_record, new_block, count=1, flags=re.MULTILINE | re.DOTALL,
        )
        if n == 0:
            log.append(f"{decision_id}: no Owner decision record section; skipped")
            continue
        text = text.replace(block, new_block, 1)
        log.append(f"{decision_id}: {kind} -> {new_state} ({record['Choice'][:60]})")
        # summary matrix row
        text = re.sub(
            rf"^(\|\s*\d+\s*\|\s*`{re.escape(decision_id)}`\s*\|[^|]*\|)\s*[^|]*?\s*(\|)",
            rf"\1 {new_state} \2", text, count=1, flags=re.MULTILINE,
        )
    return text, log


def cmd_ingest(args: argparse.Namespace) -> int:
    packet = Path(args.packet).expanduser().resolve()
    state_dir = state_dir_for(packet, args.state_dir)
    answers = load_answers(state_dir, packet)
    if not answers["items"]:
        print("ERROR: no answers recorded yet", file=sys.stderr)
        return 3
    if not answers.get("completed_at") and not args.partial:
        print("ERROR: review not submitted; pass --partial to ingest answered items anyway", file=sys.stderr)
        return 3
    if answers.get("packet_sha256") != packet_digest(packet) and not args.force:
        print("ERROR: packet changed since it was served; refresh the owner's view or pass --force", file=sys.stderr)
        return 4
    text = packet.read_text(encoding="utf-8")
    new_text, log = apply_answers_to_packet(text, answers)
    if new_text != text and not args.dry_run:
        atomic_write(packet, new_text)
        answers["packet_sha256"] = packet_digest(packet)
        answers["ingested_at"] = now_iso()
        save_answers(state_dir, answers)
    errors = vq.validate_packet(new_text)
    result = {
        "packet": str(packet), "dry_run": args.dry_run, "changes": log, "apply_now": answers.get("apply_now"),
        "completed_at": answers.get("completed_at"),
        "needs_rework": [i for i, a in answers["items"].items() if a["kind"] == "edit"],
        "validator_errors": errors,
    }
    print(json.dumps(result, indent=2))
    return 1 if errors else 0


def cmd_stop(args: argparse.Namespace) -> int:
    packet = Path(args.packet).expanduser().resolve()
    state_dir = state_dir_for(packet, args.state_dir)
    info = read_server_info(state_dir)
    if not info:
        print("no running server")
        return 0
    if not _terminate(int(info["pid"])):
        print(f"ERROR: pid {info['pid']} did not exit", file=sys.stderr)
        return 1
    (state_dir / "server.json").unlink(missing_ok=True)
    print(f"stopped pid {info['pid']} on port {info['port']}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)

    def common(p: argparse.ArgumentParser) -> None:
        p.add_argument("packet", help="Markdown questionnaire packet")
        p.add_argument("--state-dir", help="override the derived state directory")

    p = sub.add_parser("serve", help="start or reuse a background server and print its URL")
    common(p)
    p.add_argument("--bind", default="0.0.0.0", help="interface to bind (default all)")
    p.add_argument("--port", type=int, default=0, help="port (default: random open port)")
    p.add_argument("--host", help="hostname for the printed URL (default: tailscale DNS name, else FQDN)")
    p.add_argument("--token", help="fixed URL token (default: random)")
    p.add_argument("--restart", action="store_true", help="replace a running server for this packet")
    p.add_argument("--foreground", action="store_true", help="run in this process (used by the daemon child)")
    p.set_defaults(fn=cmd_serve)

    p = sub.add_parser("render", help="write the HTML page without serving")
    common(p)
    p.add_argument("--out", help="output file (default stdout)")
    p.set_defaults(fn=cmd_render)

    p = sub.add_parser("status", help="print progress; exit 0 when submitted, 3 otherwise")
    common(p)
    p.add_argument("--wait", type=int, default=0, help="poll up to N seconds for submission")
    p.set_defaults(fn=cmd_status)

    p = sub.add_parser("ingest", help="write answers into the packet's owner decision records")
    common(p)
    p.add_argument("--partial", action="store_true", help="ingest even if the owner has not submitted")
    p.add_argument("--force", action="store_true", help="ingest even if the packet changed since serving")
    p.add_argument("--dry-run", action="store_true", help="report changes without writing")
    p.set_defaults(fn=cmd_ingest)

    p = sub.add_parser("stop", help="stop the background server for this packet")
    common(p)
    p.set_defaults(fn=cmd_stop)

    args = parser.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
