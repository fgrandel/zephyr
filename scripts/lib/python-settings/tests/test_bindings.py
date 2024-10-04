# Copyright (c) 2019 Nordic Semiconductor ASA
# SPDX-License-Identifier: BSD-3-Clause

import contextlib
import os

import pytest

import bindings

# Test suite for bindings.py.
#
# Run it using pytest (https://docs.pytest.org/en/stable/usage.html):
#
#   $ pytest test_bindings.py

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


def test_include_filters():
    """Test property-allowlist and property-blocklist in an include."""

    fname2path = {
        "include.yaml": "test-bindings-include/include.yaml",
        "include-2.yaml": "test-bindings-include/include-2.yaml",
    }

    with pytest.raises(bindings.BindingError) as e:
        with from_here():
            bindings.Binding(
                "test-bindings-include/allow-and-blocklist.yaml", fname2path
            )
    assert (
        "should not specify both 'property-allowlist:' and 'property-blocklist:'"
        in str(e.value)
    )

    with pytest.raises(bindings.BindingError) as e:
        with from_here():
            bindings.Binding(
                "test-bindings-include/allow-and-blocklist-child.yaml", fname2path
            )
    assert (
        "should not specify both 'property-allowlist:' and 'property-blocklist:'"
        in str(e.value)
    )

    with pytest.raises(bindings.BindingError) as e:
        with from_here():
            bindings.Binding("test-bindings-include/allow-not-list.yaml", fname2path)
    value_str = str(e.value)
    assert value_str.startswith("'property-allowlist' value")
    assert value_str.endswith("should be a list")

    with pytest.raises(bindings.BindingError) as e:
        with from_here():
            bindings.Binding("test-bindings-include/block-not-list.yaml", fname2path)
    value_str = str(e.value)
    assert value_str.startswith("'property-blocklist' value")
    assert value_str.endswith("should be a list")

    with pytest.raises(bindings.BindingError) as e:
        with from_here():
            binding = bindings.Binding(
                "test-bindings-include/include-invalid-keys.yaml", fname2path
            )
    value_str = str(e.value)
    assert value_str.startswith(
        "'include:' in test-bindings-include/include-invalid-keys.yaml should not have these "
        "unexpected contents: "
    )
    assert "bad-key-1" in value_str
    assert "bad-key-2" in value_str

    with pytest.raises(bindings.BindingError) as e:
        with from_here():
            binding = bindings.Binding(
                "test-bindings-include/include-invalid-type.yaml", fname2path
            )
    value_str = str(e.value)
    assert value_str.startswith(
        "'include:' in test-bindings-include/include-invalid-type.yaml "
        "should be a string or list, but has type "
    )

    with pytest.raises(bindings.BindingError) as e:
        with from_here():
            binding = bindings.Binding(
                "test-bindings-include/include-no-name.yaml", fname2path
            )
    value_str = str(e.value)
    assert value_str.startswith("'include:' element")
    assert value_str.endswith(
        "in test-bindings-include/include-no-name.yaml should have a 'name' key"
    )

    with from_here():
        binding = bindings.Binding("test-bindings-include/allowlist.yaml", fname2path)
        assert set(binding.prop2specs.keys()) == {"x"}  # 'x' is allowed

        binding = bindings.Binding(
            "test-bindings-include/empty-allowlist.yaml", fname2path
        )
        assert set(binding.prop2specs.keys()) == set()  # nothing is allowed

        binding = bindings.Binding("test-bindings-include/blocklist.yaml", fname2path)
        assert set(binding.prop2specs.keys()) == {"y", "z"}  # 'x' is blocked

        binding = bindings.Binding(
            "test-bindings-include/empty-blocklist.yaml", fname2path
        )
        assert set(binding.prop2specs.keys()) == {"x", "y", "z"}  # nothing is blocked

        binding = bindings.Binding("test-bindings-include/intermixed.yaml", fname2path)
        assert set(binding.prop2specs.keys()) == {"x", "a"}

        binding = bindings.Binding(
            "test-bindings-include/include-no-list.yaml", fname2path
        )
        assert set(binding.prop2specs.keys()) == {"x", "y", "z"}

        binding = bindings.Binding(
            "test-bindings-include/filter-child-bindings.yaml", fname2path
        )
        child = binding.child_binding
        grandchild = child.child_binding
        assert set(binding.prop2specs.keys()) == {"x"}
        assert set(child.prop2specs.keys()) == {"child-prop-2"}
        assert set(grandchild.prop2specs.keys()) == {"grandchild-prop-1"}

        binding = bindings.Binding(
            "test-bindings-include/allow-and-blocklist-multilevel.yaml", fname2path
        )
        assert set(binding.prop2specs.keys()) == {"x"}  # 'x' is allowed
        child = binding.child_binding
        assert set(child.prop2specs.keys()) == {
            "child-prop-1",
            "child-prop-2",
            "x",
            "z",
        }  # root level 'y' is blocked


def test_include_paths():
    """Test "last modified" semantic for included bindings paths."""

    fname2path = {
        "base.yaml": "test-bindings-include/base.yaml",
        "modified.yaml": "test-bindings-include/modified.yaml",
    }

    with from_here():
        top = bindings.Binding("test-bindings-include/top.yaml", fname2path)

        assert "modified.yaml" == os.path.basename(top.prop2specs["x"].path)
        assert "base.yaml" == os.path.basename(top.prop2specs["y"].path)
        assert "top.yaml" == os.path.basename(top.prop2specs["p"].path)


def test_include_filters_included_bindings():
    """Test filters set by including bindings."""
    fname2path = {
        "base.yaml": "test-bindings-include/base.yaml",
        "inc-base.yaml": "test-bindings-include/inc-base.yaml",
    }

    with from_here():
        top_allows = bindings.Binding(
            "test-bindings-include/top-allows.yaml", fname2path
        )
    assert top_allows.prop2specs.get("x")
    assert not top_allows.prop2specs.get("y")

    with from_here():
        top_blocks = bindings.Binding(
            "test-bindings-include/top-blocks.yaml", fname2path
        )
    assert not top_blocks.prop2specs.get("x")
    assert top_blocks.prop2specs.get("y")


def test_wrong_props():
    """Test Node.wrong_props (derived from DT and 'properties:' in the binding)"""

    with from_here():
        with pytest.raises(bindings.BindingError) as e:
            bindings.Binding(
                "test-wrong-bindings/wrong-specifier-space-type.yaml", None
            )
        assert (
            "'specifier-space' in 'properties: wrong-type-for-specifier-space' has type 'phandle', expected 'phandle-array'"
            in str(e.value)
        )

        with pytest.raises(bindings.BindingError) as e:
            bindings.Binding("test-wrong-bindings/wrong-phandle-array-name.yaml", None)
        value_str = str(e.value)
        assert value_str.startswith("'wrong-phandle-array-name' in 'properties:'")
        assert value_str.endswith("but no 'specifier-space' was provided.")
