# Glimmer

Live animations for the Twinkly LED strings, at **https://glimmer.fruitiex.org**. The route is on the private gateway only. Sign-in uses Pocket ID (client `glimmer`, group `family`).

Source and CI: [FruitieX/glimmer](https://github.com/FruitieX/glimmer). Pushes to `main` publish the image and update the digest here automatically. [docs/DEPLOYMENT.md](https://github.com/FruitieX/glimmer/blob/main/docs/DEPLOYMENT.md) covers running it.

- **Networking:** the pod streams UDP (port 7777) and calls HTTP on the strings on the LAN through ordinary pod networking.
- **Replicas:** keep one, with `Recreate`: the server owns the realtime streams.
- **MQTT:** uses the `twinkly-mqtt` broker user and the same `home/lights/twinkly/#` topics as the retired twinkly-mqtt bridge.
- **Secret `glimmer-env`:** holds the OIDC client secret, the MQTT credentials, the AI endpoint (the Hermes API) and its key, and `GLIMMER_SEED`, which lists the strings to import on first start. After that, strings are edited in the app.
- **Data:** SQLite on `glimmer-data-nfs` with DELETE journaling. PVC pruning is disabled.
