# Copyright (c) 2024 The Zephyr Project
# SPDX-License-Identifier: Apache-2.0

"""
Both, devicetree and configuration sources, represent abstract trees of nodes
with named properties (key/value pairs). The configuration tree may interface to
the devicetree via references. Both trees combined represent Zephyr's settings
tree (STree). Properties from the devicetree are called hard(ware) settings
while properties from the configuration tree are called soft(ware) settings.

Settings nodes (STNode) will be validated against bindings (bindings.Binding)
which define property specifications (bindings.PropertySpec) for all properties
in the node that shall be used in Zephyr applications.

The combined information from DTS/configuration and binding sources will be made
available through STNode instances that together represent the STree (settings
tree).

An STNode from the devicetree (EDTNode) represents hardware settings while an
STNode from a configuration source (ConfigNode) contains software settings.

The STree will be normalized in the sense that properties belonging to the same
conceptual entity (i.e. functionally depending on the same entity ID or "key")
will be kept in the same STNode (implemented by a MergedSTNode) even if they
come from different sources. In Zephyr an entity ID (or "key") is materialized
by the setting node's full path (including in the EDTNode case only its unit
address).
"""

from __future__ import annotations

from enum import Enum, auto
import os
import logging
import re
import yaml

from abc import ABC, abstractmethod
from collections import defaultdict
from copy import deepcopy
from dataclasses import dataclass, field, InitVar
from typing import (
    Generic,
    Iterable,
    NoReturn,
    Optional,
    Self,
    TypeVar,
    Union,
    TYPE_CHECKING,
    override,
)

from settings.bindings import _NOT_ALPHANUM_OR_UNDERSCORE, _STBinding, _STPropertySpec
from settings.error import STError
from settings.graph import _STGraph
from settings.yamlutil import _IncludeLoader

#
# Private types
#

V = TypeVar("V", bound="_GenericPropertyValType")
N = TypeVar("N", bound="_STNode")
PT = TypeVar("PT", bound="_STPartialTree")

_GenericPropertyValType = Union[
    int,
    str,
    list[int],
    list[str],
    bytes,
    None,
]

#
# Private constants
#

# Logging object
_LOG = logging.getLogger(__name__)


#
# Private classes
#


@dataclass
class _STProperty(ABC, Generic[V, N]):
    """
    Represents a property on a node, as specified in its settings source file
    with additional info from the 'properties:' section of the binding that is
    required to validate the property and make it available as typed C macros or
    settings file system images.

    Only properties mentioned in 'properties:' get created.

    These attributes are available on STProperty objects. Several are just
    convenience accessors for attributes on the bindings.PropertySpec object
    accessible via the 'spec' attribute:

    spec:
      The bindings.PropertySpec object which specifies this property.

    node:
      The Node instance the property is on

    val:
      The value of the property, with the format determined by spec.type, which
      comes from the 'type:' string in the binding. The value of the
      type-to-python mapping for all settings sources is as follows:

        - For 'type: int/array/string/string-array', 'val' is what you'd expect
          (a Python integer or string, or a list of them)

        - For 'type: uint8-array', 'val' is a bytes object

        - For 'type: phandle' and 'type: path', 'val' is the pointed-to Node
          instance

        - For 'type: phandles', 'val' is a list of the pointed-to Node
          instances

        - For 'type: phandle-array', 'val' is a list of ControllerAndData
          instances. See the documentation for that class.

    Also see property docstrings.
    """

    spec: _STPropertySpec
    node: N
    _val: V = field(init=False)

    has_prop: InitVar[bool]
    err_on_deprecated: InitVar[bool]

    def __post_init__(self, has_prop: bool, err_on_deprecated: bool):
        self._val = self._prop_val(has_prop, err_on_deprecated)

    @property
    def val(self) -> V:
        # We need to bypass the dataclass logic to be able
        # to override this property.
        return self._val

    @property
    def name(self) -> str:
        "Convenience for spec.name."
        return self.spec.name

    @property
    def description(self) -> Optional[str]:
        """
        Convenience for spec.description with leading and trailing whitespace
        (including newlines) removed. May be None.
        """
        return self.spec.description.strip() if self.spec.description else None

    @property
    def type(self) -> str:
        "Convenience for spec.type."
        return self.spec.type

    @property
    def val_as_token(self) -> str:
        """
        The value of the property as a token, i.e. with non-alphanumeric
        characters replaced with underscores. This is only safe to access if
        'spec.enum_tokenizable' returns True.
        """
        assert isinstance(self.val, str)
        return str_as_token(self.val)

    @property
    def enum_index(self) -> Optional[int]:
        """
        The index of 'val' in 'spec.enum' (which comes from the 'enum:' list in
        the binding), or None if spec.enum is None.
        """
        enum = self.spec.enum
        return enum.index(self.val) if enum else None

    def _prop_val(
        self,
        has_prop: bool,
        err_on_deprecated: bool,
    ) -> V:
        # has_prop:
        #   Whether this property is present in the node.
        #
        # prop_spec:
        #   PropertySpec from binding
        #
        # err_on_deprecated:
        #   If True, a deprecated property is an error instead of warning.
        name = self.spec.name
        prop_type = self.spec.type
        binding_path = self.spec.path

        if not prop_type:
            _err(f"'{name}' in {binding_path} lacks 'type'")

        if has_prop:
            if self.spec.deprecated:
                msg = (
                    f"'{name}' is marked as deprecated in 'properties:' "
                    f"in {binding_path} for node {self.node.path}."
                )
                if err_on_deprecated:
                    _err(msg)
                else:
                    _LOG.warning(msg)
        else:
            if self.spec.required and self.node.status == "okay":
                _err(
                    f"'{name}' is marked as required in 'properties:' in "
                    f"{binding_path}, but does not appear in {self.node.path}."
                )

            default = self.spec.default
            if default is not None:
                # YAML doesn't have a native format for byte arrays. We need to
                # convert those from an array like [0x12, 0x34, ...]. The
                # format has already been checked in
                # bindings._check_prop_by_type().
                if prop_type == "uint8-array":
                    return bytes(default)  # type: ignore
                return default

            return False if prop_type == "boolean" else None

        if prop_type == "boolean":
            return self._to_bool()

        if prop_type == "int":
            return self._to_num()

        if prop_type == "array":
            return self._to_nums()

        if prop_type == "uint8-array":
            return self._to_bytes()

        if prop_type == "string":
            return self._to_string()

        if prop_type == "string-array":
            return self._to_strings()

        if prop_type in ("phandle", "path"):
            return self._to_node()

        if prop_type == "phandles":
            return self._to_nodes()

        return None

    @abstractmethod
    def _to_bool(self) -> bool:
        "Interpret the property value as a boolean."
        ...

    @abstractmethod
    def _to_num(self) -> int:
        "Interpret the property value as an integer."
        ...

    @abstractmethod
    def _to_nums(self) -> list[int]:
        "Interpret the property value as a list of integers."
        ...

    @abstractmethod
    def _to_bytes(self) -> bytes:
        "Interpret the property value as a bytes object."
        ...

    @abstractmethod
    def _to_string(self) -> str:
        "Interpret the property value as a string."
        ...

    @abstractmethod
    def _to_strings(self) -> list[str]:
        "Interpret the property value as a list of strings."
        ...

    @abstractmethod
    def _to_node(self) -> _STNode:
        "Interpret the property value as a node."
        ...

    @abstractmethod
    def _to_nodes(self) -> list[_STNode]:
        "Interpret the property value as a list of nodes."
        ...


