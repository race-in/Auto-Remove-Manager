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


def _normal_cleanup(name: str, task: dict[str, Any], args: Namespace,
                    hybrid: dict[str, Any], settings: dict[str, Any],
                    logger) -> None:
    """Invoke the unchanged upstream YAML strategy for this task only."""
    global_safe = bool(settings.get("allow_removal", False)) and not bool(settings.get("preview_only", True))
    hybrid_safe = not bool(hybrid.get("preview_only", True))
    data_safe = (not bool(task.get("delete_data", False))
                 or bool(settings.get("allow_data_deletion", False)))
    removal = global_safe and hybrid_safe and data_safe
    if bool(task.get("delete_data", False)) and not bool(settings.get("allow_data_deletion", False)):
        logger.warning("%s: payload deletion not authorized; Normal cleanup is preview-only.", name)
    lock = manager.acquire_lock(args.state_dir)
    if lock is None:
        logger.info("%s: shared run lock busy; Normal cycle skipped.", name)
        return
    temporary = None
    try:
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
        result = manager.invoke_upstream(child_args, preview=not removal)
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
        lock.close()


def _racing_cleanup(name: str, api: racing.ClientAPI, config: dict[str, Any],
                    state_dir: pathlib.Path, logger) -> None:
    """Evaluate Racing candidates under the same lock used by Normal cleanup."""
    lock = manager.acquire_lock(state_dir)
    if lock is None:
        logger.info("%s: shared run lock busy; Racing cycle skipped.", name)
        return
    try:
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
            not bool(config.get("preview_only", True))
            and bool(config.get("allow_removal", False))
            and bool(config.get("allow_data_deletion", False))
        )
        if not deletion:
            logger.warning("%s: Racing preview only; no torrent data was changed.", name)
            return
        # Re-read candidate state immediately before any removal.
        fresh = racing.eligible_torrents(api.client, api.torrents(), ratio_min, upload_max)
        hashes = [str(item["hash"]) for item in fresh if item.get("hash")]
        if not hashes:
            logger.info("%s: candidates no longer qualify; nothing removed.", name)
            return
        removed, failed = api.remove_with_data(hashes)
        logger.warning("%s: Racing removal result: %d removed, %d failed.",
                       name, len(removed), len(failed))
    finally:
        lock.close()


def run_hybrid(tasks: dict[str, Any], config: dict[str, Any],
               args: Namespace, settings: dict[str, Any]) -> int:
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
    stop = threading.Event()

    def worker(name: str, task: dict[str, Any]) -> None:
        tracker = ModeTracker(threshold, sustain)
        api = None
        next_cleanup = 0.0
        logger.info("%s (%s): Hybrid worker started in Normal mode; preview_only=%s.",
                    name, task.get("client"), bool(config.get("preview_only", True)))
        while not stop.is_set():
            try:
                if api is None:
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
                if tracker.mode is not None and now >= next_cleanup:
                    if tracker.mode == "normal":
                        _normal_cleanup(name, task, args, config, settings, logger)
                    else:
                        _racing_cleanup(name, api, config, args.state_dir, logger)
                    next_cleanup = time.monotonic() + interval
            except Exception as exc:
                logger.error("%s: API/worker error (%s); cleanup suspended and streak reset.",
                             name, type(exc).__name__)
                tracker = ModeTracker(threshold, sustain, initial_mode=None)
                # Never run Normal immediately after reconnect: wait for a
                # fresh, continuous five-second classification first.
                next_cleanup = float("inf")
                if api is not None:
                    api.close()
                    api = None
                stop.wait(max(poll, 2.0))
                continue
            stop.wait(poll)
        if api is not None:
            api.close()

    workers = [
        threading.Thread(target=worker, args=(name, task), name="hybrid-" + name, daemon=True)
        for name, task in specs
    ]
    try:
        for thread in workers:
            thread.start()
        logger.info("Hybrid controller started with %d independent client worker(s).", len(workers))
        while any(thread.is_alive() for thread in workers):
            for thread in workers:
                thread.join(timeout=0.5)
    except KeyboardInterrupt:
        logger.info("Stop requested; waiting for workers.")
        stop.set()
        for thread in workers:
            thread.join(timeout=10)
    finally:
        stop.set()
    return 0
