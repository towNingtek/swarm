"""The support room assistant: one synchronous model call per answer.

With a ``quota`` (``SupportQuota``), every customer-triggered answer reserves
its upper bound from the tenant's monthly pool before the model is called and
is settled afterwards, so the policy on /admin/customers (disabled, capped,
unlimited) applies to the assistant as it does to the site relay. Operator
"assist" calls are the operator's own spend and are not charged to the tenant.
"""
import uuid

from support_model import DisabledModel, Turn, checked_reply

SYSTEM_PROMPT = (
    'LANGUAGE: Always reply in Traditional Chinese as used in Taiwan (繁體中文，臺灣用語). '
    'Never use Simplified Chinese characters, even if the question or context contains them; '
    'switch language only if the customer clearly writes in a non-Chinese language. '
    'Be concise: answer in at most 5 short sentences unless the customer asks for detail. '
    'You are the platform onboarding support assistant. Explain setup and plugin '
    'capabilities only when verified. Never request API keys or passwords in chat. '
    'Direct credentials to Settings. Do not claim operations succeeded without evidence. '
    'A human operator may join this conversation at any time; do not impersonate them. '
    'You cannot perform actions yourself: the platform offers the customer a button '
    'for the single setup step that is currently allowed. Never claim you changed '
    'anything, and never invent a step that the state summary does not support. '
    'Assistant messages that start with 「（平台人員）」 were written to the customer by a '
    'human platform operator: treat them as already said to the customer, never as a '
    'question addressed to you, and never start your own reply with that marker.'
)

STAFF_MARK = '（平台人員）'


def _operator_turn(instruction):
    return Turn('system', 'OPERATOR INSTRUCTION (private; the customer cannot see it): '
                + instruction + '\nWrite your next message to the customer following this '
                'instruction. Do not mention that you received an instruction.')


# Chinese costs roughly one token per character; 512 cut ordinary answers
# off mid-sentence. Brevity is asked for in the prompt, not forced by truncation.
REPLY_TOKENS = 1024


