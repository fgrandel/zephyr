# Copyright (c) 2019 Nordic Semiconductor ASA
# Copyright (c) 2019 Linaro Limited
# Copyright (c) 2024 The Linux Project
# SPDX-License-Identifier: BSD-3-Clause

# This library is layered on top of dtlib, and is not meant to expose it to
# clients. This keeps the header generation script simple.
#

"""
Library for working with devicetrees at a higher level compared to dtlib. Like
dtlib, this library presents a partial tree of devicetree nodes, but the nodes
are augmented with information from bindings (see bindings.py) and include some
interpretation of properties. Some of this interpretation is based on
conventions established by the Linux kernel, so the
Documentation/devicetree/bindings in the Linux source code is sometimes good
reference material.

Each devicetree node (dtlib.Node) gets a corresponding edtree._EDTNode instance,
which has all the information related to the node.

The top-level entry point for the library is the EDTree class. See its
constructor docstrings for details.
"""

# NOTE: tests/test_edtree.py is the test suite for this library.

from __future__ import annotations

from collections import OrderedDict
import logging
import os
import re

from copy import deepcopy
from dataclasses import dataclass
from typing import (
    Any,
    Callable,
    Iterable,
    NoReturn,
    Optional,
    TYPE_CHECKING,
    Tuple,
    Union,
    override,
)

from settings.bindings import (
    _STBinding,
    _STPropertySpec,
)
from settings.devicetree.dtlib import (
    DT,
    Node as DTNode,
    Property as DTProperty,
    to_num as dt_to_num,
    to_nums as dt_to_nums,
    Type as DTType,
)
from settings.error import DTError, EDTError, STBindingError
from settings.graph import _STGraph
from settings.stree import (
    _GenericPropertyValType,
    _STMergedNode,
    _STPartialTree,
    _STPartialTreeNode,
    _STNode,
    _STPartialTreeProperty,
    _STreeMappingView,
    str_as_token,
)

#
# Private types
#


_EDTPropertyValType = Union[
    _GenericPropertyValType,
    "_EDTNode",  # phandle
    list["_EDTNode"],  # phandles
    list[Optional["_EDTControllerAndData"]],  # phandle-array
]


#
# Private constants
#


# Logging object
_LOG = logging.getLogger(__name__)

# "Default" binding for properties which are defined by the spec.
#
# Do not change the _DEFAULT_PROP_TYPES keys without updating the documentation
# for the DT_PROP() macro in include/devicetree.h.

_STATUS_ENUM: list[str] = "ok okay disabled reserved fail fail-sss".split()


_DEFAULT_PROP_TYPES: dict[str, str] = {
    "compatible": "string-array",
    "status": "string",
    "ranges": "compound",  # NUMS or EMPTY
    "reg": "array",
    "reg-names": "string-array",
    "label": "string",
    "interrupts": "array",
    "interrupts-extended": "compound",
    "interrupt-names": "string-array",
    "interrupt-controller": "boolean",
}


def _raw_default_property_for(name: str) -> dict[str, Union[str, bool, list[str]]]:
    ret: dict[str, Union[str, bool, list[str]]] = {
        "type": _DEFAULT_PROP_TYPES[name],
        "required": False,
    }
    if name == "status":
        ret["enum"] = _STATUS_ENUM
    return ret


#
# Private classes
#


class _EDTPropertySpec(_STPropertySpec):

    def __repr__(self) -> str:
        return f"<EDTPropertySpec {self.name} type '{self.type}' in '{self.path}'>"

    @property
    def specifier_space(self) -> Optional[str]:
        """
        The specifier space for the property as given in the binding, or None.
        """
        return self._yaml_source.get("specifier-space")


class _EDTBinding(_STBinding[_EDTPropertySpec, "_EDTBinding"]):
    """
    Represents a parsed devicetree binding.

    This additional init attribute is available:

    specifier2cells:
      A dict that maps specifier space names (like "gpio",
      "clock", "pwm", etc.) to lists of cell names.

      For example, if the binding YAML contains 'pin' and 'flags' cell names
      for the 'gpio' specifier space, like this:

          gpio-cells:
          - pin
          - flags

      Then the Binding object will have a 'specifier2cells' attribute mapping
      "gpio" to ["pin", "flags"]. A missing key should be interpreted as zero
      cells.

    Also see superclass and property docstrings.
    """

    def __init__(
        self,
        path: str,
        yaml_source: Any = None,
        require_schema: bool = True,
        require_description: bool = True,
        allowlist: Optional[list[str]] = None,
        blocklist: Optional[list[str]] = None,
        fname2path: dict[str, str] = {},
        is_child_binding: bool = False,
    ):
        """
        Binding constructor.

        See superclass docstring.
        """

        super().__init__(
            path,
            yaml_source,
            require_schema,
            require_description,
            allowlist,
            blocklist,
            fname2path,
            is_child_binding,
        )

        # Initialize DT-specific look up table.
        self.specifier2cells: dict[str, list[str]] = {}
        for key, val in self.yaml_source.items():
            if key.endswith("-cells"):
                self.specifier2cells[key[: -len("-cells")]] = val

        if "bus" in self.yaml_source:
            bus = yaml_source["bus"]
            if not isinstance(bus, str) and (
                not isinstance(bus, list)
                and not all(isinstance(elem, str) for elem in bus)
            ):
                _err(
                    f"malformed 'bus:' value in {self.path}, "
                    "expected string or list of strings"
                )

            if isinstance(bus, list):
                self._buses = bus
            else:
                # Convert bus into a list
                self._buses = [bus]
        else:
            self._buses = []

    def __repr__(self) -> str:
        schema_name = f" for schema '{self.schema}'" if self.schema else ""
        variant_name = f"  on '{self.variant}'" if self.variant else ""
        basename = os.path.basename(self.path or "")
        return f"<EDTBinding {basename}" + schema_name + variant_name + ">"

    @property
    def schema_prop_name(self) -> str:
        "See the class docstring"
        return _EDTBinding._schema_prop_name_from_yaml_source(self.yaml_source)

    @property
    def buses(self) -> list[str]:
        """
        If nodes with this binding's 'schema' describe a bus, a list describing
        supported protocols (like ["i3c", "i2c"]). An empty list otherwise.
        """
        return self._buses

    @property
    def variant(self) -> Optional[str]:
        "See the class docstring"
        return self.yaml_source.get("on-bus")

    @staticmethod
    def _schema_prop_name_from_yaml_source(yaml_source: OrderedDict[str, Any]) -> str:
        # TODO: deprecate "compatible".
        return "schema" if "schema" in yaml_source else "compatible"

    @classmethod
    def _overridable_props(cls) -> set[str]:
        overridable_props = set(super()._overridable_props())
        overridable_props.add("compatible")
        return overridable_props

    @override
    def _instantiate_prop_spec(
        self, prop_name: str, prop_yaml_source: OrderedDict[str, Any], path: str
    ) -> _EDTPropertySpec:
        return _EDTPropertySpec(prop_name, self, path, prop_yaml_source)

    def _check(
        self,
        require_schema: bool,
        require_description: bool,
        top_level_props: set[str],
        legacy_errors: dict[str, str],
    ):
        top_level_props.update(
            {
                self.schema_prop_name,  # may be "compatible" for legacy support
                "bus",
                "on-bus",
            }
        )

        # TODO: Add "compatible" once we've migrated in-tree bindings.
        legacy_errors.update(
            {
                "#cells": "expected *-cells syntax",
                "child": "use 'bus: <bus>' instead",
                "child-bus": "use 'bus: <bus>' instead",
                "parent": "use 'on-bus: <bus>' instead",
                "parent-bus": "use 'on-bus: <bus>' instead",
            }
        )

        yaml_source = self.yaml_source

        if "on-bus" in yaml_source and not isinstance(yaml_source["on-bus"], str):
            _err(f"malformed 'on-bus:' value in {self.path}, " "expected string")

        for key, val in yaml_source.items():
            if TYPE_CHECKING:
                assert isinstance(key, str)

            if key.endswith("-cells"):
                if not isinstance(val, list) or not all(
                    isinstance(elem, str) for elem in val
                ):
                    _err(
                        f"malformed '{key}:' in {self.path}, "
                        "expected a list of strings"
                    )

            elif key not in top_level_props:
                _err(
                    f"unknown key '{key}' in {self.path}, "
                    "expected one of {', '.join(ok_top)}, or *-cells"
                )

        if "bus" in yaml_source:
            bus = yaml_source["bus"]
            if not isinstance(bus, str) and (
                not isinstance(bus, list)
                and not all(isinstance(elem, str) for elem in bus)
            ):
                _err(
                    f"malformed 'bus:' value in {self.path}, "
                    "expected string or list of strings"
                )

            if isinstance(bus, list):
                self._buses = bus
            else:
                # Convert bus into a list
                self._buses = [bus]

        if "on-bus" in yaml_source and not isinstance(yaml_source["on-bus"], str):
            _err(f"malformed 'on-bus:' value in {self.path}, " "expected string")

        super()._check(
            require_schema, require_description, top_level_props, legacy_errors
        )

    def _check_properties(
        self,
        top_level_props: set[str],
        ok_prop_keys: set[str],
        ok_prop_types: dict[str, tuple[type | list[type], bool]],
    ) -> None:
        ok_prop_keys.add("specifier-space")
        ok_prop_types.update(
            {
                "boolean": (bool, False),
                "path": (str, False),
                "phandle": (str, False),
                "phandles": (str, False),
                "phandle-array": (str, False),
                "compound": (str, False),
            }
        )
        super()._check_properties(top_level_props, ok_prop_keys, ok_prop_types)

    def _check_prop_by_source_specific_type(
        self,
        prop_name: str,
        prop_type: str,
        prop_spec_source: dict,
        binding_path: Optional[str],
    ) -> None:
        if "specifier-space" in prop_spec_source and prop_type != "phandle-array":
            _err(
                f"'specifier-space' in 'properties: {prop_name}' "
                f"has type '{prop_type}', expected 'phandle-array'"
            )

        if prop_type == "phandle-array":
            if (
                not prop_name.endswith("s")
                and not "specifier-space" in prop_spec_source
            ):
                _err(
                    f"'{prop_name}' in 'properties:' in {binding_path} "
                    f"has type 'phandle-array' and its name does not end in 's', "
                    f"but no 'specifier-space' was provided."
                )


