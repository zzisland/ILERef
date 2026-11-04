"""Full pipeline selection; non-Full execution uses the isolated ablation CLI."""
from dataclasses import dataclass
from pathlib import Path
import yaml

DEFAULT_PATH = Path(__file__).resolve().parents[1] / 'config/ablation_profiles.yaml'
EXPECTED = {
    'FULL': ('instance_level', 'executable', 'evidence_linked'),
    'A1': ('category_level', 'executable', 'evidence_linked'),
    'A2': ('instance_level', 'category_rule', 'evidence_linked'),
    'A3': ('instance_level', 'executable', 'compile_test_only'),
}


@dataclass(frozen=True)
class StrategySelection:
    profile: str
    applicability: str
    contract: str
    acceptance: str
    schema_version: str = '1.0'

    def describe(self):
        return dict(self.__dict__, executable=True,
                    entrypoint='run_refactoring_pipeline.py' if self.profile == 'FULL' else 'run_ablation.py',
                    legacy_pipeline_executable=self.profile == 'FULL')

    def require_executable(self):
        if self.profile != 'FULL':
            raise NotImplementedError(f'{self.profile} requires run_ablation.py; legacy pipeline execution is forbidden.')

    def applicability_policy(self, assessor, guard):
        from .applicability import InstanceLevelApplicabilityPolicy, CategoryLevelApplicabilityPolicy
        return (InstanceLevelApplicabilityPolicy(assessor, guard) if self.applicability == 'instance_level'
                else CategoryLevelApplicabilityPolicy())

    def contract_builder(self):
        from .contract_builders import ExecutableContractBuilder, CategoryRuleContractBuilder
        return ExecutableContractBuilder() if self.contract == 'executable' else CategoryRuleContractBuilder()

    def acceptance_policy(self):
        from .acceptance import EvidenceLinkedAcceptancePolicy
        from .ablation_runtime import CompileTestOnlyAcceptancePolicy
        return EvidenceLinkedAcceptancePolicy() if self.acceptance == 'evidence_linked' else CompileTestOnlyAcceptancePolicy()


def load_ablation_config(path=None, *, profile=None):
    value = yaml.safe_load(Path(path or DEFAULT_PATH).read_text(encoding='utf-8'))
    if not isinstance(value, dict) or set(value) != {'schema_version', 'active_profile', 'profiles'} or value['schema_version'] != '1.0':
        raise ValueError('Invalid ablation configuration schema')
    profiles = value['profiles']
    if value['active_profile'] not in EXPECTED:
        raise ValueError('Unknown active ablation profile')
    if not isinstance(profiles, dict) or set(profiles) != set(EXPECTED):
        raise ValueError('FULL/A1/A2/A3 profiles must be explicit')
    for name, expected in EXPECTED.items():
        item = profiles[name]
        if not isinstance(item, dict) or set(item) != {'applicability', 'contract', 'acceptance'}:
            raise ValueError('Invalid profile fields: ' + name)
        # prompt_only is an accepted descriptive alias for the reserved A2 builder.
        actual = (item['applicability'], 'category_rule' if item['contract'] == 'prompt_only' else item['contract'], item['acceptance'])
        if actual != expected:
            raise ValueError('Profile axes changed: ' + name)
    name = profile if profile is not None else value['active_profile']
    if name not in EXPECTED:
        raise ValueError('Unknown ablation profile: ' + str(name))
    return StrategySelection(name, *EXPECTED[name])


if __name__ == '__main__':
    import argparse
    import json
    parser = argparse.ArgumentParser(description='Inspect configuration only; never run an experiment')
    parser.add_argument('--profile', choices=tuple(EXPECTED))
    parser.add_argument('--config', type=Path)
    args = parser.parse_args()
    print(json.dumps(load_ablation_config(args.config, profile=args.profile).describe(), indent=2))
