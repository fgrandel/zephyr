# Copyright (c) 2019 Nordic Semiconductor ASA
# SPDX-License-Identifier: BSD-3-Clause

from collections import OrderedDict
import contextlib
import io
import os
import pickle
import pytest

from logging import WARNING
from copy import deepcopy
from pathlib import Path

from settings.devicetree.edtree import (
    _EDTBinding,
    _EDTControllerAndData,
    _EDTNode,
    _EDTRange,
    _EDTPinCtrl,
)
from settings.error import STError
from settings.stree import _STMergedNode
from settings import EDTError, EDTree, STree

# Test suite for edtree.py.
#
# Run it using pytest (https://docs.pytest.org/en/stable/usage.html):
#
#   $ pytest test_edttree.py
#
# See the comment near the top of test_dtlib.py for additional pytest advice.
#
# test.dts is the main test file. test-bindings/ and test-bindings-2/ has
# bindings. The tests mostly use string comparisons via the various __repr__()
# methods.

HERE = os.path.dirname(__file__)


@contextlib.contextmanager
def from_here():
    # Convenience hack to minimize diff from zephyr.
    cwd = os.getcwd()
    try:
        os.chdir(HERE)
        yield
    finally:
        os.chdir(cwd)


def hpath(filename):
    """Convert 'filename' to the host path syntax."""
    return os.fspath(Path(filename))


def test_warnings(caplog):
    """Tests for situations that should cause warnings."""

    with from_here():
        STree().add_partial_tree(EDTree("test.dts", "test-bindings")).process()

    enums_hpath = hpath("test-bindings/enums.yaml")
    expected_warnings = [
        "unit address and first address in 'reg' (0x1) don't match for /reg-zero-size-cells/node",
        "unit address and first address in 'reg' (0x5) don't match for /reg-ranges/parent/node",
        "unit address and first address in 'reg' (0x30000000200000001) don't match for /reg-nested-ranges/grandparent/parent/node",
        f"'oldprop' is marked as deprecated in 'properties:' in {hpath('test-bindings/deprecated.yaml')} for node /test-deprecated.",
        f"schema 'enums' in binding '{enums_hpath}' has non-tokenizable enum for property 'string-enum': 'foo bar', 'foo_bar'",
        f"schema 'enums' in binding '{enums_hpath}' has enum for property 'tokenizable-lower-enum' that is only tokenizable in lowercase: 'bar', 'BAR'",
    ]
    assert caplog.record_tuples[0:3] == [
        ("settings.devicetree.edtree", WARNING, warning_message)
        for warning_message in expected_warnings[:3]
    ]
    assert caplog.record_tuples[3:] == [
        ("settings.stree", WARNING, warning_message)
        for warning_message in expected_warnings[3:]
    ]


def test_interrupts():
    """Tests for the interrupts property."""
    with from_here():
        stree = STree().add_partial_tree(EDTree("test.dts", "test-bindings")).process()

    node = stree.node_by_path("/interrupt-parent-test/node").cast(_EDTNode)
    controller = stree.node_by_path("/interrupt-parent-test/controller").cast(_EDTNode)
    assert node.interrupts == [
        _EDTControllerAndData(
            node=node,
            controller=controller,
            data={"one": 1, "two": 2, "three": 3},
            name="foo",
            basename=None,
        ),
        _EDTControllerAndData(
            node=node,
            controller=controller,
            data={"one": 4, "two": 5, "three": 6},
            name="bar",
            basename=None,
        ),
    ]

    node = stree.node_by_path("/interrupts-extended-test/node").cast(_EDTNode)
    controller_0 = stree.node_by_path("/interrupts-extended-test/controller-0").cast(
        _EDTNode
    )
    controller_1 = stree.node_by_path("/interrupts-extended-test/controller-1").cast(
        _EDTNode
    )
    controller_2 = stree.node_by_path("/interrupts-extended-test/controller-2").cast(
        _EDTNode
    )
    assert node.interrupts == [
        _EDTControllerAndData(
            node=node,
            controller=controller_0,
            data={"one": 1},
            name=None,
            basename=None,
        ),
        _EDTControllerAndData(
            node=node,
            controller=controller_1,
            data={"one": 2, "two": 3},
            name=None,
            basename=None,
        ),
        _EDTControllerAndData(
            node=node,
            controller=controller_2,
            data={"one": 4, "two": 5, "three": 6},
            name=None,
            basename=None,
        ),
    ]

    node = stree.node_by_path("/interrupt-map-test/node@0").cast(_EDTNode)
    controller_0 = stree.node_by_path("/interrupt-map-test/controller-0").cast(_EDTNode)
    controller_1 = stree.node_by_path("/interrupt-map-test/controller-1").cast(_EDTNode)
    controller_2 = stree.node_by_path("/interrupt-map-test/controller-2").cast(_EDTNode)

    assert node.interrupts == [
        _EDTControllerAndData(
            node=node,
            controller=controller_0,
            data={"one": 0},
            name=None,
            basename=None,
        ),
        _EDTControllerAndData(
            node=node,
            controller=controller_1,
            data={"one": 0, "two": 1},
            name=None,
            basename=None,
        ),
        _EDTControllerAndData(
            node=node,
            controller=controller_2,
            data={"one": 0, "two": 0, "three": 2},
            name=None,
            basename=None,
        ),
    ]

    node = stree.node_by_path("/interrupt-map-test/node@1").cast(_EDTNode)
    assert node.interrupts == [
        _EDTControllerAndData(
            node=node,
            controller=controller_0,
            data={"one": 3},
            name=None,
            basename=None,
        ),
        _EDTControllerAndData(
            node=node,
            controller=controller_1,
            data={"one": 0, "two": 4},
            name=None,
            basename=None,
        ),
        _EDTControllerAndData(
            node=node,
            controller=controller_2,
            data={"one": 0, "two": 0, "three": 5},
            name=None,
            basename=None,
        ),
    ]

    node = stree.node_by_path("/interrupt-map-bitops-test/node@70000000E").cast(
        _EDTNode
    )
    assert node.interrupts == [
        _EDTControllerAndData(
            node=node,
            controller=stree.node_by_path("/interrupt-map-bitops-test/controller").cast(
                _EDTNode
            ),
            data={"one": 3, "two": 2},
            name=None,
            basename=None,
        )
    ]