_DEFAULT_PROP_BINDING: _EDTBinding = _EDTBinding(
    path="<built-in>",
    yaml_source={
        "properties": {
            name: _raw_default_property_for(name) for name in _DEFAULT_PROP_TYPES
        },
    },
    require_schema=False,
    require_description=False,
)

_DEFAULT_PROP_SPECS: dict[str, _EDTPropertySpec] = {
    name: _EDTPropertySpec(
        name,
        _DEFAULT_PROP_BINDING,
        "<built-in>",
        _DEFAULT_PROP_BINDING.yaml_source["properties"][name],
    )
    for name in _DEFAULT_PROP_TYPES
}


class _EDTProperty(
    _STPartialTreeProperty[_EDTPropertyValType, "_EDTNode", _EDTPropertySpec]
):
    """
    Represents a devicetree property.

    See docstring of the STProperty base class for default types.

    For EDTree-specific types, the val-property returns:

      - For 'type: phandle' and 'type: path', 'val' is the pointed-to Node
        instance

      - For 'type: phandles', 'val' is a list of the pointed-to Node
        instances

      - For 'type: phandle-array', 'val' is a list of ControllerAndData
        instances. See the documentation for that class.


    Note: Properties of type 'compound' currently do not get EDTProperty
    instances, as it's not clear what to generate for them.

    Also see docstring of the STProperty base class.
    """

    def __init__(
        self,
        dt_prop: Optional[DTProperty],
        prop_spec: _EDTPropertySpec,
        node: _EDTNode,
        err_on_deprecated: bool,
    ):
        """
        For internal use only; not meant to be instantiated directly by settings
        library clients.
        """
        # dt_prop:
        #   The dtlib.Property instance for the property, or None if the property
        #   is not present in the devicetree.
        # prop_spec:
        #   The STPropertySpec instance for the property.
        # node:
        #   The EDTNode instance the property is on.
        # err_on_deprecated:
        #   True if an error should be raised if the property is deprecated.
        self._dt_prop = dt_prop
        super().__init__(prop_spec, node, bool(dt_prop), err_on_deprecated)

    def __str__(self):
        return self._dt_prop.__str__()

    def __repr__(self):
        return (
            f"<EDTProperty '{self.name}' at '{self.node.path}' in "
            f"'{self.spec.path}'>"
        )

    @override
    def _prop_val(
        self,
        has_prop: bool,
        err_on_deprecated: bool,
    ) -> _EDTPropertyValType:
        val = super()._prop_val(has_prop, err_on_deprecated)

        prop_type = self.spec.type

        if has_prop and val is None:
            if prop_type in ("phandle", "path"):
                return self._to_node()

            if prop_type == "phandles":
                return self._to_nodes()

            if prop_type == "phandle-array":
                specifier_space = self.spec.specifier_space
                return self._standard_phandle_val_list(specifier_space)

            # prop_type == "compound". Checking that the 'type:'
            # value is valid is done in _check_prop_by_type().
            #
            # 'compound' is a dummy type for properties that don't fit any of the
            # patterns above, so that we can require all entries in 'properties:'
            # to have a 'type: ...'. No Property object is created for it.
        return val

    @override
    def _to_bool(self) -> bool:
        if TYPE_CHECKING:
            assert self._dt_prop is not None
        if self._dt_prop.type != DTType.EMPTY:
            _err(
                "'{0}' in {1} is defined with 'type: boolean' in {2}, "
                "but is assigned a value ('{3!r}') instead of being empty "
                "('{0};')".format(
                    self.name, self.node.path, self.spec.path, self._dt_prop.value
                )
            )
        return True

    @override
    def _to_num(self) -> int:
        if TYPE_CHECKING:
            assert self._dt_prop is not None
        return self._dt_prop.to_num()

    @override
    def _to_nums(self) -> list[int]:
        if TYPE_CHECKING:
            assert self._dt_prop is not None
        return self._dt_prop.to_nums()

    @override
    def _to_bytes(self) -> bytes:
        if TYPE_CHECKING:
            assert self._dt_prop is not None
        return self._dt_prop.to_bytes()

    @override
    def _to_string(self) -> str:
        if TYPE_CHECKING:
            assert self._dt_prop is not None
        return self._dt_prop.to_string()

    @override
    def _to_strings(self) -> list[str]:
        if TYPE_CHECKING:
            assert self._dt_prop is not None
        return self._dt_prop.to_strings()

    @override
    def _to_node(self) -> _STNode:
        if TYPE_CHECKING:
            assert self._dt_prop is not None
        try:
            dt_node = (
                self._dt_prop.to_path()
                if self.spec.type == "path"
                else self._dt_prop.to_node()
            )
        except DTError as e:
            raise EDTError(e) from e

        return self.node.partial_tree._dtnode2enode[dt_node]

    @override
    def _to_nodes(self) -> list[_STNode]:
        # This type is a bit high-level for dtlib as it involves
        # information from bindings and *-names properties, so there's no
        # to_phandle_array() in dtlib. Do the type check ourselves.
        if TYPE_CHECKING:
            assert self._dt_prop is not None
        if self._dt_prop.type not in (
            DTType.PHANDLE,
            DTType.PHANDLES,
            DTType.PHANDLES_AND_NUMS,
        ):
            _err(
                f"expected property '{self.spec.name}' in {self.node.path} in "
                f"{self.node.source_path} to be assigned "
                f"with '{self.spec.name} = < &foo ... &bar 1 ... &baz 2 3 >' "
                f"(a mix of phandles and numbers), not '{self._dt_prop}'"
            )

        try:
            return [
                self.node.partial_tree._dtnode2enode[node]
                for node in self._dt_prop.to_nodes()
            ]
        except DTError as e:
            raise EDTError(e) from e

    def _standard_phandle_val_list(
        self, specifier_space: Optional[str]
    ) -> list[Optional[_EDTControllerAndData]]:
        # Parses a property like
        #
        #     <prop.name> = <phandle cell phandle cell ...>;
        #
        # where each phandle points to a controller node that has a
        #
        #     #<specifier_space>-cells = <size>;
        #
        # property that gives the number of cells in the value after the
        # controller's phandle in the property.
        #
        # E.g. with a property like
        #
        #     pwms = <&foo 1 2 &bar 3>;
        #
        # If 'specifier_space' is "pwm", then we should have this elsewhere
        # in the tree:
        #
        #     foo: ... {
        #             #pwm-cells = <2>;
        #     };
        #
        #     bar: ... {
        #             #pwm-cells = <1>;
        #     };
        #
        # These values can be given names using the <specifier_space>-names:
        # list in the binding for the phandle nodes.
        #
        # Also parses any
        #
        #     <specifier_space>-names = "...", "...", ...
        #
        # Returns a list of Optional[ControllerAndData] instances.
        #
        # An index is None if the underlying phandle-array element is
        # unspecified.
        if TYPE_CHECKING:
            assert self._dt_prop is not None

        dt_prop = self._dt_prop

        if not specifier_space:
            if dt_prop.name.endswith("gpios"):
                # There's some slight special-casing for *-gpios properties in that
                # e.g. foo-gpios still maps to #gpio-cells rather than
                # #foo-gpio-cells
                specifier_space = "gpio"
            else:
                # Strip -s. We've already checked that property names end in -s
                # if there is no specifier space in _check_prop_by_type().
                specifier_space = dt_prop.name[:-1]

        res: list[Optional[_EDTControllerAndData]] = []

        for item in _phandle_val_list(dt_prop, specifier_space):
            if item is None:
                res.append(None)
                continue

            controller_node, data = item
            mapped_controller, mapped_data = _EDTProperty._map_phandle_array_entry(
                dt_prop.node, controller_node, data, specifier_space
            )

            controller = self.node.partial_tree._dtnode2enode[mapped_controller]
            # We'll fix up the names below.
            res.append(
                _EDTControllerAndData(
                    node=self.node,
                    controller=controller,
                    data=self.node._named_cells(
                        controller, mapped_data, specifier_space
                    ),
                    name=None,
                    basename=specifier_space,
                )
            )

        _add_names(self._dt_prop.node, specifier_space, res)

        return res

    @staticmethod
    def _map_phandle_array_entry(
        dt_child: DTNode, dt_parent: DTNode, child_spec: bytes, basename: str
    ) -> Tuple[DTNode, bytes]:
        # Returns a (<controller>, <data>) tuple with the final destination after
        # mapping through any '<basename>-map' (e.g. gpio-map) properties. See
        # _map_interrupt().

        def spec_len_fn(node):
            prop_name = f"#{basename}-cells"
            if prop_name not in node.props:
                _err(
                    f"expected '{prop_name}' property on {node!r} "
                    f"(referenced by {dt_child!r})"
                )
            return node.props[prop_name].to_num()

        # Do not require <prefix>-controller for anything but interrupts for now
        return _map(
            basename,
            dt_child,
            dt_parent,
            child_spec,
            spec_len_fn,
            require_controller=False,
        )


