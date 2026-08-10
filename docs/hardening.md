# Hardening & Security Guide

Goal: Lock down the production environment following security best practices.

---

## File Permissions

### Secure .env

```bash
sudo chmod 640 /opt/4thealth/.env
sudo chown 4thealth:4thealth /opt/4thealth/.env
ls -la /opt/4thealth/.env
# Expected mode: -rw-r-----
```

### Secure Runtime Data Files

```bash
sudo chmod 640 /opt/4thealth/users.json
sudo chown 4thealth:4thealth /opt/4thealth/users.json

sudo chmod 640 /opt/4thealth/infra_targets.json
sudo chown 4thealth:4thealth /opt/4thealth/infra_targets.json

sudo chmod 640 /opt/4thealth/policy_db.json
sudo chown 4thealth:4thealth /opt/4thealth/policy_db.json

sudo chmod 640 /opt/4thealth/app_settings.json
sudo chown 4thealth:4thealth /opt/4thealth/app_settings.json

sudo chmod 640 /opt/4thealth/api_tokens.json
sudo chown 4thealth:4thealth /opt/4thealth/api_tokens.json
```

---

## Rotate Flask SECRET_KEY

```bash
uv run python manage_users.py secret
# Update SECRET_KEY in .env
sudo systemctl restart 4thealth
```

Note: rotating `SECRET_KEY` invalidates all active sessions.

---

## Log Rotation

Create `/etc/logrotate.d/4thealth`:

```conf
/var/log/4thealth/*.log {
    daily
    rotate 30
    compress
    delaycompress
    missingok
    notifempty
    create 0640 4thealth 4thealth
    postrotate
        systemctl kill -s USR1 4thealth
    endscript
}
```

---

## Fail2ban (Brute Force Protection)

```bash
sudo dnf install -y fail2ban
# or
sudo apt-get install -y fail2ban
```

Create `/etc/fail2ban/jail.d/4thealth.conf`:

```ini
[4thealth-login]
enabled   = true
port      = https
filter    = 4thealth-login
logpath   = /var/log/4thealth/access.log
maxretry  = 5
findtime  = 300
bantime   = 3600
```

Create `/etc/fail2ban/filter.d/4thealth-login.conf`:

```ini
[Definition]
failregex = ^<HOST> .* "POST /login .*" 401
```

```bash
sudo systemctl enable fail2ban
sudo systemctl restart fail2ban
```

---

## Nginx Rate Limiting

Add to `nginx.conf` or `conf.d/4thealth.conf`:

```nginx
# In http {}
limit_req_zone $binary_remote_addr zone=login:10m rate=5r/m;

# In server/location
location /login {
    limit_req zone=login burst=3 nodelay;
    proxy_pass http://127.0.0.1:8100;
}
```

---

## SELinux Audit (RHEL/Rocky)

```bash
sudo ausearch -c gunicorn --ts recent
sudo ausearch -c nginx --ts recent
sudo setsebool -P httpd_can_network_connect 1
```

---

## Zone Policy Tab — policy_db.json

The Zone Policy tab requires `policy_db.json` in the project root. It is not committed to the repository.

```bash
# Copy from another server or dev machine
scp policy_db.json deploy@server:/opt/4thealth/policy_db.json

# Or start from the example
sudo -u 4thealth cp policy_db.example.json /opt/4thealth/policy_db.json
```

```bash
sudo chmod 640 /opt/4thealth/policy_db.json
sudo chown 4thealth:4thealth /opt/4thealth/policy_db.json
```

Block direct HTTP access in Nginx (already included in the [deployment.md](deployment.md) config):

```nginx
location /policy_db.json { deny all; return 404; }
```

The file can be added or replaced at runtime — changes take effect immediately on the next request without a service restart.

Backup:

```bash
sudo cp /opt/4thealth/policy_db.json /opt/4thealth/policy_db.json.bak.$(date +%Y%m%d)
```

---

## External API Runtime Files

```bash
sudo -u 4thealth cp /opt/4thealth/app_settings.example.json /opt/4thealth/app_settings.json
sudo -u 4thealth cp /opt/4thealth/api_tokens.example.json  /opt/4thealth/api_tokens.json

sudo chmod 640 /opt/4thealth/app_settings.json
sudo chown 4thealth:4thealth /opt/4thealth/app_settings.json

sudo chmod 640 /opt/4thealth/api_tokens.json
sudo chown 4thealth:4thealth /opt/4thealth/api_tokens.json
```

Block direct HTTP access in Nginx:

```nginx
location /app_settings.json { deny all; return 404; }
location /api_tokens.json   { deny all; return 404; }
```

---

## Map (Beta) — Connectivity

The **app server** requires no internet access — all Leaflet/MarkerCluster JavaScript and CSS are bundled in `app/static/vendor/`.

**Users' browsers** make tile requests to `https://{s}.tile.openstreetmap.org`. If blocked:

| Browser network access | Result |
|---|---|
| Browser can reach `*.tile.openstreetmap.org` on port 443 | Map tiles render normally |
| Browser is blocked | Map shows grey background; device pins and clustering still work |

For fully on-prem tile serving, substitute the `L.tileLayer(...)` URL in `app/static/js/map.js` with a self-hosted tile server.

---

## Master Security Checklist

- [ ] `SECRET_KEY` is a 64-char random hex string
- [ ] `.env` permission is `640`, owner `4thealth`
- [ ] `users.json` permission is `640`, owner `4thealth`
- [ ] `policy_db.json` permission is `640`, owner `4thealth`
- [ ] `app_settings.json` permission is `640`, owner `4thealth`
- [ ] `api_tokens.json` permission is `640`, owner `4thealth`
- [ ] `COOKIE_SECURE=true` in `.env`
- [ ] `FMG_VERIFY_SSL=true` if FMG has a valid cert
- [ ] Nginx HSTS header is present
- [ ] TLS 1.0/1.1 are disabled
- [ ] fail2ban is running and monitoring `/login`
- [ ] Port `8100` is not externally exposed
- [ ] `certs/key.pem` permission is `600`
- [ ] Log rotation is configured
- [ ] Gunicorn is using `gthread` worker class (required for background jobs)
- [ ] Gunicorn `--timeout 130` and Nginx `proxy_read_timeout 140s` are set
- [ ] `SUMMARY_REFRESH_HOUR` is set to a low-traffic window (default `1` = 01:00 local time)
- [ ] `MAP_CACHE_INTERVAL_HOURS` is set appropriately (default `24`)
- [ ] `policy_db.json` is blocked in Nginx (`deny all; return 404`)
- [ ] Zone Policy Edit Database tab is restricted to `admin` role (enforced by `@admin_required`)
- [ ] `app_settings.json` is blocked in Nginx
- [ ] `api_tokens.json` is blocked in Nginx
- [ ] External API is disabled (`external_api_enabled: false`) unless actively needed
- [ ] Token names are descriptive so revocation is unambiguous
- [ ] Unused External API tokens are revoked promptly
- [ ] External API calls originate from a known internal IP
- [ ] Confirm user browsers can reach `tile.openstreetmap.org:443`, or document that map tiles will be unavailable
- [ ] Map tab (`map_view`) is granted to groups that should see it via **Admin → Groups & Permissions**
- [ ] RADIUS shared secret is at least 32 random characters (if RADIUS is enabled — see [authentication.md](authentication.md))
- [ ] AD bind password is strong and unique (if AD is enabled)
