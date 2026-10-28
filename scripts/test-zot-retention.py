#!/usr/bin/env python3
"""Exercise native Zot retention using an isolated disposable Docker registry.

Requires Docker and an already downloaded Zot image. Only generated test data
is modified. Real registry storage and credentials are never mounted.
"""

import argparse
import copy
import hashlib
import json
import pathlib
import subprocess
import tempfile
import time
import urllib.request
import urllib.error
import urllib.parse
import uuid

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--docker", default="docker")
parser.add_argument(
    "--config",
    default=str(pathlib.Path(__file__).resolve().parents[1] / "nas/zot/config.json"),
)
parser.add_argument("--scratch-parent", default=None)
parser.add_argument(
    "--image",
    default="ghcr.io/project-zot/zot-linux-amd64:v2.1.22@sha256:46f688dc26315a35a247e1784368829e66a67bf91de94c7d8d1645044fac1d5b",
)
args = parser.parse_args()
PRODUCTION_POLICY = json.loads(pathlib.Path(args.config).read_text())["storage"][
    "retention"
]
D = args.docker
name = "zot-retention-test-" + uuid.uuid4().hex[:8]
IMAGE = args.image
# Scale time windows down while preserving the production matching/count rules.
retention = copy.deepcopy(PRODUCTION_POLICY)
retention["delay"] = "1s"
retention["dryRun"] = True
for p in retention["policies"]:
    for rule in p.get("keepTags", []) + (
        [p["keepUntagged"]] if p.get("keepUntagged") else []
    ):
        for k in ["pushedWithin", "pulledWithin"]:
            if k in rule:
                rule[k] = "1s"