class _STNode(ABC, Generic[V, N]):
    """
    Represents a settings tree node, augmented with information from bindings,
    and with properties convertible to the C types specified in the binding.
    There's a one-to-one correspondence between source nodes and STNodes.

    This abstract base class contains the properties and methods that are common
    to both, partial tree nodes and merged nodes.

    Also see property docstrings.
    """

    def __hash__(self):
        return hash(self.key)

    def __eq__(self, other):
        if not isinstance(other, _STNode):
            return False
        return self.key == other.key

    @property
    @abstractmethod
    def stree(self) -> STree:
        "The STree instance this node is from."
        ...

    @property
    def key(self) -> tuple[str, str, Optional[str]]:
        # This sort key ensures that sibling nodes with the same name will
        # use unit addresses as tiebreakers. That in turn ensures ordinals
        # for otherwise indistinguishable siblings are in increasing order
        # by unit address, which is convenient for displaying output.

        if self.parent:
            parent_path = self.parent.path
        else:
            parent_path = "/"

        return (parent_path, self.name, None)

    @property
    @abstractmethod
    def name(self) -> str:
        "The name of the node"
        ...

    @property
    @abstractmethod
    def path(self) -> str:
        "The settings tree path of the node"
        ...

    @property
    def description(self) -> Optional[str]:
        """
        The description string from the most specific binding for the node, or
        None if the node has no binding. Leading and trailing whitespace
        (including newlines) is removed.
        """
        return self.bindings[0].description if self.bindings else None

    @property
    @abstractmethod
    def compats(self) -> list[str]:
        """
        A list of 'compatible' strings for the node, in the same order that
        they're listed in the .dts file
        """
        ...

    @property
    @abstractmethod
    def props(self) -> dict[str, _STProperty[V, N]]:
        """
        A dict that maps property names to STProperty objects. STProperty
        objects are created for all settings tree properties on the node that
        are mentioned in 'properties:' in the binding.
        """
        ...

    @property
    def labels(self) -> list[str]:
        """
        A list of all of the labels for the node, in the same order as the
        labels were defined in the source, but with duplicates removed.
        """
        return []

    @property
    @abstractmethod
    def parent(self) -> Optional[N]:
        """
        The STNode instance for the parent of the STNode, or None if the node is
        the root node
        """
        ...

    @property
    @abstractmethod
    def children(self) -> dict[str, N]:
        """
        A dictionary with the STNode instances for the children of the node,
        indexed by name
        """
        ...

    def child_index(self, node: N) -> int:
        """
        Get the index of *node* in self.children.

        Raises KeyError if the argument is not a child of this node.
        """
        if not hasattr(self, "_child2index"):
            # Defer initialization of this lookup table until this
            # method is callable to handle parents needing to be
            # initialized before their chidlren. By the time we
            # return from __init__, 'self.children' is callable.
            self._child2index: dict[str, int] = {}
            for index, child_path in enumerate(
                child.path for child in self.children.values()
            ):
                self._child2index[child_path] = index

        return self._child2index[node.path]

    @property
    def has_child_binding(self) -> bool:
        """
        True if any of the node's bindings contains a child-binding definition,
        False otherwise
        """
        return any(binding.child_binding for binding in self.bindings)

    @property
    def required_by(self) -> list[N]:
        "A list with the nodes that directly depend on the node"
        return self.stree._graph.required_by(self)

    @property
    def depends_on(self) -> list[N]:
        "A list with the nodes that the node directly depends on"
        return self.stree._graph.depends_on(self)

    @property
    def status(self) -> str:
        "The node's status property value, as a string."
        return "okay"

    @property
    def read_only(self) -> bool:
        "True if the node has a 'read-only' property, and False otherwise"
        return True

    @property
    def aliases(self) -> list[str]:
        """
        A list of aliases for the node. This is fetched from the /aliases node.
        """
        return []

    @property
    @abstractmethod
    def bindings(self) -> list[_STBinding]:
        """
        The list of bindings for the node, by order of precedcence (i.e. most
        specific first)
        """
        ...

    @property
    @abstractmethod
    def matching_compats(self) -> list[str]:
        """
        A list of 'compatible' strings for the bindings that matched the node,
        or None if the node has no binding
        """
        ...

    @property
    @abstractmethod
    def binding_paths(self) -> list[str | None]:
        """
        A list of paths to the binding files for the node, or None if the node
        has no binding
        """
        ...

    def _binding_by_compat(self, compat: str) -> Optional[_STBinding]:
        # Convenience method to get the binding for the given compatible string.
        return self.stree.binding_by_compat(compat, None)