def test_ranges():
    """Tests for the ranges property"""
    with from_here():
        stree = STree().add_partial_tree(EDTree("test.dts", "test-bindings")).process()

    node = stree.node_by_path("/reg-ranges/parent").cast(_EDTNode)
    assert node.ranges == [
        _EDTRange(
            node=node,
            child_bus_cells=0x1,
            child_bus_addr=0x1,
            parent_bus_cells=0x2,
            parent_bus_addr=0xA0000000B,
            length_cells=0x1,
            length=0x1,
        ),
        _EDTRange(
            node=node,
            child_bus_cells=0x1,
            child_bus_addr=0x2,
            parent_bus_cells=0x2,
            parent_bus_addr=0xC0000000D,
            length_cells=0x1,
            length=0x2,
        ),
        _EDTRange(
            node=node,
            child_bus_cells=0x1,
            child_bus_addr=0x4,
            parent_bus_cells=0x2,
            parent_bus_addr=0xE0000000F,
            length_cells=0x1,
            length=0x1,
        ),
    ]

    node = stree.node_by_path("/reg-nested-ranges/grandparent").cast(_EDTNode)
    assert node.ranges == [
        _EDTRange(
            node=node,
            child_bus_cells=0x2,
            child_bus_addr=0x0,
            parent_bus_cells=0x3,
            parent_bus_addr=0x30000000000000000,
            length_cells=0x2,
            length=0x200000002,
        )
    ]

    node = stree.node_by_path("/reg-nested-ranges/grandparent/parent").cast(_EDTNode)
    assert node.ranges == [
        _EDTRange(
            node=node,
            child_bus_cells=0x1,
            child_bus_addr=0x0,
            parent_bus_cells=0x2,
            parent_bus_addr=0x200000000,
            length_cells=0x1,
            length=0x2,
        )
    ]

    assert stree.node_by_path("/ranges-zero-cells/node").cast(_EDTNode).ranges == []

    node = stree.node_by_path("/ranges-zero-parent-cells/node").cast(_EDTNode)
    assert node.ranges == [
        _EDTRange(
            node=node,
            child_bus_cells=0x1,
            child_bus_addr=0xA,
            parent_bus_cells=0x0,
            parent_bus_addr=None,
            length_cells=0x0,
            length=None,
        ),
        _EDTRange(
            node=node,
            child_bus_cells=0x1,
            child_bus_addr=0x1A,
            parent_bus_cells=0x0,
            parent_bus_addr=None,
            length_cells=0x0,
            length=None,
        ),
        _EDTRange(
            node=node,
            child_bus_cells=0x1,
            child_bus_addr=0x2A,
            parent_bus_cells=0x0,
            parent_bus_addr=None,
            length_cells=0x0,
            length=None,
        ),
    ]

    node = stree.node_by_path("/ranges-one-address-cells/node").cast(_EDTNode)
    assert node.ranges == [
        _EDTRange(
            node=node,
            child_bus_cells=0x1,
            child_bus_addr=0xA,
            parent_bus_cells=0x0,
            parent_bus_addr=None,
            length_cells=0x1,
            length=0xB,
        ),
        _EDTRange(
            node=node,
            child_bus_cells=0x1,
            child_bus_addr=0x1A,
            parent_bus_cells=0x0,
            parent_bus_addr=None,
            length_cells=0x1,
            length=0x1B,
        ),
        _EDTRange(
            node=node,
            child_bus_cells=0x1,
            child_bus_addr=0x2A,
            parent_bus_cells=0x0,
            parent_bus_addr=None,
            length_cells=0x1,
            length=0x2B,
        ),
    ]

    node = stree.node_by_path("/ranges-one-address-two-size-cells/node").cast(_EDTNode)
    assert node.ranges == [
        _EDTRange(
            node=node,
            child_bus_cells=0x1,
            child_bus_addr=0xA,
            parent_bus_cells=0x0,
            parent_bus_addr=None,
            length_cells=0x2,
            length=0xB0000000C,
        ),
        _EDTRange(
            node=node,
            child_bus_cells=0x1,
            child_bus_addr=0x1A,
            parent_bus_cells=0x0,
            parent_bus_addr=None,
            length_cells=0x2,
            length=0x1B0000001C,
        ),
        _EDTRange(
            node=node,
            child_bus_cells=0x1,
            child_bus_addr=0x2A,
            parent_bus_cells=0x0,
            parent_bus_addr=None,
            length_cells=0x2,
            length=0x2B0000002C,
        ),
    ]

    node = stree.node_by_path("/ranges-two-address-cells/node@1").cast(_EDTNode)
    assert node.ranges == [
        _EDTRange(
            node=node,
            child_bus_cells=0x2,
            child_bus_addr=0xA0000000B,
            parent_bus_cells=0x1,
            parent_bus_addr=0xC,
            length_cells=0x1,
            length=0xD,
        ),
        _EDTRange(
            node=node,
            child_bus_cells=0x2,
            child_bus_addr=0x1A0000001B,
            parent_bus_cells=0x1,
            parent_bus_addr=0x1C,
            length_cells=0x1,
            length=0x1D,
        ),
        _EDTRange(
            node=node,
            child_bus_cells=0x2,
            child_bus_addr=0x2A0000002B,
            parent_bus_cells=0x1,
            parent_bus_addr=0x2C,
            length_cells=0x1,
            length=0x2D,
        ),
    ]

    node = stree.node_by_path("/ranges-two-address-two-size-cells/node@1").cast(
        _EDTNode
    )
    assert node.ranges == [
        _EDTRange(
            node=node,
            child_bus_cells=0x2,
            child_bus_addr=0xA0000000B,
            parent_bus_cells=0x1,
            parent_bus_addr=0xC,
            length_cells=0x2,
            length=0xD0000000E,
        ),
        _EDTRange(
            node=node,
            child_bus_cells=0x2,
            child_bus_addr=0x1A0000001B,
            parent_bus_cells=0x1,
            parent_bus_addr=0x1C,
            length_cells=0x2,
            length=0x1D0000001E,
        ),
        _EDTRange(
            node=node,
            child_bus_cells=0x2,
            child_bus_addr=0x2A0000002B,
            parent_bus_cells=0x1,
            parent_bus_addr=0x2C,
            length_cells=0x2,
            length=0x2D0000001D,
        ),
    ]

    node = stree.node_by_path("/ranges-three-address-cells/node@1").cast(_EDTNode)
    assert node.ranges == [
        _EDTRange(
            node=node,
            child_bus_cells=0x3,
            child_bus_addr=0xA0000000B0000000C,
            parent_bus_cells=0x2,
            parent_bus_addr=0xD0000000E,
            length_cells=0x1,
            length=0xF,
        ),
        _EDTRange(
            node=node,
            child_bus_cells=0x3,
            child_bus_addr=0x1A0000001B0000001C,
            parent_bus_cells=0x2,
            parent_bus_addr=0x1D0000001E,
            length_cells=0x1,
            length=0x1F,
        ),
        _EDTRange(
            node=node,
            child_bus_cells=0x3,
            child_bus_addr=0x2A0000002B0000002C,
            parent_bus_cells=0x2,
            parent_bus_addr=0x2D0000002E,
            length_cells=0x1,
            length=0x2F,
        ),
    ]

    node = stree.node_by_path("/ranges-three-address-two-size-cells/node@1").cast(
        _EDTNode
    )
    assert node.ranges == [
        _EDTRange(
            node=node,
            child_bus_cells=0x3,
            child_bus_addr=0xA0000000B0000000C,
            parent_bus_cells=0x2,
            parent_bus_addr=0xD0000000E,
            length_cells=0x2,
            length=0xF00000010,
        ),
        _EDTRange(
            node=node,
            child_bus_cells=0x3,
            child_bus_addr=0x1A0000001B0000001C,
            parent_bus_cells=0x2,
            parent_bus_addr=0x1D0000001E,
            length_cells=0x2,
            length=0x1F00000110,
        ),
        _EDTRange(
            node=node,
            child_bus_cells=0x3,
            child_bus_addr=0x2A0000002B0000002C,
            parent_bus_cells=0x2,
            parent_bus_addr=0x2D0000002E,
            length_cells=0x2,
            length=0x2F00000210,
        ),
    ]


