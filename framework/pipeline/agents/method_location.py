"""MNT method identity on the existing Java AST; never count nested methods."""
from pathlib import Path

from .safety_contracts import Java, children
from .refactoring_contract import normalized_source

METHOD_LOCATION_VERSION = '1'


def signature(java, node):
    # Keep the schema 4.1 parameter spelling, including parameter names.
    params = java.text(node.child_by_field_name('parameters'))[1:-1]
    return java.text(node.child_by_field_name('name')) + '(' + ' '.join(params.split()) + ')'


def single_method(source):
    java = Java(source)
    roots = children(java.root)
    if len(roots) != 1 or roots[0].type != 'method_declaration':
        raise ValueError('MNT_SINGLE_OWNED_METHOD_REQUIRED')
    node = roots[0]
    if node.child_by_field_name('body') is None:
        raise ValueError('MNT_COMPLETE_METHOD_REQUIRED')
    return java, node


def owned_method(source, instance):
    java = Java(source)
    packages = java.nodes('package_declaration')
    package = java.text(packages[0])[len('package'):].strip().rstrip(';').strip() if packages else ''
    if package != (instance.get('test_package_name') or ''):
        raise ValueError('MNT_METHOD_PACKAGE_MISMATCH')
    name = Path(instance.get('test_filename') or '').stem
    owners = [n for n in children(java.root) if n.type == 'class_declaration'
              and java.text(n.child_by_field_name('name')) == name]
    if len(owners) != 1:
        raise ValueError('MNT_METHOD_OWNER_AMBIGUOUS_OR_MISSING')
    methods = [n for n in children(owners[0].child_by_field_name('body'))
               if n.type == 'method_declaration' and signature(java,n) == instance.get('method_signature')]
    if len(methods) != 1:
        raise ValueError('MNT_METHOD_IDENTITY_AMBIGUOUS_OR_MISSING')
    return java, methods[0]


def verify_location(contract, original_file, candidate, candidate_file=None):
    instance = contract.get('safety_instance') or {}
    if contract.get('source_file') or instance.get('test_file_path'):
        if (not instance.get('test_file_path') or not contract.get('source_file') or
                Path(contract['source_file']).resolve()!=Path(instance['test_file_path']).resolve()):
            raise ValueError('MNT_METHOD_SOURCE_FILE_MISMATCH')
    if contract.get('instance_id', instance.get('instance_id')) != instance.get('instance_id'):
        raise ValueError('MNT_METHOD_INSTANCE_MISMATCH')
    if contract.get('method_signature') != instance.get('method_signature'):
        raise ValueError('MNT_METHOD_SIGNATURE_MISMATCH')
    java,node = owned_method(original_file,instance)
    start,end = contract['method_start_line'],contract['method_end_line']
    if instance.get('line_range') != {'start':start,'end':end}:
        raise ValueError('MNT_METHOD_RANGE_MISMATCH')
    if (node.start_point.row+1,node.end_point.row+1) != (start,end):
        raise ValueError('MNT_METHOD_RANGE_MISMATCH')
    if normalized_source(java.text(node)) != normalized_source(instance.get('test_code') or ''):
        raise ValueError('MNT_METHOD_SOURCE_MISMATCH')
    parsed,method = single_method(candidate)
    if signature(parsed,method) != instance['method_signature']:
        raise ValueError('MNT_METHOD_SIGNATURE_MISMATCH')
    if candidate_file is not None:
        full,located = owned_method(candidate_file,instance)
        if normalized_source(full.text(located)) != normalized_source(candidate):
            raise ValueError('MNT_METHOD_FILE_MISMATCH')
    return True


def test_execution_identity(source, instance):
    """Map one source-bound method to a JVM binary class name, including members.

    Local/anonymous classes and overloaded test names need richer report mappings
    and are deliberately unsupported. This does not alter any smell contract.
    """
    java=Java(source)
    methods=[n for n in java.nodes('method_declaration')
             if signature(java,n)==instance.get('method_signature')
             and normalized_source(java.text(n))==normalized_source(instance.get('test_code') or '')]
    if len(methods)!=1:
        raise ValueError('TARGET_MAPPING_AMBIGUOUS')
    node=methods[0]; lines=instance.get('line_range') or {}
    if (node.start_point.row+1,node.end_point.row+1)!=(lines.get('start'),lines.get('end')):
        raise ValueError('TARGET_SOURCE_RANGE_MISMATCH')
    name=java.text(node.child_by_field_name('name'))
    body=node.parent
    if body.type!='class_body' or len([m for m in children(body) if m.type=='method_declaration'
            and java.text(m.child_by_field_name('name'))==name])!=1:
        raise ValueError('TARGET_MAPPING_AMBIGUOUS')
    owners=[]; cursor=body.parent
    while cursor.type=='class_declaration':
        owners.append(java.text(cursor.child_by_field_name('name')))
        parent=cursor.parent
        if parent.type=='program':break
        if parent.type!='class_body' or parent.parent.type!='class_declaration':
            raise ValueError('TARGET_LOCAL_CLASS_UNSUPPORTED')
        cursor=parent.parent
    if not owners or owners[-1]!=Path(instance.get('test_filename') or '').stem:
        raise ValueError('TARGET_CLASS_IDENTITY_MISMATCH')
    packages=java.nodes('package_declaration')
    package=java.text(packages[0])[len('package'):].strip().rstrip(';').strip() if packages else ''
    if package!=(instance.get('test_package_name') or ''):
        raise ValueError('TARGET_CLASS_IDENTITY_MISMATCH')
    return '.'.join(filter(None,(package,'$'.join(reversed(owners))))),name
