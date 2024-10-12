# Copyright (c) 2024 The Zephyr Project
# SPDX-License-Identifier: Apache-2.0

"""
Contains all errors that may be thrown by the settings-tree library.

The errors are organized in a hierarchy, with the base class being STError.

The following classes are defined:
- STError: Base class for all errors in the settings-tree library.
- DTError: Error related to devicetree processing.
- BindingError: Error related to bindings processing.
- EDTError: Error related to EDTree processing.
- ConfigError: Error related to ConfigTree processing.
- GraphError: Error related to graph processing.

Errors are kept in a separate module to avoid circular imports.
"""


class STError(Exception):
    "Exception raised for settings-tree related errors"


class DTError(STError):
    "Exception raised for devicetree-related errors"


class STBindingError(STError):
    "Exception raised for binding-related errors"


class EDTError(STError):
    "Exception raised for EDTree-related errors"


class ConfigError(STError):
    "Exception raised for ConfigTree-related errors"


class STGraphError(STError):
    "Exception raised for graph-related errors"