class _STMergedNode(_STNode):
    """
    Represents a settings tree node that is the result of merging multiple
    settings tree nodes that represent the same entity as identified by their
    absolute path in the overall settings tree. This is used to normalize the
    settings tree.

    Dependency ordinals will be calculated on merged nodes, so that dependencies
    between different settings sources (e.g. from a network interface
    configuration to the corresponding interface driver's devicetree node) will
    be represented, too. This allows configuration clients to determine which
    drivers or other subsystems need to be initialized before the configuration
    can be applied.

    If subsystems and drivers include all their initialization dependencies in
    configuration files, then this ordinal can also be used to determine the
    initialization order of drivers and subsystems.

    Also see superclass and property docstrings.
    """

    def __init__(self, nodes: list[_STPartialTreeNode]):
        """
        Initialize a merged node.

        nodes:
          A list of STNode objects that represent the same entity in the
          settings tree. The list must contain at least one node.

        For internal use only; not meant to be instantiated directly by settings
        library clients.
        """
        # The dependency ordinal is initialized to -1 and will be set by the
        # STree instance when the tree is processed.
        self._dep_ordinal = -1

        assert len(nodes) > 0
        self._nodes: list[_STPartialTreeNode] = nodes

        # Merge the properties of all nodes into a single dict.
        self._props: dict[str, _STProperty] = {}
        for node in self._nodes:
            for prop in node.props.values():
                assert prop.name not in self._props

                merged_node = self

                class MergedProperty(type(prop)):
                    def __init__(self):
                        self._wrapped_prop = prop

                    def __repr__(self) -> str:
                        return f"<MergedProperty '{self.name}' at '{self.node.path}'>"

                    @override
                    @property
                    def node(self) -> _STMergedNode:
                        # Point to the merged node rather than the original
                        # partial tree node.
                        return merged_node

                    @override
                    @property
                    def val(self):
                        # Patches the property to return merged nodes instead of
                        # partial tree nodes for references.
                        if isinstance(super().val, _STPartialTreeNode):
                            return self.node.stree.node_by_path(super().val.path)
                        elif isinstance(super().val, list):
                            return [
                                (
                                    self.node.stree.node_by_path(element.path)
                                    if isinstance(element, _STPartialTreeNode)
                                    else element
                                )
                                for element in super().val
                            ]
                        else:
                            return super().val

                    def __getattr__(self, attr):
                        return getattr(self._wrapped_prop, attr)

                self._props[prop.name] = MergedProperty()

    def __repr__(self) -> str:
        source_paths = (
            self.source_paths[0] if len(self.source_paths) == 1 else self.source_paths
        )
        binding_paths = (
            self.binding_paths[0]
            if len(self.binding_paths) == 1
            else self.binding_paths
        )
        if len(binding_paths) == 0:
            binding_paths = "no binding"
        else:
            binding_paths = f"binding {binding_paths}"

        return f"<STMergedNode {self.path} in '{source_paths}', {binding_paths}>"

    def _merge_attrs(self, attr_name: str) -> list[Optional[str | bool | _STNode]]:
        # Merge the attribute of all nodes into a list with unique entries.
        attrs = []
        for node in self._nodes:
            attr = getattr(node, attr_name)
            if isinstance(attr, dict):
                attrs.extend(attr.values())
            elif isinstance(attr, list):
                attrs.extend(attr)
            else:
                assert attr is None or isinstance(attr, (str, bool, _STNode))
                attrs.append(attr)
        return list(set(attrs))

    def _merge_attr(self, attr_name: str) -> str | _STNode:
        # Assert that the given attribute has the same value on all merged nodes
        # and if so return it.
        values = self._merge_attrs(attr_name)
        assert len(values) == 1
        return values[0]

    @override
    @property
    def stree(self) -> STree:
        strees = {partial_tree.stree for partial_tree in self.partial_trees}
        assert len(strees) == 1
        return next(iter(strees))

    @override
    @property
    def name(self) -> str:
        return self._merge_attr("name")

    @override
    @property
    def path(self) -> str:
        return self._merge_attr("path")

    @override
    @property
    def compats(self) -> list[str]:
        return self._merge_attrs("compats")

    @override
    @property
    def props(self) -> dict[str, _STProperty]:
        return self._props

    @override
    @property
    def labels(self) -> list[str]:
        return self._merge_attrs("labels")

    @override
    @property
    def parent(self) -> Optional[_STMergedNode]:
        parent = self._merge_attr("parent")
        return None if parent is None else self.stree.node_by_path(parent.path)

    @override
    @property
    def children(self) -> dict[str, _STMergedNode]:
        child_paths = {child.path for child in self._merge_attrs("children")}
        return {
            self.stree.node_by_path(child_path).name: self.stree.node_by_path(
                child_path
            )
            for child_path in child_paths
        }

    @override
    @property
    def status(self) -> str:
        return self._merge_attr("status")

    @override
    @property
    def read_only(self) -> bool:
        return any(self._merge_attrs("read_only"))

    @override
    @property
    def aliases(self) -> list[str]:
        return self._merge_attrs("aliases")

    @override
    @property
    def bindings(self) -> list[_STBinding]:
        return self._merge_attrs("bindings")

    @override
    @property
    def matching_compats(self) -> list[str]:
        return self._merge_attrs("matching_compats")

    @override
    @property
    def binding_paths(self) -> list[str | None]:
        return self._merge_attrs("binding_paths")

    @property
    def partial_trees(self) -> list[_STPartialTree]:
        "All partial trees that contributed to this merged node."
        return [node.partial_tree for node in self._nodes]

    @property
    def source_paths(self) -> list[str]:
        "The source paths of all partial trees that contributed to this node."
        return [partial_tree.source_path for partial_tree in self.partial_trees]

    @property
    def dep_ordinal(self) -> int:
        """
        A non-negative integer value such that the value for a node is less than
        the value for all nodes that depend on it.

        The ordinal is defined for all nodes that are not involved in a
        dependency circle, and is unique among nodes in its STree.nodes list.

        In Zephyr we currently assume that the settings tree is free of
        dependency cycles.

        The ordinal is calculated by the STree instance when the tree is
        processed.

        Raises an error when the tree was not processed yet.
        """
        if self._dep_ordinal == -1:
            raise STError(f"Dependency ordinal not set for node {self.path}.")
        return self._dep_ordinal

    @dep_ordinal.setter
    def dep_ordinal(self, value: int) -> None:
        """
        Set the dependency ordinal for the node. This may only called by the
        STree instance while the tree is being processed.

        Should be called only once for each node.
        """
        assert self._dep_ordinal == -1
        self._dep_ordinal = value

    def add_to_graph(
        self,
        graph: _STGraph,
        bound_child: Optional[tuple[_STNode[V, N], _STBinding]] = None,
    ) -> None:
        """
        Process properties of this node and its child-binding related children
        and add all encountered dependencies (i.e. 'phandle' or 'phandles'
        properties as edges from this node to the dependent node. The children
        may come from the same partial tree or from a different one.

        This method only implements dependency logic shared between all source
        file types. Subclasses may override this method to add dependency logic
        for source file specific dependency types.
        """
        if bound_child is None:
            bound_node = self
            bindings = self.bindings

            # Always insert this node into the graph if it is the root node.
            if not self.parent:
                graph.add_node(self)

            # All children depend on this node as parent.
            for child in self.children.values():
                graph.add_edge(child, self)
        else:
            # We are processing a child node that is dependent on the parent
            # due to a child-binding.
            bound_node, child_binding = bound_child
            bindings = [child_binding]

        for binding in bindings:
            for prop_name in binding.prop2specs.keys():
                prop = bound_node.props.get(prop_name)
                if prop is None:
                    continue

                if prop.type == "phandle":
                    if TYPE_CHECKING:
                        assert isinstance(prop.val, _STNode)
                    graph.add_edge(self, prop.val)
                elif prop.type == "phandles":
                    if TYPE_CHECKING:
                        assert isinstance(prop.val, list)
                    for dependent_node in prop.val:
                        graph.add_edge(self, dependent_node)

            # Delegate to merged nodes to add source-specific dependencies for
            # the binding.
            for node in bound_node._nodes:
                node.add_source_specific_deps_to_graph(self, graph, binding)

            # If the binding defines a child binding and any of the children
            # contains a dependency that is defined by that child binding, link
            # the child's dependency to this node as well.
            if binding.child_binding is not None:
                for child in bound_node.children.values():
                    self.add_to_graph(graph, (child, binding.child_binding))

        for node in bound_node._nodes:
            node.add_source_specific_deps_to_graph(self, graph)

    def cast[N](self, cls: N) -> N:
        """
        Try to cast the merged node to the interface of one of the participating
        nodes.
        """
        matching_nodes = [node for node in self._nodes if isinstance(node, cls)]
        if len(matching_nodes) == 1:
            return matching_nodes[0]
        elif len(matching_nodes) == 0:
            raise TypeError(
                f"Node {self.path} cannot be cast to {cls}: no matching source node."
            )
        else:
            raise TypeError(
                f"Node {self.path} cannot be cast to {cls}: more than one matching source node."
            )


