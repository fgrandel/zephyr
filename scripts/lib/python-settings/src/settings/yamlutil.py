import yaml

try:
    # Use the C LibYAML parser if available, rather than the Python parser.
    # This makes e.g. gen_defines.py more than twice as fast.
    from yaml import CSafeLoader as SafeLoader
except ImportError:
    from yaml import SafeLoader  # type: ignore


def _yaml_inc_error(msg):
    # Helper for reporting errors in the !include implementation
    raise yaml.constructor.ConstructorError(None, None, "error: " + msg)


def _yaml_include(loader, node):
    # Implements !include, for backwards compatibility. '!include [foo, bar]'
    # just becomes [foo, bar].
    if isinstance(node, yaml.ScalarNode):
        # !include foo.yaml
        return [loader.construct_scalar(node)]

    if isinstance(node, yaml.SequenceNode):
        # !include [foo.yaml, bar.yaml]
        return loader.construct_sequence(node)

    _yaml_inc_error("unrecognised node type in !include statement")


# Custom PyYAML binding loader class to avoid modifying yaml.Loader directly,
# which could interfere with YAML loading in clients
class _IncludeLoader(SafeLoader):
    pass


# Add legacy '!include foo.yaml' handling
_IncludeLoader.add_constructor("!include", _yaml_include)
