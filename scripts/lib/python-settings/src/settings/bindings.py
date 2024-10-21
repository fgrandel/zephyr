# Copyright (c) 2024 The Zephyr Project
# SPDX-License-Identifier: Apache-2.0

"""
Both, devicetree and configuration sources, represent abstract trees of nodes
with named properties (key/value pairs). The configuration tree may interface to
the devicetree via references. Both trees combined represent the Zephyr settings
tree (ST). Properties from the devicetree are called hard(ware) settings while
properties from the configuration tree are called soft(ware) settings.

Bindings are YAML files that describe setting nodes and their properties. Nodes
are mapped to bindings via their 'compatible = "..."' (devicetree) or 'schema =
"..."' (configuration) property. A binding may impose restrictions on the bound
node as well as on its child nodes via 'child-binding's.

Settings nodes will be validated against bindings and property types (string,
array, integer, bool, ...). Allowable types and value ranges for properties will
be derived from the corresponding property definitions in the binding.

The combined information from DTS/configuration and binding sources will be made
available through STNode (settings tree node) instances that together represent
the STree (settings tree).

An STNode from the devicetree (DTNode) represents hardware settings while an
STNode from a configuration source (ConfigNode) contains software settings.

The STree will be normalized in the sense that properties belonging to the same
conceptual entity (i.e. functionally depending on the same entity ID or "key")
will be kept in the same STNode.
"""

from copy import deepcopy
from dataclasses import dataclass
from typing import (
    Any,
    Dict,
    List,
    NoReturn,
    Optional,
    Union,
    TypeVar,
    Generic,
    TYPE_CHECKING,
)
import os
import re

import yaml
try:
    # Use the C LibYAML parser if available, rather than the Python parser.
    # This makes e.g. gen_defines.py more than twice as fast.
    from yaml import CLoader as Loader
except ImportError:
    from yaml import Loader  # type: ignore

#
# Public classes
#