class _STPartialTreeNode(Generic[V, N, PT], _STNode[V, N]):
    """
    Represents a settings tree node from a specific settings source file (e.g. a
    devicetree or configuration YAML file). This is the abstract base class for
    all concrete implementations of a partial settings tree.

    Subclasses encapsulate the logic to access the specific source file format
    and expose a source-file independent abstract STNode interface that
    represents the node in the normalized settings tree.

    Also see superclass and property docstrings.
    """

    def __init__(self, partial_tree: PT, compats: list[str], err_on_deprecated: bool):
        """
        For internal use only; not meant to be instantiated directly by settings
        library clients.
        """
        # Private, don't touch outside the class:
        self._prop2specs: dict[str, _STPropertySpec] = {}
        self._specifier2cells: dict[str, list[str]] = {}
        self._err_on_deprecated: bool = err_on_deprecated

        # State exposed through public properties:
        self._compats: list[str] = compats
        self._bindings: list[_STBinding] = []
        self._matching_compats: list[str] = []
        self._binding_paths: list[str | None] = []
        self._props: dict[str, _STProperty[V, N]] = {}

        # Public, some of which are initialized properly later:
        self.partial_tree: PT = partial_tree

    @override
    @property
    def stree(self) -> STree:
        return self.partial_tree.stree

    @override
    @property
    def compats(self) -> list[str]:
        return self._compats

    @override
    @property
    def props(self) -> dict[str, _STProperty[V, N]]:
        return self._props

    @override
    @property
    def bindings(self) -> list[_STBinding]:
        return self._bindings

    @override
    @property
    def matching_compats(self) -> list[str]:
        return self._matching_compats

    @override
    @property
    def binding_paths(self) -> list[str | None]:
        return self._binding_paths

    @property
    def source_path(self) -> str:
        return self.partial_tree.source_path

    def add_source_specific_deps_to_graph(
        self, root_merged: _STMergedNode, graph: _STGraph, binding: Optional[_STBinding]
    ):
        """
        Partial tree nodes may have additional dependencies that are specific to
        the source file type and that _STMergedTreeNode can therefore not be
        aware of without breaking encapsulation. Therefore _STMergedTreeNode
        delegates to this method to let the partial tree node override it and
        add its own dependencies.

        Implementations must add dependencies to the graph as merged nodes, i.e.
        they have to look up the merged node for the node they depend on:

          merged_dependency = self.stree.node_by_path(partial_tree_node.path)
          graph.add_edge(self_merged, merged_dependency)

        By default this method is a no-op.

        root_merged:
            The merged node that is recursively checked for dependencies. This
            id the node that dependencies should be added to.

        graph:
            The graph to add dependencies to.

        binding (default: None):
            If given, then the properties of the binding are to be checked for
            dependencies. If not given, then node-specific dependencies are to
            be added to the graph. Due to type composition the method may be
            called several times for the same node with different bindings.
        """
        pass

    @property
    @abstractmethod
    def _source_prop_names(self) -> list[str]:
        """
        Lists all property names that are actually present in the source
        settings file
        """
        ...

    def _init_bindings(self) -> None:
        # Initializes STNode.matching_compat, STNode.bindings,
        # STNode.binding_paths, STNode._prop2specs and STNode._specifier2cells.
        #
        # STNode.bindings holds the data from the node's binding file, in the
        # format returned by PyYAML (plain Python lists, dicts, etc.), or None
        # if the node has no binding.
        #
        # This relies on the parent of the node having already been initialized,
        # which is guaranteed by going through the nodes in node_iter() order.

        if self.compats:
            # When matching, respect the order of the 'compatible' entries.
            for compat in self.compats:
                binding = self._binding_by_compat(compat)
                if binding is not None:
                    self._bindings.append(binding)
                    self._binding_paths.append(binding.path)
                    self._matching_compats.append(compat)

        # See if we inherit additional compatibles from parent bindings that
        # impose child bindings on us. Child bindings have lower precedence
        # than compatibles.
        bindings_from_parent = self._bindings_from_parent()
        if bindings_from_parent:
            self._bindings.extend(bindings_from_parent)
            self._binding_paths.extend(
                [binding.path for binding in bindings_from_parent]
            )
            self._matching_compats.extend(
                [binding.compatible for binding in bindings_from_parent]
            )

        # More specific bindings override less specific ones.
        for binding in reversed(self.bindings):
            self._prop2specs.update(binding.prop2specs)
            self._specifier2cells.update(binding.specifier2cells)

    def _bindings_from_parent(self) -> list[_STBinding]:
        # Returns the binding from 'child-binding:' in the parent node's
        # binding.

        if not self.parent or not self.parent.bindings:
            return []

        return [
            parent_binding.child_binding
            for parent_binding in self.parent.bindings
            if parent_binding.child_binding
        ]

    def _init_node(self):
        self._init_bindings()
        self._check_undeclared_props()

    def _init_crossrefs(self) -> None:
        # Initializes all properties that require cross-references to other
        # nodes, like 'phandle' and 'phandles'. This is done after all nodes
        # have been initialized.
        self._init_props()

    def _init_props(self) -> None:
        # Instantiates all properties on the node that are mentioned in the
        # binding's 'properties:' section.
        for prop_spec in self._prop2specs.values():
            prop = self._instantiate_prop(prop_spec)

            if prop.val is None:
                # This is an optional property not present in the source.
                continue

            self._props[prop.name] = prop

    @abstractmethod
    def _instantiate_prop(self, prop_spec: _STPropertySpec) -> _STProperty[V, N]:
        # Instantiates a (potentially unset) property on the node.
        ...

    def _check_undeclared_props(self) -> None:
        if not self._prop2specs:
            return

        for prop_name in self._source_prop_names:
            self._check_undeclared_prop(prop_name)

    def _check_undeclared_prop(self, prop_name: str) -> None:
        # Checks that the given property is declared in the binding

        # Allow a few special properties to not be declared in the binding
        if prop_name in {
            "compatible",
            "status",
            "phandle",
        }:
            return

        if prop_name not in self._prop2specs:
            if len(self.binding_paths) == 0:
                _err(
                    f"'{prop_name}' appears in {self.path} in {self.source_path}, "
                    "but has no corresponding binding."
                )

            _err(
                f"'{prop_name}' appears in {self.path} in "
                f"{self.partial_tree.source_path}, but is not declared in "
                f"'properties:' in {self.binding_paths if len(self.binding_paths) > 1 else self.binding_paths[0]}"
            )


