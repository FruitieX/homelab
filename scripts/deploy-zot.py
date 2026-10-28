#!/usr/bin/env python3
"""Deploy the NAS registry from Git and 1Password, without logging secrets."""

import base64
import io
import json
import os
from pathlib import Path
import shlex
import subprocess
import tarfile

ROOT = Path(__file__).resolve().parents[1]
HOST = "nectarine.internal.fruitiex.org"
DESTINATION = "/volume3/docker/zot"


def item(title):
    result = json.loads(subprocess.check_output([
        "op-agent", "item", "get", title, "--vault", "Agents", "--format", "json"
    ]))
    return {field["label"]: field.get("value", "") for field in result["fields"]}


def main():
    nas = item("synology - nectarine - ai-agent")
    registry = item("homelab - zot registry")
    reader = item("homelab - zot node reader")
    retention = item("homelab - zot retention")
    # Reuse the existing upstream read access. Keep private GHCR images behind
    # registry authentication, just like custom builds.
    secret = json.loads(subprocess.check_output([
        "kubectl", "--kubeconfig", str(ROOT / "kubeconfig"), "-n", "default",
        "get", "secret", "ghcr-credentials", "-o", "json"
    ]))
    docker_config = json.loads(base64.b64decode(secret["data"][".dockerconfigjson"]))
    ghcr = docker_config["auths"]["ghcr.io"]
    if "auth" in ghcr:
        username, password = base64.b64decode(ghcr["auth"]).decode().split(":", 1)
    else:
        username, password = ghcr["username"], ghcr["password"]
    files = {
        "compose.yaml": (ROOT / "nas/zot/compose.yaml").read_bytes(),
        "config.json": (ROOT / "nas/zot/config.json").read_bytes(),
        "ca.crt": (ROOT / "nas/zot/ca.crt").read_bytes(),
        "secrets/server.crt": (ROOT / "nas/zot/server.crt").read_bytes(),
        "secrets/server.key": registry["server-key"].encode(),
        "secrets/htpasswd": (
            registry["htpasswd"] + reader["htpasswd"] + retention["htpasswd"]
        ).encode(),
        "secrets/upstreams.json": json.dumps({
            "ghcr.io": {"username": username, "password": password}
        }).encode(),
    }
    archive = io.BytesIO()
    with tarfile.open(fileobj=archive, mode="w") as tar:
        for name, data in files.items():
            entry = tarfile.TarInfo(name)
            entry.size = len(data)
            entry.mode = 0o600 if name.startswith("secrets/") else 0o644
            tar.addfile(entry, io.BytesIO(data))
    # sudo consumes the password line; the child reads the archive from the
    # remaining stdin. Neither passwords nor private keys appear in argv.
    remote = f"""
import io, os, pathlib, subprocess, sys, tarfile
root = pathlib.Path({DESTINATION!r})
root.mkdir(parents=True, exist_ok=True)
(root / 'data').mkdir(exist_ok=True)
(root / 'secrets').mkdir(mode=0o700, exist_ok=True)
os.chmod(root / 'secrets', 0o700)
with tarfile.open(fileobj=io.BytesIO(sys.stdin.buffer.read())) as tar:
    tar.extractall(root)
docker = '/var/packages/ContainerManager/target/usr/bin/docker'
subprocess.run([docker, 'compose', 'run', '--rm', '--no-deps', 'zot', 'verify', '/etc/zot/config.json'], cwd=root, check=True)
subprocess.run([docker, 'compose', 'up', '-d'], cwd=root, check=True)
# Bind-mounted file updates do not reload TLS or authentication in a running
# process. Restart only this NAS service after validating the new config.
subprocess.run([docker, 'compose', 'restart', 'zot'], cwd=root, check=True)
"""
    read_fd, write_fd = os.pipe()
    nas_password = nas["confirmpassword"]
    try:
        os.write(write_fd, (nas_password + "\n").encode())
        os.close(write_fd)
        subprocess.run([
            "sshpass", "-d", str(read_fd), "ssh", "-o", "StrictHostKeyChecking=yes",
            "-o", "ConnectTimeout=10", f"{nas['username']}@{HOST}",
            "sudo -S -p '' /usr/bin/python3 -c " + shlex.quote(remote),
        ], pass_fds=(read_fd,), input=(nas_password + "\n").encode() + archive.getvalue(), check=True)
    finally:
        os.close(read_fd)


if __name__ == "__main__":
    main()
