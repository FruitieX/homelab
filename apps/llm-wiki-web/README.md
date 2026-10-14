# llm-wiki-web

Private reading and editing app for the owner's wiki at **https://llm-wiki.fruitiex.org**, on the private Envoy gateway only (LAN/VPN). Source and design: [FruitieX/llm-wiki-web](https://github.com/FruitieX/llm-wiki-web).

- One replica (`Recreate`): the pod owns a git clone of `FruitieX/llm-wiki` on the `llm-wiki-web-data-nfs` claim and pushes the owner's edits to `main`.
- `secret.sops.yaml` holds the GitHub deploy key (write access to `FruitieX/llm-wiki`, titled "llm-wiki-web (homelab)") and the first-login setup token.
- CI in the app repository dispatches `image-update`, which pins the tested digest in `deployment.yaml`.

First sign-in: read the setup token in your own terminal, open the site and choose a password.

```sh
nix-shell
sops --decrypt --extract '["stringData"]["LLM_WIKI_SETUP_TOKEN"]' apps/llm-wiki-web/secret.sops.yaml
```

The clone is disposable: deleting `/data/repo` makes the next start clone afresh. `/data/auth.json` holds the password hash and sessions; delete it to reset the password with the setup token.
