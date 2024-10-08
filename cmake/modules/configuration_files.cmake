# SPDX-License-Identifier: Apache-2.0
#
# Copyright (c) 2021, Nordic Semiconductor ASA

# Zephyr build system configuration files.
#
# Locate the Kconfig and DT config files that are to be used.
# Also, locate the appropriate application config directory.
#
# Outcome:
# The following variables will be defined when this CMake module completes:
#
# - CONF_FILE:              List of Kconfig fragments
# - EXTRA_CONF_FILE:        List of additional Kconfig fragments
# - SETTINGS_FILES:         List of devicetree and configuration files
# - DTS_EXTRA_CPPFLAGS      List of additional devicetree preprocessor defines
# - APPLICATION_CONFIG_DIR: Root folder for application configuration
#
# If any of the above variables are already set when this CMake module is
# loaded, then no changes to the variable will happen.
#
# Variables set by this module and not mentioned above are considered internal
# use only and may be removed, renamed, or re-purposed without prior notice.

include_guard(GLOBAL)

include(extensions)

# Merge in variables from other sources (e.g. sysbuild)
zephyr_get(FILE_SUFFIX SYSBUILD GLOBAL)

zephyr_get(APPLICATION_CONFIG_DIR SYSBUILD GLOBAL)
zephyr_file(APPLICATION_ROOT APPLICATION_CONFIG_DIR)
set_ifndef(APPLICATION_CONFIG_DIR ${APPLICATION_SOURCE_DIR})
string(CONFIGURE ${APPLICATION_CONFIG_DIR} APPLICATION_CONFIG_DIR)

zephyr_get(CONF_FILE SYSBUILD LOCAL)
if(NOT DEFINED CONF_FILE)
  zephyr_file(CONF_FILES ${APPLICATION_CONFIG_DIR} KCONF CONF_FILE NAMES "prj" SUFFIX ${FILE_SUFFIX} REQUIRED)
  zephyr_file(CONF_FILES ${APPLICATION_CONFIG_DIR}/socs KCONF CONF_FILE QUALIFIERS SUFFIX ${FILE_SUFFIX})
  zephyr_file(CONF_FILES ${APPLICATION_CONFIG_DIR}/boards KCONF CONF_FILE SUFFIX ${FILE_SUFFIX})
else()
  string(CONFIGURE "${CONF_FILE}" CONF_FILE_EXPANDED)
  string(REPLACE " " ";" CONF_FILE_AS_LIST "${CONF_FILE_EXPANDED}")
  list(LENGTH CONF_FILE_AS_LIST CONF_FILE_LENGTH)
  if(${CONF_FILE_LENGTH} EQUAL 1)
    get_filename_component(CONF_FILE_NAME ${CONF_FILE} NAME)
    if(${CONF_FILE_NAME} MATCHES "prj_(.*).conf")
      set(CONF_FILE_BUILD_TYPE ${CMAKE_MATCH_1})
      zephyr_file(CONF_FILES ${APPLICATION_CONFIG_DIR}/boards KCONF CONF_FILE
                  BUILD ${CONF_FILE_BUILD_TYPE}
      )
      set(CONF_FILE_FORCE_CACHE FORCE)
    endif()
  endif()
endif()

set(APPLICATION_CONFIG_DIR ${APPLICATION_CONFIG_DIR} CACHE PATH "The application configuration folder" FORCE)
set(CONF_FILE ${CONF_FILE} CACHE STRING "If desired, you can build the application using\
the configuration settings specified in an alternate .conf file using this parameter. \
These settings will override the settings in the application’s .config file or its default .conf file.\
Multiple files may be listed, e.g. CONF_FILE=\"prj1.conf prj2.conf\" \
The CACHED_CONF_FILE is internal Zephyr variable used between CMake runs. \
To change CONF_FILE, use the CONF_FILE variable." ${CONF_FILE_FORCE_CACHE})

# The CONF_FILE variable is now set to its final value.
zephyr_boilerplate_watch(CONF_FILE)

