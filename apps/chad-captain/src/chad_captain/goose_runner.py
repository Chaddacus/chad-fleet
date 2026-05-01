"""Per-app goose-runner daemon.

One process per tracked app. Watches `current_slice.json` for instructions,
invokes `goose run --no-session` with the captain's sandboxed goose-runtime,
streams progress to `progress.jsonl`, and writes `slice_complete.json` on
exit. Loops forever — launchctl handles restart on crash.

Captain owns the protocol; this module just executes.

Invocation:
    chad-captain goose-runner --app <id> --repo <path> [--goose-runtime <dir>]
                              [--poll-interval 2.0]
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import shlex
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import IO

from chad_captain.protocol import (
    AppWorkspace,
    CurrentSlice,
    ProgressEvent,
    SliceComplete,
    append_progress,
    clear_current_slice,
    read_current_slice,
    write_slice_complete,
)

logger = logging.getLogger(__name__)

# Cheat-detection patterns (mirrors ~/.claude/bin/goose_dispatch.py).
# Captain-side validator looks at these flags to decide reject/escalate.
CHEAT_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (
        "bare-except-swallow",
        re.compile(r"except\b[^:]*:\s*\n\s+(pass|continue|print\s*\()", re.MULTILINE),
    ),
    ("pytest-skip-added", re.compile(r"^\s*@pytest\.mark\.skip\b", re.MULTILINE)),
    ("assert-true-only", re.compile(r"^\s*assert\s+(True|1)\s*(#.*)?$", re.MULTILINE)),
]

GOOSE_BIN_DEFAULT = "/opt/homebrew/bin/goose"
LOG_DIR = Path.home() / ".chad" / "fleet" / "logs" / "goose-runner"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Slice execution
# ---------------------------------------------------------------------------


class GooseRunner:
    """Executes slices for one tracked app, in a loop."""

    def __init__(
        self,
        app_id: str,
        repo_path: Path,
        *,
        goose_runtime: Path,
        goose_bin: str = GOOSE_BIN_DEFAULT,
        workspace_base: Path | None = None,
        poll_interval: float = 2.0,
        log_dir: Path | None = None,
    ) -> None:
        self.app_id = app_id
        self.repo_path = repo_path.resolve()
        self.goose_runtime = goose_runtime.resolve()
        self.goose_bin = goose_bin
        self.poll_interval = poll_interval
        self.log_dir = (log_dir or LOG_DIR) / app_id
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.ws = AppWorkspace(app_id, base=workspace_base)
        self.ws.ensure()

    # ---- main loop ----

    def run_forever(self, max_iters: int | None = None) -> None:
        """Block forever, executing slices as captain queues them.

        max_iters caps loop iterations (used in tests). Default = unbounded.
        """
        i = 0
        while max_iters is None or i < max_iters:
            i += 1
            try:
                self.tick()
            except Exception as exc:  # noqa: BLE001
                logger.exception("[%s] runner tick crashed: %s", self.app_id, exc)
                # Don't let a tick exception kill the daemon — log and recover.
            time.sleep(self.poll_interval)

    def tick(self) -> bool:
        """Single iteration. Returns True if a slice was executed."""
        # If captain hasn't consumed the previous completion yet, wait.
        if self.ws.slice_complete_path.exists():
            return False

        current = read_current_slice(self.ws)
        if current is None:
            return False

        # Avoid double-executing the same slice id (captain didn't write a new one).
        if current.started_at is not None:
            return False

        self._execute_slice(current)
        return True

    # ---- slice execution ----

    def _execute_slice(self, slice_: CurrentSlice) -> None:
        slice_log = self.log_dir / f"{slice_.slice_id}.log"
        started_at = _now_iso()
        wall_start = time.time()

        # Mark started in current_slice (captain reads this for "in flight" status).
        slice_.started_at = started_at
        from chad_captain.protocol import write_current_slice  # avoid top cycle in tests
        write_current_slice(self.ws, slice_)

        append_progress(
            self.ws,
            ProgressEvent(
                slice_id=slice_.slice_id,
                kind="slice_started",
                detail={"objective": slice_.objective, "log": str(slice_log)},
            ),
        )

        # CRASH-RESILIENCE: from this point onward, ALWAYS write a SliceComplete
        # before returning (success OR exception). Without this, an unhandled
        # exception during goose run / git inspection / summary extraction
        # leaves current_slice with started_at set + no slice_complete on disk;
        # tick() then skips the slice forever via the "started_at is not None"
        # guard, requiring captain's stall watchdog to recover (~35min wait).
        try:
            self._execute_slice_inner(slice_, slice_log, wall_start)
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "[%s] _execute_slice crashed for %s — emitting synthetic "
                "SliceComplete(-9) so captain can recover",
                self.app_id, slice_.slice_id,
            )
            try:
                duration = time.time() - wall_start
                tail = f"goose-runner crashed: {type(exc).__name__}: {exc}"[-2048:]
                write_slice_complete(
                    self.ws,
                    SliceComplete(
                        slice_id=slice_.slice_id,
                        app_id=self.app_id,
                        duration_seconds=duration,
                        goose_exit_code=-9,
                        summary=f"runner crash: {type(exc).__name__}",
                        files_changed=[],
                        diff_path=None,
                        log_path=str(slice_log) if slice_log.exists() else None,
                        failure_tail=tail,
                        cheat_flags=[],
                    ),
                )
                clear_current_slice(self.ws)
            except Exception:  # noqa: BLE001
                logger.exception(
                    "[%s] failed to write recovery SliceComplete; captain "
                    "stall watchdog will recover instead", self.app_id,
                )

    def _execute_slice_inner(
        self,
        slice_: CurrentSlice,
        slice_log: Path,
        wall_start: float,
    ) -> None:
        """The actual slice-execution body. Wrapped by _execute_slice's
        try/except so any failure path still emits a SliceComplete."""
        # Snapshot pre-state for diff/files-changed detection.
        pre_commit = _git_head(self.repo_path)
        pre_dirty = _git_dirty_files(self.repo_path)

        env = os.environ.copy()
        env["XDG_CONFIG_HOME"] = str(self.goose_runtime / "config")
        env["XDG_STATE_HOME"] = str(self.goose_runtime / "state")
        env["XDG_DATA_HOME"] = str(self.goose_runtime / "data")
        # Goose's OpenTelemetry emitter panics noisily; suppress unless debug.
        env.setdefault("OTEL_TRACES_EXPORTER", "none")
        env.setdefault("OTEL_METRICS_EXPORTER", "none")
        env.setdefault("OTEL_LOGS_EXPORTER", "none")

        cmd = [
            self.goose_bin,
            "run",
            "--no-session",
            "--max-turns",
            str(slice_.max_turns),
            "--max-tool-repetitions",
            str(slice_.max_tool_repetitions),
            "--system",
            slice_.system_prompt,
            "--text",
            slice_.user_prompt,
        ]

        exit_code, failure_tail = self._run_goose(cmd, env, slice_log, slice_.timeout_seconds, slice_.slice_id)
        duration = time.time() - wall_start

        # Post-state.
        post_commit = _git_head(self.repo_path)
        files_changed = _files_changed(self.repo_path, pre_commit, post_commit, pre_dirty)
        cheat_flags = _scan_cheats(self.repo_path, files_changed)
        diff_path = self._capture_diff(slice_, pre_commit, post_commit, files_changed)
        summary = _extract_summary(slice_log)

        # Auto-commit any uncommitted changes so the *next* slice starts from
        # a clean working tree. Without this, slice N+1's `pre_dirty` set
        # already contains slice N's mutations and `_files_changed` reports
        # an empty diff, which the validator misreads as "no files changed".
        if files_changed and post_commit == pre_commit:
            _git_autocommit(self.repo_path, slice_.slice_id)

        append_progress(
            self.ws,
            ProgressEvent(
                slice_id=slice_.slice_id,
                kind="slice_completing",
                detail={
                    "exit_code": exit_code,
                    "duration_seconds": duration,
                    "files_changed": files_changed,
                    "cheat_flags": cheat_flags,
                },
            ),
        )

        complete = SliceComplete(
            slice_id=slice_.slice_id,
            app_id=self.app_id,
            duration_seconds=duration,
            goose_exit_code=exit_code,
            summary=summary,
            files_changed=files_changed,
            diff_path=str(diff_path) if diff_path else None,
            log_path=str(slice_log),
            failure_tail=failure_tail,
            cheat_flags=cheat_flags,
        )
        write_slice_complete(self.ws, complete)

        # Captain consumes current_slice + writes the next one. We just clear ours
        # so the runner doesn't re-execute the same slice on next tick.
        clear_current_slice(self.ws)

    def _run_goose(
        self,
        cmd: list[str],
        env: dict[str, str],
        log_file: Path,
        timeout: int,
        slice_id: str,
    ) -> tuple[int, str | None]:
        """Spawn goose, stream stdout to log_file + progress.jsonl, return (exit, tail)."""
        with log_file.open("w", encoding="utf-8") as lf:
            lf.write(f"===== GOOSE INVOCATION @ {_now_iso()} =====\n")
            lf.write(f"CMD: {shlex.join(cmd[:-2])} --system <...> --text <...>\n")
            lf.write(f"CWD: {self.repo_path}\n")
            lf.flush()

            try:
                proc = subprocess.Popen(  # noqa: S603 — trusted local cmd
                    cmd,
                    cwd=self.repo_path,
                    env=env,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                )
            except FileNotFoundError as exc:
                lf.write(f"GOOSE BIN NOT FOUND: {exc}\n")
                return 127, str(exc)[-2048:]

            # Stream stdout lines → log + progress.jsonl heartbeats.
            stream_thread = threading.Thread(
                target=self._tail_subprocess,
                args=(proc.stdout, lf, slice_id),
                daemon=True,
            )
            stream_thread.start()

            try:
                exit_code = proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
                exit_code = -9
                lf.write(f"\nTIMEOUT after {timeout}s — process killed.\n")

            stream_thread.join(timeout=5)

        tail = _tail_bytes(log_file, 2048) if exit_code != 0 else None
        return exit_code, tail

    def _tail_subprocess(self, stream: IO[str] | None, log: IO[str], slice_id: str) -> None:
        """Pump goose stdout into the log file + emit periodic heartbeats."""
        if stream is None:
            return
        last_heartbeat = time.time()
        for line in stream:
            log.write(line)
            log.flush()
            now = time.time()
            # Detect tool calls in goose's verbose output. Cheap signal — captain
            # tails progress.jsonl to know goose is making real progress.
            if "tool" in line.lower() and ("call" in line.lower() or "result" in line.lower()):
                try:
                    append_progress(
                        self.ws,
                        ProgressEvent(
                            slice_id=slice_id,
                            kind="tool_call",
                            detail={"line": line.rstrip()[:240]},
                        ),
                    )
                    last_heartbeat = now
                    continue
                except Exception:
                    pass
            # Heartbeat every 30s of activity so captain can detect stalls.
            if now - last_heartbeat > 30:
                try:
                    append_progress(
                        self.ws,
                        ProgressEvent(slice_id=slice_id, kind="heartbeat", detail={}),
                    )
                    last_heartbeat = now
                except Exception:
                    pass

    def _capture_diff(
        self,
        slice_: CurrentSlice,
        pre_commit: str | None,
        post_commit: str | None,
        files_changed: list[str],
    ) -> Path | None:
        if not files_changed:
            return None
        diff_path = self.log_dir / f"{slice_.slice_id}.diff"
        if pre_commit and post_commit and pre_commit != post_commit:
            ok, out = _run_capture(["git", "-C", str(self.repo_path), "diff", pre_commit, post_commit])
        else:
            # Fall back to `git diff` of working tree (slice didn't commit).
            ok, out = _run_capture(["git", "-C", str(self.repo_path), "diff"])
        if ok:
            diff_path.write_text(out, encoding="utf-8")
            return diff_path
        return None


# ---------------------------------------------------------------------------
# Git helpers
# ---------------------------------------------------------------------------


def _run_capture(cmd: list[str], timeout: int = 30) -> tuple[bool, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)  # noqa: S603
        return p.returncode == 0, p.stdout
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False, ""


def _git_head(repo: Path) -> str | None:
    ok, out = _run_capture(["git", "-C", str(repo), "rev-parse", "HEAD"])
    return out.strip() if ok else None


def _git_dirty_files(repo: Path) -> set[str]:
    ok, out = _run_capture(["git", "-C", str(repo), "status", "--porcelain"])
    if not ok:
        return set()
    files: set[str] = set()
    for line in out.splitlines():
        if len(line) > 3:
            files.add(line[3:].strip())
    return files


def _git_autocommit(repo: Path, slice_id: str) -> None:
    """Stage + commit all uncommitted changes under a captain-runner author.

    No-op if the repo isn't a git checkout, has nothing staged, or commit
    fails for any reason — slice success doesn't depend on this.
    """
    if not (repo / ".git").exists():
        return
    _run_capture(["git", "-C", str(repo), "add", "-A"])
    _run_capture([
        "git", "-C", str(repo),
        "-c", "user.email=captain-runner@local",
        "-c", "user.name=captain-runner",
        "commit", "-qm", f"captain-runner: {slice_id}",
    ])


def _files_changed(
    repo: Path,
    pre_commit: str | None,
    post_commit: str | None,
    pre_dirty: set[str],
) -> list[str]:
    """Detect files modified during the slice (ignoring pre-existing dirty)."""
    if pre_commit and post_commit and pre_commit != post_commit:
        ok, out = _run_capture(["git", "-C", str(repo), "diff", "--name-only", pre_commit, post_commit])
        if ok:
            return [f for f in out.splitlines() if f.strip()]

    # Working-tree dirty (slice didn't commit).
    post_dirty = _git_dirty_files(repo)
    new_dirty = sorted(post_dirty - pre_dirty)
    return new_dirty


def _scan_cheats(repo: Path, files_changed: list[str]) -> list[str]:
    """Look for high-confidence test-cheat patterns in newly-modified test files."""
    flags: list[str] = []
    for rel in files_changed:
        if not (rel.startswith("tests/") or rel.startswith("test_") or "/test_" in rel):
            continue
        path = repo / rel
        if not path.exists():
            continue
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for name, pattern in CHEAT_PATTERNS:
            if pattern.search(content):
                flag = f"{name}:{rel}"
                if flag not in flags:
                    flags.append(flag)
    return flags


def _extract_summary(log_file: Path) -> str:
    """Goose typically ends with a final assistant message that is a summary.

    Heuristic: grab the last 600 chars of the log, strip ANSI, return as
    summary. Captain can re-summarize if needed.
    """
    if not log_file.exists():
        return ""
    text = log_file.read_text(encoding="utf-8", errors="replace")
    text = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", text)  # strip ANSI
    return text[-600:].strip()


def _tail_bytes(path: Path, n: int) -> str:
    if not path.exists():
        return ""
    data = path.read_bytes()
    return data[-n:].decode("utf-8", errors="replace")


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="chad-captain goose-runner", description=__doc__)
    parser.add_argument("--app", required=True, help="Tracked app id (e.g. spark-of-defiance)")
    parser.add_argument("--repo", required=True, help="Repo path goose runs in")
    parser.add_argument(
        "--goose-runtime",
        default=str(Path(__file__).resolve().parents[2] / "goose-runtime"),
        help="Captain's goose-runtime dir (XDG_CONFIG/STATE/DATA root)",
    )
    parser.add_argument("--goose-bin", default=GOOSE_BIN_DEFAULT)
    parser.add_argument("--poll-interval", type=float, default=2.0)
    parser.add_argument("--workspace-base", default=None, help="Override fleet apps base dir")
    parser.add_argument("--max-iters", type=int, default=None, help="Cap loop iterations (test mode)")

    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s [%(name)s] %(message)s")

    runner = GooseRunner(
        app_id=args.app,
        repo_path=Path(args.repo),
        goose_runtime=Path(args.goose_runtime),
        goose_bin=args.goose_bin,
        workspace_base=Path(args.workspace_base) if args.workspace_base else None,
        poll_interval=args.poll_interval,
    )

    if shutil.which(args.goose_bin) is None and not Path(args.goose_bin).exists():
        logger.warning("goose binary not found at %s — runner will report 127 per slice", args.goose_bin)

    logger.info("[%s] goose-runner up. repo=%s runtime=%s", args.app, runner.repo_path, runner.goose_runtime)
    runner.run_forever(max_iters=args.max_iters)
    return 0


if __name__ == "__main__":
    sys.exit(main())
