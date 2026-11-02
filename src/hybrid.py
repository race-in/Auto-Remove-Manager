#!/usr/bin/env python3
"""Per-client Hybrid Normal/Racing controller (preview implementation)."""
from __future__ import annotations

import math
import threading
import time
from typing import Any

import racing

MB = 1_000_000


class ModeTracker:
    """Switch modes after a continuous high/low speed streak."""
    def __init__(self, threshold_bytes: float, sustain_seconds: float):
        self.threshold = float(threshold_bytes)
        self.sustain = float(sustain_seconds)
        self.mode = "normal"
        self.high_since = None
        self.low_since = None

    def sample(self, speed: float, now: float):
        if not math.isfinite(speed) or speed < 0:
            self.high_since = None
            self.low_since = None
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


def run_hybrid(tasks: dict[str, Any], config: dict[str, Any],
               state_dir, log_dir, preview_callback, racing_callback) -> int:
    """Run independent per-client workers; callbacks own serialized cleanup."""
    logger = racing.make_logger(log_dir)
    try:
        threshold = float(config.get("download_threshold_mb", 100.0)) * MB
        sustain = float(config.get("sustain_seconds", 5.0))
        poll = float(config.get("poll_interval_seconds", 1.0))
        interval = float(config.get("cleanup_interval_seconds", 60.0))
        if (not all(math.isfinite(x) for x in (threshold, sustain, poll, interval))
                or threshold <= 0 or sustain <= 0 or poll <= 0 or interval < 1):
            raise ValueError("invalid thresholds")
    except (TypeError, ValueError, OverflowError):
        logger.error("Invalid Hybrid settings; controller not started.")
        return 2

    specs = racing._client_specs(tasks)
    if not specs:
        logger.error("Hybrid requires qBittorrent or Deluge tasks.")
        return 2
    stop = threading.Event()

    def worker(name, task):
        tracker = ModeTracker(threshold, sustain)
        api = None
        next_cleanup = 0.0
        logger.info("%s: Hybrid worker started in Normal mode.", name)
        while not stop.is_set():
            try:
                if api is None:
                    api = racing.ClientAPI(name, task)
                    api.connect()
                speed = api.download_speed()
                changed = tracker.sample(speed, time.monotonic())
                if changed:
                    logger.warning("%s: switched to %s mode.", name, changed.upper())
                    next_cleanup = 0.0
                now = time.monotonic()
                if now >= next_cleanup:
                    if tracker.mode == "normal":
                        preview_callback(name, task)
                    else:
                        racing_callback(name, task, api, config)
                    next_cleanup = time.monotonic() + interval
            except Exception as exc:
                logger.error("%s: API error (%s); cleanup suspended.", name, type(exc).__name__)
                tracker = ModeTracker(threshold, sustain)
                if api is not None:
                    api.close()
                    api = None
                stop.wait(max(poll, 2.0))
                continue
            stop.wait(poll)
        if api is not None:
            api.close()

    workers = [threading.Thread(target=worker, args=spec, daemon=True) for spec in specs]
    try:
        for thread in workers:
            thread.start()
        logger.info("Hybrid controller started with %d independent worker(s).", len(workers))
        while any(thread.is_alive() for thread in workers):
            for thread in workers:
                thread.join(timeout=0.5)
    except KeyboardInterrupt:
        stop.set()
        for thread in workers:
            thread.join(timeout=10)
    finally:
        stop.set()
    return 0
