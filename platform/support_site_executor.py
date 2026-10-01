"""Real site provisioning executor: queued job -> running DSH container + DNS + proxy.

This is the ONLY component permitted to cause external effects for a site job.
It holds the opaque worker capability in-process; no HTTP request can reach it.

Honest boundaries this module maintains:

* A real success is recorded as `succeeded_provisioned`, never as
  `succeeded_simulated`. The two states are distinct on purpose, and neither
  implies the customer may start work (`ready_for_work` stays false until model
  configuration and handoff are separately verified).
* `dsh_sitectl.cmd_create` already performs allocation, container start, DNS,
  proxy and an authenticated self-test, and rolls back what it acquired on
  failure. This module does not reimplement any of that, and does not "clean up"
  on its own beyond what that rollback reports.
* Any failure -> `fail(...)` which always moves the job to
  `reconciliation_required`. External effects may be partial, so a trusted human
  or recovery path decides whether a retry is safe. This module never retries.
* The site name is derived from the tenant's own `site_host`, never from
  customer input, and must be a bare label under the configured DOMAIN.
"""
from __future__ import annotations

import argparse
import os
import sys
import tempfile
import threading
from pathlib import Path

import _site_path  # noqa: E402,F401  (site/ on sys.path)

from support_core import InvalidInput
from support_site_jobs import SupportSiteJobs


class ProvisioningDisabled(RuntimeError):
    """Real provisioning was not explicitly enabled by trusted composition."""


