# Home Assistant container

This is the operator template for the `homeassistant` container recorded on the
gateway.  `automation/install.sh` does not install it: Home Assistant is site
data and is applied separately.

## Apply on the gateway (stage 3)

1. Read the digest of the running image (never guess it):

   ```sh
   docker inspect --format '{{index .RepoDigests 0}}' homeassistant
   ```

2. Export it and render the compose file:

   ```sh
   export HA_IMAGE='ghcr.io/home-assistant/home-assistant@sha256:<digest>'
   docker compose -f ops/homeassistant/docker-compose.yml config
   ```

3. Recreate only after a rollback point exists:

   ```sh
   docker compose -f ops/homeassistant/docker-compose.yml up -d
   ```

## Security boundary

`privileged: true` and `network_mode: host` are the recorded state, kept only
because removing them needs a Home Assistant integration regression.  The file
marks this as `STAGE3(SC-06)`.  `/run/dbus` is removed; the image is pinned by
digest, not by the floating `stable` tag.  The 8123 UI remains blocked from the
WAN by `homeassistant-access-guard.service`.
