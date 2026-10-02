"""Platform task library: prompts the operator writes once and hands to sites.

A *template* is a classic swarm report the operator authors on /admin: a name,
the role that runs it, a suggested cron, the prompt (context) and an optional
skill. Templates live in the platform database only.

A *delivery* hands one template to one tenant:

* ``direct``  the platform writes it into the site itself, into the first
              hive that exists. A site without a hive yet keeps the delivery
              pending until the customer creates one.
* ``offer``   the customer sees it on the settings page and decides: add it to
              a hive of their choice, or dismiss it.

Templates marked ``is_default`` are delivered ``direct`` to every newly
provisioned site. Existing sites only ever receive what the operator pushes.

Once written, a schedule belongs to the customer: editing or deleting a
template never rewrites a site, and a delivery is applied at most once. Writes
go through OfficeEditor (revision guarded, atomic, backed up, no symlinks).
"""
from __future__ import annotations

import re
import secrets
import time

from support_core import InvalidInput
from support_site_office import (MAX_CONTEXT, OfficeConflict, OfficeError, _ID,
                                 _SCHEDULE_META, _SECRET, read_office, validate_cron)

MAX_TEMPLATES = 50
MAX_TITLE = 60
MAX_PENDING_PER_TENANT = 30
_DELIVERY_ID = re.compile(r'^d-[0-9a-f]{16}$')


def _clean_template(body):
    name = body.get('name')
    if not isinstance(name, str) or not _ID.match(name) or name in _SCHEDULE_META:
        raise OfficeError('任務代號請用英文小寫、數字、底線或連字號')
    title = body.get('title')
    if not isinstance(title, str) or not title.strip() or len(title.strip()) > MAX_TITLE:
        raise OfficeError(f'任務名稱必填，最多 {MAX_TITLE} 字')
    role = body.get('role')
    if not isinstance(role, str) or not _ID.match(role):
        raise OfficeError('角色代號不正確')
    cron = validate_cron(body.get('cron'))
    skill = body.get('skill') or ''
    if skill and (not isinstance(skill, str) or not _ID.match(skill)):
        raise OfficeError('skill 名稱不正確')
    context = body.get('context')
    if not isinstance(context, str) or not context.strip() or len(context) > MAX_CONTEXT:
        raise OfficeError(f'任務內容必填，最多 {MAX_CONTEXT} 字')
    if _SECRET.search(context) or _SECRET.search(title):
        raise OfficeError('內容看起來含有金鑰，請移除')
    if type(body.get('is_default')) is not bool:
        raise OfficeError('預設旗標不正確')
    return {'name': name, 'title': title.strip(), 'role': role, 'cron': cron,
            'skill': skill, 'context': context.strip(), 'is_default': body['is_default']}


