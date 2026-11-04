"""Role-isolated runtime configuration. Never serialize RoleConfig itself."""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import math
import os
from pathlib import Path
from urllib.parse import urlsplit

import yaml

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MODEL_CONFIG = ROOT / 'config' / 'model_config.local.yaml'
ROLES = {'rag', 'refactoring', 'decision'}


class ModelConfigurationError(ValueError, RuntimeError):
    pass


@dataclass(frozen=True)
class RoleConfig:
    role: str
    enabled: bool
    provider: str
    base_url: str = field(repr=False)
    api_key: str = field(repr=False)
    model: str
    timeout: float
    max_retries: int
    temperature: float | None = None
    top_p: float | None = None
    max_tokens: int | None = None
    dimensions: int | None = None
    source: str = 'local'

    def identity(self):
        # Hash the entire endpoint, not its path: even URL paths may contain credentials.
        return dict(role=self.role, enabled=self.enabled, provider=self.provider,
                    endpoint_sha256=hashlib.sha256(self.base_url.encode()).hexdigest(),
                    model=self.model, timeout=self.timeout, transport_max_retries=self.max_retries,
                    temperature=self.temperature, top_p=self.top_p, max_tokens=self.max_tokens,
                    dimensions=self.dimensions, source=self.source,
                    credential_configured=bool(self.api_key))

    def require(self):
        if not self.enabled:
            raise ModelConfigurationError(f'{self.role}: role is disabled in model configuration.')
        if not self.api_key:
            if self.source == 'legacy' and self.role == 'refactoring':
                raise ModelConfigurationError('OPENAI_API_KEY is not set in the pipeline process.')
            raise ModelConfigurationError(f'{self.role}: api_key is required; set the environment variable referenced by the selected model configuration.')
        return self


def _local(path=None):
    path = Path(path) if path is not None else DEFAULT_MODEL_CONFIG
    if not path.is_file():
        if path != DEFAULT_MODEL_CONFIG:
            raise ModelConfigurationError('Explicit model configuration file is missing.')
        return None
    try:
        value = yaml.safe_load(path.read_text(encoding='utf-8'))
    except Exception:
        raise ModelConfigurationError('Invalid model configuration YAML (content omitted).') from None
    if not isinstance(value, dict) or type(value.get('enabled')) is not bool:
        raise ModelConfigurationError('model configuration requires boolean enabled.')
    if not value['enabled']:
        return None
    if set(value) != {'enabled', 'roles'} or not isinstance(value['roles'], dict) or set(value['roles']) != ROLES:
        raise ModelConfigurationError('model configuration must define exactly rag, refactoring and decision roles.')
    return value['roles']


def _parse(role, raw, source):
    # Reproduction package: resolve credentials from the process, never save them.
    if isinstance(raw, dict) and isinstance(raw.get('api_key'), str):
        raw = dict(raw)
        value = raw['api_key']
        if value.startswith('${') and value.endswith('}'):
            raw['api_key'] = os.environ.get(value[2:-1], '')
    common = {'enabled', 'provider', 'base_url', 'api_key', 'model', 'timeout', 'max_retries'}
    specific = {'dimensions'} if role == 'rag' else {'temperature', 'top_p', 'max_tokens'}
    if not isinstance(raw, dict) or set(raw) != common | specific:
        raise ModelConfigurationError(f'{role}: missing or unknown configuration fields.')
    if type(raw['enabled']) is not bool or raw['provider'] != 'openai_compatible':
        raise ModelConfigurationError(f'{role}: boolean enabled and provider=openai_compatible required.')
    if any(not isinstance(raw[k], str) for k in ('base_url', 'api_key', 'model')):
        raise ModelConfigurationError(f'{role}: endpoint, api_key and model must be strings.')
    try:
        url = urlsplit(raw['base_url'])
        valid_url = (url.scheme in {'http', 'https'} and bool(url.hostname) and not (url.username or url.password)
                     and (source == 'legacy' or not (url.query or url.fragment)))
        url.port
    except ValueError:
        valid_url = False
    if not valid_url or any(ord(c) <= 32 for c in raw['base_url']) or not raw['model'].strip():
        raise ModelConfigurationError(f'{role}: valid absolute http(s) endpoint without credentials/query/fragment and nonempty model required.')
    if type(raw['max_retries']) is not int or not 0 <= raw['max_retries'] <= 5:
        raise ModelConfigurationError(f'{role}: max_retries must be an integer from 0 to 5.')
    for key, low, high in [('timeout', 0.001, 3600)] + ([] if role == 'rag' else [('temperature', 0, 2), ('top_p', 0.001, 1)]):
        n = raw[key]
        if type(n) not in (float, int) or not math.isfinite(n) or not low <= n <= high:
            raise ModelConfigurationError(f'{role}: {key} is outside its supported range.')
    integer = 'dimensions' if role == 'rag' else 'max_tokens'
    if type(raw[integer]) is not int or not 1 <= raw[integer] <= 1048576:
        raise ModelConfigurationError(f'{role}: {integer} must be a bounded positive integer.')
    return RoleConfig(role=role, source=source, **raw)


