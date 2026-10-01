"""Onboarding Copilot: reads the customer's real progress and can act for them.

This is not a chat window. It answers from the customer's ACTUAL state and can
perform a narrow set of setup actions on their behalf.

Safety rules that define this component:

* Every action is on an explicit allowlist. There is no general "run this"
  path, no shell, no site access, and nothing that touches another tenant.
* State is read through the same authorized services the customer's own UI
  uses, with the customer's own capability. The Copilot has no ambient
  authority of its own and cannot see another customer's data.
* An action is performed only when the customer confirms that exact proposal.
  A proposal names the action and its arguments, and is bound to the revision
  it was computed from, so a stale page cannot apply a decision the customer
  never saw.
* The Copilot never asserts that something works. It reports what the platform
  recorded, and readiness flags stay owned by the services that verify them.
* It never asks for, accepts, or repeats credentials.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets

from support_core import Conflict, InvalidInput

# The complete set of things the Copilot may do for a customer. Anything absent
# here cannot be reached, whatever a model emits.
ACTION_ACKNOWLEDGE_RULES = 'acknowledge_rules'
# Model choice now happens inside the site (Settings → Models), where the
# customer's own key lives; the platform no longer records a selection.
ALLOWED_ACTIONS = frozenset({ACTION_ACKNOWLEDGE_RULES})


class OnboardingCopilot:
    def __init__(self, onboarding, *, proposal_secret=None):
        self.onboarding = onboarding
        # Proposals are signed so a client cannot fabricate one the Copilot
        # never made. Regenerated per process: a restart invalidates them.
        self._secret = (proposal_secret or secrets.token_urlsafe(32)).encode('utf-8')

    # -- reading state -----------------------------------------------------

    def situation(self, actor):
        """What the platform actually recorded for this customer, right now."""
        status = self.onboarding.status(actor)
        job = status['site_job']
        office = status.get('office') or {}
        model = office.get('default_model') or {}
        steps = {step['id']: step['status'] for step in status['steps']}
        return {
            'rules_acknowledged': status['rules_acknowledged'],
            'rules_version': status['rules_version'],
            'site_status': job['status'],
            'site_provisioned': job.get('provisioned', False),
            'site_host': job.get('site_host'),
            'template': office.get('template'),
            'hives': [{'id': h['id'], 'name': h['name'], 'roles': [r['id'] for r in h['roles']],
                       'schedules': len(h.get('schedules', [])),
                       'schedules_ready': sum(1 for j in h.get('schedules', [])
                                              if j.get('state') == 'ready'),
                       'enabled': h['enabled']} for h in office.get('hives', [])],
            'office_step': steps.get('office'),
            'default_model': (f"{model['provider']}/{model['model']}" if model else None),
            'using_starter': bool(model.get('starter')),
            'starter': status.get('starter'),
            'ready_for_work': status['ready_for_work'],
            'executor': bool((status.get('tasks') or {}).get('executor')),
            'task_offers': len((status.get('tasks') or {}).get('offers') or []),
        }

    # -- proposing the next step ------------------------------------------

    def next_step(self, actor):
        """Return {'message', 'action'|None} describing what to do next.

        The message is generated from state, not from a model, so it cannot
        hallucinate a step or claim an unverified success.
        """
        s = self.situation(actor)
        if not s['rules_acknowledged']:
            return self._with_action(
                '你還沒確認使用與支援說明。那是後續步驟的前提，內容包含客服對話的可見範圍，'
                '以及「不要把密碼或金鑰貼進聊天」這條規則。',
                ACTION_ACKNOWLEDGE_RULES,
                {'rules_version': s['rules_version']},
                '幫我確認說明')
        if not s['site_provisioned']:
            wording = {
                'not_requested': '說明已確認。接下來由管理員為你建立站台，建立後這一頁會出現進入按鈕。',
                'queued': '建站任務已排入，通常需要數分鐘，這段期間不需要你操作。',
                'running': '站台正在建立中，完成後這一頁會出現進入按鈕。',
                'reconciliation_required': '建站未完成，已交由管理員確認，不會自動重試。請聯絡管理員。',
                'failed': '建站失敗，已交由管理員處理。請聯絡管理員。',
            }.get(s['site_status'], '建站狀態尚未確認，請稍後重新載入。')
            return {'message': wording, 'action': None}
        host = s['site_host']
        if s['office_step'] == 'pending':
            return {'message': f'站台已就緒（{host}）。下一步是建立你的第一間公司：按「進入我的站台」，'
                               '在輸入框打「帶我完成新手任務」，AI 會一步一步帶你做。', 'action': None}
        if s['using_starter']:
            return {'message': '辦公室已建立。你目前用的是平台借你的起步模型，有每月額度；'
                               '想不受限制，可在站台的 Settings → Models 接上自己的模型。'
                               '金鑰只在站台裡輸入，不要貼到聊天。', 'action': None}
        return {'message': f'都設定好了。按「進入我的站台」（{host}）就能開始工作，'
                           '進站不需要另外的帳號密碼。', 'action': None}

    # -- performing a confirmed action ------------------------------------

    def _with_action(self, message, kind, arguments, label):
        payload = {'kind': kind, 'arguments': arguments}
        body = json.dumps(payload, sort_keys=True, separators=(',', ':'))
        token = hmac.new(self._secret, body.encode('utf-8'), hashlib.sha256).hexdigest()
        return {'message': message,
                'action': {**payload, 'label': label, 'token': token}}

    def perform(self, actor, kind, arguments, token):
        """Apply a proposal the customer confirmed. Refuses anything else."""
        if kind not in ALLOWED_ACTIONS:
            raise InvalidInput('unsupported action')
        if not isinstance(arguments, dict):
            raise InvalidInput('invalid action arguments')
        body = json.dumps({'kind': kind, 'arguments': arguments},
                          sort_keys=True, separators=(',', ':'))
        expected = hmac.new(self._secret, body.encode('utf-8'), hashlib.sha256).hexdigest()
        # Constant-time: the token proves this exact proposal came from us.
        if not isinstance(token, str) or not hmac.compare_digest(expected, token):
            raise Conflict('proposal is stale or was not issued here')

        version = arguments.get('rules_version')
        if not isinstance(version, str):
            raise InvalidInput('invalid rules version')
        # The service re-checks the version; a changed ruleset conflicts.
        self.onboarding.acknowledge(actor, version)
        return self.next_step(actor)