class _EDTNode(
    _STPartialTreeNode[
        _EDTPropertyValType, "_EDTNode", "EDTree", _EDTBinding, _EDTPropertySpec
    ]
):
    """
    Represents a devicetree node, augmented with information from bindings, and
    with some interpretation of devicetree properties. There's a one-to-one
    correspondence between devicetree nodes and Nodes.

    These attributes are initialized by the constructor:

    ranges:
      A list of Range objects extracted from the node's ranges property.
      The list is empty if the node does not have a range property.

    regs:
      A list of Register objects for the node's registers

    interrupts:
      A list of ControllerAndData objects for the interrupts generated by the
      node. The list is empty if the node does not generate interrupts.

    pinctrls:
      A list of PinCtrl objects for the pinctrl-<index> properties on the
      node, sorted by index. The list is empty if the node does not have any
      pinctrl-<index> properties.

    bus_node:
      Like on_bus, but contains the EDTNode for the bus controller, or None if the
      node is not on a bus.

    Also see superclass and property docstrings.
    """

    def __init__(
        self,
        dt_node: DTNode,
        edtree: EDTree,
        err_on_deprecated: bool,
        default_prop_types: bool,
        support_fixed_partitions_on_any_bus: bool,
    ):
        """
        For internal use only; not meant to be instantiated directly by settings
        library clients.
        """
        schema_prop_name = edtree.schema_prop_name()
        schemas = (
            dt_node.props[schema_prop_name].to_strings()
            if schema_prop_name in dt_node.props
            else []
        )

        super().__init__(edtree, schemas, err_on_deprecated, _EDTNode)

        # Private, don't touch outside the class:
        self._dt_node: DTNode = dt_node
        self._default_prop_types: bool = default_prop_types

        # Public, some of which are initialized properly later:
        self.ranges: list[_EDTRange] = []
        self.regs: list[_EDTRegister] = []
        self.interrupts: list[_EDTControllerAndData] = []
        self.pinctrls: list[_EDTPinCtrl] = []
        self.bus_node = self._bus_node(support_fixed_partitions_on_any_bus)

        self._init_node()

    def __repr__(self) -> str:
        if self.binding_paths:
            binding = (
                f"binding {self.binding_paths[0]}"
                if len(self.binding_paths) == 1
                else f"bindings {', '.join(self.binding_paths)}"
            )
        else:
            binding = "no binding"
        return f"<EDTNode {self.path} in '{self.partial_tree.source_path}', {binding}>"

    @override
    @property
    def key(self):
        (parent_path, name, _) = super().key

        # This sort key ensures that sibling nodes with the same name will
        # use unit addresses as tiebreakers. That in turn ensures ordinals
        # for otherwise indistinguishable siblings are in increasing order
        # by unit address, which is convenient for displaying output.

        if self.unit_addr is not None:
            name = self.name.rsplit("@", 1)[0]
            unit_addr = self.unit_addr
        else:
            unit_addr = -1

        return (parent_path, name, unit_addr)

    @override
    @property
    def name(self) -> str:
        return self._dt_node.name

    @property
    def unit_addr(self) -> Optional[int]:
        """
        An integer with the ...@<unit-address> portion of the node name,
        translated through any 'ranges' properties on parent nodes, or None if
        the node name has no unit-address portion. PCI devices use a different
        node name format ...@<dev>,<func> or ...@<dev> (e.g. "pcie@1,0"), in
        this case None is returned.
        """

        # TODO: Return a plain string here later, like dtlib.Node.unit_addr?

        # PCI devices use a different node name format (e.g. "pcie@1,0")
        if "@" not in self.name or self.is_pci_device:
            return None

        try:
            addr = int(self.name.split("@", 1)[1], 16)
        except ValueError:
            _err(f"{self!r} has non-hex unit address")

        return self._translate(addr, self._dt_node)

    @override
    @property
    def path(self) -> str:
        return self._dt_node.path

    @property
    def label(self) -> Optional[str]:
        """
        The text from the 'label' property on the node, or None if the node has
        no 'label'
        """

        if "label" in self._dt_node.props:
            return self._dt_node.props["label"].to_string()
        return None

    @override
    @property
    def labels(self) -> list[str]:
        """
        This corresponds to the actual devicetree source labels, unlike the
        "label" attribute, which is the value of a devicetree property named
        "label".

        Also see the superclass docstring.
        """
        return self._dt_node.labels

    @override
    @property
    def parent(self) -> Optional[_EDTNode]:
        return (
            None
            if self._dt_node.parent is None
            else self.partial_tree._dtnode2enode.get(self._dt_node.parent)
        )

    @override
    @property
    def children(self) -> OrderedDict[str, _STNode]:
        # Could be initialized statically too to preserve identity, but not
        # sure if needed. Parent nodes being initialized before their children
        # would need to be kept in mind.
        return OrderedDict(
            [
                (name, self.partial_tree._dtnode2enode[node])
                for name, node in self._dt_node.nodes.items()
            ]
        )

    @override
    @property
    def enabled(self) -> bool:
        """
        Returns True if the node's status property is not set, "okay" or "ok",
        otherwise False.
        """
        return self.status == "okay"

    @property
    def status(self) -> str:
        """
        Returns the node's status property, "ok" will be returned as "okay".
        """
        status = self._dt_node.props.get("status")

        if status is None:
            return "okay"

        as_string = status.to_string()
        return "okay" if as_string == "ok" else as_string

    @override
    @property
    def read_only(self) -> bool:
        return "read-only" in self._dt_node.props

    @override
    @property
    def aliases(self) -> list[str]:
        return [
            alias
            for alias, dt_node in self._dt_node.dt.alias2node.items()
            if dt_node is self._dt_node
        ]

    @property
    def buses(self) -> list[str]:
        """
        If the node is a bus node (has a 'bus:' key in one of its binding), then
        this attribute holds the list of supported bus types, e.g. ["i2c"],
        ["spi"] or ["i3c", "i2c"] if multiple protocols are supported via the
        same bus. If the node is not a bus node, then this attribute is an empty
        list.
        """
        buses = set()
        for binding in self.bindings:
            buses.update(binding.buses)
        return list(buses)

    @property
    def on_buses(self) -> list[str]:
        """
        The bus the node appears on, e.g. ["i2c"], ["spi"] or ["i3c", "i2c"] if
        multiple protocols are supported via the same bus. The bus is determined
        by searching upwards for a parent node whose binding has a 'bus:' key,
        returning the value of the first 'bus:' key found. If none of the node's
        parents has a 'bus:' key, this attribute is an empty list.
        """
        bus_node = self.bus_node
        return bus_node.buses if bus_node else []

    @property
    def flash_controller(self) -> _EDTNode:
        """
        The flash controller for the node. Only meaningful for nodes
        representing flash partitions.
        """

        # The node path might be something like
        # /flash-controller@4001E000/flash@0/partitions/partition@fc000. We go
        # up two levels to get the flash and check its compat. The flash
        # controller might be the flash itself (for cases like NOR flashes).
        # For the case of 'soc-nv-flash', we assume the controller is the
        # parent of the flash node.

        if not self.parent or not self.parent.parent:
            _err(f"flash partition {self!r} lacks parent or grandparent node")

        controller = self.parent.parent
        if "soc-nv-flash" in controller.matching_schemas:
            if controller.parent is None:
                _err(f"flash controller '{controller.path}' cannot be the root node")
            return controller.parent
        return controller

    @property
    def spi_cs_gpio(self) -> Optional[_EDTControllerAndData]:
        """
        The device's SPI GPIO chip select as a ControllerAndData instance, if it
        exists, and None otherwise. See
        Documentation/devicetree/bindings/spi/spi-controller.yaml in the Linux kernel.
        """

        if not (
            "spi" in self.on_buses
            and self.bus_node
            and "cs-gpios" in self.bus_node.props
        ):
            return None

        if not self.regs:
            _err(
                f"{self!r} needs a 'reg' property, to look up the "
                "chip select index for SPI"
            )

        parent_cs_lst = self.bus_node.props["cs-gpios"].val
        if TYPE_CHECKING:
            assert isinstance(parent_cs_lst, list)

        # cs-gpios is indexed by the unit address
        cs_index = self.regs[0].addr
        if TYPE_CHECKING:
            assert isinstance(cs_index, int)

        if cs_index >= len(parent_cs_lst):
            _err(
                f"index from 'regs' in {self!r} ({cs_index}) "
                "is >= number of cs-gpios in "
                f"{self.bus_node!r} ({len(parent_cs_lst)})"
            )

        ret = parent_cs_lst[cs_index]
        if TYPE_CHECKING:
            assert isinstance(ret, _EDTControllerAndData)
        return ret

    @property
    def gpio_hogs(self) -> list[_EDTControllerAndData]:
        """
        A list of ControllerAndData objects for the GPIOs hogged by the node. The
        list is empty if the node does not hog any GPIOs. Only relevant for GPIO hog
        nodes.
        """

        if "gpio-hog" not in self.props:
            return []

        if not self.parent or not "gpio-controller" in self.parent.props:
            _err(f"GPIO hog {self!r} lacks parent GPIO controller node")

        if not "#gpio-cells" in self.parent._dt_node.props:
            _err(f"GPIO hog {self!r} parent node lacks #gpio-cells")

        n_cells = self.parent._dt_node.props["#gpio-cells"].to_num()
        res = []

        for item in _EDTNode._slice(
            self._dt_node, "gpios", 4 * n_cells, f"4*(<#gpio-cells> (= {n_cells})"
        ):
            controller = self.parent
            res.append(
                _EDTControllerAndData(
                    node=self,
                    controller=controller,
                    data=self._named_cells(controller, item, "gpio"),
                    name=None,
                    basename="gpio",
                )
            )

        return res

    @property
    def is_pci_device(self) -> bool:
        "True if the node is a PCI device."
        return "pcie" in self.on_buses

    @override
    def add_source_specific_deps_to_graph(
        self,
        root_merged: _STMergedNode,
        graph: _STGraph,
        binding: Optional[_STBinding] = None,
    ):
        """
        Adds dependencies to the graph that are specific to the devicetree format.

        See the docstring of the overridden method for more information.
        """
        # 1. Add node-specific dependencies
        if binding is None:
            # An EDTNode depends on whatever supports the interrupts it generates.

            # TODO: Should we really include interrupts unconditionally even if
            # 'self' is only bound by a child binding?
            for intr in self.interrupts:
                controller_merged = self.stree.node_by_path(intr.controller.path)
                if controller_merged is None:
                    _err(
                        f"interrupt controller '{intr.controller.path}' not in merged graph"
                    )
                graph.add_edge(root_merged, controller_merged)

            return

        # 2. Add property-specific dependencies
        for prop_pattern, _ in binding.prop2specs.values():
            for prop in self.props.values():
                if not prop_pattern.match(prop.name):
                    continue

                # EDTNodes have additional dependencies via 'phandle-array'-typed
                # properties that cannot be handled by the generic base class.
                if prop.type == "phandle-array":
                    if TYPE_CHECKING:
                        assert isinstance(prop.val, list)
                    for element in prop.val:
                        if element is None:
                            continue

                        if TYPE_CHECKING:
                            assert isinstance(element, _EDTControllerAndData)
                        controller_merged = self.stree.node_by_path(
                            element.controller.path
                        )
                        if controller_merged is None:
                            _err(
                                f"controller '{element.controller.path}' not in merged graph"
                            )

                        graph.add_edge(root_merged, controller_merged)

    @override
    @property
    def _source_prop_names(self) -> list[str]:
        return list(self._dt_node.props.keys())

    def _bus_node(
        self, support_fixed_partitions_on_any_bus: bool = True
    ) -> Optional[_EDTNode]:
        # Returns the value for self.bus_node. Relies on parent nodes being
        # initialized before their children.

        if not self.parent:
            # This is the root node
            return None

        # Treat 'fixed-partitions' as if they are not on any bus.  The reason is
        # that flash nodes might be on a SPI or controller or SoC bus.  Having
        # bus be None means we'll always match the binding for fixed-partitions
        # also this means want processing the fixed-partitions node we wouldn't
        # try to do anything bus specific with it.
        if support_fixed_partitions_on_any_bus and "fixed-partitions" in self.schemas:
            return None

        if self.parent.buses:
            # The parent node is a bus node
            return self.parent

        # Same bus node as parent (possibly None)
        return self.parent.bus_node

    @override
    def _init_bindings(self) -> None:
        # EDTNodes may receive implicit bindings inferred from properties.
        # Otherwise they behave like generic STNodes.

        if self.path in self.partial_tree._infer_binding_for_paths:
            self._binding_from_properties()

        super()._init_bindings()

        for binding in reversed(self.bindings):
            self._specifier2cells.update(binding.specifier2cells)

    @override
    def _binding_by_schema(self, schema: str) -> Optional[_EDTBinding]:
        # First try to match against an explicitly specified bus (if any) and
        # then against any bus. This is so that matching against bindings which
        # do not specify a bus works the same way in Zephyr as it does
        # elsewhere.
        binding = None

        for variant in self.on_buses:
            binding = super()._binding_by_schema_and_variant(schema, variant)
            if binding is not None:
                return binding

        return super()._binding_by_schema_and_variant(schema)

    def _binding_from_properties(self) -> None:
        # Sets up a Binding object synthesized from the properties in the node.

        if self.schemas:
            _err(f"compatible in node with inferred binding: {self.path}")

        # Synthesize a 'raw' binding as if it had been parsed from YAML.
        yaml_source: dict[str, Any] = {
            "description": "Inferred binding from properties, via edtree.",
            "properties": {},
        }
        for name, prop in self._dt_node.props.items():
            pp: dict[str, str] = {}
            if prop.type == DTType.EMPTY:
                pp["type"] = "boolean"
            elif prop.type == DTType.BYTES:
                pp["type"] = "uint8-array"
            elif prop.type == DTType.NUM:
                pp["type"] = "int"
            elif prop.type == DTType.NUMS:
                pp["type"] = "array"
            elif prop.type == DTType.STRING:
                pp["type"] = "string"
            elif prop.type == DTType.STRINGS:
                pp["type"] = "string-array"
            elif prop.type == DTType.PHANDLE:
                pp["type"] = "phandle"
            elif prop.type == DTType.PHANDLES:
                pp["type"] = "phandles"
            elif prop.type == DTType.PHANDLES_AND_NUMS:
                pp["type"] = "phandle-array"
            elif prop.type == DTType.PATH:
                pp["type"] = "path"
            else:
                _err(
                    f"cannot infer binding from property: {prop} "
                    f"with type {prop.type!r}"
                )
            yaml_source["properties"][name] = pp

        # Set up EDTNode state.
        assert not self._bindings
        self._bindings = [
            _EDTBinding("<inferred>", yaml_source=yaml_source, require_schema=False)
        ]

    def _init_node(self) -> None:
        super()._init_node()
        self._init_regs()
        self._init_ranges()

    @override
    def _init_crossrefs(self) -> None:
        super()._init_crossrefs()
        self._init_interrupts()
        self._init_pinctrls()

    @override
    def _init_props(self) -> None:
        # Creates self.props. See the class docstring. Also checks that all
        # properties on the node are declared in its binding.

        # Initialize self.props
        if self._prop2specs:
            super()._init_props()
        elif self._default_prop_types:
            for dt_prop_name in self._dt_node.props:
                if dt_prop_name not in _DEFAULT_PROP_SPECS:
                    continue

                prop = self._instantiate_prop(_DEFAULT_PROP_SPECS[dt_prop_name])
                if prop.val is None:
                    # This is an optional default property not present in the
                    # source.
                    continue

                self._props[prop.name] = prop

    @override
    def _instantiate_prop(self, prop_spec: _EDTPropertySpec) -> _EDTProperty:
        return _EDTProperty(
            self._dt_node.props.get(prop_spec.name),
            prop_spec,
            self,
            self._err_on_deprecated,
        )

    @override
    def _check_undeclared_prop(self, prop_name: str) -> None:
        # Allow a few special properties to not be declared in the binding
        if (
            prop_name.endswith("-controller")
            or prop_name.startswith("#")
            or prop_name
            in {
                "interrupt-parent",
                "interrupts-extended",
                "device_type",
                "ranges",
            }
        ):
            return

        super()._check_undeclared_prop(prop_name)

    def _init_ranges(self) -> None:
        # Initializes self.ranges
        dt_node = self._dt_node

        self.ranges = []

        if "ranges" not in dt_node.props:
            return

        raw_child_address_cells = dt_node.props.get("#address-cells")
        parent_address_cells = _EDTNode._address_cells(dt_node)
        if raw_child_address_cells is None:
            child_address_cells = 2  # Default value per DT spec.
        else:
            child_address_cells = raw_child_address_cells.to_num()
        raw_child_size_cells = dt_node.props.get("#size-cells")
        if raw_child_size_cells is None:
            child_size_cells = 1  # Default value per DT spec.
        else:
            child_size_cells = raw_child_size_cells.to_num()

        # Number of cells for one translation 3-tuple in 'ranges'
        entry_cells = child_address_cells + parent_address_cells + child_size_cells

        if entry_cells == 0:
            if len(dt_node.props["ranges"].value) == 0:
                return
            else:
                _err(
                    f"'ranges' should be empty in {self._dt_node.path} since "
                    f"<#address-cells> = {child_address_cells}, "
                    f"<#address-cells for parent> = {parent_address_cells} and "
                    f"<#size-cells> = {child_size_cells}"
                )

        for raw_range in _EDTNode._slice(
            dt_node,
            "ranges",
            4 * entry_cells,
            f"4*(<#address-cells> (= {child_address_cells}) + "
            "<#address-cells for parent> "
            f"(= {parent_address_cells}) + "
            f"<#size-cells> (= {child_size_cells}))",
        ):

            child_bus_cells = child_address_cells
            if child_address_cells == 0:
                child_bus_addr = None
            else:
                child_bus_addr = dt_to_num(raw_range[: 4 * child_address_cells])
            parent_bus_cells = parent_address_cells
            if parent_address_cells == 0:
                parent_bus_addr = None
            else:
                parent_bus_addr = dt_to_num(
                    raw_range[
                        (4 * child_address_cells) : (
                            4 * child_address_cells + 4 * parent_address_cells
                        )
                    ]
                )
            length_cells = child_size_cells
            if child_size_cells == 0:
                length = None
            else:
                length = dt_to_num(
                    raw_range[(4 * child_address_cells + 4 * parent_address_cells) :]
                )

            self.ranges.append(
                _EDTRange(
                    self,
                    child_bus_cells,
                    child_bus_addr,
                    parent_bus_cells,
                    parent_bus_addr,
                    length_cells,
                    length,
                )
            )

    def _init_regs(self) -> None:
        # Initializes self.regs

        dt_node = self._dt_node

        self.regs = []

        if "reg" not in dt_node.props:
            return

        address_cells = _EDTNode._address_cells(dt_node)
        size_cells = _EDTNode._size_cells(dt_node)

        for raw_reg in _EDTNode._slice(
            dt_node,
            "reg",
            4 * (address_cells + size_cells),
            f"4*(<#address-cells> (= {address_cells}) + "
            f"<#size-cells> (= {size_cells}))",
        ):
            if address_cells == 0:
                addr = None
            else:
                addr = self._translate(dt_to_num(raw_reg[: 4 * address_cells]), dt_node)
            if size_cells == 0:
                size = None
            else:
                size = dt_to_num(raw_reg[4 * address_cells :])
            # Size zero is ok for PCI devices
            if size_cells != 0 and size == 0 and not self.is_pci_device:
                _err(
                    f"zero-sized 'reg' in {self._dt_node!r} seems meaningless "
                    "(maybe you want a size of one or #size-cells = 0 "
                    "instead)"
                )

            # We'll fix up the name when we're done.
            self.regs.append(_EDTRegister(self, None, addr, size))

        _add_names(dt_node, "reg", self.regs)

    def _init_pinctrls(self) -> None:
        # Initializes self.pinctrls from any pinctrl-<index> properties

        dt_node = self._dt_node

        # pinctrl-<index> properties
        pinctrl_props = [
            prop
            for name, prop in dt_node.props.items()
            if re.match("pinctrl-[0-9]+", name)
        ]
        # Sort by index
        pinctrl_props.sort(key=lambda prop: prop.name)

        # Check indices
        for i, prop in enumerate(pinctrl_props):
            if prop.name != "pinctrl-" + str(i):
                _err(
                    f"missing 'pinctrl-{i}' property on {dt_node!r} "
                    "- indices should be contiguous and start from zero"
                )

        self.pinctrls = []
        for prop in pinctrl_props:
            # We'll fix up the names below.
            self.pinctrls.append(
                _EDTPinCtrl(
                    node=self,
                    name=None,
                    conf_nodes=[
                        self.partial_tree._dtnode2enode[node]
                        for node in prop.to_nodes()
                    ],
                )
            )

        _add_names(dt_node, "pinctrl", self.pinctrls)

    def _init_interrupts(self) -> None:
        # Initializes self.interrupts

        dt_node = self._dt_node

        self.interrupts = []

        for controller_node, data in self._interrupts(dt_node):
            # We'll fix up the names below.
            controller = self.partial_tree._dtnode2enode[controller_node]
            self.interrupts.append(
                _EDTControllerAndData(
                    node=self,
                    controller=controller,
                    data=self._named_cells(controller, data, "interrupt"),
                    name=None,
                    basename=None,
                )
            )

        _add_names(dt_node, "interrupt", self.interrupts)

    def _named_cells(
        self, controller: _EDTNode, data: bytes, basename: str
    ) -> dict[str, int]:
        # Returns a dictionary that maps <basename>-cells names given in the
        # binding for 'controller' to cell values. 'data' is the raw data, as a
        # byte array.

        if not controller.bindings:
            _err(
                f"{basename} controller {controller._dt_node!r} "
                f"for {self._dt_node!r} lacks binding"
            )

        if basename in controller._specifier2cells:
            cell_names: list[str] = controller._specifier2cells[basename]
        else:
            # Treat no *-cells in the binding the same as an empty *-cells, so
            # that bindings don't have to have e.g. an empty 'clock-cells:' for
            # '#clock-cells = <0>'.
            cell_names = []

        data_list = dt_to_nums(data)
        if len(data_list) != len(cell_names):
            _err(
                f"unexpected '{basename}-cells:' length in binding for "
                f"{controller._dt_node!r} - {len(cell_names)} "
                f"instead of {len(data_list)}"
            )

        return dict(zip(cell_names, data_list))

    def _translate(self, addr: int, dt_node: DTNode) -> int:
        # Recursively translates 'addr' on 'node' to the address space(s) of its
        # parent(s), by looking at 'ranges' properties. Returns the translated
        # address.

        if not dt_node.parent or "ranges" not in dt_node.parent.props:
            # No translation
            return addr

        if not dt_node.parent.props["ranges"].value:
            # DT spec.: "If the property is defined with an <empty> value, it
            # specifies that the parent and child address space is identical, and
            # no address translation is required."
            #
            # Treat this the same as a 'range' that explicitly does a one-to-one
            # mapping, as opposed to there not being any translation.
            return self._translate(addr, dt_node.parent)

        # Gives the size of each component in a translation 3-tuple in 'ranges'
        child_address_cells = _EDTNode._address_cells(dt_node)
        parent_address_cells = _EDTNode._address_cells(dt_node.parent)
        child_size_cells = _EDTNode._size_cells(dt_node)

        # Number of cells for one translation 3-tuple in 'ranges'
        entry_cells = child_address_cells + parent_address_cells + child_size_cells

        for raw_range in _EDTNode._slice(
            dt_node.parent,
            "ranges",
            4 * entry_cells,
            f"4*(<#address-cells> (= {child_address_cells}) + "
            "<#address-cells for parent> "
            f"(= {parent_address_cells}) + "
            f"<#size-cells> (= {child_size_cells}))",
        ):
            child_addr = dt_to_num(raw_range[: 4 * child_address_cells])
            raw_range = raw_range[4 * child_address_cells :]

            parent_addr = dt_to_num(raw_range[: 4 * parent_address_cells])
            raw_range = raw_range[4 * parent_address_cells :]

            child_len = dt_to_num(raw_range)

            if child_addr <= addr < child_addr + child_len:
                # 'addr' is within range of a translation in 'ranges'. Recursively
                # translate it and return the result.
                return self._translate(parent_addr + addr - child_addr, dt_node.parent)

        # 'addr' is not within range of any translation in 'ranges'
        return addr

    @staticmethod
    def _interrupts(dt_node: DTNode) -> list[Tuple[DTNode, bytes]]:
        # Returns a list of (<controller>, <data>) tuples, with one tuple per
        # interrupt generated by 'node'. <controller> is the destination of the
        # interrupt (possibly after mapping through an 'interrupt-map'), and <data>
        # the data associated with the interrupt (as a 'bytes' object).

        # Takes precedence over 'interrupts' if both are present
        if "interrupts-extended" in dt_node.props:
            prop = dt_node.props["interrupts-extended"]

            ret: list[Tuple[DTNode, bytes]] = []
            for entry in _phandle_val_list(prop, "interrupt"):
                if entry is None:
                    _err(
                        f"node '{dt_node.path}' interrupts-extended property "
                        "has an empty element"
                    )
                iparent, spec = entry
                ret.append(_EDTNode._map_interrupt(dt_node, iparent, spec))
            return ret

        if "interrupts" in dt_node.props:
            # Treat 'interrupts' as a special case of 'interrupts-extended', with
            # the same interrupt parent for all interrupts

            iparent = _EDTNode._interrupt_parent(dt_node)
            interrupt_cells = _EDTNode._interrupt_cells(iparent)

            return [
                _EDTNode._map_interrupt(dt_node, iparent, raw)
                for raw in _EDTNode._slice(
                    dt_node, "interrupts", 4 * interrupt_cells, "4*<#interrupt-cells>"
                )
            ]

        return []

    @staticmethod
    def _map_interrupt(
        dt_child: DTNode, dt_parent: DTNode, child_spec: bytes
    ) -> Tuple[DTNode, bytes]:
        # Translates an interrupt headed from 'child' to 'parent' with data
        # 'child_spec' through any 'interrupt-map' properties. Returns a
        # (<controller>, <data>) tuple with the final destination after mapping.

        if "interrupt-controller" in dt_parent.props:
            return (dt_parent, child_spec)

        def own_address_cells(node):
            # Used for parents pointed at by 'interrupt-map'. We can't use
            # _address_cells(), because it's the #address-cells property on 'node'
            # itself that matters.

            address_cells = node.props.get("#address-cells")
            if not address_cells:
                _err(
                    f"missing #address-cells on {node!r} "
                    "(while handling interrupt-map)"
                )
            return address_cells.to_num()

        def spec_len_fn(node):
            # Can't use _address_cells() here, because it's the #address-cells
            # property on 'node' itself that matters
            return own_address_cells(node) + _EDTNode._interrupt_cells(node)

        dt_parent, raw_spec = _map(
            "interrupt",
            dt_child,
            dt_parent,
            _EDTNode._raw_unit_addr(dt_child) + child_spec,
            spec_len_fn,
            require_controller=True,
        )

        # Strip the parent unit address part, if any
        return (dt_parent, raw_spec[4 * own_address_cells(dt_parent) :])

    @staticmethod
    def _raw_unit_addr(dt_node: DTNode) -> bytes:
        # _map_interrupt() helper. Returns the unit address (derived from 'reg' and
        # #address-cells) as a raw 'bytes'

        if "reg" not in dt_node.props:
            _err(
                f"{dt_node!r} lacks 'reg' property "
                "(needed for 'interrupt-map' unit address lookup)"
            )

        addr_len = 4 * _EDTNode._address_cells(dt_node)

        if len(dt_node.props["reg"].value) < addr_len:
            _err(
                f"{dt_node!r} has too short 'reg' property "
                "(while doing 'interrupt-map' unit address lookup)"
            )

        return dt_node.props["reg"].value[:addr_len]

    @staticmethod
    def _interrupt_cells(dt_node: DTNode) -> int:
        # Returns the #interrupt-cells property value on 'node', erroring out if
        # 'node' has no #interrupt-cells property

        if "#interrupt-cells" not in dt_node.props:
            _err(f"{dt_node!r} lacks #interrupt-cells")
        return dt_node.props["#interrupt-cells"].to_num()

    @staticmethod
    def _interrupt_parent(dt_start_node: DTNode) -> DTNode:
        # Returns the node pointed at by the closest 'interrupt-parent', searching
        # the parents of 'node'. As of writing, this behavior isn't specified in
        # the DT spec., but seems to match what some .dts files except.

        node: Optional[DTNode] = dt_start_node

        while node:
            if "interrupt-parent" in node.props:
                return node.props["interrupt-parent"].to_node()
            node = node.parent

        _err(
            f"{dt_start_node!r} has an 'interrupts' property, but neither the node "
            f"nor any of its parents has an 'interrupt-parent' property"
        )

    @staticmethod
    def _address_cells(dt_node: DTNode) -> int:
        # Returns the #address-cells setting for 'node', giving the number of <u32>
        # cells used to encode the address in the 'reg' property
        if TYPE_CHECKING:
            assert dt_node.parent

        if "#address-cells" in dt_node.parent.props:
            return dt_node.parent.props["#address-cells"].to_num()
        return 2  # Default value per DT spec.

    @staticmethod
    def _size_cells(dt_node: DTNode) -> int:
        # Returns the #size-cells setting for 'node', giving the number of <u32>
        # cells used to encode the size in the 'reg' property
        if TYPE_CHECKING:
            assert dt_node.parent

        if "#size-cells" in dt_node.parent.props:
            return dt_node.parent.props["#size-cells"].to_num()
        return 1  # Default value per DT spec.

    @staticmethod
    def _slice(
        dt_node: DTNode, prop_name: str, size: int, size_hint: str
    ) -> list[bytes]:
        return _EDTNode._slice_helper(dt_node, prop_name, size, size_hint, EDTError)

    @staticmethod
    def _slice_helper(
        node: Any,  # avoids a circular import with dtlib
        prop_name: str,
        size: int,
        size_hint: str,
        err_class: Callable[..., Exception],
    ):
        # Splits node.props[prop_name].value into 'size'-sized chunks,
        # returning a list of chunks. Raises err_class(...) if the length
        # of the property is not evenly divisible by 'size'. The argument
        # to err_class is a string which describes the error.
        #
        # 'size_hint' is a string shown on errors that gives a hint on how
        # 'size' was calculated.

        raw = node.props[prop_name].value
        if len(raw) % size:
            raise err_class(
                f"'{prop_name}' property in {node!r} has length {len(raw)}, "
                f"which is not evenly divisible by {size} (= {size_hint}). "
                "Note that #*-cells properties come either from the parent node or "
                "from the controller (in the case of 'interrupts')."
            )

        return [raw[i : i + size] for i in range(0, len(raw), size)]


