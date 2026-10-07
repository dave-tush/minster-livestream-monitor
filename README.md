# minster-livestream-monitor

# testing live server

## Render smoke test

Create a Render **Web Service** for this repository and use `python run.py` as the
start command. The service listens on Render's assigned `PORT`. Once deployed,
open the service's base URL to see the running status page, or visit `/health`
for a JSON health check.

The web service can start without monitoring credentials for deployment checks.
To enable livestream monitoring, configure `MINISTER_NAME` and
`YOUTUBE_API_KEY` in the Render environment variables. See `.env.example` for
the other optional settings.