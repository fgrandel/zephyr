# SPDX-License-Identifier: Apache-2.0
#
# Copyright (c) 2021, 2023 Nordic Semiconductor ASA

include_guard(GLOBAL)
include(extensions)

# Finalize the values of DTS_ROOT and CONFIG_ROOT, so we know where all our DTS
# and configuration files, bindings, and vendor prefixes are.
#
# Outcome:
# The following variables will be defined when this CMake module completes:
#
# - DTS_ROOT: a deduplicated list of places where devicetree
#   implementation files (like bindings, vendor prefixes, etc.) are
#   found
# - DTS_ROOT_SYSTEM_INCLUDE_DIRS: set to "PATH1 PATH2 ...",
#   with one path per potential location where C preprocessor #includes
#   may be found for devicetree files
# - CONFIG_ROOT: a deduplicated list of places where configuration
#   implementation files (default subsystem configurations, bindings, etc.) are
#   found
# - CONFIG_ROOT_SYSTEM_INCLUDE_DIRS: set to "PATH1 PATH2 ...",
#   with one path per potential location where C preprocessor #includes
#   may be found for configuration files
#
# Required variables:
# None.
#
# Optional variables:
# - APPLICATION_SOURCE_DIR: path to app (added to DTS_ROOT and CONFIG_ROOT)
# - BOARD_DIR: directory containing the board definition (added to DTS_ROOT and
#   CONFIG_ROOT)
# - DTS_ROOT/CONFIG_ROOT: initial contents may be populated here
# - ZEPHYR_BASE: path to zephyr repository (added to DTS_ROOT and CONFIG_ROOT)
# - SHIELD_DIRS: paths to shield definitions (added to DTS_ROOT and CONFIG_ROOT)

# Using a function avoids polluting the parent scope unnecessarily.
function(pre_settings_module_run)
  # Convert relative paths to absolute paths relative to the application
  # source directory.
  zephyr_file(APPLICATION_ROOT DTS_ROOT)
  zephyr_file(APPLICATION_ROOT CONFIG_ROOT)

  # DTS_ROOT and CONFIG_ROOT always include the application directory, the board
  # directory, shield directories, and ZEPHYR_BASE.
  list(APPEND
    DTS_ROOT
    ${APPLICATION_SOURCE_DIR}
    ${BOARD_DIR}
    ${SHIELD_DIRS}
    ${ZEPHYR_BASE}
    )
  list(APPEND
    CONFIG_ROOT
    ${APPLICATION_SOURCE_DIR}
    ${BOARD_DIR}
    ${SHIELD_DIRS}
    ${ZEPHYR_BASE}
    )

  # Convert the directories in DTS_ROOT and CONFIG_ROOT to absolute paths
  # without symlinks.
  #
  # DTS and configuration directories can come from multiple places. Some
  # places, like a user's CMakeLists.txt can preserve symbolic links. Others,
  # like scripts/zephyr_module.py --settings-out resolve them.
  unset(real_dts_root)
  foreach(dts_dir ${DTS_ROOT})
    file(REAL_PATH ${dts_dir} real_dts_dir)
    list(APPEND real_dts_root ${real_dts_dir})
  endforeach()
  set(DTS_ROOT ${real_dts_root})
  unset(real_config_root)
  foreach(config_dir ${CONFIG_ROOT})
    file(REAL_PATH ${config_dir} real_config_dir)
    list(APPEND real_config_root ${real_config_dir})
  endforeach()
  set(CONFIG_ROOT ${real_config_root})

  # Finalize DTS_ROOT and CONFIG_ROOT.
  list(REMOVE_DUPLICATES DTS_ROOT)
  list(REMOVE_DUPLICATES CONFIG_ROOT)

  if(HWMv1)
    set(arch_include dts/${ARCH})
  else()
    foreach(arch ${ARCH_V2_NAME_LIST})
      list(APPEND arch_include dts/${arch})
    endforeach()
  endif()

  # Finalize DTS_ROOT_SYSTEM_INCLUDE_DIRS.
  unset(DTS_ROOT_SYSTEM_INCLUDE_DIRS)
  foreach(dts_root ${DTS_ROOT})
    foreach(dts_root_path
        include
        include/zephyr
        dts/common
        ${arch_include}
        dts
        )
      get_filename_component(full_path ${dts_root}/${dts_root_path} REALPATH)
      if(EXISTS ${full_path})
        list(APPEND DTS_ROOT_SYSTEM_INCLUDE_DIRS ${full_path})
      endif()
    endforeach()
  endforeach()

  # Finalize CONFIG_ROOT_SYSTEM_INCLUDE_DIRS.
  unset(CONFIG_ROOT_SYSTEM_INCLUDE_DIRS)
  foreach(config_root ${CONFIG_ROOT})
    foreach(config_root_path
        include
        include/zephyr
        config/common
        ${arch_include}
        config
        )
      get_filename_component(full_path ${config_root}/${config_root_path} REALPATH)
      if(EXISTS ${full_path})
        list(APPEND CONFIG_ROOT_SYSTEM_INCLUDE_DIRS ${full_path})
      endif()
    endforeach()
  endforeach()

  # Set output variables.
  set(DTS_ROOT ${DTS_ROOT} PARENT_SCOPE)
  set(DTS_ROOT_SYSTEM_INCLUDE_DIRS ${DTS_ROOT_SYSTEM_INCLUDE_DIRS} PARENT_SCOPE)
  set(CONFIG_ROOT ${CONFIG_ROOT} PARENT_SCOPE)
  set(CONFIG_ROOT_SYSTEM_INCLUDE_DIRS ${CONFIG_ROOT_SYSTEM_INCLUDE_DIRS} PARENT_SCOPE)
endfunction()

pre_settings_module_run()
