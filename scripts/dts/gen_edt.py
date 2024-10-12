#!/usr/bin/env python3

# Copyright (c) 2019 - 2020 Nordic Semiconductor ASA
# Copyright (c) 2019 Linaro Limited
# Copyright (c) 2024 SILA Embedded Solutions GmbH
# Copyright (c) 2024, The Zephyr Project
# SPDX-License-Identifier: Apache-2.0

# This script uses the settings library to generate a pickled merged settings
# tree from devicetree (.dts) and configuration tree (.config.yaml) files.
# Information from binding files in YAML format is used to validate all settings
# and expose them in a form that can be used to generate C-typed macros.

import argparse
import os
import pickle
import sys

sys.path.insert(
    0, os.path.join(os.path.dirname(__file__), "..", "lib", "python-settings", "src")
)

from settings import EDTree, STree, zephyr_build_stree

def main():
    args = parse_args()

    stree = zephyr_build_stree(
        bindings_dirs=args.bindings_dirs,
        dts_path=args.dts,
        config_path=args.config,
        vendor_prefix_files=args.vendor_prefixes,
        strict=args.edtlib_Werror,
        warn_reg_unit_address_mismatch="-Wno-simple_bus_reg" not in args.dtc_flags,
    )

    # Save merged DTS source, as a debugging aid
    with open(args.dts_out, "w", encoding="utf-8") as f:
        edtree: EDTree = stree.partial_tree_by_source_id("dts")
        print(edtree.dts_source, file=f)

    write_pickled_settings(stree, args.stree_pickle_out)


def parse_args() -> argparse.Namespace:
    # Returns parsed command-line arguments

    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("--dts", required=True, help="DTS file")
    parser.add_argument("--config", required=True, help="Config file")
    parser.add_argument("--dtc-flags",
                        help="'dtc' devicetree compiler flags, some of which "
                             "might be respected here")
    parser.add_argument("--bindings-dirs", nargs='+', required=True,
                        help="directory with bindings in YAML format, "
                        "we allow multiple")
    parser.add_argument("--dts-out", required=True,
                        help="path to write merged DTS source code to (e.g. "
                             "as a debugging aid)")
    parser.add_argument("--stree-pickle-out",
                        help="path to write pickled settings.STree to", required=True)
    parser.add_argument("--vendor-prefixes", action='append', default=[],
                        help="vendor-prefixes.txt path; used for validation; "
                             "may be given multiple times")
    parser.add_argument("--edtlib-Werror", action="store_true",
                        help="if set, edtlib-specific warnings become errors. "
                             "(this does not apply to warnings shared "
                             "with dtc.)")

    return parser.parse_args()


def write_pickled_settings(stree: STree, out_file: str) -> None:
    # Writes the edt object in pickle format to out_file.

    with open(out_file, 'wb') as f:
        # Pickle protocol version 4 is the default as of Python 3.8
        # and was introduced in 3.4, so it is both available and
        # recommended on all versions of Python that Zephyr supports
        # (at time of writing, Python 3.6 was Zephyr's minimum
        # version, and 3.10 the most recent CPython release).
        #
        # Using a common protocol version here will hopefully avoid
        # reproducibility issues in different Python installations.
        pickle.dump(stree, f, protocol=4)


if __name__ == "__main__":
    main()
