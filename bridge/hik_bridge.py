"""On-site bridge between Hikvision terminals on the office LAN and the cloud-hosted app.

Run it on any always-on PC (Windows or Linux) on the same network as the
terminal. It only makes outgoing HTTPS requests, so no router changes are needed.

Every few seconds it asks the server for work (actions you clicked in the web
UI, e.g. "Send employee to device"), carries them out on the terminal, and on
the configured interval uploads new punches.

Configuration: bridge.ini next to this file (copy bridge.ini.example) or the
environment variables BRIDGE_SERVER_URL, BRIDGE_TOKEN, BRIDGE_POLL_SECONDS.

    python bridge/hik_bridge.py           # run forever
    python bridge/hik_bridge.py --once    # one cycle, then exit (for testing)
"""
import argparse
import configparser
import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import device_actions as actions  # noqa: E402
from app.hikvision import HikvisionError  # noqa: E402

log = logging.getLogger("hik-bridge")
CHUNK = 500


class Bridge:
    def __init__(self, server_url, token, poll_seconds=20, session=None):
        self.server = server_url.rstrip("/")
        self.poll_seconds = poll_seconds
        self.http = session or requests.Session()
        self.http.headers["Authorization"] = f"Bearer {token}"
        self.last_pull = {}  # device id -> time.monotonic() of last event upload

    # -- server calls ----------------------------------------------------------
    def _api(self, method, path, **kwargs):
        resp = self.http.request(method, f"{self.server}/api/bridge{path}", timeout=60, **kwargs)
        if resp.status_code == 401:
            raise SystemExit("Server rejected the bridge token - copy it again from Settings → Bridge agent.")
        resp.raise_for_status()
        return resp.json()

    # -- work ------------------------------------------------------------------
    def cycle(self):
        work = self._api("GET", "/poll")
        devices = {d["id"]: d for d in work["devices"]}
        settings = work["settings"]
        for job in work["jobs"]:
            device = devices.get(job["device_id"])
            if device:
                self.run_job(device, job, settings)
        interval = settings["sync_interval_min"] * 60
        for device in devices.values():
            due = interval > 0 and time.monotonic() - self.last_pull.get(device["id"], -1e9) >= interval
            if due:
                self.pull_events(device, settings)

    def pull_events(self, device, settings, start=None, end=None):
        client = actions.client_for(device)
        if not (start and end):
            start, end = actions.event_window(device, settings["initial_sync_days"])
        try:
            events = actions.fetch_events(client, start, end, settings["event_minors"])
        except HikvisionError as exc:
            log.warning("%s: %s", device["name"], exc)
            self._api("POST", "/status", json={"device_id": device["id"], "ok": False, "error": str(exc)})
            self.last_pull[device["id"]] = time.monotonic()
            return None
        seen = added = 0
        for i in range(0, max(len(events), 1), CHUNK):
            res = self._api("POST", "/punches", json={"device_id": device["id"], "events": events[i:i + CHUNK]})
            seen += res["seen"]
            added += res["added"]
        self.last_pull[device["id"]] = time.monotonic()
        if added:
            log.info("%s: uploaded %d new punches", device["name"], added)
        return seen, added

    def run_job(self, device, job, settings):
        action, payload = job["action"], job["payload"]
        log.info("Job %s: %s on %s", job["id"], action, device["name"])
        try:
            if action == "sync-events":
                res = self.pull_events(device, settings, payload.get("start"), payload.get("end"))
                if res is None:
                    raise HikvisionError("could not read events (see device status)")
                result = {"ok": True, "message": f"Downloaded {res[0]} events, {res[1]} new punches."}
            else:
                message, data = actions.run(actions.client_for(device), action, payload, device["tz_offset"])
                result = {"ok": True, "message": message, "data": data}
        except (HikvisionError, KeyError, ValueError) as exc:
            log.warning("Job %s failed: %s", job["id"], exc)
            result = {"ok": False, "message": str(exc)}
        self._api("POST", f"/jobs/{job['id']}", json=result)

    def forever(self):
        log.info("Bridge started, talking to %s", self.server)
        while True:
            try:
                self.cycle()
            except requests.RequestException as exc:
                log.warning("Server not reachable (%s); retrying", exc)
            except Exception:
                log.exception("Unexpected error; retrying")
            time.sleep(self.poll_seconds)


def load_config():
    cfg = configparser.ConfigParser()
    cfg.read(Path(__file__).with_name("bridge.ini"))
    section = cfg["bridge"] if cfg.has_section("bridge") else {}
    url = os.environ.get("BRIDGE_SERVER_URL") or section.get("server_url", "")
    token = os.environ.get("BRIDGE_TOKEN") or section.get("token", "")
    poll = int(os.environ.get("BRIDGE_POLL_SECONDS") or section.get("poll_seconds", 20))
    if not url or not token:
        raise SystemExit("Set server_url and token in bridge/bridge.ini (see bridge.ini.example).")
    return url, token, poll


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--once", action="store_true", help="run a single cycle and exit")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        handlers=[logging.StreamHandler(),
                                  logging.FileHandler(Path(__file__).with_name("bridge.log"), encoding="utf-8")])
    bridge = Bridge(*load_config())
    if args.once:
        bridge.cycle()
        log.info("Done at %s", datetime.now())
    else:
        bridge.forever()


if __name__ == "__main__":
    main()
