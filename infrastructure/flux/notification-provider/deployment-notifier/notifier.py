"""Notify Discord after Flux and a digest-pinned Deployment are both ready.

Only Python's standard library is needed. Run one replica with persistent state.
"""

import base64
import datetime
import json
import logging
import os
from pathlib import Path
import re
import signal
import ssl
import threading
import time
from urllib.parse import urlencode, urlsplit, urlunsplit, parse_qsl
from urllib.request import Request, urlopen


LOG = logging.getLogger("deployment-notifier")
ACCEPT = ", ".join([
    "application/vnd.oci.image.index.v1+json",
    "application/vnd.docker.distribution.manifest.list.v2+json",
    "application/vnd.oci.image.manifest.v1+json",
    "application/vnd.docker.distribution.manifest.v2+json",
])


def request_json(url, *, headers=None, payload=None, context=None):
    headers = {"User-Agent": "homelab-deployment-notifier", **(headers or {})}
    data = None
    if payload is not None:
        data = json.dumps(payload).encode()
        headers["Content-Type"] = "application/json"
    with urlopen(Request(url, data=data, headers=headers), timeout=15, context=context) as response:
        return json.loads(response.read())


def ready(resource):
    """Reject stale Ready conditions and in-progress or failed reconciliation."""
    generation = resource["metadata"]["generation"]
    status = resource.get("status", {})
    if status.get("observedGeneration") != generation:
        return False
    conditions = status.get("conditions", [])
    if any(c["type"] in ("Reconciling", "Stalled") and c["status"] == "True" for c in conditions):
        return False
    return any(c["type"] == "Ready" and c["status"] == "True"
               and c.get("observedGeneration") == generation for c in conditions)


def deployed_image(deployment, container):
    """Require all replicas on the new generation, ready, with no old replicas."""
    spec, status = deployment["spec"], deployment.get("status", {})
    count = spec.get("replicas", 1)
    if spec.get("paused") or count < 1:
        return None
    if status.get("observedGeneration") != deployment["metadata"]["generation"]:
        return None
    if any(status.get(key, 0) != count for key in
           ("replicas", "updatedReplicas", "readyReplicas", "availableReplicas")):
        return None
    image = next((c["image"] for c in spec["template"]["spec"]["containers"]
                  if c["name"] == container), "")
    return image if re.search(r"@sha256:[a-f0-9]{64}$", image) else None


class Kubernetes:
    def __init__(self):
        directory = Path("/var/run/secrets/kubernetes.io/serviceaccount")
        self.token_file = directory / "token"
        self.context = ssl.create_default_context(cafile=str(directory / "ca.crt"))
        self.base = "https://kubernetes.default.svc"

    def get(self, path):
        # Re-read projected tokens so long-running pods survive token rotation.
        return request_json(self.base + path, context=self.context, headers={
            "Authorization": "Bearer " + self.token_file.read_text().strip(),
        })

    def snapshot(self, service):
        flux = self.get("/apis/kustomize.toolkit.fluxcd.io/v1/namespaces/flux-system/"
                        "kustomizations/" + service["kustomization"])
        if flux.get("spec", {}).get("suspend") or not ready(flux):
            return None
        deployment = self.get("/apis/apps/v1/namespaces/" + service["namespace"]
                              + "/deployments/" + service["deployment"])
        image = deployed_image(deployment, service["container"])
        if not image:
            return None
        return {"image": image, "flux_revision": flux["status"].get("lastAppliedRevision", "")}

    def registry_credentials(self, service):
        """Read the workload's existing pull secret, including credential rotation."""
        if not service.get("registry_secret"):
            return None
        secret = self.get("/api/v1/namespaces/" + service["namespace"]
                          + "/secrets/" + service["registry_secret"])
        config = json.loads(base64.b64decode(secret["data"][".dockerconfigjson"]))
        entry = config["auths"]["ghcr.io"]
        if entry.get("auth"):
            username, password = base64.b64decode(entry["auth"], validate=True).decode().split(":", 1)
            return username, password
        return entry["username"], entry["password"]


def image_labels(image, architecture, credentials=None):
    """Resolve the pinned GHCR digest, including multi-platform image indexes."""
    match = re.fullmatch(r"ghcr.io/([a-z0-9._/-]+)(?::[^@/]+)?@(sha256:[a-f0-9]{64})", image)
    if not match:
        return {}  # Other registries still get digest-based success messages.
    repository, digest = match.groups()
    query = urlencode({"service": "ghcr.io", "scope": f"repository:{repository}:pull"})
    login_headers = {}
    if credentials:
        auth = base64.b64encode(":".join(credentials).encode()).decode()
        login_headers["Authorization"] = "Basic " + auth
    token = request_json("https://ghcr.io/token?" + query, headers=login_headers)["token"]
    headers = {"Authorization": "Bearer " + token, "Accept": ACCEPT}
    base = "https://ghcr.io/v2/" + repository
    manifest = request_json(base + "/manifests/" + digest, headers=headers)
    if "manifests" in manifest:
        candidate = next((m for m in manifest["manifests"]
                          if m.get("platform", {}).get("os") == "linux"
                          and m["platform"].get("architecture") == architecture), None)
        if candidate is None:
            raise ValueError("no image manifest for configured architecture")
        manifest = request_json(base + "/manifests/" + candidate["digest"], headers=headers)
    config = request_json(base + "/blobs/" + manifest["config"]["digest"], headers=headers)
    return config.get("config", {}).get("Labels") or {}