class SiteProvisioner:
    """Claims queued jobs and provisions real DSH sites.

    Construction alone does nothing: `run_once` must be called by trusted
    in-process composition. `enabled` must be passed explicitly so that merely
    importing or constructing this class can never provision anything.
    """

    def __init__(self, jobs, worker_capability, *, enabled=False, domain=None,
                 cpus='2', memory='4g', credentials=None, credential_capability=None,
                 starter_model=None, templates=None, language='繁體中文', task_library=None):
        if type(jobs) is not SupportSiteJobs or type(worker_capability) is not object:
            raise InvalidInput('trusted jobs store and opaque capability required')
        if credentials is not None and credential_capability is None:
            raise InvalidInput('credential store requires its writer capability')
        if enabled is not True:
            raise ProvisioningDisabled(
                'real provisioning must be enabled explicitly by trusted composition')
        import common

        self.jobs = jobs
        self._capability = worker_capability
        # DSH password reset is deliberately disabled upstream, so the password
        # generated here is the site's ONLY password. Without a credential store
        # it is printed and lost, leaving the customer locked out of their site.
        self.credentials = credentials
        self._credential_capability = credential_capability
        self.domain = domain or common.DOMAIN
        self.cpus, self.memory = str(cpus), str(memory)
        # Optional (access, base_url, models): lend new sites the platform
        # starter model through the site model relay.
        self.starter_model = starter_model
        self.templates = templates
        self.language = language
        # Optional: default task templates handed to every new site.
        self.task_library = task_library
        self._lock = threading.Lock()

    def site_name_for(self, site_host):
        """Derive the bare site label from the tenant host; reject anything else.

        Only `<label>.<DOMAIN>` is provisionable. A host that is not under the
        configured domain (including bare `localhost` used by fixtures) is
        refused rather than silently provisioned under the wrong authority.
        """
        suffix = '.' + self.domain
        if not isinstance(site_host, str) or not site_host.endswith(suffix):
            raise InvalidInput(f'site host must be a label under {self.domain}')
        label = site_host[:-len(suffix)]
        from names import validate_name

        return validate_name(label)  # Raises ValueError on reserved/invalid names.

    def _provision(self, lease):
        """Perform the real create. Returns (name, port, dns_record_id, password)."""
        import dsh_sitectl
        import common

        name = self.site_name_for(lease.site_host)
        # Generate explicitly rather than letting cmd_create invent one: an
        # internally generated password is only printed, and cannot be delivered.
        password = common.generate_password()
        # dsh sites store no provider keys; an empty keys file is still required
        # by the shared create signature.
        fd, keys_path = tempfile.mkstemp(prefix='platform-site-', suffix='.env')
        os.close(fd)
        os.chmod(keys_path, 0o600)
        try:
            namespace = argparse.Namespace(
                name=name, model=[], keys_file=keys_path, password=password,
                cpus=self.cpus, memory=self.memory,
                oauth_entries={}, keyless_providers=[], engine='dsh',
            )
            dsh_sitectl.cmd_create(namespace)
        finally:
            os.unlink(keys_path)
        state = dsh_sitectl.load_state(name)
        starter = self._enable_starter_model(lease.tenant_id, name)
        self._prepare_workspace(lease.tenant_id, name, starter)
        self._seed_tasks(lease.tenant_id, name)
        return name, state.get('port'), state.get('dns_record_id'), password

    def _enable_starter_model(self, tenant_id, name):
        """Best effort: the site is live already; without the starter model the
        customer can still connect their own, so a failure is logged, not fatal."""
        if self.starter_model is None:
            return
        import dsh_sitectl

        access, base_url, models = self.starter_model
        try:
            token = access.issue(tenant_id)
            dsh_sitectl.enable_starter_model(name, token, base_url=base_url, models=list(models))
        except Exception as exc:
            print(f'[provision] starter model not enabled for {name}: {type(exc).__name__}',
                  flush=True)
            return None
        return list(models)[0]

    def _prepare_workspace(self, tenant_id, name, starter_model):
        """Best effort, like the starter model: platform guide + tenant template.
        Placed once; never overwrites what already exists in the workspace."""
        import dsh_sitectl

        template = self.templates.get(tenant_id) if self.templates is not None else 'none'
        try:
            dsh_sitectl.prepare_workspace(name, template, starter_model=starter_model,
                                          language=self.language)
        except Exception as exc:
            print(f'[provision] workspace not prepared for {name}: {type(exc).__name__}',
                  flush=True)

    def _seed_tasks(self, tenant_id, name):
        """Best effort: queue the platform's default tasks for this new site.
        They are written once the customer has a hive (see TaskLibrary)."""
        if self.task_library is None:
            return
        try:
            self.task_library.seed_defaults(tenant_id)
        except Exception as exc:
            print(f'[provision] default tasks not queued for {name}: {type(exc).__name__}',
                  flush=True)

    def provision_one(self, job_id, *, lease_seconds=900):
        """Claim and provision a single job. Returns the resulting job view or None.

        The lease is long because a real create pulls images, starts a container
        and waits for an authenticated self-test. If the lease still expires the
        store fences the job to reconciliation_required, which is the correct
        conservative outcome for possibly-partial external effects.
        """
        lease = self.jobs.claim(self._capability, job_id, lease_seconds=lease_seconds)
        if lease is None:
            return None
        try:
            name, port, record_id, password = self._provision(lease)
        except BaseException as exc:
            # Always reconcile: create may have left partial external state, and
            # its own rollback may itself have failed.
            try:
                self.jobs.fail(self._capability, lease, 'executor_failed')
            except Exception:
                pass  # Fencing already recorded by the store; do not mask exc.
            raise
        writer = None
        if self.credentials is not None:
            def writer(conn, tenant_id, site_host, _p=password):
                self.credentials.store(self._credential_capability, conn, tenant_id,
                                       site_host, 'admin', _p)
        return self.jobs.complete_provisioned(self._capability, lease, site_name=name,
                                              site_port=port, dns_record_id=record_id,
                                              credential_writer=writer)

    def run_once(self, *, limit=1, lease_seconds=900):
        """Provision up to `limit` queued jobs; returns the completed job views."""
        if type(limit) is not int or not 1 <= limit <= 50:
            raise InvalidInput('invalid provisioning batch limit')
        done = []
        with self._lock:
            for job_id in self.jobs.queued_job_ids(self._capability)[:limit]:
                result = self.provision_one(job_id, lease_seconds=lease_seconds)
                if result is not None:
                    done.append(result)
        return done
