#!/usr/bin/env python3
"""Per-client Normal/Racing mode controller; preview-only unless explicitly enabled."""
from __future__ import annotations

import copy
import math
import os
import pathlib
import tempfile
import threading
import time
from argparse import Namespace
from typing import Any

import yaml

import manager
import racing

MB = 1_000_000


class ModeTracker:
    """Switch modes only after a continuous high/low speed streak."""
    def __init__(self, threshold_bytes: float, sustain_seconds: float,
                 initial_mode: str | None = "normal"):
        self.threshold = float(threshold_bytes)
        self.sustain = float(sustain_seconds)
        self.mode = initial_mode
        self.high_since = None
        self.low_since = None

    def sample(self, speed: float, now: float):
        if not math.isfinite(speed) or speed < 0:
            self.high_since = None
            self.low_since = None
            return None
        # After an API outage, require a fresh uninterrupted five-second
        # speed streak before allowing either cleanup strategy to run.
        if self.mode is None:
            if speed >= self.threshold:
                self.low_since = None
                if self.high_since is None:
                    self.high_since = now
                elif now - self.high_since >= self.sustain:
                    self.mode = "racing"
                    self.high_since = None
                    return self.mode
            else:
                self.high_since = None
                if self.low_since is None:
                    self.low_since = now
                elif now - self.low_since >= self.sustain:
                    self.mode = "normal"
                    self.low_since = None
                    return self.mode
            return None
        if self.mode == "normal":
            self.low_since = None
            if speed >= self.threshold:
                if self.high_since is None:
                    self.high_since = now
                elif now - self.high_since >= self.sustain:
                    self.mode = "racing"
                    self.high_since = None
                    return self.mode
            else:
                self.high_since = None
        else:
            self.high_since = None
            if speed < self.threshold:
                if self.low_since is None:
                    self.low_since = now
                elif now - self.low_since >= self.sustain:
                    self.mode = "normal"
                    self.low_since = None
                    return self.mode
            else:
                self.low_since = None
        return None


def _normal_removal_allowed(task: dict[str, Any], hybrid: dict[str, Any],
                           settings: dict[str, Any]) -> bool:
    """Require every global and Hybrid safeguard before Normal removal."""
    global_safe = (settings.get("allow_removal") is True
                   and settings.get("preview_only", True) is False)
    hybrid_safe = (hybrid.get("allow_removal") is True
                   and hybrid.get("preview_only", True) is False)
    delete_data = task.get("delete_data", False)
    if type(delete_data) is not bool:
        return False
    if delete_data:
        data_safe = (settings.get("allow_data_deletion") is True
                     and hybrid.get("allow_data_deletion") is True)
    else:
        data_safe = True
    return global_safe and hybrid_safe and data_safe


def _normal_cleanup(name: str, task: dict[str, Any], args: Namespace,
                    hybrid: dict[str, Any], settings: dict[str, Any],
                    logger, stop_event=None) -> None:
    """Invoke the unchanged upstream YAML strategy for this task only."""
    if stop_event is not None and stop_event.is_set():
        logger.info("%s: Normal cleanup cancelled before lock acquisition.", name)
        return
    removal = _normal_removal_allowed(task, hybrid, settings)
    if task.get("delete_data", False) is True and not (
            settings.get("allow_data_deletion") is True
            and hybrid.get("allow_data_deletion") is True
    ):
        logger.warning("%s: payload deletion not authorized; Normal cleanup is preview-only.", name)
    lock = manager.acquire_lock(args.state_dir)
    if lock is None:
        logger.info("%s: shared run lock busy; Normal cycle skipped.", name)
        return
    temporary = None
    try:
        if stop_event is not None and stop_event.is_set():
            logger.info("%s: Normal cleanup cancelled before starting upstream.", name)
            return
        fd, temp_name = tempfile.mkstemp(prefix=".hybrid-task-", suffix=".yml",
                                         dir=str(args.state_dir), text=True)
        temporary = pathlib.Path(temp_name)
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            yaml.safe_dump({name: copy.deepcopy(task)}, stream, sort_keys=False)
            stream.flush()
            os.fsync(stream.fileno())
        child_args = Namespace(upstream_config=temporary, log_dir=args.log_dir)
        logger.info("%s: Normal strategy run (%s).", name, "removal" if removal else "preview")
        result = manager.invoke_upstream(
            child_args, preview=not removal, stop_event=stop_event
        )
        if result == 130:
            logger.info("%s: Normal cleanup stopped on request.", name)
            return
        if result:
            logger.error("%s: upstream Normal cleanup exited with status %s.", name, result)
    except Exception as exc:
        logger.error("%s: Normal cleanup failed closed (%s).", name, type(exc).__name__)
    finally:
        if temporary is not None:
            try:
                temporary.unlink()
            except OSError:
                logger.error("%s: temporary task configuration cleanup failed.", name)
        try:
            lock.close()
        except Exception:
            logger.exception("%s: failed to release Normal cleanup lock.", name)


