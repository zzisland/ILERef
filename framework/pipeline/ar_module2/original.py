"""Pinned UTRefactor prompt semantics. Never execute upstream imports/client setup."""
import ast
from pathlib import Path
from pipeline.resume_store import read_json, file_hash

UPSTREAM = Path(__file__).resolve().parent / 'upstream'
COMMIT = '0670cd0a4a13bfb27734da507bcfb9e4bf28c539'


class PromptTemplate:
    def __init__(self, *, input_variables, template):
        self.variables, self.template = tuple(input_variables), template

    def format(self, **values):
        if set(self.variables) - set(values):
            raise ValueError('ORIGINAL_PROMPT_VARIABLE_MISSING')
        return self.template.format(**values)


def verify():
    manifest = read_json(UPSTREAM / 'manifest.json')
    if manifest['commit'] != COMMIT:
        raise ValueError('ORIGINAL_REVISION_DRIFT')
    for relative, expected in manifest['files'].items():
        target = (UPSTREAM / relative).resolve()
        if not target.is_relative_to(UPSTREAM.resolve()) or file_hash(target) != expected:
            raise ValueError('ORIGINAL_PIN_HASH_DRIFT:' + relative)
    return manifest


def builder():
    verify()
    relative = 'refactoring/buildindex/code_repo_v5.py'
    tree = ast.parse((UPSTREAM / relative).read_text(encoding='utf-8'))
    nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'build_prompt_total']
    if len(nodes) != 1:
        raise ValueError('ORIGINAL_PROMPT_FUNCTION_MISSING')
    namespace = {'PromptTemplate': PromptTemplate,
                 'system_role': '\nYou are a java testing expert, and now you need to refactor the test code which contains bad test code smells.\n'}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), relative, 'exec'), namespace)
    return namespace['build_prompt_total']


def resources():
    verify()
    return [[(UPSTREAM / 'refactoring/prompt_generation' / folder / 'Assertion_Roulette.txt').read_text(encoding='utf-8')]
            for folder in ('smelltype', 'refactor_rules', 'checkpoints')]


def render(test_code, context):
    return builder()(test_code, context, ['Assertion_Roulette'], *resources())