@dataclass
class _EDTRegister:
    """
    Represents a register on a node.

    These attributes are available on Register objects:

    node:
      The EDTNode instance this register is from

    name:
      The name of the register as given in the 'reg-names' property, or None if
      there is no 'reg-names' property

    addr:
      The starting address of the register, in the parent address space, or None
      if #address-cells is zero. Any 'ranges' properties are taken into account.

    size:
      The length of the register in bytes

    For internal use only; not meant to be instantiated directly by settings
    library clients.
    """

    node: _EDTNode
    name: Optional[str]
    addr: Optional[int]
    size: Optional[int]


@dataclass
class _EDTRange:
    """
    Represents a translation range on a node as described by the 'ranges' property.

    These attributes are available on Range objects:

    node:
      The EDTNode instance this range is from

    child_bus_cells:
      The number of cells used to describe a child bus address.

    child_bus_addr:
      A physical address within the child bus address space, or None if the
      child's #address-cells equals 0.

    parent_bus_cells:
      The number of cells used to describe a parent bus address.

    parent_bus_addr:
      A physical address within the parent bus address space, or None if the
      parent's #address-cells equals 0.

    length_cells:
      The number of cells used to describe the size of range in
      the child's address space.

    length:
      The size of the range in the child address space, or None if the
      child's #size-cells equals 0.

    For internal use only; not meant to be instantiated directly by settings
    library clients.
    """

    node: _EDTNode
    child_bus_cells: int
    child_bus_addr: Optional[int]
    parent_bus_cells: int
    parent_bus_addr: Optional[int]
    length_cells: int
    length: Optional[int]


