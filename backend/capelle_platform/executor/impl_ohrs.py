"""
ohrs subprocess executor — spawns a real openharness-rs agent per job.

Each job gets its own ohrs process running in the capelle-repo-agent
working directory with full access to all 5 skills (cbs, beleid, budget,
buitenbeter, bewonersenquete) and the /capelle-analyse orchestrator.

Progress is streamed in two ways:

1. Primary path — an HTTP ``post_tool_use`` hook is injected into a
   per-job ``settings.json`` file.  ohrs POSTs each tool-use event to
   ``http://127.0.0.1:8080/internal/hook/{message_id}`` as it happens,
   which :class:`InternalHandler` rebroadcasts over the user's
   WebSocket channel.  This gives the frontend timeline real-time
   updates without waiting for the trajectory file to flush.

2. Fallback path — :meth:`_tail_trajectory` polls the on-disk
   ``trajectory.jsonl`` file.  In practice ohrs flushes the file only
   on termination, so this rarely fires mid-run; it remains best-effort
   in case the HTTP hook is unreachable.  Events already delivered via
   the hook are de-duplicated by ``(tool_name, first 80 chars of
   tool_input)``.

On completion, the executor extracts the final assistant response and
the structured AnalysisResult JSON (if the skill produced one) as the
job result.

Thread safety: each execute() call is self-contained with local state only.
No shared mutable attributes are modified at runtime.
"""

from __future__ import annotations

import asyncio
import json
import os
import pwd
import re
import shutil
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")

# Strip the ``capelle-`` brand prefix from CLI tool names when echoing a raw
# command into the user-facing activity feed (e.g. ``capelle-cube`` →
# ``cube``). Scoped to the known tool names ONLY so it never mangles the
# ``capelle-aan-den-ijssel`` gemeente slug that appears in legitimate data
# paths. Both the legacy capelle-* names and the neutral aliases are valid on
# the agent host; the feed must show the neutral form regardless.
_TOOL_DEBRAND_RE = re.compile(
    r"\bcapelle-(beleid|cube|budget|buitenbeter|jeugdzorg|graph|ask|report|"
    r"analyse|dashboard)\b"
)


def _debrand_cmd(cmd: str) -> str:
    """Drop the ``capelle-`` brand from known tool names in a shell command."""
    return _TOOL_DEBRAND_RE.sub(r"\1", cmd)


def _cmd_invokes(cmd: str, tool: str) -> bool:
    """True if ``tool`` is run as a command word in ``cmd``.

    Matches the tool at the start of the command or right after a shell
    separator (``;`` ``|`` ``&`` ``(`` newline), so a substring inside a
    path or argument (e.g. ``./cube_export.csv``) does NOT false-match.
    """
    return re.search(rf"(?:^|[;&|(\n]|&&|\|\|)\s*{re.escape(tool)}\b", cmd) is not None

# Validator for beleid tool `source_url` values. The Capelle_beleid indexer
# (Capelle_beleid/build_vectordb.py:125-134 — in a separate repo) constructs
# URLs of this exact shape: scheme+host, a document-type+year slug, a
# second-level path (programma/paragraaf/bestuur/bestanden), and a final
# kebab-case slug. An empirical investigation established that any URL on a
# non-beleid section is LLM-fabricated and that the agent sometimes mangles
# beleid URLs (e.g. drops the /bestanden/ segment, yielding 404s). Keeping
# only URLs that match this regex filters out both failure modes without a
# network check. Updates to the indexer's URL layout require updating this
# regex too.
_BELEID_URL_RE = re.compile(
    r"^https://capelleaandenijssel\.begrotingsapp\.nl/"
    r"(begroting|voorjaarsnota|najaarsnota|jaarstukken|slotwijziging|"
    r"bestuursrapportage|kadernota)-\d{4}(-\d+)?/"
    r"(programma|paragraaf|bestuur|bestanden)/[a-z0-9-]+$"
)

from capelle_platform.executor.base_executor import BaseExecutor
from capelle_platform.executor.modes import ModeConfig, ModeConfigFactory
from capelle_platform.executor.prompt_focus import (
    MAX_FOCUS_NODES,
    _collect_neighbor_lines,
    build_focus_node_block,
)
from capelle_platform.graph.base_store import BaseGraphStore
from capelle_platform.graph.models import NodeStatus
from capelle_platform.models.job import JobMessage
from capelle_platform.observability import get_logger
from capelle_platform.observability.logger import TraceContext
from capelle_platform.settings import Settings
from capelle_platform.utils.hook_auth import HOOK_SIGNATURE_HEADER, sign_message_id

_logger = get_logger(__name__)

# --- Trajectory schema (contract C1) -------------------------------------
# ohrs streams one versioned, kind-tagged JSON object per line, flushed
# after every write (so partial/crashed runs still parse). Every line
# carries ``"v": <int>``; ``kind`` discriminates the record type.
_TRAJECTORY_SCHEMA_VERSION = 1
_KIND_META = "meta"
_KIND_ASSISTANT = "assistant"
_KIND_TOOL_RESULT = "tool_result"
_KIND_USAGE = "usage"
_KIND_END = "end"

# Run-end statuses reported on the terminal ``end`` line.
_END_STATUS_OK = "ok"
_END_STATUS_ERROR = "error"
_END_STATUS_MAX_TURNS = "max_turns"

# Lines at or above this byte length are size-skipped during the combined
# trajectory pass when their *content* isn't needed (EXEC-7). Only the big
# ``tool_result`` lines (multi-MB tool output) reach this; the diagnosis
# pass still counts their bytes (for the "context overflow" heuristic)
# without paying to ``json.loads`` the payload.
_TRAJECTORY_LARGE_LINE_BYTES = 256_000

# A single ``tool_result`` line above this raw byte length is treated as a
# signal that the model was handed an oversized payload (the dominant
# empty-final-turn failure mode: an unfiltered ``cbs get`` dump).
_TRAJECTORY_BIG_TOOL_RESULT_BYTES = 500_000

# --- Cancellation (contract C6, capelle side) ----------------------------
# The sandboxed child runs in a NAMED systemd transient service so cancel /
# timeout can ``systemctl stop`` the whole cgroup (agent + bash children),
# not just the systemd-run --wait client `proc` points at.
_JOB_UNIT_PREFIX = "capelle-job-"
_JOB_UNIT_SUFFIX = ".service"
# Seconds to wait for the (client) process to die after we signal it.
_CANCEL_GRACE_SECONDS = 5


@dataclass
class _TrajectoryScan:
    """
    Everything one combined pass over ``trajectory.jsonl`` collects (EXEC-7).

    A single walk gathers per-turn usage (EXEC-1 aggregation), the
    empty-final-turn diagnosis signals (EXEC-1 diagnosis), and the best
    assistant text (EXEC-1 recovery) so the three former full passes —
    each ``json.loads``-ing every line including multi-MB tool results —
    collapse into one size-aware pass.
    """

    file_present: bool = False

    # --- usage (summed across all `usage` lines) ---
    model: str | None = None
    turns: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0

    # --- assistant text + tool-call signals ---
    assistant_texts: list[str] = field(default_factory=list)
    bash_commands: list[str] = field(default_factory=list)
    saw_assistant: bool = False
    # Snapshot of the LAST assistant line's emptiness (no text, no calls).
    last_assistant_empty: bool = False

    # --- tool-result size signals (context-overflow heuristic) ---
    big_tool_result_count: int = 0
    big_tool_result_bytes: int = 0

    # --- terminal marker (`end` line) ---
    end_status: str | None = None
    end_error: str | None = None