class Binding:
    """
    Represents a parsed binding.

    These attributes are available on Binding objects:

    path:
      The absolute path to the file defining the binding.

    description:
      The free-form description of the binding, or None.

    compatible:
      The compatible string the binding matches.

      This may be None. For example, it's None when the Binding is inferred
      from node properties. It can also be None for Binding objects created
      using 'child-binding:' with no compatible.

    prop2specs:
      A dict mapping property names to PropertySpec objects
      describing those properties' values.

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

    raw:
      The binding as an object parsed from YAML.

    bus:
      If nodes with this binding's 'compatible' describe a bus, a string
      describing the bus type (like "i2c") or a list describing supported
      protocols (like ["i3c", "i2c"]). None otherwise.

      Note that this is the raw value from the binding where it can be
      a string or a list. Use "buses" instead unless you need the raw
      value, where "buses" is always a list.

    buses:
      Deprived property from 'bus' where 'buses' is a list of bus(es),
      for example, ["i2c"] or ["i3c", "i2c"]. Or an empty list if there is
      no 'bus:' in this binding.

    on_bus:
      If nodes with this binding's 'compatible' appear on a bus, a string
      describing the bus type (like "i2c"). None otherwise.

    child_binding:
      If this binding describes the properties of child nodes, then
      this is a Binding object for those children; it is None otherwise.
      A Binding object's 'child_binding.child_binding' is not None if there
      are multiple levels of 'child-binding' descriptions in the binding.
    """

    def __init__(
        self,
        path: Optional[str],
        fname2path: Dict[str, str],
        raw: Any = None,
        require_compatible: bool = True,
        require_description: bool = True,
        inc_allowlist: Optional[List[str]] = None,
        inc_blocklist: Optional[List[str]] = None,
    ):
        """
        Binding constructor.

        path:
          Path to binding YAML file. May be None.

        fname2path:
          Map from include files to their absolute paths. Must
          not be None, but may be empty.

        raw:
          Optional raw content in the binding.
          This does not have to have any "include:" lines resolved.
          May be left out, in which case 'path' is opened and read.
          This can be used to resolve child bindings, for example.

        require_compatible:
          If True, it is an error if the binding does not contain a
          "compatible:" line. If False, a missing "compatible:" is
          not an error. Either way, "compatible:" must be a string
          if it is present in the binding.

        require_description:
          If True, it is an error if the binding does not contain a
          "description:" line. If False, a missing "description:" is
          not an error. Either way, "description:" must be a string
          if it is present in the binding.

        inc_allowlist:
          The property-allowlist filter set by including bindings.

        inc_blocklist:
          The property-blocklist filter set by including bindings.
        """
        self.path: Optional[str] = path
        self._fname2path: Dict[str, str] = fname2path

        self._inc_allowlist: Optional[List[str]] = inc_allowlist
        self._inc_blocklist: Optional[List[str]] = inc_blocklist

        if raw is None:
            if path is None:
                _err("you must provide either a 'path' or a 'raw' argument")
            with open(path, encoding="utf-8") as f:
                raw = yaml.load(f, Loader=_BindingLoader)

        # Get the properties this binding modifies
        # before we merge the included ones.
        last_modified_props = list(raw.get("properties", {}).keys())

        # Map property names to their specifications:
        # - first, _merge_includes() will recursively populate prop2specs with
        #   the properties specified by the included bindings
        # - eventually, we'll update prop2specs with the properties
        #   this binding itself defines or modifies
        self.prop2specs: Dict[str, "PropertySpec"] = {}

        # Merge any included files into self.raw. This also pulls in
        # inherited child binding definitions, so it has to be done
        # before initializing those.
        self.raw: dict = self._merge_includes(raw, self.path)

        # Recursively initialize any child bindings. These don't
        # require a 'compatible' or 'description' to be well defined,
        # but they must be dicts.
        if "child-binding" in raw:
            if not isinstance(raw["child-binding"], dict):
                _err(
                    f"malformed 'child-binding:' in {self.path}, "
                    "expected a binding (dictionary with keys/values)"
                )
            self.child_binding: Optional["Binding"] = Binding(
                path,
                fname2path,
                raw=raw["child-binding"],
                require_compatible=False,
                require_description=False,
            )
        else:
            self.child_binding = None

        # Make sure this is a well defined object.
        self._check(require_compatible, require_description)

        # Update specs with the properties this binding defines or modifies.
        for prop_name in last_modified_props:
            self.prop2specs[prop_name] = PropertySpec(prop_name, self)

        # Initialize look up tables.
        self.specifier2cells: Dict[str, List[str]] = {}
        for key, val in self.raw.items():
            if key.endswith("-cells"):
                self.specifier2cells[key[: -len("-cells")]] = val

    def __repr__(self) -> str:
        if self.compatible:
            compat = f" for compatible '{self.compatible}'"
        else:
            compat = ""
        basename = os.path.basename(self.path or "")
        return f"<Binding {basename}" + compat + ">"

    @property
    def description(self) -> Optional[str]:
        "See the class docstring"
        return self.raw.get("description")

    @property
    def compatible(self) -> Optional[str]:
        "See the class docstring"
        return self.raw.get("compatible")

    @property
    def bus(self) -> Union[None, str, List[str]]:
        "See the class docstring"
        return self.raw.get("bus")

    @property
    def buses(self) -> List[str]:
        "See the class docstring"
        if self.raw.get("bus") is not None:
            return self._buses
        else:
            return []

    @property
    def on_bus(self) -> Optional[str]:
        "See the class docstring"
        return self.raw.get("on-bus")

    def _merge_includes(self, raw: dict, binding_path: Optional[str]) -> dict:
        # Constructor helper. Merges included files in
        # 'raw["include"]' into 'raw' using 'self._include_paths' as a
        # source of include files, removing the "include" key while
        # doing so.
        #
        # This treats 'binding_path' as the binding file being built up
        # and uses it for error messages.

        if "include" not in raw:
            return raw

        include = raw.pop("include")

        # First, merge the included files together. If more than one included
        # file has a 'required:' for a particular property, OR the values
        # together, so that 'required: true' wins.

        merged: Dict[str, Any] = {}

        if isinstance(include, str):
            # Simple scalar string case
            # Load YAML file and register property specs into prop2specs.
            inc_raw = self._load_raw(include, self._inc_allowlist, self._inc_blocklist)

            _merge_props(merged, inc_raw, None, binding_path, False)
        elif isinstance(include, list):
            # List of strings and maps. These types may be intermixed.
            for elem in include:
                if isinstance(elem, str):
                    # Load YAML file and register property specs into prop2specs.
                    inc_raw = self._load_raw(
                        elem, self._inc_allowlist, self._inc_blocklist
                    )

                    _merge_props(merged, inc_raw, None, binding_path, False)
                elif isinstance(elem, dict):
                    name = elem.pop("name", None)

                    # Merge this include property-allowlist filter
                    # with filters from including bindings.
                    allowlist = elem.pop("property-allowlist", None)
                    if allowlist is not None:
                        if self._inc_allowlist:
                            allowlist.extend(self._inc_allowlist)
                    else:
                        allowlist = self._inc_allowlist

                    # Merge this include property-blocklist filter
                    # with filters from including bindings.
                    blocklist = elem.pop("property-blocklist", None)
                    if blocklist is not None:
                        if self._inc_blocklist:
                            blocklist.extend(self._inc_blocklist)
                    else:
                        blocklist = self._inc_blocklist

                    child_filter = elem.pop("child-binding", None)

                    if elem:
                        # We've popped out all the valid keys.
                        _err(
                            f"'include:' in {binding_path} should not have "
                            f"these unexpected contents: {elem}"
                        )

                    _check_include_dict(
                        name, allowlist, blocklist, child_filter, binding_path
                    )

                    # Load YAML file, and register (filtered) property specs
                    # into prop2specs.
                    contents = self._load_raw(name, allowlist, blocklist, child_filter)

                    _merge_props(merged, contents, None, binding_path, False)
                else:
                    _err(
                        f"all elements in 'include:' in {binding_path} "
                        "should be either strings or maps with a 'name' key "
                        "and optional 'property-allowlist' or "
                        f"'property-blocklist' keys, but got: {elem}"
                    )
        else:
            # Invalid item.
            _err(
                f"'include:' in {binding_path} "
                f"should be a string or list, but has type {type(include)}"
            )

        # Next, merge the merged included files into 'raw'. Error out if
        # 'raw' has 'required: false' while the merged included files have
        # 'required: true'.

        _merge_props(raw, merged, None, binding_path, check_required=True)

        return raw

    def _load_raw(
        self,
        fname: str,
        allowlist: Optional[List[str]] = None,
        blocklist: Optional[List[str]] = None,
        child_filter: Optional[dict] = None,
    ) -> dict:
        # Returns the contents of the binding given by 'fname' after merging
        # any bindings it lists in 'include:' into it, according to the given
        # property filters.
        #
        # Will also register the (filtered) included property specs
        # into prop2specs.

        path = self._fname2path.get(fname)

        if not path:
            _err(f"'{fname}' not found")

        with open(path, encoding="utf-8") as f:
            contents = yaml.load(f, Loader=_BindingLoader)
            if not isinstance(contents, dict):
                _err(f"{path}: invalid contents, expected a mapping")

        # Apply constraints to included YAML contents.
        _filter_properties(contents, allowlist, blocklist, child_filter, self.path)

        # Register included property specs.
        self._add_included_prop2specs(fname, contents, allowlist, blocklist)

        return self._merge_includes(contents, path)

    def _add_included_prop2specs(
        self,
        fname: str,
        contents: dict,
        allowlist: Optional[List[str]] = None,
        blocklist: Optional[List[str]] = None,
    ) -> None:
        # Registers the properties specified by an included binding file
        # into the properties this binding supports/requires (aka prop2specs).
        #
        # Consider "this" binding B includes I1 which itself includes I2.
        #
        # We assume to be called in that order:
        # 1) _add_included_prop2spec(B, I1)
        # 2) _add_included_prop2spec(B, I2)
        #
        # Where we don't want I2 "taking ownership" for properties
        # modified by I1.
        #
        # So we:
        # - first create a binding that represents the included file
        # - then add the property specs defined by this binding to prop2specs,
        #   without overriding the specs modified by an including binding
        #
        # Note: Unfortunately, we can't cache these base bindings,
        # as a same YAML file may be included with different filters
        # (property-allowlist and such), leading to different contents.

        inc_binding = Binding(
            self._fname2path[fname],
            self._fname2path,
            contents,
            require_compatible=False,
            require_description=False,
            # Recursively pass filters to included bindings.
            inc_allowlist=allowlist,
            inc_blocklist=blocklist,
        )

        for prop, spec in inc_binding.prop2specs.items():
            if prop not in self.prop2specs:
                self.prop2specs[prop] = spec

    def _check(self, require_compatible: bool, require_description: bool):
        # Does sanity checking on the binding.

        raw = self.raw

        if "compatible" in raw:
            compatible = raw["compatible"]
            if not isinstance(compatible, str):
                _err(
                    f"malformed 'compatible: {compatible}' "
                    f"field in {self.path} - "
                    f"should be a string, not {type(compatible).__name__}"
                )
        elif require_compatible:
            _err(f"missing 'compatible' in {self.path}")

        if "description" in raw:
            description = raw["description"]
            if not isinstance(description, str) or not description:
                _err(f"malformed or empty 'description' in {self.path}")
        elif require_description:
            _err(f"missing 'description' in {self.path}")

        # Allowed top-level keys. The 'include' key should have been
        # removed by _load_raw() already.
        ok_top = {
            "description",
            "compatible",
            "bus",
            "on-bus",
            "properties",
            "child-binding",
        }

        # Descriptive errors for legacy bindings.
        legacy_errors = {
            "#cells": "expected *-cells syntax",
            "child": "use 'bus: <bus>' instead",
            "child-bus": "use 'bus: <bus>' instead",
            "parent": "use 'on-bus: <bus>' instead",
            "parent-bus": "use 'on-bus: <bus>' instead",
            "sub-node": "use 'child-binding' instead",
            "title": "use 'description' instead",
        }

        for key in raw:
            if key in legacy_errors:
                _err(f"legacy '{key}:' in {self.path}, {legacy_errors[key]}")

            if key not in ok_top and not key.endswith("-cells"):
                _err(
                    f"unknown key '{key}' in {self.path}, "
                    "expected one of {', '.join(ok_top)}, or *-cells"
                )

        if "bus" in raw:
            bus = raw["bus"]
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

        if "on-bus" in raw and not isinstance(raw["on-bus"], str):
            _err(f"malformed 'on-bus:' value in {self.path}, " "expected string")

        self._check_properties()

        for key, val in raw.items():
            if key.endswith("-cells"):
                if not isinstance(val, list) or not all(
                    isinstance(elem, str) for elem in val
                ):
                    _err(
                        f"malformed '{key}:' in {self.path}, "
                        "expected a list of strings"
                    )

    def _check_properties(self) -> None:
        # _check() helper for checking the contents of 'properties:'.

        raw = self.raw

        if "properties" not in raw:
            return

        ok_prop_keys = {
            "description",
            "type",
            "required",
            "enum",
            "const",
            "default",
            "deprecated",
            "specifier-space",
        }

        for prop_name, options in raw["properties"].items():
            for key in options:
                if key not in ok_prop_keys:
                    _err(
                        f"unknown setting '{key}' in "
                        f"'properties: {prop_name}: ...' in {self.path}, "
                        f"expected one of {', '.join(ok_prop_keys)}"
                    )

            _check_prop_by_type(prop_name, options, self.path)

            for true_false_opt in ["required", "deprecated"]:
                if true_false_opt in options:
                    option = options[true_false_opt]
                    if not isinstance(option, bool):
                        _err(
                            f"malformed '{true_false_opt}:' setting '{option}' "
                            f"for '{prop_name}' in 'properties' in {self.path}, "
                            "expected true/false"
                        )

            if options.get("deprecated") and options.get("required"):
                _err(
                    f"'{prop_name}' in 'properties' in {self.path} should not "
                    "have both 'deprecated' and 'required' set"
                )

            if "description" in options and not isinstance(options["description"], str):
                _err(
                    "missing, malformed, or empty 'description' for "
                    f"'{prop_name}' in 'properties' in {self.path}"
                )

            if "enum" in options and not isinstance(options["enum"], list):
                _err(f"enum in {self.path} for property '{prop_name}' " "is not a list")


