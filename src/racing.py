#!/usr/bin/env python3
"""Independent, fail-closed Racing Mode monitor for qBittorrent and Deluge."""
from __future__ import annotations

import fcntl
import logging
import logging.handlers
import math
import os
import pathlib
import time
from typing import Any

MB = 1_000_000
DEFAULT_DOWNLOAD_THRESHOLD_MB = 100.0
DEFAULT_UPLOAD_MAX_MB = 10.0
DEFAULT_SUSTAIN_SECONDS = 5.0
DEFAULT_POLL_INTERVAL = 1.0
LOG_MAX_BYTES = 5 * 1024 * 1024
LOG_BACKUP_COUNT = 1

QB_SEED_STATES = frozenset(("uploading", "stalledup", "queuedup", "forcedup"))


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if math.isfinite(result) else None


def eligible_torrents(client: str, torrents: list[dict[str, Any]],
                      ratio_min: float = 1.0,
                      upload_max_bytes: float = DEFAULT_UPLOAD_MAX_MB * MB
                      ) -> list[dict[str, Any]]:
    """Return only verified seeding torrents meeting ratio and live-upload limits."""
    selected = []
    for torrent in torrents:
        state = str(torrent.get("state", "")).strip().lower()
        if client == "qbittorrent":
            is_seeding = state in QB_SEED_STATES
            ratio = _number(torrent.get("ratio"))
            upload = _number(torrent.get("upspeed"))
            torrent_hash = torrent.get("hash")
        elif client == "deluge":
            is_seeding = state == "seeding"
            ratio = _number(torrent.get("ratio"))
            upload = _number(torrent.get("upload_payload_rate"))
            torrent_hash = torrent.get("hash")
        else:
            raise ValueError("Racing Mode supports only qBittorrent and Deluge.")

        # Missing or malformed API fields are never treated as zero.
        if not is_seeding or not torrent_hash or ratio is None or upload is None:
            continue
        if ratio >= ratio_min and 0 <= upload <= upload_max_bytes:
            selected.append(torrent)
    return selected


class SpeedGate:
    """One-shot threshold gate; a failed sample or dip resets the full streak."""

    def __init__(self, threshold_bytes: float, sustain_seconds: float):
        self.threshold_bytes = float(threshold_bytes)
        self.sustain_seconds = float(sustain_seconds)
        self.started_at: float | None = None
        self.triggered = False

    def sample(self, speed_bytes: float | None, now: float) -> bool:
        if speed_bytes is None or speed_bytes < self.threshold_bytes:
            self.started_at = None
            self.triggered = False
            return False
        if self.triggered:
            return False
        if self.started_at is None:
            self.started_at = now
            return False
        if now - self.started_at >= self.sustain_seconds:
            self.triggered = True
            return True
        return False


