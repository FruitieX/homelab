import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import notifier


IMAGE = "ghcr.io/fruitiex/add-bot-rs:main@sha256:" + "a" * 64
NEW_IMAGE = IMAGE.replace("a" * 64, "b" * 64)
SERVICE = {"name": "add-bot", "repository": "FruitieX/add-bot-rs"}


class ReadinessTests(unittest.TestCase):
    def test_flux_requires_current_ready_and_no_reconciliation(self):
        resource = {"metadata": {"generation": 2}, "status": {
            "observedGeneration": 2, "conditions": [
                {"type": "Ready", "status": "True", "observedGeneration": 2},
            ],
        }}
        self.assertTrue(notifier.ready(resource))
        resource["status"]["conditions"][0]["observedGeneration"] = 1
        self.assertFalse(notifier.ready(resource))
        resource["status"]["conditions"][0]["observedGeneration"] = 2
        for condition in ("Reconciling", "Stalled"):
            resource["status"]["conditions"].append({"type": condition, "status": "True"})
            self.assertFalse(notifier.ready(resource))
            resource["status"]["conditions"].pop()

    def test_rollout_rejects_old_missing_unready_and_scaled_down_replicas(self):
        deployment = {"metadata": {"generation": 2}, "spec": {
            "replicas": 1, "template": {"spec": {"containers": [{"name": "bot", "image": IMAGE}]}},
        }, "status": {"observedGeneration": 2, "replicas": 1, "updatedReplicas": 1,
                      "readyReplicas": 1, "availableReplicas": 1}}
        self.assertEqual(notifier.deployed_image(deployment, "bot"), IMAGE)
        for key, value in (("observedGeneration", 1), ("replicas", 2),
                           ("updatedReplicas", 0), ("readyReplicas", 0), ("availableReplicas", 0)):
            changed = copy.deepcopy(deployment)
            changed["status"][key] = value
            self.assertIsNone(notifier.deployed_image(changed, "bot"))
        deployment["spec"]["replicas"] = 0
        self.assertIsNone(notifier.deployed_image(deployment, "bot"))
        deployment["spec"]["replicas"] = 1
        deployment["spec"]["template"]["spec"]["containers"][0]["image"] = "bot:main"
        self.assertIsNone(notifier.deployed_image(deployment, "bot"))


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "state.json"
        self.image = IMAGE
        self.sent = []
        self.labels_error = False
        self.send_error = False
        self.metadata_images = []

    def make_notifier(self):
        def labels(image):
            self.metadata_images.append(image)
            if self.labels_error:
                raise TimeoutError()
            revision = "1" * 40 if image == IMAGE else "2" * 40
            return {"org.opencontainers.image.revision": revision, "org.opencontainers.image.version": "main"}

        def send(payload):
            if self.send_error:
                raise TimeoutError()
            self.sent.append(payload)

        return notifier.Notifier(self.path,
                                 lambda _: {"image": self.image} if self.image else None,
                                 labels,
                                 lambda *args: {"url": "https://github.com/example/compare/1...2",
                                                "changes": ["Remember recent form styles"]}, send)

    def test_baseline_restart_duplicate_config_change_and_rollback(self):
        worker = self.make_notifier()
        worker.check(SERVICE)
        self.assertEqual(self.sent, [])
        self.image = NEW_IMAGE
        worker = self.make_notifier()  # Persistence survives restarts.
        worker.check(SERVICE)
        self.assertEqual(len(self.sent), 1)
        embed = self.sent[0]["embeds"][0]
        self.assertIn("Remember recent form styles", embed["description"])
        self.assertIn("View changes", embed["description"])
        self.assertEqual(embed["fields"][0]["value"], "2222222")
        worker.check(SERVICE)
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(len(self.metadata_images), 2)
        self.image = IMAGE  # Returning to an older digest is also a new deployment.
        worker.check(SERVICE)
        self.assertEqual(len(self.sent), 2)

    def test_failed_delivery_does_not_advance_and_retries(self):
        worker = self.make_notifier()
        worker.check(SERVICE)
        self.image, self.send_error = NEW_IMAGE, True
        with self.assertRaises(TimeoutError):
            worker.check(SERVICE)
        self.assertEqual(json.loads(self.path.read_text())["add-bot"]["image"], IMAGE)
        self.send_error = False
        self.make_notifier().check(SERVICE)
        self.assertEqual(len(self.sent), 1)

    def test_unready_or_unavailable_metadata_is_not_announced(self):
        worker = self.make_notifier()
        worker.check(SERVICE)
        self.image = None
        worker.check(SERVICE)
        self.image, self.labels_error = NEW_IMAGE, True
        with self.assertRaises(TimeoutError):
            worker.check(SERVICE)
        self.assertEqual(self.sent, [])
        self.assertEqual(worker.state["add-bot"]["image"], IMAGE)

    def test_rollout_changed_during_metadata_lookup_is_not_announced(self):
        worker = self.make_notifier()
        worker.check(SERVICE)
        self.image = NEW_IMAGE
        original = worker.labels

        def racing_labels(image):
            result = original(image)
            self.image = None
            return result

        worker.labels = racing_labels
        worker.check(SERVICE)
        self.assertEqual(self.sent, [])


class MetadataTests(unittest.TestCase):
    @patch("notifier.request_json")
    def test_pinned_multiarch_image_skips_attestations(self, request):
        request.side_effect = [
            {"token": "dummy"},
            {"manifests": [
                {"digest": "sha256:attestation", "platform": {"os": "unknown", "architecture": "unknown"}},
                {"digest": "sha256:arm", "platform": {"os": "linux", "architecture": "arm64"}},
                {"digest": "sha256:amd", "platform": {"os": "linux", "architecture": "amd64"}},
            ]},
            {"config": {"digest": "sha256:config"}},
            {"config": {"Labels": {"org.opencontainers.image.revision": "1234567"}}},
        ]
        self.assertEqual(notifier.image_labels(IMAGE, "amd64")["org.opencontainers.image.revision"], "1234567")
        self.assertTrue(request.call_args_list[1].args[0].endswith("sha256:" + "a" * 64))
        self.assertTrue(request.call_args_list[2].args[0].endswith("sha256:amd"))

    @patch("notifier.request_json")
    def test_rollback_fetches_deployed_commit_instead_of_forward_changelog(self, request):
        request.side_effect = [{"status": "behind", "commits": [], "total_commits": 0},
                               {"commit": {"message": "Earlier working version\n\nDetails"}}]
        details = notifier.github_details("FruitieX/add-bot-rs", "1" * 40, "2" * 40)
        self.assertEqual(details["changes"], ["Earlier working version"])
        payload = notifier.discord_payload("add-bot", {"image": IMAGE}, {}, details)
        self.assertIn("Rollback", payload["embeds"][0]["description"])

    @patch("notifier.request_json", side_effect=TimeoutError())
    def test_github_outage_keeps_valid_compare_link(self, _):
        details = notifier.github_details("FruitieX/add-bot-rs", "2" * 40, "1" * 40)
        self.assertIn("/compare/", details["url"])
        self.assertEqual(details["changes"], [])

    @patch("notifier.request_json")
    def test_webhook_waits_for_confirmation_and_preserves_thread(self, request):
        notifier.send_discord("https://discord.com/api/webhooks/dummy?thread_id=123&wait=false", {})
        self.assertIn("wait=true", request.call_args.args[0])
        self.assertIn("thread_id=123", request.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