class PropertySpec:
    """
    Represents a "property specification", i.e. the description of a
    property provided by a binding file, like its type and description.

    These attributes are available on PropertySpec objects:

    binding:
      The Binding object which defined this property.

    name:
      The property's name.

    path:
      The file where this property was defined. In case a binding includes
      other bindings, this is the file where the property was last modified.

    type:
      The type of the property as a string, as given in the binding.

    description:
      The free-form description of the property as a string, or None.

    enum:
      A list of values the property may take as given in the binding, or None.

    enum_tokenizable:
      True if enum is not None and all the values in it are tokenizable;
      False otherwise.

      A property must have string or string-array type and an "enum:" in its
      binding to be tokenizable. Additionally, the "enum:" values must be
      unique after converting all non-alphanumeric characters to underscores
      (so "foo bar" and "foo_bar" in the same "enum:" would not be
      tokenizable).

    enum_upper_tokenizable:
      Like 'enum_tokenizable', with the additional restriction that the
      "enum:" values must be unique after uppercasing and converting
      non-alphanumeric characters to underscores.

    const:
      The property's constant value as given in the binding, or None.

    default:
      The property's default value as given in the binding, or None.

    deprecated:
      True if the property is deprecated; False otherwise.

    required:
      True if the property is marked required; False otherwise.

    specifier_space:
      The specifier space for the property as given in the binding, or None.
    """

    def __init__(self, name: str, binding: Binding):
        self.binding: Binding = binding
        self.name: str = name
        self._raw: Dict[str, Any] = self.binding.raw["properties"][name]

    def __repr__(self) -> str:
        return f"<PropertySpec {self.name} type '{self.type}'>"

    @property
    def path(self) -> Optional[str]:
        "See the class docstring"
        return self.binding.path

    @property
    def type(self) -> str:
        "See the class docstring"
        return self._raw["type"]

    @property
    def description(self) -> Optional[str]:
        "See the class docstring"
        return self._raw.get("description")

    @property
    def enum(self) -> Optional[list]:
        "See the class docstring"
        return self._raw.get("enum")

    @property
    def enum_tokenizable(self) -> bool:
        "See the class docstring"
        if not hasattr(self, "_enum_tokenizable"):
            if self.type not in {"string", "string-array"} or self.enum is None:
                self._enum_tokenizable = False
            else:
                # Saving _as_tokens here lets us reuse it in
                # enum_upper_tokenizable.
                self._as_tokens = [
                    re.sub(_NOT_ALPHANUM_OR_UNDERSCORE, "_", value)
                    for value in self.enum
                ]
                self._enum_tokenizable = len(self._as_tokens) == len(
                    set(self._as_tokens)
                )

        return self._enum_tokenizable

    @property
    def enum_upper_tokenizable(self) -> bool:
        "See the class docstring"
        if not hasattr(self, "_enum_upper_tokenizable"):
            if not self.enum_tokenizable:
                self._enum_upper_tokenizable = False
            else:
                self._enum_upper_tokenizable = len(self._as_tokens) == len(
                    set(x.upper() for x in self._as_tokens)
                )
        return self._enum_upper_tokenizable

    @property
    def const(self) -> Union[None, int, List[int], str, List[str]]:
        "See the class docstring"
        return self._raw.get("const")

    @property
    def default(self) -> Union[None, int, List[int], str, List[str]]:
        "See the class docstring"
        return self._raw.get("default")

    @property
    def required(self) -> bool:
        "See the class docstring"
        return self._raw.get("required", False)

    @property
    def deprecated(self) -> bool:
        "See the class docstring"
        return self._raw.get("deprecated", False)

    @property
    def specifier_space(self) -> Optional[str]:
        "See the class docstring"
        return self._raw.get("specifier-space")


