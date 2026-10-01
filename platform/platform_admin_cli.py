"""Operator CLI for the production platform: tenants and invites.

Deliberately a CLI rather than an HTTP surface. Tenant creation and invite
issuance are rare, high-authority actions; exposing them on the same public
origin customers visit would put an admin session on the one host every
invitee is asked to open.

Access control is filesystem access to PLATFORM_DB plus the ability to run this
command, not a password this program checks (which it could not do safely).

Usage:
  PLATFORM_DB=/var/lib/swarm/platform/platform.sqlite PLATFORM_ORIGIN=https://platform.example.com \\
      python platform_admin_cli.py add-tenant --site-host acme.example.com
  ... invite --tenant <id> [--ttl 86400]
  ... list
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from urllib.parse import urlsplit

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from support_core import SupportCore

OPERATOR = 'platform-operator'


def _core():
    path = os.environ.get('PLATFORM_DB', '').strip()
    if not path or not Path(path).is_absolute():
        raise SystemExit('PLATFORM_DB must be an absolute path')
    # The verifier accepts only this in-process constant: there is no remote
    # caller and no credential to forge.
    return SupportCore(Path(path), admin_verifier=lambda a: OPERATOR if a is _SENTINEL else None)


_SENTINEL = object()


def _platform_host():
    origin = os.environ.get('PLATFORM_ORIGIN', '').strip()
    host = urlsplit(origin).hostname if origin else None
    if not host:
        raise SystemExit('PLATFORM_ORIGIN must be set, e.g. https://platform.example.com')
    return host.lower()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='command', required=True)
    add = sub.add_parser('add-tenant', help='register a customer and their future site host')
    add.add_argument('--site-host', required=True,
                     help='the host the customer will eventually receive (need not exist yet)')
    invite = sub.add_parser('invite', help='issue a one-time activation link')
    invite.add_argument('--tenant', required=True)
    invite.add_argument('--ttl', type=int, default=86400, help='seconds, 60..604800')
    sub.add_parser('list', help='list tenants (no secrets)')
    build = sub.add_parser('provision', help='queue REAL site provisioning for a tenant')
    build.add_argument('--tenant', required=True)
    build.add_argument('--request-id', required=True,
                       help='idempotency key; replaying the same id never creates a second site')
    starter = sub.add_parser('starter-model',
                             help='give an EXISTING site the platform starter model (rotates its token)')
    starter.add_argument('--tenant', required=True)
    starter.add_argument('--relay-url', required=True, help='e.g. http://172.17.0.1:8212/v1')
    starter.add_argument('--models', default='cloud-fast')
    prep = sub.add_parser('prepare-workspace',
                          help='write the platform guide and place the tenant template into an '
                               'EXISTING site (only creates missing files; never overwrites)')
    prep.add_argument('--tenant', required=True)
    prep.add_argument('--template', help='override the tenant choice (default: its /admin choice)')
    prep.add_argument('--starter-model', default='cloud-fast',
                      help="model named in the guide; 'none' if the site has no starter model")
    prep.add_argument('--dry-run', action='store_true', help='list what would be created')
    iso = sub.add_parser('isolate-site',
                         help='move a site onto the isolated network; its AI may then run any command')
    iso.add_argument('tenant')
    iso.add_argument('--check', action='store_true',
                     help='only probe the site network isolation; change nothing')
    args = parser.parse_args(argv)

    core = _core()
    admin = core.admin_actor(_SENTINEL)
    platform = _platform_host()

    if args.command == 'add-tenant':
        tenant = core.create_tenant(admin, args.site_host, platform_host=platform)
        print(f'tenant   {tenant.id}')
        print(f'site     {tenant.site_host}   (not provisioned yet)')
        print(f'platform {tenant.platform_host}')
    elif args.command == 'invite':
        if not 60 <= args.ttl <= 604800:
            raise SystemExit('--ttl must be between 60 and 604800 seconds')
        token = core.issue_invite(admin, args.tenant, ttl=args.ttl)
        # Printed once. It is stored only as a digest and cannot be recovered.
        print(f'https://{platform}/?token={token}')
        print(f'(valid {args.ttl}s, single use; not recoverable if lost)')
    elif args.command == 'provision':
        # Queue intent only. The running platform service owns the worker
        # capability and performs the actual build; this CLI cannot provision
        # directly, so a stray command can never race the service.
        from support_site_jobs import SupportSiteJobs

        jobs = SupportSiteJobs(core)
        job = jobs.queue(admin, args.tenant, args.request_id)
        print(f"job    {job['job_id']}")
        print(f"site   {job['site_host']}")
        print(f"status {job['status']}")
        if job['status'] == 'queued':
            print('the platform service will build it within ~15s; this is REAL infrastructure')
        else:
            print('already past queue; no new work was created')
    elif args.command == 'starter-model':
        # The site learns only its own relay token; it is written straight into
        # the site's DSH credential store and never printed.
        from support_quota import SupportQuota
        from support_site_model import SiteModelAccess

        with core._connect() as conn:
            row = conn.execute("SELECT site_name FROM support_site_jobs WHERE tenant_id=? "
                               "AND status='succeeded_provisioned'", (args.tenant,)).fetchone()
        if row is None or not row['site_name']:
            raise SystemExit('tenant has no provisioned site')
        import _site_path  # noqa: F401
        import dsh_sitectl

        SupportQuota(core)
        access = SiteModelAccess(core)
        models = [m.strip() for m in args.models.split(',') if m.strip()]
        dsh_sitectl.enable_starter_model(row['site_name'], access.issue(args.tenant),
                                         base_url=args.relay_url, models=models)
        print(f"site   {row['site_name']}: starter model {models[0]} via {args.relay_url}")
        print('budget follows the tenant policy on /admin/customers (disabled = refused)')
    elif args.command == 'prepare-workspace':
        from support_site_template import SiteTemplateChoice

        with core._connect() as conn:
            row = conn.execute("SELECT site_name FROM support_site_jobs WHERE tenant_id=? "
                               "AND status='succeeded_provisioned'", (args.tenant,)).fetchone()
        if row is None or not row['site_name']:
            raise SystemExit('tenant has no provisioned site')
        import _site_path  # noqa: F401
        import dsh_sitectl
        import site_templates

        template = args.template or SiteTemplateChoice(core).get(args.tenant)
        starter = None if args.starter_model == 'none' else args.starter_model
        if args.dry_run:
            values = site_templates.guide_values(starter_model=starter)
            files = {} if template == site_templates.NONE else site_templates.load_template(template, values)[1]
            workspace = dsh_sitectl.site_root(row['site_name']) / 'workspace'
            print(f"site     {row['site_name']}  template {template}")
            print('guide    $DSH_HOME/AGENTS.md (rewrite)')
            for rel in files:
                print(('keep     ' if (workspace / rel).exists() else 'create   ') + rel)
            return
        out = dsh_sitectl.prepare_workspace(row['site_name'], template, starter_model=starter)
        print(f"site     {row['site_name']}  template {out['template']}")
        print(f"created  {len(out['created'])} files; kept {len(out['kept'])} existing")
    elif args.command == 'isolate-site':
        import _site_path  # noqa: F401
        import dsh_sitectl

        if args.check:
            result = dsh_sitectl.verify_site_isolation(dsh_sitectl.DSH_IMAGE)
            print('isolation ok: relay reachable, internet reachable, '
                  f"{len(result['block'])} sampled internal targets blocked")
            return
        with core._connect() as conn:
            row = conn.execute("SELECT site_name FROM support_site_jobs WHERE tenant_id=? "
                               "AND status='succeeded_provisioned'", (args.tenant,)).fetchone()
        if row is None or not row['site_name']:
            raise SystemExit('tenant has no provisioned site')
        dsh_sitectl.isolate_site(row['site_name'])
        print(f"site     {row['site_name']}: on {dsh_sitectl.SITE_NETWORK}, commands unrestricted, "
              'per-site entry key')
    else:
        with core._connect() as conn:
            rows = conn.execute('SELECT id, site_host, platform_host, enabled FROM tenants'
                                ' ORDER BY site_host').fetchall()
        for row in rows:
            state = 'enabled' if row['enabled'] else 'disabled'
            print(f"{row['id']}  {row['site_host']:<40} {state}  via {row['platform_host']}")
        if not rows:
            print('(no tenants)')


if __name__ == '__main__':
    main()
