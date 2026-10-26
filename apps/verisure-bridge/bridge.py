"""Verisure notification bridge: Android notifications from the android-bliss VM -> MQTT.

Read-only towards the Verisure app and the alarm: it only runs `dumpsys` over adb and never sends input,
starts activities or touches the app, and verisure_api.py can only send read queries. On each (re)connect
it re-applies two device settings that do not survive a reboot (mains power, and the Verisure app's
battery-optimisation exemption) so pushes are not deferred.

MQTT topics (prefix from MQTT_PREFIX, default home/verisure):
  <prefix>/notification   every new Verisure notification as JSON (not retained)
  <prefix>/last           the most recent one (retained)
  <prefix>/state          armed / disarmed (retained)
  <prefix>/changed_by     who armed or disarmed, as the notification names them (retained)

The Finnish app texts are "Päällekytkentä - <name>" (armed) and "Poiskytkentä - <name>" (disarmed); the
notification does not say whether the system was armed fully or in stay/home mode. On start the newest
existing Verisure notification seeds the state.
  <prefix>/bridge         online / offline (retained, last will)
  <prefix>/mode           armed_away / armed_home / disarmed from Verisure's API (retained); needs
                          VERISURE_USERNAME/PASSWORD, see verisure_api.py
  <prefix>/arm_state      the API's armState as JSON (retained)

For homectl (generic MQTT integration, topic home/sensors/verisure/{id}), retained JSON sensors:
  home/sensors/verisure/alarm       sensor_value armed_away / armed_home / disarmed (or "armed" until the
                                    API answers)
  home/sensors/verisure/armed       sensor_value true / false
  home/sensors/verisure/changed_by  sensor_value the name from the notification
Nothing subscribes to commands: these devices are read-only.
"""
import json, os, re, subprocess, sys, threading, time

import verisure_api

SERIAL = os.environ["ADB_SERIAL"]
PKG = os.environ.get("VERISURE_PACKAGE", "com.sdi.mobile.android.verisure")
PREFIX = os.environ.get("MQTT_PREFIX", "home/verisure")
MQTT = os.environ.get("MQTT_HOST", "mqtt")
INTERVAL = float(os.environ.get("POLL_SECONDS", "1"))

EXTRA = re.compile(r"^\s*android\.(title|text|bigText|subText|infoText|summaryText)=\S+ \((.*)\)\s*$")
ANY_EXTRA = re.compile(r"^\s*((?:android|com)\.[\w.]+)=(\S+)(?: \((.*)\))?\s*$")
CHANNEL = re.compile(r"Notification\(channel=(\S+)")
RECORD = re.compile(r"NotificationRecord\(0x[0-9a-f]+: pkg=(\S+) .*?key=(\S+?):")
WHEN = re.compile(r"^\s*when=(\d+)\s*$")


def log(*a):
    print(time.strftime("%Y-%m-%dT%H:%M:%S"), *a, flush=True)


def adb(*args, timeout=20):
    return subprocess.run(["adb", "-s", SERIAL, *args], capture_output=True, text=True, timeout=timeout)


def publish(topic, payload, retain=False):
    cmd = ["mosquitto_pub", "-h", MQTT, "-t", f"{PREFIX}/{topic}", "-m", payload, "-q", "1"]
    if retain:
        cmd.append("-r")
    subprocess.run(cmd, timeout=15, check=False)


def connect():
    subprocess.run(["adb", "connect", SERIAL], capture_output=True, text=True, timeout=20)
    r = adb("shell", "getprop sys.boot_completed")
    if r.stdout.strip() != "1":
        return False
    adb("shell", f"dumpsys battery set ac 1; dumpsys battery set level 100; dumpsys deviceidle whitelist +{PKG}")
    return True


def notifications():
    """Current Verisure notifications as {key: {title, text, ...}}."""
    r = adb("shell", "dumpsys notification --noredact")
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip() or "dumpsys failed")
    out, cur = {}, None
    for line in r.stdout.splitlines():
        m = RECORD.search(line)
        if m:
            cur = out.setdefault(m.group(2), {}) if m.group(1) == PKG else None
            c = CHANNEL.search(line)
            if cur is not None and c:
                cur["channel"] = c.group(1)
            continue
        if cur is None:
            continue
        m = EXTRA.match(line)
        if m and m.group(2) not in ("null", ""):
            cur.setdefault(m.group(1), m.group(2))
        # Every extra, raw, to tell notification variants apart (e.g. full vs stay arming).
        m = ANY_EXTRA.match(line)
        if m:
            cur.setdefault("extras", {}).setdefault(m.group(1), m.group(3) if m.group(3) is not None else m.group(2))
        m = WHEN.match(line)
        if m and "when_ms" not in cur:
            cur["when_ms"] = int(m.group(1))
    return {k: v for k, v in out.items() if v.get("title") or v.get("text")}


