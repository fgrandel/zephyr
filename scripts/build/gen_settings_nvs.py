#!/usr/bin/env python3

# Copyright (c) 2024 The Zephyr Project
# SPDX-License-Identifier: Apache-2.0

# Note: Do not access private (_-prefixed) identifiers anywhere in this script.

"""
This script builds on the pickled devicetree representation (zephyr/edt.pickle),
Kconfig (zephyr/.config) and information extracted from the Zephyr binary
(zephyr/zephyr.elf) to generate an image of the selected settings compatible
with the NVS settings backend. You can include arbitrary configuration subtrees
in the image by specifying them with the --include option.
"""


import sys
import os

# The python-devicetree module is needed to load and access the edt.pickle file.
sys.path.insert(
    0, os.path.join(os.path.dirname(__file__), "..", "dts", "python-devicetree", "src")
)

import argparse
import pickle
import re

from ctypes import Structure, sizeof, c_uint8, c_uint16
from elf_parser import ZephyrElf, Device
from enum import IntEnum
from dataclasses import dataclass, field
from devicetree.edtlib import EDT, Node as EDTNode, Property as EDTProperty
from functools import cached_property
from intelhex import IntelHex
from math import floor
from pathlib import Path
from textwrap import dedent
from typing import Final, Optional, Iterator


class Config:
    FILTERED_PROPS = {"label", "status", "reg"}

    def __init__(self):
        parser = self._parser
        self._args = parser.parse_args()

        # Input directory
        input_dir = self.input_dir
        if not input_dir.is_dir():
            print(f"Error: '{input_dir}' given as '--input-dir' is not a directory.")
            parser.exit()

        # Input files
        input_files = [
            input_dir / "edt.pickle",
            input_dir / "zephyr.elf",
            input_dir / ".config",
        ]
        for input_file in input_files:
            if not input_file.is_file():
                print(f"Error: '{input_file}' not found.")
                parser.exit()
        self.edt_pickle_path, self.zephyr_elf_path, self.dot_config_path = input_files

        # Output files
        self.settings_hex_path = input_dir / f"{self.rel_output_file_basename}.hex"
        self.settings_bin_path = input_dir / f"{self.rel_output_file_basename}.bin"

    @cached_property
    def input_dir(self) -> Path:
        return Path(self._args.input_dir)

    @cached_property
    def output_dir(self) -> Path:
        return self._args.output_dir if self._args.output_dir else self.input_dir

    @cached_property
    def filtered_props(self) -> set[str]:
        return self.FILTERED_PROPS | set(self._args.exclude_prop)

    @cached_property
    def rel_output_file_basename(self) -> Path:
        return (self.output_dir / self._args.name).relative_to(Path.cwd())

    @cached_property
    def subtrees_with_prefix(self) -> list[str]:
        includes = []
        for idx, parts in enumerate(
            [include.split(":") for include in self._args.include]
        ):
            if len(parts) > 2:
                raise ValueError(f"Invalid include: {self._args.include[idx]}")
            elif len(parts) == 2:
                includes.append(parts)
            else:
                includes.append((None, parts[0]))
        return includes

    @property
    def devices_start_symbol(self) -> str:
        return self._args.devices_start_symbol

    @property
    def verbose(self) -> bool:
        return self._args.verbose

    @cached_property
    def _parser(self):
        parser = argparse.ArgumentParser(description=__doc__)

        parser.add_argument(
            "-n",
            "--name",
            help="""
                 The base name of the settings image file to be generated. The
                 generated files will be <output-dir>/<name>.hex and
                 <output-dir>/<name>.bin (mandatory).
                 """,
            required=True,
        )

        parser.add_argument(
            "-i",
            "--input-dir",
            help="""
                 The build directory containing the zephyr/edt.pickle,
                 zephyr/zephyr.elf, and zephyr/.config files. This also is the
                 default output directory unless the --output-dir option is
                 used. If not given, the current working directory is used.
                 """,
            default=Path.cwd(),
            type=Path,
        )

        parser.add_argument(
            "-o",
            "--output-dir",
            help="""
                 The output directory where the settings image files will be
                 generated. If not given, <input-dir> will be used.
                 """,
            type=Path,
        )

        parser.add_argument(
            "--include",
            help="""
                 A subtree referenced by its full path or node label in the
                 global data model to be included into the image with all
                 children, e.g. "/alpha" or "&beta". The original subtree prefix
                 in the configuration source can be replaced by including a
                 colon-terminated string colon, e.g. "/gamma:/alpha" or
                 "/gamma:&beta" (mandatory, can be repeated).
                 """,
            action="append",
            required=True,
        )

        parser.add_argument(
            "--exclude-prop",
            help="""
                 A property to exclude from the settings image. The properties
                 "label", "status", and "reg" are always excluded (can be
                 repeated).
                 """,
            action="append",
            default=[],
        )

        parser.add_argument(
            "-s",
            "--devices-start-symbol",
            help="""
                 Symbol name of the section which contains the
                 devices. The symbol name must point to the first
                 device in that section.
                 """,
            required=True,
        )

        parser.add_argument(
            "-v",
            "--verbose",
            help="""
                 Print settings and setting values as they are written
                 to the NVS settings image.
                 """,
            action="store_true",
        )
        return parser


