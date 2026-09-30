"""Check the rendered namespace, references and exact read permissions."""

import json
from pathlib import Path
import shutil
import subprocess
import unittest

import yaml


DIRECTORY = Path(__file__).resolve().parent
ROOT = DIRECTORY.parents[3]


class ManifestTests(unittest.TestCase):
    def test_rendered_config_references_and_rbac_match_monitored_services(self):
        command = ["kustomize", "build"] if shutil.which("kustomize") else ["kubectl", "kustomize"]
        # Build the parent too: namespace transformations can break the cross-namespace roles.
        rendered = subprocess.check_output(command + [str(DIRECTORY.parent)], text=True)
        resources = list(yaml.safe_load_all(rendered))
        deployment = next(r for r in resources if r["kind"] == "Deployment"
                          and r["metadata"]["name"] == "deployment-notifier")
        self.assertEqual(deployment["metadata"]["namespace"], "flux-system")
        pod = deployment["spec"]["template"]["spec"]
        maps = {r["metadata"]["name"]: r for r in resources if r["kind"] == "ConfigMap"}
        for volume in pod["volumes"]:
            if "configMap" in volume:
                self.assertIn(volume["configMap"]["name"], maps)
                self.assertEqual(maps[volume["configMap"]["name"]]["metadata"]["namespace"], "flux-system")
        service_config = next(r for r in maps.values() if "services.json" in r["data"])
        services = json.loads(service_config["data"]["services.json"])["services"]
        roles = [r for r in resources if r["kind"] == "Role"]
        permissions = {}
        for role in roles:
            for rule in role["rules"]:
                self.assertEqual(rule["verbs"], ["get"])
                for kind in rule["resources"]:
                    permissions[(role["metadata"]["namespace"], kind)] = set(rule["resourceNames"])
        for service in services:
            self.assertIn(service["deployment"], permissions[(service["namespace"], "deployments")])
            self.assertIn(service["kustomization"], permissions[("flux-system", "kustomizations")])
            flux = yaml.safe_load((ROOT / "clusters/homelab/apps" / (service["kustomization"] + ".yaml")).read_text())
            self.assertTrue(flux["spec"]["wait"])
        for binding in (r for r in resources if r["kind"] == "RoleBinding"):
            self.assertEqual(binding["subjects"][0]["namespace"], "flux-system")
            self.assertTrue(any(r["metadata"]["name"] == binding["roleRef"]["name"]
                                and r["metadata"]["namespace"] == binding["metadata"]["namespace"]
                                for r in roles))
        script = next(r["data"]["notifier.py"] for r in maps.values() if "notifier.py" in r["data"])
        compile(script, "rendered-notifier.py", "exec")
        self.assertEqual(deployment["spec"]["replicas"], 1)
        self.assertEqual(deployment["spec"]["strategy"]["type"], "Recreate")
        self.assertEqual(pod["containers"][0]["env"][0]["valueFrom"]["secretKeyRef"],
                         {"name": "discord-webhook-url", "key": "address"})


if __name__ == "__main__":
    unittest.main()
