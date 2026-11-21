# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Resolve one parent session's known agents from its runtime's own records.

Codex: the parent's indexed spawn edges in the read-only runtime index.
Claude Code: the bound session's own `subagents/` folder, found by a
name-only lookup of `<home>/projects/*/<session>.jsonl`; only that folder is
listed and only its agents' small `.meta.json` files are read.
No crawl of other sessions, transcript content, tracker queries or model calls.
Runtime records are private input; only matched identities reach the browser.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import stat
from contextlib import closing
from pathlib import Path
from uuid import UUID

from loop_telemetry import validate_source

MAX_SESSIONS = 200
META_LIMIT = 64 * 1024
CLAUDE_AGENT_LOG = re.compile(r"agent-([A-Za-z0-9_-]{1,64})\.jsonl")


def configuration(thread_id: str, home: str | Path | None = None) -> dict:
    try:
        thread_id = str(UUID(thread_id))
    except (ValueError, TypeError, AttributeError):
        raise ValueError("Codex watch requires a runtime thread UUID") from None
    if home is not None and not isinstance(home, (str, Path)):
        raise ValueError("Codex home must be a directory path")
    try:
        home = Path(home or os.environ.get("CODEX_HOME") or "~/.codex").expanduser().resolve()
    except (OSError, RuntimeError):
        raise ValueError("Codex home cannot be resolved") from None
    return {"provider": "codex", "thread_id": thread_id, "home": str(home)}


def claude_configuration(session_id: str, home: str | Path | None = None) -> dict:
    try:
        session_id = str(UUID(session_id))
    except (ValueError, TypeError, AttributeError):
        raise ValueError("Claude watch requires a runtime session UUID") from None
    if home is not None and not isinstance(home, (str, Path)):
        raise ValueError("Claude home must be a directory path")
    try:
        home = Path(home or os.environ.get("CLAUDE_CONFIG_DIR") or "~/.claude").expanduser().resolve()
    except (OSError, RuntimeError):
        raise ValueError("Claude home cannot be resolved") from None
    return {"provider": "claude", "session_id": session_id, "home": str(home)}


def _regular(path: Path) -> bool:
    try:
        return stat.S_ISREG(path.lstat().st_mode)
    except OSError:
        return False


def _meta(path: Path) -> dict:
    """An agent's small runtime descriptor; anything unusual reads as absent."""
    try:
        if not _regular(path) or path.lstat().st_size > META_LIMIT:
            return {}
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _claude_sessions(config: dict) -> list[dict]:
    """The bound parent plus the agents in its own subagents/ folder, nothing else."""
    clean = claude_configuration(config.get("session_id"), config.get("home"))
    try:
        parents = [path for path in (Path(clean["home"]) / "projects").glob(f"*/{clean['session_id']}.jsonl")
                   if _regular(path)]
    except OSError:
        parents = []
    if len(parents) != 1:
        raise ValueError("configured Claude parent session is absent or ambiguous under its runtime home")
    parent = parents[0]
    rows = [{"id": clean["session_id"], "rollout_path": str(parent), "agent_path": None, "model": None,
             "tool_use_id": None, "description": None}]
    folder = parent.with_suffix("") / "subagents"
    try:
        logs = sorted(folder.glob("agent-*.jsonl")) if folder.is_dir() and not folder.is_symlink() else []
    except OSError:
        logs = []
    if len(logs) > MAX_SESSIONS:
        raise ValueError("Claude runtime sub-agents exceed the bounded lookup limit")
    for log in logs:
        match = CLAUDE_AGENT_LOG.fullmatch(log.name)
        if not match or not _regular(log):
            continue
        meta = _meta(log.with_name(f"agent-{match.group(1)}.meta.json"))
        field = lambda key: meta[key] if isinstance(meta.get(key), str) else None  # noqa: E731
        rows.append({"id": match.group(1), "rollout_path": str(log), "agent_path": field("agentType"),
                     "model": field("model"), "tool_use_id": field("toolUseId"),
                     "description": field("description")})
    return rows


def _runtime(config: dict) -> tuple[str, str, list[dict]]:
    """(provider, parent id, owned sessions) for a validated watch configuration."""
    provider = config.get("provider") if isinstance(config, dict) else None
    if provider == "codex":
        return provider, configuration(config.get("thread_id"), config.get("home"))["thread_id"], _sessions(config)
    if provider == "claude":
        return (provider, claude_configuration(config.get("session_id"), config.get("home"))["session_id"],
                _claude_sessions(config))
    raise ValueError("unsupported runtime watch configuration")


def _candidates(provider: str, agent: dict, children: list[dict]) -> list[dict]:
    """Published selector first (exact), then a unique name-shaped fallback."""
    identity = agent.get("thread_id") or agent["id"]
    if provider == "codex":
        found = [row for row in children if identity in (row["id"], row["agent_path"])]
        if not found and not agent.get("thread_id"):
            found = [row for row in children if isinstance(row["agent_path"], str)
                     and row["agent_path"].rsplit("/", 1)[-1] == identity]
        return found
    found = [row for row in children if identity in (row["id"], row["tool_use_id"])]
    if not found and not agent.get("thread_id"):
        names = {agent["id"], agent.get("name")} - {None, ""}
        found = [row for row in children if row["description"] in names]
    return found


