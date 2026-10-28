import hashlib
import json
import unittest

from protect import Manifest, PIN, Registry, image_location, protect, select_images


def pod(image, phase="Running", digest=None):
    return {
        "kind": "Pod",
        "spec": {"containers": [{"image": image}]},
        "status": {
            "phase": phase,
            "containerStatuses": [{"imageID": digest}] if digest else [],
        },
    }


def revision(number, image, kind="ReplicaSet"):
    result = {
        "kind": kind,
        "metadata": {
            "namespace": "default",
            "ownerReferences": [{"uid": "workload", "controller": True}],
            "annotations": {"deployment.kubernetes.io/revision": str(number)},
        },
        "spec": {"template": {"spec": {"containers": [{"image": image}]}}},
    }
    if kind == "ControllerRevision":
        result["revision"] = number
        result["data"] = result.pop("spec")
    return result


def manifest(repository, value):
    body = json.dumps({"test": value}).encode()
    return Manifest(
        repository,
        "sha256:" + hashlib.sha256(body).hexdigest(),
        "application/vnd.oci.image.manifest.v1+json",
        body,
    )


class FakeRegistry:
    def __init__(self):
        self.images = {
            ("dockerhub/library/app", "current"): manifest(
                "dockerhub/library/app", "current"
            )
        }
        self.tags = {
            "dockerhub/library/app": {"current", "release-v1", "retain-" + "0" * 64}
        }
        self.writes = []
        self.fail_pin = False
        self.fail_list = False
        self.fail_complete = False

    def manifest(self, repo, ref):
        return self.images[(repo, ref)]

    def ensure_complete(self, image):
        if self.fail_complete:
            raise ValueError("Incomplete image blobs")

    def pin(self, image):
        if self.fail_pin:
            raise ValueError("Failed pin write")
        tag = "retain-" + image.digest.split(":")[1]
        self.writes.append(("pin", image.repository, tag))
        self.tags[image.repository].add(tag)

    def listing(self, path, key):
        if self.fail_list:
            raise ValueError("Catalog unavailable")
        if key == "repositories":
            return list(self.tags)
        repo = path.removeprefix("/v2/").split("/tags/list")[0]
        return list(self.tags[repo])

    def unpin(self, repo, tag):
        assert PIN.fullmatch(tag)
        self.writes.append(("unpin", repo, tag))
        self.tags[repo].remove(tag)


POLICY = {
    "registry": "https://nas:5050",
    "mirrors": {"docker.io": "dockerhub", "ghcr.io": "ghcr"},
    "rollbackVersions": 3,
    "systemImages": [],
}


class ProtectionTests(unittest.TestCase):
    def test_old_running_digest_survives_beyond_rollback_window(self):
        old = "ghcr.io/example/app@sha256:" + "a" * 64
        items = [revision(n, f"example/app:v{n}") for n in range(1, 8)]
        items += [
            pod("example/app:latest", digest=old),
            pod("example/app:completed", "Succeeded"),
        ]
        images = select_images(items, 3)
        self.assertIn(old, images)
        self.assertIn("example/app:latest", images)
        self.assertEqual(
            {f"example/app:v{n}" for n in range(4, 8)}, {x for x in images if ":v" in x}
        )
        self.assertNotIn("example/app:completed", images)

    def test_stateful_history_and_suspended_cronjob_templates(self):
        items = [
            revision(n, f"postgres:{n}", "ControllerRevision") for n in range(1, 7)
        ]
        items += [
            {
                "kind": "CronJob",
                "spec": {
                    "suspend": True,
                    "jobTemplate": {
                        "spec": {
                            "template": {
                                "spec": {"initContainers": [{"image": "busybox:old"}]}
                            }
                        }
                    },
                },
            }
        ]
        self.assertEqual(
            select_images(items, 3),
            {"postgres:3", "postgres:4", "postgres:5", "postgres:6", "busybox:old"},
        )

    def test_normalize_docker_and_direct_private_references(self):
        self.assertEqual(
            image_location("busybox:1", POLICY), ("dockerhub/library/busybox", "1")
        )
        self.assertEqual(
            image_location("nas:5050/private/app:feature-x", POLICY),
            ("private/app", "feature-x"),
        )
        self.assertEqual(
            image_location("ghcr.io/a/b:main@sha256:abcd", POLICY),
            ("ghcr/a/b", "sha256:abcd"),
        )

    def test_empty_snapshot_or_failed_image_preserves_every_pin(self):
        for items in ([], [pod("app:missing")]):
            registry = FakeRegistry()
            with self.assertRaises((ValueError, KeyError)):
                protect(items, POLICY, registry)
            self.assertEqual(registry.writes, [])

    def test_incomplete_cached_image_does_not_unpin(self):
        registry = FakeRegistry()
        registry.fail_complete = True
        with self.assertRaises(ValueError):
            protect([pod("app:current")], POLICY, registry)
        self.assertEqual(registry.writes, [])

    def test_failed_pin_write_preserves_old_protection(self):
        registry = FakeRegistry()
        registry.fail_pin = True
        with self.assertRaises(ValueError):
            protect([pod("app:current")], POLICY, registry)
        self.assertFalse(any(action == "unpin" for action, *_ in registry.writes))

    def test_catalog_failure_after_new_pins_does_not_remove_old_ones(self):
        registry = FakeRegistry()
        registry.fail_list = True
        with self.assertRaises(ValueError):
            protect([pod("app:current")], POLICY, registry)
        self.assertEqual([action for action, *_ in registry.writes], ["pin"])
        self.assertIn("retain-" + "0" * 64, registry.tags["dockerhub/library/app"])

    def test_success_adds_new_protection_before_removing_only_stale_pin_tags(self):
        registry = FakeRegistry()
        result = protect([pod("app:current")], POLICY, registry)
        self.assertEqual([action for action, *_ in registry.writes], ["pin", "unpin"])
        self.assertIn("release-v1", registry.tags["dockerhub/library/app"])
        self.assertEqual(result["protectedDigests"], 1)

    def test_dry_run_makes_no_tag_changes(self):
        registry = FakeRegistry()
        result = protect([pod("app:current")], POLICY, registry, dry_run=True)
        self.assertEqual(registry.writes, [])
        self.assertEqual(result["stalePins"], 1)

    def test_unpin_cannot_delete_a_manifest_by_digest_or_an_ordinary_tag(self):
        registry = Registry("http://localhost:5000", "", "")
        calls = []
        registry.request = lambda *args: calls.append(args) or (202, b"", {})
        for reference in ("release-v1", "sha256:" + "a" * 64, "retain-not-a-digest"):
            with self.assertRaises(ValueError):
                registry.unpin("private/app", reference)
        self.assertEqual(calls, [])
        tag = "retain-" + "a" * 64
        registry.unpin("private/app", tag)
        self.assertEqual(calls, [("DELETE", "/v2/private/app/manifests/" + tag)])


if __name__ == "__main__":
    unittest.main()