class _STPartialTree(ABC, Generic[V, N]):
    """
    A partial settings tree represents the settings from a single settings
    source file (e.g. a devicetree or configuration YAML file) while hiding
    source-file specific implementation details from clients.

    Subclasses encapsulate the logic to access the specific source file format
    and expose a source-file independent abstract STPartialTree interface that
    represents a partial tree within the overall settings tree.

    source_path:
      The source file encapsulated and exposed by this partial tree.

    Also see property docstrings.
    """

    def __init__(
        self,
        source_path: str,
        err_on_deprecated: bool = False,
    ):
        """
        Partial tree constructor.

        source_path:
          The path to the source file that the partial tree is based on (e.g.
          the devicetree or configuration YAML file).

        err_on_deprecated (default: False):
          Processing the tree will error out if any deprecated properties are
          used in the source.

        For internal use only; not meant to be instantiated directly by settings
        library clients.
        """
        # If you change the state of this class or any of its subclasses, make
        # sure to update the __deepcopy__ methods and corresponding tests, too.
        # The full state of a partial tree should be reproducible by
        # instantiating it with the same constructor arguments and calling
        # STPartialTree.process().

        # Public attributes (the rest are properties)
        self.source_path: str = source_path

        # Saved kwarg values for internal use
        self._err_on_deprecated: bool = err_on_deprecated

        # Internal state
        self._is_processed = False
        self._stree: Optional[STree] = None
        self._nodes: list[_STPartialTreeNode[V, N, Self]] = []

    def process(self) -> None:
        """
        Process the partial tree, initializing all nodes, their properties and
        cross-references to other nodes in the same partial tree or dependent
        partial trees from the underlying source file.

        Expects that all dependent partial trees have already been processed.
        """
        self._init_nodes()

        # Public attributes (the rest are properties)
        for node in self._nodes:
            # Initialize properties that may depend on other STNode objects
            # having been created, because they (either always or sometimes)
            # reference other nodes. Must be called separately after all nodes
            # have been created.
            node._init_crossrefs()

        self._is_processed = True
        return self

    @property
    @abstractmethod
    def compats(self) -> set[str]:
        """Get a set of all compatibles in the partial tree."""
        ...

    @property
    def nodes(self) -> list[N]:
        """
        A list of abstract STNode objects for the nodes that appear in the
        source file.

        The partial tree must have been processed before you access this
        property, otherwise it will raise an error.
        """

        if not self._is_processed:
            _err(
                f"Partial tree {self} has not been processed. Attach it to an"
                " STree and call STTree.process() first."
            )
        return self._nodes

    @property
    def stree(self) -> STree:
        if self._stree is None:
            _err(
                f"Partial tree {self} has not been attached to an STree."
                " Call STree.add_partial_tree() first."
            )
        return self._stree

    @stree.setter
    def stree(self, stree: STree) -> None:
        """
        Set the STree instance that the partial tree is part of.

        This is called by STree.add_partial_tree() and should not be called
        directly.
        """
        self._stree = stree

    @property
    @abstractmethod
    def path2node(self) -> dict[str, N]:
        """
        Maps all node paths to the node itself.

        If you want to look up a single node by path, use
        STPartialTree.node_by_path() instead.
        """
        ...

    @abstractmethod
    def node_by_path(self, path: str) -> N:
        """Get a node by path."""
        ...

    def node_iter(self) -> Iterable[N]:
        """Iterator over all STNodes in the partial tree."""
        return self.nodes

    @abstractmethod
    def _init_nodes(self) -> None:
        # Initialize and validate all nodes and their properties in the partial
        # tree.
        ...