V = TypeVar("V")
N = TypeVar("N")


@dataclass
class Property(Generic[V, N]):
    """
    Represents a property on a Node, as set in its ST node and with
    additional info from the 'properties:' section of the binding.

    Only properties mentioned in 'properties:' get created. Properties of type
    'compound' currently do not get Property instances, as it's not clear
    what to generate for them.

    These attributes are available on Property objects. Several are
    just convenience accessors for attributes on the PropertySpec object
    accessible via the 'spec' attribute:

    spec:
      The PropertySpec object which specifies this property.

    val:
      The value of the property, with the format determined by spec.type, which
      comes from the 'type:' string in the binding. See subclasses for specific
      conversion rules.

    node:
      The Node instance the property is on

    name:
      Convenience for spec.name.

    description:
      Convenience for spec.description with leading and trailing whitespace
      (including newlines) removed. May be None.

    type:
      Convenience for spec.type.

    val_as_tokens:
      The value of the property as a list of tokens, i.e. with non-alphanumeric
      characters replaced with underscores. This is only safe to access
      if 'spec.enum_tokenizable' returns True.

    enum_indices:
      A list of indices of 'val' in 'spec.enum' (which comes from the 'enum:'
      list in the binding), or None if spec.enum is None.
    """

    spec: PropertySpec
    val: V
    node: N

    @property
    def name(self) -> str:
        "See the class docstring"
        return self.spec.name

    @property
    def description(self) -> Optional[str]:
        "See the class docstring"
        return self.spec.description.strip() if self.spec.description else None

    @property
    def type(self) -> str:
        "See the class docstring"
        return self.spec.type

    @property
    def val_as_tokens(self) -> List[str]:
        "See the class docstring"
        ret = []
        for subval in self.val if isinstance(self.val, list) else [self.val]:
            assert isinstance(subval, str)
            ret.append(str_as_token(subval))
        return ret

    @property
    def enum_indices(self) -> Optional[List[int]]:
        "See the class docstring"
        enum = self.spec.enum
        val = self.val if isinstance(self.val, list) else [self.val]
        return [enum.index(subval) for subval in val] if enum else None