def test_reg():
    """Tests for the regs property"""
    with from_here():
        stree = STree().add_partial_tree(EDTree("test.dts", "test-bindings")).process()

    def verify_regs(node: _EDTNode, expected_tuples):
        regs = node.regs
        assert len(regs) == len(expected_tuples)
        for reg, expected_tuple in zip(regs, expected_tuples):
            name, addr, size = expected_tuple
            assert reg.node is node
            assert reg.name == name
            assert reg.addr == addr
            assert reg.size == size

    verify_regs(
        stree.node_by_path("/reg-zero-address-cells/node").cast(_EDTNode),
        [("foo", None, 0x1), ("bar", None, 0x2)],
    )

    verify_regs(
        stree.node_by_path("/reg-zero-size-cells/node").cast(_EDTNode),
        [(None, 0x1, None), (None, 0x2, None)],
    )

    verify_regs(
        stree.node_by_path("/reg-ranges/parent/node").cast(_EDTNode),
        [
            (None, 0x5, 0x1),
            (None, 0xE0000000F, 0x1),
            (None, 0xC0000000E, 0x1),
            (None, 0xC0000000D, 0x1),
            (None, 0xA0000000B, 0x1),
            (None, 0x0, 0x1),
        ],
    )

    verify_regs(
        stree.node_by_path("/reg-nested-ranges/grandparent/parent/node").cast(_EDTNode),
        [(None, 0x30000000200000001, 0x1)],
    )


