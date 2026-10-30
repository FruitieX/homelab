# Twinkly

Live animations for the Twinkly LED strings, at **https://twinkly.fruitiex.org**. The route is on the private gateway only. Sign-in uses Pocket ID (client `twinkly`, group `family`).

Source and CI: [FruitieX/twinkly](https://github.com/FruitieX/twinkly). Pushes to `main` publish the image and update the digest here automatically. [docs/DEPLOYMENT.md](https://github.com/FruitieX/twinkly/blob/main/docs/DEPLOYMENT.md) covers running it.

- **Networking:** the pod streams UDP (port 7777) and calls HTTP on the strings on the LAN through ordinary pod networking.
- **Replicas:** keep one, with `Recreate`: the server owns the realtime streams.
- **MQTT:** uses the `twinkly-mqtt` broker user and the same `home/lights/twinkly/#` topics as the retired twinkly-mqtt bridge.
- **Secret `twinkly-env`:** holds the OIDC client secret, the MQTT credentials and `TWINKLY_SEED`, which lists the strings to import on first start. After that, strings are edited in the app.
- **Data:** SQLite on `twinkly-data-nfs` with DELETE journaling. PVC pruning is disabled.
