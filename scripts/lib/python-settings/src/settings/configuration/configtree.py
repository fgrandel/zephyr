import yaml

from copy import deepcopy
from typing import Any, Iterable, NoReturn, Optional, Self, Union, override

from settings.bindings import _STPropertySpec
from settings.error import ConfigError
from settings.stree import (
    _GenericPropertyValType,
    _STPartialTree,
    _STPartialTreeNode,
    _STNode,
    _STProperty,
)
from settings.yamlutil import _IncludeLoader

#
# Private types
#


_ConfigPropertyValType = Union[
    _GenericPropertyValType,
    _STNode,
    list[_STNode],
]

#
# Private classes
#


class _ConfigProperty(_STProperty[_ConfigPropertyValType, "_ConfigNode"]):
    """
    Represents a YAML configuration property.

    See docstring of the STProperty base class.
    """

    def __init__(
        self,
        yaml: Optional[list | str | bool | int],
        prop_spec: _STPropertySpec,
        node: "_ConfigNode",
        err_on_deprecated: bool,
    ):
        """
        For internal use only; not meant to be instantiated directly by settings
        library clients.
        """
        # yaml:
        #   The YAML node representing the value of the property.
        # prop_spec:
        #   The STPropertySpec object defining the property.
        # node:
        #   The ConfigNode object the property belongs to.
        # err_on_deprecated:
        #   Whether to raise an error if the property is deprecated.
        self._value = yaml
        super().__init__(prop_spec, node, yaml is not None, err_on_deprecated)

    def __str__(self):
        return f"{self.name} = {self._value}"

    def __repr__(self):
        return (
            f"<ConfigProperty '{self.name}' at '{self.node.path}' in "
            f"'{self.spec.path}'>"
        )

    @override
    def _to_bool(self) -> bool:
        # Returns the value of the property as a boolean.
        #
        # Raises an error if the property has not been parsed as a boolean by
        # PyYAML.

        if not isinstance(self._value, bool):
            _err(
                "expected property '{0}' on {1} in {2} to be a boolean, not '{3}'".format(
                    self.name, self.node.path, self.spec.path, self._value
                )
            )

        return self._value

    @override
    def _to_num(self) -> int:
        # Returns the value of the property as an integer.
        #
        # Raises an error if the property has not been parsed as an integer
        # number by PyYAML.

        if not isinstance(self._value, int):
            _err(
                "expected property '{0}' on {1} in {2} to be an integer, not '{3}'".format(
                    self.name, self.node.path, self.spec.path, self._value
                )
            )

        return self._value

    @override
    def _to_nums(self) -> list[int]:
        # Returns the value of the property as a a list of integers.
        #
        # Raises an error if the property has not been parsed as an integer
        # list by PyYAML.
        if not isinstance(self._value, list):
            _err(
                "expected property '{0}' on {1} in {2} to be a list of integers, not '{3}'".format(
                    self.name, self.node.path, self.spec.path, self._value
                )
            )

        for val in self._value:
            if not isinstance(val, int):
                _err(
                    "expected property '{0}' on {1} in {2} to be a list of integers, but it contains '{3}'".format(
                        self.name, self.node.path, self.spec.path, val
                    )
                )

        return self._value

    @override
    def _to_bytes(self) -> bytes:
        # Returns the value of the property as bytes.
        #
        # Raises an error if the property has not been parsed as a scalar
        # integer or string by PyYAML.
        #
        # This function might also raise UnicodeDecodeError if the byte value
        # was given as string and the string is not valid UTF-8.
        if isinstance(self._value, int):
            return self._value.to_bytes(byteorder="big")
        elif isinstance(self._value, str):
            try:
                return self._value.encode("utf-8")
            except UnicodeDecodeError:
                _err(
                    f"value of property '{self.name}' ({self._value!r}) "
                    f"on {self.node.path} in {self.spec.path} "
                    "is not valid UTF-8"
                )
        else:
            _err(
                "expected property '{0}' on {1} in {2} to be a scalar value, not '{3}'".format(
                    self.name, self.node.path, self.spec.path, self._value
                )
            )

    @override
    def _to_string(self) -> str:
        # Returns the value of the property as a string.
        #
        # Raises an error if the property has not been parsed as a scalar
        # string by PyYAML.
        if not isinstance(self._value, str):
            _err(
                "expected property '{0}' on {1} in {2} to be a string, not '{3}'".format(
                    self.name, self.node.path, self.spec.path, self._value
                )
            )

        return self._value

    @override
    def _to_strings(self) -> list[str]:
        # Returns the value of the property as a a list of strings.
        #
        # Raises an error if the property has not been parsed as a list of
        # strings by PyYAML.
        if not isinstance(self._value, list):
            _err(
                "expected property '{0}' on {1} in {2} to be a list of strings, not '{3}'".format(
                    self.name, self.node.path, self.spec.path, self._value
                )
            )

        for val in self._value:
            if not isinstance(val, str):
                _err(
                    "expected property '{0}' on {1} in {2} to be a list of strings, but it contains '{3}'".format(
                        self.name, self.node.path, self.spec.path, val
                    )
                )

        return self._value

    @override
    def _to_node(self) -> _STNode:
        # Returns the STNode the phandle in the property points to.
        #
        # Raises an error if the property cannot be resolved to a node.
        if not isinstance(self._value, str):
            _err(
                "expected property '{0}' on {1} in {2} to be"
                "'&foo' or '&{/bar/foo}', not '{3}'".format(
                    self.name, self.node.path, self.spec.path, self._value
                )
            )

        return self.node.stree.phandle2node(self._value)

    @override
    def _to_nodes(self) -> list[_STNode]:
        # Returns the list of STNodes the phandles in the property point to.
        #
        # Raises an error if the property cannot be resolved to a list of nodes.
        if not isinstance(self._value, list):
            _err(
                "expected property '{0}' on {1} in {2} to be a list of phandles, not '{3}'".format(
                    self.name, self.node.path, self.spec.path, self._value
                )
            )

        phandles = []

        for val in self._value:
            if not isinstance(val, str):
                _err(
                    "expected property '{0}' on {1} in {2} to be a list of '&foo' or '&{/bar/foo}, but it contains '{3}'".format(
                        self.name, self.node.path, self.spec.path, val
                    )
                )
            phandles.append(self.node.stree.phandle2node(self._value))

        return phandles