def test_pinctrl():
    """Test 'pinctrl-<index>'."""
    with from_here():
        stree = STree().add_partial_tree(EDTree("test.dts", "test-bindings")).process()

    node = stree.node_by_path("/pinctrl/dev").cast(_EDTNode)
    state_1 = stree.node_by_path("/pinctrl/pincontroller/state-1").cast(_EDTNode)
    state_2 = stree.node_by_path("/pinctrl/pincontroller/state-2").cast(_EDTNode)
    assert node.pinctrls == [
        _EDTPinCtrl(node=node, name="zero", conf_nodes=[]),
        _EDTPinCtrl(node=node, name="one", conf_nodes=[state_1]),
        _EDTPinCtrl(node=node, name="two", conf_nodes=[state_1, state_2]),
    ]


def test_hierarchy():
    """Test Node.parent and Node.children"""
    with from_here():
        stree = STree().add_partial_tree(EDTree("test.dts", "test-bindings")).process()

    assert stree.node_by_path("/").parent is None

    assert (
        str(stree.node_by_path("/parent/child-1").parent)
        == "<STMergedNode /parent in 'test.dts', no binding>"
    )

    assert (
        str(stree.node_by_path("/parent/child-2/grandchild").parent)
        == "<STMergedNode /parent/child-2 in 'test.dts', no binding>"
    )

    assert (
        str(
            OrderedDict(
                [
                    [name, child]
                    for name, child in stree.node_by_path("/parent").children.items()
                ]
            )
        )
        == "OrderedDict({'child-1': <STMergedNode /parent/child-1 in 'test.dts', no binding>, 'child-2': <STMergedNode /parent/child-2 in 'test.dts', no binding>})"
    )

    assert stree.node_by_path("/parent/child-1").children == {}


def test_child_index():
    """Test Node.child_index."""
    with from_here():
        stree = STree().add_partial_tree(EDTree("test.dts", "test-bindings")).process()

    parent, child_1, child_2 = [
        stree.node_by_path(path)
        for path in ("/parent", "/parent/child-1", "/parent/child-2")
    ]
    assert parent.child_index(child_1) == 0
    assert parent.child_index(child_2) == 1
    with pytest.raises(KeyError):
        parent.child_index(parent)


def test_include():
    """Test 'include:' and the legacy 'inherits: !include ...' in bindings"""
    with from_here():
        stree = STree().add_partial_tree(EDTree("test.dts", "test-bindings")).process()

    binding_include = stree.node_by_path("/binding-include")

    assert binding_include.description == "Parent binding"

    verify_props(
        binding_include,
        ["foo", "bar", "baz", "qaz"],
        ["int", "int", "int", "int"],
        [0, 1, 2, 3],
    )

    verify_props(
        stree.node_by_path("/binding-include/child"),
        ["foo", "bar", "baz", "qaz"],
        ["int", "int", "int", "int"],
        [0, 1, 2, 3],
    )


def test_bus():
    """Test 'bus:' and 'on-bus:' in bindings"""
    with from_here():
        stree = STree().add_partial_tree(EDTree("test.dts", "test-bindings")).process()

    foo_bus = stree.node_by_path("/buses/foo-bus").cast(_EDTNode)
    assert isinstance(foo_bus.buses, list)
    assert "foo" in foo_bus.buses

    # foo-bus does not itself appear on a bus
    assert isinstance(foo_bus.on_buses, list)
    assert not foo_bus.on_buses
    assert foo_bus.bus_node is None

    # foo-bus/node1 is not a bus node...
    foo_bus_node1 = stree.node_by_path("/buses/foo-bus/node1").cast(_EDTNode)
    assert isinstance(foo_bus_node1.buses, list)
    assert not foo_bus_node1.buses
    # ...but is on a bus
    assert isinstance(foo_bus_node1.on_buses, list)
    assert "foo" in foo_bus_node1.on_buses
    assert foo_bus_node1.bus_node.path == "/buses/foo-bus"

    # foo-bus/node2 is not a bus node...
    foo_bus_node2 = stree.node_by_path("/buses/foo-bus/node2").cast(_EDTNode)
    assert isinstance(foo_bus_node2.buses, list)
    assert not foo_bus_node2.buses
    # ...but is on a bus
    assert isinstance(foo_bus_node2.on_buses, list)
    assert "foo" in foo_bus_node2.on_buses

    # no-bus-node is not a bus node...
    no_bus_node = stree.node_by_path("/buses/no-bus-node").cast(_EDTNode)
    assert isinstance(no_bus_node.buses, list)
    assert not no_bus_node.buses
    # ... and is not on a bus
    assert isinstance(no_bus_node.on_buses, list)
    assert not no_bus_node.on_buses

    # Same schema string, but different binding variants due to being on
    # different buses
    assert str(foo_bus_node1.binding_paths) == hpath(
        "['test-bindings/device-on-foo-bus.yaml', 'test-bindings/device-on-any-bus.yaml']"
    )
    assert str(foo_bus_node2.binding_paths) == hpath(
        "['test-bindings/device-on-any-bus.yaml', 'test-bindings/device-on-foo-bus.yaml']"
    )
    assert str(
        stree.node_by_path("/buses/bar-bus/node").cast(_EDTNode).binding_paths
    ) == hpath("['test-bindings/device-on-bar-bus.yaml']")
    assert str(no_bus_node.binding_paths) == hpath(
        "['test-bindings/device-on-any-bus.yaml']"
    )

    # foo-bus/node/nested also appears on the foo-bus bus
    nested_node = stree.node_by_path("/buses/foo-bus/node1/nested").cast(_EDTNode)
    assert isinstance(nested_node.on_buses, list)
    assert "foo" in nested_node.on_buses
    assert str(nested_node.binding_paths) == hpath(
        "['test-bindings/device-on-foo-bus.yaml']"
    )


