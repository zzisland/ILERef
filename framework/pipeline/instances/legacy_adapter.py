"""Only this boundary translates historical instance field names.

No file writes; provenance is retained verbatim. Envelope contract_version is
unrelated and must never be renamed by a recursive global replacement.
"""
from .model import UnifiedRefactoringInstance, thaw


class LegacyInstanceAdapter:
    version = '1.0'

    def adapt(self, value):
        if isinstance(value, UnifiedRefactoringInstance):
            return value
        if not isinstance(value, dict):
            raise ValueError('Instance must be an object')
        if 'contract_version' in value:
            if 'instance_schema_version' in value:
                raise ValueError('Ambiguous legacy and runtime instance schema versions')
            if value['contract_version'] != '1.0':
                raise ValueError('Unsupported legacy instance schema version')
            value = {('instance_schema_version' if key == 'contract_version' else key):
                     ('2.0' if key == 'contract_version' else item) for key, item in value.items()}
        return UnifiedRefactoringInstance(value)

    def to_legacy(self, instance):
        instance = self.adapt(instance)
        return {('contract_version' if key == 'instance_schema_version' else key):
                ('1.0' if key == 'instance_schema_version' else thaw(value)) for key, value in instance.items()}
