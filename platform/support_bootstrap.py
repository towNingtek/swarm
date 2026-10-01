"""Atomic metadata bootstrap, never a Docker/DNS/service provisioning API.

Requires already initialized SupportCore, SupportRooms and SupportQuota. Only
bootstrap-owned versioned receipt tables are initialized; existing schemas are
never recreated. Receipts contain no credentials. Global client request IDs are permanent and bound to administrator
identity. Canonical defaults/normalized hosts are equivalent payloads. Replays
return the original snapshot, NOT current mutable tenant/room/policy state.
"""
import hashlib
import json
import re
import sqlite3

from support_core import Conflict, InvalidInput, normalize_host
from support_quota import SupportQuota


class SupportBootstrap:
    def __init__(self, core, rooms, quota, *, platform_host=None):
        if rooms.core is not core or quota.core is not core:
            raise ValueError('bootstrap dependencies must share core')
        self.core = core
        # Where customers activate. None keeps the historical single-host
        # behaviour (platform host == the tenant's own site host).
        from support_core import normalize_host
        self.platform_host = None if platform_host is None else normalize_host(platform_host)
        # Check prerequisites without creating or migrating their schemas.
        with core._connect() as conn:
            for table, columns in (
                ('tenants', 'id,site_host,platform_host,enabled,quota_mode'),
                ('rooms', 'id,tenant_id,mode,epoch'),
                ('support_quota_policies', 'tenant_id,mode,monthly_limit'),
            ):
                conn.execute(f'SELECT {columns} FROM {table} LIMIT 0')
            conn.execute('BEGIN IMMEDIATE')
            conn.execute('CREATE TABLE IF NOT EXISTS support_bootstrap_schema (version INTEGER PRIMARY KEY)')
            versions = [r[0] for r in conn.execute('SELECT version FROM support_bootstrap_schema')]
            if versions not in ([], [1]):
                raise Conflict('unsupported bootstrap schema')
            if not versions:
                conn.execute('INSERT INTO support_bootstrap_schema VALUES (1)')
                conn.execute('CREATE TABLE support_bootstrap_requests (request_id TEXT PRIMARY KEY, admin_id TEXT NOT NULL, payload TEXT NOT NULL, result TEXT NOT NULL)')
            conn.execute('SELECT request_id,admin_id,payload,result FROM support_bootstrap_requests LIMIT 0')

    @staticmethod
    def payload(body):
        if type(body) is not dict or not {'client_request_id', 'host'} <= set(body) or set(body) - {'client_request_id', 'host', 'room_mode', 'policy'}:
            raise InvalidInput('invalid bootstrap fields')
        request_id = body['client_request_id']
        if type(request_id) is not str or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,128}', request_id):
            raise InvalidInput('invalid request id')
        host = normalize_host(body['host'])
        mode = body.get('room_mode', 'ai')
        if type(mode) is not str or mode not in ('ai', 'coassist', 'human'):
            raise InvalidInput('invalid room mode')
        policy = body.get('policy', {'mode': 'disabled'})
        if type(policy) is not dict or type(policy.get('mode')) is not str or policy['mode'] not in ('disabled', 'unlimited', 'capped'):
            raise InvalidInput('invalid policy')
        capped = policy['mode'] == 'capped'
        if set(policy) != ({'mode', 'monthly_limit'} if capped else {'mode'}):
            raise InvalidInput('invalid policy fields')
        if capped:
            SupportQuota._integer(policy['monthly_limit'], 0, SupportQuota.MAX_MONTHLY_LIMIT)
        return {'client_request_id': request_id, 'host': host, 'room_mode': mode,
                'policy': dict(policy)}

    def _checkpoint(self, stage):
        """Trusted offline fault-injection seam; never controlled by HTTP input."""

    def create(self, actor, body):
        self.core._admin(actor)
        payload = self.payload(body)
        key = hashlib.sha256(payload['client_request_id'].encode('ascii')).hexdigest()
        tenant_id, room_id = 'bootstrap-tenant-' + key, 'bootstrap-room-' + key
        try:
            with self.core._connect() as conn:
                conn.execute('BEGIN IMMEDIATE')
                self.core._admin(actor)
                old = conn.execute('SELECT admin_id,payload,result FROM support_bootstrap_requests WHERE request_id=?', (payload['client_request_id'],)).fetchone()
                if old:
                    if old['admin_id'] != actor.id or json.loads(old['payload']) != payload:
                        raise Conflict('bootstrap request conflict')
                    return json.loads(old['result'])
                policy = payload['policy']
                legacy_mode = 'unlimited' if policy['mode'] == 'unlimited' else 'metered'
                result = {'client_request_id': payload['client_request_id'],
                          'tenant': {'id': tenant_id, 'host': payload['host'], 'enabled': True,
                                     'quota_mode': legacy_mode},
                          'room': {'id': room_id, 'tenant_id': tenant_id,
                                   'mode': payload['room_mode'], 'epoch': 0},
                          'policy': {**policy, 'monthly_limit': policy.get('monthly_limit')},
                          'metadata_only': True, 'provisioning': 'not_provisioned'}
                conn.execute('INSERT INTO tenants (id,site_host,platform_host,enabled,quota_mode) VALUES (?,?,?,1,?)',
                             (tenant_id, payload['host'],
                              self.platform_host or payload['host'], legacy_mode))
                self._checkpoint('before_room')
                conn.execute('INSERT INTO rooms (id,tenant_id,mode,epoch) VALUES (?,?,?,0)',
                             (room_id, tenant_id, payload['room_mode']))
                self._checkpoint('before_policy')
                conn.execute('INSERT INTO support_quota_policies (tenant_id,mode,monthly_limit) VALUES (?,?,?)',
                             (tenant_id, policy['mode'], policy.get('monthly_limit')))
                self._checkpoint('before_receipt')
                conn.execute('INSERT INTO support_bootstrap_requests (request_id,admin_id,payload,result) VALUES (?,?,?,?)',
                             (payload['client_request_id'], actor.id,
                              json.dumps(payload, sort_keys=True), json.dumps(result, sort_keys=True)))
                self._checkpoint('before_commit')
                return result
        except sqlite3.IntegrityError:
            raise Conflict('bootstrap resource conflict') from None