def test_child_binding():
    """Test 'child-binding:' in bindings"""
    with from_here():
        stree = STree().add_partial_tree(EDTree("test.dts", "test-bindings")).process()
    child1 = stree.node_by_path("/child-binding/child-1")
    child2 = stree.node_by_path("/child-binding/child-2")
    grandchild = stree.node_by_path("/child-binding/child-1/grandchild")

    assert str(child1.binding_paths) == hpath("['test-bindings/child-binding.yaml']")
    assert str(child1.description) == "child node"
    verify_props(child1, ["child-prop"], ["int"], [1])

    assert str(child2.binding_paths) == hpath("['test-bindings/child-binding.yaml']")
    assert str(child2.description) == "child node"
    verify_props(child2, ["child-prop"], ["int"], [3])

    assert str(grandchild.binding_paths) == hpath(
        "['test-bindings/child-binding.yaml']"
    )
    assert str(grandchild.description) == "grandchild node"
    verify_props(grandchild, ["grandchild-prop"], ["int"], [2])

    with from_here():
        binding_file = Path("test-bindings/child-binding.yaml").resolve()
        top = _EDTBinding(binding_file)
    child_bindings = top.prop2bindings[".*"][1]
    assert len(child_bindings) == 1
    child = child_bindings[0]
    assert Path(top.path) == binding_file
    assert Path(child.path) == binding_file
    assert top.schema == "top-binding"
    assert child.schema is None

    with from_here():
        binding_file = Path("test-bindings/child-binding-with-schema.yaml").resolve()
        top = _EDTBinding(binding_file)
    child_bindings = top.prop2bindings[".*"][1]
    assert len(child_bindings) == 1
    child = child_bindings[0]
    assert Path(top.path) == binding_file
    assert Path(child.path) == binding_file
    assert top.schema == "top-binding-with-schema"
    assert child.schema == "child-schema"


def test_props():
    """Test Node.props (derived from DT and 'properties:' in the binding)"""
    with from_here():
        stree = STree().add_partial_tree(EDTree("test.dts", "test-bindings")).process()

    props_node = stree.node_by_path("/props").cast(_EDTNode)
    ctrl_1, ctrl_2 = [
        stree.node_by_path(path).cast(_EDTNode) for path in ["/ctrl-1", "/ctrl-2"]
    ]

    verify_props(
        props_node,
        [
            "int",
            "existent-boolean",
            "nonexistent-boolean",
            "array",
            "uint8-array",
            "string",
            "string-array",
            "phandle-ref",
            "phandle-refs",
            "path",
        ],
        [
            "int",
            "boolean",
            "boolean",
            "array",
            "uint8-array",
            "string",
            "string-array",
            "phandle",
            "phandles",
            "path",
        ],
        [
            1,
            True,
            False,
            [1, 2, 3],
            b"\x124",
            "foo",
            ["foo", "bar", "baz"],
            ctrl_1,
            [ctrl_1, ctrl_2],
            ctrl_1,
        ],
    )

    verify_phandle_array_prop(
        props_node,
        "phandle-array-foos",
        [(ctrl_1, {"one": 1}), (ctrl_2, {"one": 2, "two": 3})],
    )

    verify_phandle_array_prop(
        stree.node_by_path("/props-2").cast(_EDTNode),
        "phandle-array-foos",
        [
            (stree.node_by_path("/ctrl-0-1").cast(_EDTNode), {}),
            None,
            (stree.node_by_path("/ctrl-0-2").cast(_EDTNode), {}),
        ],
    )

    verify_phandle_array_prop(props_node, "foo-gpios", [(ctrl_1, {"gpio-one": 1})])