class TaskLibrary:
    def __init__(self, core, office=None, *, clock=time.time):
        self.core = core
        self.office = office  # SiteOffice; only the platform process applies deliveries
        self.clock = clock
        with core._connect() as conn:
            conn.execute('''CREATE TABLE IF NOT EXISTS support_task_templates (
                name TEXT PRIMARY KEY, title TEXT NOT NULL, role TEXT NOT NULL,
                cron TEXT NOT NULL, skill TEXT NOT NULL, context TEXT NOT NULL,
                is_default INTEGER NOT NULL, updated_at INTEGER NOT NULL)''')
            conn.execute('''CREATE TABLE IF NOT EXISTS support_task_deliveries (
                id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
                mode TEXT NOT NULL CHECK(mode IN ('direct','offer')),
                status TEXT NOT NULL CHECK(status IN ('pending','applied','dismissed','failed')),
                name TEXT NOT NULL, title TEXT NOT NULL, role TEXT NOT NULL, cron TEXT NOT NULL,
                skill TEXT NOT NULL, context TEXT NOT NULL,
                hive TEXT, applied_name TEXT, message TEXT,
                created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL)''')
            conn.execute('CREATE INDEX IF NOT EXISTS support_task_deliveries_tenant '
                         'ON support_task_deliveries(tenant_id, status)')

    # -- operator ---------------------------------------------------------

    def templates(self, admin):
        self.core._admin(admin)
        with self.core._connect() as conn:
            rows = conn.execute('SELECT * FROM support_task_templates ORDER BY name').fetchall()
        return [{'name': r['name'], 'title': r['title'], 'role': r['role'], 'cron': r['cron'],
                 'skill': r['skill'], 'context': r['context'],
                 'is_default': bool(r['is_default'])} for r in rows]

    def save_template(self, admin, body, *, original=''):
        self.core._admin(admin)
        value = _clean_template(body)
        if original and (not isinstance(original, str) or not _ID.match(original)):
            raise OfficeError('原任務代號不正確')
        with self.core._connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            names = {r[0] for r in conn.execute('SELECT name FROM support_task_templates')}
            if original and original not in names:
                raise OfficeError('找不到這個任務範本')
            if value['name'] != original and value['name'] in names:
                raise OfficeError('已經有同代號的任務範本')
            if not original and len(names) >= MAX_TEMPLATES:
                raise OfficeError(f'任務範本最多 {MAX_TEMPLATES} 個')
            if original:
                conn.execute('DELETE FROM support_task_templates WHERE name=?', (original,))
            conn.execute('INSERT INTO support_task_templates VALUES (?,?,?,?,?,?,?,?)',
                         (value['name'], value['title'], value['role'], value['cron'],
                          value['skill'], value['context'], int(value['is_default']),
                          int(self.clock())))
        return self.templates(admin)

    def delete_template(self, admin, name):
        self.core._admin(admin)
        with self.core._connect() as conn:
            if not conn.execute('DELETE FROM support_task_templates WHERE name=?',
                                (name,)).rowcount:
                raise OfficeError('找不到這個任務範本')
        return self.templates(admin)

    def push(self, admin, tenant_id, names, mode):
        """Hand templates to a tenant. A snapshot is stored, so later template
        edits never change what this tenant was given."""
        self.core._admin(admin)
        if mode not in ('direct', 'offer'):
            raise OfficeError('推送方式不正確')
        if (not isinstance(names, list) or not names or len(names) > MAX_TEMPLATES
                or len(set(names)) != len(names)
                or not all(isinstance(n, str) and _ID.match(n) for n in names)):
            raise OfficeError('請選擇任務範本')
        with self.core._connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            if not conn.execute('SELECT 1 FROM tenants WHERE id=?', (tenant_id,)).fetchone():
                raise InvalidInput('unknown tenant')
            self._insert(conn, tenant_id, names, mode)
        return self.deliveries(admin, tenant_id)

    def _insert(self, conn, tenant_id, names, mode):
        rows = {r['name']: r for r in conn.execute('SELECT * FROM support_task_templates')}
        missing = [n for n in names if n not in rows]
        if missing:
            raise OfficeError('找不到任務範本：' + '、'.join(missing))
        pending = conn.execute("SELECT COUNT(*) FROM support_task_deliveries WHERE tenant_id=? "
                               "AND status='pending'", (tenant_id,)).fetchone()[0]
        if pending + len(names) > MAX_PENDING_PER_TENANT:
            raise OfficeError('這個客戶待處理的任務太多，請等客戶處理後再推送')
        now = int(self.clock())
        for name in names:
            r = rows[name]
            conn.execute('INSERT INTO support_task_deliveries VALUES '
                         '(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                         ('d-' + secrets.token_hex(8), tenant_id, mode, 'pending', r['name'],
                          r['title'], r['role'], r['cron'], r['skill'], r['context'],
                          None, None, None, now, now))

    def deliveries(self, admin, tenant_id):
        self.core._admin(admin)
        with self.core._connect() as conn:
            rows = conn.execute('SELECT * FROM support_task_deliveries WHERE tenant_id=? '
                                'ORDER BY created_at DESC, id LIMIT 50', (tenant_id,)).fetchall()
        # Status only: what the customer later did with a schedule is theirs.
        return [{'id': r['id'], 'mode': r['mode'], 'status': r['status'], 'name': r['name'],
                 'title': r['title'], 'hive': r['hive'], 'message': r['message'],
                 'created_at': r['created_at']} for r in rows]

    def seed_defaults(self, tenant_id):
        """Trusted provisioning hook: default templates for a brand-new site."""
        with self.core._connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            names = [r[0] for r in conn.execute(
                'SELECT name FROM support_task_templates WHERE is_default=1 ORDER BY name')]
            if names:
                self._insert(conn, tenant_id, names[:MAX_PENDING_PER_TENANT], 'direct')
        return len(names)

    # -- customer ---------------------------------------------------------

    def _tenant(self, actor):
        with self.core._connect() as conn:
            conn.execute('BEGIN')
            return self.core._customer(conn, actor).tenant_id

    def customer_view(self, actor):
        tenant_id = self._tenant(actor)
        with self.core._connect() as conn:
            rows = conn.execute("SELECT * FROM support_task_deliveries WHERE tenant_id=? AND "
                                "(status='pending' OR (status='applied' AND updated_at>?)) "
                                "ORDER BY created_at, id",
                                (tenant_id, int(self.clock()) - 7 * 86400)).fetchall()
        offers, waiting, recent = [], 0, []
        for r in rows:
            if r['status'] == 'pending' and r['mode'] == 'offer':
                offers.append({'id': r['id'], 'title': r['title'], 'name': r['name'],
                               'role': r['role'], 'cron': r['cron'], 'skill': r['skill'],
                               'context': r['context']})
            elif r['status'] == 'pending':
                waiting += 1
            else:
                recent.append({'title': r['title'], 'hive': r['hive'], 'name': r['applied_name']})
        return {'offers': offers, 'waiting_for_hive': waiting, 'recent': recent[-5:]}

    def accept(self, actor, site_host, delivery_id, hive_id):
        tenant_id = self._tenant(actor)
        row = self._delivery(tenant_id, delivery_id, 'offer')
        self._apply(row, site_host, hive_id)

    def dismiss(self, actor, delivery_id):
        tenant_id = self._tenant(actor)
        self._delivery(tenant_id, delivery_id, 'offer')
        self._finish(delivery_id, 'dismissed', None, None, None)

    def _delivery(self, tenant_id, delivery_id, mode):
        if not isinstance(delivery_id, str) or not _DELIVERY_ID.match(delivery_id):
            raise OfficeError('找不到這個任務')
        with self.core._connect() as conn:
            row = conn.execute("SELECT * FROM support_task_deliveries WHERE id=? AND tenant_id=? "
                               "AND mode=? AND status='pending'",
                               (delivery_id, tenant_id, mode)).fetchone()
        if row is None:
            raise OfficeError('這個任務已經處理過了')
        return row

    # -- applying ---------------------------------------------------------

    def _finish(self, delivery_id, status, hive, applied_name, message):
        with self.core._connect() as conn:
            conn.execute("UPDATE support_task_deliveries SET status=?, hive=?, applied_name=?, "
                         "message=?, updated_at=? WHERE id=? AND status='pending'",
                         (status, hive, applied_name, message, int(self.clock()), delivery_id))

    def _apply(self, row, site_host, hive_id):
        """Write one delivery into one hive. Raises OfficeError for the caller."""
        if self.office is None:
            raise OfficeError('這個平台沒有開放寫入站台')
        editor = self.office.editor_for_host(site_host)
        for _attempt in range(3):
            office = read_office(editor.site_root)
            hive = next((h for h in office['hives'] if h['id'] == hive_id and h['exists']), None)
            if hive is None:
                raise OfficeError('找不到這間公司')
            roles = [r['id'] for r in hive['roles']]
            if not roles:
                raise OfficeError('這間公司還沒有任何角色')
            # The suggested role may not exist in this hive: fall back to pm,
            # then the first role, rather than inventing a role.
            role = row['role'] if row['role'] in roles else ('pm' if 'pm' in roles else roles[0])
            taken = {j['name'] for j in hive['schedules']}
            # Truncate the base, never the suffix: cutting `_n` off a long
            # name repeated the same taken name forever. Bounded either way.
            name = row['name']
            for n in range(2, len(taken) + 3):
                if name not in taken:
                    break
                suffix = f'_{n}'
                name = row['name'][:63 - len(suffix)] + suffix
            else:
                raise OfficeError('這間公司的排程名稱都被占用了')
            try:
                editor.save_schedule(office['revision'], hive_id, original='', name=name,
                                     role=role, cron=row['cron'], skill=row['skill'],
                                     context=row['context'])
            except OfficeConflict:
                continue
            self._finish(row['id'], 'applied', hive_id, name, None)
            return name
        raise OfficeError('站台設定一直在變動，稍後再試')

    def apply_pending(self, site_hosts):
        """Background: apply ``direct`` deliveries to the first hive of each
        site. ``site_hosts`` maps tenant_id -> provisioned site host."""
        with self.core._connect() as conn:
            rows = conn.execute("SELECT * FROM support_task_deliveries WHERE mode='direct' "
                                "AND status='pending' ORDER BY created_at, id").fetchall()
        applied = 0
        for row in rows:
            host = site_hosts.get(row['tenant_id'])
            if host is None:
                continue
            try:
                office = self.office.for_host(host) if self.office is not None else None
                hive = next((h for h in (office or {}).get('hives', []) if h['exists']), None)
            except Exception:
                continue  # an unreadable site waits; it never blocks the others
            if hive is None:
                continue  # waits for the customer's first hive
            try:
                self._apply(row, host, hive['id'])
                applied += 1
            except OfficeError as exc:
                self._finish(row['id'], 'failed', hive['id'], None, str(exc)[:200])
            except OSError:
                self._finish(row['id'], 'failed', hive['id'], None, '平台沒有權限寫入站台設定')
            except Exception as exc:
                self._finish(row['id'], 'failed', hive['id'], None, '站台設定無法處理')
                print(f'[tasks] {host}: {type(exc).__name__}', flush=True)
        return applied