class OhrsExecutor(BaseExecutor):
    """
    Execute analysis jobs by spawning ohrs as an async subprocess.

    Each job runs an isolated ohrs process with:
    - /capelle-analyse skill for multi-source analysis
    - full_auto permission mode (no human confirmation needed)
    - trajectory recording for audit trail + progress streaming
    - per-job data directory for session isolation
    """

    def __init__(
        self,
        settings: Settings,
        progress_callback: Callable[[str, str, dict[str, Any]], Awaitable[None]] | None = None,
        graph_store: BaseGraphStore | None = None,
    ) -> None:
        """
        Construct with paths from Settings and an optional progress callback.

        Args:
            settings:          Application settings providing ohrs binary path,
                               working dir, etc.
            progress_callback: Async function called with (user_id, message_id,
                               event) for real-time progress streaming.  Injected
                               by the worker pool.
            graph_store:       Optional finding-graph store.  When supplied and
                               ``settings.graph_enabled`` is True, node-scoped
                               follow-up messages (``job.node_id`` set) will have
                               the referenced node's 1-hop subgraph injected as a
                               FOCUS NODE block in the prompt.  Defaults to None
                               so existing constructions (tests, CLIExecutor
                               wrappers, older integration code) require no
                               changes — additive only.
        """
        self._settings = settings
        self._ohrs_binary = settings.ohrs_binary
        # _working_dir is retained ONLY for the legacy scan in
        # _find_analysis_result — it points at the groeikern checkout's
        # capelle_rag/analyses/ directory, which older agents (running
        # alongside a freshly-deployed platform) might still write to.
        # Per-job runtime paths (cwd, plugin skeleton, symlinks, system
        # prompt) all come from the per-mode ModeConfig now.
        self._working_dir = settings.ohrs_working_dir
        self._jobs_dir = settings.ohrs_jobs_dir
        # Per-job timeout + max-turns are resolved per-mode inside execute()
        # from the ModeConfig (not captured here) so a single executor
        # instance serves every mode without mutating shared state.
        self._progress_callback = progress_callback
        # Optional graph store for node-scoped follow-up prompt injection.
        # None → feature inactive (additive, backward compatible).
        self._graph_store: BaseGraphStore | None = graph_store
        self._running_procs: dict[str, asyncio.subprocess.Process] = {}
        # message_id → the named systemd transient unit the child runs in
        # when sandboxed (EXEC-3). Present only for sandboxed jobs; absent
        # entries fall back to signalling the proc directly. ``cancel()`` and
        # the timeout path ``systemctl stop`` this unit so the WHOLE cgroup
        # (the ohrs agent plus its bash grandchildren) is torn down, not just
        # the systemd-run --wait client that ``proc`` actually refers to.
        self._running_units: dict[str, str] = {}
        # Shared per-job dedup keys populated both by the HTTP hook handler
        # (primary) and the trajectory tailer (fallback). A key is
        # ``f"{tool_name}|{stringified_first_80_chars_of_tool_input}"``.
        # The InternalHandler reads+writes this dict via :meth:`hook_seen_for`
        # to avoid broadcasting a duplicate event for something the trajectory
        # tailer already emitted, and vice versa. Cleaned up on job completion.
        self._hook_seen: dict[str, set[str]] = {}

    def hook_seen_for(self, message_id: str) -> set[str]:
        """
        Return the shared dedup set for ``message_id``, creating it if needed.

        Shared between :meth:`_tail_trajectory` and the InternalHandler HTTP
        hook so a tool invocation is broadcast at most once per message,
        regardless of which path observes it first.
        """
        seen = self._hook_seen.get(message_id)
        if seen is None:
            seen = set()
            self._hook_seen[message_id] = seen
        return seen

    @staticmethod
    def make_dedup_key(tool_name: str, tool_input: Any) -> str:
        """
        Build the ``(tool_name, first 80 chars of tool_input)`` dedup key.

        Serialising the input deterministically via ``json.dumps(..., sort_keys=True)``
        keeps the key stable regardless of ordering differences between the
        hook payload and the trajectory block.
        """
        try:
            serialised = json.dumps(tool_input, sort_keys=True, default=str)
        except (TypeError, ValueError):
            serialised = str(tool_input)
        return f"{tool_name}|{serialised[:80]}"

    @staticmethod
    def _job_unit_name(message_id: str) -> str:
        """
        Build the transient-unit name for ``message_id`` (EXEC-3).

        systemd unit names must be a single path-free token; ``message_id``
        is a server-minted opaque id, but we still sanitise defensively so a
        stray separator can never reshape the ``systemctl stop`` target.
        """
        safe = re.sub(r"[^A-Za-z0-9_.-]", "_", message_id)
        return f"{_JOB_UNIT_PREFIX}{safe}{_JOB_UNIT_SUFFIX}"

    def _wrap_in_systemd_run(
        self,
        cmd: list[str],
        job_dir: Path,
        job_settings_path: Path,
        env: dict[str, str],
        message_id: str,
        *,
        timeout_seconds: int,
    ) -> list[str] | None:
        """
        Return ``cmd`` wrapped in a ``systemd-run`` transient scope under the
        unprivileged ``capelle-agent`` uid with a hardened namespace, or
        ``None`` if the sandbox can't be used on this host (the user isn't
        provisioned, or ``systemd-run`` isn't on PATH).

        The ohrs child then:
        - Runs as capelle-agent, not root — so chmod-750 system-probe binaries
          (hostname, ss, ps, ip, ping, curl, …) return EACCES.
        - Sees a read-only view of /opt (skills, tools-venv) and the rest of
          the rootfs.
        - Can only write inside the per-job dir (``ReadWritePaths``).
        - Cannot read /etc/capelle-codex.env (``InaccessiblePaths``).
        - Cannot see other processes in /proc (``ProtectProc=invisible``,
          ``ProcSubset=pid``).
        - Cannot open netlink / packet sockets (``RestrictAddressFamilies``).

        All layers are redundant with each other and with the Day 0 env
        scrub. Any one of them fails open, the rest still block.
        """
        systemd_run = shutil.which("systemd-run") or "/usr/bin/systemd-run"
        if not Path(systemd_run).exists():
            return None
        try:
            agent_pw = pwd.getpwnam("capelle-agent")
        except KeyError:
            return None

        # Chown the per-job artifacts to the agent so it can write trajectory,
        # analysis files, etc. If this fails for any reason, bail out to the
        # direct-spawn path — partial lockdown is worse than none.
        try:
            os.chown(job_dir, agent_pw.pw_uid, agent_pw.pw_gid)
            os.chown(job_settings_path, agent_pw.pw_uid, agent_pw.pw_gid)
        except OSError as exc:
            _logger.warning(
                "sandbox_chown_failed",
                job_dir=str(job_dir),
                error=str(exc)[:200],
            )
            return None

        # Use a transient service unit (NOT --scope): --scope rejects the
        # hardening properties below with "Unknown assignment" because they
        # are Service-type directives. --wait runs synchronously so systemd-
        # run returns when ohrs exits; --pipe connects asyncio's stdin/
        # stdout/stderr pipes to the service's stdio.
        wrapped: list[str] = [
            systemd_run,
            "--wait",
            "--pipe",
            "--quiet",
            "--collect",
            # Name the unit so cancel()/timeout can `systemctl stop` it and
            # tear down the whole cgroup (EXEC-3 / contract C6). --collect
            # garbage-collects the unit once it stops so the name is free for
            # a requeued message with the same id.
            f"--unit={self._job_unit_name(message_id)}",
            f"--uid={agent_pw.pw_uid}",
            f"--gid={agent_pw.pw_gid}",
        ]
        # Carry the scrubbed env across the uid boundary. systemd-run by
        # default copies the caller's env into the scope, but --setenv is
        # explicit and deterministic.
        for k, v in env.items():
            wrapped.append(f"--setenv={k}={v}")
        # Hardening properties. ProtectSystem=strict makes every fs tree
        # read-only except /dev, /proc, /sys, /tmp, /var/tmp, $HOME — we
        # re-add the one writable path the agent needs via ReadWritePaths.
        wrapped.extend([
            "--property=NoNewPrivileges=yes",
            "--property=ProtectSystem=strict",
            "--property=ProtectHome=yes",
            "--property=ProtectProc=invisible",
            "--property=ProcSubset=pid",
            "--property=ProtectKernelTunables=yes",
            "--property=ProtectKernelModules=yes",
            "--property=ProtectKernelLogs=yes",
            "--property=ProtectControlGroups=yes",
            "--property=ProtectClock=yes",
            "--property=RestrictNamespaces=yes",
            "--property=RestrictRealtime=yes",
            "--property=RestrictSUIDSGID=yes",
            "--property=LockPersonality=yes",
            "--property=RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6",
            "--property=CapabilityBoundingSet=",
            "--property=AmbientCapabilities=",
            "--property=SystemCallArchitectures=native",
            "--property=InaccessiblePaths=/etc/capelle-codex.env",
            # Kernel-enforced job ceiling: if asyncio.wait_for fires, cancel()
            # tears down the cgroup; but if the event loop itself stalls, this
            # RuntimeMaxSec backstop (per-mode timeout + grace + 30s buffer)
            # ensures the service is killed unconditionally by the kernel.
            f"--property=RuntimeMaxSec={timeout_seconds + _CANCEL_GRACE_SECONDS + 30}",
            # Hide the host topology. /etc/hosts lists every LXC neighbour
            # (aoi-todo, yuji-itadori, …) and /etc/hostname leaks "yuta-*".
            # MagicDNS (127.0.0.53 → 100.100.100.100) still resolves the
            # tailnet names via the .lxd / .ts.net domains, so the CLI tools
            # keep working.
            "--property=InaccessiblePaths=/etc/hosts",
            "--property=InaccessiblePaths=/etc/hostname",
            # UTS namespace — agent can't change the hostname. Does not
            # change the *value* without ProtectHostname=private (systemd 257+);
            # our 255 doesn't support that, so $HOSTNAME still reads
            # "yuta-*". Minor residual leak vs the /etc/hosts bonanza.
            "--property=ProtectHostname=yes",
            f"--property=ReadWritePaths={job_dir}",
            # The OHRS CLI resolves --cwd before plugin loading, then scans
            # <cwd>/.openharnessrs/plugins/* (see crates/oh-harness/src/cli.rs
            # and oh-plugins/src/discovery.rs).  Capelle therefore materialises
            # a minimal plugin skeleton inside each job dir; this startup
            # WorkingDirectory no longer participates in plugin discovery.
            f"--property=WorkingDirectory=/opt/capelle-repo-agent",
            "--",
        ])
        wrapped.extend(cmd)
        _logger.info(
            "ohrs_sandboxed",
            job_dir=str(job_dir),
            uid=agent_pw.pw_uid,
        )
        return wrapped

    def _write_job_settings(
        self, job_dir: Path, message_id: str, working_dir: Path
    ) -> Path:
        """
        Materialise a per-job ``settings.json`` that ohrs reads via ``--settings``.

        Layers (later overrides earlier):
          1. ``ohrs_home_dir/.openharnessrs/settings.json`` — the operator's
             global ohrs config (provider, model, default permissions). ohrs
             itself only auto-reads this when ``--settings`` is omitted; we
             pass ``--settings`` explicitly to inject hooks, so we have to
             fold the global in ourselves.
          2. ``working_dir/.openharnessrs/settings.json`` — per-mode overrides
             (different allowed_tools sets, different model, etc.).
          3. ``post_tool_use`` + ``pre_tool_use`` HTTP hooks pointing at the
             platform's internal endpoint for this message_id — the hooks
             power real-time UI progress without waiting for the trajectory
             file to flush at termination.
          4. ``enabled_plugins.capelle`` forced on so the per-job plugin
             skeleton is loaded.

        Missing files at layer 1 or 2 are tolerated; the hook layer always
        runs so progress streaming survives a missing repo settings file.
        """
        merged: dict[str, Any] = {}
        for label, path in (
            ("global", self._settings.ohrs_home_dir / ".openharnessrs" / "settings.json"),
            ("workspace", working_dir / ".openharnessrs" / "settings.json"),
        ):
            if not path.exists():
                continue
            try:
                layer = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                _logger.warning(
                    "base_settings_unreadable",
                    layer=label,
                    path=str(path),
                    error=str(exc)[:200],
                )
                continue
            if not isinstance(layer, dict):
                _logger.warning(
                    "base_settings_not_object", layer=label, path=str(path)
                )
                continue
            merged.update(layer)

        hooks = dict(merged.get("hooks") or {})
        hook_url = f"http://127.0.0.1:8080/internal/hook/{message_id}"

        # API-5: when a shared secret is configured, mint the static HMAC the
        # platform verifies on /internal/hook. ohrs HTTP hooks only support a
        # static header map, so the signature is over the message_id (which the
        # endpoint also derives from its URL). No-op when the secret is unset.
        hook_headers: dict[str, str] = {}
        secret = self._settings.internal_hook_secret
        if secret:
            hook_headers[HOOK_SIGNATURE_HEADER] = sign_message_id(secret, message_id)

        def _is_platform_hook(h: Any) -> bool:
            return (
                isinstance(h, dict)
                and h.get("type") == "http"
                and isinstance(h.get("url"), str)
                and "/internal/hook/" in h["url"]
            )

        # Register the same URL on BOTH pre_tool_use (→ 'running') and
        # post_tool_use (→ 'done') so the UI timeline shows each tool's
        # lifecycle, not just its completion.
        for event_name in ("pre_tool_use", "post_tool_use"):
            existing = list(hooks.get(event_name) or [])
            filtered = [h for h in existing if not _is_platform_hook(h)]
            hook_entry: dict[str, Any] = {
                "type": "http",
                "url": hook_url,
                "timeout_seconds": 2,
                "block_on_failure": False,
            }
            if hook_headers:
                hook_entry["headers"] = dict(hook_headers)
            filtered.append(hook_entry)
            hooks[event_name] = filtered

        merged["hooks"] = hooks

        # Plugin discovery is driven by OHRS's resolved --cwd, not by the
        # settings file path.  Be explicit anyway: every job's settings must
        # enable the Capelle plugin once the job-local plugin skeleton exists.
        enabled_plugins = dict(merged.get("enabled_plugins") or {})
        enabled_plugins["capelle"] = True
        merged["enabled_plugins"] = enabled_plugins

        out_path = job_dir / "settings.json"
        out_path.write_text(
            json.dumps(merged, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        return out_path

    def _materialize_job_openharness(
        self, job_dir: Path, config: ModeConfig
    ) -> None:
        """
        Materialise the minimal OpenHarness project plugin layout inside
        ``job_dir`` so OHRS discovers Capelle skills from its actual ``--cwd``.

        OHRS loads project plugins from ``<cwd>/.openharnessrs/plugins`` after
        applying ``--cwd``.  The platform therefore cannot rely on the process
        startup directory or on the ``--settings`` path for plugin discovery.

        ``config`` carries the mode-specific ``working_dir`` (source of the
        plugin manifest + skills) and the list of required skill files used
        for preflight. Per-mode workspaces never share state through this
        method — each job materialises strictly out of its own mode's root.
        """
        working_dir = config.working_dir
        plugin_src = working_dir / ".openharnessrs" / "plugins" / "capelle"
        plugin_json_src = plugin_src / "plugin.json"
        skills_src = working_dir / "skills"

        plugin_dst = job_dir / ".openharnessrs" / "plugins" / "capelle"
        plugin_dst.mkdir(parents=True, exist_ok=True)

        if not plugin_json_src.exists():
            raise RuntimeError(
                f"OHRS Capelle plugin manifest missing for mode "
                f"{config.mode!r}: {plugin_json_src}"
            )
        if not skills_src.exists():
            raise RuntimeError(
                f"OHRS Capelle skills directory missing for mode "
                f"{config.mode!r}: {skills_src}"
            )

        shutil.copy2(plugin_json_src, plugin_dst / "plugin.json")

        skills_link = plugin_dst / "skills"
        if skills_link.is_symlink() or skills_link.exists():
            if skills_link.is_dir() and not skills_link.is_symlink():
                shutil.rmtree(skills_link)
            else:
                skills_link.unlink()
        skills_link.symlink_to(skills_src, target_is_directory=True)

        required = [plugin_dst / "plugin.json", *config.required_skill_files(skills_link)]
        missing = [str(path) for path in required if not path.exists()]
        if missing:
            raise RuntimeError(
                f"OHRS Capelle plugin preflight failed for mode "
                f"{config.mode!r}; missing: " + ", ".join(missing)
            )

    async def execute(self, job: JobMessage) -> dict[str, Any]:
        """
        Spawn an ohrs agent to perform the analysis.

        The ohrs process runs /capelle-analyse with the user's query,
        producing a multi-source analysis using CBS, beleid, budget,
        buitenbeter, and bewonersenquete data.

        Progress events are streamed via the callback as ohrs works.
        The final assistant response is returned as the result dict.
        """
        TraceContext.set(job.trace_id)
        # Resolve the per-mode configuration up front so every downstream
        # path (cwd, prompt, symlinks, env) flows from one source. The
        # factory raises if the mode is unknown — the API validates
        # JobMessage.mode against the Mode literal before this runs, but
        # this catches a stale queue replay across an incompatible deploy.
        mode_config: ModeConfig = ModeConfigFactory.from_mode(
            job.mode, self._settings
        )
        # Resolve per-mode budget as local variables so this execute() call is
        # self-contained: no shared mutable instance attributes are read at
        # runtime (thread safety — the comment at the top of this module
        # documents this invariant explicitly).
        timeout = mode_config.job_timeout_seconds
        max_turns = mode_config.max_turns
        working_dir = mode_config.working_dir
        job_dir = self._jobs_dir / job.message_id
        job_dir.mkdir(parents=True, exist_ok=True)
        self._materialize_job_openharness(job_dir, mode_config)
        trajectory_path = job_dir / "trajectory.jsonl"
        started_at_ns = __import__("time").time_ns()

        # Surface read-only reference data directly inside the job workspace
        # via symlinks so the agent can refer to them with short relative
        # paths. Targets are mode-specific: groeikern mounts Capelle_enquetes
        # for the bewonersenquete PDFs; jeugdzorg mounts the three synthetic
        # CSVs. Targets stay read-only under ProtectSystem=strict in prod.
        for name, target in mode_config.workspace_symlinks().items():
            link = job_dir / name
            if not link.exists() and target.exists():
                try:
                    link.symlink_to(target)
                except OSError as exc:
                    _logger.warning(
                        "workspace_symlink_failed",
                        name=name,
                        target=str(target),
                        error=str(exc)[:200],
                    )

        _logger.info(
            "ohrs_starting",
            message_id=job.message_id,
            session_id=job.session_id,
            query=job.query,
            skill=job.skill,
            mode=mode_config.mode,
        )

        # Write path is inside the per-job dir — that's the one directory the
        # sandbox grants ReadWritePaths on. The old location
        # /opt/capelle-repo-agent/capelle_rag/analyses/ is read-only now under
        # ProtectSystem=strict. _find_analysis_result picks this file up by
        # scanning job_dir.
        output_path = f"{job_dir}/analysis.json"
        system_prompt = mode_config.build_system_prompt(output_path)

        prompt = await OhrsExecutor._build_prompt_async(
            job, output_path, self._graph_store, self._settings
        )
        job_settings_path = self._write_job_settings(
            job_dir, job.message_id, working_dir
        )

        cmd = [
            self._ohrs_binary,
            "-p", prompt,
            "--permission-mode", "full_auto",
            "--max-turns", str(max_turns),
            "--output-format", "json",
            "--trajectory", str(trajectory_path),
            # --system-prompt REPLACES ohrs's default "You are ohrs, a coding
            # assistant..." base (see oh-services/src/prompts/base.rs). We
            # supply a fully self-contained Capelle-analyst prompt above so
            # the model never sees the conflicting coding-assistant identity.
            "--system-prompt", system_prompt,
            # --bare suppresses ohrs's PromptBuilder env + CLAUDE.md/memory
            # walk rooted at --cwd (EXEC-8). Without it the override is only
            # *prepended* to whatever PromptBuilder discovers; --bare makes
            # our self-contained prompt the deterministic, sole base identity
            # regardless of what lands in the per-job working tree.
            "--bare",
            "--settings", str(job_settings_path),
            # OHRS plugin discovery uses this --cwd.  _materialize_job_openharness
            # puts .openharnessrs/plugins/capelle in the job dir before launch,
            # so skills and runtime file tools now agree on one self-contained
            # per-job working tree.
            "--cwd", str(job_dir),
        ]

        # SECURITY: scrub env to an explicit allowlist before handing to the
        # ohrs child. We remove CAPELLE_JWT_SECRET, CAPELLE_POSTGRES_DSN,
        # CAPELLE_RABBITMQ_URL and any other parent-only secret so the agent's
        # bash can't leak them via `echo $VAR` / `env` / `compgen -v`.
        #
        # CODEX_* and OPENAI_API_KEY are kept because ohrs itself is the codex
        # client — without them, the provider handshake fails with
        # "missing env var CODEX_ACCESS_TOKEN" before any turn runs. Moving
        # token custody out of the agent scope requires a proxy (future work);
        # for now the agent can still read them via `echo $CODEX_ACCESS_TOKEN`,
        # which is acknowledged risk.
        #
        # CAPELLE_CHROMA_HTTP is kept because the cbs/capelle-beleid CLI tools
        # read it to reach yuji-itadori. It's a hostname, not a secret.
        _AGENT_ENV_ALLOW = {
            "PATH", "TZ", "LANG", "LC_ALL", "LC_CTYPE",
            "CODEX_ACCESS_TOKEN", "CODEX_REFRESH_TOKEN", "CODEX_BASE_URL",
            "OPENAI_API_KEY",
            "CAPELLE_CHROMA_HTTP",
            "CAPELLE_MESSAGE_ID",
            # ohrs' WebSearch builtin reads this from its own process env to call
            # the Brave Search API. It does NOT match the CAPELLE_*/CBS_* prefix
            # the bash tool forwards, so it stays out of agent-controlled bash.
            "BRAVE_API_KEY",
        }
        env = {k: v for k, v in os.environ.items() if k in _AGENT_ENV_ALLOW}
        env["CAPELLE_MESSAGE_ID"] = job.message_id
        env.setdefault("PATH", "/usr/local/bin:/usr/bin:/bin")
        # Mode-specific PATH additions — each mode declares where its CLI
        # binary lives so the skill markdown can invoke it bare-name. We
        # prepend (not append) so a same-named binary from the mode's venv
        # always wins over a system one.
        mode_path_additions = mode_config.path_additions()
        if mode_path_additions:
            prefix = ":".join(str(p) for p in mode_path_additions)
            env["PATH"] = f"{prefix}:{env['PATH']}"
        # HOME is configurable via Settings — production stages read-only
        # reference data under /opt/capelle-shared (cbs-tool catalog at
        # ~/.local/share/cbs-tool/{catalog,municipalities}.json which
        # cbs_tool/catalog.py:11-13 resolves via Path.home(); ohrs's own
        # global ~/.openharnessrs/settings.json fallback for provider/model)
        # while local dev points at the operator's actual home so the same
        # files resolve out of ~/.openharnessrs and ~/.local/share. The
        # production dir is owned root:root 0755 and ProtectSystem=strict
        # makes /opt read-only in the sandbox — the agent reads, can't
        # mutate. Anything the agent needs to WRITE goes into
        # OPENHARNESSRS_DATA_DIR (= per-job dir), which ReadWritePaths
        # carves out.
        env["HOME"] = str(self._settings.ohrs_home_dir)
        env["OPENHARNESSRS_DATA_DIR"] = str(job_dir)
        # Mode-specific env vars (e.g., JEUGDZORG_DATA pointing at the
        # canonical CSV directory) — merged AFTER the platform defaults
        # so a mode can override HOME / DATA_DIR if it ever needs to.
        env.update(mode_config.extra_env())

        # SECURITY: if the unprivileged capelle-agent user is provisioned on
        # this host, wrap the ohrs spawn in a systemd-run transient scope so
        # the child runs under that uid with a hardened namespace (no
        # /proc/net visibility, no writes outside job_dir, no /etc secrets
        # readable, no extra capabilities). Auto-detect — if the user isn't
        # there, fall back to direct spawn so deploys stay safe.
        sandbox_cmd = self._wrap_in_systemd_run(
            cmd, job_dir, job_settings_path, env, job.message_id,
            timeout_seconds=timeout,
        )
        if sandbox_cmd is not None:
            cmd = sandbox_cmd
            # Record the unit so cancel()/timeout tears down the cgroup, not
            # just the systemd-run client proc (EXEC-3).
            self._running_units[job.message_id] = self._job_unit_name(
                job.message_id
            )

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(working_dir),
                env=env,
            )
            self._running_procs[job.message_id] = proc

            progress_task = asyncio.create_task(
                self._tail_trajectory(job, trajectory_path, proc)
            )

            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )

            progress_task.cancel()
            try:
                await progress_task
            except asyncio.CancelledError:
                pass

            self._running_procs.pop(job.message_id, None)
            self._running_units.pop(job.message_id, None)
            self._hook_seen.pop(job.message_id, None)

            codex_refresh_failed: bool = False
            if stderr:
                stderr_text = stderr.decode("utf-8", errors="replace").strip()
                if stderr_text:
                    _logger.warning(
                        "ohrs_stderr",
                        message_id=job.message_id,
                        stderr=stderr_text[:1000],
                    )
                    # ohrs surfaces Codex refresh failures on stderr as an
                    # AUTH error mentioning refresh_token_reused. We flag
                    # it so the worker pool can emit a dedicated telemetry
                    # event — admins see recent occurrences on the
                    # dashboard's system-status strip and know it's time
                    # to re-sync tokens from ~/.codex/auth.json.
                    if (
                        "refresh_token_reused" in stderr_text
                        or "refresh HTTP 401" in stderr_text
                    ):
                        codex_refresh_failed = True

            if proc.returncode != 0:
                _logger.error(
                    "ohrs_nonzero_exit",
                    message_id=job.message_id,
                    returncode=proc.returncode,
                )
                # Salvage: ohrs can panic on the way out (Rust exit code 101)
                # AFTER the skill has already written a complete, valid
                # analysis.json. A crashed process must not throw away a
                # finished report — if one exists for THIS run, fall through to
                # the normal finalisation path below and surface it. Only fail
                # the job when there is genuinely no report to recover.
                if self._find_analysis_result(job, started_at_ns) is None:
                    stderr_preview = (
                        stderr.decode("utf-8", errors="replace").strip()[:500]
                        if stderr
                        else ""
                    )
                    detail = f" stderr: {stderr_preview}" if stderr_preview else ""
                    raise RuntimeError(
                        f"ohrs exited with code {proc.returncode} for message "
                        f"{job.message_id}.{detail}"
                    )
                _logger.warning(
                    "ohrs_nonzero_exit_with_report",
                    message_id=job.message_id,
                    returncode=proc.returncode,
                )

            # Single combined pass over the trajectory (EXEC-7): usage +
            # diagnosis signals + best assistant text in one walk, reused by
            # every consumer below instead of three separate full passes.
            scan = self._scan_trajectory(trajectory_path)

            result = self._parse_ohrs_output(stdout, scan, job)

            # Prefer structured AnalysisResult if the skill wrote one to disk
            analysis = self._find_analysis_result(job, started_at_ns)
            if analysis is not None:
                stripped = self._strip_fabricated_source_urls(analysis)
                if stripped > 0:
                    _logger.warning(
                        "fabricated_source_url",
                        message_id=job.message_id,
                        stripped=stripped,
                    )
                empty_dropped = self._strip_empty_source_rows(analysis)
                if empty_dropped > 0:
                    _logger.warning(
                        "empty_source_rows_dropped",
                        message_id=job.message_id,
                        dropped=empty_dropped,
                    )

            # Fallback: parse markdown-with-inline-JSON if no file was saved.
            # A missing report on ANY turn (including the first) is no longer a
            # hard failure: the agent may legitimately choose to answer
            # conversationally without producing a structured AnalysisResult.
            # We log the absence at info level and fall through to the
            # conversational-response path below, which recovers the best
            # available assistant text from stdout or the trajectory.
            if analysis is None and not job.history:
                fallback_text = self._best_text_from_scan(stdout, scan)
                diagnostic = self._diagnose_from_scan(scan)
                reason = diagnostic or fallback_text or result.get("content") or "no final text"
                _logger.info(
                    "ohrs_no_analysis_result_first_turn",
                    message_id=job.message_id,
                    output_path=str(Path(job_dir) / "analysis.json"),
                    reason=str(reason)[:500],
                )

            # Fallback: parse markdown-with-inline-JSON for any turn where no
            # structured file was saved.
            if analysis is None:
                analysis = self._parse_markdown_to_analysis(
                    job.query, result.get("content", "")
                )

            if analysis is not None:
                # Stamp the report with the actual completion time. The agent
                # frequently writes a wrong/hallucinated timestamp (e.g. a date
                # from its training data); the platform is the authority on when
                # the analysis was produced, so override it unconditionally.
                analysis["timestamp"] = datetime.now(timezone.utc).isoformat()
                result = {
                    "type": "analysis_result",
                    "content": analysis.get("summary", result.get("content", "")),
                    "analysis": analysis,
                    "query": job.query,
                    "skill": job.skill,
                }
            else:
                # No structured result. If this was a follow-up turn the agent
                # is allowed to answer conversationally — render as a plain
                # chat bubble instead of surfacing the "kon niet worden
                # geparsed" error. Recover the best available text from
                # stdout or trajectory before falling back to an apology.
                is_followup = bool(job.history)
                fallback_text = self._best_text_from_scan(stdout, scan)
                content = (result.get("content") or "").strip()
                looks_like_error = (
                    not content
                    or content.startswith("De analyse is afgerond maar het resultaat")
                )
                if looks_like_error:
                    if fallback_text:
                        content = fallback_text
                    elif is_followup:
                        content = (
                            "Ik kon geen antwoord genereren voor deze vervolgvraag. "
                            "Probeer het opnieuw of stel de vraag anders."
                        )
                    else:
                        # Try to detect the "huge tool output collapsed the
                        # final turn" failure mode and surface a more useful
                        # Dutch message that cites the tools we DID run
                        # instead of a bare parsing error.
                        diagnostic = self._diagnose_from_scan(scan)
                        if diagnostic:
                            content = diagnostic
                        else:
                            content = (
                                "De analyse is afgerond maar het resultaat kon niet "
                                "worden geparsed. Probeer het opnieuw."
                            )
                result = {
                    "type": "chat_response" if is_followup else "ohrs_response",
                    "content": content,
                    "query": job.query,
                    "skill": job.skill,
                }

            usage = self._usage_from_scan(scan)
            if usage is not None:
                result["usage"] = usage
                _logger.info(
                    "ohrs_usage",
                    message_id=job.message_id,
                    model=usage.get("model"),
                    turns=usage.get("turns"),
                    input_tokens=usage.get("input_tokens"),
                    output_tokens=usage.get("output_tokens"),
                    cache_read_input_tokens=usage.get("cache_read_input_tokens"),
                    cache_creation_input_tokens=usage.get(
                        "cache_creation_input_tokens"
                    ),
                )

            # Flag pass-through: the worker pool inspects this to decide
            # whether to emit a dedicated codex_refresh_failed event.
            if codex_refresh_failed:
                result["codex_refresh_failed"] = True

            _logger.info(
                "ohrs_completed",
                message_id=job.message_id,
                returncode=proc.returncode,
                has_analysis=analysis is not None,
                is_followup=bool(job.history),
                codex_refresh_failed=codex_refresh_failed,
            )
            return result

        except asyncio.TimeoutError:
            _logger.error("ohrs_timeout", message_id=job.message_id)
            await self.cancel(job.message_id)
            raise RuntimeError(
                f"ohrs timed out after {timeout}s for query: {job.query}"
            )
        except FileNotFoundError:
            _logger.error("ohrs_binary_not_found", binary=self._ohrs_binary)
            raise RuntimeError(
                f"ohrs binary not found at: {self._ohrs_binary}. "
                "Ensure openharness-rs is installed."
            )

    async def cancel(self, message_id: str) -> None:
        """
        Cancel the running ohrs job for ``message_id`` (EXEC-3 / contract C6).

        Sandboxed jobs run inside a named systemd transient unit whose cgroup
        holds the ohrs agent AND its bash grandchildren. ``proc`` is only the
        ``systemd-run --wait --pipe`` client, so signalling it leaves the
        agent burning CPU/tokens in a separate cgroup. We therefore
        ``systemctl stop`` (escalating to ``systemctl kill --signal=KILL``)
        the unit so the whole cgroup dies.

        For the non-sandboxed dev path (no unit recorded) there is no separate
        cgroup, so we signal the child process directly as before.

        Idempotent: a second cancel after the maps are cleared is a no-op.
        """
        unit = self._running_units.pop(message_id, None)
        proc = self._running_procs.pop(message_id, None)
        self._hook_seen.pop(message_id, None)

        if unit is not None:
            await self._stop_unit(unit, message_id)
            # Also reap the client proc handle so its transport is closed.
            if proc is not None and proc.returncode is None:
                try:
                    await asyncio.wait_for(proc.wait(), timeout=_CANCEL_GRACE_SECONDS)
                except asyncio.TimeoutError:
                    proc.kill()
            _logger.info("ohrs_cancelled", message_id=message_id, unit=unit)
            return

        # Dev / non-sandboxed path: signal the child directly.
        if proc and proc.returncode is None:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=_CANCEL_GRACE_SECONDS)
            except asyncio.TimeoutError:
                proc.kill()
            _logger.info("ohrs_cancelled", message_id=message_id)

    async def _stop_unit(self, unit: str, message_id: str) -> None:
        """
        ``systemctl stop`` the transient unit, escalating to a SIGKILL on the
        whole cgroup if the graceful stop doesn't return in time.

        Best-effort: a missing ``systemctl`` or a unit that already exited is
        logged at warning level and otherwise tolerated — cancellation must
        never raise into the caller.
        """
        systemctl = shutil.which("systemctl") or "/usr/bin/systemctl"

        async def _run(*args: str) -> int | None:
            try:
                stopper = await asyncio.create_subprocess_exec(
                    systemctl,
                    *args,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.PIPE,
                )
            except (FileNotFoundError, OSError) as exc:
                _logger.warning(
                    "ohrs_cancel_systemctl_missing",
                    message_id=message_id,
                    unit=unit,
                    error=str(exc)[:200],
                )
                return None
            try:
                _, stderr = await asyncio.wait_for(
                    stopper.communicate(), timeout=_CANCEL_GRACE_SECONDS
                )
            except asyncio.TimeoutError:
                stopper.kill()
                return None
            if stopper.returncode != 0:
                _logger.warning(
                    "ohrs_cancel_systemctl_nonzero",
                    message_id=message_id,
                    unit=unit,
                    args=" ".join(args),
                    returncode=stopper.returncode,
                    stderr=(stderr.decode("utf-8", errors="replace")[:300]
                            if stderr else ""),
                )
            return stopper.returncode

        # Graceful stop first (SIGTERM to the cgroup), then a hard kill of any
        # survivors so a wedged bash grandchild can't keep the cgroup alive.
        await _run("stop", unit)
        await _run("kill", "--signal=KILL", unit)

    async def _tail_trajectory(
        self,
        job: JobMessage,
        trajectory_path: Path,
        proc: asyncio.subprocess.Process,
    ) -> None:
        """
        Best-effort fallback tailer for the trajectory JSONL file.

        ohrs currently flushes ``trajectory.jsonl`` only on termination, so
        the primary progress path is the HTTP ``post_tool_use`` hook handled
        by :class:`InternalHandler`. This tailer remains as a safety net —
        it polls every 2 seconds and only emits events that were NOT already
        broadcast via the hook (de-duped by ``make_dedup_key``).
        """
        if self._progress_callback is None:
            return

        seen = self.hook_seen_for(job.message_id)
        lines_seen = 0
        while proc.returncode is None:
            await asyncio.sleep(2)
            if not trajectory_path.exists():
                continue
            try:
                with open(trajectory_path, "r", encoding="utf-8") as f:
                    all_lines = f.readlines()
                new_lines = all_lines[lines_seen:]
                lines_seen = len(all_lines)

                for line in new_lines:
                    line = line.strip()
                    if not line:
                        continue
                    parsed = self._parse_trajectory_line(line)
                    if parsed is None:
                        continue
                    event, dedup_key = parsed
                    if dedup_key is not None:
                        if dedup_key in seen:
                            continue
                        seen.add(dedup_key)
                    await self._progress_callback(
                        job.user_id, job.message_id, event
                    )
            except Exception:
                _logger.debug("trajectory_read_error", message_id=job.message_id)

    @staticmethod
    def _scan_trajectory(trajectory_path: Path) -> _TrajectoryScan:
        """
        Walk ``trajectory.jsonl`` ONCE, collecting everything downstream needs.

        This is the single combined pass (EXEC-7) that replaces the three
        former full passes (usage / diagnosis / best-text). It consumes the
        contract-C1 ``kind``-tagged versioned JSONL:

        - ``meta``  → captures the model name.
        - ``usage`` → summed per turn (``input_tokens`` already includes the
          re-sent prior context at that turn, so the sum reflects model usage
          volume, not context-window size).
        - ``assistant`` → collects text and any ``Bash`` commands; tracks the
          emptiness of the LAST assistant turn for the diagnosis heuristic.
        - ``tool_result`` → counts/sizes oversized payloads WITHOUT parsing
          their (multi-MB) content: lines at or above
          :data:`_TRAJECTORY_LARGE_LINE_BYTES` are size-skipped (we only need
          their byte length, not the JSON), and those above
          :data:`_TRAJECTORY_BIG_TOOL_RESULT_BYTES` feed the context-overflow
          signal.
        - ``end`` → captures the terminal status/error marker.

        Never raises: malformed/oversized/legacy lines are skipped. A missing
        file yields ``_TrajectoryScan(file_present=False)``.
        """
        scan = _TrajectoryScan()
        if not trajectory_path.exists():
            return scan
        scan.file_present = True

        try:
            with open(trajectory_path, "r", encoding="utf-8") as fp:
                for raw in fp:
                    raw_len = len(raw)
                    line = raw.strip()
                    if not line:
                        continue

                    # Size-skip giant lines whose content we don't need. Only
                    # tool_result lines reach this size in practice; we still
                    # account their bytes for the overflow heuristic without
                    # paying to json.loads the payload (EXEC-7).
                    if raw_len >= _TRAJECTORY_LARGE_LINE_BYTES:
                        if raw_len > _TRAJECTORY_BIG_TOOL_RESULT_BYTES:
                            scan.big_tool_result_count += 1
                            scan.big_tool_result_bytes += raw_len
                        continue

                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(entry, dict):
                        continue
                    if entry.get("v") != _TRAJECTORY_SCHEMA_VERSION:
                        continue

                    kind = entry.get("kind")

                    if kind == _KIND_META:
                        model = entry.get("model")
                        if isinstance(model, str) and model:
                            scan.model = model

                    elif kind == _KIND_USAGE:
                        scan.turns += 1
                        scan.input_tokens += int(entry.get("input_tokens") or 0)
                        scan.output_tokens += int(entry.get("output_tokens") or 0)
                        scan.cache_creation_input_tokens += int(
                            entry.get("cache_creation_input_tokens") or 0
                        )
                        scan.cache_read_input_tokens += int(
                            entry.get("cache_read_input_tokens") or 0
                        )

                    elif kind == _KIND_ASSISTANT:
                        scan.saw_assistant = True
                        text = entry.get("text")
                        has_text = isinstance(text, str) and bool(text.strip())
                        if has_text:
                            scan.assistant_texts.append(text.strip())
                        tool_calls = entry.get("tool_calls")
                        calls = tool_calls if isinstance(tool_calls, list) else []
                        for call in calls:
                            if not isinstance(call, dict):
                                continue
                            if call.get("name") != "Bash":
                                continue
                            args = call.get("arguments")
                            if not isinstance(args, dict):
                                continue
                            cmd = args.get("command")
                            if isinstance(cmd, str) and cmd.strip():
                                scan.bash_commands.append(cmd.strip())
                        # The diagnosis heuristic cares about the LAST
                        # assistant turn; overwrite each time so the final
                        # assistant line wins.
                        scan.last_assistant_empty = not has_text and not calls

                    elif kind == _KIND_TOOL_RESULT:
                        # Below the large-line threshold but still possibly a
                        # "big" payload for the overflow signal.
                        if raw_len > _TRAJECTORY_BIG_TOOL_RESULT_BYTES:
                            scan.big_tool_result_count += 1
                            scan.big_tool_result_bytes += raw_len

                    elif kind == _KIND_END:
                        status = entry.get("status")
                        if isinstance(status, str):
                            scan.end_status = status
                        err = entry.get("error")
                        if isinstance(err, str):
                            scan.end_error = err
        except OSError as exc:
            _logger.warning(
                "trajectory_read_failed",
                path=str(trajectory_path),
                error=str(exc),
            )

        return scan

    @staticmethod
    def _usage_from_scan(scan: _TrajectoryScan) -> dict[str, Any] | None:
        """
        Build the flat usage dict from a completed :class:`_TrajectoryScan`.

        Returns ``None`` when no ``usage`` lines were seen (e.g. ohrs crashed
        before the first response). Flat-JSON so the worker can hand it
        straight to ``TelemetryRecorder.record`` without re-serialising.
        """
        if scan.turns == 0:
            return None
        return {
            "model": scan.model,
            "turns": scan.turns,
            "input_tokens": scan.input_tokens,
            "output_tokens": scan.output_tokens,
            "cache_creation_input_tokens": scan.cache_creation_input_tokens,
            "cache_read_input_tokens": scan.cache_read_input_tokens,
            "total_tokens": (
                scan.input_tokens
                + scan.output_tokens
                + scan.cache_creation_input_tokens
                + scan.cache_read_input_tokens
            ),
        }

    @staticmethod
    def _aggregate_usage_from_trajectory(
        trajectory_path: Path,
    ) -> dict[str, Any] | None:
        """
        Sum per-turn ``usage`` lines from ``trajectory.jsonl`` (contract C1).

        Thin wrapper over :meth:`_scan_trajectory` retained for callers that
        only want usage; ``execute()`` itself scans once and reuses the
        result for usage + diagnosis + best-text (EXEC-7).
        """
        return OhrsExecutor._usage_from_scan(
            OhrsExecutor._scan_trajectory(trajectory_path)
        )

    @staticmethod
    async def _build_prompt_async(
        job: JobMessage,
        output_path: str,
        graph_store: BaseGraphStore | None,
        settings: Settings,
    ) -> str:
        """
        Async wrapper around :meth:`_build_prompt` with optional FOCUS NODE injection.

        When ALL of the following conditions are met the FOCUS NODE block is
        prepended to the prompt:
          1. ``job.node_id`` is not None.
          2. ``settings.graph_enabled`` is True.
          3. ``graph_store`` is not None.
          4. The session graph exists and contains a node with that id.

        When ANY condition is unmet the return value is byte-identical to
        calling :meth:`_build_prompt` directly — the regression contract.

        Args:
            job:         The job carrying the query, history, and optional node_id.
            output_path: Absolute path where the executor will write analysis.json.
            graph_store: Optional graph persistence store. None disables injection.
            settings:    Application settings; checked for ``graph_enabled`` flag.

        Returns:
            The final prompt string, with or without the FOCUS NODE block prepended.
        """
        base = OhrsExecutor._build_prompt(job, output_path)

        # Normalise scope: node_ids (multi) supersedes node_id (single);
        # preserve order, drop duplicates, cap at MAX_FOCUS_NODES.
        scoped_ids = list(dict.fromkeys(job.node_ids or ([job.node_id] if job.node_id else [])))
        scoped_ids = scoped_ids[:MAX_FOCUS_NODES]

        # Guard: all three preconditions must be met for focus-node injection.
        if (
            not scoped_ids
            or not settings.graph_enabled
            or graph_store is None
        ):
            return base

        graph = await graph_store.get(job.session_id)
        if graph is None:
            return base

        nodes_by_id: dict[str, object] = {n.id: n for n in graph.nodes}
        blocks: list[str] = []
        for node_id in scoped_ids:
            focus_node = nodes_by_id.get(node_id)
            if focus_node is None:
                continue
            # A pruned node is no longer part of the analysis — focusing on
            # it would resurrect a dead finding. Skip like an unknown node.
            if getattr(focus_node, "status", None) == NodeStatus.PRUNED:
                continue
            neighbor_lines = _collect_neighbor_lines(
                node_id, graph.edges, nodes_by_id  # type: ignore[arg-type]
            )
            blocks.append(
                build_focus_node_block(
                    focus_node,  # type: ignore[arg-type]
                    neighbor_lines,
                )
            )
        if not blocks:
            return base
        return "\n".join(blocks) + "\n" + base

    @staticmethod
    def _build_prompt(job: JobMessage, output_path: str) -> str:
        """
        Build the user-facing prompt, injecting prior-turn context when present.

        For the first turn in a session (``job.history`` empty) the prompt is
        the standard fresh-analysis instruction. For follow-ups we prepend a
        compact recap of the prior exchanges (capped at the last 6 exchanges =
        12 messages) and classify intent: requests to re-run/recreate/redo
        always trigger a fresh analysis with real tool calls; clarification
        requests answer from prior context; related-but-new questions reuse
        prior tool results and fill only the gaps. The terminal contract
        (write a valid AnalysisResult JSON with id/timestamp/query/summary/
        sections[] with heading+source+content) stays in force so the UI can
        render the follow-up turn identically.

        When ``job.document_context`` is non-empty the assembled prompt is
        wrapped by :meth:`_with_document_context` so a clearly-delimited
        DOCUMENT section precedes the question (add-in grounding). Absent →
        behaviour unchanged.
        """
        return OhrsExecutor._with_document_context(
            job, OhrsExecutor._build_core_prompt(job, output_path)
        )

    @staticmethod
    def _with_document_context(job: JobMessage, prompt: str) -> str:
        """
        Prepend a delimited DOCUMENT section to ``prompt`` when supplied.

        The Office add-in sends the open document's text in
        ``job.document_context`` to ground the answer. We frame it as context
        (explicitly: do NOT cite it as a source) ahead of the actual prompt
        so the agent reads the grounding first. When the field is empty or
        whitespace-only the prompt is returned byte-identical — the
        regression contract for ordinary web-chat messages.
        """
        doc = (job.document_context or "").strip()
        if not doc:
            return prompt
        return (
            "# DOCUMENT VAN DE GEBRUIKER (context)\n"
            "De gebruiker werkt in dit document. Gebruik het als context voor "
            "de vraag; citeer er niet uit alsof het een bron is.\n"
            "\n"
            f"{doc}\n"
            "\n"
            "# VRAAG\n"
            f"{prompt}"
        )

    @staticmethod
    def _build_core_prompt(job: JobMessage, output_path: str) -> str:
        """
        Assemble the prompt body (query + optional follow-up recap).

        Split out from :meth:`_build_prompt` so the document-context wrapper
        composes cleanly around the unchanged first-turn / follow-up logic.
        """
        base_instruction = (
            f"User query: {job.query}\n\n"
            f"Use at least 3 of the local skills/tools (prefer /capelle-analyse as orchestrator). "
            f"When done, write the AnalysisResult JSON to the ABSOLUTE path "
            f"{output_path} per the contract in the system prompt."
        )

        history = job.history or []
        if not history:
            return base_instruction

        # Keep only the last 12 messages (≈6 user/assistant exchanges) to
        # bound the prompt size regardless of how long the session runs.
        recent = history[-12:]

        lines = ["Prior conversation in this session (oldest first, truncated):"]
        for entry in recent:
            role = entry.get("role", "user")
            content = (entry.get("content") or "").strip()
            # Each entry content is already capped at 2000 chars upstream;
            # collapse internal whitespace so the recap stays compact.
            content = re.sub(r"\s+", " ", content)
            # Strip JSON-looking passages from the recap so the agent cannot
            # lean on prior AnalysisResult numbers and must re-fetch for
            # precision. Heuristic: drop anything that looks like a JSON
            # object or array literal.
            content = re.sub(r"\{[^{}]*\}", "[…json…]", content)
            content = re.sub(r"\[[^\[\]]*\]", "[…array…]", content)
            # 200-char ceiling per message — tight enough that the agent
            # cannot parrot numbers from the recap but loose enough to
            # preserve intent ("you asked about jeugdzorg in Capelle…").
            if len(content) > 200:
                content = content[:200].rstrip() + "…"
            label = "User" if role == "user" else "Assistant"
            lines.append(f"- {label}: {content}")

        follow_up_block = (
            "\n".join(lines)
            + f"\n\nNew user message: {job.query}\n\n"
            "# Intent classification — do this FIRST\n"
            "Classify the new message and act accordingly:\n"
            "\n"
            "1. **Re-run / recreate / redo / refresh** — triggers: "
            "'opnieuw', 'nog een keer', 'doe dat nog eens', 'recreate', "
            "'redo', 'rerun', 'dezelfde analyse', 'vernieuw', 'update'. "
            "Treat this as a FRESH analysis. Run the full skill chain "
            "(`/capelle-analyse` or cbs+beleid+budget) with real tool calls. "
            "Produce a complete AnalysisResult just like a first-turn "
            "analysis. Do NOT copy numbers from the recap above — re-fetch.\n"
            "\n"
            "2. **Clarify / summarize / explain** — triggers: 'leg uit', "
            "'wat bedoel je', 'geef een samenvatting', 'vat samen', 'in het "
            "kort'. Answer from prior context without tools. Emit narrative "
            "JSON (below).\n"
            "\n"
            "3. **Related-but-new question** — anything else that extends "
            "the topic. Reuse prior tool results where relevant and only "
            "fetch the gap. Include a full AnalysisResult (below).\n"
            "\n"
            "**If in doubt between 'reuse' and 'rerun', prefer RERUN.** A "
            "fresh trajectory with real `tool_output` is the contract; a "
            "narrative summary alone is NOT a complete analysis.\n"
            "\n"
            "# Terminal contract — same as first turn\n"
            f"Write the final AnalysisResult JSON to the ABSOLUTE path "
            f"{output_path}. Required fields: "
            "`id`, `timestamp`, `query`, `summary`, `sections[]` with "
            "`heading` + `source` + `content` (and `tool_output` when tools "
            "were run), per the schema in the system prompt. Narrative in "
            "Dutch. Schema-incomplete JSON is a failure just like an empty "
            "analysis."
        )
        return follow_up_block

    @staticmethod
    def _parse_trajectory_line(
        line: str,
    ) -> tuple[dict[str, Any], str | None] | None:
        """
        Parse one versioned trajectory line into ``(event, dedup_key)``.

        Consumes the contract-C1 ``kind``-tagged JSONL. Two kinds yield a
        progress event for the live tailer fallback:

        - ``assistant`` lines whose ``tool_calls[]`` is non-empty — one
          ``running`` event per tool call, keyed by ``make_dedup_key`` so it
          isn't re-broadcast for something the HTTP ``pre_tool_use`` hook
          already emitted. Only the FIRST tool call is surfaced per line (the
          tailer is a coarse safety net; the hook carries full fidelity).
        - ``tool_result`` lines — a ``done`` event keyed on the
          ``tool_use_id`` so a result is de-duped against the hook's
          ``post_tool_use`` delivery.

        Returns ``None`` for ``meta``/``usage``/``end`` lines, malformed
        JSON, wrong schema version, or an ``assistant`` line with no tool
        calls (pure text turns carry no progress signal).
        """
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            return None
        if not isinstance(entry, dict):
            return None
        if entry.get("v") != _TRAJECTORY_SCHEMA_VERSION:
            return None

        kind = entry.get("kind")

        if kind == _KIND_ASSISTANT:
            tool_calls = entry.get("tool_calls")
            if not isinstance(tool_calls, list) or not tool_calls:
                return None
            for call in tool_calls:
                if not isinstance(call, dict):
                    continue
                tool_name = call.get("name") or "unknown"
                tool_input = call.get("arguments")
                if not isinstance(tool_input, dict):
                    tool_input = {}
                description = OhrsExecutor._describe_tool_use(
                    tool_name, tool_input
                )
                event = {
                    "type": "tool_progress",
                    "tool": tool_name,
                    "action": description,
                    "status": "running",
                    "source": "trajectory",
                }
                return event, OhrsExecutor.make_dedup_key(tool_name, tool_input)
            return None

        if kind == _KIND_TOOL_RESULT:
            tool_use_id = entry.get("tool_use_id") or ""
            event = {
                "type": "tool_progress",
                "tool": entry.get("name") or tool_use_id,
                "action": "voltooid",
                "status": "done",
                "source": "trajectory",
            }
            # Key on the tool_use_id so this de-dupes against the hook's
            # post_tool_use delivery for the same call.
            dedup_key = f"result|{tool_use_id}" if tool_use_id else None
            return event, dedup_key

        return None

    @staticmethod
    def _describe_tool_use(tool_name: str, tool_input: dict[str, Any]) -> str:
        """Generate a human-readable description of a tool invocation."""
        if tool_name == "Bash":
            cmd = tool_input.get("command", "")
            # Friendly, brand-free labels that match BOTH the legacy capelle-*
            # tool names and their display aliases in the activity feed.
            if "capelle-beleid" in cmd or _cmd_invokes(cmd, "beleid"):
                return f"Beleid: zoekt documenten..."
            if "capelle-cube" in cmd or _cmd_invokes(cmd, "cube"):
                return f"Cube: analyseert tabel..."
            if "cbs " in cmd:
                return f"CBS: {cmd[:80]}"
            if "capelle-budget" in cmd:
                return f"Budget: analyseert uitgaven..."
            if "capelle-buitenbeter" in cmd:
                return f"BuitenBeter: haalt meldingen op..."
            if "pdftotext" in cmd:
                return f"Enquete: leest bewonersenquete..."
            # Last-resort echo: de-brand so no "capelle-" tool name leaks.
            return f"Uitvoeren: {_debrand_cmd(cmd)[:60]}"
        if tool_name == "Skill":
            skill = tool_input.get("skill", "")
            return f"Skill: /{skill} gestart"
        if tool_name == "Read":
            path = tool_input.get("file_path", "")
            return f"Leest: {Path(path).name}"
        if tool_name == "Grep":
            pattern = tool_input.get("pattern", "")
            return f"Zoekt: '{pattern}'"
        return f"{tool_name}: bezig..."

    @staticmethod
    def _clean(text: str) -> str:
        """Strip ANSI escape codes and normalize whitespace."""
        return _ANSI_RE.sub("", text).strip()

    @staticmethod
    def _diagnose_from_scan(scan: _TrajectoryScan) -> str | None:
        """
        Build the empty-final-turn diagnosis message from a scan (contract C1).

        The dominant failure mode: the agent issued one or more large data
        fetches (typically ``cbs get <table> --series`` with no filter), the
        ``tool_result`` lines exceeded ~500 KB each, the combined context
        overflowed the model, and codex returned an empty final turn. The
        user otherwise sees only a bare "kon niet worden geparsed" error.

        The signature requires the LAST assistant turn to be empty (no text,
        no tool calls) AND at least one oversized ``tool_result`` line. When
        matched, returns a Dutch message naming the bash commands we ran and
        advising the user to narrow the query; ``None`` otherwise so the
        caller falls back to the generic message.

        Note: with the per-turn ``usage`` schema (C1) the old "final turn
        reported 0 tokens" proxy is gone — an empty final turn simply has no
        ``usage`` line — so the oversized-tool-result signal is now the sole
        trigger, which is the case this message exists to explain.
        """
        if not scan.saw_assistant or not scan.last_assistant_empty:
            return None
        if scan.big_tool_result_count == 0:
            return None

        # Build a short summary of what we ran so the user gets some signal.
        tool_hits: list[str] = []
        seen_prefixes: set[str] = set()
        for cmd in scan.bash_commands:
            head = cmd.split("\n", 1)[0].strip()
            if head.startswith("cbs "):
                prefix = "cbs"
            elif head.startswith("capelle-beleid"):
                prefix = "capelle-beleid"
            elif head.startswith("capelle-budget"):
                prefix = "capelle-budget"
            elif head.startswith("capelle-buitenbeter"):
                prefix = "capelle-buitenbeter"
            elif "pdftotext" in head:
                prefix = "pdftotext (enquete)"
            else:
                continue
            if prefix in seen_prefixes:
                continue
            seen_prefixes.add(prefix)
            # Trim the command to at most 110 chars for display
            display = head if len(head) <= 110 else head[:107] + "…"
            tool_hits.append(f"`{display}`")

        parts: list[str] = []
        parts.append(
            "De analyse is voortijdig afgebroken: het model kreeg teveel "
            "gegevens terug om te verwerken en leverde geen eindrapport op."
        )
        if tool_hits:
            parts.append(
                "We hebben de volgende tool-oproepen uitgevoerd: "
                + ", ".join(tool_hits)
                + "."
            )
        if scan.big_tool_result_count > 0:
            approx_mb = scan.big_tool_result_bytes / (1024 * 1024)
            parts.append(
                f"Eén of meer tool-resultaten waren samen ongeveer "
                f"{approx_mb:.1f} MB groot, wat de context-limiet overschreed."
            )
        parts.append(
            "Probeer de vraag te vernauwen — bijvoorbeeld een specifiek "
            "jaartal, één wijk, of één CBS-tabel — en verstuur opnieuw."
        )
        return " ".join(parts)

    @staticmethod
    def _diagnose_empty_final_turn(trajectory_path: Path) -> str | None:
        """Thin wrapper: scan then diagnose (see :meth:`_diagnose_from_scan`)."""
        return OhrsExecutor._diagnose_from_scan(
            OhrsExecutor._scan_trajectory(trajectory_path)
        )

    def _best_text_from_scan(
        self, stdout: bytes | None, scan: _TrajectoryScan
    ) -> str:
        """
        Last-resort text extraction from stdout + a completed scan.

        Used for follow-up turns where the agent answers conversationally
        without saving a JSON file. Tries, in order:
          1. Clean plain-text stdout (non-JSON — e.g. ``--output-format text``)
          2. All assistant text gathered by the scan, joined oldest-first
          3. Clean stdout as a final fallback
        Returns the first non-empty string, or an empty string.
        """
        stdout_text = stdout.decode("utf-8", errors="replace").strip() if stdout else ""
        if stdout_text and not stdout_text.startswith("{"):
            # Plain text stdout (e.g. --output-format text) — use as-is.
            return self._clean(stdout_text)

        if scan.assistant_texts:
            return self._clean("\n\n".join(scan.assistant_texts))
        return self._clean(stdout_text) if stdout_text else ""

    def _extract_best_text(
        self, stdout: bytes | None, trajectory_path: Path
    ) -> str:
        """Thin wrapper: scan then extract (see :meth:`_best_text_from_scan`)."""
        return self._best_text_from_scan(
            stdout, self._scan_trajectory(trajectory_path)
        )

    @staticmethod
    def _lenient_json_loads(raw: str) -> dict[str, Any] | None:
        """
        Best-effort JSON parser that repairs common LLM mistakes.

        Agents sometimes embed Bash commands with single-quote escapes like
        ``\\'`` inside JSON strings, which is not valid JSON.  This function
        tries a strict parse first, then applies progressively more aggressive
        repairs.  Returns None if nothing works.
        """
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            pass

        # Repair 1: backslash-single-quote is not a valid JSON escape; strip it
        repaired = re.sub(r"\\'", "'", raw)
        try:
            return json.loads(repaired)
        except json.JSONDecodeError:
            pass

        # Repair 2: convert any stray unrecognised backslash-escape to the
        # literal character. JSON only accepts " \ / b f n r t u.
        repaired = re.sub(r'\\([^"\\/bfnrtu])', r"\1", raw)
        try:
            return json.loads(repaired)
        except json.JSONDecodeError:
            pass

        # Repair 3: combine both
        repaired = re.sub(r'\\([^"\\/bfnrtu])', r"\1", re.sub(r"\\'", "'", raw))
        try:
            return json.loads(repaired)
        except json.JSONDecodeError:
            return None

    @staticmethod
    def _parse_markdown_to_analysis(
        query: str, markdown: str
    ) -> dict[str, Any] | None:
        """
        Fallback parser: extract an AnalysisResult from a markdown-heavy response.

        Agents sometimes inline ```json blocks containing tool_output structures
        instead of saving an AnalysisResult JSON file. This parser splits the
        markdown by top-level sections (## heading) and promotes each embedded
        tool_output into a proper AnalysisSection.
        """
        if not markdown:
            return None

        # Find all fenced JSON code blocks
        json_block_re = re.compile(r"```json\s*\n(.*?)\n```", re.DOTALL)
        blocks: list[tuple[int, dict[str, Any]]] = []
        for m in json_block_re.finditer(markdown):
            try:
                parsed = json.loads(m.group(1))
            except json.JSONDecodeError:
                continue
            blocks.append((m.start(), parsed))

        if not blocks:
            return None

        # Split markdown by ## headings; each section may own the blocks that
        # appear inside its span.
        heading_re = re.compile(r"^## (.+)$", re.MULTILINE)
        heading_matches = list(heading_re.finditer(markdown))

        sections: list[dict[str, Any]] = []
        if heading_matches:
            summary = markdown[:heading_matches[0].start()].strip()
            for i, h in enumerate(heading_matches):
                start = h.end()
                end = heading_matches[i + 1].start() if i + 1 < len(heading_matches) else len(markdown)
                heading_text = h.group(1).strip()
                body = markdown[start:end]

                tool_output = None
                for block_pos, block in blocks:
                    if start <= block_pos < end:
                        candidate = block.get("tool_output", block)
                        if isinstance(candidate, dict) and "data" in candidate:
                            tool_output = candidate
                            break

                # Strip fenced JSON from the displayed narrative
                clean_body = json_block_re.sub("", body).strip()

                source = (tool_output or {}).get("tool", "platform")
                sections.append({
                    "heading": heading_text,
                    "source": source,
                    "content": clean_body,
                    "tool_output": tool_output,
                })
        else:
            summary = markdown.strip()

        if not sections:
            for _, block in blocks:
                candidate = block.get("tool_output", block)
                if isinstance(candidate, dict) and "data" in candidate:
                    sections.append({
                        "heading": candidate.get("query", "Analyse"),
                        "source": candidate.get("tool", "platform"),
                        "content": "",
                        "tool_output": candidate,
                    })

        if not sections:
            # Pure-markdown fallback: split by ## headings, no tool_output data,
            # infer source from heading text
            if heading_matches:
                summary = markdown[:heading_matches[0].start()].strip()
                for i, h in enumerate(heading_matches):
                    start = h.end()
                    end = heading_matches[i + 1].start() if i + 1 < len(heading_matches) else len(markdown)
                    heading_text = h.group(1).strip()
                    body = markdown[start:end].strip()
                    sections.append({
                        "heading": heading_text,
                        "source": OhrsExecutor._guess_source(heading_text + " " + body),
                        "content": body,
                        "tool_output": None,
                    })

        if not sections:
            return None

        # Emit v2 blocks (NOT the retired v1 sections schema): the frontend
        # renders blocks natively, and a tool_output without a real data
        # array is DROPPED rather than shipped as a dataless stub that
        # crashes the report.
        blocks_out: list[dict[str, Any]] = []
        for s in sections:
            heading = s.get("heading")
            if heading:
                blocks_out.append(
                    {"type": "heading", "level": 2, "text": heading}
                )
            content = s.get("content")
            if content:
                blocks_out.append({"type": "prose", "markdown": content})
            to = s.get("tool_output")
            if (
                isinstance(to, dict)
                and isinstance(to.get("data"), list)
                and to["data"]
            ):
                cols = to.get("columns")
                blocks_out.append({
                    "type": "table",
                    "columns": cols if isinstance(cols, list) else [],
                    "data": to["data"],
                    "caption": to.get("query", "") or "",
                })

        result: dict[str, Any] = {
            "schema_version": 2,
            "id": __import__("uuid").uuid4().hex[:12],
            "timestamp": __import__("datetime").datetime.now(
                __import__("datetime").timezone.utc
            ).isoformat(),
            "query": query,
            "summary": summary[:2000],
            "blocks": blocks_out,
            "citations": [],
        }
        # Report title: the first heading block when present; otherwise omit so
        # the frontend falls back to the query.
        fallback_title = next(
            (
                b.get("text")
                for b in blocks_out
                if b.get("type") == "heading" and b.get("text")
            ),
            None,
        )
        if fallback_title:
            result["title"] = fallback_title
        return result

    @staticmethod
    def _guess_source(text: str) -> str:
        """
        Infer tool source from section heading/content keywords.

        Falls back to 'platform' if no specific source matches.
        """
        lower = text.lower()
        if any(k in lower for k in ["enquete", "enquête", "rapportcijfer", "bewoners"]):
            return "bewonersenquete"
        if any(k in lower for k in ["begroting", "beleid", "jaarstukken", "voorjaarsnota", "najaarsnota"]):
            return "beleid"
        if any(k in lower for k in ["budget", "taakveld", "uitgaven", "lasten", "baten", "€"]):
            return "budget"
        if any(k in lower for k in ["buitenbeter", "melding", "klacht"]):
            return "buitenbeter"
        if any(k in lower for k in ["cbs", "statline", "misdrijven"]):
            return "cbs"
        return "platform"

    @staticmethod
    def _strip_fabricated_source_urls(analysis: dict[str, Any]) -> int:
        """
        Strip fabricated/malformed ``source_url`` values from an AnalysisResult.

        WHY: only the beleid tool emits ``source_url`` (constructed by the
        indexer at Capelle_beleid/build_vectordb.py:125-134 in a separate
        repo). Every other tool (cbs, budget, buitenbeter, bewonersenquete)
        emits NO source_url, so any URL on a non-beleid section is 100%
        fabricated by the LLM. Additionally, the agent sometimes truncates
        or mangles beleid URLs (e.g. dropping the ``/bestanden/`` segment,
        turning a valid URL into a 404). Fabricated / broken URLs erode
        user trust in the citation footer, so we post-filter them out
        unconditionally before the result leaves the executor.

        HOW: walk every section. If ``source != "beleid"``, delete the
        ``source_url`` key on every row in ``tool_output.data``. If
        ``source == "beleid"``, keep the URL only if it matches
        :data:`_BELEID_URL_RE` (scheme+host + doc-type-year slug +
        programma|paragraaf|bestuur|bestanden + final slug). Otherwise
        delete it.

        Malformed inputs (missing ``sections``, non-list ``sections``,
        non-dict row, non-dict ``tool_output``) are skipped silently —
        the method never raises.

        Returns the number of ``source_url`` keys stripped so the caller
        can emit a single per-job warning instead of per-URL spam.
        """
        stripped = 0

        # --- Legacy v1 path: walk sections[*].tool_output.data rows ---
        sections = analysis.get("sections")
        if isinstance(sections, list):
            for section in sections:
                if not isinstance(section, dict):
                    continue
                source_raw = section.get("source", "")
                source = source_raw.lower() if isinstance(source_raw, str) else ""

                tool_output = section.get("tool_output")
                if not isinstance(tool_output, dict):
                    continue
                rows = tool_output.get("data")
                if not isinstance(rows, list):
                    continue

                for row in rows:
                    if not isinstance(row, dict):
                        continue
                    url = row.get("source_url")
                    if not isinstance(url, str) or not url:
                        continue

                    if source not in ("beleid", "regelgeving"):
                        del row["source_url"]
                        stripped += 1
                        continue

                    ok = (
                        bool(_BELEID_URL_RE.match(url))
                        if source == "beleid"
                        else url.startswith(("http://", "https://"))
                    )
                    if not ok:
                        del row["source_url"]
                        stripped += 1

        # --- v2 path: enforce URL policy on citations[] ---
        # Each citation is expected to carry a ``source`` field (e.g.
        # ``"beleid"``, ``"cbs"``) and an optional ``source_url``.  Only
        # beleid citations may carry a ``source_url``, and only when it
        # matches :data:`_BELEID_URL_RE`.
        citations = analysis.get("citations")
        if isinstance(citations, list):
            for citation in citations:
                if not isinstance(citation, dict):
                    continue
                url = citation.get("source_url")
                if not isinstance(url, str) or not url:
                    continue

                source_raw = citation.get("source", "")
                source = source_raw.lower() if isinstance(source_raw, str) else ""

                # beleid + regelgeving carry tool-provided URLs; every other
                # source emits none, so a URL there is LLM-fabricated.
                if source not in ("beleid", "regelgeving"):
                    del citation["source_url"]
                    stripped += 1
                    continue

                # beleid URLs have a known indexer-built shape; regelgeving
                # URLs come straight from the corpus, so just sanity-check
                # that it is an http(s) URL rather than fabricated junk.
                ok = (
                    bool(_BELEID_URL_RE.match(url))
                    if source == "beleid"
                    else url.startswith(("http://", "https://"))
                )
                if not ok:
                    del citation["source_url"]
                    stripped += 1

        return stripped

    @staticmethod
    def _strip_empty_source_rows(analysis: dict[str, Any]) -> int:
        """
        Remove ``tool_output.data`` rows that carry no identifying content.

        WHY: when a tool call failed (network glitch, rate limit, skip
        error) the agent sometimes still emits placeholder rows to pad
        the ``data`` array. Those rows land in the sources footer as
        "— / — / — / —" which both looks broken and lies about how
        much the analysis actually consulted. Dropping them is safer
        than trying to reconstruct missing fields — the narrative
        ``content`` is the right place to explain a tool failure.

        HOW: a row is considered empty when every user-visible
        identifier (``doc_type``, ``document``, ``year``, ``source_url``,
        ``name``, ``title``) is either missing, ``None``, or the empty
        string after trimming. Such rows are removed in place; all
        other rows are left untouched. Never raises on malformed input.

        Returns the number of rows dropped.
        """
        dropped = 0
        identity_keys = ("doc_type", "document", "year", "source_url", "name", "title")

        def _is_empty_row(row: Any) -> bool:
            """Return True when every identity key in *row* is absent/None/blank."""
            if not isinstance(row, dict):
                return False
            for key in identity_keys:
                value = row.get(key)
                if value is None:
                    continue
                if isinstance(value, str):
                    if value.strip() != "":
                        return False
                else:
                    # numeric / bool / dict → count as identifying
                    return False
            return True

        def _drop_empty_rows(rows: list[Any]) -> tuple[list[Any], int]:
            """Filter *rows* in place and return (kept, n_dropped)."""
            kept: list[Any] = []
            n = 0
            for row in rows:
                if _is_empty_row(row):
                    n += 1
                else:
                    kept.append(row)
            return kept, n

        def _is_empty_data_row(row: Any) -> bool:
            """Empty check for chart/table DATA rows.

            Unlike legacy sources-footer rows (which carry identity keys like
            ``document``/``year``), v2 chart/table rows are real data such as
            ``{"jaar": 2019, "cohesie": 5.2}``. A data row is empty only when
            it has no keys, or every VALUE is None/blank — NOT when the
            identity keys are absent (they never apply here).
            """
            if not isinstance(row, dict):
                return False
            if not row:
                return True
            for value in row.values():
                if value is None:
                    continue
                if isinstance(value, str):
                    if value.strip() != "":
                        return False
                else:
                    # numeric / bool / etc. is real content
                    return False
            return True

        def _drop_empty_data_rows(rows: list[Any]) -> tuple[list[Any], int]:
            """Filter chart/table data *rows* by the value-based check."""
            kept: list[Any] = []
            n = 0
            for row in rows:
                if _is_empty_data_row(row):
                    n += 1
                else:
                    kept.append(row)
            return kept, n

        # --- Legacy v1 path: sections[*].tool_output.data ---
        sections = analysis.get("sections")
        if isinstance(sections, list):
            for section in sections:
                if not isinstance(section, dict):
                    continue
                tool_output = section.get("tool_output")
                if not isinstance(tool_output, dict):
                    continue
                rows = tool_output.get("data")
                if not isinstance(rows, list):
                    continue
                kept, n = _drop_empty_rows(rows)
                tool_output["data"] = kept
                dropped += n

        # --- v2 path: blocks[*] (chart → spec.data; table → data) ---
        # Chart blocks carry their row data under ``block["spec"]["data"]``;
        # table blocks carry theirs directly under ``block["data"]``.
        blocks = analysis.get("blocks")
        if isinstance(blocks, list):
            for block in blocks:
                if not isinstance(block, dict):
                    continue
                block_type = block.get("type", "")

                if block_type == "chart":
                    spec = block.get("spec")
                    if not isinstance(spec, dict):
                        continue
                    rows = spec.get("data")
                    if not isinstance(rows, list):
                        continue
                    kept, n = _drop_empty_data_rows(rows)
                    spec["data"] = kept
                    dropped += n

                elif block_type == "table":
                    rows = block.get("data")
                    if not isinstance(rows, list):
                        continue
                    kept, n = _drop_empty_data_rows(rows)
                    block["data"] = kept
                    dropped += n

        return dropped

    def _find_analysis_result(
        self, job: JobMessage, started_at_ns: int
    ) -> dict[str, Any] | None:
        """
        Locate the AnalysisResult JSON produced by the /capelle-analyse skill.

        As of the Day 1 sandbox pivot the agent writes to
        ``{job_dir}/analysis.json`` — the only writable path under
        ProtectSystem=strict. We also still scan the legacy
        {working_dir}/capelle_rag/analyses/*.json location so analyses
        produced by an older agent (e.g. during deploys that cross versions)
        are still pickable up. Newest file wins.
        """
        candidates: list[tuple[int, Path]] = []

        # Primary: per-job dir (new path, sandbox-safe).
        job_dir = self._jobs_dir / job.message_id
        primary = job_dir / "analysis.json"
        if primary.exists():
            try:
                candidates.append((primary.stat().st_mtime_ns, primary))
            except OSError:
                pass

        # Legacy: shared analyses/ dir. Still scanned for backwards compat.
        analyses_dir = self._working_dir / "capelle_rag" / "analyses"
        if analyses_dir.exists():
            for path in analyses_dir.glob("*.json"):
                try:
                    mtime_ns = path.stat().st_mtime_ns
                except OSError:
                    continue
                if mtime_ns >= started_at_ns:
                    candidates.append((mtime_ns, path))

        if not candidates:
            return None

        candidates.sort(reverse=True)
        for _, path in candidates:
            try:
                raw = path.read_text(encoding="utf-8")
            except OSError:
                continue
            data = self._lenient_json_loads(raw)
            if data is None:
                _logger.warning(
                    "analysis_file_invalid_json",
                    message_id=job.message_id,
                    path=str(path),
                )
                continue
            if not isinstance(data, dict):
                continue
            # Accept both v1 (has "sections") and v2 (has "blocks" or
            # schema_version==2). Both must have "summary" and "query".
            has_summary = "summary" in data
            has_query = "query" in data
            is_v1 = "sections" in data
            is_v2 = data.get("schema_version") == 2 or "blocks" in data
            if has_summary and has_query and (is_v1 or is_v2):
                # Normalize optional array fields the model sometimes omits
                # when writing via the Write tool — the frontend expects
                # them as arrays and `foo.length` crashes on undefined.
                # These fields are v1-specific; v2 reports use blocks/citations.
                for arr_field in ("data_gaps", "follow_up"):
                    if not isinstance(data.get(arr_field), list):
                        data[arr_field] = []
                schema = data.get("schema_version", 1)
                _logger.info(
                    "analysis_result_found",
                    message_id=job.message_id,
                    path=str(path),
                    schema_version=schema,
                    section_count=len(data.get("sections", [])),
                    block_count=len(data.get("blocks", [])),
                )
                return data
        return None

    def _parse_ohrs_output(
        self,
        stdout: bytes,
        scan: _TrajectoryScan,
        job: JobMessage,
    ) -> dict[str, Any]:
        """
        Parse the ohrs print-mode output into a structured result dict (C2).

        Because the executor spawns ``oh -p ... --output-format json``, ohrs
        prints **exactly one** JSON object on stdout (contract C2)::

            {"v":1,"result":"<final assistant text>","model":"<str>",
             "status":"ok"|"error"|"max_turns","error":<str|null>,
             "usage":{...}}

        We parse that object for the final ``result`` text. When stdout is
        missing/malformed (crash before the object was printed), we fall back
        to the assistant text the trajectory scan already gathered, then to
        raw stdout, then to a generic Dutch apology.
        """
        stdout_text = stdout.decode("utf-8", errors="replace").strip() if stdout else ""

        parsed = self._parse_print_mode_json(stdout_text, job.message_id)
        if parsed is not None:
            result_text = parsed.get("result")
            if isinstance(result_text, str) and result_text.strip():
                return {
                    "type": "ohrs_response",
                    "content": self._clean(result_text),
                    "query": job.query,
                    "skill": job.skill,
                }
            # The object parsed but carried no usable text (e.g. status
            # "error"/"max_turns" with an empty result). Fall through to the
            # trajectory/stdout recovery below rather than returning blank.

        # Fallback 1: best assistant text the single scan already collected.
        if scan.assistant_texts:
            return {
                "type": "ohrs_response",
                "content": self._clean("\n\n".join(scan.assistant_texts)),
                "query": job.query,
                "skill": job.skill,
            }

        # Fallback 2: raw stdout (e.g. a non-JSON line slipped through).
        if stdout_text:
            return {
                "type": "ohrs_response",
                "content": self._clean(stdout_text),
                "query": job.query,
                "skill": job.skill,
            }

        return {
            "type": "ohrs_response",
            "content": "De analyse is afgerond maar het resultaat kon niet worden geparsed.",
            "query": job.query,
            "skill": job.skill,
        }

    @staticmethod
    def _parse_print_mode_json(
        stdout_text: str, message_id: str
    ) -> dict[str, Any] | None:
        """
        Parse the single contract-C2 JSON object printed in ``--output-format json``.

        In json mode ohrs emits exactly one object and no other stdout noise,
        so a strict whole-string parse is the common path. As a tolerance for
        a stray leading/trailing line we also try the last non-empty line.
        Returns the parsed object (validated ``v == 1`` and dict-shaped) or
        ``None`` when nothing parses.
        """
        if not stdout_text:
            return None

        candidates = [stdout_text]
        # Tolerate a single stray line by also trying the last non-empty line.
        lines = [ln.strip() for ln in stdout_text.splitlines() if ln.strip()]
        if lines and lines[-1] != stdout_text:
            candidates.append(lines[-1])

        for candidate in candidates:
            try:
                obj = json.loads(candidate)
            except json.JSONDecodeError:
                continue
            if not isinstance(obj, dict):
                continue
            if obj.get("v") != _TRAJECTORY_SCHEMA_VERSION:
                # Unknown/legacy stdout shape — don't trust it as C2.
                _logger.warning(
                    "ohrs_stdout_unexpected_version",
                    message_id=message_id,
                    version=obj.get("v"),
                )
                continue
            status = obj.get("status")
            if status in (_END_STATUS_ERROR, _END_STATUS_MAX_TURNS):
                _logger.warning(
                    "ohrs_print_mode_nonok_status",
                    message_id=message_id,
                    status=status,
                    error=str(obj.get("error"))[:300] if obj.get("error") else None,
                )
            return obj
        return None