@dataclass
class _EDTControllerAndData:
    """
    Represents an entry in an 'interrupts' or 'type: phandle-array' property
    value, e.g. <&ctrl-1 4 0> in

        cs-gpios = <&ctrl-1 4 0 &ctrl-2 3 4>;

    These attributes are available on ControllerAndData objects:

    node:
      The EDTNode instance the property appears on

    controller:
      The EDTNode instance for the controller (e.g. the controller the interrupt
      gets sent to for interrupts)

    data:
      A dictionary that maps names from the *-cells key in the binding for the
      controller to data values, e.g. {"pin": 4, "flags": 0} for the example
      above.

      'interrupts = <1 2>' might give {"irq": 1, "level": 2}.

    name:
      The name of the entry as given in
      'interrupt-names'/'gpio-names'/'pwm-names'/etc., or None if there is no
      *-names property

    basename:
      Basename for the controller when supporting named cells

    For internal use only; not meant to be instantiated directly by settings
    library clients.
    """

    node: _EDTNode
    controller: _EDTNode
    data: dict
    name: Optional[str]
    basename: Optional[str]


@dataclass
class _EDTPinCtrl:
    """
    Represents a pin control configuration for a set of pins on a device,
    e.g. pinctrl-0 or pinctrl-1.

    These attributes are available on PinCtrl objects:

    node:
      The EDTNode instance the pinctrl-* property is on

    name:
      The name of the configuration, as given in pinctrl-names, or None if
      there is no pinctrl-names property

    name_as_token:

    conf_nodes:
      A list of EDTNode instances for the pin configuration nodes, e.g.
      the nodes pointed at by &state_1 and &state_2 in

          pinctrl-0 = <&state_1 &state_2>;

    Also see property docstrings.

    For internal use only; not meant to be instantiated directly by settings
    library clients.
    """

    node: _EDTNode
    name: Optional[str]
    conf_nodes: list[_EDTNode]

    @property
    def name_as_token(self):
        """
        Like 'name', but with non-alphanumeric characters converted to
        underscores.
        """
        return str_as_token(self.name) if self.name is not None else None


