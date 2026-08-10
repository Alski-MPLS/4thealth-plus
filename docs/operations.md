# Operations Guide

Monitoring, updates, maintenance, and CI/CD for 4THealth in production.

---

## Verifying the Full Stack

In the standard production layout, Gunicorn binds to `127.0.0.1:8100` and Nginx terminates TLS on port 443. Port 5443 is **only used** in direct/dev mode (no Nginx).

```bash
# 1. Confirm both services are active
sudo systemctl status 4thealth
sudo systemctl status nginx

# 2. Confirm Nginx is listening on 443 and Gunicorn is on 8100
sudo ss -tlnp | grep -E '443|8100'

# 3. Test Gunicorn directly (bypasses Nginx)
curl -s http://127.0.0.1:8100/login | grep -i 4thealth

# 4. Test the full stack through Nginx
curl -sk https://localhost/login | grep -i 4thealth

# 5. Tail application logs
sudo journalctl -u 4thealth -f
sudo tail -f /var/log/4thealth/access.log
sudo tail -f /var/log/4thealth/error.log
```

If step 3 succeeds but step 4 fails, the problem is in Nginx. Check with `sudo nginx -t` and `sudo journalctl -u nginx -n 50`.

---

## Monitoring the Background Summary Job

```bash
# Confirm the job started at app boot
sudo journalctl -u 4thealth | grep "summary_job"

# See the final result of the last run
sudo journalctl -u 4thealth | grep "summary_job: done"

# Check current status via the API (requires an active session cookie)
curl -sk --cookie "session=..." https://4thealth.yourdomain.com/api/summary
```

Expected log output after a successful run:

```
summary_job: scheduler started — daily at 01:00 local time
summary_job: starting calculation
summary_job: 24 ADOMs found: [...]
summary_job: done in 277.7s — 900 firewalls, 14767 rules
```

If the job fails, `status` in the `/api/summary` response will be `"error"`. The FMG API token must have read access to `dvmdb`, `pm/pkg`, and `pm/config`.

**Manual refresh after a large change window:**

```bash
curl -sk -X POST --cookie "session=..." https://4thealth.yourdomain.com/api/summary/refresh
# Returns: {"queued": true}
sudo journalctl -u 4thealth -f | grep summary_job
```

---

## Monitoring the Map Cache

```bash
sudo journalctl -u 4thealth | grep "map_cache"
sudo journalctl -u 4thealth | grep "map_cache: done"
curl -sk --cookie "session=..." https://4thealth.yourdomain.com/api/map/status
```

Expected log on a successful run:

```
map_cache: scheduler started — every 24 hours
map_cache: done in 18.4s — 846 devices with coords across 10 ADOMs
```

---

## Optional: Application Health Endpoint

Add to `app/routes/api_routes.py`:

```python
@bp.route("/health")
def health():
    return jsonify({"status": "ok"}), 200
```

```bash
curl -sk https://4thealth.yourdomain.com/api/health
# Expected: {"status": "ok"}
```

---

## Updating the Application

```bash
cd /opt/4thealth
sudo -u 4thealth git pull
sudo -u 4thealth uv sync --extra prod
sudo systemctl restart 4thealth
sudo systemctl status 4thealth
```

---

## Backup

```bash
sudo cp /opt/4thealth/.env /opt/4thealth/.env.bak.$(date +%Y%m%d)
sudo cp /opt/4thealth/users.json /opt/4thealth/users.json.bak.$(date +%Y%m%d)
sudo cp /opt/4thealth/infra_targets.json /opt/4thealth/infra_targets.json.bak.$(date +%Y%m%d)
sudo cp /opt/4thealth/policy_db.json /opt/4thealth/policy_db.json.bak.$(date +%Y%m%d)
gpg --symmetric /opt/4thealth/.env.bak.*
```

---

## SSL Certificate Renewal

**Option A — Let's Encrypt:**

```bash
sudo systemctl status certbot.timer
sudo certbot renew --dry-run
```

**Option B — Manual cert replacement:**

```bash
# Replace cert files, then restart services
sudo systemctl restart nginx
sudo systemctl restart 4thealth
```

**Option C — Self-signed renewal:**

Re-run the `openssl` command from [deployment.md Phase 2.4](deployment.md).

---

## Quick Reference — Common Commands