# Backwards compatibility for deprecated variables
# TODO: Remove this in a future release.
if(DEFINED SETTINGS_FILES AND DEFINED DTS_SOURCE)
  message(FATAL_ERROR "Both SETTINGS_FILES and DTS_SOURCE are defined. "
          "Please only use SETTINGS_FILES (DTS_SOURCE is deprecated)."
  )
elseif(DEFINED DTS_SOURCE)
  message(DEPRECATED "DTS_SOURCE is deprecated. Please use SETTINGS_FILES instead.")
  set(SETTINGS_FILES ${DTS_SOURCE})
endif()
if(DEFINED SETTINGS_OVERLAY_FILES AND DEFINED DTC_OVERLAY_FILE)
  message(FATAL_ERROR "Both SETTINGS_OVERLAY_FILES and DTC_OVERLAY_FILE are defined. "
          "Please only use SETTINGS_OVERLAY_FILES (DTC_OVERLAY_FILE is deprecated)."
  )
elseif(DEFINED DTC_OVERLAY_FILE)
  message(DEPRECATED "DTC_OVERLAY_FILE is deprecated. Please use SETTINGS_OVERLAY_FILES instead.")
  set(SETTINGS_OVERLAY_FILES ${DTC_OVERLAY_FILE})
endif()
if(DEFINED EXTRA_SETTINGS_OVERLAY_FILES AND DEFINED EXTRA_DTC_OVERLAY_FILE)
  message(FATAL_ERROR "Both EXTRA_SETTINGS_OVERLAY_FILES and EXTRA_DTC_OVERLAY_FILE are defined. "
          "Please only use EXTRA_SETTINGS_OVERLAY_FILES (EXTRA_DTC_OVERLAY_FILE is deprecated)."
  )
elseif(DEFINED EXTRA_DTC_OVERLAY_FILE)
  message(DEPRECATED "EXTRA_DTC_OVERLAY_FILE is deprecated. Please use EXTRA_SETTINGS_OVERLAY_FILES instead.")
  set(EXTRA_SETTINGS_OVERLAY_FILES ${EXTRA_DTC_OVERLAY_FILE})
endif()


# Search for devicetree overlays and/or configuration files iterating over
# a prioritized list of search paths.
#
# The following will be merged into the final list of settings files (in this
# order):

# 1. one of (first match):
#    - base devicetree source, devicetree overlays and configuration files from
#      the command line or environment (SETTINGS_FILES).
#    - base devicetree source (or a stub source if no base devicetree source is
#      present), devicetree overlays and configuration files from the board
#      directory plus the root configuration source.
if(DEFINED SETTINGS_FILES AND NOT SETTINGS_FILES STREQUAL "")
  zephyr_get(SETTINGS_FILES SYSBUILD LOCAL)
else()
  # Search the board directory for the board's base device tree and dts overlay
  # files matching the current qualifiers and revision (if any)...
  # TODO: Check that a single /dts-v1/ header is present in the first file found
  #       while all other files must not contain the /dts-v1/ header.
  zephyr_file(CONF_FILES ${BOARD_DIR} SETTINGS SETTINGS_FILES)

  if(NOT DEFINED SETTINGS_FILES OR SETTINGS_FILES STREQUAL "")
    # If we don't have any board settings, provide at least an empty devicetree
    # stub and a base configuration skeleton.
    set(SETTINGS_FILES ${ZEPHYR_BASE}/boards/common/stub.dts)
  endif()

  list(APPEND SETTINGS_FILES ${ZEPHYR_BASE}/config/root.config.yaml)
endif()

# 2. devicetree overlays and configuration files from board extensions
#    (BOARD_EXTENSION_DIRS)
if (DEFINED BOARD_EXTENSION_DIRS AND NOT BOARD_EXTENSION_DIRS STREQUAL "")
  zephyr_file(CONF_FILES ${BOARD_EXTENSION_DIRS} SETTINGS SETTINGS_FILES)
endif()

