"""Verisure notification bridge: Android notifications from the android-bliss VM -> MQTT.

Read-only towards the Verisure app and the alarm: it only runs `dumpsys` over adb and never sends input,
starts activities or touches the app. On each (re)connect it re-applies two device settings that do not
survive a reboot (mains power, and the Verisure app's battery-optimisation exemption) so pushes are not
deferred.

MQTT topics (prefix from MQTT_PREFIX, default home/verisure):
  <prefix>/notification   every new Verisure notification as JSON (not retained)
  <prefix>/last           the most recent one (retained)
  <prefix>/state          armed / disarmed (retained)
  <prefix>/changed_by     who armed or disarmed, as the notification names them (retained)

The Finnish app texts are "Päällekytkentä - <name>" (armed) and "Poiskytkentä - <name>" (disarmed); the
notification does not say whether the system was armed fully or in stay/home mode. On start the newest
existing Verisure notification seeds the state.
  <prefix>/bridge         online / offline (retained, last will)
"""
import json, os, re, subprocess, sys, time

SERIAL = os.environ["ADB_SERIAL"]
PKG = os.environ.get("VERISURE_PACKAGE", "com.sdi.mobile.android.verisure")
PREFIX = os.environ.get("MQTT_PREFIX", "home/verisure")
MQTT = os.environ.get("MQTT_HOST", "mqtt")
INTERVAL = float(os.environ.get("POLL_SECONDS", "2"))

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


def publish_state(n):
    state, who = state_of(n)
    if state:
        publish("state", state, retain=True)
        publish("changed_by", who or "", retain=True)
        log("state", state, "by", who)


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
                log("notification", payload)
                publish("notification", payload)
                publish("last", payload, retain=True)
                publish_state(n)
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
