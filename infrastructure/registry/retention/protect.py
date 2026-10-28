#!/usr/bin/env python3
"""Protect desired/running images and available rollout history before Zot GC."""

import argparse
import base64
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import ssl
import subprocess
import urllib.parse
import urllib.request

PIN = re.compile(r"^retain-[0-9a-f]{64}$")
MEDIA_TYPES = (
    "application/vnd.oci.image.index.v1+json",
    "application/vnd.oci.image.manifest.v1+json",
    "application/vnd.docker.distribution.manifest.list.v2+json",
    "application/vnd.docker.distribution.manifest.v2+json",
)
RESOURCES = (
    ("Pod", "pods", "/api/v1/pods"),
    (
        "ReplicationController",
        "replicationcontrollers",
        "/api/v1/replicationcontrollers",
    ),
    ("Deployment", "deployments.apps", "/apis/apps/v1/deployments"),
    ("StatefulSet", "statefulsets.apps", "/apis/apps/v1/statefulsets"),
    ("DaemonSet", "daemonsets.apps", "/apis/apps/v1/daemonsets"),
    ("ReplicaSet", "replicasets.apps", "/apis/apps/v1/replicasets"),
    (
        "ControllerRevision",
        "controllerrevisions.apps",
        "/apis/apps/v1/controllerrevisions",
    ),
    ("CronJob", "cronjobs.batch", "/apis/batch/v1/cronjobs"),
    ("Job", "jobs.batch", "/apis/batch/v1/jobs"),
    ("Cluster", "clusters.postgresql.cnpg.io", "/apis/postgresql.cnpg.io/v1/clusters"),
)


def images_in(value):
    if isinstance(value, dict):
        for key, child in value.items():
            if key in ("image", "imageName") and isinstance(child, str) and child:
                yield child
            else:
                yield from images_in(child)
    elif isinstance(value, list):
        for child in value:
            yield from images_in(child)


def select_images(items, rollbacks):
    """Current templates, active jobs/pods, plus current and previous revisions."""
    images = set()
    histories = defaultdict(list)
    for item in items:
        kind = item["kind"]
        if kind == "Pod":
            if item.get("status", {}).get("phase") in ("Succeeded", "Failed"):
                continue
            images.update(images_in(item.get("spec", {})))
            for key in (
                "containerStatuses",
                "initContainerStatuses",
                "ephemeralContainerStatuses",
            ):
                for status in item.get("status", {}).get(key, []):
                    image_id = status.get("imageID", "").removeprefix(
                        "docker-pullable://"
                    )
                    if "@sha256:" in image_id:
                        images.add(image_id)
        elif kind in ("ReplicaSet", "ControllerRevision"):
            meta = item["metadata"]
            owner = next(
                (o for o in meta.get("ownerReferences", []) if o.get("controller")),
                None,
            )
            if owner:
                revision = item.get(
                    "revision",
                    meta.get("annotations", {}).get(
                        "deployment.kubernetes.io/revision", "0"
                    ),
                )
                histories[
                    (kind, meta.get("namespace", "default"), owner["uid"])
                ].append((int(revision), item))
            elif kind == "ReplicaSet" and item.get("spec", {}).get("replicas", 0):
                images.update(images_in(item.get("spec", {})))
        elif kind == "Job":
            terminal = any(
                c.get("type") in ("Complete", "Failed") and c.get("status") == "True"
                for c in item.get("status", {}).get("conditions", [])
            )
            if not terminal:
                images.update(images_in(item.get("spec", {})))
        else:
            images.update(images_in(item.get("spec", {})))
    for revisions in histories.values():
        for _, item in sorted(revisions, key=lambda value: value[0], reverse=True)[
            : rollbacks + 1
        ]:
            images.update(images_in(item.get("spec", item.get("data", {}))))
    return images