def test_nexus():
    """Test <prefix>-map via gpio-map (the most common case)."""
    with from_here():
        stree = STree().add_partial_tree(EDTree("test.dts", "test-bindings")).process()

    source = stree.node_by_path("/gpio-map/source").cast(_EDTNode)
    destination = stree.node_by_path("/gpio-map/destination").cast(_EDTNode)
    verify_phandle_array_prop(
        source, "foo-gpios", [(destination, {"val": 6}), (destination, {"val": 5})]
    )

    assert source.props["foo-gpios"].val[0].basename == f"gpio"


def test_prop_defaults():
    """Test property default values given in bindings"""
    with from_here():
        stree = STree().add_partial_tree(EDTree("test.dts", "test-bindings")).process()

    verify_props(
        stree.node_by_path("/defaults"),
        ["int", "array", "uint8-array", "string", "string-array", "default-not-used"],
        ["int", "array", "uint8-array", "string", "string-array", "int"],
        [123, [1, 2, 3], b"\x89\xab\xcd", "hello", ["hello", "there"], 234],
    )


def test_prop_enums():
    """test properties with enum: in the binding"""

    with from_here():
        stree = STree().add_partial_tree(EDTree("test.dts", "test-bindings")).process()
    props = stree.node_by_path("/enums").props
    int_enum = props["int-enum"]
    string_enum = props["string-enum"]
    tokenizable_enum = props["tokenizable-enum"]
    tokenizable_lower_enum = props["tokenizable-lower-enum"]
    array_enum = props["array-enum"]
    string_array_enum = props["string-array-enum"]
    no_enum = props["no-enum"]

    assert int_enum.val == 1
    assert int_enum.enum_indices[0] == 0
    assert not int_enum.spec.enum_tokenizable
    assert not int_enum.spec.enum_upper_tokenizable

    assert string_enum.val == "foo_bar"
    assert string_enum.enum_indices[0] == 1
    assert not string_enum.spec.enum_tokenizable
    assert not string_enum.spec.enum_upper_tokenizable

    assert tokenizable_enum.val == "123 is ok"
    assert tokenizable_enum.val_as_tokens[0] == "123_is_ok"
    assert tokenizable_enum.enum_indices[0] == 2
    assert tokenizable_enum.spec.enum_tokenizable
    assert tokenizable_enum.spec.enum_upper_tokenizable

    assert tokenizable_lower_enum.val == "bar"
    assert tokenizable_lower_enum.val_as_tokens[0] == "bar"
    assert tokenizable_lower_enum.enum_indices[0] == 0
    assert tokenizable_lower_enum.spec.enum_tokenizable
    assert not tokenizable_lower_enum.spec.enum_upper_tokenizable

    assert array_enum.val == [0, 40, 40, 10]
    assert array_enum.enum_indices == [0, 4, 4, 1]
    assert not array_enum.spec.enum_tokenizable
    assert not array_enum.spec.enum_upper_tokenizable

    assert string_array_enum.val == ["foo", "bar"]
    assert string_array_enum.val_as_tokens == ["foo", "bar"]
    assert string_array_enum.enum_indices == [1, 0]
    assert string_array_enum.spec.enum_tokenizable
    assert string_array_enum.spec.enum_upper_tokenizable

    assert no_enum.enum_indices is None
    assert not no_enum.spec.enum_tokenizable
    assert not no_enum.spec.enum_upper_tokenizable


def test_binding_inference():
    """Test inferred bindings for special zephyr-specific nodes."""
    warnings = io.StringIO()
    with from_here():
        stree = (
            STree()
            .add_partial_tree(EDTree("test.dts", "test-bindings", warnings))
            .process()
        )

    assert str(stree.node_by_path("/zephyr,user").props) == "OrderedDict()"

    with from_here():
        stree = (
            STree(warnings)
            .add_partial_tree(
                EDTree(
                    "test.dts",
                    "test-bindings",
                    warnings,
                    infer_binding_for_paths=["/zephyr,user"],
                ),
            )
            .process()
        )
    ctrl_1 = stree.node_by_path("/ctrl-1")
    ctrl_2 = stree.node_by_path("/ctrl-2")
    zephyr_user = stree.node_by_path("/zephyr,user")

    verify_props(
        zephyr_user,
        ["boolean", "bytes", "number", "numbers", "string", "strings"],
        ["boolean", "uint8-array", "int", "array", "string", "string-array"],
        [True, b"\x81\x82\x83", 23, [1, 2, 3], "text", ["a", "b", "c"]],
    )

    assert zephyr_user.props["handle"].val is ctrl_1

    phandles = zephyr_user.props["phandles"]
    val = phandles.val
    assert len(val) == 2
    assert val[0] is ctrl_1
    assert val[1] is ctrl_2

    verify_phandle_array_prop(
        zephyr_user,
        "phandle-array-foos",
        [(stree.node_by_path("/ctrl-2").cast(_EDTNode), {"one": 1, "two": 2})],
    )


def test_multi_bindings():
    """Test having multiple directories with bindings"""
    with from_here():
        stree = (
            STree()
            .add_partial_tree(
                EDTree("test-multidir.dts", ["test-bindings", "test-bindings-2"])
            )
            .process()
        )

    assert str(stree.node_by_path("/in-dir-1").binding_paths) == hpath(
        "['test-bindings/multidir.yaml']"
    )

    assert str(stree.node_by_path("/in-dir-2").binding_paths) == hpath(
        "['test-bindings-2/multidir.yaml']"
    )


