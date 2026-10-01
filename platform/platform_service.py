"""Production customer platform: activation, onboarding and site delivery.

Serves ONLY the customer surface on one HTTPS authority (e.g.
https://platform.example.com). Administration is deliberately NOT mounted here:

* Admin and customer cookies would share an origin, widening CSRF/confusion
  surface on the one host every customer is invited to visit.
* Tenant creation and invite issuance are rare, high-authority actions. They run
  through `platform_admin_cli.py` against the same database, so no admin HTTP
  surface is exposed to the public internet at all.

Real provisioning is OFF unless PLATFORM_PROVISION=1. Without it the platform still
activates customers and records intent, but no container, DNS record or proxy
entry is ever created.

Configuration (all required unless noted):
  PLATFORM_ORIGIN   https origin, e.g. https://platform.example.com
  PLATFORM_DB       absolute path to the persistent SQLite database
  PLATFORM_PORT     loopback port to bind (default 8210)
  PLATFORM_PROVISION   "1" to enable REAL site provisioning (default off)
  PLATFORM_MODELS   comma-separated model allowlist (optional)
"""
from __future__ import annotations

import os
import sys
import threading
from pathlib import Path
from urllib.parse import urlsplit

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from support_chat_app import create_customer_chat_app
from support_core import SupportCore
from support_rooms import SupportRooms
from support_model_settings import CustomerModelSettings
from support_site_credentials import SiteCredentials
from support_site_jobs import SupportSiteJobs


def _require(name):
    value = os.environ.get(name, '').strip()
    if not value:
        raise RuntimeError(f'{name} is required')
    return value


def _origin():
    origin = _require('PLATFORM_ORIGIN')
    parsed = urlsplit(origin)
    # The customer app enforces this too; fail here with a clear message rather
    # than deep inside the edge policy at first request.
    if (parsed.scheme != 'https' or parsed.port is not None or parsed.username
            or parsed.path not in ('', '/') or not parsed.hostname):
        raise RuntimeError('PLATFORM_ORIGIN must be an HTTPS DNS origin without port or path')
    return 'https://' + parsed.hostname.lower()