class BindingError(Exception):
    "Exception raised for binding-related errors"


#
# Public global functions
#


def bindings_from_paths(
    yaml_paths: List[str], ignore_errors: bool = False
) -> List[Binding]:
    """
    Get a list of Binding objects from the yaml files 'yaml_paths'.

    If 'ignore_errors' is True, YAML files that cause an BindingError when
    loaded are ignored. (No other exception types are silenced.)
    """

    ret = []
    fname2path = {os.path.basename(path): path for path in yaml_paths}
    for path in yaml_paths:
        try:
            ret.append(Binding(path, fname2path))
        except BindingError:
            if ignore_errors:
                continue
            raise

    return ret


#
# Private global functions
#

def _check_prop_by_type(
    prop_name: str, options: dict, binding_path: Optional[str]
) -> None:
    # Binding._check_properties() helper. Checks 'type:', 'default:',
    # 'const:' and # 'specifier-space:' for the property named 'prop_name'

    prop_type = options.get("type")
    default = options.get("default")
    const = options.get("const")

    if prop_type is None:
        _err(f"missing 'type:' for '{prop_name}' in 'properties' in " f"{binding_path}")

    ok_types = {
        "boolean",
        "int",
        "array",
        "uint8-array",
        "string",
        "string-array",
        "phandle",
        "phandles",
        "phandle-array",
        "path",
        "compound",
    }

    if prop_type not in ok_types:
        _err(
            f"'{prop_name}' in 'properties:' in {binding_path} "
            f"has unknown type '{prop_type}', expected one of " + ", ".join(ok_types)
        )

    if "specifier-space" in options and prop_type != "phandle-array":
        _err(
            f"'specifier-space' in 'properties: {prop_name}' "
            f"has type '{prop_type}', expected 'phandle-array'"
        )

    if prop_type == "phandle-array":
        if not prop_name.endswith("s") and not "specifier-space" in options:
            _err(
                f"'{prop_name}' in 'properties:' in {binding_path} "
                f"has type 'phandle-array' and its name does not end in 's', "
                f"but no 'specifier-space' was provided."
            )

    # If you change const_types, be sure to update the type annotation
    # for PropertySpec.const.
    const_types = {"int", "array", "uint8-array", "string", "string-array"}
    if const and prop_type not in const_types:
        _err(
            f"const in {binding_path} for property '{prop_name}' "
            f"has type '{prop_type}', expected one of " + ", ".join(const_types)
        )

    # Check default

    if default is None:
        return

    if prop_type in {
        "boolean",
        "compound",
        "phandle",
        "phandles",
        "phandle-array",
        "path",
    }:
        _err(
            "'default:' can't be combined with "
            f"'type: {prop_type}' for '{prop_name}' in "
            f"'properties:' in {binding_path}"
        )

    def ok_default() -> bool:
        # Returns True if 'default' is an okay default for the property's type.
        # If you change this, be sure to update the type annotation for
        # PropertySpec.default.

        if (
            prop_type == "int"
            and isinstance(default, int)
            or prop_type == "string"
            and isinstance(default, str)
        ):
            return True

        # array, uint8-array, or string-array

        if not isinstance(default, list):
            return False

        if prop_type == "array" and all(isinstance(val, int) for val in default):
            return True

        if prop_type == "uint8-array" and all(
            isinstance(val, int) and 0 <= val <= 255 for val in default
        ):
            return True

        # string-array
        return all(isinstance(val, str) for val in default)

    if not ok_default():
        _err(
            f"'default: {default}' is invalid for '{prop_name}' "
            f"in 'properties:' in {binding_path}, "
            f"which has type {prop_type}"
        )


