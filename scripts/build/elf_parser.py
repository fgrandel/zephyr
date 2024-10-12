#!/usr/bin/env python3
#
# Copyright (c) 2022, CSIRO
#
# SPDX-License-Identifier: Apache-2.0

import struct
import sys
from packaging import version
from typing import Optional, Union, NewType
from pathlib import PurePath
from ctypes import (
    Structure,
    LittleEndianStructure,
    BigEndianStructure,
    c_uint8,
    c_uint16,
    c_uint32,
    c_uint64,
    sizeof,
)

import elftools
from elftools.elf.elffile import ELFFile
from elftools.elf.sections import SymbolTableSection
from elftools.dwarf.die import DIE
from elftools.common.exceptions import DWARFError

from settings import EDTree

if version.parse(elftools.__version__) < version.parse('0.24'):
    sys.exit("pyelftools is out of date, need version 0.24 or later")

# Unfortunately the ctypes module does not expose a public base type for struct
# fields, so we define our own. Please note that we only permit platform
# independent types.
_CType = Union[c_uint8 | c_uint16 | c_uint32 | c_uint64]
_StructureField = Union[tuple[str, _CType] | tuple[str, _CType, int]]

# We only keep the types that are currently being used. Add more on an as-needed
# basis. Only primitive types need to be mapped.  We map types to uint*_t types
# to ensure host platform independence.
_C_TYPE_DATA_MODEL: dict[int, dict[str, _CType]] = {
    32: {
        "unsigned char": c_uint8,
        "short unsigned int": c_uint16,
    },
    64: {
        "unsigned char": c_uint8,
        "short unsigned int": c_uint16,
    },
}

class _Symbol:
    """
    Parent class for objects derived from an elf symbol.
    """
    def __init__(self, elf, sym):
        self.elf = elf
        self.sym = sym
        self.data = self.elf.symbol_data(sym)

    def __lt__(self, other):
        return self.sym.entry.st_value < other.sym.entry.st_value

    def _data_native_read(self, offset):
        (format, size) = self.elf.native_struct_format
        return struct.unpack(format, self.data[offset:offset + size])[0]

class DevicePM(_Symbol):
    """
    Represents information about device PM capabilities.
    """
    required_ld_consts = [
        "_PM_DEVICE_STRUCT_FLAGS_OFFSET",
        "_PM_DEVICE_FLAG_PD"
    ]

    def __init__(self, elf, sym):
        super().__init__(elf, sym)
        self.flags = self._data_native_read(self.elf.ld_consts['_PM_DEVICE_STRUCT_FLAGS_OFFSET'])

    @property
    def is_power_domain(self):
        return self.flags & (1 << self.elf.ld_consts["_PM_DEVICE_FLAG_PD"])

