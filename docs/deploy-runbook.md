# DialogScribe deployment runbook

## Hardened stack cutover

1. Stop the existing stack: `docker compose down`.
2. Apply runtime ownership: `sudo chown -R 10001:10003 ./data`.
3. Create the Hugging Face cache with matching ownership:
   `sudo mkdir -p ./hf-cache && sudo chown -R 10001:10003 ./hf-cache`.
4. Keep `./samples` mounted read-only. Render Vault secrets to
   `./vault/secrets/secrets.json`; startup fails closed if it is missing,
   malformed, or empty.
5. Start the stack and verify health and migration logs.

Production cutover happens through the Task 20 canary; do not promote this
stack directly without that canary.

## Ingress architecture

The application container is not published to the host. The `ingress-guard`
nginx container publishes `192.168.1.48:7860` and proxies to the app over the
Compose network. Its allow list accepts the Synology reverse proxy
(`192.168.1.47`), local operations (`127.0.0.1` and `192.168.1.48`), and
Docker bridge health checks (`172.16.0.0/12`). External access is through
`https://dialogscribe.komolov.synology.me`; direct LAN access is intentionally
rejected with HTTP 403.

To allow another source, add it to `deploy/ingress-guard.conf` and apply the
guard configuration with `docker compose up -d ingress-guard`.

## Rollback

Revert the compose and image changes to the last known-good revision, then
restore ownership for host data and cache:

```sh
sudo chown -R 10001:10003 ./data
sudo chown -R 10001:10003 ./hf-cache
```

Bring the previous stack back with `docker compose up -d`. Preserve the
read-only samples mount and never copy secrets into the image or repository.
