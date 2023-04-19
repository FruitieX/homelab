# Overwriting SOPS encrypted secrets in the configuration

The repository contains a number of secrets that have been encrypted using my GPG key with Mozilla SOPS. You (and your cluster) won't be able to read these encrypted values, and attempting to deploy them as such will probably fail.

Thankfully you can still open these yaml files containing the secrets, and see what keys (as in key/value pair) they contain.
Search for `sops:` to find remaining encrypted secrets.

## The procedure for each secrets file is as follows:

- Open up the secrets file in your text editor and remove the entire `sops:` section at the end.

- Replace all values under `stringData` with their plain-text representations. If you're not sure what the values refer to, search for them in the repo. Remove any secrets that you don't think you will need.

- Re-encrypt the file using your GPG key by running: `sops --encrypt --in-place path/to/secrets-file.yaml`

- Next time you can edit the file using `sops path/to/secrets-file.yaml`