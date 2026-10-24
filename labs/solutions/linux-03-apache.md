# Solution — Linux 03: Install and administer Apache (httpd)

## 1. Install, enable, start

```bash
sudo dnf install -y httpd
sudo systemctl enable --now httpd
systemctl is-enabled httpd; systemctl is-active httpd
# enabled
# active

sudo ss -ltnp | grep ':80 '
# LISTEN 0  511  *:80  *:*  users:(("httpd",pid=3120,fd=4),...)

httpd -v
# Server version: Apache/2.4.6x (Amazon Linux)
```

With no content, `curl -I http://localhost/` returns `403 Forbidden`: that is the
`welcome.conf` test page behaviour, not an error in your setup.

## 2. Document root and content

```bash
sudo mkdir -p /srv/www/asrs-status/logs-archive
cat <<EOF | sudo tee /srv/www/asrs-status/index.html
<!doctype html>
<html><head><title>ASRS status</title></head>
<body><h1>ASRS status: OK</h1><p>Served by $(hostname)</p></body></html>
EOF

sudo chown -R root:root /srv/www
sudo find /srv/www -type d -exec chmod 755 {} +
sudo find /srv/www -type f -exec chmod 644 {} +
namei -l /srv/www/asrs-status/index.html
```

Apache workers run as `apache`. They need `r` on files and `x` on every directory in the
path; they should not own or write the content.

SELinux (permissive here, but do it properly):

```bash
getenforce
# Permissive
ls -Zd /srv/www/asrs-status
# unconfined_u:object_r:var_t:s0 /srv/www/asrs-status
```

`semanage` comes from `policycoreutils-python-utils`:

```bash
sudo dnf install -y policycoreutils-python-utils
sudo semanage fcontext -a -t httpd_sys_content_t '/srv/www(/.*)?'
sudo restorecon -Rv /srv/www
ls -Zd /srv/www/asrs-status
# unconfined_u:object_r:httpd_sys_content_t:s0 /srv/www/asrs-status
```

## 3 and 4. Virtual host and hardening

Hardening file (loaded for the whole server):

```bash
cat <<'EOF' | sudo tee /etc/httpd/conf.d/00-hardening.conf
ServerTokens Prod
ServerSignature Off
TraceEnable Off
EOF
```

Default (catch-all) vhost. The first vhost loaded for an address:port is the default
for unmatched host names, hence the `00-` prefix:

```bash
cat <<'EOF' | sudo tee /etc/httpd/conf.d/00-default-vhost.conf
<VirtualHost *:80>
    ServerName default.invalid
    DocumentRoot /var/www/html
    <Directory /var/www/html>
        Options -Indexes
        Require all granted
    </Directory>
</VirtualHost>
EOF
```

Status vhost:

```bash
cat <<'EOF' | sudo tee /etc/httpd/conf.d/10-asrs-status.conf
<VirtualHost *:80>
    ServerName status.asrs.internal
    DocumentRoot /srv/www/asrs-status

    <Directory /srv/www/asrs-status>
        Options -Indexes +FollowSymLinks
        AllowOverride None
        Require all granted
    </Directory>

    ErrorLog  /var/log/httpd/asrs-status-error.log
    CustomLog /var/log/httpd/asrs-status-access.log combined
</VirtualHost>
EOF
```

`Require all granted` is required: the main `httpd.conf` denies everything under `/`
and only grants `/var/www`.

## 5. Validate and reload gracefully

```bash
sudo httpd -t
# Syntax OK
sudo httpd -S
```

Expected excerpt:

```
VirtualHost configuration:
*:80                   is a NameVirtualHost
         default server default.invalid (/etc/httpd/conf.d/00-default-vhost.conf:1)
         port 80 namevhost default.invalid (/etc/httpd/conf.d/00-default-vhost.conf:1)
         port 80 namevhost status.asrs.internal (/etc/httpd/conf.d/10-asrs-status.conf:1)
```

```bash
sudo systemctl reload httpd       # graceful: finishes in-flight requests
```

`systemctl reload httpd` on AL2023 sends a graceful restart (equivalent to
`apachectl graceful`). `restart` drops connections.

## 6. Test locally

```bash
curl -s -H 'Host: status.asrs.internal' http://localhost/
# <h1>ASRS status: OK</h1><p>Served by ip-10-20-1-25.ec2.internal</p> ...

curl -sI http://localhost/ | grep -E '^HTTP|^Server'
# HTTP/1.1 403 Forbidden
# Server: Apache

curl -s -o /dev/null -w '%{http_code}\n' -H 'Host: status.asrs.internal' http://localhost/logs-archive/
# 403

sudo tail -2 /var/log/httpd/asrs-status-access.log
# 127.0.0.1 - - [23/Oct/2026:10:20:01 +0000] "GET / HTTP/1.1" 200 120 "-" "curl/8.x"
```

The default site answers 403 because `/var/www/html` is empty and indexes are off.
Optional: add a neutral `/var/www/html/index.html`.

Alternatively add `127.0.0.1 status.asrs.internal` to `/etc/hosts` and use the name directly.

## 7. Test from Windows

On `asrs-lab-windows` (PowerShell), using the `LinuxPrivateIp` output:

```powershell
$r = Invoke-WebRequest -Uri http://10.20.1.25/ -Headers @{Host='status.asrs.internal'} -UseBasicParsing
$r.StatusCode
# 200
$r.Content
```

`curl.exe -H "Host: status.asrs.internal" http://10.20.1.25/` also works (use `curl.exe`,
not the PowerShell `curl` alias). This works because the Linux SG allows TCP 80 from the
Windows SG, and the Windows SG allows egress 80 to the Linux SG.

Check the Linux access log shows a `10.20.2.x` client address.

## Day-2 admin commands worth knowing

```bash
sudo apachectl configtest
sudo journalctl -u httpd -n 50 --no-pager
sudo tail -f /var/log/httpd/error_log
httpd -M | sort                     # loaded modules
sudo dnf install -y mod_ssl         # TLS, adds conf.d/ssl.conf and a self-signed cert
```

## Cleanup / reset

```bash
sudo systemctl disable --now httpd
sudo rm -f /etc/httpd/conf.d/00-hardening.conf /etc/httpd/conf.d/00-default-vhost.conf \
           /etc/httpd/conf.d/10-asrs-status.conf
sudo semanage fcontext -d '/srv/www(/.*)?'
sudo rm -rf /srv/www
sudo dnf remove -y httpd
sudo rm -rf /etc/httpd /var/log/httpd    # removes leftover config/logs for a clean repeat
```

Keep httpd installed if you are going on to Linux 04, 06 or 10, which reuse it.

## Common mistakes

- Missing `Require all granted` for a new document root → `AH01630: client denied by server configuration`.
- Missing `x` on a parent directory → `AH00035: access ... denied because search permissions are missing`.
- Testing with `curl http://localhost/` and wondering why the vhost is not used — send the `Host` header.
- Vhost file ordering: the alphabetically first vhost is the default for unknown names.
- Using `yum install httpd24` or `amazon-linux-extras` (Amazon Linux 2 only).
- Opening port 80 to `0.0.0.0/0` in the SG — not needed and outside the lab's design.
- Running `restart` instead of `reload` for config changes on a busy server.
