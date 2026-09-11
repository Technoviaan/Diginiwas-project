# Deploying Niwas AI to a VPS

Docker Compose runs the API behind **Caddy**, which gets and renews HTTPS
certificates automatically. If your server already runs nginx, see
[Already running nginx?](#already-running-nginx).

```
Internet ──HTTPS──► Caddy  (ports 80/443, automatic certificates)
                      │
                      ▼
                    api container  (uvicorn, 1 worker, 127.0.0.1:8000 only)
                      │
          ┌───────────┴────────────┐
          ▼                        ▼
     OpenAI API           DigiNiwas properties API
```

## Before you start

- **A VPS** running Ubuntu 22.04 or 24.04. 1 vCPU and 1 GB RAM is plenty:
  the API mostly waits on OpenAI.
- **A domain or subdomain** for the API, such as `api.diginiwas.com`, with a
  DNS **A record** pointing at the server's IP. Caddy can't get a certificate
  until that resolves.
- **SSH access** to the server.
- **A new OpenAI key for production.** The one in your local `.env` appeared
  in a chat transcript, so don't reuse it. Also set a monthly **spend limit**
  in the OpenAI dashboard: the rate limit caps each user, but only OpenAI can
  cap the total.

## 1. Prepare the server (once)

```bash
ssh user@YOUR_SERVER_IP
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER
exit
```

Log back in so the `docker` group applies, then open the firewall:

```bash
sudo ufw allow OpenSSH
sudo ufw allow 80
sudo ufw allow 443
sudo ufw enable
```

Docker publishes ports around `ufw`, so the compose file binds the API to
`127.0.0.1` only. The only public ports are Caddy's 80 and 443.

## 2. Get the code onto the server

```bash
ssh user@YOUR_SERVER_IP
git clone https://github.com/Technoviaan/Diginiwas-project.git ~/diginiwas
```

If the repository is private, give the server read access first. A GitHub
deploy key (repository Settings → Deploy keys) is the simplest way.

Only committed files reach the server, so your local `.env` never does.
`.env.production`, created in the next step, is ignored by git, so later
`git pull`s never touch it.

## 3. Configure

```bash
ssh user@YOUR_SERVER_IP
cd ~/diginiwas
cp .env.production.example .env.production
chmod 600 .env.production
nano .env.production
```

Set at least:

| Setting | Value |
| --- | --- |
| `DOMAIN` | the API's domain, e.g. `api.diginiwas.com` |
| `OPENAI_API_KEY` | the new production key |
| `CORS_ORIGINS` | the websites that embed the chat, e.g. `["https://diginiwas.com"]` |

## 4. Start

```bash
docker compose --env-file .env.production up -d --build
```

The first build takes a few minutes. Caddy then requests the certificate,
which needs the DNS record and open ports from the steps above.

Every compose command needs `--env-file .env.production`. To save typing:

```bash
echo "alias dc='docker compose --env-file .env.production'" >> ~/.bashrc && source ~/.bashrc
```

## 5. Check it works

```bash
docker compose --env-file .env.production ps        # both services "healthy" / "running"
curl https://YOUR_DOMAIN/health
```

A chat turn:

```bash
curl -s https://YOUR_DOMAIN/v1/chat -H 'content-type: application/json' \
  -d '{"message": "Show me homes for sale in Vijay Nagar", "session_id": "smoke-test"}'
```

Streaming. Events should appear one at a time, not all at the end:

```bash
curl -N https://YOUR_DOMAIN/v1/chat/stream -H 'content-type: application/json' \
  -d '{"message": "3 BHK in Indore", "session_id": "smoke-test"}'
```

Check that the API sees real visitor IPs, because the per-user rate limit
depends on them:

```bash
docker compose --env-file .env.production logs api | grep '"POST /v1/chat' | tail -3
```

Each line starts with the caller's IP, so after a request from your own
machine you should see your public IP. If **every** line shows a Docker
address such as `172.18.0.1`, client IPs are being hidden and all users share
one rate limit. See Troubleshooting.

Swagger is at `https://YOUR_DOMAIN/docs`.

## Day to day

| To… | Run |
| --- | --- |
| Deploy new code | `git pull`, then `docker compose --env-file .env.production up -d --build` |
| Change a setting | edit `.env.production`, then `docker compose --env-file .env.production up -d` |
| Follow the logs | `docker compose --env-file .env.production logs -f api` |
| Restart the API | `docker compose --env-file .env.production restart api` |
| Stop everything | `docker compose --env-file .env.production down` |

`restart` does **not** pick up `.env.production` changes. `up -d` recreates
the container with the new values.

## Already running nginx?

Caddy needs ports 80 and 443. If nginx already has them:

1. Start only the API:
   `docker compose --env-file .env.production up -d --build api`
2. Follow the steps at the top of [`deploy/nginx.conf`](deploy/nginx.conf):
   copy it into `sites-available`, set your domain, reload nginx, and run
   `certbot --nginx` for HTTPS.

That config turns off buffering for the streaming endpoint. Without it,
nginx holds the reply back and the chat appears all at once.

## Troubleshooting

| Symptom | Likely cause | Look at |
| --- | --- | --- |
| `api` keeps restarting | `OPENAI_API_KEY` missing or blank; the API refuses to start without it | `docker compose --env-file .env.production logs api` |
| `caddy` never starts | it waits for `api` to be healthy; fix `api` first | same |
| HTTPS certificate error | DNS doesn't point at this server yet, or ports 80/443 are closed | `docker compose --env-file .env.production logs caddy` |
| Browser: CORS error | the site isn't in `CORS_ORIGINS` | `.env.production` |
| `429 Too many messages` | one IP sent more than `RATE_LIMIT_PER_MINUTE` messages in a minute | raise the limit if real users share an IP |
| Everyone gets `429` together; API logs show `172.x.x.x` for every request | Docker is hiding client IPs. Rootless Docker and Docker Desktop do this; the standard install from step 1 normally doesn't | `docker info` (look for `rootless`) |
| Stream arrives all at once | a proxy in front is buffering (nginx without this config, or a CDN) | the proxy's settings |

## Things to know in production

- **Conversations reset on every deploy and restart.** They're held in the
  API's memory. Fine for a chat widget; move them to Redis when that matters.
- **Exactly one worker, on purpose.** Don't add `--workers`: a second worker
  would split conversations and rate-limit counts. One async worker handles
  many chats at once, because most of each turn is spent waiting on OpenAI.
- **Rate limit:** 20 messages per minute per IP by default. Over the limit,
  clients get `429` with a `Retry-After` header. People behind one office or
  mobile-network IP share a limit.
- **CORS only affects browsers.** Postman, curl and server-to-server calls
  ignore it.
- **Always send a `session_id` per user.** Requests without one share a
  single `default` conversation, so users would see each other's context.
- **Swagger at `/docs` is public.**
- **`/health` only means the process is up.** It doesn't check OpenAI or the
  listings API.
