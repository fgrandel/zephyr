# Copyright (c) 2021 Nordic Semiconductor ASA
# SPDX-License-Identifier: Apache-2.0

"""
The settings package provides a library for working with settings trees.

Note: Only use objects that are exported by the top-level settings package and
do not access private (_-prefixed) identifiers from the settings here. In
particular note that the settings library is not meant to expose the internal
dtlib or bindings API directly.

Please think twice before you export further objects from this library. The
smaller the surface area of the public API, the easier it is to maintain and
evolve the library.

Hint: You can view the documentation of this package with pydoc3, e.g.
'$> pydoc3 settings.stree'
"""

# Implementation notes for the settings library package
# -----------------------------------------------------
#
# This library can be used to build a normalized, merged settings tree from
# devicetree (.dts) and configuration tree (.config.yaml) files. Information
# from binding files in YAML format is used to validate all settings and expose
# them in a form that can be used to generate C-typed macros.
#
# Bindings are files that describe the schema of settings nodes. Settings nodes
# are mapped to bindings via their 'schema = "..."' property.
#
# See Zephyr's Devicetree and Configuration (TODO) user guides for details.
#
# Note: Only use objects that are exported by the top-level settings package and
# do not access private (_-prefixed) identifiers. In particular note that the
# settings library is not meant to expose the internal dtlib or bindings APIs
# directly.
#
# If you want to add features to the settings library then think from a client's
# perspective what API the client expects, and add it as a publicly documented
# API to the settings library. This will keep this script and the library
# simple, extensible and maintainable.
# A '_' prefix on an identifier in Python is a convention for marking it
# private. Please do not access private types, constants, classes, class members
# or functions. Instead, think of what API you need, and add it.
#
# None of the modules in this package is meant to have any global state. It
# should be possible to create several instances of STTree, EDTree and
# ConfigTree with independent binding paths and flags. If you need to add a
# configuration parameter or the like, store it in the STree or any of the
# partial tree instances, and initialize it e.g. with a constructor argument.
#
# General biased advice:
#
# - Consider using @property for APIs that don't need parameters. It makes
#   functions look like attributes, which is less awkward in clients, and makes
#   it easy to switch back and forth between variables and functions.
#
# - Think about the data type of the thing you're exposing. Exposing something
#   as e.g. a list or a dictionary is often nicer and more flexible than adding
#   a function. Use typing hints to docment the data type.
#
# - Avoid get_*() prefixes on functions. Name them after the thing they return
#   instead. This often makes the code read more naturally in callers.
#
#   Also, consider using @property instead of get_*().
#
# - Don't expose dtlib stuff directly.
#
# - Add documentation for any new APIs you add.
#
#   The convention here is that docstrings (quoted strings) are used for public
#   APIs, and "doc comments" for internal functions.
#
#   @properties are documented in the class docstring, as if they were
#   variables. See the existing @properties for a template.

import sys

# Note: We use the 'import as X from X' convention to mark public objects to be
# exported by the package. Please do not remove those.

# Legacy logging helper. Must be called before any other function in this module.
from settings.log import setup_logging as setup_logging

# Partial trees representing specific settings source formats. Instantiate these
# and add them to an STree:
from settings.configuration.configtree import ConfigTree as ConfigTree
from settings.devicetree.edtree import (
    EDTree as EDTree,
    bindings_from_paths as bindings_from_paths,
)

# STree is the top-level entry point to the settings library:
from settings.stree import (
    STree as STree,
    # Public helper functions:
    load_vendor_prefixes_txt as load_vendor_prefixes_txt,
    str_as_token as str_as_token,
    str2ident as str2ident,
)

# Errors that may be thrown by this library:
from settings.error import (
    # Base error class: Catch this if you want to catch all errors thrown by
    # this library.
    STError as STError,
    # Module-specific errors:
    EDTError as EDTError,  # Error related to EDTree processing.
    ConfigError as ConfigError,  # Error related to ConfigTree processing.
    STBindingError as STBindingError,  # Error related to bindings processing.
    STGraphError as STGraphError,  # Error related to graph processing.
    # Note: Do not expose DTError from dtlib. DTError should be wrapped by
    # EDTError where it may be thrown.
)


def zephyr_build_stree(
    dts_path: str,
    dts_bindings_dirs: list[str],
    config_path: str,
    config_bindings_dirs: list[str],
    vendor_prefix_files: list[str],
    strict: bool,
    warn_reg_unit_address_mismatch: bool,
) -> STree:
    """
    Build the default global settings tree for the Zephyr build system including
    both, devicetree and software configuration settings.
    """
    setup_logging()

    vendor_prefixes: dict[str, str] = {}
    for vendor_prefix_file in vendor_prefix_files:
        vendor_prefixes.update(load_vendor_prefixes_txt(vendor_prefix_file))

    try:
        stree = STree(
            vendor_prefixes=vendor_prefixes,
            err_on_missing_vendor=strict,
        )

        edt_tree = EDTree(
            dts_path,
            bindings_dirs=dts_bindings_dirs,
            warn_reg_unit_address_mismatch=warn_reg_unit_address_mismatch,
            err_on_deprecated=strict,
            infer_binding_for_paths=["/zephyr,user"],
        )
        stree.add_partial_tree(edt_tree)

        if config_path:
            config_tree = ConfigTree(
                config_path,
                bindings_dirs=config_bindings_dirs,
                err_on_deprecated=strict,
            )
            stree.add_partial_tree(config_tree)

        stree.process()

        return stree
    except STError as e:
        sys.exit(f"settings tree error: {e}")