# 3. devicetree overlays and configuration files from shields
#    (SHIELD_SETTINGS_FILES)
if (DEFINED SHIELD_SETTINGS_FILES AND NOT SHIELD_SETTINGS_FILES STREQUAL "")
  list(APPEND SETTINGS_FILES ${SHIELD_SETTINGS_FILES})
endif()

# 4. one of (first match):
#    - devicetree overlays and configuration files from the command line
#      or environment (SETTINGS_OVERLAY_FILES).
#    - board- and SoC-specific overlays (in this order) in the 'boards' and
#      'socs' subdirectories of the application configuration directory.
#    - board-specific overlays in the application configuration directory
#      (deprecated).
#    - app.overlay[.y[a]ml] in the application configuration directory
if (DEFINED SETTINGS_OVERLAY_FILES AND NOT SETTINGS_OVERLAY_FILES STREQUAL "")
  zephyr_get(SETTINGS_OVERLAY_FILES SYSBUILD LOCAL)
endif()
if (NOT DEFINED SETTINGS_OVERLAY_FILES OR SETTINGS_OVERLAY_FILES STREQUAL "")
  zephyr_file(CONF_FILES ${APPLICATION_CONFIG_DIR}/boards SETTINGS SETTINGS_OVERLAY_FILES SUFFIX ${FILE_SUFFIX})
  zephyr_file(CONF_FILES ${APPLICATION_CONFIG_DIR}/socs SETTINGS SETTINGS_OVERLAY_FILES QUALIFIERS SUFFIX ${FILE_SUFFIX})
endif()
if (NOT DEFINED SETTINGS_OVERLAY_FILES OR SETTINGS_OVERLAY_FILES STREQUAL "")
  zephyr_file(CONF_FILES ${APPLICATION_CONFIG_DIR} SETTINGS SETTINGS_OVERLAY_FILES)
endif()
if (NOT DEFINED SETTINGS_OVERLAY_FILES OR SETTINGS_OVERLAY_FILES STREQUAL "")
  zephyr_file(CONF_FILES ${APPLICATION_CONFIG_DIR} SETTINGS SETTINGS_OVERLAY_FILES NAMES "app" SUFFIX ${FILE_SUFFIX})
endif()

# 5. extra devicetree overlays and configuration files from the command line
#    or environment (EXTRA_SETTINGS_OVERLAY_FILES).
zephyr_get(EXTRA_SETTINGS_OVERLAY_FILES SYSBUILD LOCAL)

set(SETTINGS_OVERLAY_FILES ${SETTINGS_OVERLAY_FILES} CACHE STRING "If desired, \
you can build the application using the settings specified in an alternate \
.overlay (devicetree) and .config.yaml (configuration) file using this \
parameter on the command line. These settings will replace the setting \
overlays found in the application directory (boards/socs subdirectories and \
app.{overlay|yaml}). Multiple files may be listed, e.g. \
SETTINGS_OVERLAY_FILES=\"dts1.overlay dts2.overlay net.config.yaml\". If \
multiple files are given those will be merged.")

# Merge all sources of settings files (lowest to highest priority):
zephyr_get(SETTINGS_FILES VAR EXTRA_SETTINGS_OVERLAY_FILES SETTINGS_OVERLAY_FILES SETTINGS_FILES MERGE REVERSE)
zephyr_boilerplate_watch(SETTINGS_FILES)

zephyr_get(EXTRA_CONF_FILE SYSBUILD LOCAL VAR EXTRA_CONF_FILE OVERLAY_CONFIG MERGE REVERSE)
zephyr_boilerplate_watch(EXTRA_CONF_FILE)

zephyr_get(DTS_EXTRA_CPPFLAGS SYSBUILD LOCAL MERGE REVERSE)
zephyr_get(CONFIG_EXTRA_CPPFLAGS SYSBUILD LOCAL MERGE REVERSE)
build_info(application source-dir VALUE ${APPLICATION_SOURCE_DIR})
build_info(application configuration-dir VALUE ${APPLICATION_CONFIG_DIR})
