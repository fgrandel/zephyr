# Copyright (c) 2024 The Zephyr Project
# SPDX-License-Identifier: Apache-2.0

"""
Library for working with configuration files. This library presents a partial
tree of configuration nodes, but the nodes are augmented with information from
bindings (see bindings.py) and include some interpretation of properties.

Other than the edtree.py module, properties in this library support the full
range of C types.

The top-level entry point for the library is the ConfigTree class. See its
constructor docstrings for details.
"""

from __future__ import annotations

import os
import re
import yaml

from collections import OrderedDict
from copy import deepcopy
from typing import Any, Iterable, NoReturn, Optional, override

from settings.bindings import _STBinding, _STPropertySpec
from settings.error import ConfigError
from settings.stree import (
    _GenericPropertyValType,
    _STPartialTree,
    _STPartialTreeNode,
    _STNode,
    _STPartialTreeProperty,
)

#
# Private types
#


_ConfigPropertyValType = _GenericPropertyValType | float | list[float]

#
# Private classes
#


class _ConfigBinding(_STBinding[_STPropertySpec, "_ConfigBinding"]):
    """
    Represents a parsed configuration binding.

    See superclass and property docstrings.
    """

    def __repr__(self) -> str:
        schema_name = f" for schema '{self.schema}'" if self.schema else ""
        variant_name = f"  on '{self.variant}'" if self.variant else ""
        basename = os.path.basename(self.path or "")
        return f"<ConfigBinding {basename}" + schema_name + variant_name + ">"

    def _check(
        self,
        require_schema: bool,
        require_description: bool,
        top_level_props: set[str],
        legacy_errors: dict[str, str],
    ):
        for key in self.yaml_source.keys():
            if key not in top_level_props:
                _err(
                    f"unknown key '{key}' in {self.path}, "
                    "expected one of {', '.join(ok_top)}, or *-cells"
                )

        super()._check(
            require_schema, require_description, top_level_props, legacy_errors
        )

    def _check_properties(
        self,
        top_level_props: set[str],
        ok_prop_keys: set[str],
        ok_prop_types: dict[str, tuple[type | list[type], bool]],
    ) -> None:
        ok_prop_types.update(
            {
                "boolean": (bool, True),
                "pointer": (str, False),
                "pointer-array": ([str], False),
                "float": (float, True),
                "float-array": ([float], True),
                "double": (float, True),
                "double-array": ([float], True),
                "int64": (int, True),
                "int64-array": ([int], True),
                "int32": (int, True),
                "int32-array": ([int], True),
                "int16": (int, True),
                "int16-array": ([int], True),
                "int8": (int, True),
                "int8-array": ([int], True),
                "uint64": (int, True),
                "uint64-array": ([int], True),
                "uint32": (int, True),
                "uint32-array": ([int], True),
                "uint16": (int, True),
                "uint16-array": ([int], True),
                "uint8": (int, True),
            }
        )
        super()._check_properties(top_level_props, ok_prop_keys, ok_prop_types)

    def _instantiate_prop_spec(
        self, prop_name: str, prop_yaml_source: OrderedDict[str, Any], path: str
    ) -> _STPropertySpec:
        return _STPropertySpec(prop_name, self, path, prop_yaml_source)