class _ConfigNode(_STPartialTreeNode[_ConfigPropertyValType, Self, "ConfigTree"]):
    """
    Represents a YAML configuration node.

    Also see superclass and property docstrings.
    """

    def __init__(
        self,
        config_tree: "ConfigTree",
        path: str,
        yaml: dict[str, Any],
        err_on_deprecated: bool,
    ):
        """
        For internal use only; not meant to be instantiated directly by settings
        library clients.
        """
        if not "compatible" in yaml:
            compats = []
        elif isinstance(yaml["compatible"], str):
            compats = [yaml["compatible"]]
        elif isinstance(yaml["compatible"], list):
            compats = yaml["compatible"]
        else:
            _err("Invalid 'compatible' property in config node")

        super().__init__(config_tree, compats, err_on_deprecated)

        # Private, don't touch outside the class:
        self._yaml = yaml

        # Public, some of which are initialized properly later:
        self._path = path

        super()._init_node()

    def __repr__(self) -> str:
        return f"<ConfigTree for '{self.source_path}'>"

    @override
    @property
    def name(self) -> str:
        name = self.path.split("/")[-1]
        return "/" if name == "" else name

    @override
    @property
    def path(self) -> str:
        return self._path

    @override
    @property
    def parent(self) -> Optional["_ConfigNode"]:
        if self.path == "/":
            return None
        parent_path = "/".join(self.path.split("/")[:-1]) or "/"
        return self.partial_tree.node_by_path(parent_path)

    @override
    @property
    def children(self) -> dict[str, "_ConfigNode"]:
        return {
            name: self.partial_tree.node_by_path(
                f"{'' if self.path == '/' else self.path}/{name}"
            )
            for name, value in self._yaml.items()
            if isinstance(value, dict)
        }

    @override
    @property
    def _source_prop_names(self) -> Iterable[str]:
        return [
            str(key) for key, value in self._yaml.items() if not isinstance(value, dict)
        ]

    @override
    def _instantiate_prop(self, prop_spec: _STPropertySpec) -> _ConfigProperty:
        return _ConfigProperty(
            self._yaml.get(prop_spec.name), prop_spec, self, self._err_on_deprecated
        )


#
# Public classes
#


class ConfigTree(_STPartialTree[_ConfigPropertyValType, _ConfigNode]):
    """
    Represents a tree of YAML configuration nodes.

    Instantiate this class for each YAML configuration source file you want to
    add to the overall merged settings tree. Then add it to the tree calling
    STree.add_partial_tree().

    Also see the docstring of the STPartialTree base class.
    """

    def __init__(self, config_path: str, err_on_deprecated: bool = False):
        """
        ConfigTree constructor.

        config_path:
          The path to the YAML configuration source file from which the tree
          will be constructed.

        err_on_deprecated (default: False):
            If True, an error is raised if a deprecated property is encountered.
            If False, a warning is logged.
        """
        # If you change the state of this class or its superclass, make sure to
        # update the __deepcopy__ method and corresponding tests, too.  The full
        # state of a partial tree should be reproducible by instantiating it
        # with the same constructor arguments and calling
        # STPartialTree.process().
        super().__init__(config_path, err_on_deprecated)

        with open(config_path, encoding="utf-8") as f:
            self._yaml = yaml.load(f, Loader=_IncludeLoader)

        # Internal state - do not touch from outside the module.
        self._path2node: dict[str, _ConfigNode] = {}

    def __deepcopy__(self, memo) -> Self:
        """
        Implements support for the standard library copy.deepcopy()
        function on ConfigTree instances.
        """

        clone = ConfigTree(
            self.source_path,
            err_on_deprecated=self._err_on_deprecated,
        )
        clone.stree = (
            self.stree
        )  # Shallow copy is fine - the tree will be cloned and updated separately
        clone.process()

        return clone

    def __repr__(self) -> str:
        return f"<ConfigTree for '{self.source_path}'>"

    @override
    @property
    def compats(self) -> set[str]:
        return set([compat for compat in self._compat_iter()])

    @override
    @property
    def path2node(self) -> dict[str, _ConfigNode]:
        return self._path2node

    @override
    def node_by_path(self, path: str) -> _ConfigNode:
        return self._path2node[path]

    def _compat_iter(self) -> Iterable[str]:
        for _, yaml_node in self._yaml_node_iter(self._yaml):
            compat = yaml_node.get("compatible")
            if compat:
                if not isinstance(compat, list):
                    compat = [compat]
                yield from compat

    @override
    def _init_nodes(self) -> None:
        for path, yaml_node in self._yaml_node_iter(self._yaml):
            node = _ConfigNode(self, path, yaml_node, self._err_on_deprecated)
            self._nodes.append(node)
            self._path2node[path] = node

    def _yaml_node_iter(self, node: dict, path: str = "") -> Iterable[tuple[str, dict]]:
        yield "/" if not path else path, node
        for name, child in node.items():
            if isinstance(child, dict):
                yield from self._yaml_node_iter(child, f"{path}/{name}")


#
# Private global functions
#


def _err(msg) -> NoReturn:
    raise ConfigError(msg)
