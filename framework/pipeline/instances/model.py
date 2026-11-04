"""Runtime instance schema 2.0 with immutable provenance.

Historical field conversion lives exclusively in LegacyInstanceAdapter.
"""
from copy import deepcopy
from types import MappingProxyType


def _readonly(*args, **kwargs):
    raise TypeError('source_record/evidence is read-only provenance')


class FrozenDict(dict):
    """JSON-compatible read-only snapshot, including nested containers."""
    __setitem__ = __delitem__ = clear = pop = popitem = setdefault = update = __ior__ = _readonly

    def __deepcopy__(self, memo):
        return self


class FrozenList(list):
    __setitem__ = __delitem__ = append = clear = extend = insert = pop = remove = reverse = sort = __iadd__ = __imul__ = _readonly

    def __deepcopy__(self, memo):
        return self


def freeze(value):
    if isinstance(value, dict):
        return FrozenDict({key: freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return FrozenList(freeze(item) for item in value)
    return deepcopy(value)


def thaw(value):
    if isinstance(value, dict):
        return {key: thaw(item) for key, item in value.items()}
    if isinstance(value, list):
        return [thaw(item) for item in value]
    return deepcopy(value)


class UnifiedRefactoringInstance(dict):
    """Normalized mutable fields plus an isolated, immutable source snapshot.

    Runtime serialization uses instance_schema_version. Normalization must never
    rewrite provenance. Legacy exports are explicit compatibility projections.
    """

    def __init__(self, value):
        if 'contract_version' in value:
            raise ValueError('Legacy instance requires LegacyInstanceAdapter')
        if value.get('instance_schema_version') != '2.0':
            raise ValueError('UnifiedRefactoringInstance requires instance_schema_version=2.0')
        values = thaw(value)
        values['source_record'] = freeze(values['source_record'])
        super().__init__(values)

    @classmethod
    def from_legacy(cls, value):
        """Deprecated external compatibility API; runtime uses the adapter boundary."""
        from .legacy_adapter import LegacyInstanceAdapter
        return LegacyInstanceAdapter().adapt(value)

    @property
    def source_record(self):
        return self['source_record']

    @property
    def source_schema_version(self):
        return self['source_schema_version']

    @property
    def instance_schema_version(self):
        return self['instance_schema_version']

    @property
    def normalized_fields(self):
        return MappingProxyType({key: value for key, value in self.items()
                                 if key not in {'source_record', 'source_schema_version', 'instance_schema_version'}})

    def to_legacy_dict(self):
        from .legacy_adapter import LegacyInstanceAdapter
        return LegacyInstanceAdapter().to_legacy(self)

    def to_versioned_dict(self):
        return thaw(self)

    def __deepcopy__(self, memo):
        result = type(self)(self)
        memo[id(self)] = result
        return result

    def __setitem__(self, key, value):
        if key == 'contract_version':
            raise ValueError('Legacy field is not allowed in runtime instances')
        if key == 'source_record':
            _readonly()
        super().__setitem__(key, value)

    def __delitem__(self, key):
        if key == 'source_record':
            _readonly()
        super().__delitem__(key)

    def update(self, *args, **kwargs):
        values = dict(*args, **kwargs)
        if 'contract_version' in values:
            raise ValueError('Legacy field is not allowed in runtime instances')
        if 'source_record' in values:
            _readonly()
        super().update(values)

    def pop(self, key, *default):
        if key == 'source_record':
            _readonly()
        return super().pop(key, *default)

    def setdefault(self, key, default=None):
        if key in self:
            return self[key]
        self[key] = default
        return default

    def __ior__(self, other):
        self.update(other)
        return self

    clear = popitem = _readonly
