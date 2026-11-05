"""Regression checks for honest preflight results and protected historical inputs."""
import contextlib
import copy
import hashlib
import io
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'scripts'), str(ROOT / 'framework')]
import check_package
import inspect_instance
import validate_dataset
from common import result_path


class PreflightSafetyTests(unittest.TestCase):
    def missing_client(self):
        rows = copy.deepcopy(check_package.inspect_dependencies())
        rows['langchain_openai'].update(available=False, status='MISSING', installed_version=None)
        return rows

    def test_missing_model_dependency_never_passes(self):
        dependencies = self.missing_client()
        with patch.object(check_package, 'inspect_dependencies', return_value=dependencies), \
                patch.object(check_package, 'write_json') as write, contextlib.redirect_stdout(io.StringIO()):
            code = check_package.check()
        report = write.call_args.args[1]
        self.assertEqual(code, 2)
        self.assertEqual(report['status'], 'BLOCKED')
        self.assertEqual(report['dependency_validation'], 'BLOCKED')
        self.assertIn({'dependency': 'langchain_openai', 'status': 'MISSING'}, report['blocked'])

    def test_resource_failure_is_not_hidden_by_missing_dependency(self):
        dependencies = self.missing_client()
        with patch.object(check_package, 'inspect_dependencies', return_value=dependencies), \
                patch('pipeline.contract_registry.check_registry', side_effect=ValueError('TEST_ONLY_BROKEN_RESOURCE')), \
                patch.object(check_package, 'write_json') as write, contextlib.redirect_stdout(io.StringIO()):
            code = check_package.check()
        self.assertEqual(code, 1)
        self.assertEqual(write.call_args.args[1]['status'], 'FAIL')

    def test_validation_report_cannot_overwrite_historical_file(self):
        path = ROOT / 'data/normalized/instances.csv'
        before = hashlib.sha256(path.read_bytes()).hexdigest()
        with self.assertRaisesRegex(ValueError, 'under results/'):
            validate_dataset.run(report='data/normalized/instances.csv')
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), before)

    def test_inspection_rejects_historical_output_before_collection(self):
        with patch.object(sys, 'argv', ['inspect_instance.py', '--example', '--output', 'data/normalized/instances.csv']), \
                patch.object(inspect_instance, 'offline_guard'), patch.object(inspect_instance, 'collect') as collect:
            with self.assertRaisesRegex(ValueError, 'under results/'):
                inspect_instance.main()
        collect.assert_not_called()

    def test_traversal_and_absolute_input_paths_are_rejected(self):
        for path in ['results/../data/normalized/instances.csv', ROOT / 'README.md', 'results']:
            with self.subTest(path=str(path)), self.assertRaises(ValueError):
                result_path(path)

    def test_documented_report_paths_are_allowed(self):
        for path in ['results/inspection/example.json', 'results/validation/repository_validation.json']:
            self.assertEqual(result_path(path), (ROOT / path).resolve())


if __name__ == '__main__':
    unittest.main(verbosity=2)