def _sessions(config: dict) -> list[dict]:
    """Use indexed spawn edges, including retained/archived descendants."""
    if not isinstance(config, dict) or config.get("provider") != "codex":
        raise ValueError("unsupported runtime watch configuration")
    clean = configuration(config.get("thread_id"), config.get("home"))
    index = Path(clean["home"]) / "state_5.sqlite"
    try:
        if not stat.S_ISREG(index.lstat().st_mode):
            raise ValueError("Codex runtime index must be a regular non-symlink file")
        with closing(sqlite3.connect(index.as_uri() + "?mode=ro", uri=True, timeout=0.2)) as database:
            database.row_factory = sqlite3.Row
            # UNION prevents cycles; LIMIT bounds both traversal and projection.
            rows = database.execute("""
                WITH RECURSIVE owned(id) AS (
                    SELECT id FROM threads WHERE id = ?
                    UNION
                    SELECT edge.child_thread_id FROM thread_spawn_edges AS edge
                    JOIN owned ON edge.parent_thread_id = owned.id
                    LIMIT ?
                )
                SELECT thread.id, thread.rollout_path, thread.agent_path, thread.model
                FROM owned JOIN threads AS thread ON thread.id = owned.id
                """, (clean["thread_id"], MAX_SESSIONS + 1)).fetchall()
    except (OSError, sqlite3.Error):
        raise ValueError("Codex runtime index unavailable or unsupported; check the configured home") from None
    if len(rows) > MAX_SESSIONS:
        raise ValueError("Codex runtime descendants exceed the bounded lookup limit")
    if not any(row["id"] == clean["thread_id"] for row in rows):
        raise ValueError("configured Codex parent is absent from its runtime index")
    return [dict(row) for row in rows]


def resolve(config: dict, actor: str, agents: list[dict], explicit: list[dict]) -> tuple[list[dict], dict, list[str]]:
    """Match only published IDs; ambiguity never silently selects a session."""
    provider, parent_id, sessions = _runtime(config)
    parent = next(row for row in sessions if row["id"] == parent_id)
    children = [row for row in sessions if row["id"] != parent["id"]]
    matches = [(actor, parent)]
    warnings = []
    for agent in agents:
        if agent["id"] == actor:
            raise ValueError("parent and child agent ids must be distinct")
        candidates = _candidates(provider, agent, children)
        if len(candidates) == 1:
            matches.append((agent["id"], candidates[0]))
        elif len(candidates) > 1:
            warnings.append("An agent name matches multiple owned sessions; publish its thread_id.")
    bindings = []
    for agent_id, row in matches:
        # Claude agent ids are not UUIDs; their identity lives in the metadata.
        selector = {"thread_id": row["id"]} if provider == "codex" else {}
        source = validate_source({"id": agent_id, "provider": provider, "path": row["rollout_path"], **selector})
        bindings.append((source, {"thread_id": row["id"], "agent_path": row["agent_path"],
                                  "model": row["model"]}))
    sources, runtime, merged_warnings = _merge(bindings, explicit)
    return sources, runtime, sorted(set(warnings + merged_warnings))


def _merge(bindings: list[tuple[dict, dict]], explicit: list[dict]) -> tuple[list[dict], dict, list[str]]:
    normalized = [validate_source(source) for source in explicit]
    if len({row["id"] for row in normalized}) != len(normalized):
        raise ValueError("usage source agent ids must be unique")
    sources = {row["id"]: row for row in normalized}
    runtime, warnings = {}, []
    for source, metadata in bindings:
        agent_id = source["id"]
        prior = sources.get(agent_id)
        if prior and any(prior.get(key) != source[key] for key in ("provider", "path")):
            continue  # Explicit nonmatching sources retain precedence.
        if any(item["path"] == source["path"] for key, item in sources.items() if key != agent_id):
            warnings.append("A runtime log is already attributed to another agent; duplicate measurement refused.")
            continue
        sources[agent_id] = source
        runtime[agent_id] = metadata
    return list(sources.values()), runtime, sorted(set(warnings))


def retain(config: dict, actor: str, agents: list[dict], saved: dict, explicit: list[dict]) -> tuple[list[dict], dict, list[str]]:
    """Reuse proven bindings across restart; current explicit attachments win."""
    if (not isinstance(saved, dict) or saved.get("config") != config
        or not isinstance(saved.get("sources"), list) or len(saved["sources"]) > MAX_SESSIONS
        or not isinstance(saved.get("metadata"), dict)):
        raise ValueError("retained runtime bindings are invalid; restore the runtime index")
    provider = config.get("provider")
    expected = {actor, *[agent["id"] for agent in agents]}
    selectors = {agent["id"]: agent.get("thread_id") for agent in agents}
    bindings = []
    for row in saved["sources"]:
        source = validate_source(row)
        metadata = saved["metadata"].get(source["id"])
        if (source["provider"] != provider or provider not in ("codex", "claude")
            or (provider == "codex" and not source.get("thread_id"))
            or not isinstance(metadata, dict) or set(metadata) != {"thread_id", "agent_path", "model"}
            or not isinstance(metadata["thread_id"], str)
            or (provider == "codex" and metadata["thread_id"] != source["thread_id"])
            or any(metadata[key] is not None and not isinstance(metadata[key], str)
                   for key in ("agent_path", "model"))):
            raise ValueError("retained runtime identity is invalid; restore the runtime index")
        selector = selectors.get(source["id"])
        # A selector that no longer names the retained identity drops it: no stale counts.
        if source["id"] in expected and (not selector or selector == metadata["thread_id"]):
            bindings.append((source, metadata))
    return _merge(bindings, explicit)