def _database():
    path = Path(_require('PLATFORM_DB'))
    if not path.is_absolute():
        raise RuntimeError('PLATFORM_DB must be absolute')
    if _HERE.parent in path.resolve().parents:
        raise RuntimeError('PLATFORM_DB must live outside the repository')
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def build():
    """Construct the platform. Returns (app, provisioner_or_None)."""
    origin = _origin()
    host = urlsplit(origin).hostname
    core = SupportCore(_database())
    rooms = SupportRooms(core)

    catalog = tuple(m.strip() for m in os.environ.get('PLATFORM_MODELS', '').split(',') if m.strip())
    # No verifier capability: nothing on this deployment may mark a model as
    # tested. Customers select from the allowlist; verification stays unproven.
    settings = CustomerModelSettings(core, catalog=catalog)

    # The provisioner's generated site password is no longer stored or handed
    # out: customers enter with a ticket and may set their own site password.
    # Any row left by the old flow is removed at startup.
    SiteCredentials(core)
    with core._connect() as conn:
        conn.execute('DELETE FROM support_site_credentials')
    # Setting a site password recreates the site container. Off unless the
    # operator enables it: a DSH site whose profile was rewritten at runtime
    # refuses to boot again, so a restart must be proven safe first.
    site_password = None
    if os.environ.get('PLATFORM_SITE_PASSWORD') == '1':
        from support_site_password import SitePassword, dsh_setter

        site_password = SitePassword(core, setter=dsh_setter)

    # One-click entry: the customer never needs the site password. Requires the
    # same secret the provisioner baked into the site.
    entry = None
    entry_secret = os.environ.get('SWARM_ENTRY_SECRET', '').strip()
    if entry_secret:
        from support_site_entry import SiteEntry

        entry = SiteEntry(core, entry_secret=entry_secret)
    # The onboarding Copilot. PLATFORM_COPILOT=fake wires the explicit offline
    # FakeModel so the flow can be exercised end to end; its replies are clearly
    # marked TEST ONLY and it contacts no provider. A real provider/model,
    # entitlement to serve customers, and a spend cap are still undecided
    # (issue #9 P0), so there is deliberately no way to enable one by accident.
    copilot_model = None
    requested = os.environ.get('PLATFORM_COPILOT', '').strip().lower()
    if requested == 'fake':
        from support_model import FakeModel

        copilot_model = FakeModel()
    elif requested == 'gateway':
        # Operator-approved provider. All three values are mandatory: a missing
        # one must stop the service rather than silently disable the assistant
        # or fall back to a different model.
        from support_model_gateway import GatewayModel

        base = os.environ.get('PLATFORM_MODEL_BASE_URL', '').strip()
        key = os.environ.get('PLATFORM_MODEL_KEY', '').strip()
        model_id = os.environ.get('PLATFORM_MODEL_ID', '').strip()
        if not (base and key and model_id):
            raise RuntimeError('PLATFORM_COPILOT=gateway requires PLATFORM_MODEL_BASE_URL, '
                               'PLATFORM_MODEL_KEY and PLATFORM_MODEL_ID')
        copilot_model = GatewayModel(base, key, model_id)
    elif requested:
        raise RuntimeError("PLATFORM_COPILOT accepts 'fake' or 'gateway'; a real provider "
                           'requires an approved model, entitlement and spend cap')

    # Read-only office summary (registry, hives, default model) for the
    # onboarding page. Never reads keys or credentials.
    from support_site_office import SiteOffice
    import _site_path  # noqa: F401
    import common

    office = SiteOffice(common.SITES_ROOT, common.DOMAIN)

    # Platform task library (operator-authored prompts handed to sites) and the
    # site scheduler that runs customer schedules inside each customer's own
    # container. The scheduler is opt-in: without PLATFORM_SITE_SCHEDULER=1 the
    # schedules stay recorded only and the page says so.
    from support_task_library import TaskLibrary

    task_library = TaskLibrary(core, office)
    site_scheduler = None
    if os.environ.get('PLATFORM_SITE_SCHEDULER') == '1':
        from support_site_scheduler import SiteScheduler

        site_scheduler = SiteScheduler(core, office, library=task_library)
        # .swarm/platform-status.md in every site, so the site's own AI knows
        # its model, allowance, schedules and recent results.
        from support_onboarding import starter_allowance
        from support_site_status import SiteStatusWriter

        site_scheduler.status = SiteStatusWriter(
            site_scheduler, lambda conn, tenant: starter_allowance(core, conn, tenant),
            library=task_library)

    # Discord ping to the operator when a conversation needs a human. Both
    # values are required together; the token is read from the environment
    # (a 0600 file via platform-run.sh) and never logged.
    notifier = None
    discord_channel = os.environ.get('PLATFORM_NOTIFY_DISCORD_CHANNEL', '').strip()
    if discord_channel:
        from support_notify import DiscordNotifier

        notifier = DiscordNotifier(os.environ.get('PLATFORM_NOTIFY_DISCORD_TOKEN', ''),
                                   discord_channel,
                                   origin.rstrip('/') + '/admin/customers#chat-section')

    app = create_customer_chat_app(core, rooms, origin, model=copilot_model, notifier=notifier,
                                   site_office=office, task_library=task_library,
                                   site_scheduler=site_scheduler,
                                   model_settings=settings,
                                   site_entry=entry, site_password=site_password,
                                   # The onboarding Copilot reads real state and
                                   # needs no model: it never generates prose.
                                   onboarding_copilot=True)
    from support_ui import install_customer_ui

    install_customer_ui(app)

    provisioner = None
    if os.environ.get('PLATFORM_PROVISION') == '1':
        from support_site_executor import SiteProvisioner

        worker_capability, recovery_capability = object(), object()
        jobs = SupportSiteJobs(core, worker_capability=worker_capability,
                               recovery_capability=recovery_capability)
        starter = None
        relay_url = os.environ.get('SITE_MODEL_RELAY_URL', '').strip()
        if relay_url:
            from support_site_model import SiteModelAccess

            models = [m.strip() for m in os.environ.get('SITE_MODEL_IDS', 'cloud-fast').split(',')
                      if m.strip()]
            starter = (SiteModelAccess(core), relay_url, models)
        from support_site_template import SiteTemplateChoice

        provisioner = SiteProvisioner(jobs, worker_capability, enabled=True, starter_model=starter,
                                      templates=SiteTemplateChoice(core),
                                      task_library=task_library)
    app.state.platform_host = host
    app.state.platform_core = core
    app.state.site_scheduler = site_scheduler
    return app, provisioner


def _provision_loop(provisioner, stop):
    # Real provisioning takes minutes; poll slowly and never overlap runs. A
    # failure is already fenced to reconciliation_required by the job store and
    # must never be retried automatically or crash the service.
    import traceback

    while not stop.wait(15.0):
        try:
            provisioner.run_once(limit=1)
        except Exception:
            # Record why. Swallowing this left operators with a failed job and
            # no way to find the cause. The traceback stays server-side; it is
            # never returned to a customer or an admin page.
            print('[provisioner] run failed:', flush=True)
            traceback.print_exc()


def main():
    import uvicorn

    app, provisioner = build()
    port = int(os.environ.get('PLATFORM_PORT', '8210'))
    stop = threading.Event()
    if provisioner is not None:
        threading.Thread(target=_provision_loop, args=(provisioner, stop), daemon=True).start()
    if app.state.site_scheduler is not None:
        from support_site_scheduler import loop

        threading.Thread(target=loop, args=(app.state.site_scheduler, stop), daemon=True).start()
    try:
        # Loopback only: TLS and the public authority are terminated by nginx.
        uvicorn.run(app, host='127.0.0.1', port=port, reload=False,
                    proxy_headers=False, server_header=False, date_header=False)
    finally:
        stop.set()


if __name__ == '__main__':
    main()