def _check_include_dict(
    name: Optional[str],
    allowlist: Optional[List[str]],
    blocklist: Optional[List[str]],
    child_filter: Optional[dict],
    binding_path: Optional[str],
) -> None:
    # Check that an 'include:' named 'name' with property-allowlist
    # 'allowlist', property-blocklist 'blocklist', and
    # child-binding filter 'child_filter' has valid structure.

    if name is None:
        _err(f"'include:' element in {binding_path} " "should have a 'name' key")

    if allowlist is not None and blocklist is not None:
        _err(
            f"'include:' of file '{name}' in {binding_path} "
            "should not specify both 'property-allowlist:' "
            "and 'property-blocklist:'"
        )

    while child_filter is not None:
        child_copy = deepcopy(child_filter)
        child_allowlist: Optional[List[str]] = child_copy.pop(
            "property-allowlist", None
        )
        child_blocklist: Optional[List[str]] = child_copy.pop(
            "property-blocklist", None
        )
        next_child_filter: Optional[dict] = child_copy.pop("child-binding", None)

        if child_copy:
            # We've popped out all the valid keys.
            _err(
                f"'include:' of file '{name}' in {binding_path} "
                "should not have these unexpected contents in a "
                f"'child-binding': {child_copy}"
            )

        if child_allowlist is not None and child_blocklist is not None:
            _err(
                f"'include:' of file '{name}' in {binding_path} "
                "should not specify both 'property-allowlist:' and "
                "'property-blocklist:' in a 'child-binding:'"
            )

        child_filter = next_child_filter


