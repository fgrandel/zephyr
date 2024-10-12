# Copyright 2009-2013, 2019 Peter A. Bigot
#
# SPDX-License-Identifier: Apache-2.0

# This implementation is derived from the one in
# [PyXB](https://github.com/pabigot/pyxb), stripped down and modified
# specifically to manage stree._STNode instances.

from __future__ import annotations

from collections import defaultdict
import pdb
from typing import Optional, TYPE_CHECKING

from settings.error import STGraphError

if TYPE_CHECKING:
    from settings.stree import _STMergedNode

class _TarjanState:

    def __init__(self, graph: _STGraph, roots: Optional[list[_STMergedNode]] = None):
        # Immutable state
        self._graph = graph
        self._roots = self._calculate_roots() if roots is None else roots

        # Result of Tarjan's algorithm: a list of strongly connected components
        # partially ordered over dependencies.
        self.ordered_sccs: list[list[_STMergedNode]] = []

        # Mutable internal state
        self._stack: list[_STMergedNode] = []
        self._index: int = 0
        self._tarjan_index: dict[_STMergedNode, Optional[int]] = {
            node: None for node in self._graph._nodes
        }
        self._tarjan_low_link: dict[_STMergedNode, int] = {}

        self._calculate_ordered_sccs()

    def _calculate_roots(self) -> list[_STMergedNode]:
        # Returns a singleton list with the explicit root if one was provided in
        # the constructor.
        #
        # If no explicit root was provided then returns the set of nodes
        # calculated to be roots (i.e., those that have no incoming edges).
        roots: set[_STMergedNode] = set()
        for node in self._graph._nodes:
            if node not in self._graph._reverse_edge_map:
                roots.add(node)

        return sorted(roots, key=lambda n: n.key)

    def _calculate_ordered_sccs(self) -> list[list[_STMergedNode]]:
        # Execute Tarjan's algorithm on the graph.
        #
        # Tarjan's algorithm
        # (http://en.wikipedia.org/wiki/Tarjan%27s_strongly_connected_components_algorithm)
        # computes the strongly-connected components
        # (http://en.wikipedia.org/wiki/Strongly_connected_component)
        # of the graph: i.e., the sets of nodes that form a minimal
        # closed set under edge transition.  In essence, the loops.
        # We use this to detect groups of components that have a
        # dependency cycle, and to impose a total order on components
        # based on dependencies.

        if self._graph._nodes and not self._roots:
            raise STGraphError(
                "TARJAN: No roots found in graph with {} nodes".format(
                    len(self._graph._nodes)
                )
            )

        for root in self._roots:
            self._follow_tarjan_root(root)

        return self.ordered_sccs

    def _follow_tarjan_root(self, root):
        # Recursively do the work of Tarjan's algorithm for a given root node.

        if self._tarjan_index[root] is not None:
            # "Root" was already reached.
            return

        self._tarjan_index[root] = self._tarjan_low_link[root] = self._index
        self._index += 1
        self._stack.append(root)

        source = root
        for target in sorted(self._graph._edge_map[source], key=lambda n: n.key):
            if self._tarjan_index[target] is None:
                self._follow_tarjan_root(target)
                self._tarjan_low_link[root] = min(
                    self._tarjan_low_link[root], self._tarjan_low_link[target]
                )
            elif target in self._stack:
                self._tarjan_low_link[root] = min(
                    self._tarjan_low_link[root], self._tarjan_low_link[target]
                )

        if self._tarjan_low_link[root] == self._tarjan_index[root]:
            scc = []
            while True:
                scc.append(self._stack.pop())
                if root == scc[-1]:
                    break
            self.ordered_sccs.append(scc)


class _STGraph:
    """
    Represent a directed graph with STNode objects as nodes.

    This is used to determine order dependencies among nodes in a
    settings tree. An edge from C{source} to C{target} indicates that
    some aspect of C{source} requires that some aspect of C{target}
    already be available.
    """

    def __init__(self, root: Optional[_STMergedNode] = None):
        # Graph data structures
        self._nodes: set[_STMergedNode] = set()
        self._edge_map: dict[_STMergedNode, set[_STMergedNode]] = defaultdict(set)
        self._reverse_edge_map: dict[_STMergedNode, set[_STMergedNode]] = defaultdict(
            set
        )

        # Tarjan's algorithm state
        self._tarjan_state: Optional[_TarjanState] = None

        # Externally specified root node (if any)
        self._explicit_root: Optional[_STMergedNode] = root

    def add_node(self, node: _STMergedNode):
        """
        Add a node without any dependency to the graph.
        """
        self._nodes.add(node)
        self._reset_tarjan_state()

    def add_edge(self, source: _STMergedNode, target: _STMergedNode):
        """
        Add a directed edge from the C{source} to the C{target} node.

        The source and target nodes are added to the graph if necessary.
        """
        self._edge_map[source].add(target)
        if source != target:
            self._reverse_edge_map[target].add(source)
        self.add_node(source)
        self.add_node(target)
        self._reset_tarjan_state()

    @property
    def ordered_sccs(self) -> list[list[_STMergedNode]]:
        """
        Return the strongly-connected components (SCC) in order.

        The data structure is a list, in dependency order, of SCCs. SCCs are
        groups of nodes that circularly depend on each other. A graph w/o loops
        has precisely one node per SCC.

        Appearance of a node in an SCC earlier in the list indicates that it has
        no dependencies on any node that appears in a subsequent SCC. This order
        is preferred over a depth-first-search order for code generation, since
        it detects loops.

        Computing the SCC order is lazily deferred to the first time the
        ordered_sccs property is accessed after a change to the graph.
        """
        if self._tarjan_state is None:
            self._tarjan_state = _TarjanState(
                self,
                None if self._explicit_root is None else [self._explicit_root],
            )

        return self._tarjan_state.ordered_sccs

    def depends_on(self, node: _STMergedNode):
        """Get the nodes that 'node' directly depends on."""
        return sorted(self._edge_map[node], key=lambda n: n.key)

    def required_by(self, node: _STMergedNode):
        """Get the nodes that directly depend on 'node'."""
        return sorted(self._reverse_edge_map[node], key=lambda n: n.key)

    def _reset_tarjan_state(self):
        # Invalidate the internal state of the Tarjan algorithm whenever the
        # graph changes.
        self._tarjan_state = None
