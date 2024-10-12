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

from collections.abc import Mapping
from enum import Enum, auto
import os
import logging
import re
import yaml

from abc import ABC, abstractmethod
from collections import OrderedDict, defaultdict
from copy import deepcopy
from typing import (
    Any,
    Callable,
    Generic,
    NoReturn,
    Optional,
    Self,
    TypeGuard,
    TypeVar,
    Union,
    TYPE_CHECKING,
    override,
)

from settings.bindings import _NOT_ALPHANUM_OR_UNDERSCORE, _STBinding, _STPropertySpec
from settings.error import STError
from settings.graph import _STGraph
from settings.yamlutil import _IncludeLoader

if TYPE_CHECKING:
    # Avoid circular imports.
    from settings.devicetree.edtree import EDTree
    from settings.configuration.configtree import ConfigTree
#
# Private types
#

S = TypeVar("S", bound="_STPropertySpec")
B = TypeVar("B", bound="_STBinding")
V = TypeVar("V")  # property value type
N = TypeVar("N", bound="_STNode")
PTN = TypeVar("PTN", bound="_STPartialTreeNode")
PT = TypeVar("PT", bound="_STPartialTree")

_GenericPropertyValType = Union[
    int,
    str,
    list[int],
    list[str],
    bytes,
    "_STMergedNode",
    list["_STMergedNode"],
    None,
]

_MergedPropertyValType = Any

#
# Private constants
#

# Logging object
_LOG = logging.getLogger(__name__)


#
# Private classes
#


class _STreeApi(ABC, Generic[N]):
    """
    Common API shared by STree and partial trees (EDTree and ConfigTree).

    This API is meant as a shared "normalized view" across all setting tree
    variants. Depending on whether the merged STree, EDTree or ConfigTree view
    is chosen, one can access the settings tree as a whole or devicetree and
    configuration data in isolation.
    """

    @property
    @abstractmethod
    def schemas(self) -> set[str]:
        """
        The set of schemas that are used in this tree.
        """
        ...

    @property
    @abstractmethod
    def nodes(self) -> list[N]:
        """
        A list of STNode objects for the nodes that appear in the settings tree.
        """
        ...

    @property
    @abstractmethod
    def label2node(self) -> Mapping[str, N]:
        """
        A dictionary that maps a node label to the node with that label.
        """
        ...

    @property
    @abstractmethod
    def compat2nodes(self) -> Mapping[str, list[N]]:
        """
        A dictionary that maps each schema assignment that appears
        on some node to a list of nodes that validate against that schema. The
        collection is sorted so that enabled nodes appear first in the
        collection.
        """
        ...

    @property
    @abstractmethod
    def compat2okay(self) -> Mapping[str, list[N]]:
        """
        Like compat2nodes, but for enabled nodes (e.g. status "okay" in DTS
        sources).
        """
        ...

    @property
    @abstractmethod
    def compat2notokay(self) -> Mapping[str, list[N]]:
        """
        Like compat2nodes, but for disabled nodes (e.g. status not "okay" in DTS
        sources).
        """
        ...

    @property
    @abstractmethod
    def compat2vendor(self) -> Mapping[str, str]:
        """
        A dictionary that maps each schema string assigned to some node to a
        vendor name parsed from vendor_prefixes.
        """
        ...

    @property
    @abstractmethod
    def compat2model(self) -> Mapping[str, str]:
        """
        A dictionary that maps each schema string assigned to some node to a
        vendor-specific schema name parsed from that schema (represents the
        vendor-specific hardware programming model in DTS or the vendor specific
        configuration schema in configuration files).
        """
        ...

    @property
    @abstractmethod
    def dep_ord2node(self) -> Mapping[int, N]:
        """
        A dictionary that maps an ordinal to the node with that dependency ordinal.
        """
        ...

    @property
    @abstractmethod
    def ordered_sccs(self) -> list[list[N]]:
        """
        A list of lists of Nodes. All elements of each list depend on each
        other, and the Nodes in any list do not depend on any STNode in a
        subsequent list. Each list defines a strongly-connected component (SCC)
        of the graph.

        For an acyclic graph each SCC will be a singleton. Cycles will be
        represented by SCCs with multiple nodes. Cycles are not expected to be
        present in settings tree graphs.
        """
        ...

    @abstractmethod
    def node_by_path(self, path: str) -> Optional[N]:
        "Retrieve a (merged) node by its path."
        ...


class _STProperty(ABC, Generic[V, N, S]):
    """
    Represents a generic property on a node.

    Most attributes are just convenience accessors for attributes on the
    bindings.PropertySpec object accessible via the 'spec' attribute.

    The following constructor arguments are available:

    spec:
      The bindings.PropertySpec object which specifies this property.

    node:
      The node this property is on.

    Also see property docstrings.
    """

    def __init__(self, spec: S, node: N):
        self.spec: S = spec
        self.node: N = node

    def _post_init(self, val: V):
        self._val: V = val

    @property
    def val(self) -> V:
        """
        The value of the property, with the format determined by spec.type, which
        comes from the 'type:' string in the binding. The value of the
        type-to-python mapping for all settings sources is as follows:

          - For 'type: int/array/string/string-array/boolean', 'val' is what
            you'd expect (a python integer, string, boolean or a list of them)
            except for 'type: uint8-array', where 'val' is a bytes object.

        """
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
    def val_as_tokens(self) -> list[str]:
        """
        The value of the property as a list of tokens, i.e. with
        non-alphanumeric characters replaced with underscores. This is only safe
        to access if 'spec.enum_tokenizable' returns True.
        """
        ret = []
        for subval in self.val if isinstance(self.val, list) else [self.val]:
            assert isinstance(subval, str)
            ret.append(str_as_token(subval))
        return ret

    @property
    def enum_indices(self) -> Optional[list[int]]:
        """
        A list of indices of 'val' in 'spec.enum' (which comes from the 'enum:'
        list in the binding), or None if spec.enum is None.
        """
        enum = self.spec.enum
        val = self.val if isinstance(self.val, list) else [self.val]
        return [enum.index(subval) for subval in val] if enum else None


