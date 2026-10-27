#!/usr/bin/env python3
"""Warm Zot with workload images and their running digests (linux/amd64)."""

import base64
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
from pathlib import Path
import ssl
import subprocess
import sys
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
PREFIXES = {"docker.io": "dockerhub", "ghcr.io": "ghcr", "quay.io": "quay",
            "registry.k8s.io": "k8s", "factory.talos.dev": "factory"}
ACCEPT = ", ".join([
    "application/vnd.oci.image.index.v1+json",
    "application/vnd.oci.image.manifest.v1+json",
    "application/vnd.docker.distribution.manifest.list.v2+json",
    "application/vnd.docker.distribution.manifest.v2+json",
])


def main():
    item = json.loads(subprocess.check_output([
        "op-agent", "item", "get", "homelab - zot node reader", "--vault", "Agents", "--format", "json"
    ]))
    fields = {field["label"]: field.get("value", "") for field in item["fields"]}
    auth = "Basic " + base64.b64encode(
        (fields["username"] + ":" + fields["password"]).encode()).decode()
    context = ssl.create_default_context(cafile=str(ROOT / "nas/zot/ca.crt"))
    pods = json.loads(subprocess.check_output([
        "kubectl", "--kubeconfig", str(ROOT / "kubeconfig"), "get", "pods", "-A", "-o", "json"
    ]))["items"]
    images = set(sys.argv[1:])
    for pod in pods:
        for container in pod["spec"].get("containers", []) + pod["spec"].get("initContainers", []):
            images.add(container["image"])
        for status in pod.get("status", {}).get("containerStatuses", []) + pod.get("status", {}).get("initContainerStatuses", []):
            image_id = status.get("imageID", "")
            if "@sha256:" in image_id:
                images.add(image_id.removeprefix("docker-pullable://"))

    def fetch(repository, kind, reference):
        request = urllib.request.Request(
            f"https://nectarine.internal.fruitiex.org:5050/v2/{repository}/{kind}/{reference}",
            headers={"Authorization": auth, "Accept": ACCEPT})
        with urllib.request.urlopen(request, context=context, timeout=1800) as response:
            digest = hashlib.sha256()
            data = bytearray()
            while chunk := response.read(1024 * 1024):
                digest.update(chunk)
                if kind == "manifests":
                    data.extend(chunk)
            if reference.startswith("sha256:") and "sha256:" + digest.hexdigest() != reference:
                raise ValueError("Digest mismatch")
            return json.loads(data) if kind == "manifests" else None

    def manifest(repository, reference):
        value = fetch(repository, "manifests", reference)
        if "manifests" in value:
            candidates = [child for child in value["manifests"]
                          if child.get("platform", {}).get("os") == "linux"
                          and child.get("platform", {}).get("architecture") == "amd64"]
            if not candidates:
                raise ValueError("No linux/amd64 manifest")
            for child in candidates:
                manifest(repository, child["digest"])
        else:
            for blob in [value["config"], *value["layers"]]:
                fetch(repository, "blobs", blob["digest"])

    def seed(image):
        first = image.split("/")[0]
        if "/" in image and ("." in first or ":" in first or first == "localhost"):
            registry, name = image.split("/", 1)
        else:
            registry, name = "docker.io", image
        if "@" in name:
            name, reference = name.split("@", 1)
            name = name.split(":", 1)[0]
        elif ":" in name:
            name, reference = name.rsplit(":", 1)
        else:
            reference = "latest"
        if registry == "docker.io" and "/" not in name:
            name = "library/" + name
        repository = PREFIXES[registry] + "/" + name
        manifest(repository, reference)

    failures = []
    with ThreadPoolExecutor(max_workers=4) as pool:
        pending = {pool.submit(seed, image): image for image in sorted(images)}
        for number, future in enumerate(as_completed(pending), 1):
            image = pending[future]
            try:
                future.result()
                print(f"{number}/{len(images)} cached {image}", flush=True)
            except Exception as error:
                failures.append(image)
                print(f"{number}/{len(images)} FAILED {image}: {error}", flush=True)
    print(f"Cached {len(images) - len(failures)}/{len(images)} image references")
    if failures:
        sys.exit(1)


if __name__ == "__main__":
    main()
