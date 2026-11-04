"""Opt-in transport journaling; ordinary pipeline requests are unchanged."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from types import SimpleNamespace

from .resume_store import checkpoint, read_checkpoint, digest, contained


class BatchStop(BaseException):
    """Must not be swallowed by agent error/fallback handlers."""


class ModelBudgetReached(BatchStop):
    pass


class ModelOutcomeUnknown(BatchStop):
    pass


class SavedTransportFailure(RuntimeError):
    def __init__(self, error_type):
        self.error_type = error_type
        super().__init__(error_type)


ACTIVE = ContextVar('audited_resume_transport', default=None)


@contextmanager
def transport_scope(journal):
    token = ACTIVE.set(journal)
    try:
        yield
    finally:
        ACTIVE.reset(token)


class CallJournal:
    def __init__(self, runner, instance_id, *, allow_calls):
        self.runner, self.instance_id, self.allow_calls = runner, instance_id, allow_calls

    def invoke(self, role, request, operation):
        runner = self.runner
        key = digest({'instance_id': self.instance_id, 'role': role, 'request': request})
        ref = 'transport/' + key + '.json'
        path = contained(runner.root, ref)
        saved = runner.state['calls'].get(key)
        if saved:
            if path.is_file():
                response = read_checkpoint(path)
                if response['request_hash'] != key:
                    raise ValueError('TRANSPORT_RESPONSE_IDENTITY_MISMATCH')
                saved['status'] = response['status']
                runner.save()
                if response['status'] == 'FAILED':
                    raise SavedTransportFailure(response['error_type'])
                return response['value']
            raise ModelOutcomeUnknown('MODEL_OUTCOME_UNKNOWN:' + key)
        if not self.allow_calls:
            raise ModelBudgetReached('REAL_MODEL_CALLS_DISABLED')
        batch = runner.state['active_batch']
        used = sum(x['batch_id'] == batch['batch_id'] for x in runner.state['calls'].values())
        if used >= runner.limits.max_model_calls_per_batch:
            raise ModelBudgetReached('MAX_MODEL_CALLS')
        runner.state['calls'][key] = {'batch_id': batch['batch_id'], 'instance_id': self.instance_id,
                                    'role': role, 'status': 'STARTED', 'response_ref': ref}
        runner.save()  # Reserve before transport; unknown outcomes never refund budget.
        try:
            value = operation()
        except Exception as exc:
            checkpoint(path, {'request_hash': key, 'status': 'FAILED', 'error_type': type(exc).__name__})
            runner.state['calls'][key]['status'] = 'FAILED'
            runner.save()
            raise
        checkpoint(path, {'request_hash': key, 'status': 'COMPLETED', 'value': value})
        runner.state['calls'][key]['status'] = 'COMPLETED'
        runner.save()
        return value

    def invoke_with_retries(self, role, request, operation, max_retries):
        for attempt in range(max_retries + 1):
            try:
                return self.invoke(role, {'request': request, 'attempt': attempt}, operation)
            except Exception as exc:
                kind = exc.error_type if isinstance(exc, SavedTransportFailure) else type(exc).__name__
                if attempt == max_retries or kind not in {
                    'RateLimitError', 'APIConnectionError', 'APITimeoutError', 'InternalServerError'}:
                    raise


class JournalChatClient:
    def __init__(self, client, role, identity):
        self.client, self.role, self.identity = client, role, identity

    def invoke(self, prompt, **kwargs):
        journal = ACTIVE.get()
        if journal is None:
            return self.client.invoke(prompt, **kwargs)
        if not isinstance(prompt, str):
            raise ValueError('RESUME_REQUIRES_TEXT_CHAT_PROMPT')
        def call():
            response = self.client.invoke(prompt, **kwargs)
            return {'content': response.content if hasattr(response, 'content') else response}
        value = journal.invoke_with_retries(self.role, {'prompt': prompt, 'kwargs': kwargs,
                                          'model_identity': self.identity}, call,
                                          self.identity.get('transport_max_retries', 0))
        return SimpleNamespace(content=value['content'])

    def __getattr__(self, name):
        return getattr(self.client, name)