def resolve_role(role, *, path=None, legacy_embedding=None, require=False):
    if role not in ROLES:
        raise ModelConfigurationError('Unknown model role.')
    local = _local(path)
    if local is not None:
        # Validate every explicit role, including disabled entries, without exposing values.
        configs = {name: _parse(name, local[name], 'local') for name in sorted(ROLES)}
        config = configs[role]
    else:
        try:
            if role == 'rag':
                legacy = legacy_embedding if legacy_embedding is not None else load_index_embedding_config()
                raw = dict(enabled=True, provider='openai_compatible', base_url=legacy['base_url'],
                           api_key=os.getenv(legacy.get('api_key_env') or 'DASHSCOPE_API_KEY', '') or legacy.get('api_key', ''),
                           model=legacy['model_name'], dimensions=int(legacy['embedding_dimension']),
                           timeout=120.0, max_retries=int(os.getenv('UTREFACTOR_RAG_MAX_RETRIES', '0')))
            else:
                prefix = 'UTREFACTOR_DECISION_' if role == 'decision' else 'UTREFACTOR_'
                raw = dict(enabled=True, provider='openai_compatible',
                           base_url=os.getenv('UTREFACTOR_DECISION_API_BASE' if role == 'decision' else 'OPENAI_API_BASE', 'https://opencode.ai/zen/go/v1'),
                           api_key=os.getenv('UTREFACTOR_DECISION_API_KEY' if role == 'decision' else 'OPENAI_API_KEY', ''),
                           model=os.getenv(prefix + 'MODEL', 'deepseek-v4-pro'),
                           timeout=float(os.getenv(prefix + 'TIMEOUT', '120')),
                           max_retries=int(os.getenv(prefix + 'MAX_RETRIES', '0')),
                           temperature=float(os.getenv(prefix + 'TEMPERATURE', '0.0')),
                           top_p=float(os.getenv(prefix + 'TOP_P', '1.0')),
                           max_tokens=int(os.getenv(prefix + 'MAX_TOKENS', '4096' if role == 'decision' else '32768')))
            config = _parse(role, raw, 'legacy')
        except (KeyError, TypeError, ValueError):
            raise ModelConfigurationError(f'{role}: invalid legacy model configuration (values omitted).') from None
    return config.require() if require else config


def local_role_enabled(role):
    local = _local()
    if local is None:
        return None
    return resolve_role(role).enabled


def load_index_embedding_config():
    root = ROOT / 'test_refactoring_knowledge_base'
    kb = json.loads((root / 'config' / 'knowledge_base_config.json').read_text(encoding='utf-8-sig'))
    return json.loads((root / kb['embedding_config_path']).read_text(encoding='utf-8-sig'))


def validate_rag_compatibility(config, index):
    if config.model != index.get('model_name') or config.dimensions != index.get('embedding_dimension'):
        raise ModelConfigurationError('rag: query model/dimensions do not match the existing index; automatic rebuild is forbidden.')


def rag_index_identity(index=None):
    index = load_index_embedding_config() if index is None else index
    return {'model': index.get('model_name'), 'dimensions': index.get('embedding_dimension'),
            'normalize_embeddings': bool(index.get('normalize_embeddings', False))}


def chat_client(role, *, session_id=None, config=None, factory=None, max_retries=None):
    from .llm_transport import llm_default_headers
    from .resume_transport import ACTIVE, JournalChatClient
    config = (config or resolve_role(role)).require()
    if config.role != role or role == 'rag':
        raise ModelConfigurationError('Chat client role mismatch.')
    if factory is None:
        from langchain_openai import ChatOpenAI
        factory = ChatOpenAI
    client = factory(api_key=config.api_key, base_url=config.base_url, model=config.model,
                   timeout=config.timeout, temperature=config.temperature, top_p=config.top_p,
                   max_tokens=config.max_tokens,
                   max_retries=0 if ACTIVE.get() is not None else config.max_retries if max_retries is None else max_retries,
                   default_headers=llm_default_headers(config.base_url, session_id) or None)
    return JournalChatClient(client, role, config.identity()) if ACTIVE.get() is not None else client


def safe_failure(exc):
    # Provider exceptions may echo arbitrary keys, request bodies or credential URLs.
    if isinstance(exc, ModelConfigurationError):
        return str(exc)  # Our configuration errors contain static field names, never values.
    return f'{type(exc).__name__}: external operation failed; diagnostic content omitted.'


def preflight_models(*, execution_mode, session_id=None, agent_config_path=None):
    """Validate active local roles and decision configuration without network I/O."""
    from .agents.decision_support import load_decision_settings
    from .agents.base import load_refactoring_agent_config
    from .llm_transport import llm_default_headers
    settings = load_decision_settings()
    local = _local()
    roles = []
    if settings['mode'] == 'rule_llm':
        roles.append('decision')
    if local is not None:
        # Eagerly detect malformed explicit settings even for inactive roles.
        resolve_role('refactoring')
        if execution_mode == 'FORMAL':
            roles.append('refactoring')
        if load_refactoring_agent_config(agent_config_path)['rag_enabled']:
            rag = resolve_role('rag')
            validate_rag_compatibility(rag, load_index_embedding_config())
            if execution_mode == 'FORMAL':
                roles.append('rag')
    identities = {}
    for role in roles:
        config = resolve_role(role, require=True)
        if role != 'rag':
            llm_default_headers(config.base_url, session_id)
        identities[role] = config.identity()
    return identities