def _find_in_kconfig(dot_config: Path | str, config_keys: str | list[str]) -> list[str]:
    if isinstance(config_keys, str):
        config_keys = [config_keys]

    re_keys = re.compile(rf"({'|'.join(config_keys)})=(.+)")

    with open(dot_config) as f:
        lines = f.readlines()

    matches = {}
    for line in lines:
        if m := re_keys.match(line):
            matches[m.group(1)] = m.group(2).strip("\"'")
            if len(matches) == len(config_keys):
                break

    return [matches.get(key) for key in config_keys]


class CRC:
    CRC8_CCITT_SMALL_TABLE: Final[list[int]] = [
        0x00,
        0x07,
        0x0E,
        0x09,
        0x1C,
        0x1B,
        0x12,
        0x15,
        0x38,
        0x3F,
        0x36,
        0x31,
        0x24,
        0x23,
        0x2A,
        0x2D,
    ]

    @staticmethod
    def crc8_ccitt(val: int, data: bytes) -> int:
        for i in range(len(data)):
            val ^= data[i]
            val = ((val << 4) & NvsFs.UINT8_MASK) ^ CRC.CRC8_CCITT_SMALL_TABLE[val >> 4]
            val = ((val << 4) & NvsFs.UINT8_MASK) ^ CRC.CRC8_CCITT_SMALL_TABLE[val >> 4]
        return val


@dataclass
class FlashPartition:
    id: int  # DT fixed flash partition id
    offset: int  # in bytes
    size: int  # in bytes
    sector_size: int  # in bytes
    write_block_size: int  # in bytes
    erase_value: int = 0xFF

    data: bytearray = field(init=False)

    def __post_init__(self):
        assert self.erase_value > 0 and self.erase_value <= 0xFF
        self.data = bytearray([self.erase_value] * self.size)


