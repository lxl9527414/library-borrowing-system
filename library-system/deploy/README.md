# Production Deployment Notes

Target domain: `lxlssr.icu`

## Architecture

```text
Cloudflare HTTPS
    |
    v
Nginx :80 (server_name lxlssr.icu)
    |
    v
Spring Boot :18080 (127.0.0.1 only)
    |
    +-- MySQL 8 :3306 (127.0.0.1 only)
    +-- Redis 7 :6379 (127.0.0.1 only)
```

The existing CentOS default Nginx site remains the default server for requests that do not match the library domain.

## Server Layout

| Path | Purpose |
| --- | --- |
| `/opt/library/java` | Temurin JDK 17 |
| `/opt/library/app/library-server.jar` | Application jar |
| `/opt/library/app/library-server.jar.previous` | Previous jar backup |
| `/opt/library/config/library.env` | Runtime secrets |
| `/opt/library/redis` | Redis binaries, config and persistence |
| `/etc/nginx/conf.d/library.conf` | Domain reverse proxy |
| `/etc/systemd/system/library-server.service` | Application service |
| `/etc/systemd/system/redis-library.service` | Redis service |
| `/root/library-credentials.txt` | Server-side credentials file, mode 600 |

Local copy of the production credentials is ignored by Git and stored in `deploy/credentials.local`.

## Service Commands

```bash
systemctl status library-server
systemctl restart library-server
journalctl -u library-server -f

systemctl status redis-library
systemctl status mysqld
systemctl status nginx
```

All four services are enabled for startup:

```text
mysqld redis-library library-server nginx
```

## Update Application

Build locally:

```bash
./build.sh
```

Upload and restart:

```bash
scp backend/target/library-server.jar root@47.98.103.160:/root/library-server.jar.new
ssh root@47.98.103.160 '
  cp -a /opt/library/app/library-server.jar /opt/library/app/library-server.jar.previous
  install -o library -g library -m 0644 /root/library-server.jar.new /opt/library/app/library-server.jar
  systemctl restart library-server
'
```

Rollback:

```bash
ssh root@47.98.103.160 '
  cp -a /opt/library/app/library-server.jar.previous /opt/library/app/library-server.jar
  systemctl restart library-server
'
```

## Cloudflare HTTPS

The server side currently listens on HTTP port 80. HTTPS is completed at Cloudflare after the domain is proxied.

1. Add `lxlssr.icu` in Cloudflare.
2. Keep the imported `A` record pointing to `47.98.103.160`.
3. Set the A record to proxied/orange cloud.
4. Replace the registrar's current HiChina nameservers with the two Cloudflare nameservers.
5. Wait for DNS propagation.
6. In Cloudflare, set SSL/TLS encryption mode to `Flexible` for the simplest origin-HTTP setup, or switch to `Full (strict)` after installing a Cloudflare Origin Certificate.

For production, `Full (strict)` is recommended. The origin certificate can be generated in Cloudflare and installed into the Nginx domain server, after which Nginx listens on both `80` and `443`.

## Security Notes

- MySQL, Redis and the Spring Boot port bind to `127.0.0.1`.
- MySQL X Protocol is disabled.
- `SQL_INIT_MODE` is `always` only on first initialization and is then set to `never`.
- Default demo passwords are not used in production.
- Static assets are served with a one-year immutable cache header and Nginx buffering is disabled for `/assets/`.
- Rotate the values in `/root/library-credentials.txt` after any suspected exposure.
- Keep SSH restricted and consider allowing only trusted IP ranges.