class _STNode(ABC, Generic[V, N, B, S]):
    """
    Represents a settings tree node, augmented with information from bindings,
    and with properties convertible to the C types specified in the binding.
    There's a one-to-one correspondence between source nodes and STNodes.

    This abstract base class contains the properties and methods that are common
    to both, partial tree nodes and merged nodes.

    Also see property docstrings.
    """

    def __init__(self, cls):
        # The class of the node. This is used to cast merged nodes to the
        # correct type.
        self._cls = cls

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

        parent_path = self.path.rsplit("/", 1)[0] or "/"
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
    def schemas(self) -> list[str]:
        """
        A list of schemas for the node, in the same order that they're listed in
        the source file
        """
        ...

    @property
    @abstractmethod
    def props(self) -> OrderedDict[str, _STProperty[V, N, S]]:
        """
        An ordered dictionary that maps property names to STProperty objects.
        STProperty objects are created for all settings tree properties on the
        node that are mentioned in 'properties:' in the binding. Ordered by the
        order in which the properties are defined in the binding file.
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
    def parent(self) -> Optional[_STNode]:
        """
        The STNode instance for the parent of the STNode, or None if the node is
        the root node
        """
        ...

    @property
    @abstractmethod
    def children(self) -> OrderedDict[str, _STNode]:
        """
        An ordered dictionary with the STNode instances for the children of the
        node, indexed by name, ordered by the order in which they appear in the
        source file.
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
        True if any of the node's properties is of type "node" and contains a
        child binding, False otherwise
        """
        return any(binding.prop2bindings for binding in self.bindings)

    @property
    def required_by(self) -> list[N]:
        "A list with the nodes that directly depend on the node"
        return self._deps_from_merged_node("required_by")

    @property
    def depends_on(self) -> list[N]:
        "A list with the nodes that the node directly depends on"
        return self._deps_from_merged_node("depends_on")

    @property
    def enabled(self) -> bool:
        "Whether the node is enabled or not."
        return True

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
    def bindings(self) -> list[B]:
        """
        The list of bindings for the node, by order of precedcence (i.e. most
        specific first)
        """
        ...

    @property
    @abstractmethod
    def matching_schemas(self) -> list[str]:
        """
        A list of 'schema' strings for the bindings that matched the node,
        or None if the node has no binding
        """
        ...

    @property
    @abstractmethod
    def binding_paths(self) -> list[str]:
        """
        A list of paths to the binding files for the node.
        """
        ...

    @property
    def dep_ordinal(self) -> int:
        merged_node = self.stree.node_by_path(self.path)
        return merged_node.dep_ordinal if merged_node else -1

    @property
    def z_path_id(self) -> str:
        """
        Return the node specific bit of the node's path identifier:

        - the root node's path "/" has path identifier "N"
        - "/foo" has "N_S_foo"
        - "/foo/bar" has "N_S_foo_S_bar"
        - "/foo/bar@123" has "N_S_foo_S_bar_123"

        This is used throughout this file to generate macros related to
        the node.
        """

        components = ["N"]
        if self.parent is not None:
            components.extend(
                f"S_{str2ident(component)}" for component in self.path.split("/")[1:]
            )

        return "_".join(components)

    @property
    def _nonunique_labels(self) -> list[str]:
        # Returns a list of label candidates that may not be unique in the tree.
        # The default implementation returns the labels property.
        return self.labels

    def _deps_from_merged_node(self, attribute_name: str) -> list[N]:
        # Retrieve and cast dependencies from the merged tree.
        merged_node = self.stree.node_by_path(self.path)
        if merged_node is None:
            raise STError(f"Node {self.path} not found in STree.")
        dependent_nodes: list[_STMergedNode] = getattr(merged_node, attribute_name)
        return [
            node.cast(self._cls)
            for node in dependent_nodes
            if node.implements(self._cls)
        ]


class _STMergedProperty(
    _STProperty[_MergedPropertyValType, "_STMergedNode", _STPropertySpec]
):
    def __init__(self, merged_node: _STMergedNode, wrapped_prop: _STProperty):
        self._wrapped_prop = wrapped_prop
        super().__init__(wrapped_prop.spec, merged_node)
        super()._post_init(wrapped_prop.val)

    def __repr__(self) -> str:
        return f"<MergedProperty '{self.name}' at '{self.node.path}'>"

    @override
    @property
    def val(self):
        # Patches the property to return merged nodes instead of
        # partial tree nodes for references.
        if isinstance(super().val, _STPartialTreeNode):
            return self.node.stree._path2node[super().val.path]
        elif isinstance(super().val, list):
            return [
                (
                    self.node.stree._path2node[element.path]
                    if isinstance(element, _STPartialTreeNode)
                    else element
                )
                for element in super().val
            ]
        else:
            # All other scalar or implementation-specific values will be
            # returned verbatim, even if they contain node references.
            # It doesn't make sense to transform nodes to merged nodes when the
            # implementation will never be able to point to something outside
            # its own partial tree.
            # Clients are assumed to deal with this situation if they access
            # partial-tree specific properties.
            return super().val


T = TypeVar("T")

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
        super().__init__(_STMergedNode)

        # The dependency ordinal is initialized to -1 and will be set by the
        # STree instance when the tree is processed.
        self._dep_ordinal = -1

        self._nodes: list[_STPartialTreeNode] = []
        self._props: OrderedDict[str, _STProperty] = OrderedDict()

        assert len(nodes) > 0
        for node in nodes:
            self.add_partial_tree_node(node)

    def __repr__(self) -> str:
        source_paths = (
            self.source_paths[0] if len(self.source_paths) == 1 else self.source_paths
        )
        if len(self.binding_paths) == 0:
            binding_paths = "no binding"
        else:
            binding_paths = f"binding {self.binding_paths[0] if len(self.binding_paths) == 1 else self.binding_paths}"

        return f"<STMergedNode {self.path} in '{source_paths}', {binding_paths}>"

    @override
    @property
    def stree(self) -> STree:
        strees = {partial_tree.stree for partial_tree in self.partial_trees}
        assert len(strees) == 1
        return next(iter(strees))

    @override
    @property
    def name(self) -> str:
        return self._merge_attr("name", str)

    @override
    @property
    def path(self) -> str:
        return self._merge_attr("path", str)

    @override
    @property
    def schemas(self) -> list[str]:
        return self._merge_attrs("schemas", str)

    @override
    @property
    def props(self) -> OrderedDict[str, _STProperty]:
        return self._props

    @override
    @property
    def labels(self) -> list[str]:
        return self._merge_attrs("labels", str)

    @override
    @property
    def parent(self) -> Optional[_STMergedNode]:
        parent = self._merge_optional_attr("parent", _STNode)
        if parent is None:
            return None
        return self.stree.node_by_path(parent.path)

    @override
    @property
    def children(self) -> OrderedDict[str, _STNode]:
        children = self._merge_attrs("children", _STNode)
        merged_nodes = [self.stree.node_by_path(child.path) for child in children]
        assert self._is_typed_list(merged_nodes, _STMergedNode)
        return OrderedDict(
            [
                (
                    merged_node.name,
                    merged_node,
                )
                for merged_node in merged_nodes
            ]
        )

    @property
    def required_by(self) -> list[_STMergedNode]:
        "A list with the nodes that directly depend on the node"
        return self.stree._graph.required_by(self)

    @property
    def depends_on(self) -> list[_STMergedNode]:
        "A list with the nodes that the node directly depends on"
        return self.stree._graph.depends_on(self)

    @override
    @property
    def enabled(self) -> bool:
        return self._merge_attr("enabled", bool)

    @override
    @property
    def read_only(self) -> bool:
        return any(self._merge_attrs("read_only", bool))

    @override
    @property
    def aliases(self) -> list[str]:
        return self._merge_attrs("aliases", str)

    @override
    @property
    def bindings(self) -> list[_STBinding]:
        return self._merge_attrs("bindings", _STBinding)

    @override
    @property
    def matching_schemas(self) -> list[str]:
        return self._merge_attrs("matching_schemas", str)

    @override
    @property
    def binding_paths(self) -> list[str]:
        return self._merge_attrs("binding_paths", str)

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

    def add_partial_tree_node(self, node: _STPartialTreeNode) -> None:
        """
        Add a partial tree node to the merged node.
        """
        self._nodes.append(node)
        self._add_props(node)

    def add_to_graph(
        self,
        graph: _STGraph,
        bound_child: Optional[tuple[_STMergedNode, list[_STBinding]]] = None,
    ) -> None:
        """
        Process properties of this node and its child binding related children
        and add all encountered dependencies (e.g. references between nodes as
        edges from this node to the dependent node. The children may come from
        the same partial tree or from a different one.

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
            children = list(self.children.values())
            assert self._is_typed_list(children, _STMergedNode)
            for child in children:
                graph.add_edge(child, self)
        else:
            # We are processing a child node that is dependent on the parent
            # due to a child binding.
            bound_node, bindings = bound_child

        for binding in bindings:
            for prop_pattern, _ in binding.prop2specs.values():
                for prop in bound_node.props.values():
                    if not re.match(prop_pattern, prop.name):
                        continue
                    if prop.type == "phandle":
                        assert isinstance(prop.val, _STMergedNode)
                        graph.add_edge(self, prop.val)
                    elif prop.type == "phandles":
                        assert self._is_typed_list(prop.val, _STMergedNode)
                        for dependent_node in prop.val:
                            graph.add_edge(self, dependent_node)

            # Delegate to merged nodes to add source-specific dependencies for
            # the binding.
            for node in bound_node._nodes:
                node.add_source_specific_deps_to_graph(self, graph, binding)

            # If the binding defines child bindings and any of the children
            # matches the binding and contains a dependency that is defined by
            # that child binding, link the child's dependency to this node as
            # well.
            for child_pattern, child_bindings in binding.prop2bindings.values():
                children = list(bound_node.children.values())
                assert self._is_typed_list(children, _STMergedNode)
                for child in children:
                    if re.match(child_pattern, child.name):
                        self.add_to_graph(graph, (child, child_bindings))

        for node in bound_node._nodes:
            node.add_source_specific_deps_to_graph(self, graph)

    def implements[PTN](self, cls: type[PTN]) -> bool:
        """
        Check whether the merged node implements the interface of a specific
        node class.
        """
        matching_nodes = [node for node in self._nodes if isinstance(node, cls)]
        return len(matching_nodes) == 1

    def cast[PTN](self, cls: type[PTN]) -> PTN:
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

    def _add_props(self, node: _STPartialTreeNode) -> None:
        for prop in node.props.values():
            assert prop.name not in self._props
            self._props[prop.name] = _STMergedProperty(self, prop)

    def _merge_attrs_untyped(self, attr_name: str) -> list[Any]:
        # Merge the attribute of all nodes into a list with ordered, unique entries.
        attrs: list[Optional[str | bool | _STNode]] = []
        for node in self._nodes:
            attr = getattr(node, attr_name)
            if isinstance(attr, dict):
                attrs.extend(attr.values())
            elif isinstance(attr, list):
                attrs.extend(attr)
            else:
                attrs.append(attr)
        return list(OrderedDict.fromkeys(attrs))

    def _merge_attrs[T](self, attr_name: str, cls: type[T]) -> list[T]:
        attrs = self._merge_attrs_untyped(attr_name)
        assert self._is_typed_list(attrs, cls)
        return attrs

    def _merge_optional_attrs[
        T
    ](self, attr_name: str, cls: type[T]) -> list[Optional[T]]:
        attrs = self._merge_attrs_untyped(attr_name)
        assert self._is_optional_typed_list(attrs, cls)
        return attrs

    def _merge_attr[T](self, attr_name: str, cls: type[T]) -> T:
        # Assert that the given attribute has the same value on all merged nodes
        # and if so return it.
        values = self._merge_attrs(attr_name, cls)
        assert len(values) == 1 and isinstance(values[0], cls)
        return values[0]

    def _merge_optional_attr[T](self, attr_name: str, cls: type[T]) -> Optional[T]:
        # Assert that the given attribute has the same value on all merged nodes
        # and if so return it.
        values = self._merge_optional_attrs(attr_name, cls)
        assert len(values) == 1 and (values[0] is None or isinstance(values[0], cls))
        return values[0]

    def _is_typed_list[T](self, val: list[Any], cls: type[T]) -> TypeGuard[list[T]]:
        "Determines whether all objects in the list are strings"
        return all(isinstance(x, cls) for x in val)

    def _is_optional_typed_list[
        T
    ](self, val: list[Any], cls: type[T]) -> TypeGuard[list[Optional[T]]]:
        "Determines whether all objects in the list are strings"
        return all(x is None or isinstance(x, cls) for x in val)


class _STPartialTreeProperty(Generic[V, PTN, S], _STProperty[V, PTN, S]):
    """
    Represents a property on a partial tree node, as specified in its settings
    source file with additional info from the 'properties:' section of the
    binding that is required to validate the property and make it available as
    typed C macros or settings file system images.

    Only properties mentioned in 'properties:' get created.

    Also see property and superclass docstrings.
    """

    def __init__(
        self,
        spec: S,
        node: PTN,
        has_prop: bool,
        err_on_deprecated: bool,
    ):
        """
        Constructor args:

        spec:
          The PropertySpec object from the binding that specifies this property.

        node:
          The partial tree node this property is on.

        has_prop:
          Whether this property is present in the corresponding node.

        err_on_deprecated:
          If True, a deprecated property in the source is treated as an error
          instead of warning.
        """
        super().__init__(spec, node)
        super()._post_init(self._prop_val(has_prop, err_on_deprecated))

    def _prop_val(
        self,
        has_prop: bool,
        err_on_deprecated: bool,
    ) -> V:
        # Used by subclasses to validate and instantiate a prop value. Only for
        # internal use.
        #
        # has_prop:
        #   Whether this property is present in the node.
        #
        # prop_spec:
        #   PropertySpec from binding
        #
        # err_on_deprecated:
        #   If True, a deprecated property is an error instead of warning.
        #
        # We can ignore type errors in the return format as the typing has
        # already been checked in bindings._check_prop_by_type().
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
            if self.spec.required and self.node.enabled:
                _err(
                    f"'{name}' is marked as required in 'properties:' in "
                    f"{binding_path}, but does not appear in {self.node.path}."
                )

            default = self.spec.default
            if default is not None:
                # YAML doesn't have a native format for byte arrays. We need to
                # convert those from an array like [0x12, 0x34, ...].
                if prop_type == "uint8-array":
                    return bytes(default)  # type: ignore
                return default  # type: ignore

            return False if prop_type == "boolean" else None  # type: ignore

        if prop_type == "boolean":
            return self._to_bool()  # type: ignore

        if prop_type == "int":
            return self._to_num()  # type: ignore

        if prop_type == "array":
            return self._to_nums()  # type: ignore

        if prop_type == "uint8-array":
            return self._to_bytes()  # type: ignore

        if prop_type == "string":
            return self._to_string()  # type: ignore

        if prop_type == "string-array":
            return self._to_strings()  # type: ignore

        return None  # type: ignore

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
        """
        Interpret the property value as a node.

        The implementation of the node will be PTN for a reference to a node in
        the same partial tree or whatever STree.node_by_path() returns for
        cross-tree-references.
        """
        ...

    @abstractmethod
    def _to_nodes(self) -> list[_STNode]:
        """
        Interpret the property value as a list of nodes.

        See _STProperty.to_node() for details on how nodes are represented.
        """
        ...


class _STPartialTreeNode(Generic[V, PTN, PT, B, S], _STNode[V, PTN, B, S]):
    """
    Represents a settings tree node from a specific settings source file (e.g. a
    devicetree or configuration YAML file). This is the abstract base class for
    all concrete implementations of a partial settings tree.

    Subclasses encapsulate the logic to access the specific source file format
    and expose a source-file independent abstract STNode interface that
    represents the node in the normalized settings tree.

    Also see superclass and property docstrings.
    """

    def __init__(
        self,
        partial_tree: PT,
        schemas: list[str],
        err_on_deprecated: bool,
        cls: type[PTN],
    ):
        """
        For internal use only; not meant to be instantiated directly by settings
        library clients.
        """
        super().__init__(cls)

        # Public
        self.partial_tree: PT = partial_tree

        # Private, don't touch outside the class:
        self._prop2specs: dict[str, tuple[re.Pattern, S]] = {}
        self._specifier2cells: dict[str, list[str]] = {}
        self._err_on_deprecated: bool = err_on_deprecated

        # State exposed through public properties:
        self._schemas: list[str] = schemas
        self._bindings: list[B] = []
        self._matching_schemas: list[str] = []
        self._binding_paths: list[str] = []
        self._props: OrderedDict[str, _STProperty[V, PTN, S]] = OrderedDict()

    @override
    @property
    def stree(self) -> STree:
        return self.partial_tree.stree

    @override
    @property
    def schemas(self) -> list[str]:
        return self._schemas

    @override
    @property
    def props(self) -> OrderedDict[str, _STProperty[V, PTN, S]]:
        return self._props

    @override
    @property
    def bindings(self) -> list[B]:
        return self._bindings

    @override
    @property
    def matching_schemas(self) -> list[str]:
        return self._matching_schemas

    @override
    @property
    def binding_paths(self) -> list[str]:
        return self._binding_paths

    @property
    def source_path(self) -> str:
        return self.partial_tree.source_path

    def add_source_specific_deps_to_graph(
        self,
        root_merged: _STMergedNode,
        graph: _STGraph,
        binding: Optional[_STBinding] = None,
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
        # Initializes STNode.matching_schemas, STNode.bindings,
        # STNode.binding_paths, STNode._prop2specs and STNode._specifier2cells.
        #
        # STNode.bindings holds the data from the node's binding file, in the
        # format returned by PyYAML (plain Python lists, dicts, etc.), or None
        # if the node has no binding.
        #
        # This relies on the parent of the node having already been initialized,
        # which is guaranteed by iterating through the tree from its root
        # towards its leafs.

        if self.schemas:
            # When matching, respect schema order.
            for schema in self.schemas:
                binding = self._binding_by_schema(schema)
                if binding is not None:
                    self._bindings.append(binding)
                    self._binding_paths.append(binding.path)
                    self._matching_schemas.append(schema)

        # See if we inherit additional schemas from parent bindings that
        # impose child bindings on us. Child bindings have lower precedence
        # than explicit schemas.
        bindings_from_parent = self._bindings_from_parent()
        if bindings_from_parent:
            self._bindings.extend(bindings_from_parent)
            self._binding_paths.extend(
                [binding.path for binding in bindings_from_parent]
            )
            self._matching_schemas.extend(
                [
                    binding.schema
                    for binding in bindings_from_parent
                    if binding.schema is not None
                ]
            )

        # More specific bindings override less specific ones.
        for binding in reversed(self.bindings):
            self._prop2specs.update(binding.prop2specs)

    def _binding_by_schema(self, schema: str) -> Optional[B]:
        return self._binding_by_schema_and_variant(schema)

    def _binding_by_schema_and_variant(
        self, schema: str, variant: Optional[str] = None
    ) -> Optional[B]:
        # Convenience method to get the binding for the given schema string.
        return self.partial_tree.binding_by_schema(schema, variant)

    def _bindings_from_parent(self) -> list[B]:
        # Returns the bindings from child bindings in the parent node's binding.

        if not self.parent:
            return []

        bindings_from_parent = []

        for parent_binding in self.parent.bindings:
            for (
                child_pattern,
                parent_child_bindings,
            ) in parent_binding.prop2bindings.values():
                if re.match(child_pattern, self.name):
                    bindings_from_parent.extend(parent_child_bindings)

        return bindings_from_parent

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
        for _, prop_spec in self._prop2specs.values():
            prop = self._instantiate_prop(prop_spec)

            val = prop.val
            name = prop_spec.name
            binding_path = prop_spec.binding.path

            if val is None:
                # This is an optional property not present in the source.
                continue

            enum = prop_spec.enum
            for subval in val if isinstance(val, list) else [val]:
                if enum and subval not in enum:
                    _err(
                        f"value of property '{name}' on {self.path} in "
                        f"{self.source_path} ({subval!r}) is not in 'enum' list in "
                        f"{binding_path} ({enum!r})"
                    )

            const = prop_spec.const
            if const is not None and val != const:
                _err(
                    f"value of property '{name}' on {self.path} in "
                    f"{self.source_path} ({val!r}) "
                    "is different from the 'const' value specified in "
                    f"{binding_path} ({const!r})"
                )

            # Skip properties that start with '#', like '#size-cells', and mapping
            # properties like 'gpio-map'/'interrupt-map'
            if name[0] == "#" or name.endswith("-map"):
                continue

            self._props[prop.name] = prop

    @abstractmethod
    def _instantiate_prop(self, prop_spec: S) -> _STProperty[V, PTN, S]:
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
            self.partial_tree.schema_prop_name(),
            self.partial_tree.enabled_prop_name(),
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
                f"{self.source_path}, but is not declared in "
                f"'properties:' in {self.binding_paths if len(self.binding_paths) > 1 else self.binding_paths[0]}"
            )


class _STreeMappingView(Mapping, Generic[N]):

    def __init__(self, stree: STree, dict_name: str, node_cls: type[N]):
        self._node_cls = node_cls
        self._stree = stree
        assert hasattr(stree, dict_name) and isinstance(getattr(stree, dict_name), dict)
        self._dict_name = dict_name
        self._data: dict[str, _STMergedNode] = getattr(stree, dict_name)

    def __getitem__(self, key):
        item = self._data[key]
        try:
            return item.cast(self._node_cls)
        except TypeError:
            raise KeyError(f"No node with label '{key}' in {self._dict_name}")

    def __contains__(self, key):
        return key in self._data

    def __iter__(self):
        return self.values()

    def __len__(self):
        return sum(1 for _ in self.keys())

    def keys(self):
        return (
            key for key, value in self._data.items() if value.implements(self._node_cls)
        )

    def items(self):
        return (
            (key, value.cast(self._node_cls))
            for key, value in self._data.items()
            if value.implements(self._node_cls)
        )

    def values(self):
        return (
            value.cast(self._node_cls)
            for value in self._data.values()
            if value.implements(self._node_cls)
        )


class _STreeListMappingView(Mapping, Generic[N]):

    def __init__(self, stree: STree, dict_name: str, node_cls: type[N]):
        self._node_cls = node_cls
        self._stree = stree
        assert hasattr(stree, dict_name) and isinstance(getattr(stree, dict_name), dict)
        self._dict_name = dict_name
        self._data: dict[str, list[_STMergedNode]] = getattr(stree, dict_name)

    def __getitem__(self, key):
        items = self._data[key]
        return [
            item.cast(self._node_cls)
            for item in items
            if item.implements(self._node_cls)
        ]

    def __contains__(self, key):
        return key in self._data

    def __iter__(self):
        return self.values()

    def __len__(self):
        return sum(1 for _ in self.keys())

    def keys(self):
        return (
            key
            for key, items in self._data.items()
            if any(item.implements(self._node_cls) for item in items)
        )

    def items(self):
        return (
            (
                key,
                [
                    item.cast(self._node_cls)
                    for item in items
                    if item.implements(self._node_cls)
                ],
            )
            for key, items in self._data.items()
            if any(item.implements(self._node_cls) for item in items)
        )

    def values(self):
        return (
            [
                item.cast(self._node_cls)
                for item in items
                if item.implements(self._node_cls)
            ]
            for items in self._data.values()
            if any(item.implements(self._node_cls) for item in items)
        )


class _STPartialTree(Generic[V, PTN, B], _STreeApi[PTN]):
    """
    A partial settings tree represents the settings from a single settings
    source file (e.g. a devicetree or configuration YAML file) while hiding
    source-file specific implementation details from clients.

    Subclasses encapsulate the logic to access the specific source file format
    and expose a source-file independent abstract STPartialTree interface that
    represents a partial tree within the overall settings tree.

    The class constructor defines the following public attribute:

    source_path:
      The source file encapsulated and exposed by this partial tree.

    bindings_dirs:
      List of paths to directories containing bindings, in YAML format. These
      directories are recursively searched for .yaml files.

    Also see property docstrings.
    """

    @staticmethod
    @abstractmethod
    def schema_prop_name() -> str:
        """
        The property name that is used in the tree source format represented by
        this partial tree (e.g. devicetree or configuration files) to identify
        the schema of a node.
        """
        ...

    @staticmethod
    @abstractmethod
    def enabled_prop_name() -> str:
        """
        The property name that is used in the tree source format represented by
        this partial tree (e.g. devicetree or configuration files) to determine
        if a node is enabled or not.
        """
        ...

    def __init__(
        self,
        source_path: str,
        bindings_dirs: str | list[str],
        node_cls: type[PTN],
        err_on_deprecated: bool = False,
    ):
        """
        Partial tree constructor.

        source_path:
          The path to the source file that the partial tree is based on (e.g.
          the devicetree or configuration YAML file).

        bindings_dirs:
          Path or list of paths with directories containing bindings, in YAML
          format. These directories are recursively searched for .yaml files.

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
        self.bindings_dirs: list[str] = (
            bindings_dirs if isinstance(bindings_dirs, list) else [bindings_dirs]
        )

        # Saved kwarg values for internal use
        self._node_cls = node_cls
        self._err_on_deprecated: bool = err_on_deprecated

        # Internal state
        self._is_processed = False
        self._schemas = self._collect_schemas()
        self._schema2binding: dict[tuple[str, Optional[str]], B] = {}
        self._binding_paths: list[str] = self._collect_bindings_paths()
        self._binding_fname2path: dict[str, str] = {
            os.path.basename(path): path for path in self._binding_paths
        }
        self._stree: Optional[STree] = None
        self._nodes: list[PTN] = []

        self._init_schema2binding()

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

    def process(self) -> None:
        """
        Process the partial tree, initializing all nodes, their properties and
        cross-references to other nodes in the same partial tree or dependent
        partial trees from the underlying source file.

        Expects that all dependent partial trees have already been processed.

        Note: __deepcopy__() implementations have to call this method after
        copying state, too.
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
        self._check()

    @override
    @property
    def schemas(self) -> set[str]:
        return self._schemas

    @override
    @property
    def nodes(self) -> list[PTN]:
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
    @override
    def label2node(self) -> Mapping[str, PTN]:
        return _STreeMappingView(self.stree, "label2node", self._node_cls)

    @override
    @property
    def compat2nodes(self) -> Mapping[str, list[PTN]]:
        return _STreeListMappingView(self.stree, "compat2nodes", self._node_cls)

    @override
    @property
    def compat2okay(self) -> Mapping[str, list[PTN]]:
        return _STreeListMappingView(self.stree, "compat2okay", self._node_cls)

    @override
    @property
    def compat2notokay(self) -> Mapping[str, list[PTN]]:
        return _STreeListMappingView(self.stree, "compat2notokay", self._node_cls)

    @override
    @property
    def compat2vendor(self) -> Mapping[str, str]:
        return self.stree.compat2vendor

    @override
    @property
    def compat2model(self) -> Mapping[str, str]:
        return self.stree.compat2model

    @override
    @property
    def dep_ord2node(self) -> Mapping[int, PTN]:
        return _STreeMappingView(self.stree, "dep_ord2node", self._node_cls)

    @override
    @property
    def ordered_sccs(self) -> list[list[PTN]]:
        return [
            [
                node.cast(self._node_cls)
                for node in scc
                if node.implements(self._node_cls)
            ]
            for scc in self.stree.ordered_sccs
            if any(node.implements(self._node_cls) for node in scc)
        ]

    @property
    @abstractmethod
    def path2node(self) -> OrderedDict[str, PTN]:
        """
        Maps all node paths to the node itself. The paths are ordered by the
        order in which the nodes appear in the source file.

        If you want to look up a single node by path, use
        STPartialTree.node_by_path() instead.
        """
        ...

    def binding_by_schema(self, schema: str, variant: Optional[str]) -> Optional[B]:
        """
        Get the binding for a schema and it's optional variant, or None if there
        is no binding for the schema[/variant] tuple.
        """
        return self._schema2binding.get((schema, variant))

    @abstractmethod
    def _collect_schemas(self) -> set[str]:
        """Scan the partial tree for all schemas used in it."""
        ...

    def _collect_bindings_paths(self) -> list[str]:
        # Returns a list with the paths to all bindings (.yaml files) in
        # 'bindings_dirs'
        # TODO: Efficiently find .binding.yaml files in the whole tree for
        # improved encapsulation of binding files close to where the
        # corresponding nodes are being used in code.
        bindings_paths = []

        for bindings_dir in self.bindings_dirs:
            for root, _, filenames in os.walk(bindings_dir):
                for filename in filenames:
                    if filename.endswith(".yaml") or filename.endswith(".yml"):
                        bindings_paths.append(os.path.join(root, filename))

        return bindings_paths

    def _init_schema2binding(self) -> None:
        # Searches for any binding string mentioned in the devicetree
        # files, with a regex
        bindings_search = re.compile(
            "|".join(re.escape(schema) for schema in self.schemas)
        ).search

        for binding_path in self._binding_paths:
            with open(binding_path, encoding="utf-8") as f:
                unparsed_yaml_source = f.read()

            # As an optimization, skip parsing files that don't contain any of
            # the binding strings.
            if not bindings_search(unparsed_yaml_source):
                continue

            try:
                # Parsed PyYAML output (Python lists/dictionaries/strings/etc.,
                # representing the file)
                yaml_source: OrderedDict[str, Any] = yaml.load(
                    unparsed_yaml_source, Loader=_IncludeLoader
                )
            except yaml.YAMLError as e:
                _err(
                    f"'{binding_path}' appears in binding directories "
                    f"but isn't valid YAML: {e}"
                )

            if not isinstance(yaml_source, dict):
                continue

            # Convert the raw YAML data to a Binding object, erroring out if
            # necessary.
            binding = self._instantiate_binding(yaml_source, binding_path)

            # Register the binding in self._schema2binding, along with
            # any child bindings that have their own schema.
            def iterate_bindings(binding: Optional[B]) -> None:
                if not binding or not binding.schema:
                    return

                self._register_binding(binding)
                flattened_child_bindings = [
                    child_binding
                    for _, child_bindings in binding.prop2bindings.values()
                    for child_binding in child_bindings
                ]
                for child_binding in flattened_child_bindings:
                    iterate_bindings(child_binding)

            iterate_bindings(binding)

    @abstractmethod
    def _instantiate_binding(
        self, yaml_source: OrderedDict[str, Any], binding_path: str
    ) -> Optional[B]:
        # Instantiate a binding from YAML and return it.
        #
        # Error out if the YAML source data looks like an invalid binding.
        #
        # Return None if the file doesn't contain a binding or the binding's
        # schema isn't in self._schemas.
        #
        # Implementations should call _instantiate_binding_internal().
        ...

    def _instantiate_binding_internal(
        self,
        binding_cls: type[B],
        binding_schema_prop_name: str,
        yaml_source: dict,
        binding_path: str,
    ) -> Optional[B]:
        # Implements the generic parts of _instantiate_binding() for subclasses.

        if yaml_source is None:
            return None

        # Check that the binding actually matches one of the required schemas.
        # Might get false positives in the textual bindings search due to
        # comments and stuff.

        if binding_schema_prop_name not in yaml_source:
            # Empty file, binding fragment, spurious file, etc.
            return None

        if yaml_source[binding_schema_prop_name] not in self.schemas:
            # Not a schema we care about.
            return None

        # Initialize and return the Binding object.
        return binding_cls(
            path=binding_path,
            yaml_source=yaml_source,
            fname2path=self._binding_fname2path,
        )

    def _register_binding(self, binding: B) -> None:
        # Do not allow two different bindings to have the same
        # schema/variant combo.
        if TYPE_CHECKING:
            assert binding.schema
        old_binding = self._schema2binding.get((binding.schema, binding.variant))
        if old_binding:
            msg = (
                f"both {old_binding.path} and {binding.path} have "
                f"schema '{binding.schema}'"
            )
            if binding.variant is not None:
                msg += f" and 'binding variant {binding.variant}'"
            _err(msg)

        # Register the binding.
        self._schema2binding[binding.schema, binding.variant] = binding

    @abstractmethod
    def _init_nodes(self) -> None:
        # Initialize and validate all nodes and their properties in the partial
        # tree.
        ...

    def _check(self) -> None:
        # Generic tree-wide checks and warnings.
        for binding in self._schema2binding.values():
            for _, spec in binding.prop2specs.values():
                if not spec.enum or spec.type != "string":
                    continue

                if not spec.enum_tokenizable:
                    _LOG.warning(
                        f"schema '{binding.schema}' "
                        f"in binding '{binding.path}' has non-tokenizable enum "
                        f"for property '{spec.name}': "
                        + ", ".join(repr(x) for x in spec.enum)
                    )
                elif not spec.enum_upper_tokenizable:
                    _LOG.warning(
                        f"schema '{binding.schema}' "
                        f"in binding '{binding.path}' has enum for property "
                        f"'{spec.name}' that is only tokenizable "
                        "in lowercase: " + ", ".join(repr(x) for x in spec.enum)
                    )

        # Validate the contents of nodes' schema assignments.
        for node in self.nodes:
            if self.schema_prop_name() not in node.props:
                continue

            schemas = node.props[self.schema_prop_name()].val

            # _check() runs after _init_schema2binding(), which already
            # converted every schema property to a list of strings. So we know
            # 'schemas' is a list, but add an assert for future-proofing.
            assert isinstance(schemas, list)
            for schema in schemas:
                assert isinstance(schema, str)


#
# Public classes
#


class STree(_STreeApi[_STMergedNode]):
    """
    Represents a normalized, merged hardware and software settings tree
    augmented with information from bindings.

    Also see property docstrings.

    The standard library's pickle module can be used to marshal and unmarshal
    STree objects.
    """

    class _State(Enum):
        INITIAL = auto()
        HAS_PARTIAL_TREES = auto()
        PARTIAL_TREES_FROZEN = auto()
        HAS_NODES = auto()
        HAS_ORDINALS = auto()
        PROCESSED = auto()

    def __init__(
        self,
        vendor_prefixes: Optional[dict[str, str]] = {},
        err_on_missing_vendor=False,
    ):
        """
        STree constructor.

        vendor_prefixes (default: None):
          A dict mapping vendor prefixes in schema properties to their
          descriptions. If given, schemas in the form "manufacturer,device"
          for which "manufacturer" is neither a key in the dict nor a specially
          exempt set of grandfathered-in cases will cause warnings.

        err_on_missing_vendor (default: False):
          When an unknown
          vendor prefix is used.
        """

        self._processing_state = STree._State.INITIAL

        # Internal state - do not touch from outside the module.
        self._vendor_prefixes: dict[str, str] = vendor_prefixes or {}
        self._err_on_missing_vendor: bool = err_on_missing_vendor
        self._partial_trees: OrderedDict[type[_STPartialTree], _STPartialTree] = (
            OrderedDict()
        )
        self._path2node: OrderedDict[str, _STMergedNode] = OrderedDict()
        self._graph: _STGraph = _STGraph()

        # Tree-wide lookup tables that will available publicly once the tree has
        # been processed.
        self._nodes: list[_STMergedNode] = []
        self._label2node: dict[str, _STMergedNode] = {}
        # TODO: Shold we rename these properties to schema2*?
        self._compat2nodes: dict[str, list[_STMergedNode]] = defaultdict(list)
        # TODO: Shold we rename these properties to *{dis|en}abled?
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
            deepcopy(self._vendor_prefixes),
            self._err_on_missing_vendor,
        )

        for partial_tree in self._partial_trees.values():
            clone.add_partial_tree(deepcopy(partial_tree, memo))

        clone.process()

        return clone

    @override
    @property
    def schemas(self) -> set[str]:
        return {
            schema
            for partial_tree in self._partial_trees.values()
            for schema in partial_tree.schemas
        }

    @override
    @property
    def nodes(self) -> list[_STMergedNode]:
        return self._nodes

    @override
    @property
    def label2node(self) -> dict[str, _STMergedNode]:
        self._check_state(STree._State.HAS_NODES, STree._State.PROCESSED)
        return self._label2node

    @override
    @property
    def compat2nodes(self) -> dict[str, list[_STMergedNode]]:
        self._check_state(STree._State.PROCESSED)
        return self._compat2nodes

    @override
    @property
    def compat2okay(self) -> dict[str, list[_STMergedNode]]:
        self._check_state(STree._State.PROCESSED)
        return self._compat2okay

    @override
    @property
    def compat2notokay(self) -> dict[str, list[_STMergedNode]]:
        self._check_state(STree._State.PROCESSED)
        return self._compat2notokay

    @override
    @property
    def compat2vendor(self) -> dict[str, str]:
        self._check_state(STree._State.PROCESSED)
        return self._compat2vendor

    @override
    @property
    def compat2model(self) -> dict[str, str]:
        self._check_state(STree._State.PROCESSED)
        return self._compat2model

    @override
    @property
    def dep_ord2node(self) -> dict[int, _STMergedNode]:
        self._check_state(STree._State.PROCESSED)
        return self._dep_ord2node

    @override
    @property
    def ordered_sccs(self) -> list[list[_STMergedNode]]:
        self._check_state(STree._State.PROCESSED)

        try:
            return self._graph.ordered_sccs
        except Exception as e:
            raise STError(e)

    @property
    def edtree(self) -> EDTree:
        # Import here to avoid circular imports
        from settings.devicetree.edtree import EDTree

        edtree = self._partial_trees[EDTree]
        if TYPE_CHECKING:
            assert isinstance(edtree, EDTree)

        return edtree

    @property
    def configtree(self) -> ConfigTree:
        # Import here to avoid circular imports
        from settings.configuration.configtree import ConfigTree

        configtree = self._partial_trees[ConfigTree]
        if TYPE_CHECKING:
            assert isinstance(configtree, ConfigTree)

        return configtree

    @override
    def node_by_path(self, path: str) -> Optional[_STMergedNode]:
        "Retrieve a (merged) node by its path."
        self._check_state(STree._State.HAS_NODES, STree._State.PROCESSED)
        return self._path2node.get(path)

    def add_partial_tree(self, partial_tree: _STPartialTree) -> Self:
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
        self._partial_trees[type(partial_tree)] = partial_tree
        self._processing_state = STree._State.HAS_PARTIAL_TREES
        return self

    def process(self) -> Self:
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

        label2node: dict[str, list[_STMergedNode]] = {}
        for partial_tree in self._partial_trees.values():
            partial_tree.process()
            for path, node in partial_tree.path2node.items():
                if path in self._path2node:
                    merged_node = self._path2node[path]
                    merged_node.add_partial_tree_node(node)
                else:
                    merged_node = _STMergedNode([node])
                    self._nodes.append(merged_node)
                    self._path2node[path] = merged_node

                for label in node._nonunique_labels:
                    if label in label2node:
                        label2node[label].append(merged_node)
                    else:
                        label2node[label] = [merged_node]

            # Only keep unique labels.
            self._label2node = {
                label: nodes[0]
                for label, nodes in label2node.items()
                if len(nodes) == 1
            }

        self._processing_state = STree._State.HAS_NODES

        self._init_ordinals()
        self._processing_state = STree._State.HAS_ORDINALS

        self._init_luts()
        self._processing_state = STree._State.PROCESSED

        return self

    def _check_state(
        self, min: STree._State, max: Optional[STree._State] = None
    ) -> None:
        expected: set[STree._State] = (
            {min}
            if max is None
            else {
                state for state in STree._State if min.value <= state.value <= max.value
            }
        )
        if not self._processing_state in expected:
            _err(
                f"STree should be in state '{" or ".join(map(lambda e: e.name, expected))}'"
                f" but is in state '{self._processing_state.name}'."
            )

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
            for schema in node.schemas:
                if node.enabled:
                    self._compat2okay[schema].append(node)
                else:
                    self._compat2notokay[schema].append(node)

                if schema in self._compat2vendor:
                    continue

                # The regular expression comes from dt-schema.
                schema_re = r"^[a-zA-Z][a-zA-Z0-9,+\-._]+$"
                if not re.match(schema_re, schema):
                    _err(
                        f"node '{node.path}' schema '{schema}' "
                        "must match this regular expression: "
                        f"'{schema_re}'"
                    )

                if "," in schema and self._vendor_prefixes:
                    vendor, model = schema.split(",", 1)
                    if vendor in self._vendor_prefixes:
                        self._compat2vendor[schema] = self._vendor_prefixes[vendor]
                        self._compat2model[schema] = model

                    # As an exception, the root node can follow whatever schema
                    # it wants. Other nodes get checked.
                    elif node.path != "/":
                        on_error: Callable[[str], None]
                        if self._err_on_missing_vendor:
                            on_error = _err
                        else:
                            on_error = _LOG.warning
                        on_error(
                            f"node '{node.path}' schema '{schema}' "
                            f"has unknown vendor prefix '{vendor}'"
                        )

        # Place enabled nodes before disabled nodes.
        for schema, nodes in self._compat2okay.items():
            self._compat2nodes[schema].extend(nodes)

        for schema, nodes in self._compat2notokay.items():
            self._compat2nodes[schema].extend(nodes)

        for scc in self._graph.ordered_sccs:
            scc_node = scc[0]
            self._dep_ord2node[scc_node.dep_ordinal] = scc_node


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


def str2ident(s: str) -> str:
    "Converts 's' to a form suitable for (part of) an identifier."
    return re.sub("[-,.@/+]", "_", s.lower())


#
# Private global functions
#


def _err(msg) -> NoReturn:
    raise STError(msg)