class NvsFs:
    """
    Write-only version of the NVS file system,
    see zephyr/include/zephyr/fs/nvs.h
    """

    UINT8_MASK: Final[int] = 0xFF
    UINT16_MASK: Final[int] = 0xFFFF

    ADDR_SECT_MASK: Final[int] = 0xFFFF0000
    ADDR_SECT_SHIFT: Final[int] = 16
    ADDR_OFFS_MASK: Final[int] = 0x0000FFFF

    # Will be overridden in __init__, mostly for correct type hints.
    class StructNvsAte(Structure):
        _fields_ = [
            ("id", c_uint16),
            ("offset", c_uint16),
            ("len", c_uint16),
            ("part", c_uint8),
            ("crc8", c_uint8),
        ]

    class IntLen(IntEnum):
        INT8 = 1
        INT16 = 2
        INT32 = 4
        INT64 = 8

    def __init__(
        self,
        elf: ZephyrElf,
        flash_partition: FlashPartition,
        sector_size: int,  # in bytes
        sector_count: int,
        data_crc: bool,
    ):
        assert sector_size & NvsFs.UINT16_MASK == sector_size
        assert sector_count & NvsFs.UINT16_MASK == sector_count
        assert sector_count * sector_size <= flash_partition.size

        self.flash_partition = flash_partition
        self.sector_size = sector_size
        self.sector_count = sector_count
        self.data_crc = data_crc

        self.ATE_SIZE = self._nvs_al_size(sizeof(NvsFs.StructNvsAte))

        # Initialize write addresses to the first sector.
        self.ate_wra = self.sector_size - self.ATE_SIZE
        self.data_wra = 0

        self.endianness = "little" if elf.little_endian else "big"

        # Check that NVS is actually part of the build and create struct nvs_ate
        # with correct endianness.
        ElfStructNvsAte = elf.get_struct("subsys/fs/nvs/nvs.c", "nvs_ate")
        if ElfStructNvsAte is None:
            raise ValueError("NVS not built. Please enable CONFIG_NVS.")

        assert sizeof(ElfStructNvsAte) == self.ATE_SIZE
        NvsFs.StructNvsAte = ElfStructNvsAte

    def nvs_write(
        self,
        id: int,
        data: str | bytes | int | bool | list[str] | list[int],
        int_len: IntLen = None,
    ):
        if isinstance(data, bytes):
            assert int_len is None
            raw_data = data
        elif isinstance(data, str):
            assert int_len is None
            raw_data = data.encode("utf-8")
        elif isinstance(data, int) or isinstance(data, bool):
            assert int_len in iter(NvsFs.IntLen)
            raw_data = data.to_bytes(int_len, self.endianness)
        elif isinstance(data, list):
            if not data:
                raise ValueError("Empty list.")

            raw_data = b""
            for item in data:
                if isinstance(item, str):
                    raw_data += item.encode("utf-8")
                elif isinstance(item, int):
                    raw_data += item.to_bytes(int_len, self.endianness)
                else:
                    raise ValueError("Invalid data type in list.")

        else:
            raise ValueError("Invalid data type.")

        crc_size = 4 if self.data_crc else 0
        max_data_size = self.sector_size - 4 * self.ATE_SIZE - crc_size

        raw_data_len = len(raw_data)
        data_len = self._nvs_al_size(raw_data_len)
        assert data_len >= raw_data_len

        if data_len > max_data_size:
            raise ValueError("Data does not fit into a single NVS sector.")

        if data_len == 0:
            raise ValueError("Data size must be greater than zero.")

        required_space = data_len + self.ATE_SIZE + crc_size
        while True:
            if self.ate_wra >= self.data_wra + required_space:
                self._nvs_flash_wrt_entry(id, raw_data)
                break

            self._nvs_sector_close()

    @staticmethod
    def _nvs_ate_crc8_update(entry: StructNvsAte):
        entry.crc8 = CRC.crc8_ccitt(
            0xFF, bytes(entry)[: NvsFs.StructNvsAte.crc8.offset]
        )

    def _nvs_al_size(self, len: int) -> int:
        write_block_size = self.flash_partition.write_block_size

        if write_block_size <= 1:
            return len

        return (len + write_block_size - 1) & ~(write_block_size - 1)

    def _nvs_flash_al_wrt(self, addr: int, data: bytes) -> int:
        num_bytes = len(data)
        assert num_bytes > 0

        offset = self.sector_size * (addr >> NvsFs.ADDR_SECT_SHIFT)
        offset += addr & NvsFs.ADDR_OFFS_MASK

        self.flash_partition.data[offset : offset + num_bytes] = data

    def _nvs_flash_ate_wrt(self, entry: StructNvsAte):
        self._nvs_flash_al_wrt(self.ate_wra, bytes(entry))
        self.ate_wra -= self.ATE_SIZE

    def _nvs_flash_data_wrt(self, data: bytes):
        num_bytes = len(data)
        assert num_bytes > 0

        if self.data_crc:
            raise NotImplementedError("Data CRC not implemented.")

        self._nvs_flash_al_wrt(self.data_wra, data)
        self.data_wra += self._nvs_al_size(num_bytes)

    def _nvs_flash_wrt_entry(self, id: int, data: bytes):
        assert len(data) > 0

        entry = NvsFs.StructNvsAte(
            id=id, offset=self.data_wra & NvsFs.ADDR_OFFS_MASK, len=len(data), part=0xFF
        )

        self._nvs_flash_data_wrt(data)
        self._nvs_ate_crc8_update(entry)
        self._nvs_flash_ate_wrt(entry)

    def _nvs_sector_advance(self, addr: int) -> int:
        addr += 1 << NvsFs.ADDR_SECT_SHIFT
        if (addr >> NvsFs.ADDR_SECT_SHIFT) == self.sector_count:
            raise ValueError("No more NVS sectors available.")
        return addr

    def _nvs_sector_close(self):
        close_ate = NvsFs.StructNvsAte(
            id=0xFFFF,
            offset=(self.ate_wra + self.ATE_SIZE) & NvsFs.ADDR_OFFS_MASK,
            len=0,
            part=0xFF,
        )

        self.ate_wra &= NvsFs.ADDR_SECT_MASK
        self.ate_wra += self.sector_size - self.ATE_SIZE

        self._nvs_ate_crc8_update(close_ate)
        self._nvs_flash_ate_wrt(close_ate)
        self.ate_wra = self._nvs_sector_advance(self.ate_wra)
        self.data_wra = self.ate_wra & NvsFs.ADDR_SECT_MASK


