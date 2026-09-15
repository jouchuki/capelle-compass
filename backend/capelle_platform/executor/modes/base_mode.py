"""Abstract base class for per-mode ohrs configuration.

A ``ModeConfig`` answers four questions for the executor about *one* analysis
mode:

1. Where does ohrs run? (``working_dir``)
2. What system prompt does it see? (``build_system_prompt``)
3. Which skill files must be present before launch? (``required_skill_files``)
4. What workspace files must be symlinked into the per-job directory + what
   extra env vars / PATH additions does the in-process CLI need?
   (``workspace_symlinks``, ``extra_env``, ``path_additions``)

Defining the contract before any implementation (per the billion-dollar-code
blueprint) keeps the executor body free of branching: it composes a config
from the factory and consumes the four answers uniformly.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from capelle_platform.executor.modes.mode_type import Mode


class ModeConfig(ABC):
    """Per-mode configuration consumed by ``OhrsExecutor``.

    Concrete subclasses (``GroeikernModeConfig``, ``JeugdzorgModeConfig``)
    take their dependencies (paths, settings) in ``__init__`` and expose
    them via the abstract surface below. The executor never branches on the
    mode name — it just calls the same methods on whichever instance the
    factory returned.
    """

    @property
    @abstractmethod
    def mode(self) -> Mode:
        """Mode literal this config represents (``"groeikern"`` or ``"jeugdzorg"``)."""

    @property
    @abstractmethod
    def working_dir(self) -> Path:
        """Filesystem root the ohrs process runs from.

        Contains the per-mode skills/, plugin manifest, and any reference
        data (CSVs for jeugdzorg, Capelle_enquetes/ for groeikern).
        """

    @abstractmethod
    def build_system_prompt(self, output_path: str) -> str:
        """Return the full system prompt fed to ohrs via ``--system-prompt``.

        ``output_path`` is the absolute path inside the per-job directory the
        agent must write its final ``AnalysisResult`` JSON to. Implementations
        interpolate it into the prompt so the agent has the exact target.
        """

    @abstractmethod
    def required_skill_files(self, skills_link: Path) -> list[Path]:
        """Paths that must exist after plugin materialisation, or preflight fails.

        ``skills_link`` is the symlink the executor sets up under the per-job
        ``.openharnessrs/plugins/capelle/skills`` directory; implementations
        return a list of files inside it.
        """

    @abstractmethod
    def workspace_symlinks(self) -> dict[str, Path]:
        """Per-job symlinks the executor materialises before launch.

        Maps a relative name (e.g., ``"Capelle_enquetes"``) to the absolute
        target the link should point at. The executor creates each link under
        the per-job directory so the agent sees the data via short paths.
        """

    @abstractmethod
    def extra_env(self) -> dict[str, str]:
        """Per-mode env vars merged into the ohrs subprocess environment.

        Used for mode-specific configuration the CLI tools need at runtime
        (e.g., ``JEUGDZORG_DATA`` pointing at the canonical CSV directory).
        Keys overwrite any incidental value in the parent environment.
        """

    @abstractmethod
    def path_additions(self) -> list[Path]:
        """Directories prepended to ``PATH`` for the ohrs subprocess.

        Lets each mode declare where its CLI binary lives without forcing
        the deployment env to enumerate every venv bin. Returned in priority
        order: the first entry wins on conflict.
        """

    @property
    @abstractmethod
    def job_timeout_seconds(self) -> int:
        """Max wall-clock seconds for one ohrs job in this mode."""

    @property
    @abstractmethod
    def max_turns(self) -> int:
        """Max agent turns (``--max-turns``) for this mode."""

    @property
    @abstractmethod
    def elicitation_timeout_seconds(self) -> int:
        """Seconds to wait for a user's mid-run answer in this mode."""