class _ConfigProperty(
    _STPartialTreeProperty[_ConfigPropertyValType, "_ConfigNode", _STPropertySpec]
):
    """
    Represents a YAML configuration property.

    For configuration-specific types, the val-property returns:

      - For 'type: pointer' and 'type: pointer-array', 'val' is the pointed-to
        node instance or a list thereof.

      - For 'type: [u]int[{8|16|32|64}][-array]', 'val' is what you'd expect (a
        python integer or list of integers). See the exception for uint8-array
        in the base class docstring.

      - For 'type: float' and 'type: double', 'val' is a python float.

    Also see docstring of the STProperty base class.
    """

    INT_EXPR_PATTERN = re.compile(r"^[()|&!+-/*x0-9]+$")

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
    def _prop_val(
        self,
        has_prop: bool,
        err_on_deprecated: bool,
    ) -> _ConfigPropertyValType:
        val = super()._prop_val(has_prop, err_on_deprecated)

        prop_type = self.spec.type

        if has_prop and val is None:
            if prop_type.endswith("-array"):
                if prop_type == "pointer-array":
                    return self._to_nodes()
                if prop_type.startswith("int") or prop_type.startswith("uint"):
                    return self._to_nums()
                if prop_type.startswith("float") or prop_type.startswith("double"):
                    return self._to_floats()
            else:
                if prop_type == "pointer":
                    return self._to_node()
                if prop_type.startswith("int") or prop_type.startswith("uint"):
                    return self._to_num()
                if prop_type.startswith("float") or prop_type.startswith("double"):
                    return self._to_float()

        return val

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
        # number by PyYAML and cannot be evaluated as an integer expression.
        return self._eval_int_expr(self._value)

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

        return [self._eval_int_expr(val) for val in self._value]

    def _eval_int_expr(self, expr: _ConfigPropertyValType) -> int:
        # Returns the expression as an integer.
        #
        # Raises an error if the expression cannot be evaluated.
        if isinstance(self._value, int):
            return self._value

        if isinstance(expr, str) and self.INT_EXPR_PATTERN.match(expr):
            try:
                return eval(expr)
            except Exception:
                _err(
                    "expected property '{0}' on {1} in {2} to be an integer expression, not '{3}'".format(
                        self.name, self.node.path, self.spec.path, self._value
                    )
                )

        _err(
            "expected property '{0}' on {1} in {2} to be an integer, not '{3}'".format(
                self.name, self.node.path, self.spec.path, self._value
            )
        )

    def _to_float(self) -> float:
        # Returns the value of the property as a float.
        #
        # Raises an error if the property has not been parsed as a float
        # number by PyYAML.

        if not isinstance(self._value, float):
            _err(
                "expected property '{0}' on {1} in {2} to be a float, not '{3}'".format(
                    self.name, self.node.path, self.spec.path, self._value
                )
            )

        return self._value

    def _to_floats(self) -> list[int]:
        # Returns the value of the property as a a list of floats.
        #
        # Raises an error if the property has not been parsed as a floats list
        # by PyYAML.
        if not isinstance(self._value, list):
            _err(
                "expected property '{0}' on {1} in {2} to be a list of floats, not '{3}'".format(
                    self.name, self.node.path, self.spec.path, self._value
                )
            )

        for val in self._value:
            if not isinstance(val, float):
                _err(
                    "expected property '{0}' on {1} in {2} to be a list of floats, but it contains '{3}'".format(
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
                return bytes.fromhex(self._value)
            except ValueError:
                _err(
                    f"value of property '{self.name}' ({self._value!r}) "
                    f"on {self.node.path} in {self.spec.path} "
                    "is not a valid hex number"
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
        # Returns the STNode the reference in the property points to.
        #
        # Raises an error if the property cannot be resolved to a node.
        if not isinstance(self._value, str):
            _err(
                "expected property '{0}' on {1} in {2} to be"
                "'&foo' or '/bar/foo', not '{3}'".format(
                    self.name, self.node.path, self.spec.path, self._value
                )
            )

        node = self._resolve_pointer(self._value)
        if node is None:
            _err(
                "could not resolve property '{0}' on {1} in {2} to a node".format(
                    self.name, self.node.path, self.spec.path
                )
            )

        return node

    @override
    def _to_nodes(self) -> list[_STNode]:
        # Returns the list of STNodes the phandles in the property point to.
        #
        # Raises an error if the property cannot be resolved to a list of nodes.
        if not isinstance(self._value, list):
            _err(
                "expected property '{0}' on {1} in {2} to be a list of pointers, not '{3}'".format(
                    self.name, self.node.path, self.spec.path, self._value
                )
            )

        pointers = []
        for val in self._value:
            if not isinstance(val, str):
                _err(
                    "expected property '{0}' on {1} in {2} to be a list of '&foo' or '/bar/foo', but it contains '{3}'".format(
                        self.name, self.node.path, self.spec.path, val
                    )
                )
            node = self._resolve_pointer(val)
            if node is None:
                _err(
                    "could not resolve property '{0}' on {1} in {2} to a node".format(
                        self.name, self.node.path, self.spec.path
                    )
                )
            pointers.append(node)

        return pointers

    def _resolve_pointer(self, pointer: str) -> Optional[_STNode]:
        # Used by subclasses to resolve pointers.
        if pointer in self.node.partial_tree.path2node:
            return self.node.partial_tree.path2node[pointer]
        elif pointer in self.node.partial_tree._label2node:
            return self.node.partial_tree._label2node[pointer]
        elif pointer in self.node.stree._label2node:
            return self.node.partial_tree.stree._label2node[pointer]
        else:
            return self.node.stree._path2node.get(pointer)


class _ConfigNode(
    _STPartialTreeNode[
        _ConfigPropertyValType,
        "_ConfigNode",
        "ConfigTree",
        _ConfigBinding,
        _STPropertySpec,
    ]
):
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
        if not "schema" in yaml:
            schemas = []
        elif isinstance(yaml["schema"], str):
            schemas = [yaml["schema"]]
        elif isinstance(yaml["schema"], list):
            schemas = yaml["schema"]
        else:
            _err("Invalid 'schema' property in config node")

        super().__init__(config_tree, schemas, err_on_deprecated, _ConfigNode)

        # Private, don't touch outside the class:
        self._yaml = yaml

        # Public, some of which are initialized properly later:
        self._path = path

        super()._init_node()

    def __repr__(self) -> str:
        if self.binding_paths:
            binding = (
                f"binding {self.binding_paths[0]}"
                if len(self.binding_paths) == 1
                else f"bindings {', '.join(self.binding_paths)}"
            )
        else:
            binding = "no binding"
        return f"<ConfigNode {self.path} in '{self.source_path}', {binding}>"

    @override
    @property
    def name(self) -> str:
        return self.path.rsplit("/", 1)[1] or "/"

    @override
    @property
    def path(self) -> str:
        return self._path

    @override
    @property
    def labels(self) -> list[str]:
        # Only unique names will be interpreted as labels.
        return [self.name] if self.stree.label2node.get(self.name) else []

    @override
    @property
    def parent(self) -> Optional["_ConfigNode"]:
        if self.path == "/":
            return None
        parent_path = self.path.rsplit("/", 1)[0] or "/"
        return self.partial_tree.node_by_path(parent_path)

    @override
    @property
    def children(self) -> OrderedDict[str, _STNode]:
        return OrderedDict(
            [
                (
                    name,
                    self.partial_tree.node_by_path(
                        f"{'' if self.path == '/' else self.path}/{name}"
                    ),
                )
                for name, value in self._yaml.items()
                if isinstance(value, dict)
            ]
        )

    @override
    @property
    def enabled(self) -> bool:
        """
        Returns the value of the node's "enabled" property or true if the node
        has no such property.
        """
        enabled = self.props["enabled"].val if "enabled" in self.props else True
        assert isinstance(enabled, bool)
        return enabled

    @override
    @property
    def _source_prop_names(self) -> list[str]:
        return [
            str(key) for key, value in self._yaml.items() if not isinstance(value, dict)
        ]

    @override
    @property
    def _nonunique_labels(self) -> list[str]:
        # These are candidates for labels but still need to be checked for
        # uniqueness.
        return [self.name]

    @override
    def _instantiate_prop(self, prop_spec: _STPropertySpec) -> _ConfigProperty:
        return _ConfigProperty(
            self._yaml.get(prop_spec.name), prop_spec, self, self._err_on_deprecated
        )


#
# Public classes
#


class ConfigTree(_STPartialTree[_ConfigPropertyValType, _ConfigNode, _ConfigBinding]):
    """
    Represents a tree of YAML configuration nodes.

    Instantiate this class for each YAML configuration source file you want to
    add to the overall merged settings tree. Then add it to the tree calling
    STree.add_partial_tree().

    Also see the docstring of the STPartialTree base class.
    """

    @override
    @staticmethod
    def schema_prop_name() -> str:
        return "schema"

    @override
    @staticmethod
    def enabled_prop_name() -> str:
        return "enabled"

    def __init__(
        self,
        config_path: str,
        bindings_dirs: str | list[str],
        err_on_deprecated: bool = False,
    ):
        """
        ConfigTree constructor.

        config_path:
          The path to the YAML configuration source file from which the tree
          will be constructed.

        bindings_dirs:
          Path or list of paths with directories containing bindings, in YAML
          format. These directories are recursively searched for .yaml files.

        err_on_deprecated (default: False):
            If True, an error is raised if a deprecated property is encountered.
            If False, a warning is logged.
        """
        # If you change the state of this class or its superclass, make sure to
        # update the __deepcopy__ method and corresponding tests, too.  The full
        # state of a partial tree should be reproducible by instantiating it
        # with the same constructor arguments and calling
        # STPartialTree.process().

        # Internal state - do not touch from outside the module.
        with open(config_path, encoding="utf-8") as f:
            overlays = yaml.safe_load(f)

        if not isinstance(overlays, list):
            _err(f"Expected a list of configuration overlays in {config_path}")

        def merge(
            target: dict,
            mount_point_or_label: list[str] | str,
            overlay: dict,
            matched_label: Optional[str] = None,
        ) -> bool:
            """
            Recursively merge overlay into target at mount_point.

            target:
              The target configuration node to merge the overlay node into.

            mount_point:
              The mount point can either represent a path into the current
              target or a globally unique node label. The former is a list of
              mount path parts, the latter a scalar string.

            overlay:
              The configuration overlay to merge into the target.
            """
            if isinstance(mount_point_or_label, list) and len(mount_point_or_label) > 0:
                # We have not yet reached the mount point.
                key = mount_point_or_label.pop(0)
                if key not in target:
                    target[key] = {}
                return merge(target[key], mount_point_or_label, overlay)
            elif isinstance(mount_point_or_label, str):
                if mount_point_or_label in target:
                    # We have found the label in the target.
                    merge(
                        target[mount_point_or_label], [], overlay, mount_point_or_label
                    )
                    return True
                else:
                    # We have not yet found the label and continue to search the
                    # target recursively.
                    for key in target:
                        if not isinstance(target[key], dict):
                            continue
                        found_label = merge(target[key], mount_point_or_label, overlay)
                        if found_label:
                            return True
                    # The label was found nowhere.
                    return False
            else:
                if matched_label and matched_label in target:
                    _err(f"The label {matched_label} is not unique in the target.")

                # We've reached the mount point and can merge the overlay
                # recursively.
                for key in overlay:
                    if (
                        key in target
                        and isinstance(target[key], dict)
                        and isinstance(overlay[key], dict)
                    ):
                        merge(target[key], [], overlay[key], matched_label)
                    else:
                        target[key] = overlay[key]
                return True

        self._yaml: dict[str, Any] = {}
        for overlay in overlays:
            if not isinstance(overlay, dict):
                _err(
                    f"Overlay {overlay} should be a dictionary of mount points"
                    " to configuration nodes."
                )

            for mount_point, overlay in overlay.items():
                if mount_point.startswith("x-"):
                    # Skip extension points (re-usable snippets).
                    continue

                if not isinstance(mount_point, str):
                    _err(
                        f"Mount point {mount_point} of overlay {overlay} should"
                        " be a string."
                    )

                if mount_point.startswith("/"):
                    mount_point = [
                        part
                        for part in filter(
                            lambda part: part != "", mount_point[1:].split("/")
                        )
                    ]
                else:
                    if "/" in mount_point:
                        _err(
                            f"Mount point {mount_point} of overlay {overlay}"
                            " should be either be an absolute path (ie. start with"
                            " '/') or a label not containing any slashes."
                        )

                found_mount_point = merge(self._yaml, mount_point, overlay)
                if not found_mount_point:
                    _err(
                        f"Target label {mount_point} for overlay {overlay} not"
                        " found in configuration."
                    )

        self._path2node: OrderedDict[str, _ConfigNode] = OrderedDict()
        self._label2node: OrderedDict[str, _ConfigNode] = OrderedDict()

        super().__init__(config_path, bindings_dirs, _ConfigNode, err_on_deprecated)

    def __deepcopy__(self, memo) -> ConfigTree:
        """
        Implements support for the standard library copy.deepcopy()
        function on ConfigTree instances.
        """

        clone = ConfigTree(
            self.source_path,
            deepcopy(self.bindings_dirs, memo),
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
    def path2node(self) -> OrderedDict[str, _ConfigNode]:
        return self._path2node

    @override
    def node_by_path(self, path: str) -> _ConfigNode:
        return self._path2node[path]

    @property
    def config_source(self) -> str:
        return yaml.dump(self._yaml, sort_keys=False)

    @override
    def _collect_schemas(self) -> set[str]:
        return set([schema for schema in self._schema_iter()])

    def _schema_iter(self) -> Iterable[str]:
        for _, yaml_node in self._yaml_node_iter(self._yaml):
            schema = yaml_node.get("schema")
            if schema:
                if not isinstance(schema, list):
                    schema = [schema]
                yield from schema

    @override
    def _instantiate_binding(
        self, yaml_source: dict, binding_path: str
    ) -> Optional[_ConfigBinding]:
        return super()._instantiate_binding_internal(
            _ConfigBinding, "schema", yaml_source, binding_path
        )

    @override
    def _init_nodes(self) -> None:
        for path, yaml_node in self._yaml_node_iter(self._yaml):
            node = _ConfigNode(self, path, yaml_node, self._err_on_deprecated)
            self._nodes.append(node)
            self._path2node[path] = node
            self._label2node[node.name] = node

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