def _filter_properties(
    raw: dict,
    allowlist: Optional[List[str]],
    blocklist: Optional[List[str]],
    child_filter: Optional[dict],
    binding_path: Optional[str],
) -> None:
    # Destructively modifies 'raw["properties"]' and
    # 'raw["child-binding"]', if they exist, according to
    # 'allowlist', 'blocklist', and 'child_filter'.

    props = raw.get("properties")
    _filter_properties_helper(props, allowlist, blocklist, binding_path)

    child_binding = raw.get("child-binding")
    while child_filter is not None and child_binding is not None:
        _filter_properties_helper(
            child_binding.get("properties"),
            child_filter.get("property-allowlist"),
            child_filter.get("property-blocklist"),
            binding_path,
        )
        child_filter = child_filter.get("child-binding")
        child_binding = child_binding.get("child-binding")


def _filter_properties_helper(
    props: Optional[dict],
    allowlist: Optional[List[str]],
    blocklist: Optional[List[str]],
    binding_path: Optional[str],
) -> None:
    if props is None or (allowlist is None and blocklist is None):
        return

    _check_prop_filter("property-allowlist", allowlist, binding_path)
    _check_prop_filter("property-blocklist", blocklist, binding_path)

    if allowlist is not None:
        allowset = set(allowlist)
        to_del = [prop for prop in props if prop not in allowset]
    else:
        if TYPE_CHECKING:
            assert blocklist
        blockset = set(blocklist)
        to_del = [prop for prop in props if prop in blockset]

    for prop in to_del:
        del props[prop]


