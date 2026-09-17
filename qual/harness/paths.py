"""Locations and toolchain resolution.

The harness never imports ``align``. The flow is invoked as a subprocess so
that an abort in the compiled PnR extension cannot take down the pytest
session, and so the harness can run under a different interpreter than the one
ALIGN is installed into.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

QUAL_ROOT = Path(__file__).resolve().parents[1]
CASES_DIR = QUAL_ROOT / "cases"
BASELINES_DIR = QUAL_ROOT / "baselines"

# The suite lives inside the checkout it qualifies, so the enclosing directory
# is the flow under test by default. Point --align-home elsewhere to benchmark
# a second checkout against the same cases and baselines.
DEFAULT_ALIGN_HOME = QUAL_ROOT.parent
DEFAULT_ARTIFACTS_ROOT = QUAL_ROOT / "artifacts"


class ToolchainError(RuntimeError):
    """The flow under test is missing or unusable."""


@dataclass(frozen=True)
class AlignInstall:
    """A resolved ALIGN checkout plus the interpreter that can import it."""

    home: Path
    python: str

    @property
    def entry_point(self) -> Path:
        return self.home / "bin" / "schematic2layout.py"

    @property
    def pdk_root(self) -> Path:
        return self.home / "pdks"

    def pdk(self, name: str) -> Path:
        path = self.pdk_root / name
        if not path.is_dir():
            available = sorted(p.name for p in self.pdk_root.iterdir() if p.is_dir())
            raise ToolchainError(f"unknown PDK {name!r}; available: {available}")
        return path

    def available_pdks(self) -> list[str]:
        if not self.pdk_root.is_dir():
            return []
        return sorted(p.name for p in self.pdk_root.iterdir() if p.is_dir())

    def revision(self) -> str:
        """Commit of the flow under test, recorded with every result."""
        try:
            out = subprocess.run(
                ["git", "-C", str(self.home), "rev-parse", "HEAD"],
                capture_output=True, text=True, timeout=10, check=True,
            )
        except (subprocess.SubprocessError, OSError):
            return "unknown"
        return out.stdout.strip()[:12] or "unknown"

    def is_dirty(self) -> bool:
        """Uncommitted changes make a recorded revision non-reproducible."""
        try:
            out = subprocess.run(
                ["git", "-C", str(self.home), "status", "--porcelain"],
                capture_output=True, text=True, timeout=10, check=True,
            )
        except (subprocess.SubprocessError, OSError):
            return False
        return bool(out.stdout.strip())

    def native_lib_dirs(self) -> list[Path]:
        """Directories that contain ALIGN's bundled native libraries (lpsolve, etc.).

        The PnR extension is linked against a bare ``liblpsolve55.5.dylib``
        install name, so the process needs those directories on
        DYLD_LIBRARY_PATH / LD_LIBRARY_PATH regardless of cwd.
        """
        names = ("liblpsolve55.5.dylib", "liblpsolve55.dylib", "liblpsolve55.so")
        found: list[Path] = []
        candidates = [
            self.home / "align" / "thirdparty",
            *sorted(self.home.glob("_skbuild/*/cmake-install/align/thirdparty")),
            *sorted(self.home.glob("_skbuild/*/cmake-build/_deps/lpsolve-build")),
        ]
        for directory in candidates:
            if directory in found or not directory.is_dir():
                continue
            if any((directory / name).is_file() for name in names):
                found.append(directory)
        return found

    def process_env(self) -> dict[str, str]:
        """Environment for subprocesses that import ``align``."""
        env = os.environ.copy()
        dirs = [str(path) for path in self.native_lib_dirs()]
        if not dirs:
            return env
        for key in ("DYLD_LIBRARY_PATH", "DYLD_FALLBACK_LIBRARY_PATH", "LD_LIBRARY_PATH"):
            existing = env.get(key, "")
            env[key] = os.pathsep.join(dirs + ([existing] if existing else []))
        return env

    def check_importable(self) -> None:
        probe = subprocess.run(
            [self.python, "-c", "import align; print(align.__version__)"],
            capture_output=True, text=True, timeout=120, env=self.process_env(),
        )
        if probe.returncode != 0:
            raise ToolchainError(
                f"{self.python} cannot import align:\n{probe.stderr.strip()}\n"
                f"Install it with: {self.python} -m pip install -e {self.home}"
            )

    @classmethod
    def resolve(cls, home: str | os.PathLike | None = None, python: str | None = None) -> AlignInstall:
        """Resolve from an explicit path, then $ALIGN_HOME, then the enclosing checkout."""
        candidate = home or os.environ.get("ALIGN_HOME") or DEFAULT_ALIGN_HOME
        resolved = Path(candidate).expanduser().resolve()
        if not resolved.is_dir():
            raise ToolchainError(f"ALIGN checkout not found at {resolved}; pass --align-home")

        interpreter = python or os.environ.get("ALIGN_PYTHON")
        if not interpreter:
            venv_python = resolved / "general" / "bin" / "python"
            interpreter = str(venv_python) if venv_python.is_file() else sys.executable
        install = cls(home=resolved, python=interpreter)
        if not install.entry_point.is_file():
            raise ToolchainError(f"{resolved} does not look like an ALIGN checkout ({install.entry_point} missing)")
        return install


def tool_on_path(name: str) -> bool:
    """Used by the requires_drc / requires_lvs markers to skip cleanly."""
    return shutil.which(name) is not None