#
# Public classes
#


class EDTree(_STPartialTree[_EDTPropertyValType, _EDTNode, _EDTBinding]):
    """
    Represents a devicetree augmented with information from bindings.

    Instantiate this class for each devicetree source file you want to add to
    the overall merged settings tree. Then add it to the tree calling

    Also see the docstring of the STPartialTree base class.
    STree.add_partial_tree().
    """

    @override
    @staticmethod
    def schema_prop_name() -> str:
        return "compatible"

    @override
    @staticmethod
    def enabled_prop_name() -> str:
        return "status"

    def __init__(
        self,
        dts_path: str,
        bindings_dirs: str | list[str],
        warn_reg_unit_address_mismatch: bool = True,
        default_prop_types: bool = True,
        err_on_deprecated: bool = False,
        support_fixed_partitions_on_any_bus: bool = True,
        infer_binding_for_paths: Optional[Iterable[str]] = None,
    ):
        """
        EDTree constructor.

        dts_path:
          The path to the devicetree source file from which the tree will be
          constructed.

        warn_reg_unit_address_mismatch (default: True):
          If True, a warning is logged if a node has a 'reg' property where the
          address of the first entry does not match the unit address of the node

        default_prop_types (default: True):
          If True, default property types will be used when a node has no
          bindings.

        err_on_deprecated (default: False):
            If True, an error is raised if a deprecated property is encountered.
            If False, a warning is logged.

        support_fixed_partitions_on_any_bus (default True):
          If True, set the EDTNode.bus for 'fixed-partitions' compatible nodes
          to None.  This allows 'fixed-partitions' binding to match regardless
          of the bus the 'fixed-partition' is under.

        infer_binding_for_paths (default: None):
          An iterable of devicetree paths identifying nodes for which bindings
          should be inferred from the node content.  (Child nodes are not
          processed.)  Pass none if no nodes should support inferred bindings.
        """
        # If you change the state of this class or its superclass, make sure to
        # update the __deepcopy__ method and corresponding tests, too.  The full
        # state of a partial tree should be reproducible by instantiating it
        # with the same constructor arguments and calling
        # STPartialTree.process().

        # Saved kwarg values for internal use
        self._warn_reg_unit_address_mismatch: bool = warn_reg_unit_address_mismatch
        self._default_prop_types: bool = default_prop_types
        self._fixed_partitions_no_bus: bool = support_fixed_partitions_on_any_bus
        self._infer_binding_for_paths: set[str] = set(infer_binding_for_paths or [])

        # Internal state - do not touch from outside the module.
        self._dtnode2enode: dict[DTNode, _EDTNode] = {}
        try:
            self._dt = DT(dts_path)
        except DTError as e:
            raise EDTError(e) from e

        super().__init__(dts_path, bindings_dirs, _EDTNode, err_on_deprecated)

    def __deepcopy__(self, memo) -> EDTree:
        """
        Implements support for the standard library copy.deepcopy()
        function on EDTree instances.
        """

        clone = EDTree(
            self.source_path,
            deepcopy(self.bindings_dirs, memo),
            warn_reg_unit_address_mismatch=self._warn_reg_unit_address_mismatch,
            default_prop_types=self._default_prop_types,
            err_on_deprecated=self._err_on_deprecated,
            support_fixed_partitions_on_any_bus=self._fixed_partitions_no_bus,
            infer_binding_for_paths=deepcopy(self._infer_binding_for_paths),
        )
        clone.stree = (
            self.stree
        )  # Shallow copy is fine - the tree will be cloned and updated separately
        clone._dt = deepcopy(self._dt, memo)
        clone.process()

        return clone

    def __repr__(self) -> str:
        return f"<EDTree for '{self.source_path}'>"

    @override
    @property
    def path2node(self) -> OrderedDict[str, _EDTNode]:
        return OrderedDict([(node.path, node) for node in self.nodes])

    def chosen_node(self, name: str) -> Optional[_EDTNode]:
        """
        Returns the STNode pointed at by the property named 'name' in /chosen,
        or None if the property is missing
        """
        return self.chosen_nodes.get(name)

    @property
    def chosen_nodes(self) -> OrderedDict[str, _EDTNode]:
        chosen_nodes: OrderedDict[str, _EDTNode] = OrderedDict()

        try:
            chosen = self.node_by_path("/chosen")
            if chosen is None:
                return chosen_nodes
        except EDTError:
            return chosen_nodes

        for name, prop in chosen._dt_node.props.items():
            try:
                dt_node = prop.to_path()
            except DTError:
                # DTS value is not phandle or string, or path doesn't exist
                continue

            chosen_nodes[name] = self._dtnode2enode[dt_node]

        return chosen_nodes

    @property
    def dts_source(self) -> str:
        """
        The final DTS source code of the loaded devicetree after merging nodes
        and processing /delete-node/ and /delete-property/, as a string
        """
        return f"{self._dt}"

    @override
    def node_by_path(self, path: str) -> _EDTNode:
        """
        Returns the EDTNode at the DT path or alias 'path'. Raises EDTError if the
        path or alias doesn't exist.
        """
        try:
            return self._dtnode2enode[self._dt.get_node(path)]
        except DTError as e:
            _err(e)

    @override
    def _collect_schemas(self) -> set[str]:
        return {
            schema
            for node in self._dt.node_iter()
            if self.schema_prop_name() in node.props
            for schema in node.props[self.schema_prop_name()].to_strings()
        }

    @override
    def _instantiate_binding(
        self, yaml_source: OrderedDict[str, Any], binding_path: str
    ) -> Optional[_EDTBinding]:
        schema_prop_name = _EDTBinding._schema_prop_name_from_yaml_source(yaml_source)
        return super()._instantiate_binding_internal(
            _EDTBinding, schema_prop_name, yaml_source, binding_path
        )

    @override
    def _init_nodes(self) -> None:
        # Creates a list of edtree._EDTNode objects from the dtlib.Node objects, in
        # self.nodes

        # Warning: We depend on parent Nodes being created before their
        # children. This is guaranteed by DT.node_iter().
        for dt_node in self._dt.node_iter():
            # Warning: We depend on parent Nodes being created before their
            # children. This is guaranteed by node_iter().
            node = _EDTNode(
                dt_node,
                self,
                self._err_on_deprecated,
                self._default_prop_types,
                self._fixed_partitions_no_bus,
            )
            self._nodes.append(node)
            self._dtnode2enode[dt_node] = node

        if self._warn_reg_unit_address_mismatch:
            # This warning matches the simple_bus_reg warning in dtc
            for node in self._nodes:
                if TYPE_CHECKING:
                    assert isinstance(node, _EDTNode)

                # Address mismatch is ok for PCI devices
                if (
                    node.regs
                    and node.regs[0].addr != node.unit_addr
                    and not node.is_pci_device
                ):
                    _LOG.warning(
                        "unit address and first address in 'reg' "
                        f"(0x{node.regs[0].addr:x}) don't match for "
                        f"{node.path}"
                    )

    @override
    def _check(self) -> None:
        super()._check()

        # Does EDTree specific sanity checks. dtlib is meant to be general and
        # anything-goes except for very special properties like phandle, but in
        # edtree we can be pickier.

        # Check that 'status' has one of the values given in the devicetree spec.

        # Accept "ok" for backwards compatibility
        ok_status = {"ok", "okay", "disabled", "reserved", "fail", "fail-sss"}

        for node in self._dt.node_iter():
            if "status" in node.props:
                try:
                    status_val = node.props["status"].to_string()
                except DTError as e:
                    # The error message gives the path
                    _err(str(e))

                if status_val not in ok_status:
                    _err(
                        f"unknown 'status' value \"{status_val}\" in {node.path} "
                        f"in {node.dt.filename}, expected one of "
                        + ", ".join(ok_status)
                        + " (see the devicetree specification)"
                    )

            ranges_prop = node.props.get("ranges")
            if ranges_prop:
                if ranges_prop.type not in (DTType.EMPTY, DTType.NUMS):
                    _err(
                        f"expected 'ranges = < ... >;' in {node.path} in "
                        f"{node.dt.filename}, not '{ranges_prop}' "
                        "(see the devicetree specification)"
                    )

