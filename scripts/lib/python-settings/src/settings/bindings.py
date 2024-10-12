# Copyright (c) 2024 The Zephyr Project
# SPDX-License-Identifier: Apache-2.0

"""
Bindings are YAML files that describe setting nodes and their properties. Nodes
are mapped to bindings via their 'schema = "..."' property. A binding may
impose restrictions on the bound node as well as on its child nodes via
child bindings (ie. node properties).

Note: While it is still possible to designate the schema of a binding file with
the "compatible" property, this is considered deprecated usage. From a semantic
perspective it's fine if a DT node is "compatible" to a schema, but a binding is
not "compatible" to anything. So previous usage was a misnomer that may have
been tolerable for devictree bindings but not for configuration schemas.

Settings nodes will be validated against bindings and property types (string,
array, integer, bool, ...). Allowable types and value ranges for properties will
be derived from the corresponding property definitions in the binding.
"""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Collection
import os
import re
import yaml

from copy import deepcopy
from typing import (
    Any,
    Generic,
    NoReturn,
    Optional,
    TypeVar,
    Union,
    TYPE_CHECKING,
)

from settings.error import STBindingError
from settings.yamlutil import _IncludeLoader

#
# Private constants
#

# Regular expression for non-alphanumeric-or-underscore characters.
_NOT_ALPHANUM_OR_UNDERSCORE = re.compile(r"\W", re.ASCII)

#
# Public classes
#


class _STPropertySpec:
    """
    Represents a "property specification", i.e. the description of a
    property provided by a binding file, like its type and description.

    These init attributes are available on PropertySpec objects:

    name:
      The property's name.

    binding:
      The Binding object which defined this property.

    path:
        The file where this property was defined. In case a binding includes
        other bindings, this is the file where the property was last modified.

    Also see property docstrings.
    """

    def __init__(
        self,
        name: str,
        binding: _STBinding,
        path: str,
        prop_yaml_source: dict[str, Any],
    ):
        """
        _STPropertySpec constructor.

        Constructor arguments:

        name:
          The property's name.

        binding:
          The Binding object which defined this property.

        path:
          The file where this property was defined. In case a binding includes
          other bindings, this is the file where the property was last modified.

        prop_yaml_source:
          The YAML snippet that defines the property. This is the mapping
          from the binding that is assigned to the property name.

        Also see property docstrings.
        """
        self.name: str = name
        self.binding: _STBinding = binding
        self.path: str = path
        self._yaml_source: dict[str, Any] = prop_yaml_source

    def __repr__(self) -> str:
        return f"<PropertySpec {self.name} type '{self.type}' in '{self.path}'>"

    @property
    def type(self) -> str:
        """
        The type of the property as a string, as given in the binding.
        """
        return self._yaml_source["type"]

    @property
    def description(self) -> Optional[str]:
        """
        The free-form description of the property as a string, or None.
        """
        return self._yaml_source.get("description")

    @property
    def enum(self) -> Optional[list]:
        """
        A list of values the property may take as given in the binding, or None.
        """
        return self._yaml_source.get("enum")

    @property
    def enum_tokenizable(self) -> bool:
        """
        True if enum is not None and all the values in it are tokenizable;
        False otherwise.

        A property must have string or string-array type and an "enum:" in its
        binding to be tokenizable. Additionally, the "enum:" values must be
        unique after converting all non-alphanumeric characters to underscores
        (so "foo bar" and "foo_bar" in the same "enum:" would not be
        tokenizable).
        """
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
        """
        Like 'enum_tokenizable', with the additional restriction that the
        "enum:" values must be unique after uppercasing and converting
        non-alphanumeric characters to underscores.
        """
        if not hasattr(self, "_enum_upper_tokenizable"):
            if not self.enum_tokenizable:
                self._enum_upper_tokenizable = False
            else:
                self._enum_upper_tokenizable = len(self._as_tokens) == len(
                    set(x.upper() for x in self._as_tokens)
                )
        return self._enum_upper_tokenizable

    @property
    def const(self) -> Union[None, int, list[int], str, list[str]]:
        """
        The property's constant value as given in the binding, or None.
        """
        return self._yaml_source.get("const")

    @property
    def default(
        self,
    ) -> Union[None, int, list[int], str, list[str], float, list[float]]:
        """
        The property's default value as given in the binding, or None.
        """
        return self._yaml_source.get("default")

    @property
    def required(self) -> bool:
        """
        True if the property is marked required; False otherwise.
        """
        return self._yaml_source.get("required", False)

    @property
    def deprecated(self) -> bool:
        """
        True if the property is deprecated; False otherwise.
        """
        return self._yaml_source.get("deprecated", False)