# "<event> - <name>"; the name is whoever made the change and may be missing. English texts are a guess;
# the app on the VM uses Finnish.
EVENT = re.compile(r"^\s*(Päällekytkentä|Poiskytkentä|Armed[^-]*|Disarmed)\b(?:\s*-\s*(.+?))?\s*$", re.I)


def state_of(n):
    m = EVENT.match(n.get("text", ""))
    if not m:
        return None, None
    word = m.group(1).lower()
    return ("disarmed" if word.startswith(("pois", "disarm")) else "armed"), m.group(2)


API = verisure_api.from_env()
HOMECTL = os.environ.get("HOMECTL_PREFIX", "home/sensors/verisure")
current = {}


def publish_homectl(**changes):
    """Keep homectl's sensor devices in step with what we know."""
    current.update({k: v for k, v in changes.items() if v is not None})
    extra = {k: current.get(k) for k in ("state", "mode", "changed_by", "changed_via", "changed_at")}
    devices = {
        "alarm": ("Alarm", current.get("mode") or current.get("state")),
        "armed": ("Alarm armed", None if "state" not in current else current["state"] == "armed"),
        "changed_by": ("Alarm changed by", current.get("changed_by")),
    }
    for dev_id, (name, value) in devices.items():
        if value is None:
            continue
        payload = {"id": dev_id, "name": name, "sensor_value": value, **extra}
        subprocess.run(["mosquitto_pub", "-h", MQTT, "-t", f"{HOMECTL}/{dev_id}", "-r", "-q", "1",
                        "-m", json.dumps(payload, ensure_ascii=False)], timeout=15, check=False)
MODES = {"ARMED_AWAY": "armed_away", "ARMED_HOME": "armed_home", "DISARMED": "disarmed"}


def lookup_mode(expected=None):
    """One read-only API call for the arm mode (the notification does not say stay vs away).

    If the answer contradicts the notification (the API not caught up yet), try once more 3 s later."""
    api = verisure_api.from_env()  # fresh client: picks up cookies saved by other processes (MFA setup)
    for attempt in (1, 2):
        try:
            s = api.arm_state()
        except verisure_api.MfaRequired as e:
            log("verisure api:", e)
            return
        except Exception as e:  # rate limits, outages: skip this one, the next event tries again
            log("verisure api error:", e)
            return
        mode = MODES.get(s.get("statusType"))
        consistent = expected is None or (mode == "disarmed") == (expected == "disarmed")
        if consistent or attempt == 2:
            break
        log("api still says", s.get("statusType"), "- retrying in 3 s")
        time.sleep(3)
    publish("arm_state", json.dumps(s, ensure_ascii=False), retain=True)
    if mode:
        publish("mode", mode, retain=True)
        publish_homectl(mode=mode, changed_via=s.get("changedVia"), changed_at=s.get("date"))
    log("mode", s.get("statusType"), "by", s.get("name"), "via", s.get("changedVia"))


def publish_state(n):
    state, who = state_of(n)
    if state:
        publish("state", state, retain=True)
        publish("changed_by", who or "", retain=True)
        log("state", state, "by", who)
        # A new state invalidates the old mode until the API answers.
        mode = current.get("mode")
        stale = mode and ((mode == "disarmed") != (state == "disarmed"))
        if stale:
            current.pop("mode", None)
        publish_homectl(state=state, changed_by=who or "")
        if API:
            threading.Thread(target=lookup_mode, args=(state,), daemon=True).start()


def main():
    seen, connected = None, False
    while True:
        try:
            if not connected:
                connected = connect()
                if not connected:
                    publish("bridge", "offline", retain=True)
                    time.sleep(30)
                    continue
                publish("bridge", "online", retain=True)
                log("connected to", SERIAL)
            current = notifications()
            if seen is None:
                seen = {k: v for k, v in current.items()}  # don't replay what was already there
                log("existing notifications:", len(current))
                known = [n for n in current.values() if state_of(n)[0]]
                if known:
                    publish_state(max(known, key=lambda n: n.get("when_ms", 0)))
            for key, n in current.items():
                if seen.get(key) == n:
                    continue
                seen[key] = n
                event = {"key": key, **n, "seen_ms": int(time.time() * 1000)}
                payload = json.dumps(event, ensure_ascii=False)
                publish_state(n)  # state first: it is what automations wait for
                log("notification", payload)
                publish("notification", payload)
                publish("last", payload, retain=True)
            for key in list(seen):
                if key not in current:
                    del seen[key]
        except Exception as e:  # adb drops, VM reboots
            log("error:", e)
            connected = False
            publish("bridge", "offline", retain=True)
            time.sleep(10)
            continue
        time.sleep(INTERVAL)


if __name__ == "__main__":
    sys.exit(main())
