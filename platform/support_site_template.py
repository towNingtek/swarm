"""Per-tenant choice of workspace template (metadata only).

The template is placed once, when the site is provisioned (see
site/site_templates.py). Changing the choice afterwards only affects a
later rebuild; it never rewrites an existing workspace. Missing choice means the
platform default.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import _site_path  # noqa: E402,F401  (site/ on sys.path)

from support_core import InvalidInput

DEFAULT_TEMPLATE = 'swarm'


def available():
    import site_templates

    return site_templates.list_templates()


class SiteTemplateChoice:
    def __init__(self, core, *, default=DEFAULT_TEMPLATE):
        self.core = core
        if default not in {t['id'] for t in available()}:
            raise ValueError('default template is not available')
        self.default = default
        with core._connect() as conn:
            conn.execute('CREATE TABLE IF NOT EXISTS support_site_templates('
                         'tenant_id TEXT PRIMARY KEY, template_id TEXT NOT NULL, '
                         'updated_at INTEGER NOT NULL)')

    def get(self, tenant_id):
        with self.core._connect() as conn:
            row = conn.execute('SELECT template_id FROM support_site_templates WHERE tenant_id=?',
                               (tenant_id,)).fetchone()
        return row['template_id'] if row else self.default

    def all(self):
        with self.core._connect() as conn:
            return {r['tenant_id']: r['template_id'] for r in
                    conn.execute('SELECT tenant_id, template_id FROM support_site_templates')}

    def set(self, admin, tenant_id, template_id):
        self.core._admin(admin)
        if not isinstance(template_id, str) or template_id not in {t['id'] for t in available()}:
            raise InvalidInput('unknown template')
        with self.core._connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            if not conn.execute('SELECT 1 FROM tenants WHERE id=?', (tenant_id,)).fetchone():
                raise InvalidInput('unknown tenant')
            conn.execute('INSERT INTO support_site_templates VALUES (?,?,?) ON CONFLICT(tenant_id) '
                         'DO UPDATE SET template_id=excluded.template_id, updated_at=excluded.updated_at',
                         (tenant_id, template_id, int(time.time())))
