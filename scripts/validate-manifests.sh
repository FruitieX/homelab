#!/usr/bin/env bash
# Render every path referenced by a Flux Kustomization under clusters/ and
# validate the result with kubeconform. Used by .github/workflows/validate.yaml;
# run locally from the repo root with:
#   nix-shell --run scripts/validate-manifests.sh
#
# Limitations, since CI has no SOPS key and no cluster:
# - Documents with a top-level `sops:` key (encrypted Secrets, ConfigMaps,
#   routes, ...) are dropped before validation.
# - Flux postBuild variables (${VAR}) get values from cluster-config, their
#   default, or a placeholder; the secret ones are never known here.
# - CRDs missing from the datreeio catalog are skipped (-ignore-missing-schemas).
set -euo pipefail

cd "$(dirname "$0")/.."

out=$(mktemp -d)
trap 'rm -rf "$out"' EXIT

mapfile -t paths < <(
  grep -rhE '^  path: \./' clusters | awk '{print $2}' | sort -u
)

substitutions=()
while IFS='=' read -r key value; do
  substitutions+=(-e "s|\\\$\\{$key(:=[^}]*)?\\}|$value|g")
done < <(yq eval '.data | to_entries | .[] | .key + "=" + .value' \
  clusters/homelab/cluster-config.yaml)

failed=0
for path in "${paths[@]}"; do
  name=${path#./}
  name=${name//\//_}
  if [[ -f "$path/kustomization.yaml" ]]; then
    # Flux builds with load restrictions disabled.
    if ! kustomize build --load-restrictor LoadRestrictionsNone "$path" \
      > "$out/$name.raw.yaml"; then
      echo "::error::kustomize build failed for $path"
      failed=1
      continue
    fi
  else
    # Flux generates a kustomization of all manifests when none exists.
    find "$path" -name '*.yaml' -print0 | sort -z |
      xargs -0 -I{} sh -c 'cat "$1"; printf "\n---\n"' _ {} \
      > "$out/$name.raw.yaml"
  fi
  # Drop SOPS-encrypted documents and the CoreDNS Deployment, which is a
  # partial server-side apply patch over Talos' own (no containers).
  # Then fill Flux postBuild variables: cluster-config values, ${VAR:=default}
  # defaults, or a hostname-shaped placeholder.
  yq eval 'select(. != null and (has("sops") | not) and
      ((.kind == "Deployment" and .metadata.namespace == "kube-system" and
        .metadata.name == "coredns") | not))' "$out/$name.raw.yaml" |
    sed -E "${substitutions[@]}" \
      -e 's/\$\{[_a-zA-Z][_a-zA-Z0-9]*:=([^}]*)\}/\1/g' \
      -e 's/\$\{[_a-zA-Z][_a-zA-Z0-9]*\}/placeholder.example.com/g' \
    > "$out/$name.yaml"
  rm "$out/$name.raw.yaml"
done

echo "Rendered ${#paths[@]} Flux Kustomization paths"

kubeconform \
  -strict \
  -summary \
  -output text \
  -kubernetes-version "${KUBERNETES_VERSION:-1.36.0}" \
  -skip Secret \
  -ignore-missing-schemas \
  -schema-location default \
  -schema-location 'https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/{{.Group}}/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json' \
  "$out"/*.yaml || failed=1

exit "$failed"