class SettingsNvs:
    """append-only version of the NVS settings backend"""

    NAME_CNT_ID: Final[int] = 0x8000
    NAME_ID_OFFSET: Final[int] = 0x4000

    SETTINGS_MAX_DIR_DEPTH = 8
    SETTINGS_MAX_NAME_LEN = 8 * SETTINGS_MAX_DIR_DEPTH
    SETTINGS_EXTRA_LEN = (SETTINGS_MAX_DIR_DEPTH - 1) + 2

    def __init__(
        self,
        edt: EDT,
        elf: ZephyrElf,
        dot_config: Path | str,
    ):
        self.last_name_id = SettingsNvs.NAME_CNT_ID
        self.nvs_fs = SettingsNvs._get_nvs_fs(edt, elf, dot_config)

    def nvs_write_setting(self, name: str, value: int, int_len: NvsFs.IntLen = None):
        if (
            len(name)
            > SettingsNvs.SETTINGS_MAX_NAME_LEN + SettingsNvs.SETTINGS_EXTRA_LEN + 1
        ):
            raise ValueError("Name too long.")

        name_id = self.last_name_id + 1
        if name_id == SettingsNvs.NAME_CNT_ID + SettingsNvs.NAME_ID_OFFSET:
            raise ValueError("No free name IDs left.")

        self.last_name_id += 1

        self.nvs_fs.nvs_write(SettingsNvs.NAME_CNT_ID, name_id, NvsFs.IntLen.INT16)
        self.nvs_fs.nvs_write(name_id + SettingsNvs.NAME_ID_OFFSET, value, int_len)
        self.nvs_fs.nvs_write(name_id, name)

    @staticmethod
    def _get_settings_partition(edt: EDT) -> FlashPartition:
        fixed_partitions_list = edt.compat2okay["fixed-partitions"]

        enabled_partitions = sorted(
            [
                fixed_partition
                for fixed_partitions in fixed_partitions_list
                if fixed_partitions.status == "okay"
                for fixed_partition in fixed_partitions.children.values()
                if fixed_partition.status == "okay"
            ],
            key=lambda node: node.dep_ordinal,
        )

        settings_partition = edt.chosen_node("zephyr,settings-partition")

        if not settings_partition:
            settings_partition = next(
                (
                    enabled_partition
                    for enabled_partition in enabled_partitions
                    if "storage_partition" in enabled_partition.labels
                ),
                None,
            )

        if not settings_partition:
            raise ValueError(
                "No settings partition found. Please define a partition labeled"
                "'storage_partition' or a 'chosen' node named 'zephyr,settings-partition'."
            )

        flash_controller = settings_partition.flash_controller
        assert flash_controller
        erase_value = flash_controller.props.get("erase-value")

        flash_layout = settings_partition.parent.parent
        if not (flash_layout and "soc-nv-flash" in flash_layout.compats):
            raise ValueError(
                "Only SoC nonvolatile flash ('soc-nv-flash') partitions are currently supported"
            )

        # TODO: This is currently a workaround to get the sector size. It should be
        # correct in most cases, but in principle it may be different from the
        # sector size returned by the driver's `api->page_layout()` call.
        sector_size = flash_layout.props.get("erase-block-size")
        if not sector_size:
            raise ValueError("Erase block size not found in 'soc-nv-flash' node.")

        write_block_size = flash_layout.props.get("write-block-size")
        if not write_block_size:
            raise ValueError("Write block size not found in 'soc-nv-flash' node.")

        flash_id = (
            enabled_partitions.index(settings_partition) if settings_partition else -1
        )
        assert flash_id >= 0

        assert len(flash_layout.regs) == 1
        flash_reg = flash_layout.regs[0]
        flash_offset = flash_reg.addr

        assert len(settings_partition.regs) == 1
        partition_reg = settings_partition.regs[0]
        partition_offset, size = partition_reg.addr, partition_reg.size

        flash_partition = FlashPartition(
            flash_id,
            flash_offset + partition_offset,
            size,
            sector_size.val,
            write_block_size.val,
        )
        if erase_value:
            flash_partition.erase_value = erase_value.val
        return flash_partition

    @staticmethod
    def _get_nvs_fs(edt: EDT, elf: ZephyrElf, dot_config: Path | str) -> NvsFs:
        config = _find_in_kconfig(
            dot_config,
            [
                "CONFIG_SETTINGS_NVS_SECTOR_SIZE_MULT",
                "CONFIG_SETTINGS_NVS_SECTOR_COUNT",
                "CONFIG_NVS_DATA_CRC",
            ],
        )
        nvs_sector_size_mult, nvs_sector_count = map(int, config[:2])
        nvs_data_crc = bool(config[2])

        settings_partition = SettingsNvs._get_settings_partition(edt)
        nvs_sector_size = nvs_sector_size_mult * settings_partition.sector_size
        nvs_sector_count = min(
            floor(settings_partition.size / nvs_sector_size), nvs_sector_count
        )

        return NvsFs(
            elf, settings_partition, nvs_sector_size, nvs_sector_count, nvs_data_crc
        )

    @property
    def offset(self):
        return self.nvs_fs.flash_partition.offset

    @property
    def data(self):
        return bytes(self.nvs_fs.flash_partition.data)


