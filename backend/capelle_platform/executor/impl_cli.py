"""
CLI subprocess executor implementation.

Runs Capelle analysis tools as child processes — the same tools that
exist in the repo (cbs, capelle-beleid, capelle-budget, capelle-buitenbeter).
For the MVP, this executor runs a simplified analysis pipeline directly
rather than spawning ohrs, keeping external dependencies minimal.

The executor reads existing analysis JSON files from capelle_rag/analyses/
when available, and can also invoke CLI tools to produce fresh results.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from capelle_platform.executor.base_executor import BaseExecutor
from capelle_platform.models.job import JobMessage
from capelle_platform.observability import get_logger
from capelle_platform.observability.logger import TraceContext
from capelle_platform.settings import Settings

_logger = get_logger(__name__)


class CLIExecutor(BaseExecutor):
    """
    Execute analysis jobs by invoking CLI tools as async subprocesses.

    Each job runs in isolation — no shared mutable state between concurrent
    executions.  Subprocess stdout is captured and parsed as JSON.
    """

    def __init__(self, settings: Settings) -> None:
        """
        Construct with project paths from Settings.

        NOTE (EXEC-5): this MVP executor is NOT wired in production — the
        Builder only ever constructs :class:`OhrsExecutor`. The ``Settings``
        fields it originally read (``project_root`` / ``analyses_dir``) no
        longer exist, so the class was in fact non-constructible. We derive
        them from the ohrs paths to keep the (legacy/dev) executor importable
        and testable; the cancel-key fix below is the substantive change.
        """
        self._project_root = settings.ohrs_working_dir
        self._analyses_dir = settings.ohrs_jobs_dir
        self._timeout = settings.job_timeout_seconds
        # Running tool subprocesses keyed by the composite key
        # ``f"{analysis_id}:{tool_name}"`` (a single analysis may have
        # several tools in flight). ``_tool_keys`` indexes those composite
        # keys by ``analysis_id`` so ``cancel(analysis_id)`` can terminate
        # every tool belonging to that analysis (EXEC-5) — honouring the
        # ``BaseExecutor.cancel(analysis_id)`` contract, which previously
        # popped the bare ``analysis_id`` and so never matched a stored key.
        self._running_procs: dict[str, asyncio.subprocess.Process] = {}
        self._tool_keys: dict[str, set[str]] = {}

    async def execute(self, job: JobMessage) -> dict[str, Any]:
        """
        Run an analysis for the given job.

        Strategy:
        1. Try to run CLI tools (cbs search, capelle-beleid search) for the query.
        2. Collect results into a structured analysis result.
        3. Save to analyses_dir and return the result dict.

        Falls back gracefully if individual tools fail — partial results
        are still valuable.
        """
        TraceContext.set(job.trace_id)
        _logger.info(
            "executor_starting",
            analysis_id=job.analysis_id,
            query=job.query,
            skill=job.skill,
        )

        sections: list[dict[str, Any]] = []

        cbs_result = await self._run_tool(
            job.analysis_id,
            "cbs",
            ["cbs", "search", job.query, "--limit", "5"],
        )
        if cbs_result:
            sections.append({
                "heading": f"StatLine resultaten voor '{job.query}'",
                "source": "cbs",
                "content": cbs_result,
            })

        beleid_result = await self._run_tool(
            job.analysis_id,
            "capelle-beleid",
            ["capelle-beleid", "search", job.query],
        )
        if beleid_result:
            sections.append({
                "heading": f"Beleidsdocumenten over '{job.query}'",
                "source": "beleid",
                "content": beleid_result,
            })

        if not sections:
            sections.append({
                "heading": f"Analyse: {job.query}",
                "source": "platform",
                "content": (
                    f"Analyse gestart voor '{job.query}'. "
                    "De CLI tools zijn momenteel niet beschikbaar. "
                    "Probeer het later opnieuw of controleer of de tools geinstalleerd zijn."
                ),
            })

        result: dict[str, Any] = {
            "id": job.analysis_id,
            "query": job.query,
            "summary": f"Analyse van '{job.query}' uitgevoerd met {len(sections)} bronnen.",
            "sections": sections,
            "data_gaps": [],
            "follow_up": [],
        }

        self._save_result(job.analysis_id, result)
        _logger.info(
            "executor_completed",
            analysis_id=job.analysis_id,
            section_count=len(sections),
        )
        return result

    async def cancel(self, analysis_id: str) -> None:
        """
        Terminate every running tool subprocess belonging to ``analysis_id``.

        ``_run_tool`` stores each child under ``f"{analysis_id}:{tool_name}"``;
        this method resolves the composite keys via the ``_tool_keys`` index
        so a cancel-by-analysis-id terminates all of that analysis's tools,
        per the :meth:`BaseExecutor.cancel` contract.
        """
        keys = self._tool_keys.pop(analysis_id, set())
        for key in keys:
            proc = self._running_procs.pop(key, None)
            if proc and proc.returncode is None:
                proc.terminate()
        if keys:
            _logger.info(
                "executor_cancelled",
                analysis_id=analysis_id,
                tool_count=len(keys),
            )

    def _register_proc(
        self, analysis_id: str, key: str, proc: asyncio.subprocess.Process
    ) -> None:
        """Track ``proc`` under its composite ``key`` and index it by analysis."""
        self._running_procs[key] = proc
        self._tool_keys.setdefault(analysis_id, set()).add(key)

    def _forget_proc(self, analysis_id: str, key: str) -> None:
        """Drop the composite ``key`` from both the proc map and the index."""
        self._running_procs.pop(key, None)
        keys = self._tool_keys.get(analysis_id)
        if keys is not None:
            keys.discard(key)
            if not keys:
                self._tool_keys.pop(analysis_id, None)

    async def _run_tool(
        self,
        analysis_id: str,
        tool_name: str,
        cmd: list[str],
    ) -> str | None:
        """
        Run a single CLI tool as an async subprocess.

        Returns stdout as a string on success, None on failure.
        Never raises — failures are logged and swallowed so partial
        results can still be returned.
        """
        key = f"{analysis_id}:{tool_name}"
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(self._project_root),
            )
            self._register_proc(analysis_id, key, proc)
            try:
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(), timeout=self._timeout
                )
            finally:
                self._forget_proc(analysis_id, key)

            if proc.returncode == 0 and stdout:
                return stdout.decode("utf-8", errors="replace").strip()

            if stderr:
                _logger.warning(
                    "tool_stderr",
                    tool=tool_name,
                    analysis_id=analysis_id,
                    stderr=stderr.decode("utf-8", errors="replace")[:500],
                )
            return None

        except asyncio.TimeoutError:
            _logger.warning(
                "tool_timeout", tool=tool_name, analysis_id=analysis_id
            )
            # Terminate just this tool's child. The `finally` above already
            # dropped the bookkeeping; the proc handle is still live here.
            if proc.returncode is None:
                proc.terminate()
            return None
        except FileNotFoundError:
            _logger.warning(
                "tool_not_found",
                tool=tool_name,
                analysis_id=analysis_id,
            )
            return None
        except Exception:
            _logger.exception(
                "tool_execution_failed",
                tool=tool_name,
                analysis_id=analysis_id,
            )
            return None

    def _save_result(self, analysis_id: str, result: dict[str, Any]) -> None:
        """
        Persist the analysis result JSON to the analyses directory.

        Uses the same directory as the existing capelle_rag/analyses/ so
        the old dashboard can still read results.
        """
        try:
            output_dir = Path(self._analyses_dir)
            output_dir.mkdir(parents=True, exist_ok=True)
            query_slug = result.get("query", "unknown")[:40].lower()
            query_slug = "".join(
                c if c.isalnum() or c == " " else "" for c in query_slug
            )
            query_slug = query_slug.strip().replace(" ", "_")
            filename = f"{query_slug}_{analysis_id}.json"
            path = output_dir / filename
            path.write_text(
                json.dumps(result, ensure_ascii=False, indent=2, default=str),
                encoding="utf-8",
            )
            _logger.info("result_saved", path=str(path))
        except Exception:
            _logger.exception("result_save_failed", analysis_id=analysis_id)