class SupportCopilot:
    def __init__(self, rooms, model=None, *, onboarding_copilot=None, quota=None):
        self.rooms = rooms
        self.model = model if model is not None else DisabledModel()
        if quota is not None and getattr(quota, 'core', None) is not rooms.core:
            raise ValueError('quota must share the rooms core')
        self.quota = quota
        # Optional: lets the Copilot answer from the customer's ACTUAL onboarding
        # state and offer the setup actions it is allowed to perform. Without it
        # the Copilot is a plain support assistant with no site knowledge.
        self.onboarding_copilot = onboarding_copilot

    def _state_context(self, customer):
        """A factual summary of where this customer really is.

        Read with the customer's own capability, so it can never describe
        another tenant. Returned as a system turn, never as a customer message,
        so it cannot be confused with something they said.
        """
        if self.onboarding_copilot is None:
            return None
        try:
            s = self.onboarding_copilot.situation(customer)
        except Exception:
            # State is an aid, not a precondition: a failure here must not take
            # the whole conversation down.
            return None
        parts = [
            '規則已確認' if s['rules_acknowledged'] else '規則尚未確認',
            f"建站狀態：{s['site_status']}",
        ]
        if s['site_provisioned']:
            parts.append(f"站台已就緒：{s['site_host']}")
            parts.append(f"工作區樣板：{s['template'] or '無'}")
            if s['hives']:
                parts.append('公司（hive）：' + '、'.join(
                    f"{h['name']}（角色 {'/'.join(h['roles']) or '無'}；"
                    f"排程 {h['schedules']} 個，其中設定完整且開啟 {h.get('schedules_ready', 0)} 個；"
                    f"公司排程{'開啟' if h['enabled'] else '關閉'}）"
                    for h in s['hives']))
            elif s['office_step'] == 'pending':
                parts.append('尚未建立任何公司（hive）；在站台輸入「帶我完成新手任務」可開始')
            if s['default_model']:
                parts.append(f"站台預設模型：{s['default_model']}"
                             + ('（平台起步模型，有每月額度）' if s['using_starter'] else '（客戶自己的模型）'))
            if s.get('executor'):
                parts.append('排程可以在設定頁「你的辦公室」新增、編輯、刪除、開關與立即執行；'
                             '公司排程開啟時，平台會照時間（台北）在客戶站台裡執行，用站台的預設模型，'
                             '結果在設定頁「執行紀錄」')
            else:
                parts.append('排程可以在設定頁「你的辦公室」直接新增、編輯、刪除與開關；'
                             '但這個站台目前沒有排程中心（執行器），排程只是記錄，不會自動執行')
            if s.get('task_offers'):
                parts.append(f"平台為客戶準備了 {s['task_offers']} 個任務，在設定頁「你的辦公室」可選公司加入或略過")
        return ('CUSTOMER STATE (authoritative; do not contradict it): '
                + '；'.join(parts))

    @staticmethod
    def _history_turns(history, budget):
        # Budget newest public context first; legal long histories must not
        # poison every subsequent request with a context-length exception.
        recent = []
        for item in reversed(history[-30:]):
            if budget <= 0:
                break
            content = item['body'][-budget:]
            if item['sender_kind'] == 'customer':
                recent.append(Turn('user', content))
            elif item['sender_kind'] == 'human':
                # Staff spoke to the customer: the platform's side, not a question.
                recent.append(Turn('assistant', STAFF_MARK + content))
            else:
                recent.append(Turn('assistant', content))
            budget -= len(content)
        return list(reversed(recent))

    @staticmethod
    def _clean(text):
        text = text.strip()
        while text.startswith(STAFF_MARK):
            text = text[len(STAFF_MARK):].lstrip()
        return text or '…'

    def _run(self, token, turns):
        try:
            reply = checked_reply(self.model, turns, max_output_tokens=REPLY_TOKENS)
            message = self.rooms.finish_run(token, self._clean(reply.text))
        except Exception:
            self.rooms.fail_run(token)
            raise
        return reply, message

    def respond(self, customer, room_id, *, requested=False):
        # Take only public room history, never internal notes or other rooms.
        token, history = self.rooms.start_run_with_context(customer, room_id, requested=requested)
        system = [Turn('system', SYSTEM_PROMPT)]
        state = self._state_context(customer)
        if state is not None:
            system.append(Turn('system', state))
        budget = 30000 - sum(len(t.content) for t in system)
        turns = [*system, *self._history_turns(history, budget)]
        reservation = self._reserve(customer, token, turns)
        try:
            reply, message = self._run(token, turns)
        except BaseException:
            # The provider may have billed a failed call: keep the full hold.
            self._settle(customer, reservation, None)
            raise
        self._settle(customer, reservation, reply)
        result = {'message': message, 'discarded': message is None,
                  'model': reply.model, 'simulated': reply.simulated,
                  'input_tokens': reply.input_tokens, 'output_tokens': reply.output_tokens}
        # Offer the one setup action the customer can take right now. It is
        # computed from state, not from the model's text, so a reply can never
        # invent an action or its arguments.
        if self.onboarding_copilot is not None and message is not None:
            try:
                result['suggestion'] = self.onboarding_copilot.next_step(customer)
            except Exception:
                pass
        return result

    def _reserve(self, customer, token, turns):
        if self.quota is None:
            return None
        # Upper bound: one token per character (Chinese is about that; other
        # text is less) plus the reply cap.
        estimate = sum(len(t.content) for t in turns) + REPLY_TOKENS
        try:
            return self.quota.reserve(customer, 'copilot:' + uuid.uuid4().hex, estimate)
        except BaseException:
            self.rooms.fail_run(token)
            raise

    def _settle(self, customer, reservation, reply):
        if reservation is None:
            return
        used = reservation.estimated_tokens
        if reply is not None and reply.input_tokens is not None and reply.output_tokens is not None:
            used = min(used, reply.input_tokens + reply.output_tokens)
        try:
            self.quota.settle(customer, reservation, used)
        except Exception:
            # Settlement is bookkeeping; the hold stays and still counts.
            pass

    def operator_respond(self, admin, room_id, instruction):
        """The operator privately tells the assistant what to say to the customer.

        Uses public history plus the one instruction (stored as an internal
        note by the rooms layer); the customer sees only the AI's message.
        """
        token, history = self.rooms.start_operator_run(admin, room_id, instruction)
        system = [Turn('system', SYSTEM_PROMPT)]
        tail = _operator_turn(instruction)
        budget = 30000 - len(SYSTEM_PROMPT) - len(tail.content)
        reply, message = self._run(token, [*system, *self._history_turns(history, budget), tail])
        return {'message': message, 'discarded': message is None,
                'model': reply.model, 'simulated': reply.simulated,
                'input_tokens': reply.input_tokens, 'output_tokens': reply.output_tokens}