def _racing_cleanup(name: str, api: racing.ClientAPI, config: dict[str, Any],
                    settings: dict[str, Any], state_dir: pathlib.Path, logger,
                    stop_event=None) -> None:
    """Evaluate Racing candidates under the same lock used by Normal cleanup."""
    if stop_event is not None and stop_event.is_set():
        logger.info("%s: Racing cleanup cancelled before lock acquisition.", name)
        return
    lock = manager.acquire_lock(state_dir)
    if lock is None:
        logger.info("%s: shared run lock busy; Racing cycle skipped.", name)
        return
    try:
        if stop_event is not None and stop_event.is_set():
            logger.info("%s: Racing cleanup cancelled after lock acquisition.", name)
            return
        ratio_min = float(config.get("ratio_min", 1.0))
        upload_max = float(config.get("upload_max_mb", 10.0)) * MB
        candidates = racing.eligible_torrents(api.client, api.torrents(), ratio_min, upload_max)
        if not candidates:
            logger.info("%s: no Racing-eligible seeding torrents.", name)
            return
        for item in candidates:
            upload = item["upspeed"] if api.client == "qbittorrent" else item["upload_payload_rate"]
            logger.info("%s: Racing candidate name=%r ratio=%.3f upload=%.3f MB/s",
                        name, str(item.get("name", ""))[:120],
                        float(item["ratio"]), float(upload) / MB)
        deletion = (
            settings.get("preview_only", True) is False
            and settings.get("allow_removal") is True
            and settings.get("allow_data_deletion") is True
            and config.get("preview_only", True) is False
            and config.get("allow_removal") is True
            and config.get("allow_data_deletion") is True
        )
        if not deletion:
            logger.warning("%s: Racing preview only; no torrent data was changed.", name)
            return
        # Re-read candidate state immediately before any removal.
        if stop_event is not None and stop_event.is_set():
            logger.info("%s: Racing removal cancelled before final candidate check.", name)
            return
        preview_hashes = {
            str(item["hash"]).lower() for item in candidates if item.get("hash")
        }
        fresh = racing.eligible_torrents(api.client, api.torrents(), ratio_min, upload_max)
        # Revalidation may revoke a preview candidate, never add a new one.
        hashes = list(dict.fromkeys(
            str(item["hash"]) for item in fresh
            if item.get("hash") and str(item["hash"]).lower() in preview_hashes
        ))
        if not hashes:
            logger.info("%s: candidates no longer qualify; nothing removed.", name)
            return
        if stop_event is not None and stop_event.is_set():
            logger.info("%s: Racing removal cancelled before API deletion.", name)
            return
        removed, failed = api.remove_with_data(hashes, stop_event=stop_event)
        logger.warning("%s: Racing removal result: %d removed, %d failed.",
                       name, len(removed), len(failed))
    finally:
        lock.close()


