"""Check packaged contracts and reject missing or inconsistent resources."""
import copy
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

import jsonschema
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'framework'))
from pipeline.contract_registry import check_registry
from pipeline.contract_documents import check_documents


class ContractResourceTests(unittest.TestCase):
    def setUp(self):
        output = ROOT / 'results/test_checks'
        output.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=output)
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name) / 'contracts'
        shutil.copytree(ROOT / 'framework/refactoring/contracts', self.folder)
        self.registry = self.folder / 'registry.yaml'
        self.item = yaml.safe_load(self.registry.read_text(encoding='utf-8'))['contracts'][0]

    def test_packaged_contracts_and_instance_examples(self):
        self.assertEqual(len(check_registry(self.registry)['contracts']), 6)
        schema = json.loads((ROOT / 'framework/pipeline/instances/schemas/pipeline-instance.schema.json').read_text(encoding='utf-8'))
        for example in (ROOT / 'framework/pipeline/instances/templates/examples').glob('*.json'):
            with self.subTest(example=example.name):
                jsonschema.Draft202012Validator(schema).validate(json.loads(example.read_text(encoding='utf-8')))

    def test_metadata_and_rule_reference_mismatch_rejected(self):
        path = self.folder / self.item['contract_file']
        original = yaml.safe_load(path.read_text(encoding='utf-8'))
        for kind in ('version', 'rule_reference'):
            with self.subTest(kind=kind):
                doc = copy.deepcopy(original)
                if kind == 'version':
                    doc['version'] = 'incorrect-version'
                else:
                    doc['rules']['allowed_edits']['source_field'] = 'missing_rule'
                path.write_text(yaml.safe_dump(doc, allow_unicode=True), encoding='utf-8')
                with self.assertRaisesRegex(ValueError, 'metadata mismatch|Invalid rule reference'):
                    check_registry(self.registry)

    def test_missing_empty_and_external_prompt_rejected(self):
        path = self.folder / self.item['prompt_file']
        path.unlink()
        with self.assertRaisesRegex(ValueError, 'Missing contract resource'):
            check_registry(self.registry)
        path.write_text('', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'prompt template'):
            check_registry(self.registry)
        item = dict(self.item, prompt_file='../external.md')
        with self.assertRaisesRegex(ValueError, 'escapes registry directory'):
            check_documents(item, self.folder)


if __name__ == '__main__':
    unittest.main(verbosity=2)