def config_subtree_nodes(edt: EDT, subtree: str | EDTNode) -> Iterator[EDTNode]:
    """
    A depth-first iterator of all subnodes of the given configuration path or
    node (including the given node itself).
    """
    if isinstance(subtree, str):
        if subtree.startswith("/"):
            node = edt.get_node(subtree)
        elif subtree.startswith("&"):
            try:
                node = edt.label2node[subtree[1:]]
            except KeyError:
                node = None
        else:
            node = None
    else:
        node = subtree

    if not isinstance(node, EDTNode):
        raise ValueError(f"Invalid node reference: {subtree}.")

    yield node

    # depth first
    for child in node.children.values():
        yield from config_subtree_nodes(edt, child)


def config_convert_phandles(
    phandles: EDTNode | list[EDTNode], prop_type: str, devices_by_ord: dict[int, Device]
):
    phandles = phandles if isinstance(phandles, list) else [phandles]

    prop_vals = []
    devices_only = False

    for phandle in phandles:
        if phandle.dep_ordinal in devices_by_ord:
            if not devices_only:
                if prop_vals:
                    raise ValueError("Device and non-device phandles mixed.")
                else:
                    # We add a zero byte to the beginning of the array
                    # to indicate that this is not a list of zero-terminated
                    # path strings. Device handles are always non-zero.
                    prop_vals.append(0)
                    devices_only = True

            # Save the 16 bit device handle(s).
            prop_vals.append(devices_by_ord[phandle.dep_ordinal].handle)

        else:
            if devices_only:
                raise ValueError("Device and non-device phandles mixed.")

            # Save the referenced path(s) if the node is not pointing to a device.
            prop_vals.append(phandle.path)

    int_len = NvsFs.IntLen.INT16 if isinstance(prop_vals[0], int) else None

    if prop_type in ("path", "phandle"):
        assert len(prop_vals) == 1
        prop_val = prop_vals[0]
    else:
        prop_val = prop_vals

    return prop_val, int_len