with tempfile.TemporaryDirectory(
    prefix="zot-retention-test-", dir=args.scratch_parent
) as tmp:
    root = pathlib.Path(tmp)
    (root / "data").mkdir()
    config = {
        "storage": {
            "rootDirectory": "/var/lib/registry",
            "dedupe": True,
            "gc": False,
            "gcDelay": "1s",
            "gcInterval": "1s",
            "retention": retention,
        },
        "http": {"address": "0.0.0.0", "port": "5000", "compat": ["docker2s2"]},
        "log": {"level": "debug"},
    }
    path = root / "config.json"
    path.write_text(json.dumps(config))
    try:
        subprocess.run(
            [
                D,
                "run",
                "-d",
                "--pull",
                "never",
                "--name",
                name,
                "-p",
                "127.0.0.1::5000",
                "-v",
                str(path) + ":/etc/zot/config.json:ro",
                "-v",
                str(root / "data") + ":/var/lib/registry",
                IMAGE,
            ],
            check=True,
            stdout=subprocess.PIPE,
        )
        info = json.loads(subprocess.check_output([D, "inspect", name]))[0]
        port = info["NetworkSettings"]["Ports"]["5000/tcp"][0]["HostPort"]
        base = "http://127.0.0.1:" + port

        def req(method, path, body=None, typ=None):
            headers = {
                "Accept": "application/vnd.oci.image.index.v1+json, application/vnd.oci.image.manifest.v1+json"
            }
            if typ:
                headers["Content-Type"] = typ
            r = urllib.request.urlopen(
                urllib.request.Request(
                    urllib.parse.urljoin(base, path),
                    method=method,
                    data=body,
                    headers=headers,
                ),
                timeout=10,
            )
            return r.status, r.read(), r.headers

        def ready():
            global base
            info = json.loads(subprocess.check_output([D, "inspect", name]))[0]
            base = (
                "http://127.0.0.1:"
                + info["NetworkSettings"]["Ports"]["5000/tcp"][0]["HostPort"]
            )
            for i in range(60):
                try:
                    if req("GET", "/v2/")[0] == 200:
                        return
                except Exception:
                    pass
                time.sleep(0.5)
            raise RuntimeError("Isolated registry not ready")

        def digest(data):
            return "sha256:" + hashlib.sha256(data).hexdigest()

        def upload(repo, data):
            _, _, h = req("POST", "/v2/" + repo + "/blobs/uploads/", b"")
            location = h["Location"]
            req(
                "PUT",
                location + ("&" if "?" in location else "?") + "digest=" + digest(data),
                data,
                "application/octet-stream",
            )
            return digest(data)

        def push(repo, ref, body, typ="application/vnd.oci.image.manifest.v1+json"):
            req("PUT", "/v2/" + repo + "/manifests/" + ref, body, typ)
            return digest(body)

        def image(repo, tag, marker):
            blob = json.dumps(
                {"architecture": "amd64", "os": "linux", "marker": marker}
            ).encode()
            sha = upload(repo, blob)
            body = json.dumps(
                {
                    "schemaVersion": 2,
                    "mediaType": "application/vnd.oci.image.manifest.v1+json",
                    "config": {
                        "mediaType": "application/vnd.oci.image.config.v1+json",
                        "digest": sha,
                        "size": len(blob),
                    },
                    "layers": [],
                }
            ).encode()
            dg = push(repo, tag if tag else digest(body), body)
            return dg, body, sha

        def exists(repo, reference):
            try:
                return req("HEAD", "/v2/" + repo + "/manifests/" + reference)[0] == 200
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    return False
                raise

        ready()
        cache = "dockerhub/retention-check"
        private = "private/retention-check"
        child, child_body, child_blob = image(cache, None, "deployed-child")
        index = json.dumps(
            {
                "schemaVersion": 2,
                "mediaType": "application/vnd.oci.image.index.v1+json",
                "manifests": [
                    {
                        "mediaType": "application/vnd.oci.image.manifest.v1+json",
                        "digest": child,
                        "size": len(child_body),
                        "platform": {"os": "linux", "architecture": "amd64"},
                    }
                ],
            }
        ).encode()
        current = push(
            cache, "deployed", index, "application/vnd.oci.image.index.v1+json"
        )
        pin = "retain-" + current.split(":")[1]
        push(cache, pin, index, "application/vnd.oci.image.index.v1+json")
        req("DELETE", "/v2/" + cache + "/manifests/" + pin)
        assert exists(cache, "deployed") and exists(cache, current), (
            "Deleting a pin removed ordinary image references"
        )
        push(cache, pin, index, "application/vnd.oci.image.index.v1+json")
        obsolete, _, obsolete_blob = image(cache, "obsolete", "obsolete")
        for n in range(8):
            image(cache, "version-" + str(n), "version-" + str(n))
            time.sleep(0.03)
        untagged = []
        for n in range(10):
            dg, _, blob = image(cache, None, "untagged-" + str(n))
            untagged.append((dg, blob))
            time.sleep(0.03)
        release, _, _ = image(private, "release-1", "release")
        alias, _, _ = image(private, "unclassified-release", "alias")
        feature_pin = None
        for n in range(6):
            dg, body, _ = image(private, "feature-" + str(n), "feature-" + str(n))
            if n == 0:
                feature_pin = "retain-" + dg.split(":")[1]
                push(private, feature_pin, body)
            time.sleep(0.03)
        unknown, _, _ = image("unclassified/check", "old", "unknown")
        upload(cache, b"orphan-without-any-manifest")
        before = json.loads(req("GET", "/v2/" + cache + "/tags/list")[1])["tags"]
        time.sleep(2)
        config["storage"]["gc"] = True
        path.write_text(json.dumps(config))
        subprocess.run([D, "restart", name], check=True, stdout=subprocess.PIPE)
        ready()
        time.sleep(5)
        after = json.loads(req("GET", "/v2/" + cache + "/tags/list")[1])["tags"]
        assert set(before) == set(after), "Dry run removed tags"
        assert exists(cache, "obsolete") and exists(private, "feature-1")
        retention["dryRun"] = False
        path.write_text(json.dumps(config))
        subprocess.run([D, "restart", name], check=True, stdout=subprocess.PIPE)
        ready()
        for i in range(45):
            if not exists(cache, "obsolete") and not exists(private, "feature-1"):
                break
            time.sleep(1)
        else:
            raise AssertionError("Expired cache/feature tags were not removed")
        # Polling this manifest would refresh its pull timestamp and keep it alive.
        time.sleep(8)
        assert not exists(cache, untagged[0][0]), (
            "Expired untagged image was not removed"
        )
        assert exists(cache, pin) and exists(cache, current) and exists(cache, child)
        assert req("HEAD", "/v2/" + cache + "/blobs/" + child_blob)[0] == 200
        assert (
            exists(private, "release-1")
            and exists(private, "unclassified-release")
            and exists(private, feature_pin)
        )
        assert exists("unclassified/check", "old")
        print(
            "PASS: dry run preserved tags; native cleanup removed expired cache and feature tags and an untagged image; protection kept deployed index, child and blob; release and unknown namespace retained; deleting a pin left ordinary tags intact"
        )
    except Exception:
        logs = subprocess.check_output(
            [D, "logs", "--tail", "30", name], stderr=subprocess.STDOUT
        ).decode()
        print(logs[-3000:])
        raise
    finally:
        subprocess.run(
            [D, "rm", "-f", name], stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
