"""Independent executable contract version, separate from instance schema."""
from typing import Protocol


class RefactoringContract(dict):
    @property
    def contract_id(self):
        return (self.get('refactoring_contract') or {}).get('contract_id')

    @property
    def version(self):
        return (self.get('refactoring_contract') or {}).get('version', '1.0')

    def to_versioned_dict(self):
        return {'version': self.version, **self}


class ContractBuilder(Protocol):
    def build(self, request) -> RefactoringContract: ...


class ExecutableContractBuilder:
    version = 'executable-contract-builder-1.0'

    def build(self, request):
        from .agents.refactoring_contract import _build_executable_contract
        from .agents.safety_contracts import get_proof
        from .contract_registry import contract_for
        contract = RefactoringContract(_build_executable_contract(request))
        metadata = contract_for(contract['smell'], get_proof(contract['safety_evidence']) or {})
        if metadata is not None:
            contract['refactoring_contract'] = metadata
        return contract


class CategoryRuleContractBuilder:
    version = 'category-rule-prompt-1'

    def build(self, request):
        from .ablation_runtime import natural_language_input
        return natural_language_input(request)