S = TypeVar("S", bound="_STPropertySpec")
B = TypeVar("B", bound="_STBinding")


class _STBinding(Generic[S, B]):
    """
    Represents a parsed binding.

    These init attributes are available on Binding objects:

    path:
      The absolute path to the file defining the binding.

    prop2specs:
      A dict mapping property names to a tuple of a compiled regular expression
      for the property name pattern and a PropertySpec instance describing the
      properties' values.

    prop2bindings:
      A dict mapping that maps the child node name pattern to a tuple of a
      compiled regular expression for the child node name pattern and all
      binding objects for those children. The deprecated "child-binding" node
      will be represented with a ".*" key.

    yaml_source:
      The binding as a merged and filtered object parsed from YAML.

    Also see property docstrings.
    """

    CHILD_BINDING_REGEX = re.compile(".*")

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

        path:
          Path to binding YAML file. May be None.

        yaml_source:
          Optional raw yaml source for the binding. This can be used to resolve
          child bindings, for example. If not given, 'path' is mandatory and
          will be opened and read.

          The source may contain unresolved "include:" lines.

          Note: The 'yaml_source' attribute will be destructively modified by
          the constructor when resolving included files. Do not modify it after
          construction.

        require_schema:
          If True, it is an error if the binding does not contain a "schema:"
          line. If False, a missing "schema:" is not an error. Either way,
          "schema:" must be a string if it is present in the binding.

        require_description:
          If True, it is an error if the binding does not contain a
          "description:" line. If False, a missing "description:" is
          not an error. Either way, "description:" must be a string
          if it is present in the binding.

        allowlist:
          The property-allowlist filter set by including bindings.

        blocklist:
          The property-blocklist filter set by including bindings.

        fname2path:
          Map from include files to their absolute paths. Must
          not be None, but may be empty.

        is_child_binding:
          True if this binding is a child binding (i.e. comes from a property
          with type "node"), False otherwise.
        """
        self.path: str = path

        self._fname2path: dict[str, str] = fname2path
        self._is_child_binding = is_child_binding

        if path is None:
            _err("you must provide a 'path'")

        if yaml_source is None:
            with open(path, encoding="utf-8") as f:
                yaml_source = yaml.load(f, Loader=_IncludeLoader)

        self._normalize_child_binding(yaml_source)

        self.prop2specs: OrderedDict[str, tuple[re.Pattern, S]] = OrderedDict()
        self.prop2bindings: OrderedDict[str, tuple[re.Pattern, list[B]]] = OrderedDict()

        self._merge_includes(yaml_source, self.path, allowlist, blocklist)

        self.yaml_source: OrderedDict[str, Any] = yaml_source

        top_level_props = {
            self.schema_prop_name,
            "description",
            "properties",
        }

        if self._is_child_binding:
            top_level_props.add("type")

        legacy_errors = {
            "sub-node": "use a property with 'type: node' instead",
            "title": "use 'description' instead",
        }

        # Make sure this is a well defined object.
        self._check(require_schema, require_description, top_level_props, legacy_errors)

    def __repr__(self) -> str:
        schema_name = f" for schema '{self.schema}'" if self.schema else ""
        variant_name = f"  on '{self.variant}'" if self.variant else ""
        basename = os.path.basename(self.path or "")
        return f"<Binding {basename}" + schema_name + variant_name + ">"

    def __hash__(self):
        return hash((self.schema, self.variant))

    def __eq__(self, other):
        if not isinstance(other, _STBinding):
            return False
        return (self.schema, self.variant) == (other.schema, other.variant)

    @property
    def description(self) -> Optional[str]:
        "The free-form description of the binding, or None."
        return self.yaml_source.get("description")

    @property
    def schema_prop_name(self) -> str:
        return "schema"

    @property
    def schema(self) -> Optional[str]:
        """
        The name of the schema that the binding defines and validates.

        This may be None. For example, it's None when the Binding is inferred from
        node properties. It can also be None for Binding objects created from an
        anonymous 'child-binding:' belonging to a named parent binding.
        """
        return self.yaml_source.get(self.schema_prop_name)

    @property
    def variant(self) -> Optional[str]:
        """
        If nodes with this binding's 'schema' appear on a bus, a string describing
        the bus type (like "i2c"). None otherwise.
        """
        return None

    @classmethod
    def _instantiate(
        cls: type[B],
        path: str,
        yaml_source: Optional[OrderedDict[str, Any]] = None,
        require_schema: bool = True,
        require_description: bool = True,
        allowlist: Optional[list[str]] = None,
        blocklist: Optional[list[str]] = None,
        fname2path: dict[str, str] = {},
        is_child_binding: bool = False,
    ) -> B:
        # Instantiates the correct binding parent class. See the __init__()
        # docstring for parameter descriptions.
        return cls(
            path=path,
            yaml_source=yaml_source,
            require_schema=require_schema,
            require_description=require_description,
            allowlist=allowlist,
            blocklist=blocklist,
            fname2path=fname2path,
            is_child_binding=is_child_binding,
        )

    def _merge_includes(
        self,
        yaml_source: OrderedDict[str, Any],
        path: str,
        allowlist: Optional[list[str]],
        blocklist: Optional[list[str]],
    ) -> None:
        # Destructively merges included files in 'yaml_source["include"]' into
        # 'yaml_source' using 'self._include_paths' as a source of include
        # files, removing the "include" key while doing so.
        #
        # This treats 'binding_path' as the binding file being built up and uses
        # it for error messages.
        #
        # Properties specifications being defined in yaml_source and its included
        # files are registered in prop2specs.
        #
        # Merging advances depth first, this means that property specifications
        # defined by includes are overwritten by the properties defined in the
        # binding file itself.

        # We need to instantiate prop specs after we filtered the 'yaml_source'
        # but before we resolve includes. This is ensures that we don't override
        # prop specs from included files with the path from the current file.
        prop_specs = self._instantiate_prop_specs(yaml_source.get("properties"), path)
        child_bindings = self._instantiate_child_bindings(
            yaml_source.get("properties"), allowlist, blocklist, path
        )

        if "include" in yaml_source:
            merged_yaml: OrderedDict[str, Any] = OrderedDict()
            includes = yaml_source.pop("include")
            if not isinstance(includes, (str, list)):
                # Invalid item.
                _err(
                    f"'include:' in {path} "
                    f"should be a string or list, but has type {type(includes)}"
                )

            # First, merge the included yaml files together.

            # List of strings and maps. These types may be intermixed.
            includes = [includes] if isinstance(includes, str) else includes
            for elem in includes:
                if not isinstance(elem, (str, dict)):
                    _err(
                        f"all elements in 'include:' in {path} "
                        "should be either strings or maps with a 'name' key "
                        "and optional 'property-allowlist' or "
                        f"'property-blocklist' keys, but got: {elem}"
                    )

                if isinstance(elem, str):
                    # Load YAML file and register property specs into prop2specs.
                    include_fname = elem
                    merged_allowlist = allowlist
                    merged_blocklist = blocklist
                    child_binding_filter = None
                else:
                    include_fname = elem.pop("name", None)

                    def merge_list(
                        key: str, previous_list: Optional[list[str]]
                    ) -> Optional[list[str]]:
                        additional_list: Optional[list[str]] = elem.pop(key, None)
                        if additional_list is not None and not isinstance(
                            additional_list, list
                        ):
                            _err(f"'{key}' value in {path} should be a list")
                        return (
                            previous_list
                            if additional_list is None
                            else additional_list + (previous_list or [])
                        )

                    merged_allowlist = merge_list("property-allowlist", allowlist)
                    merged_blocklist = merge_list("property-blocklist", blocklist)

                    child_binding_filter = elem.pop("child-binding", None)

                    if elem:
                        # We've popped out all the valid keys.
                        _err(
                            f"'include:' in {path} should not have "
                            f"these unexpected contents: {elem}"
                        )

                    _STBinding._check_include_dict(
                        include_fname,
                        merged_allowlist,
                        merged_blocklist,
                        child_binding_filter,
                        path,
                    )

                # Load YAML file, and register (filtered) property specs
                # into prop2specs.
                include_path = self._fname2path.get(include_fname)
                if not include_path:
                    _err(f"'{include_path}' not found")

                filtered_include_yaml_source = self._load_and_filter_include(
                    include_path,
                    merged_allowlist,
                    merged_blocklist,
                    child_binding_filter,
                )

                _STBinding._merge_yaml(
                    path,
                    merged_yaml,
                    filtered_include_yaml_source,
                    check_required=False,
                )

            _STBinding._merge_yaml(path, yaml_source, merged_yaml, check_required=True)

        # Override included prop specs and bindings with the ones defined in the
        # current binding file.
        #
        # Note: self._load_and_filter_include() recurses depth first into this
        # function, so this code will be executed for included files before
        # their parents.  This ensures that property specs from files further
        # down in the include hierarchy will be overridden by their parents'
        # property specs.
        self.prop2specs.update(prop_specs)
        for prop_name, child_binding in child_bindings.items():
            if prop_name in self.prop2bindings:
                self.prop2bindings[prop_name][1].append(child_binding[1])
            else:
                self.prop2bindings[prop_name] = (child_binding[0], [child_binding[1]])

    def _load_and_filter_include(
        self,
        path: str,
        allowlist: Optional[list[str]],
        blocklist: Optional[list[str]],
        child_binding_filter: Optional[dict],
    ) -> OrderedDict[str, Any]:
        # Returns the filtered contents of the binding given by 'fname' after
        # recursively merging and filtering any bindings it lists in 'include:'
        # into it, according to the given property filters.
        #
        # Will also register the (filtered) included property specs
        # into prop2specs.

        with open(path, encoding="utf-8") as f:
            yaml_source: OrderedDict[str, Any] = yaml.load(f, Loader=_IncludeLoader)
            if not isinstance(yaml_source, dict):
                _err(f"{path}: invalid contents, expected a mapping")

        self._normalize_child_binding(yaml_source)

        # Apply constraints to included YAML contents.
        properties = yaml_source.get("properties")
        if TYPE_CHECKING:
            assert isinstance(properties, OrderedDict)
        _STBinding._filter_properties(
            self.path,
            properties,
            allowlist,
            blocklist,
            child_binding_filter,
        )

        self._merge_includes(yaml_source, path, allowlist, blocklist)

        return yaml_source

    def _normalize_child_binding(self, yaml_source: dict) -> None:
        # Recursively normalize legacy child-binding properties. This must be
        # called right after loading the source, before the child bindings are
        # being filtered.
        if "child-binding" in yaml_source:
            if not isinstance(yaml_source["child-binding"], dict):
                _err(
                    f"malformed 'child-binding:' in {self.path}, "
                    "expected a binding (dictionary with keys/values)"
                )
            child_binding_yaml = yaml_source.pop("child-binding")
            self._normalize_child_binding(child_binding_yaml)
            child_binding_yaml["type"] = "node"
            if "properties" not in yaml_source:
                yaml_source["properties"] = {}
            yaml_source["properties"][".*"] = child_binding_yaml

    @staticmethod
    def _check_include_dict(
        name: Optional[str],
        allowlist: Optional[list[str]],
        blocklist: Optional[list[str]],
        child_binding_filter: Optional[dict],
        binding_path: Optional[str],
    ) -> None:
        # Check that an 'include:' named 'name' with property-allowlist
        # 'allowlist', property-blocklist 'blocklist', and child-binding filter
        # 'child_binding_filter' has valid structure.

        if name is None:
            _err(f"'include:' element in {binding_path} " "should have a 'name' key")

        if allowlist is not None and blocklist is not None:
            _err(
                f"'include:' of file '{name}' in {binding_path} "
                "should not specify both 'property-allowlist:' "
                "and 'property-blocklist:'"
            )

        child_binding_filter = deepcopy(child_binding_filter)
        while child_binding_filter is not None:
            child_binding_allowlist: Optional[list[str]] = child_binding_filter.pop(
                "property-allowlist", None
            )
            child_binding_blocklist: Optional[list[str]] = child_binding_filter.pop(
                "property-blocklist", None
            )
            next_child_binding: Optional[dict] = child_binding_filter.pop(
                "child-binding", None
            )

            if child_binding_filter:
                # We've popped out all the valid keys.
                _err(
                    f"'include:' of file '{name}' in {binding_path} "
                    "should not have these unexpected contents in a "
                    f"'child-binding': {child_binding_filter}"
                )

            if (
                child_binding_allowlist is not None
                and child_binding_blocklist is not None
            ):
                _err(
                    f"'include:' of file '{name}' in {binding_path} "
                    "should not specify both 'property-allowlist:' and "
                    "'property-blocklist:' in a 'child-binding:'"
                )

            child_binding_filter = next_child_binding

    @classmethod
    def _filter_properties(
        cls,
        path: str,
        props_yaml: Optional[OrderedDict[str, Any]],
        allowlist: Optional[list[str]],
        blocklist: Optional[list[str]],
        child_binding_filter: Optional[dict],
    ) -> None:
        # Destructively modifies 'yaml_source["properties"]' if they exist,
        # according to 'allowlist', 'blocklist', and 'child_binding_filter'.

        _STBinding._check_prop_filter("property-allowlist", allowlist, path)
        _STBinding._check_prop_filter("property-blocklist", blocklist, path)

        if not props_yaml:
            return

        # filter props
        props_to_add = {}
        props_to_delete = []

        if allowlist is not None:
            for name, prop_spec in props_yaml.items():
                if prop_spec["type"] == "node":
                    continue

                # first check for exact matches
                if name in allowlist:
                    continue

                props_to_delete.append(name)

                # now check for regular expression matches
                for allow_key in allowlist or []:
                    if re.match(name, allow_key) and allow_key not in props_yaml:
                        # replace the matching regular expression by an exact match
                        props_to_add[allow_key] = prop_spec

        elif blocklist is not None:
            for name, prop_spec in props_yaml.items():
                if prop_spec["type"] == "node":
                    continue

                if name in blocklist:
                    props_to_delete.append(name)
                    continue

                # now check for regular expression matches
                restricted_name = name
                for block_key in blocklist or []:
                    if re.match(name, block_key):
                        # replace the matching regular expression by a restricted
                        # one that matches to all but the blocked key
                        props_to_delete.append(name)
                        restricted_name = f"(?!^{block_key}$){restricted_name}"

                if restricted_name not in props_yaml:
                    props_to_add[restricted_name] = prop_spec

        for prop_name in props_to_delete:
            del props_yaml[prop_name]
        props_yaml.update(props_to_add)

        if not child_binding_filter:
            return

        # filter child bindings
        for prop_yaml in props_yaml.values():
            is_child_binding = prop_yaml.get("type") == "node"
            if not is_child_binding:
                continue

            _STBinding._filter_properties(
                path,
                prop_yaml.get("properties"),
                child_binding_filter.get("property-allowlist"),
                child_binding_filter.get("property-blocklist"),
                child_binding_filter.get("child-binding"),
            )

    @classmethod
    def _check_prop_filter(
        cls, filter_name: str, filter_list: Optional[list[str]], path: Optional[str]
    ) -> None:
        # Ensure an include: ... property-allowlist or property-blocklist
        # is a list.

        if not filter_list:
            return

        if not isinstance(filter_list, list):
            _err(f"'{filter_name}' value {filter_list} in {path} should be a list")

    @classmethod
    def _merge_yaml(
        cls,
        path: str,
        to_yaml: OrderedDict[str, Any],
        from_yaml: OrderedDict[str, Any],
        check_required: bool = False,
        parent_key: Optional[str] = None,
    ):
        # Recursively merges 'from_yaml' into 'to_yaml', to implement
        # 'include:'.
        #
        # If 'from_yaml' and 'to_yaml' contain a 'required:' key for the same
        # property, then the values are ORed together.
        #
        # If 'check_required' is True, then an error is raised if 'from_yaml'
        # has 'required: true' while 'to_yaml' has 'required: false'. This
        # prevents bindings from "downgrading" requirements from bindings they
        # include, which might help keep bindings well-organized.
        #
        # It's an error for most other keys to appear in both 'from_yaml' and
        # 'to_yaml'. When it's not an error, the value in 'to_yaml' takes
        # precedence.
        #
        # 'parent_key' is the name of the parent key containing 'to_yaml' and
        # 'from_yaml', and 'path' is the path to the top-level binding. These
        # are used to generate errors for sketchy property overwrites.

        for prop_name in from_yaml:
            if isinstance(to_yaml.get(prop_name), dict) and isinstance(
                from_yaml[prop_name], dict
            ):
                _STBinding._merge_yaml(
                    path,
                    to_yaml[prop_name],
                    from_yaml[prop_name],
                    check_required,
                    prop_name,
                )
            elif prop_name not in to_yaml:
                to_yaml[prop_name] = from_yaml[prop_name]
            elif _STBinding._bad_overwrite(
                prop_name, to_yaml[prop_name], from_yaml[prop_name], check_required
            ):
                _err(
                    f"{path} (in '{parent_key}'): '{prop_name}' "
                    f"from included file overwritten ('{from_yaml[prop_name]}' "
                    f"replaced with '{to_yaml[prop_name]}')"
                )
            elif prop_name == "required":
                # Need a separate check here, because this code runs before
                # Binding._check()
                if not (
                    isinstance(from_yaml["required"], bool)
                    and isinstance(to_yaml["required"], bool)
                ):
                    _err(
                        f"malformed 'required:' setting for '{parent_key}' in "
                        f"'properties' in {path}, expected true/false"
                    )

                # If more than one included file has a 'required:' for a
                # particular property, OR the values together, so that
                # 'required: true' wins.
                to_yaml["required"] = to_yaml["required"] or from_yaml["required"]

    @classmethod
    def _bad_overwrite(
        cls, prop: str, to_prop: Any, from_prop: Any, check_required: bool
    ) -> bool:
        # _STBinding._merge_props() helper. Returns True in cases where it's bad
        # that to_prop takes precedence over from_prop

        if to_prop == from_prop:
            return False

        # These are overridden deliberately
        if prop in cls._overridable_props():
            return False

        if prop == "required":
            if not check_required:
                return False
            return from_prop and not to_prop

        return True

    @classmethod
    def _overridable_props(cls) -> set[str]:
        return {"title", "description", "schema"}

    def _instantiate_prop_specs(
        self, props_yaml_source: Optional[OrderedDict[str, Any]], path: str
    ) -> OrderedDict[str, tuple[re.Pattern, S]]:
        props = [] if props_yaml_source is None else props_yaml_source.items()
        return OrderedDict(
            [
                (
                    prop_name,
                    (
                        re.compile(prop_name),
                        self._instantiate_prop_spec(prop_name, prop_yaml_source, path),
                    ),
                )
                for prop_name, prop_yaml_source in props
                if prop_yaml_source.get("type") != "node"
            ]
        )

    def _instantiate_child_bindings(
        self,
        props_yaml_source: Optional[OrderedDict[str, Any]],
        allowlist: Optional[list[str]],
        blocklist: Optional[list[str]],
        path: str,
    ) -> OrderedDict[str, tuple[re.Pattern, B]]:
        child_bindings = {} if props_yaml_source is None else props_yaml_source.items()
        return OrderedDict(
            [
                (
                    prop_name,
                    (
                        re.compile(prop_name),
                        self._instantiate(
                            path=path,
                            yaml_source=prop_yaml_source,
                            require_schema=False,
                            require_description=False,
                            allowlist=allowlist,
                            blocklist=blocklist,
                            fname2path=self._fname2path,
                            is_child_binding=True,
                        ),
                    ),
                )
                for prop_name, prop_yaml_source in child_bindings
                if prop_yaml_source.get("type") == "node"
            ]
        )

    def _instantiate_prop_spec(
        self, prop_name: str, prop_yaml_source: OrderedDict[str, Any], path: str
    ) -> S:
        raise NotImplementedError

    def _check(
        self,
        require_schema: bool,
        require_description: bool,
        top_level_props: set[str],
        legacy_errors: dict[str, str],
    ):
        # Does sanity checking on the binding.

        if self.schema is not None:
            if not isinstance(self.schema, str):
                _err(
                    f"malformed '{self.schema_prop_name}: {self.schema}' "
                    f"field in {self.path} - "
                    f"should be a string, not {type(self.schema).__name__}"
                )
        elif require_schema:
            _err(f"missing 'schema' property in {self.path}")

        yaml_source = self.yaml_source

        if "description" in yaml_source:
            description = yaml_source["description"]
            if not isinstance(description, str) or not description:
                _err(f"malformed or empty 'description' in {self.path}")
        elif require_description:
            _err(f"missing 'description' in {self.path}")

        for key in yaml_source:
            if key in legacy_errors:
                _err(f"legacy '{key}:' in {self.path}, {legacy_errors[key]}")

        ok_prop_keys = {
            "description",
            "type",
            "required",
            "enum",
            "const",
            "default",
            "deprecated",
        }

        # Property types that are defined for the source format.
        # key: the string given in the "type" attribute of a property definition
        # value: A tuple containing...
        #   1. the expected python representation parsed from the YAML file. A
        #      type in list format represents an array of that type.
        #   2. a boolean defining whether this property may have a default
        #      value specified or not.
        ok_prop_types: dict[str, tuple[type | list[type], bool]] = {
            "boolean": (bool, True),
            "int": (int, True),
            "array": ([int], True),
            "uint8-array": (bytes, True),
            "string": (str, True),
            "string-array": ([str], True),
        }

        self._check_properties(top_level_props, ok_prop_keys, ok_prop_types)

    def _check_properties(
        self,
        top_level_props: set[str],
        ok_prop_keys: set[str],
        ok_prop_types: dict[str, tuple[type | list[type], bool]],
    ) -> None:
        # _check() helper for checking the contents of 'properties:'.

        props_yaml_source = self.yaml_source.get("properties") or {}
        if TYPE_CHECKING:
            assert isinstance(props_yaml_source, dict)

        for prop_name, prop_spec_source in props_yaml_source.items():
            for key in prop_spec_source:
                if prop_name in self.prop2bindings and key in top_level_props:
                    # Child bindings can have additional 'properties:' key.
                    continue

                if key not in ok_prop_keys:
                    _err(
                        f"unknown setting '{key}' in "
                        f"'properties: {prop_name}: ...' in {self.path}, "
                        f"expected one of {', '.join(ok_prop_keys)}"
                    )

            self._check_prop_by_type(
                prop_name, prop_spec_source, self.path, ok_prop_types
            )

            for true_false_opt in ["required", "deprecated"]:
                if true_false_opt in prop_spec_source:
                    option = prop_spec_source[true_false_opt]
                    if not isinstance(option, bool):
                        _err(
                            f"malformed '{true_false_opt}:' setting '{option}' "
                            f"for '{prop_name}' in 'properties' in {self.path}, "
                            "expected true/false"
                        )

            if prop_spec_source.get("deprecated") and prop_spec_source.get("required"):
                _err(
                    f"'{prop_name}' in 'properties' in {self.path} should not "
                    "have both 'deprecated' and 'required' set"
                )

            if "description" in prop_spec_source and not isinstance(
                prop_spec_source["description"], str
            ):
                _err(
                    "missing, malformed, or empty 'description' for "
                    f"'{prop_name}' in 'properties' in {self.path}"
                )

            if "enum" in prop_spec_source and not isinstance(
                prop_spec_source["enum"], list
            ):
                _err(f"enum in {self.path} for property '{prop_name}' is not a list")

    def _check_prop_by_type(
        self,
        prop_name: str,
        prop_spec_source: dict,
        binding_path: Optional[str],
        ok_prop_types: dict[str, tuple[type | list[type], bool]],
    ) -> None:
        # Binding._check_properties() helper. Checks 'type:', 'default:' and
        # 'const:' for the property named 'prop_name'

        prop_type = prop_spec_source.get("type")
        default = prop_spec_source.get("default")
        const = prop_spec_source.get("const")

        if prop_type is None:
            _err(
                f"missing 'type:' for '{prop_name}' in 'properties' in "
                f"{binding_path}"
            )

        if prop_type == "node":
            # Child nodes will be checked by child bindings.
            return

        if prop_type not in ok_prop_types:
            _err(
                f"'{prop_name}' in 'properties:' in {binding_path} "
                f"has unknown type '{prop_type}', expected one of "
                + ", ".join(ok_prop_types)
            )

        self._check_prop_by_source_specific_type(
            prop_name,
            prop_type,
            prop_spec_source,
            binding_path,
        )

        # If you change const_types, be sure to update the type annotation
        # for STPropertySpec.const.
        const_types = {"int", "array", "uint8-array", "string", "string-array"}
        if const and prop_type not in const_types:
            _err(
                f"const in {binding_path} for property '{prop_name}' "
                f"has type '{prop_type}', expected one of " + ", ".join(const_types)
            )

        # Check default

        if default is None:
            return

        expect_python_types, can_be_default = ok_prop_types[prop_type]
        if isinstance(
            expect_python_types, list
        ):  # this is also a type guard - don't simplify
            expect_array = True
            expect_python_type = expect_python_types[0]
        else:
            expect_array = False
            expect_python_type = expect_python_types

        if not can_be_default:
            _err(
                "'default:' can't be combined with "
                f"'type: {prop_type}' for '{prop_name}' in "
                f"'properties:' in {binding_path}"
            )

        def ok_default() -> bool:
            # Returns True if 'default' is an okay default for the property's type.
            # If you change this, be sure to update the type annotation for
            # PropertySpec.default.

            if expect_python_type is bytes:

                def validate_bytes(val) -> bool:
                    if isinstance(val, list):
                        return all(isinstance(v, int) for v in val)
                    elif isinstance(val, str):
                        try:
                            bytes.fromhex(val)
                            return True
                        except ValueError:
                            return False
                    return False

                if expect_array:
                    return isinstance(default, list) and all(
                        validate_bytes(val) for val in default
                    )
                else:
                    return validate_bytes(default)

            if expect_array:
                return isinstance(default, list) and all(
                    isinstance(val, expect_python_type) for val in default
                )
            else:
                return isinstance(default, expect_python_type)

        if not ok_default():
            _err(
                f"'default: {default}' is invalid for '{prop_name}' "
                f"in 'properties:' in {binding_path}, "
                f"which has type {prop_type}"
            )

    def _check_prop_by_source_specific_type(
        self,
        prop_name: str,
        prop_type: str,
        options: dict,
        binding_path: Optional[str],
    ) -> None:
        # May be overridden by subclasses.
        pass


#
# Private global functions
#


def _err(msg) -> NoReturn:
    raise STBindingError(msg)
