"""Verisure notification bridge: Android notifications from the android-bliss VM -> MQTT.

Read-only towards the Verisure app and the alarm: it only runs `dumpsys` over adb and never sends input,
starts activities or touches the app. On each (re)connect it re-applies two device settings that do not
survive a reboot (mains power, and the Verisure app's battery-optimisation exemption) so pushes are not
deferred.

MQTT topics (prefix from MQTT_PREFIX, default home/verisure):
  <prefix>/notification   every new Verisure notification as JSON (not retained)
  <prefix>/last           the most recent one (retained)
  <prefix>/state          armed_away / armed_home / disarmed, guessed from the text (retained)
  <prefix>/bridge         online / offline (retained, last will)
"""
import json, os, re, subprocess, sys, time

SERIAL = os.environ["ADB_SERIAL"]
PKG = os.environ.get("VERISURE_PACKAGE", "com.sdi.mobile.android.verisure")
PREFIX = os.environ.get("MQTT_PREFIX", "home/verisure")
MQTT = os.environ.get("MQTT_HOST", "mqtt")
INTERVAL = float(os.environ.get("POLL_SECONDS", "5"))

EXTRA = re.compile(r"^\s*android\.(title|text|bigText|subText|infoText|summaryText)=\S+ \((.*)\)\s*$")
RECORD = re.compile(r"NotificationRecord\(0x[0-9a-f]+: pkg=(\S+) .*?key=(\S+?):")
POSTED = re.compile(r"\bmPostTime(?:Ms)?=(\d+)|\bpostTime=(\d+)")


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
            continue
        if cur is None:
            continue
        m = EXTRA.match(line)
        if m and m.group(2) not in ("null", ""):
            cur.setdefault(m.group(1), m.group(2))
        m = POSTED.search(line)
        if m and "posted_ms" not in cur:
            cur["posted_ms"] = int(m.group(1) or m.group(2))
    return {k: v for k, v in out.items() if v.get("title") or v.get("text")}


def guess_state(n):
    s = " ".join(str(n.get(k, "")) for k in ("title", "text", "bigText")).lower()
    if re.search(r"\bdisarm|\bpurettu|\bpois päältä", s):
        return "disarmed"
    if re.search(r"armed home|partially|kotitila|osittain", s):
        return "armed_home"
    if re.search(r"\barmed\b|\bviritetty|\bpäällä", s):
        return "armed_away"
    return None


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
            for key, n in current.items():
                if seen.get(key) == n:
                    continue
                seen[key] = n
                event = {"key": key, **n, "seen_ms": int(time.time() * 1000)}
                payload = json.dumps(event, ensure_ascii=False)
                log("notification", payload)
                publish("notification", payload)
                publish("last", payload, retain=True)
                state = guess_state(n)
                if state:
                    publish("state", state, retain=True)
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