class ClientAPI:
    """Small adapter using the upstream project's supported Deluge RPC dependency."""

    def __init__(self, task_name: str, task_conf: dict[str, Any]):
        self.task_name = task_name
        self.client = str(task_conf.get("client", "")).strip().lower()
        self.host = str(task_conf.get("host", "")).strip()
        self.username = str(task_conf.get("username", "") or "")
        self.password = str(task_conf.get("password", "") or "")
        self.session = None
        self.rpc = None
        if self.client not in ("qbittorrent", "deluge"):
            raise ValueError("Racing Mode supports only qBittorrent and Deluge.")
        if not self.host:
            raise ValueError("Client API host is missing.")

    def connect(self) -> None:
        if self.client == "qbittorrent":
            import requests
            self.session = requests.Session()
            self.session.trust_env = True
            version = self.session.get(
                self.host.rstrip("/") + "/api/v2/app/webapiVersion", timeout=(3, 8)
            )
            version.raise_for_status()
            response = self.session.post(
                self.host.rstrip("/") + "/api/v2/auth/login",
                data={"username": self.username, "password": self.password},
                timeout=(3, 8),
            )
            if response.status_code not in (200, 204) or response.text.strip() == "Fails.":
                raise RuntimeError("qBittorrent authentication failed.")
            # Verify the authenticated session by reading transfer state.
            self._qb_get("/api/v2/transfer/info")
            return

        from deluge_client import DelugeRPCClient
        host, separator, port_text = self.host.rpartition(":")
        if not separator or not host or not port_text.isdigit():
            raise ValueError("Deluge host must be formatted as hostname:port.")
        self.rpc = DelugeRPCClient(
            host, int(port_text), self.username, self.password,
            decode_utf8=True, timeout=8,
        )
        self.rpc.connect()
        # Verify authentication and the expected session API before monitoring.
        self.rpc.call("core.get_session_status", ["payload_download_rate"])

    def _qb_get(self, path: str) -> Any:
        response = self.session.get(self.host.rstrip("/") + path, timeout=(3, 8))
        response.raise_for_status()
        return response.json()

    def _qb_post(self, path: str, data: dict[str, Any]) -> Any:
        response = self.session.post(
            self.host.rstrip("/") + path, data=data, timeout=(3, 8)
        )
        response.raise_for_status()
        return response

    def download_speed(self) -> float:
        if self.client == "qbittorrent":
            result = self._qb_get("/api/v2/transfer/info")
            value = _number(result.get("dl_info_speed"))
        else:
            result = self.rpc.call("core.get_session_status", ["payload_download_rate"])
            value = _number(result.get("payload_download_rate"))
        if value is None or value < 0:
            raise RuntimeError("Client returned an invalid download speed.")
        return value

    def torrents(self) -> list[dict[str, Any]]:
        if self.client == "qbittorrent":
            result = self._qb_get("/api/v2/torrents/info")
            if not isinstance(result, list):
                raise RuntimeError("qBittorrent returned an invalid torrent list.")
            return result
        result = self.rpc.call("core.get_torrents_status", {}, [
            "hash", "name", "state", "ratio", "upload_payload_rate",
        ])
        if not isinstance(result, dict):
            raise RuntimeError("Deluge returned an invalid torrent list.")
        torrents = []
        for torrent_hash, properties in result.items():
            if not isinstance(properties, dict):
                continue
            item = dict(properties)
            item.setdefault("hash", torrent_hash)
            torrents.append(item)
        return torrents

    def remove_with_data(self, torrent_hashes: list[str]) -> tuple[list[str], list[str]]:
        """Remove selected hashes and payload data; never called in preview mode."""
        if not torrent_hashes:
            return [], []
        if self.client == "qbittorrent":
            self._qb_post("/api/v2/torrents/delete", {
                "hashes": "|".join(torrent_hashes), "deleteFiles": "true",
            })
            # qBittorrent's API returns HTTP 200 for an accepted batch.
            return list(torrent_hashes), []

        succeeded = []
        failed = []
        # core.remove_torrent(hash, True) is compatible with Deluge 1.x and 2.x.
        for torrent_hash in torrent_hashes:
            try:
                result = self.rpc.call("core.remove_torrent", torrent_hash, True)
                if result is True:
                    succeeded.append(torrent_hash)
                else:
                    failed.append(torrent_hash)
            except Exception:
                failed.append(torrent_hash)
        return succeeded, failed

    def close(self) -> None:
        if self.session is not None:
            self.session.close()
            self.session = None
        if self.rpc is not None:
            try:
                self.rpc.disconnect()
            except Exception:
                pass
            self.rpc = None


def make_logger(log_dir: pathlib.Path) -> logging.Logger:
    log_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("auto_remove_manager.racing")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    if not logger.handlers:
        handler = logging.handlers.RotatingFileHandler(
            str(log_dir / "racing.log"), maxBytes=LOG_MAX_BYTES,
            backupCount=LOG_BACKUP_COUNT, encoding="utf-8",
        )
        handler.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)s %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
        ))
        console = logging.StreamHandler()
        console.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
        logger.addHandler(handler)
        logger.addHandler(console)
    return logger


def _try_run_lock(state_dir: pathlib.Path):
    state_dir.mkdir(parents=True, exist_ok=True)
    lock_file = (state_dir / "run.lock").open("a+")
    try:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock_file.close()
        return None
    return lock_file


