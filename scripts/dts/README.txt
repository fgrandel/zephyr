This folder contains scripts that generate artifacts (DTS pickle file, Kconfig
variables, macros, ...) from in-tree and out-of-tree devicetree and binding
source files.

Generic libraries and corresponding tests required by the scripts in this folder
can be found in the 'zephyr/scripts/lib' directory. We may publish packages
under 'zephyr/scripts/lib' to PyPi now and then for access by external tools.
To simplify development and maintenance, we'll not use the PyPi version
ourselves, though.  We'll be including shared libraries via insertion of
'zephyr/scripts/lib' sub-folders into the python module search path, instead,
such that scripts will always use the latest development version from the
working tree.
