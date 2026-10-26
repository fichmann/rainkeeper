# Rainkeeper

A local web dashboard for Rain Bird irrigation controllers, with forecast-based rain delays and Docker support.

Rainkeeper checks the forecast at startup and every **six hours** while automation is enabled. If predicted rainfall or rain probability reaches your configured threshold, it applies and verifies a one-day rain delay. Existing longer delays are preserved; dry forecasts let delays expire naturally on the controller.

## Features

- Controller connection status, zone states, rain sensor state, and remaining rain delay
- Configurable forecast thresholds, US ZIP location, and a 1–48 hour forecast window
- Manual rain delays, on-demand checks, and a persistent activity log
- Password-protected NAS dashboard, persistent data, container health checks, and automatic restart

The existing watering programs stay on the controller. Configure zone programs in the Rain Bird app or on the controller.

## Docker on a NAS

Requires Docker with Compose, a compatible Rain Bird Wi-Fi controller accessible on the LAN, and internet access for forecasts. Host networking is intended for Linux hosts such as a NAS. Both ARM64 and x86-64 are supported by the Python slim base image.

```sh
git clone https://github.com/fichmann/rainkeeper.git rainkeeper
cd rainkeeper
cp .env.example .env
python3 setup_dashboard.py
sudo chown -R 10001:10001 data
sudo chmod 700 data
docker compose up -d --build
```

On older installations, use `docker-compose` instead of `docker compose`.

Edit `.env` before starting: set `RAINKEEPER_ALLOWED_HOSTS` to the NAS hostname or IP address **including port 8084**, plus any loopback addresses used for health checks. For example:

```dotenv
RAINKEEPER_ALLOWED_HOSTS=nas.local:8084,127.0.0.1:8084,localhost:8084
```

Open `http://nas.local:8084` using your NAS address and sign in with the dashboard credentials you created. Enter the controller IP address, its device password, your garden's US ZIP code, and forecast thresholds. Enable automation and save.

The container uses host networking, serves port **8084**, runs as UID/GID 10001, has a read-only root filesystem, and limits memory to 256 MB. It stores settings, credentials, and history in the mounted `data/` directory. Back up this directory; it is excluded from Git and the image. The restart policy is `unless-stopped`.

```sh
docker compose ps
docker compose logs --tail=50
docker compose restart
docker compose down
```

Run only one Rainkeeper instance against a controller. Disable any previous scheduler before enabling a new deployment.

## Local development

Python 3.14 is used by the container.

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m uvicorn app:app --host 127.0.0.1 --port 8080
```

Open `http://127.0.0.1:8080`. By default, local development accepts loopback requests without dashboard authentication. Set `RAINKEEPER_AUTH_FILE` to the absolute path of `data/auth.json` to require authentication. `RAINKEEPER_DATA_DIR` overrides the persistent data directory; `RAINKEEPER_ALLOWED_HOSTS` sets the comma-separated Host allowlist.

For macOS login startup, stop the manual server and run `.venv/bin/python install_service.py`. To stop and unregister the service:

```sh
launchctl bootout gui/$(id -u)/local.rainkeeper
```

Remove `~/Library/LaunchAgents/local.rainkeeper.plist` to prevent it from loading at the next login. The server must remain running, awake, and connected to the controller's network. Closing the dashboard does not stop checks.

## Rain-delay rules

Defaults: **2 mm** total precipitation **or 60%** peak hourly rain probability over the next **24 hours**. Either threshold triggers a one-day delay when automation is enabled. Checks run at startup and six hours after each scheduled check completes. Saving settings or pressing the check button runs an additional immediate check.

Existing delays of at least one day are preserved. Rainkeeper never automatically clears a manual delay. Forecast or device failures appear in the activity log and leave existing delays to expire on the controller. A command that times out may still have reached the device; refresh controller status to read back the current state.

Forecasts are estimates, and six-hour checks cannot react immediately to new rain. A physical rain sensor is useful for on-site detection. Rain delays control scheduled watering; Rainkeeper does not stop an already-running manual zone.

## Access and credentials

The NAS dashboard uses HTTP Basic authentication. Plain HTTP does not encrypt credentials; use a trusted LAN and a TLS reverse proxy or VPN for remote access. Do not expose this service directly to the internet.

Device and dashboard passwords are stored in permission-restricted JSON files in `data/`, not encrypted at rest. Device passwords are omitted from API responses. Never commit `data/`, `.env`, backups, or logs. The `/healthz` endpoint reports process health without authentication; `/api/status` requires the configured login.

## Tests

```sh
.venv/bin/python -m unittest -v
```

Tests cover rain delays, preserving manual delays, dry forecasts, disabled automation, forecast failure, and dashboard access controls.

## Integrations

- [pyrainbird](https://github.com/allenporter/pyrainbird) for the local controller API
- [Open-Meteo](https://open-meteo.com/) for precipitation forecasts
- [Zippopotam.us](https://www.zippopotam.us/) for US ZIP coordinates

Compatibility depends on controller model and firmware. This is an independent project, not an official Rain Bird product.
