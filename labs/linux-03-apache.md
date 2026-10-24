# Linux 03 — Install and administer Apache (httpd)

## Objective

Install Apache on Amazon Linux 2023, serve a site from a non-default document root
through a name-based virtual host, harden the basic configuration, and prove the site
is reachable locally and from the Windows server across the VPC.

## Scenario

> **Ticket REQ-1203** — The ASRS status page must be hosted on `asrs-lab-linux`.
> Requirements:
> - Site content lives in `/srv/www/asrs-status`, not `/var/www/html`.
> - Served for host name `status.asrs.internal` on port 80, with its own access and
>   error logs.
> - Requests with any other host name get the default site, which must not reveal
>   the Apache version or allow directory listings.
> - The service must survive a reboot. Configuration changes must be applied without
>   dropping in-flight requests.
> - The Windows server team will check the page from `asrs-lab-windows`.

## Prerequisites

- Session on `asrs-lab-linux`. The OS is stock; httpd is not installed.
- Instances have outbound HTTPS, so package repositories are reachable.
- Optional for task 7: a session on `asrs-lab-windows` and the `LinuxPrivateIp` stack output.

## What you need to accomplish

1. Install Apache from the distribution repositories; enable it at boot and start it.
2. Create the document root with a simple `index.html` that includes the server's
   hostname, owned and permissioned so Apache can read it but not write it.
3. Configure a name-based virtual host for `status.asrs.internal` using that document root
   and dedicated log files under the standard Apache log directory.
4. Ensure the response headers do not expose the Apache version and OS, and that
   directory indexes are not generated for the document root.
5. Validate configuration syntax before every reload and apply changes with a graceful reload.
6. Prove from the Linux server that `status.asrs.internal` returns your page and an
   unknown host name does not.
7. From the Windows server, fetch the page by private IP with the correct `Host` header.

## Commands or tools to research

- `dnf`, `systemctl` (`enable --now`, `reload`, `is-enabled`), `apachectl` / `httpd -t`, `httpd -S`
- `/etc/httpd/conf/httpd.conf`, `/etc/httpd/conf.d/`, `/etc/httpd/conf.modules.d/`
- Directives: `<VirtualHost>`, `ServerName`, `DocumentRoot`, `<Directory>`, `Require`,
  `Options`, `ErrorLog`, `CustomLog`, `ServerTokens`, `ServerSignature`
- `curl` (`-I`, `-H 'Host: ...'`), `ss`, `/var/log/httpd/`
- SELinux file contexts: `getenforce`, `ls -Z`, `semanage fcontext`, `restorecon`
- Windows: `Invoke-WebRequest` (`-Headers`, `-UseBasicParsing`)

## Validation criteria

- `systemctl is-enabled httpd` and `systemctl is-active httpd` both succeed.
- `ss -ltnp` shows httpd listening on port 80.
- `curl -H 'Host: status.asrs.internal' http://localhost/` returns your page.
- `curl -I http://localhost/` shows a `Server:` header of just `Apache`.
- A directory under the document root without an index returns 403, not a listing.
- Requests appear in the dedicated vhost access log, not only in the default one.
- `httpd -t` returns `Syntax OK`; `httpd -S` lists your vhost.
- From Windows, the request returns HTTP 200 with your page content.

## Recovery considerations

- A config syntax error makes `restart` fail and the site go down; `reload` with a bad
  config also fails. Always run the syntax check first.
- Back up `httpd.conf` before editing it; prefer adding files under `conf.d/` so a bad
  change can be removed by deleting one file.
- No firewalld on AL2023 by default and the SG already allows port 80 from the Windows
  SG only — do not open port 80 to the internet; it is not needed.
- SELinux is permissive by default here, so wrong file contexts will log AVC denials but
  not block. Fix contexts anyway; they would break the site on an enforcing host.
- Uninstalling httpd does not remove your content in `/srv/www`; clean it up explicitly.

**Interview angle:** "A site returns 403 after moving the document root — what do you check?"
(Directory `Require`, filesystem permissions along the path, SELinux context.)

[Solution](solutions/linux-03-apache.md) — try it first.