def kubernetes_items(local):
    if local:
        result = subprocess.check_output(
            [
                "kubectl",
                "get",
                ",".join(resource for _, resource, _ in RESOURCES),
                "-A",
                "-o",
                "json",
            ]
        )
        return json.loads(result)["items"]
    service = Path("/var/run/secrets/kubernetes.io/serviceaccount")
    token = (service / "token").read_text().strip()
    context = ssl.create_default_context(cafile=str(service / "ca.crt"))
    items = []
    for kind, _, endpoint in RESOURCES:
        continuation = ""
        while True:
            query = urllib.parse.urlencode({"limit": 500, "continue": continuation})
            request = urllib.request.Request(
                "https://kubernetes.default.svc" + endpoint + "?" + query,
                headers={"Authorization": "Bearer " + token},
            )
            with urllib.request.urlopen(
                request, context=context, timeout=60
            ) as response:
                page = json.load(response)
            items.extend(dict(item, kind=kind) for item in page["items"])
            continuation = page.get("metadata", {}).get("continue", "")
            if not continuation:
                break
    return items


def image_location(image, policy):
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
    own_host = urllib.parse.urlsplit(policy["registry"]).netloc
    if registry != own_host:
        name = policy["mirrors"][registry] + "/" + name
    if not re.fullmatch(r"[a-z0-9._/-]+", name) or not reference:
        raise ValueError("Invalid registry image reference")
    return name, reference


@dataclass(frozen=True)
class Manifest:
    repository: str
    digest: str
    media_type: str
    body: bytes


class Registry:
    def __init__(self, url, username, password, ca_file=None):
        self.url = url.rstrip("/")
        self.context = ssl.create_default_context(cafile=ca_file)
        self.auth = (
            "Basic " + base64.b64encode((username + ":" + password).encode()).decode()
        )

    def request(self, method, path, body=None, media_type=None):
        url = urllib.parse.urljoin(self.url + "/", path)
        if urllib.parse.urlsplit(url)[:2] != urllib.parse.urlsplit(self.url)[:2]:
            raise ValueError("Registry pagination left the configured origin")
        headers = {"Authorization": self.auth, "Accept": ", ".join(MEDIA_TYPES)}
        if media_type:
            headers["Content-Type"] = media_type
        request = urllib.request.Request(url, method=method, data=body, headers=headers)
        with urllib.request.urlopen(
            request, context=self.context, timeout=1800
        ) as response:
            return response.status, response.read(), response.headers

    def listing(self, path, key):
        result = []
        while path:
            _, body, headers = self.request("GET", path)
            result.extend(json.loads(body).get(key) or [])
            link = headers.get("Link", "")
            match = re.search(r'<([^>]+)>;\s*rel="next"', link)
            path = match[1] if match else None
        return result

    def manifest(self, repository, reference):
        _, body, headers = self.request(
            "GET", f"/v2/{repository}/manifests/{reference}"
        )
        digest = "sha256:" + hashlib.sha256(body).hexdigest()
        if (reference.startswith("sha256:") and reference != digest) or headers.get(
            "Docker-Content-Digest", digest
        ) != digest:
            raise ValueError("Manifest digest mismatch")
        media_type = headers.get("Content-Type", "").split(";", 1)[0]
        if media_type not in MEDIA_TYPES:
            raise ValueError("Unsupported image manifest media type")
        return Manifest(repository, digest, media_type, body)

    def ensure_complete(self, manifest):
        value = json.loads(manifest.body)
        if "manifests" in value:
            children = [
                child
                for child in value["manifests"]
                if child.get("platform", {}).get("os") == "linux"
                and child.get("platform", {}).get("architecture") == "amd64"
            ]
            if not children:
                raise ValueError("No linux/amd64 image in index")
            for child in children:
                self.ensure_complete(
                    self.manifest(manifest.repository, child["digest"])
                )
        else:
            for blob in [value["config"], *value["layers"]]:
                _, _, headers = self.request(
                    "HEAD", f"/v2/{manifest.repository}/blobs/{blob['digest']}"
                )
                if int(headers["Content-Length"]) != blob["size"]:
                    raise ValueError("Image blob size mismatch")

    def pin(self, manifest):
        tag = "retain-" + manifest.digest.removeprefix("sha256:")
        status, _, headers = self.request(
            "PUT",
            f"/v2/{manifest.repository}/manifests/{tag}",
            manifest.body,
            manifest.media_type,
        )
        if status != 201 or headers.get("Docker-Content-Digest") != manifest.digest:
            raise ValueError("Registry did not confirm the protected manifest digest")
        return tag

    def unpin(self, repository, tag):
        # Tag deletion is deliberate: deleting by digest would also remove
        # ordinary tags and can destroy an image still needed elsewhere.
        if not PIN.fullmatch(tag):
            raise ValueError("Refusing to remove a non-protection tag")
        status, _, _ = self.request("DELETE", f"/v2/{repository}/manifests/{tag}")
        if status != 202:
            raise ValueError("Registry did not confirm tag deletion")