#
# Public classes
#


class STree:
    """
    Represents a normalized, merged hardware and software settings tree
    augmented with information from bindings.

    The class constructor defines the following public attribute:

    bindings_dirs:
      List of paths to directories containing bindings, in YAML format.  These
      directories are recursively searched for .yaml files.

    Also see property docstrings.

    The standard library's pickle module can be used to marshal and unmarshal
    STree objects.
    """

    # TODO: Move remaining references to "buses" in STree to edtlib.py.

    class _State(Enum):
        INITIAL = auto()
        HAS_PARTIAL_TREES = auto()
        PARTIAL_TREES_FROZEN = auto()
        HAS_BINDINGS = auto()
        HAS_NODES = auto()
        HAS_ORDINALS = auto()
        PROCESSED = auto()

    def __init__(
        self,
        bindings_dirs: list[str] | str,
        vendor_prefixes: Optional[dict[str, str]] = {},
        err_on_missing_vendor=False,
    ):
        """
        STree constructor.

        bindings_dirs:
          Path or list of paths with directories containing bindings, in YAML
          format. These directories are recursively searched for .yaml files.

        vendor_prefixes (default: None):
          A dict mapping vendor prefixes in compatible properties to their
          descriptions. If given, compatibles in the form "manufacturer,device"
          for which "manufacturer" is neither a key in the dict nor a specially
          exempt set of grandfathered-in cases will cause warnings.

        err_on_missing_vendor (default: False):
          When an unknown
          vendor prefix is used.
        """

        self._processing_state = STree._State.INITIAL

        # Public attribute
        self.bindings_dirs: list[str] = (
            bindings_dirs if isinstance(bindings_dirs, list) else [bindings_dirs]
        )

        # Internal state - do not touch from outside the module.
        self._vendor_prefixes: dict[str, str] = vendor_prefixes
        self._err_on_missing_vendor: bool = err_on_missing_vendor
        self._partial_trees: dict[str, _STPartialTree] = {}
        self._compats = set()
        self._compat2binding: dict[tuple[str, Optional[str]], _STBinding] = {}
        self._binding_paths: list[str] = self._collect_binding_paths()
        self._binding_fname2path: dict[str, str] = {
            os.path.basename(path): path for path in self._binding_paths
        }
        self._path2node: dict[str, _STMergedNode] = {}
        self._graph: _STGraph = _STGraph()

        # Tree-wide lookup tables that will available publicly once the tree has
        # been processed.
        self._nodes: list[_STMergedNode] = []
        self._label2node: dict[str, _STMergedNode] = {}
        self._compat2nodes: dict[str, list[_STMergedNode]] = defaultdict(list)
        self._compat2okay: dict[str, list[_STMergedNode]] = defaultdict(list)
        self._compat2notokay: dict[str, list[_STMergedNode]] = defaultdict(list)
        self._compat2vendor: dict[str, str] = defaultdict(str)
        self._compat2model: dict[str, str] = defaultdict(str)
        self._dep_ord2node: dict[int, _STMergedNode] = {}

    def __repr__(self) -> str:
        return f"<STree>"

    def __deepcopy__(self, memo):
        """
        Implements support for the standard library copy.deepcopy()
        function on ConfigTree instances.
        """
        self._check_state(STree._State.PROCESSED)

        clone = STree(
            deepcopy(self.bindings_dirs, memo),
            deepcopy(self._vendor_prefixes),
            self._err_on_missing_vendor,
        )

        for source_id, partial_tree in self._partial_trees.items():
            clone.add_partial_tree(source_id, deepcopy(partial_tree, memo))

        clone.process()

        return clone

    @property
    def nodes(self) -> list[_STMergedNode]:
        """
        A list of STNode objects for the nodes that appear in the settings tree.
        """
        self._check_state(STree._State.HAS_NODES, STree._State.PROCESSED)
        return self._nodes

    @property
    def label2node(self) -> dict[str, _STMergedNode]:
        """
        A dict that maps a node label to the node with that label.
        """
        self._check_state(STree._State.PROCESSED)
        return self._label2node

    @property
    def compat2nodes(self) -> dict[str, list[_STMergedNode]]:
        """
        A collections.defaultdict that maps each 'compatible' string that
        appears on some node to a list of nodes with that compatible. The
        collection is sorted so that enabled nodes appear first in the
        collection.
        """
        self._check_state(STree._State.PROCESSED)
        return self._compat2nodes

    @property
    def compat2okay(self) -> dict[str, list[_STMergedNode]]:
        """
        Like compat2nodes, but just for nodes with status 'okay'.
        """
        self._check_state(STree._State.PROCESSED)
        return self._compat2okay

    @property
    def compat2notokay(self) -> dict[str, list[_STMergedNode]]:
        """
        Like compat2nodes, but just for nodes with status not 'okay'.
        """
        self._check_state(STree._State.PROCESSED)
        return self._compat2notokay

    @property
    def compat2vendor(self) -> dict[str, str]:
        """
        A collections.defaultdict that maps each 'compatible' string that appears
        on some STNode to a vendor name parsed from vendor_prefixes.
        """
        self._check_state(STree._State.PROCESSED)
        return self._compat2vendor

    @property
    def compat2model(self) -> dict[str, str]:
        """
        A collections.defaultdict that maps each 'compatible' string that appears
        on some STNode to a model name parsed from that compatible.
        """
        self._check_state(STree._State.PROCESSED)
        return self._compat2model

    @property
    def dep_ord2node(self) -> dict[int, _STMergedNode]:
        """
        A dict that maps an ordinal to the node with that dependency ordinal.
        """
        self._check_state(STree._State.PROCESSED)
        return self._dep_ord2node

    @property
    def chosen_nodes(self) -> dict[str, _STMergedNode]:
        """
        A dict that maps the properties defined on the devicetree's /chosen node
        to their values. 'chosen' is indexed by property name (a string), and
        values are converted to STNode objects. Note that properties of the
        /chosen node which can't be converted to a STNode are not included in
        the value.
        """
        self._check_state(STree._State.PROCESSED)

        chosen_nodes: dict[str, _STNode] = {}

        try:
            chosen = self.node_by_path("/chosen")
        except STError:
            return chosen_nodes

        for name, prop in chosen.props.items():
            try:
                chosen_nodes[name] = prop._to_node()
            except STError:
                # DTS value is not phandle or string, or path doesn't exist
                continue

        return chosen_nodes

    @property
    def ordered_sccs(self) -> list[list[_STMergedNode]]:
        """
        A list of lists of Nodes. All elements of each list depend on each
        other, and the Nodes in any list do not depend on any STNode in a
        subsequent list. Each list defines a strongly-connected component (SCC)
        of the graph.

        For an acyclic graph each SCC will be a singleton. Cycles will be
        represented by SCCs with multiple nodes. Cycles are not expected to be
        present in settings tree graphs.
        """

        self._check_state(STree._State.PROCESSED)

        try:
            return self._graph.ordered_sccs
        except Exception as e:
            raise STError(e)

    def partial_tree_by_source_id(self, source_id: str) -> _STPartialTree:
        "Get a partial tree by source ID."
        self._check_state(STree._State.HAS_PARTIAL_TREES, STree._State.PROCESSED)
        return self._partial_trees[source_id]

    def add_partial_tree(self, source_id: str, partial_tree: _STPartialTree) -> Self:
        """
        Add a partial tree to the settings tree.

        If partial trees depend on each other then add base trees first and only
        then add trees that depend on them.

        Once all partial trees have been added, clients have to call
        STree.process() to process the settings tree and make it ready for use.

        The method returns the STree instance to allow method chaining.
        """
        self._check_state(STree._State.INITIAL, STree._State.HAS_PARTIAL_TREES)
        partial_tree.stree = self
        self._compats.update(partial_tree.compats)
        self._partial_trees[source_id] = partial_tree
        self._processing_state = STree._State.HAS_PARTIAL_TREES
        return self

    def process(self) -> None:
        """
        Process all partial trees in the settings tree.

        This method parses all required binding files, initializes all nodes and
        their properties including cross-references, calculates dependency
        ordinals for all nodes in the settings tree, initializes convenience
        lookup tables for clients and sanity-checks the settings tree.

        This method must be called after all partial trees have been added to
        the settings tree before any of the trees public properties may be
        accessed.

        The method returns the STree instance to allow method chaining.
        """
        self._check_state(STree._State.HAS_PARTIAL_TREES)

        self._init_compat2binding()
        self._processing_state = STree._State.HAS_BINDINGS

        path2nodes: dict[str, list[_STNode]] = {}
        for partial_tree in self._partial_trees.values():
            partial_tree.process()
            for path, node in partial_tree.path2node.items():
                if path in path2nodes:
                    path2nodes[path].append(node)
                else:
                    path2nodes[path] = [node]

        for path, nodes in path2nodes.items():
            merged_node = _STMergedNode(nodes)
            self._nodes.append(merged_node)
            self._path2node[path] = merged_node

        self._processing_state = STree._State.HAS_NODES

        self._init_ordinals()
        self._processing_state = STree._State.HAS_ORDINALS

        self._init_luts()
        self._check()
        self._processing_state = STree._State.PROCESSED

        return self

    def node_by_path(self, path: str) -> _STMergedNode:
        "Retrieve a (merged) node by its path."
        self._check_state(STree._State.HAS_NODES, STree._State.PROCESSED)
        return self._path2node[path]

    def chosen_node(self, name: str) -> Optional[_STMergedNode]:
        """
        Returns the STNode pointed at by the property named 'name' in /chosen,
        or None if the property is missing
        """
        return self.chosen_nodes.get(name)

    def binding_by_compat(
        self, compatible: str, on_bus: Optional[str]
    ) -> Optional[_STBinding]:
        """
        Get the binding for a compatible on a bus (if given), or None if there
        is no binding for the compatible[/bus] tuple.
        """
        self._check_state(STree._State.HAS_BINDINGS, STree._State.PROCESSED)
        return self._compat2binding.get((compatible, on_bus))

    def _check_state(self, min: STree._State, max: STree._State = None) -> None:
        expected: set[STree._State] = (
            [min]
            if max is None
            else [
                state for state in STree._State if min.value <= state.value <= max.value
            ]
        )
        if not self._processing_state in expected:
            _err(
                f"STree should be in state '{" or ".join(map(lambda e: e.name, expected))}'"
                f" but is in state '{self._processing_state.name}'."
            )

    def _init_compat2binding(self) -> None:
        # Searches for any binding string mentioned in the devicetree
        # files, with a regex
        bindings_search = re.compile(
            "|".join(re.escape(compat) for compat in self._compats)
        ).search

        for binding_path in self._binding_paths:
            with open(binding_path, encoding="utf-8") as f:
                contents = f.read()

            # As an optimization, skip parsing files that don't contain any of
            # the binding strings.
            if not bindings_search(contents):
                continue

            # Load the binding and check that it actually matches one of the
            # compatibles. Might get false positives above due to comments and
            # stuff.
            try:
                # Parsed PyYAML output (Python lists/dictionaries/strings/etc.,
                # representing the file)
                raw = yaml.load(contents, Loader=_IncludeLoader)
            except yaml.YAMLError as e:
                _err(
                    f"'{binding_path}' appears in binding directories "
                    f"but isn't valid YAML: {e}"
                )
            if TYPE_CHECKING:
                assert isinstance(raw, dict)

            # Convert the raw data to a Binding object, erroring out
            # if necessary.
            binding = self._instantiate_binding(raw, binding_path)

            # Register the binding in self._compat2binding, along with
            # any child bindings that have their own compatibles.
            while binding is not None:
                if binding.compatible:
                    self._register_binding(binding)
                binding = binding.child_binding

    def _collect_binding_paths(self) -> list[str]:
        # Returns a list with the paths to all bindings (.yaml files) in
        # 'bindings_dirs'
        # TODO: Efficiently find .binding.yaml files in the whole tree for
        # improved encapsulation of binding files close to where the
        # corresponding nodes are being used in code.
        binding_paths = []

        for bindings_dir in self.bindings_dirs:
            for root, _, filenames in os.walk(bindings_dir):
                for filename in filenames:
                    if filename.endswith(".yaml") or filename.endswith(".yml"):
                        binding_paths.append(os.path.join(root, filename))

        return binding_paths

    def _instantiate_binding(
        self, yaml_source: dict, binding_path: str
    ) -> Optional[_STBinding]:
        # Convert a 'raw' binding from YAML to a Binding object and return it.
        #
        # Error out if the raw data looks like an invalid binding.
        #
        # Return None if the file doesn't contain a binding or the binding's
        # compatible isn't in self._bindings.

        # Get the 'compatible:' string.
        if yaml_source is None or "compatible" not in yaml_source:
            # Empty file, binding fragment, spurious file, etc.
            return None

        if yaml_source["compatible"] not in self._compats:
            # Not a compatible we care about.
            return None

        # Initialize and return the Binding object.
        return _STBinding(binding_path, self._binding_fname2path, raw=yaml_source)

    def _register_binding(self, binding: _STBinding) -> None:
        # Do not allow two different bindings to have the same
        # 'compatible:'/'on-bus:' combo.
        if TYPE_CHECKING:
            assert binding.compatible
        old_binding = self._compat2binding.get((binding.compatible, binding.on_bus))
        if old_binding:
            msg = (
                f"both {old_binding.path} and {binding.path} have "
                f"'compatible: {binding.compatible}'"
            )
            if binding.on_bus is not None:
                msg += f" and 'on-bus: {binding.on_bus}'"
            _err(msg)

        # Register the binding.
        self._compat2binding[binding.compatible, binding.on_bus] = binding

    def _init_ordinals(self) -> None:
        # Constructs a graph of dependencies (i.e. children, phandles,
        # interrupts, etc) between node instances.
        for node in self.nodes:
            node.add_to_graph(self._graph)

        # The graph is used to compute a partial order of nodes over
        # dependencies such that it is guaranteed that nodes earlier in the list
        # will not depend on any node later in the list. The algorithm supports
        # detecting dependency loops.

        # We use this ordered list to assign ordinals to nodes.
        ordinal = 0
        for scc in self._graph.ordered_sccs:
            # In Zephyr, settings graphs should have no loops, so all
            # strongly-connected components (SCCs) should be singletons. That
            # may change in the future, but for now we only give an ordinal to
            # singletons.
            if len(scc) == 1:
                scc[0].dep_ordinal = ordinal
                ordinal += 1
            else:
                _err(f"Dependency loop detected: {scc}")

    def _init_luts(self) -> None:
        # Initialize node lookup tables (LUTs).

        for node in self.nodes:
            for label in node.labels:
                self._label2node[label] = node

            for compat in node.compats:
                if node.status == "okay":
                    self._compat2okay[compat].append(node)
                else:
                    self._compat2notokay[compat].append(node)

                if compat in self._compat2vendor:
                    continue

                # The regular expression comes from dt-schema.
                compat_re = r"^[a-zA-Z][a-zA-Z0-9,+\-._]+$"
                if not re.match(compat_re, compat):
                    _err(
                        f"node '{node.path}' compatible '{compat}' "
                        "must match this regular expression: "
                        f"'{compat_re}'"
                    )

                if "," in compat and self._vendor_prefixes:
                    vendor, model = compat.split(",", 1)
                    if vendor in self._vendor_prefixes:
                        self._compat2vendor[compat] = self._vendor_prefixes[vendor]
                        self._compat2model[compat] = model

                    # As an exception, the root node can have whatever
                    # compatibles it wants. Other nodes get checked.
                    elif node.path != "/":
                        if self._err_on_missing_vendor:
                            on_error = _err
                        else:
                            on_error = _LOG.warning
                        on_error(
                            f"node '{node.path}' compatible '{compat}' "
                            f"has unknown vendor prefix '{vendor}'"
                        )

        # Place nodes in status "okay" before nodes with other status values.
        for compat, nodes in self._compat2okay.items():
            self._compat2nodes[compat].extend(nodes)

        for compat, nodes in self._compat2notokay.items():
            self._compat2nodes[compat].extend(nodes)

        for scc in self._graph.ordered_sccs:
            node: _STMergedNode = scc[0]
            self._dep_ord2node[node.dep_ordinal] = node

    def _check(self) -> None:
        # Tree-wide checks and warnings.

        for binding in self._compat2binding.values():
            for spec in binding.prop2specs.values():
                if not spec.enum or spec.type != "string":
                    continue

                if not spec.enum_tokenizable:
                    _LOG.warning(
                        f"compatible '{binding.compatible}' "
                        f"in binding '{binding.path}' has non-tokenizable enum "
                        f"for property '{spec.name}': "
                        + ", ".join(repr(x) for x in spec.enum)
                    )
                elif not spec.enum_upper_tokenizable:
                    _LOG.warning(
                        f"compatible '{binding.compatible}' "
                        f"in binding '{binding.path}' has enum for property "
                        f"'{spec.name}' that is only tokenizable "
                        "in lowercase: " + ", ".join(repr(x) for x in spec.enum)
                    )

        # Validate the contents of compatible properties.
        for node in self.nodes:
            if "compatible" not in node.props:
                continue

            compatibles = node.props["compatible"].val

            # _check() runs after _init_compat2binding() has called
            # _dt_compats(), which already converted every compatible
            # property to a list of strings. So we know 'compatibles'
            # is a list, but add an assert for future-proofing.
            assert isinstance(compatibles, list)

            for compat in compatibles:
                # This is also just for future-proofing.
                assert isinstance(compat, str)


#
# Public global functions
#


def load_vendor_prefixes_txt(vendor_prefixes: str) -> dict[str, str]:
    """Load a vendor-prefixes.txt file and return a dict
    representation mapping a vendor prefix to the vendor name.
    """
    vnd2vendor: dict[str, str] = {}
    with open(vendor_prefixes, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()

            if not line or line.startswith("#"):
                # Comment or empty line.
                continue

            # Other lines should be in this form:
            #
            # <vnd><TAB><vendor>
            vnd_vendor = line.split("\t", 1)
            assert len(vnd_vendor) == 2, line
            vnd2vendor[vnd_vendor[0]] = vnd_vendor[1]
    return vnd2vendor


def str_as_token(val: str) -> str:
    """Return a canonical representation of a string as a C token.

    This converts special characters in 'val' to underscores, and
    returns the result."""

    return re.sub(_NOT_ALPHANUM_OR_UNDERSCORE, "_", val)


#
# Private global functions
#


def _err(msg) -> NoReturn:
    raise STError(msg)