class DeviceOrdinals(_Symbol):
    """
    Represents information about device dependencies.
    """
    DEVICE_HANDLE_SEP = -32768
    DEVICE_HANDLE_ENDS = 32767
    DEVICE_HANDLE_NULL = 0

    def __init__(self, elf, sym):
        super().__init__(elf, sym)
        format = "<" if self.elf.little_endian else ">"
        format += "{:d}h".format(len(self.data) // 2)
        self._ordinals = struct.unpack(format, self.data)
        self._ordinals_split = []

        # Split ordinals on DEVICE_HANDLE_SEP
        prev =  1
        for idx, val in enumerate(self._ordinals, 1):
            if val == self.DEVICE_HANDLE_SEP:
                self._ordinals_split.append(self._ordinals[prev:idx-1])
                prev = idx
        self._ordinals_split.append(self._ordinals[prev:])

    @property
    def self_ordinal(self):
        return self._ordinals[0]

    @property
    def ordinals(self):
        return self._ordinals_split

class Device(_Symbol):
    """
    Represents information about a device object and its references to other objects.
    """
    required_ld_consts = [
        "_DEVICE_STRUCT_HANDLES_OFFSET",
        "_DEVICE_STRUCT_PM_OFFSET"
    ]

    def __init__(self, elf, sym):
        super().__init__(elf, sym)
        self.edt_node = None
        self.handle = None
        self.ordinals = None
        self.pm = None

        # Devicetree dependencies, injected dependencies, supported devices
        self.devs_depends_on = set()
        self.devs_depends_on_injected = set()
        self.devs_supports = set()

        # Point to the handles instance associated with the device;
        # assigned by correlating the device struct handles pointer
        # value with the addr of a Handles instance.
        self.obj_ordinals = None
        if '_DEVICE_STRUCT_HANDLES_OFFSET' in self.elf.ld_consts:
            ordinal_offset = self.elf.ld_consts['_DEVICE_STRUCT_HANDLES_OFFSET']
            self.obj_ordinals = self._data_native_read(ordinal_offset)

        self.obj_pm = None
        if '_DEVICE_STRUCT_PM_OFFSET' in self.elf.ld_consts:
            pm_offset = self.elf.ld_consts['_DEVICE_STRUCT_PM_OFFSET']
            self.obj_pm = self._data_native_read(pm_offset)

    @property
    def ordinal(self):
        return self.ordinals.self_ordinal

class ZephyrElf:
    """
    Represents information about devices in an elf file.
    """
    def __init__(
        self, kernel, edtree: EDTree, device_start_symbol: Optional[str] = None
    ):
        self.elf = ELFFile(open(kernel, "rb"))
        self.relocatable = self.elf['e_type'] == 'ET_REL'
        self.edtree = edtree
        self.devices = []
        if device_start_symbol is not None:
            self.ld_consts = self._symbols_find_value(
                set(
                    [
                        device_start_symbol,
                        *Device.required_ld_consts,
                        *DevicePM.required_ld_consts,
                    ]
                )
            )
            self._device_parse_and_link()

    @property
    def little_endian(self):
        """
        True if the elf file is for a little-endian architecture.
        """
        return self.elf.little_endian

    @property
    def native_struct_format(self):
        """
        Get the struct format specifier and byte size of the native machine type.
        """
        format = "<" if self.little_endian else ">"
        if self.elf.elfclass == 32:
            format += "I"
            size = 4
        else:
            format += "Q"
            size = 8
        return (format, size)

    def symbol_data(self, sym):
        """
        Retrieve the raw bytes associated with a symbol from the elf file.
        """
        # Symbol data parameters
        addr = sym.entry.st_value
        length = sym.entry.st_size
        # Section associated with the symbol
        section = self.elf.get_section(sym.entry['st_shndx'])
        data = section.data()
        # Relocatable data does not appear to be shifted
        offset = addr - (0 if self.relocatable else section['sh_addr'])
        # Validate data extraction
        assert offset + length <= len(data)
        # Extract symbol bytes from section
        return bytes(data[offset:offset + length])

    def _symbols_find_value(self, names):
        symbols = {}
        for section in self.elf.iter_sections():
            if isinstance(section, SymbolTableSection):
                for sym in section.iter_symbols():
                    if sym.name in names:
                        symbols[sym.name] = sym.entry.st_value
        return symbols

    def _object_find_named(self, prefix, cb):
        for section in self.elf.iter_sections():
            if isinstance(section, SymbolTableSection):
                for sym in section.iter_symbols():
                    if sym.entry.st_info.type != 'STT_OBJECT':
                        continue
                    if sym.name.startswith(prefix):
                        cb(sym)

    def get_struct(self, cu_path: str, struct_name: str) -> Optional[Structure]:
        struct_die = self._get_struct_DIE(cu_path, struct_name)
        if struct_die is None:
            return None

        members = [
            self._get_struct_field(member_die)
            for member_die in struct_die.iter_children()
        ]

        class StructClass(
            LittleEndianStructure if self.little_endian else BigEndianStructure
        ):
            _fields_ = members

        struct_class_name = struct_name.replace("_", " ").title().replace(" ", "")
        StructClass.__name__ = f"Struct{struct_class_name}"
        StructClass.__qualname__ = StructClass.__name__

        return StructClass

    def _get_struct_DIE(self, cu_path: str, struct_name: str) -> Optional[DIE]:
        if not self.elf.has_dwarf_info():
            return None
        dwarf_info = self.elf.get_dwarf_info()
        for cu in dwarf_info.iter_CUs():
            cu_die: DIE = cu.get_top_DIE()
            path = PurePath(cu_die.attributes["DW_AT_name"].value.decode("utf-8"))
            if not path.match(cu_path):
                continue

            struct_die: DIE
            for struct_die in cu_die.iter_children():
                if (
                    struct_die.tag == "DW_TAG_structure_type"
                    and "DW_AT_name" in struct_die.attributes.keys()
                    and struct_die.attributes["DW_AT_name"].value
                    == struct_name.encode()
                ):
                    return struct_die

        return None

    def _get_struct_field(self, member_die: DIE) -> _StructureField:
        # Identifies and returns the nominal type of the struct member, its name
        # and the C type that represents it.

        if member_die.tag != "DW_TAG_member":
            raise ValueError("Expected a DW_TAG_member DIE")

        type_die = member_die.get_DIE_from_attribute("DW_AT_type")
        while "DW_AT_type" in type_die.attributes:
            type_die = type_die.get_DIE_from_attribute("DW_AT_type")

        (member_name, primary_type, byte_size) = (
            member_die.attributes["DW_AT_name"].value.decode("utf-8"),
            type_die.attributes["DW_AT_name"].value.decode("utf-8"),
            type_die.attributes["DW_AT_byte_size"].value,
        )

        elf_class = self.elf.elfclass
        if primary_type not in _C_TYPE_DATA_MODEL[elf_class]:
            struct_name = (
                member_die.get_parent().attributes["DW_AT_name"].value.decode("utf-8")
            )
            raise DWARFError(
                f"Unknown type '{primary_type}' for member '{member_name}' of '{struct_name}'."
            )

        c_type = _C_TYPE_DATA_MODEL[elf_class][primary_type]
        assert (
            sizeof(c_type) == byte_size
        ), f"Invalid byte size for type '{primary_type}': expected '{byte_size}', found '{sizeof(c_type)}'."

        return (member_name, c_type)

    def _link_devices(self, devices):
        # Compute the dependency graph induced from the full graph restricted to the
        # the nodes that exist in the application.  Note that the edges in the
        # induced graph correspond to paths in the full graph.
        root = self.edtree.dep_ord2node[0]

        for ord, dev in devices.items():
            n = self.edtree.dep_ord2node[ord]

            deps = set(n.depends_on)
            while len(deps) > 0:
                dn = deps.pop()
                if dn.dep_ordinal in devices:
                    # this is used
                    dev.devs_depends_on.add(devices[dn.dep_ordinal])
                elif dn != root:
                    # forward the dependency up one level
                    for ddn in dn.depends_on:
                        deps.add(ddn)

            sups = set(n.required_by)
            while len(sups) > 0:
                sn = sups.pop()
                if sn.dep_ordinal in devices:
                    dev.devs_supports.add(devices[sn.dep_ordinal])
                else:
                    # forward the support down one level
                    for ssn in sn.required_by:
                        sups.add(ssn)

    def _link_injected(self, devices):
        for dev in devices.values():
            injected = dev.ordinals.ordinals[1]
            for inj in injected:
                if inj in devices:
                    dev.devs_depends_on_injected.add(devices[inj])
                    devices[inj].devs_supports.add(dev)

    def _device_parse_and_link(self):
        # Find all PM structs
        pm_structs = {}
        def _on_pm(sym):
            pm_structs[sym.entry.st_value] = DevicePM(self, sym)
        self._object_find_named('__pm_device_', _on_pm)

        # Find all ordinal arrays
        ordinal_arrays = {}
        def _on_ordinal(sym):
            ordinal_arrays[sym.entry.st_value] = DeviceOrdinals(self, sym)
        self._object_find_named('__devicedeps_', _on_ordinal)

        # Find all device structs
        def _on_device(sym):
            self.devices.append(Device(self, sym))
        self._object_find_named('__device_', _on_device)

        # Sort the device array by address (st_value) for handle calculation
        self.devices = sorted(self.devices)

        # Assign handles to the devices
        for idx, dev in enumerate(self.devices):
            dev.handle = 1 + idx

        # Link devices structs with PM and ordinals
        for dev in self.devices:
            if dev.obj_pm in pm_structs:
                dev.pm = pm_structs[dev.obj_pm]
            if dev.obj_ordinals in ordinal_arrays:
                dev.ordinals = ordinal_arrays[dev.obj_ordinals]
                if dev.ordinal != DeviceOrdinals.DEVICE_HANDLE_NULL:
                    dev.edt_node = self.edtree.dep_ord2node[dev.ordinal]

        # Create mapping of ordinals to devices
        devices_by_ord = {d.ordinal: d for d in self.devices if d.edt_node}

        # Link devices to each other based on the EDTree
        self._link_devices(devices_by_ord)

        # Link injected devices to each other
        self._link_injected(devices_by_ord)

    def device_dependency_graph(self, title, comment):
        """
        Construct a graphviz Digraph of the relationships between devices.
        """
        import graphviz
        dot = graphviz.Digraph(title, comment=comment)
        # Split iteration so nodes and edges are grouped in source
        for dev in self.devices:
            if dev.ordinal == DeviceOrdinals.DEVICE_HANDLE_NULL:
                text = '{:s}\\nHandle: {:d}'.format(dev.sym.name, dev.handle)
            else:
                n = self.edtree.dep_ord2node[dev.ordinal]
                text = '{:s}\\nOrdinal: {:d} | Handle: {:d}\\n{:s}'.format(
                    n.name, dev.ordinal, dev.handle, n.path
                )
            dot.node(str(dev.ordinal), text)
        for dev in self.devices:
            for sup in sorted(dev.devs_supports):
                dot.edge(str(dev.ordinal), str(sup.ordinal))
        return dot
