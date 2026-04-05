# Personal Development Management Dashboard

Local development environment using Docker Compose with Hono (Node.js) backend and Vite frontend.

---

## Prerequisites

- [Docker](https://docs.docker.com/get-docker/) (v24+)
- [Docker Compose](https://docs.docker.com/compose/install/) (v2+ — included with Docker Desktop)

---

## Setup

1. Copy the environment file:
   ```bash
   cp .env.example .env
   ```

2. Start all services:
   ```bash
   docker compose up -d
   ```

   On first run, Docker will build both images. This takes a minute or two.

---

## Access

| Service  | URL                        |
|----------|----------------------------|
| Frontend | http://localhost:5173      |
| API      | http://localhost:3001      |
| Health   | http://localhost:3001/health |

---

## Common Commands

```bash
# View live logs for all services
docker compose logs -f

# View logs for a single service
docker compose logs -f backend
docker compose logs -f frontend

# Restart a single service
docker compose restart backend
docker compose restart frontend

# Stop all services (keeps containers)
docker compose stop

# Stop and remove containers
docker compose down

# Rebuild images after dependency changes (e.g. package.json updated)
docker compose up -d --build
```

---

## Hot Reload

Source files are mounted directly into the containers:

- `./backend/src` -> `/app/src` (backend watches this via `npm run dev`)
- `./frontend/src` -> `/app/src` (Vite HMR picks up changes automatically)

Editing files in `src/` on your host machine will trigger live reload without restarting containers.

---

## Data

The SQLite database is stored at:

```
./backend/data/dashboard.db
```

This path is mounted into the backend container at `/app/data/dashboard.db` and persists across container restarts.

### Backup

```bash
cp ./backend/data/dashboard.db ./backend/data/dashboard.db.backup-$(date +%Y%m%d%H%M%S)
```