def _check_prop_filter(
    name: str, value: Optional[List[str]], binding_path: Optional[str]
) -> None:
    # Ensure an include: ... property-allowlist or property-blocklist
    # is a list.

    if value is None:
        return

    if not isinstance(value, list):
        _err(f"'{name}' value {value} in {binding_path} should be a list")


def _merge_props(
    to_dict: dict,
    from_dict: dict,
    parent: Optional[str],
    binding_path: Optional[str],
    check_required: bool = False,
):
    # Recursively merges 'from_dict' into 'to_dict', to implement 'include:'.
    #
    # If 'from_dict' and 'to_dict' contain a 'required:' key for the same
    # property, then the values are ORed together.
    #
    # If 'check_required' is True, then an error is raised if 'from_dict' has
    # 'required: true' while 'to_dict' has 'required: false'. This prevents
    # bindings from "downgrading" requirements from bindings they include,
    # which might help keep bindings well-organized.
    #
    # It's an error for most other keys to appear in both 'from_dict' and
    # 'to_dict'. When it's not an error, the value in 'to_dict' takes
    # precedence.
    #
    # 'parent' is the name of the parent key containing 'to_dict' and
    # 'from_dict', and 'binding_path' is the path to the top-level binding.
    # These are used to generate errors for sketchy property overwrites.

    for prop in from_dict:
        if isinstance(to_dict.get(prop), dict) and isinstance(from_dict[prop], dict):
            _merge_props(
                to_dict[prop], from_dict[prop], prop, binding_path, check_required
            )
        elif prop not in to_dict:
            to_dict[prop] = from_dict[prop]
        elif _bad_overwrite(to_dict, from_dict, prop, check_required):
            _err(
                f"{binding_path} (in '{parent}'): '{prop}' "
                f"from included file overwritten ('{from_dict[prop]}' "
                f"replaced with '{to_dict[prop]}')"
            )
        elif prop == "required":
            # Need a separate check here, because this code runs before
            # Binding._check()
            if not (
                isinstance(from_dict["required"], bool)
                and isinstance(to_dict["required"], bool)
            ):
                _err(
                    f"malformed 'required:' setting for '{parent}' in "
                    f"'properties' in {binding_path}, expected true/false"
                )

            # 'required: true' takes precedence
            to_dict["required"] = to_dict["required"] or from_dict["required"]


def _bad_overwrite(
    to_dict: dict, from_dict: dict, prop: str, check_required: bool
) -> bool:
    # _merge_props() helper. Returns True in cases where it's bad that
    # to_dict[prop] takes precedence over from_dict[prop].

    if to_dict[prop] == from_dict[prop]:
        return False

    # These are overridden deliberately
    if prop in {"title", "description", "compatible"}:
        return False

    if prop == "required":
        if not check_required:
            return False
        return from_dict[prop] and not to_dict[prop]

    return True


def _binding_inc_error(msg):
    # Helper for reporting errors in the !include implementation

    raise yaml.constructor.ConstructorError(None, None, "error: " + msg)


def _binding_include(loader, node):
    # Implements !include, for backwards compatibility. '!include [foo, bar]'
    # just becomes [foo, bar].

    if isinstance(node, yaml.ScalarNode):
        # !include foo.yaml
        return [loader.construct_scalar(node)]

    if isinstance(node, yaml.SequenceNode):
        # !include [foo.yaml, bar.yaml]
        return loader.construct_sequence(node)

    _binding_inc_error("unrecognised node type in !include statement")


# Regular expression for non-alphanumeric-or-underscore characters.
_NOT_ALPHANUM_OR_UNDERSCORE = re.compile(r"\W", re.ASCII)


def str_as_token(val: str) -> str:
    """Return a canonical representation of a string as a C token.

    This converts special characters in 'val' to underscores, and
    returns the result."""

    return re.sub(_NOT_ALPHANUM_OR_UNDERSCORE, "_", val)


def _err(msg) -> NoReturn:
    raise BindingError(msg)


# Custom PyYAML binding loader class to avoid modifying yaml.Loader directly,
# which could interfere with YAML loading in clients
class _BindingLoader(Loader):
    pass


# Add legacy '!include foo.yaml' handling
_BindingLoader.add_constructor("!include", _binding_include)