def protect(items, policy, registry, dry_run=False):
    if not items:
        raise ValueError("Empty Kubernetes inventory; refusing to change protection")
    images = select_images(items, policy["rollbackVersions"]) | set(
        policy["systemImages"]
    )
    desired = {}
    def resolve(location):
        repository, reference = location
        print(
            json.dumps(
                {"action": "check", "repository": repository, "reference": reference}
            ),
            flush=True,
        )
        manifest = registry.manifest(repository, reference)
        registry.ensure_complete(manifest)
        return manifest

    # Resolve and check the entire snapshot before changing any protection.
    # Independent repositories can fetch concurrently; cap NAS load at four.
    locations = {image_location(image, policy) for image in images}
    with ThreadPoolExecutor(max_workers=4) as pool:
        pending = [pool.submit(resolve, location) for location in sorted(locations)]
        for future in as_completed(pending):
            manifest = future.result()
            repository = manifest.repository
            desired[(repository, manifest.digest)] = manifest
    if not desired:
        raise ValueError("No images resolved; refusing to change protection")
    wanted = {
        (repo, "retain-" + digest.removeprefix("sha256:")) for repo, digest in desired
    }
    if not dry_run:
        for manifest in desired.values():
            registry.pin(manifest)
    # Finish discovery before the first removal. Errors leave every old pin.
    existing = set()
    for repository in registry.listing("/v2/_catalog?n=1000", "repositories"):
        for tag in registry.listing(f"/v2/{repository}/tags/list?n=1000", "tags"):
            if PIN.fullmatch(tag):
                existing.add((repository, tag))
    stale = sorted(existing - wanted)
    for repository, tag in stale:
        if not dry_run:
            registry.unpin(repository, tag)
        print(
            json.dumps(
                {
                    "action": "would-unpin" if dry_run else "unpin",
                    "repository": repository,
                    "tag": tag,
                }
            ),
            flush=True,
        )
    summary = {
        "dryRun": dry_run,
        "imageReferences": len(images),
        "protectedDigests": len(desired),
        "stalePins": len(stale),
    }
    print(json.dumps(summary), flush=True)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--local",
        action="store_true",
        help="Use kubectl and the Agents vault instead of pod credentials",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Resolve images and report pin changes without writing tags",
    )
    parser.add_argument(
        "--policy", default=str(Path(__file__).with_name("policy.json"))
    )
    parser.add_argument("--ca-file", default="/etc/zot/ca.crt")
    args = parser.parse_args()
    if args.local:
        item = json.loads(
            subprocess.check_output(
                [
                    "op-agent",
                    "item",
                    "get",
                    "homelab - zot retention",
                    "--vault",
                    "Agents",
                    "--format",
                    "json",
                ]
            )
        )
        fields = {field["label"]: field.get("value", "") for field in item["fields"]}
        username, password = fields["username"], fields["password"]
    else:
        username = Path("/credentials/username").read_text().strip()
        password = Path("/credentials/password").read_text().strip()
    policy = json.loads(Path(args.policy).read_text())
    registry = Registry(policy["registry"], username, password, args.ca_file)
    protect(kubernetes_items(args.local), policy, registry, args.dry_run)


if __name__ == "__main__":
    main()