#
# Public global functions
#


def bindings_from_paths(
    yaml_paths: list[str], ignore_errors: bool = False
) -> list[_STBinding]:
    """
    Get a list of Binding objects from the yaml files 'yaml_paths'.

    If 'ignore_errors' is True, YAML files that cause an STBindingError when
    loaded are ignored. (No other exception types are silenced.)
    """

    ret: list[_STBinding] = []
    fname2path = {os.path.basename(path): path for path in yaml_paths}
    for path in yaml_paths:
        try:
            ret.append(_EDTBinding(path, fname2path))
        except STBindingError:
            if ignore_errors:
                continue
            raise

    return ret


#
# Private global functions
#


def _add_names(node: DTNode, names_ident: str, objs: Any) -> None:
    # Helper for registering names from <foo>-names properties.
    #
    # node:
    #   Node which has a property that might need named elements.
    #
    # names-ident:
    #   The <foo> part of <foo>-names, e.g. "reg" for "reg-names"
    #
    # objs:
    #   list of objects whose .name field should be set

    full_names_ident = names_ident + "-names"

    if full_names_ident in node.props:
        names = node.props[full_names_ident].to_strings()
        if len(names) != len(objs):
            _err(
                f"{full_names_ident} property in {node.path} "
                f"in {node.dt.filename} has {len(names)} strings, "
                f"expected {len(objs)} strings"
            )

        for obj, name in zip(objs, names):
            if obj is None:
                continue
            obj.name = name
    else:
        for obj in objs:
            if obj is not None:
                obj.name = None