def _client_specs(tasks: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    specs = []
    for name, conf in tasks.items():
        if not isinstance(conf, dict):
            continue
        client = str(conf.get("client", "")).strip().lower()
        if client in ("qbittorrent", "deluge"):
            specs.append((name, conf))
    return specs


def run_monitor(tasks: dict[str, Any], config: dict[str, Any],
                state_dir: pathlib.Path, log_dir: pathlib.Path) -> int:
    """Monitor each configured qBittorrent/Deluge task independently until stopped."""
    logger = make_logger(log_dir)
    download_mb = float(config.get("download_threshold_mb", DEFAULT_DOWNLOAD_THRESHOLD_MB))
    upload_mb = float(config.get("upload_max_mb", DEFAULT_UPLOAD_MAX_MB))
    sustain = float(config.get("sustain_seconds", DEFAULT_SUSTAIN_SECONDS))
    poll = float(config.get("poll_interval_seconds", DEFAULT_POLL_INTERVAL))
    ratio_min = float(config.get("ratio_min", 1.0))
    if (not math.isfinite(download_mb) or download_mb <= 0
            or not math.isfinite(upload_mb) or upload_mb < 0
            or not math.isfinite(sustain) or sustain <= 0
            or not math.isfinite(poll) or poll <= 0
            or not math.isfinite(ratio_min) or ratio_min < 1.0):
        logger.error("Invalid Racing Mode thresholds; monitor was not started.")
        return 2

    specs = _client_specs(tasks)
    if not specs:
        logger.error("No qBittorrent or Deluge tasks are configured.")
        return 2

    monitors = {}
    for name, task_conf in specs:
        monitors[name] = {
            "conf": task_conf,
            "client": None,
            "gate": SpeedGate(download_mb * MB, sustain),
            "last_error": None,
        }
    logger.info(
        "Racing Mode started: download >= %.2f MB/s for %.1f s; "
        "eligible seed upload <= %.2f MB/s, ratio >= %.2f; poll %.1f s.",
        download_mb, sustain, upload_mb, ratio_min, poll,
    )
    logger.info("Seeding time is not evaluated. Payload deletion is enabled only when removal safeguards allow it.")

    try:
        while True:
            cycle_started = time.monotonic()
            for name, state in monitors.items():
                client = state["client"]
                try:
                    if client is None:
                        client = ClientAPI(name, state["conf"])
                        client.connect()
                        state["client"] = client
                        logger.info("%s (%s): API authenticated and read-only state verified.",
                                    name, client.client)
                    speed = client.download_speed()
                    should_trigger = state["gate"].sample(speed, time.monotonic())
                    state["last_error"] = None
                    if not should_trigger:
                        continue

                    logger.warning(
                        "%s (%s): download threshold sustained for %.1f seconds at %.2f MB/s.",
                        name, client.client, sustain, speed / MB,
                    )
                    torrents = client.torrents()
                    candidates = eligible_torrents(
                        client.client, torrents, ratio_min, upload_mb * MB
                    )
                    if not candidates:
                        logger.info("%s: no eligible seeding torrents found.", name)
                        continue

                    for item in candidates:
                        logger.info(
                            "%s: eligible name=%r hash=%s ratio=%.3f upload=%.3f MB/s",
                            name, str(item.get("name", ""))[:160], item.get("hash"),
                            float(item.get("ratio")), (
                                float(item.get("upspeed") if client.client == "qbittorrent"
                                      else item.get("upload_payload_rate")) / MB
                            ),
                        )

                    preview_only = bool(config.get("preview_only", True))
                    allow_removal = bool(config.get("allow_removal", False))
                    allow_data_deletion = bool(config.get("allow_data_deletion", False))
                    if preview_only or not allow_removal or not allow_data_deletion:
                        logger.warning(
                            "%s: PREVIEW ONLY — %d eligible torrent(s); no data was removed. "
                            "Enable all Racing Mode safeguards explicitly to delete.",
                            name, len(candidates),
                        )
                        continue

                    lock = _try_run_lock(state_dir)
                    if lock is None:
                        logger.warning("%s: another manager cleanup is active; this trigger was skipped.", name)
                        continue
                    try:
                        hashes = [str(item["hash"]) for item in candidates]
                        succeeded, failed = client.remove_with_data(hashes)
                        logger.warning(
                            "%s: removed %d torrent(s) with payload data; %d failure(s).",
                            name, len(succeeded), len(failed),
                        )
                        for failed_hash in failed:
                            logger.error("%s: removal failed for hash=%s", name, failed_hash)
                    finally:
                        lock.close()
                except KeyboardInterrupt:
                    raise
                except Exception as exc:
                    # A failed sample breaks the 5-second streak. The other
                    # client's monitor continues independently.
                    state["gate"].sample(None, time.monotonic())
                    if state["client"] is not None:
                        state["client"].close()
                        state["client"] = None
                    error = type(exc).__name__
                    if state["last_error"] != error:
                        logger.error("%s: API unavailable/unverified (%s); no deletion. Retrying.",
                                     name, error)
                        state["last_error"] = error
            elapsed = time.monotonic() - cycle_started
            time.sleep(max(0.0, poll - elapsed))
    except KeyboardInterrupt:
        logger.info("Racing Mode stopped by user.")
        return 0
    finally:
        for state in monitors.values():
            if state["client"] is not None:
                state["client"].close()