def run_hybrid(tasks: dict[str, Any], config: dict[str, Any],
                args: Namespace, settings: dict[str, Any],
                stop_event=None) -> int:
    logger = racing.make_logger(args.log_dir)
    try:
        threshold = float(config.get("download_threshold_mb", 100.0)) * MB
        sustain = float(config.get("sustain_seconds", 5.0))
        poll = float(config.get("poll_interval_seconds", 1.0))
        interval = float(config.get("cleanup_interval_seconds", 60.0))
        ratio = float(config.get("ratio_min", 1.0))
        upload = float(config.get("upload_max_mb", 10.0))
        values = (threshold, sustain, poll, interval, ratio, upload)
        if (not all(math.isfinite(value) for value in values)
                or threshold <= 0 or sustain <= 0 or poll <= 0
                or interval < 1 or ratio < 1 or upload < 0):
            raise ValueError("invalid Hybrid thresholds")
    except (TypeError, ValueError, OverflowError):
        logger.error("Invalid Hybrid settings; controller not started.")
        return 2

    specs = racing._client_specs(tasks)
    if not specs:
        logger.error("Hybrid requires at least one configured qBittorrent or Deluge task.")
        return 2

    stop = stop_event if stop_event is not None else threading.Event()

    def close_api_safely(name, api):
        if api is not None:
            try:
                api.close()
            except Exception as exc:
                logger.error("%s: API close failed (%s).", name, type(exc).__name__)

    def worker(name: str, task: dict[str, Any]) -> None:
        tracker = ModeTracker(threshold, sustain)
        api = None
        next_cleanup = 0.0
        logger.info("%s (%s): Hybrid worker started in Normal mode; preview_only=%s.",
                    name, task.get("client"), config.get("preview_only", True) is True)
        try:
            while not stop.is_set():
                try:
                    if api is None:
                        if stop.is_set():
                            break
                        api = racing.ClientAPI(name, task)
                        api.connect()
                        logger.info("%s: API authenticated; monitoring enabled.", name)

                    speed = api.download_speed()
                    changed = tracker.sample(speed, time.monotonic())

                    if changed:
                        logger.warning("%s: switched to %s mode after sustained speed threshold.",
                                       name, changed.upper())
                        next_cleanup = 0.0

                    now = time.monotonic()
                    if stop.is_set():
                        break

                    if tracker.mode is not None and now >= next_cleanup:
                        if stop.is_set():
                            break
                        if tracker.mode == "normal":
                            _normal_cleanup(
                                name, task, args, config, settings, logger,
                                stop_event=stop
                            )
                        else:
                            _racing_cleanup(
                                name, api, config, settings, args.state_dir, logger,
                                stop_event=stop
                            )
                        next_cleanup = time.monotonic() + interval

                except Exception as exc:
                    logger.error(
                        "%s: API/worker error (%s); cleanup suspended and streak reset.",
                        name, type(exc).__name__
                    )
                    tracker = ModeTracker(threshold, sustain, initial_mode=None)
                    next_cleanup = float("inf")
                    close_api_safely(name, api)
                    api = None
                    if stop.wait(max(poll, 2.0)):
                        break
                    continue

                if stop.wait(poll):
                    break
        finally:
            close_api_safely(name, api)
            logger.info("%s: Hybrid worker stopped.", name)

    workers = [
        threading.Thread(
            target=worker,
            args=(name, task),
            name="hybrid-" + name,
            daemon=False,
        )
        for name, task in specs
    ]

    started = []
    try:
        for thread in workers:
            thread.start()
            started.append(thread)

        logger.info("Hybrid controller started with %d independent client worker(s).",
                    len(workers))

        while any(thread.is_alive() for thread in started):
            for thread in started:
                thread.join(timeout=0.5)

    except KeyboardInterrupt:
        logger.info("Stop requested; signalling all Hybrid workers.")
        stop.set()

    finally:
        stop.set()
        # Do not return while a non-daemon worker still owns an API session
        # or the shared run lock. Normal child processes observe this event.
        for thread in started:
            while thread.is_alive():
                try:
                    thread.join(timeout=0.5)
                except KeyboardInterrupt:
                    stop.set()
                    continue

    logger.info("Hybrid controller stopped; all workers have exited.")
    return 0