```bash
# Start / stop / restart service
sudo systemctl start|stop|restart 4thealth

# View live application logs
sudo journalctl -u 4thealth -f

# View access log
sudo tail -f /var/log/4thealth/access.log

# Add a local user
cd /opt/4thealth && sudo -u 4thealth uv run python manage_users.py add <user> --role admin|viewer

# List local users
cd /opt/4thealth && sudo -u 4thealth uv run python manage_users.py list

# Generate a new SECRET_KEY
cd /opt/4thealth && sudo -u 4thealth uv run python manage_users.py secret

# Test LDAP connectivity
ldapsearch -x -H ldaps://your-dc:636 -D "<bind_dn>" -w "<pw>" -b "<base_dn>" "(sAMAccountName=<user>)" dn memberOf

# Check nginx config
sudo nginx -t

# Reload nginx without downtime
sudo nginx -s reload

# Check open ports (production: Nginx on 443/80, Gunicorn on 8100 — NOT 5443)
ss -tlnp | grep -E '443|80|8100'

# Check SELinux denials (RHEL/Rocky)
sudo ausearch -c gunicorn --ts recent
```

---

## CI/CD with GitLab

### Strategy

Use a two-stage approach:

1. Validate code in GitLab CI (`lint`, `tests`, optional security scan).
2. Deploy only from protected branches/tags to production with approval gates.

Recommended branch flow:

- Feature branches → merge request → `main`
- Auto-run CI on merge requests
- Deploy from `main` (continuous) or version tags `v1.x.y` (release-based)

### Server Prerequisites

```bash
# Create a deploy user with least-privilege sudo
sudo useradd --system --create-home --shell /bin/bash deploy
sudo usermod -aG 4thealth deploy

sudo visudo -f /etc/sudoers.d/4thealth-deploy
# Add: deploy ALL=(root) NOPASSWD:/bin/systemctl restart 4thealth,/bin/systemctl status 4thealth

# Install GitLab runner public key
sudo -u deploy mkdir -p /home/deploy/.ssh
sudo -u deploy chmod 700 /home/deploy/.ssh
sudo -u deploy nano /home/deploy/.ssh/authorized_keys
sudo -u deploy chmod 600 /home/deploy/.ssh/authorized_keys
```

### GitLab CI Variables

Configure in **Settings → CI/CD → Variables**:

| Variable | Example |
|---|---|
| `SSH_PRIVATE_KEY` | (masked, protected) |
| `DEPLOY_HOST` | `4thealth-prod.yourdomain.com` |
| `DEPLOY_USER` | `deploy` |
| `DEPLOY_PATH` | `/opt/4thealth` |
| `DEPLOY_PORT` | `22` (optional) |

### `.gitlab-ci.yml`

```yaml
stages:
    - test
    - deploy

default:
    image: python:3.12
    before_script:
        - pip install uv

test:
    stage: test
    script:
        - uv sync --extra prod
        - uv run python -m compileall app manage_users.py wsgi.py
    rules:
        - if: $CI_PIPELINE_SOURCE == "merge_request_event"
        - if: $CI_COMMIT_BRANCH == "main"

deploy_production:
    stage: deploy
    image: alpine:3.20
    before_script:
        - apk add --no-cache openssh-client bash
        - eval "$(ssh-agent -s)"
        - echo "$SSH_PRIVATE_KEY" | tr -d '\r' | ssh-add -
        - mkdir -p ~/.ssh && chmod 700 ~/.ssh
        - ssh-keyscan -p "${DEPLOY_PORT:-22}" "$DEPLOY_HOST" >> ~/.ssh/known_hosts
    script:
        - >
            ssh -p "${DEPLOY_PORT:-22}" "$DEPLOY_USER@$DEPLOY_HOST"
            "cd '$DEPLOY_PATH' &&
            git fetch --all &&
            git checkout main &&
            git reset --hard origin/main &&
            uv sync --extra prod &&
            sudo systemctl restart 4thealth &&
            sudo systemctl status 4thealth --no-pager"
    environment:
        name: production
    rules:
        - if: $CI_COMMIT_BRANCH == "main"
          when: manual
    allow_failure: false
```

Keep `deploy_production` as `when: manual` initially. After confidence, change to automatic on `main` or on signed tags.

### Deployment Sequence

1. Developer merges to `main`.
2. GitLab runs test stage.
3. Deploy job connects over SSH.
4. Server pulls latest commit and runs `uv sync --extra prod`.
5. Server restarts `4thealth`.
6. Job verifies service status and returns pass/fail.

### Optional Improvements

- Add pre-deploy backup of `.env` and `users.json`.
- Add health check gate after restart:
  ```bash
  curl -sk https://4thealth.yourdomain.com/api/health
  ```
- Use GitLab Environments with required approvals for production.
- Add rollback job: store previous commit hash before reset, re-checkout on failure.

### Alternative: Pull-Based Auto-Update

If inbound SSH from GitLab runner is not allowed, run a systemd timer on the server every 5 minutes:

- `git fetch`
- Compare local `HEAD` with `origin/main`
- If changed, run `uv sync --extra prod` and restart service
- Log each run to journald for auditing