def min_bytes(val: int) -> int:
    min_bytes = (val.bit_length() - 1) // 8 + 1
    next_pow2 = 2 ** (min_bytes - 1).bit_length()
    return next_pow2


def config_prepare_property(
    prop: EDTProperty,
    root_node: EDTNode,
    prefix: Optional[str],
    devices_by_ord: dict[int, Device],
    verbose: bool = False,
):
    node = prop.node
    prop_type = prop.spec.type

    if prop_type in ("path", "phandle", "phandles"):
        prop_val, int_len = config_convert_phandles(prop.val, prop_type, devices_by_ord)
    else:
        prop_val = prop.val

        if prop_type == "boolean":
            int_len = NvsFs.IntLen.INT8

        elif prop_type in ("int", "array"):
            val_list = prop_val if isinstance(prop_val, list) else [prop_val]
            int_len = NvsFs.IntLen(max(map(min_bytes, val_list)))

        elif prop_type in ("string", "string-array", "uint8-array"):
            int_len = None

        else:
            raise ValueError(f"Unsupported property type: {prop_type}")

    array_suffix = f"[{len(prop.val)}]" if isinstance(prop.val, list) else ""
    setting_path = f"{node.path}/{prop.name}{array_suffix}"

    if prefix is not None:
        setting_path = setting_path.replace(root_node.path, prefix)

    setting_path = setting_path[1:]

    if verbose:
        print(f"Writing {setting_path}: {prop_val} (len: {int_len or len(prop_val)}).")

    return setting_path, prop_val, int_len


def main():
    cfg = Config()

    if cfg.verbose:

        def format_input_dir(subtree_with_prefix):
            prefix, subtree = subtree_with_prefix
            prefix = f'prefixed with "{prefix}"' if prefix else "(not prefixed)"
            return f'"{subtree}" {prefix}'

        next_line = "\n                    - "
        print(
            dedent(
                f"""
                Image Files: {cfg.rel_output_file_basename}.{{hex|bin}}
                Input Directory: {cfg.input_dir}
                Output Directory: {cfg.output_dir}
                Include:
                    - {next_line.join(map(format_input_dir, cfg.subtrees_with_prefix))}
                Devices Start Symbol: {cfg.devices_start_symbol}
                """
            )
        )

    with open(cfg.edt_pickle_path, "rb") as f:
        edt: EDT = pickle.load(f)

    elf = ZephyrElf(cfg.zephyr_elf_path, edt, cfg.devices_start_symbol)
    devices_by_ord: dict[int, Device] = {
        device.ordinal: device for device in elf.devices if device.edt_node
    }

    settings_nvs = SettingsNvs(edt, elf, cfg.dot_config_path)

    for prefix, subtree_path in cfg.subtrees_with_prefix:
        root_node = None
        for node in config_subtree_nodes(edt, subtree_path):
            if not root_node:
                root_node = node

            for prop in node.props.values():
                if prop.name in cfg.filtered_props:
                    continue

                setting_path, prop_val, int_len = config_prepare_property(
                    prop, root_node, prefix, devices_by_ord, cfg.verbose
                )

                settings_nvs.nvs_write_setting(setting_path, prop_val, int_len)

    ih = IntelHex()
    ih.frombytes(settings_nvs.data, settings_nvs.offset)
    ih.tofile(cfg.settings_hex_path, "hex")
    ih.tofile(cfg.settings_bin_path, "bin")

    if cfg.verbose:
        print(
            f"\nGenerated '{cfg.rel_output_file_basename}.{{hex|bin}}' "
            f"for flash address range {'-'.join(map(hex,ih.segments()[0]))}\n"
        )


if __name__ == "__main__":
    main()