def github_details(repository, revision, previous):
    if not re.fullmatch(r"[a-f0-9]{7,40}", revision):
        return {}
    base = "https://github.com/" + repository
    details = {"url": base + "/commit/" + revision, "changes": []}
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    if os.environ.get("GITHUB_TOKEN"):
        headers["Authorization"] = "Bearer " + os.environ["GITHUB_TOKEN"]
    api = "https://api.github.com/repos/" + repository
    try:
        if previous and re.fullmatch(r"[a-f0-9]{7,40}", previous):
            details["url"] = base + "/compare/" + previous + "..." + revision
            comparison = request_json(api + "/compare/" + previous + "..." + revision
                                      + "?per_page=100", headers=headers)
            details["direction"] = comparison["status"]
            details["total"] = comparison.get("total_commits", 0)
            commits = comparison.get("commits", [])
            # Rollbacks/diverged history must not be described as forward changes.
            if comparison["status"] not in ("ahead", "identical"):
                commits = [request_json(api + "/commits/" + revision, headers=headers)]
        else:
            commits = [request_json(api + "/commits/" + revision, headers=headers)]
        details["changes"] = [c["commit"]["message"].splitlines()[0] for c in commits[:5]]
    except Exception as error:
        # GitHub availability/rate limiting should not block a rollout notification.
        LOG.warning("change summary unavailable (%s)", type(error).__name__)
    return details


def discord_payload(name, current, previous, details):
    revision = current.get("revision", "")
    version = current.get("version") or revision[:7] or current["image"].split("@")[1][:19]
    fields = [{"name": "Version", "value": version[:1024], "inline": True}]
    if previous.get("revision"):
        fields.append({"name": "Previous commit", "value": previous["revision"][:7], "inline": True})
    if revision:
        fields.append({"name": "Commit", "value": revision[:7], "inline": True})
    changes = details.get("changes", [])
    description = "\n".join("• " + message[:250] for message in changes)
    if details.get("direction") in ("behind", "diverged"):
        description = "Rollback or different history; deployed commit:\n" + description
    elif details.get("total", 0) > len(changes):
        description += f"\n…and {details['total'] - len(changes)} more commits."
    if details.get("direction") == "identical":
        description = "Rebuilt image; source commit is unchanged."
    if not description:
        description = "The new image is ready. Change summaries are unavailable."
    if details.get("url"):
        description += "\n\n[View changes](" + details["url"] + ")"
    embed = {
        "title": "✅ " + name + " deployed to homelab",
        "description": description,
        "color": 0x2ECC71,
        "fields": fields + [{"name": "Image", "value": current["image"][:1024]}],
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "footer": {"text": "Flux reconciliation and Deployment rollout are ready"},
    }
    return {"username": "Homelab deployments", "allowed_mentions": {"parse": []}, "embeds": [embed]}


class Notifier:
    def __init__(self, state_path, snapshot, labels, changes, send):
        self.path = Path(state_path)
        self.snapshot, self.labels, self.changes, self.send = snapshot, labels, changes, send
        # A malformed state file is an error, not permission to flood/rebaseline silently.
        self.state = json.loads(self.path.read_text()) if self.path.exists() else {}

    def save(self, name, current):
        updated = {**self.state, name: current}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        with temporary.open("w") as output:
            json.dump(updated, output)
            output.flush()
            os.fsync(output.fileno())
        temporary.replace(self.path)
        self.state = updated

    def check(self, service):
        current = self.snapshot(service)
        if current is None:
            return
        name = service["name"]
        previous = self.state.get(name)
        if previous and current["image"].split("@")[1] == previous["image"].split("@")[1]:
            return
        labels = self.labels(current["image"], service)
        # Some CI builds tag images with the full source SHA but omit OCI labels.
        sha_tag = re.search(r":([a-f0-9]{40})@sha256:", current["image"])
        current["revision"] = (labels.get("org.opencontainers.image.revision")
                               or (sha_tag.group(1) if sha_tag else ""))
        current["version"] = labels.get("org.opencontainers.image.version", "")
        # main is a moving tag, not a useful release version.
        if current["version"] in ("main", "master", "latest"):
            current["version"] = ""
        if previous is None:
            self.save(name, current)
            LOG.info("recorded initial baseline for %s", name)
            return
        details = self.changes(service["repository"], current["revision"], previous.get("revision"))
        # Confirm the image is still the ready image after external metadata lookups.
        confirmed = self.snapshot(service)
        if confirmed is None or confirmed["image"] != current["image"]:
            return
        self.send(discord_payload(name, current, previous, details))
        self.save(name, current)  # Advance only after Discord confirms delivery.
        LOG.info("notified successful deployment of %s", name)


def send_discord(webhook, payload):
    parts = urlsplit(webhook)
    query = dict(parse_qsl(parts.query))
    query["wait"] = "true"
    url = urlunsplit(parts._replace(query=urlencode(query)))
    request_json(url, payload=payload)


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    config = json.loads(Path(os.environ.get("CONFIG_PATH", "/config/services.json")).read_text())
    webhook = os.environ["DISCORD_WEBHOOK_URL"]
    if urlsplit(webhook).scheme != "https":
        raise ValueError("Discord webhook requires HTTPS")
    kube = Kubernetes()
    notifier = Notifier("/data/state.json", kube.snapshot,
                        lambda image, service: image_labels(
                            image, config.get("architecture", "amd64"), kube.registry_credentials(service)),
                        github_details, lambda payload: send_discord(webhook, payload))
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    while not stop.is_set():
        for service in config["services"]:
            Path("/data/heartbeat").write_text(str(time.time()))
            try:
                notifier.check(service)
            except Exception as error:
                # Exception messages can contain webhook/token URLs; don't log them.
                LOG.warning("check for %s failed (%s, HTTP status %s); will retry",
                            service["name"], type(error).__name__, getattr(error, "code", "n/a"))
        Path("/data/heartbeat").write_text(str(time.time()))
        stop.wait(config.get("interval_seconds", 30))


if __name__ == "__main__":
    main()