def test_dependencies():
    """'Test dependency relations"""
    with from_here():
        stree = (
            STree()
            .add_partial_tree(
                EDTree("test-multidir.dts", ["test-bindings", "test-bindings-2"])
            )
            .process()
        )

    assert stree.node_by_path("/").dep_ordinal == 0
    assert stree.node_by_path("/in-dir-1").dep_ordinal == 1
    assert stree.node_by_path("/") in stree.node_by_path("/in-dir-1").depends_on
    assert stree.node_by_path("/in-dir-1") in stree.node_by_path("/").required_by


def test_child_dependencies():
    """Test dependencies relashionship with child nodes propagated to parent"""
    with from_here():
        stree = STree().add_partial_tree(EDTree("test.dts", "test-bindings")).process()

    dep_node = stree.node_by_path("/child-binding-dep")

    assert dep_node in stree.node_by_path("/child-binding").depends_on
    assert (
        dep_node in stree.node_by_path("/child-binding/child-1/grandchild").depends_on
    )
    assert dep_node in stree.node_by_path("/child-binding/child-2").depends_on
    assert stree.node_by_path("/child-binding") in dep_node.required_by
    assert (
        stree.node_by_path("/child-binding/child-1/grandchild") in dep_node.required_by
    )
    assert stree.node_by_path("/child-binding/child-2") in dep_node.required_by


def test_slice_errs(tmp_path):
    """Test error messages from the internal _slice() helper"""

    dts_file = tmp_path / "error.dts"

    verify_error(
        """
/dts-v1/;

/ {
	#address-cells = <1>;
	#size-cells = <2>;

	sub {
		reg = <3>;
	};
};
""",
        dts_file,
        EDTError,
        f"'reg' property in <Node /sub in '{dts_file}'> has length 4, which is not evenly divisible by 12 (= 4*(<#address-cells> (= 1) + <#size-cells> (= 2))). Note that #*-cells properties come either from the parent node or from the controller (in the case of 'interrupts').",
    )

    verify_error(
        """
/dts-v1/;

/ {
	sub {
		interrupts = <1>;
		interrupt-parent = < &{/controller} >;
	};
	controller {
		interrupt-controller;
		#interrupt-cells = <2>;
	};
};
""",
        dts_file,
        EDTError,
        f"'interrupts' property in <Node /sub in '{dts_file}'> has length 4, which is not evenly divisible by 8 (= 4*<#interrupt-cells>). Note that #*-cells properties come either from the parent node or from the controller (in the case of 'interrupts').",
    )

    verify_error(
        """
/dts-v1/;

/ {
	#address-cells = <1>;

	sub-1 {
		#address-cells = <2>;
		#size-cells = <3>;
		ranges = <4 5>;

		sub-2 {
			reg = <1 2 3 4 5>;
		};
	};
};
""",
        dts_file,
        EDTError,
        f"'ranges' property in <Node /sub-1 in '{dts_file}'> has length 8, which is not evenly divisible by 24 (= 4*(<#address-cells> (= 2) + <#address-cells for parent> (= 1) + <#size-cells> (= 3))). Note that #*-cells properties come either from the parent node or from the controller (in the case of 'interrupts').",
    )


def test_bad_compatible(tmp_path):
    # An invalid compatible should cause an error, even on a node with
    # no binding.

    dts_file = tmp_path / "error.dts"

    verify_error(
        """
/dts-v1/;

/ {
	foo {
		compatible = "no, whitespace";
	};
};
""",
        dts_file,
        STError,
        r"node '/foo' schema 'no, whitespace' must match this regular expression: '^[a-zA-Z][a-zA-Z0-9,+\-._]+$'",
    )