def _map(
    prefix: str,
    dt_child: DTNode,
    dt_parent: DTNode,
    child_spec: bytes,
    spec_len_fn: Callable[[DTNode], int],
    require_controller: bool,
) -> Tuple[DTNode, bytes]:
    # Common code for mapping through <prefix>-map properties, e.g.
    # interrupt-map and gpio-map.
    #
    # prefix:
    #   The prefix, e.g. "interrupt" or "gpio"
    #
    # child:
    #   The "sender", e.g. the node with 'interrupts = <...>'
    #
    # parent:
    #   The "receiver", e.g. a node with 'interrupt-map = <...>' or
    #   'interrupt-controller' (no mapping)
    #
    # child_spec:
    #   The data associated with the interrupt/GPIO/etc., as a 'bytes' object,
    #   e.g. <1 2> for 'foo-gpios = <&gpio1 1 2>'.
    #
    # spec_len_fn:
    #   Function called on a parent specified in a *-map property to get the
    #   length of the parent specifier (data after phandle in *-map), in cells
    #
    # require_controller:
    #   If True, the final controller node after mapping is required to have
    #   to have a <prefix>-controller property.

    map_prop = dt_parent.props.get(prefix + "-map")
    if not map_prop:
        if require_controller and prefix + "-controller" not in dt_parent.props:
            _err(
                f"expected '{prefix}-controller' property on {dt_parent!r} "
                f"(referenced by {dt_child!r})"
            )

        # No mapping
        return (dt_parent, child_spec)

    masked_child_spec = _mask(prefix, dt_child, dt_parent, child_spec)

    raw = map_prop.value
    while raw:
        if len(raw) < len(child_spec):
            _err(f"bad value for {map_prop!r}, missing/truncated child data")
        child_spec_entry = raw[: len(child_spec)]
        raw = raw[len(child_spec) :]

        if len(raw) < 4:
            _err(f"bad value for {map_prop!r}, missing/truncated phandle")
        phandle = dt_to_num(raw[:4])
        raw = raw[4:]

        # Parent specified in *-map
        map_parent = dt_parent.dt.phandle2node.get(phandle)
        if not map_parent:
            _err(f"bad phandle ({phandle}) in {map_prop!r}")

        map_parent_spec_len = 4 * spec_len_fn(map_parent)
        if len(raw) < map_parent_spec_len:
            _err(f"bad value for {map_prop!r}, missing/truncated parent data")
        parent_spec = raw[:map_parent_spec_len]
        raw = raw[map_parent_spec_len:]

        # Got one *-map row. Check if it matches the child data.
        if child_spec_entry == masked_child_spec:
            # Handle *-map-pass-thru
            parent_spec = _pass_thru(
                prefix, dt_child, dt_parent, child_spec, parent_spec
            )

            # Found match. Recursively map and return it.
            return _map(
                prefix,
                dt_parent,
                map_parent,
                parent_spec,
                spec_len_fn,
                require_controller,
            )

    _err(
        f"child specifier for {dt_child!r} ({child_spec!r}) "
        f"does not appear in {map_prop!r}"
    )


def _mask(prefix: str, dt_child: DTNode, dt_parent: DTNode, child_spec: bytes) -> bytes:
    # Common code for handling <prefix>-mask properties, e.g. interrupt-mask.
    # See _map() for the parameters.

    mask_prop = dt_parent.props.get(prefix + "-map-mask")
    if not mask_prop:
        # No mask
        return child_spec

    mask = mask_prop.value
    if len(mask) != len(child_spec):
        _err(
            f"{dt_child!r}: expected '{prefix}-mask' in {dt_parent!r} "
            f"to be {len(child_spec)} bytes, is {len(mask)} bytes"
        )

    return _and(child_spec, mask)


def _pass_thru(
    prefix: str,
    dt_child: DTNode,
    dt_parent: DTNode,
    child_spec: bytes,
    parent_spec: bytes,
) -> bytes:
    # Common code for handling <prefix>-map-thru properties, e.g.
    # interrupt-pass-thru.
    #
    # parent_spec:
    #   The parent data from the matched entry in the <prefix>-map property
    #
    # See _map() for the other parameters.

    pass_thru_prop = dt_parent.props.get(prefix + "-map-pass-thru")
    if not pass_thru_prop:
        # No pass-thru
        return parent_spec

    pass_thru = pass_thru_prop.value
    if len(pass_thru) != len(child_spec):
        _err(
            f"{dt_child!r}: expected '{prefix}-map-pass-thru' in {dt_parent!r} "
            f"to be {len(child_spec)} bytes, is {len(pass_thru)} bytes"
        )

    res = _or(_and(child_spec, pass_thru), _and(parent_spec, _not(pass_thru)))

    # Truncate to length of parent spec.
    return res[-len(parent_spec) :]


def _and(b1: bytes, b2: bytes) -> bytes:
    # Returns the bitwise AND of the two 'bytes' objects b1 and b2. Pads
    # with ones on the left if the lengths are not equal.

    # Pad on the left, to equal length
    maxlen = max(len(b1), len(b2))
    return bytes(
        x & y for x, y in zip(b1.rjust(maxlen, b"\xff"), b2.rjust(maxlen, b"\xff"))
    )


def _or(b1: bytes, b2: bytes) -> bytes:
    # Returns the bitwise OR of the two 'bytes' objects b1 and b2. Pads with
    # zeros on the left if the lengths are not equal.

    # Pad on the left, to equal length
    maxlen = max(len(b1), len(b2))
    return bytes(
        x | y for x, y in zip(b1.rjust(maxlen, b"\x00"), b2.rjust(maxlen, b"\x00"))
    )


def _not(b: bytes) -> bytes:
    # Returns the bitwise not of the 'bytes' object 'b'

    # ANDing with 0xFF avoids negative numbers
    return bytes(~x & 0xFF for x in b)


def _phandle_val_list(
    dt_prop: DTProperty, n_cells_name: str
) -> list[Optional[Tuple[DTNode, bytes]]]:
    # Parses a '<phandle> <value> <phandle> <value> ...' value. The number of
    # cells that make up each <value> is derived from the node pointed at by
    # the preceding <phandle>.
    #
    # prop:
    #   dtlib.Property with value to parse
    #
    # n_cells_name:
    #   The <name> part of the #<name>-cells property to look for on the nodes
    #   the phandles point to, e.g. "gpio" for #gpio-cells.
    #
    # Each tuple in the return value is a (<node>, <value>) pair, where <node>
    # is the node pointed at by <phandle>. If <phandle> does not refer
    # to a node, the entire list element is None.

    full_n_cells_name = f"#{n_cells_name}-cells"

    res: list[Optional[Tuple[DTNode, bytes]]] = []

    raw = dt_prop.value
    while raw:
        if len(raw) < 4:
            # Not enough room for phandle
            _err("bad value for " + repr(dt_prop))
        phandle = dt_to_num(raw[:4])
        raw = raw[4:]

        node = dt_prop.node.dt.phandle2node.get(phandle)
        if not node:
            # Unspecified phandle-array element. This is valid; a 0
            # phandle value followed by no cells is an empty element.
            res.append(None)
            continue

        if full_n_cells_name not in node.props:
            _err(f"{node!r} lacks {full_n_cells_name}")

        n_cells = node.props[full_n_cells_name].to_num()
        if len(raw) < 4 * n_cells:
            _err("missing data after phandle in " + repr(dt_prop))

        res.append((node, raw[: 4 * n_cells]))
        raw = raw[4 * n_cells :]

    return res


def _err(msg) -> NoReturn:
    raise EDTError(msg)