def test_deepcopy():
    with from_here():
        # We intentionally use different kwarg values than the
        # defaults to make sure they're getting copied. This implies
        # we have to set werror=True, so we can't use test.dts, since
        # that generates warnings on purpose.
        stree = (
            STree(
                vendor_prefixes={"test-vnd": "A test vendor"},
                err_on_missing_vendor=True,
            )
            .add_partial_tree(
                EDTree(
                    "test-multidir.dts",
                    ["test-bindings", "test-bindings-2"],
                    warn_reg_unit_address_mismatch=False,
                    default_prop_types=False,
                    err_on_deprecated=True,
                    support_fixed_partitions_on_any_bus=False,
                    infer_binding_for_paths=["/test-node"],
                ),
            )
            .process()
        )
        stree_copy = deepcopy(stree)

    def equal_paths(list1: list[_STMergedNode], list2: list[_STMergedNode]):
        assert len(list1) == len(list2)
        return all(elt1.path == elt2.path for elt1, elt2 in zip(list1, list2))

    def equal_key2path(
        key2node1: dict[str, _STMergedNode], key2node2: dict[str, _STMergedNode]
    ):
        assert len(key2node1) == len(key2node2)
        return all(key1 == key2 for (key1, key2) in zip(key2node1, key2node2)) and all(
            node1.path == node2.path
            for (node1, node2) in zip(key2node1.values(), key2node2.values())
        )

    def equal_key2paths(
        key2nodes1: dict[str, list[_STMergedNode]],
        key2nodes2: dict[str, list[_STMergedNode]],
    ):
        assert len(key2nodes1) == len(key2nodes2)
        return all(
            key1 == key2 for (key1, key2) in zip(key2nodes1, key2nodes2)
        ) and all(
            equal_paths(nodes1, nodes2)
            for (nodes1, nodes2) in zip(key2nodes1.values(), key2nodes2.values())
        )

    def test_stree_equal_but_not_same(attribute, equal=None):
        if equal is None:
            equal = lambda a, b: a == b
        copy = getattr(stree_copy, attribute)
        original = getattr(stree, attribute)
        assert equal(copy, original)
        assert copy is not original

    test_stree_equal_but_not_same("nodes", equal_paths)
    test_stree_equal_but_not_same("label2node", equal_key2path)
    test_stree_equal_but_not_same("compat2nodes", equal_key2paths)
    test_stree_equal_but_not_same("compat2okay", equal_key2paths)
    test_stree_equal_but_not_same("compat2notokay", equal_key2paths)
    test_stree_equal_but_not_same("compat2vendor")
    test_stree_equal_but_not_same("compat2model")
    test_stree_equal_but_not_same("dep_ord2node", equal_key2path)
    assert stree_copy._processing_state
    assert stree_copy._vendor_prefixes == {"test-vnd": "A test vendor"}
    assert stree_copy._vendor_prefixes is not stree._vendor_prefixes
    assert stree_copy._err_on_missing_vendor
    test_stree_equal_but_not_same("_path2node", equal_key2path)

    edtree: EDTree = stree.edtree
    edtree_copy: EDTree = stree_copy.edtree

    def test_edtree_equal_but_not_same(attribute, equal=None):
        if equal is None:
            equal = lambda a, b: a == b
        copy = getattr(edtree_copy, attribute)
        original = getattr(edtree, attribute)
        assert equal(copy, original)
        assert copy is not original

    assert edtree_copy.source_path == "test-multidir.dts"
    assert edtree_copy.bindings_dirs == ["test-bindings", "test-bindings-2"]
    assert edtree_copy.bindings_dirs is not edtree.bindings_dirs
    test_edtree_equal_but_not_same("_schemas")
    test_edtree_equal_but_not_same("_schema2binding", equal_key2path)
    test_edtree_equal_but_not_same("_binding_paths")
    test_edtree_equal_but_not_same("_binding_fname2path")
    assert not edtree_copy._warn_reg_unit_address_mismatch
    assert not edtree_copy._default_prop_types
    assert not edtree_copy._fixed_partitions_no_bus
    assert edtree_copy._infer_binding_for_paths == set(["/test-node"])
    assert edtree_copy._infer_binding_for_paths is not edtree._infer_binding_for_paths
    assert len(edtree_copy._dtnode2enode) == len(edtree._dtnode2enode)
    for node1, node2 in zip(edtree_copy._dtnode2enode, edtree._dtnode2enode):
        enode1 = edtree_copy._dtnode2enode[node1]
        enode2 = edtree._dtnode2enode[node2]
        assert node1.path == node2.path
        assert enode1.path == enode2.path
        assert node1 is not node2
        assert enode1 is not enode2
    assert edtree_copy._dt is not edtree._dt


def verify_error(dts, dts_file, expected_err, expected_msg):
    # Verifies that parsing a file 'dts_file' with the contents 'dts'
    # (a string) raises an EDTError with the message 'expected_err'.
    #
    # The path 'dts_file' is written with the string 'dts' before the
    # test is run.

    with open(dts_file, "w", encoding="utf-8") as f:
        f.write(dts)
        f.flush()  # Can't have unbuffered text IO, so flush() instead

    with pytest.raises(expected_err) as e:
        STree().add_partial_tree(EDTree(dts_file, [])).process()

    assert str(e.value) == expected_msg


def verify_props(node: _STMergedNode, names, types, values):
    # Verifies that each property in 'names' has the expected
    # value in 'values'. Property lookup is done in Node 'node'.

    for name, type, value in zip(names, types, values):
        prop = node.props[name]
        assert prop.name == name
        assert prop.type == type
        assert prop.val == value
        assert prop.node is node


def verify_phandle_array_prop(node: _EDTNode, name, values):
    # Verifies 'node.props[name]' is a phandle-array, and has the
    # expected controller/data values in 'values'. Elements
    # of 'values' may be None.

    prop = node.props[name]
    assert prop.type == "phandle-array"
    assert prop.name == name
    val = prop.val
    assert isinstance(val, list)
    assert len(val) == len(values)
    for actual, expected in zip(val, values):
        if expected is not None:
            controller, data = expected
            assert isinstance(actual, _EDTControllerAndData)
            assert actual.controller is controller
            assert actual.data == data
        else:
            assert actual is None


def test_stree_pickle():
    with from_here():
        stree = STree().add_partial_tree(EDTree("test.dts", "test-bindings")).process()
        pickle.loads(pickle.dumps(stree))  # Make sure it's pickleable
        stree_copy = deepcopy(stree)
        pickle.loads(pickle.dumps(stree_copy))  # Make sure it's pickleable
