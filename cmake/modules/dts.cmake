# SPDX-License-Identifier: Apache-2.0

include_guard(GLOBAL)

include(extensions)
include(python)
include(boards)
include(pre_dt)
find_package(HostTools)
find_package(Dtc 1.4.6)

# This module makes information from the devicetree available to
# various build stages, as well as to other arbitrary Python scripts:
#
#   - To Zephyr and application source code files, as a C macro API
#     defined in <zephyr/devicetree.h>
#
#   - To other arbitrary Python scripts (like twister) using a
#     serialized edtlib.EDT object in Python's pickle format
#     (https://docs.python.org/3/library/pickle.html)
#
#   - To users as a final devicetree source (DTS) file which can
#     be used for debugging
#
#   - To CMake files, after this module has finished running, using
#     devicetree extensions defined in cmake/modules/extensions.cmake
#
#   - To Kconfig files, both using some Kconfig symbols we generate
#     here as well as the extension functions defined in
#     scripts/kconfig/kconfigfunctions.py
#
# See the specific API documentation for each of these cases for more
# information on what is currently available to you.
#
# We rely on the C preprocessor, the devicetree python package, and
# files in scripts/dts to make all this work. We also optionally will
# run the dtc tool if it is found, in order to catch any additional
# warnings or errors it generates.
#
# Outcome:
#
# 1. The following has happened:
#
#    - The pre_dt module has been included; refer to its outcome
#      section for more information on the consequences
#    - SETTINGS_FILES: set to the path(s) of the base devicetree and
#      configuration files of the board.
#    - ${BINARY_DIR_INCLUDE_GENERATED}/devicetree_generated.h exists
#
# 2. The following has happened if a devicetree was found and
#    no errors occurred:
#
#    - CACHED_DTS_ROOT_BINDINGS is set in the cache to the
#      value of DTS_ROOT_BINDINGS
#    - DTS_ROOT_BINDINGS is set to a ;-list of locations where DT
#      bindings were found
#    - ${PROJECT_BINARY_DIR}/zephyr.dts exists
#    - ${PROJECT_BINARY_DIR}/edt.pickle exists
#    - ${KCONFIG_BINARY_DIR}/Kconfig.dts exists
#    - DTS_INCLUDE_FILES is set to a ;-list of all devicetree files
#      used in this build, including transitive includes (the build
#      system will be regenerated if any of those files change
#    - the devicetree extensions in the extensions.cmake module
#      will be ready for use in other CMake list files that run
#      after this module
#
# Required variables:
# - SETTINGS_FILES: the devicetree and configuration source file(s)
#   to use. The first file must be a DTS file (including the /dts-v1/ header);
#   the rest must be .overlay files.
# - BINARY_DIR_INCLUDE_GENERATED: where to put generated include files
# - DTS_ROOT: a deduplicated list of places where devicetree
#   implementation files (like bindings, vendor prefixes, etc.) are
#   found
# - DTS_ROOT_SYSTEM_INCLUDE_DIRS: set to "PATH1 PATH2 ...",
#   with one path per potential location where C preprocessor #includes
#   may be found for devicetree files
# - KCONFIG_BINARY_DIR: where to put generated Kconfig files
#
# Optional variables:
# - CMAKE_DTS_PREPROCESSOR: the path to the preprocessor to use
#   for devicetree files
# - EXTRA_DTC_FLAGS: list of extra command line options to pass to
#   dtc when using it to check for additional errors and warnings;
#   invalid flags are automatically filtered out of the list
# - DTS_EXTRA_CPPFLAGS: extra command line options to pass to the
#   C preprocessor when generating the devicetree from DTS_SOURCE
#
# Variables set by this module and not mentioned above are for internal
# use only, and may be removed, renamed, or re-purposed without prior notice.

# The directory containing devicetree related scripts.
set(DT_SCRIPTS                  ${ZEPHYR_BASE}/scripts/dts)

# This parses and collects the DT information
set(GEN_EDT_SCRIPT              ${DT_SCRIPTS}/gen_edt.py)
# This generates DT information needed by the C macro APIs,
# along with a few other things.
set(GEN_DEFINES_SCRIPT          ${DT_SCRIPTS}/gen_defines.py)
# The edtlib.EDT object in pickle format.
set(EDT_PICKLE                  ${PROJECT_BINARY_DIR}/edt.pickle)
# The generated file containing the final DTS, for debugging.
set(ZEPHYR_DTS                  ${PROJECT_BINARY_DIR}/zephyr.dts)
# The generated C header needed by <zephyr/devicetree.h>
set(DEVICETREE_GENERATED_H      ${BINARY_DIR_INCLUDE_GENERATED}/devicetree_generated.h)
# Generated build system internals.
set(DTS_POST_CPP                ${PROJECT_BINARY_DIR}/zephyr.dts.pre)
set(DTS_DEPS                    ${PROJECT_BINARY_DIR}/zephyr.dts.d)
set(CONFIG_POST_CPP             ${PROJECT_BINARY_DIR}/zephyr.settings.pre.yaml)

# This generates DT information needed by the Kconfig APIs.
set(GEN_DRIVER_KCONFIG_SCRIPT   ${DT_SCRIPTS}/gen_driver_kconfig_dts.py)
# Generated Kconfig symbols go here.
set(DTS_KCONFIG                 ${KCONFIG_BINARY_DIR}/Kconfig.dts)

# This generates DT information needed by the CMake APIs.
set(GEN_DTS_CMAKE_SCRIPT        ${DT_SCRIPTS}/gen_dts_cmake.py)
# The generated information itself, which we include() after
# creating it.
set(DTS_CMAKE                   ${PROJECT_BINARY_DIR}/dts.cmake)

# The location of a file containing known vendor prefixes, relative to
# each element of DTS_ROOT. Users can define their own in their own
# modules.
set(VENDOR_PREFIXES             dts/bindings/vendor-prefixes.txt)

if(NOT DEFINED SETTINGS_FILES OR SETTINGS_FILES STREQUAL "")
  message(FATAL_ERROR "SETTINGS_FILES must be defined")
  build_info(devicetree user-files PATH ${DTC_OVERLAY_FILE_AS_LIST})
endif()

zephyr_list(TRANSFORM SETTINGS_FILES NORMALIZE_PATHS
            OUTPUT_VARIABLE SETTINGS_FILES_AS_LIST)
  build_info(devicetree extra-user-files PATH ${EXTRA_DTC_OVERLAY_FILE_AS_LIST})

set(i 0)
foreach(settings_file IN ITEMS ${SETTINGS_FILES_AS_LIST})
  if(i EQUAL 0)
    message(STATUS "Found BOARD.dts: ${settings_file}")
    list(APPEND DTS_SETTINGS_FILES ${settings_file})
  elseif(settings_file MATCHES "\.ya?ml$")
    message(STATUS "Found configuration overlay: ${settings_file}")
    list(APPEND CONFIG_SETTINGS_FILES ${settings_file})
  else()
    message(STATUS "Found devicetree overlay: ${settings_file}")
    list(APPEND DTS_SETTINGS_FILES ${settings_file})
  endif()

  math(EXPR i "${i}+1")
endforeach()

unset(DTS_ROOT_BINDINGS)
foreach(dts_root ${DTS_ROOT})
  set(bindings_path ${dts_root}/dts/bindings)
  if(EXISTS ${bindings_path})
    list(APPEND
      DTS_ROOT_BINDINGS
      ${bindings_path}
      )
  endif()

  set(vendor_prefixes ${dts_root}/${VENDOR_PREFIXES})
  if(EXISTS ${vendor_prefixes})
    list(APPEND EXTRA_GEN_EDT_ARGS --vendor-prefixes ${vendor_prefixes})
  endif()
endforeach()

# Cache the location of the root bindings so they can be used by
# scripts which use the build directory.
set(CACHED_DTS_ROOT_BINDINGS ${DTS_ROOT_BINDINGS} CACHE INTERNAL
  "DT bindings root directories")

#
# Run the C preprocessor on the devicetree source, so we can parse it
# (using the Python devicetree package) in later steps.
#

# TODO: Cut down on CMake configuration time by avoiding
# regeneration of devicetree_generated.h on every configure. How
# challenging is this? Can we cache the dts dependencies?

# Run the preprocessor on the DTS input files.
if(DEFINED CMAKE_DTS_PREPROCESSOR)
  set(dts_preprocessor ${CMAKE_DTS_PREPROCESSOR})
else()
  set(dts_preprocessor ${CMAKE_C_COMPILER})
endif()
zephyr_dt_preprocess(
  CPP ${dts_preprocessor}
  SOURCE_FILES ${DTS_SETTINGS_FILES}
  OUT_FILE ${DTS_POST_CPP}
  DEPS_FILE ${DTS_DEPS}
  EXTRA_CPPFLAGS ${DTS_EXTRA_CPPFLAGS}
  INCLUDE_DIRECTORIES ${DTS_ROOT_SYSTEM_INCLUDE_DIRS}
  WORKING_DIRECTORY ${APPLICATION_SOURCE_DIR}
  )
zephyr_dt_preprocess(
  CPP ${dts_preprocessor}
  SOURCE_FILES ${CONFIG_SETTINGS_FILES}
  OUT_FILE ${CONFIG_POST_CPP}
  DEPS_FILE ${DTS_DEPS}
  EXTRA_CPPFLAGS ${DTS_EXTRA_CPPFLAGS}
  INCLUDE_DIRECTORIES ${DTS_ROOT_SYSTEM_INCLUDE_DIRS}
  WORKING_DIRECTORY ${APPLICATION_SOURCE_DIR}
  )

#
# Make sure we re-run CMake if any devicetree sources or transitive
# includes change.
#

# Parse the generated dependency file to find the DT sources that
# were included, including any transitive includes.
toolchain_parse_make_rule(${DTS_DEPS}
  DTS_INCLUDE_FILES # Output parameter
  )

# Add the results to the list of files that, when change, force the
# build system to re-run CMake.
set_property(DIRECTORY APPEND PROPERTY
  CMAKE_CONFIGURE_DEPENDS
  ${DTS_INCLUDE_FILES}
  ${GEN_EDT_SCRIPT}
  ${GEN_DEFINES_SCRIPT}
  ${GEN_DRIVER_KCONFIG_SCRIPT}
  ${GEN_DTS_CMAKE_SCRIPT}
  )

#
# Run GEN_EDT_SCRIPT.
#

string(REPLACE ";" " " EXTRA_DTC_FLAGS_RAW "${EXTRA_DTC_FLAGS}")
set(CMD_GEN_EDT ${PYTHON_EXECUTABLE} ${GEN_EDT_SCRIPT}
--dts ${DTS_POST_CPP}
--dtc-flags '${EXTRA_DTC_FLAGS_RAW}'
--bindings-dirs ${DTS_ROOT_BINDINGS}
--dts-out ${ZEPHYR_DTS}.new # for debugging and dtc
--edt-pickle-out ${EDT_PICKLE}.new
${EXTRA_GEN_EDT_ARGS}
)

execute_process(
  COMMAND ${CMD_GEN_EDT}
  WORKING_DIRECTORY ${PROJECT_BINARY_DIR}
  COMMAND_ERROR_IS_FATAL ANY
  )
zephyr_file_copy(${ZEPHYR_DTS}.new ${ZEPHYR_DTS} ONLY_IF_DIFFERENT)
zephyr_file_copy(${EDT_PICKLE}.new ${EDT_PICKLE} ONLY_IF_DIFFERENT)
file(REMOVE ${ZEPHYR_DTS}.new ${EDT_PICKLE}.new)
message(STATUS "Generated zephyr.dts: ${ZEPHYR_DTS}")
message(STATUS "Generated pickled edt: ${EDT_PICKLE}")

#
# Run GEN_DEFINES_SCRIPT.
#

set(CMD_GEN_DEFINES ${PYTHON_EXECUTABLE} ${GEN_DEFINES_SCRIPT}
--header-out ${DEVICETREE_GENERATED_H}.new
--edt-pickle ${EDT_PICKLE}
${EXTRA_GEN_DEFINES_ARGS}
)

execute_process(
  COMMAND ${CMD_GEN_DEFINES}
  WORKING_DIRECTORY ${PROJECT_BINARY_DIR}
  COMMAND_ERROR_IS_FATAL ANY
  )
zephyr_file_copy(${DEVICETREE_GENERATED_H}.new ${DEVICETREE_GENERATED_H} ONLY_IF_DIFFERENT)
file(REMOVE ${ZEPHYR_DTS}.new ${DEVICETREE_GENERATED_H}.new)
message(STATUS "Generated zephyr.dts: ${ZEPHYR_DTS}")
message(STATUS "Generated devicetree_generated.h: ${DEVICETREE_GENERATED_H}")

#
# Run GEN_DRIVER_KCONFIG_SCRIPT.
#

execute_process(
  COMMAND ${PYTHON_EXECUTABLE} ${GEN_DRIVER_KCONFIG_SCRIPT}
  --kconfig-out ${DTS_KCONFIG}
  --bindings-dirs ${DTS_ROOT_BINDINGS}
  WORKING_DIRECTORY ${PROJECT_BINARY_DIR}
  RESULT_VARIABLE ret
  )
if(NOT "${ret}" STREQUAL "0")
  message(FATAL_ERROR "gen_driver_kconfig_dts.py failed with return code: ${ret}")
endif()

#
# Run GEN_DTS_CMAKE_SCRIPT.
#

execute_process(
  COMMAND ${PYTHON_EXECUTABLE} ${GEN_DTS_CMAKE_SCRIPT}
  --edt-pickle ${EDT_PICKLE}
  --cmake-out ${DTS_CMAKE}
  WORKING_DIRECTORY ${PROJECT_BINARY_DIR}
  RESULT_VARIABLE ret
  )
if(NOT "${ret}" STREQUAL "0")
  message(FATAL_ERROR "gen_dts_cmake.py failed with return code: ${ret}")
else()
  message(STATUS "Including generated dts.cmake file: ${DTS_CMAKE}")
  include(${DTS_CMAKE})
endif()

#
# Run dtc if it was found.
#
# This is just to generate warnings and errors; we discard the output.
#

if(DTC)

set(DTC_WARN_UNIT_ADDR_IF_ENABLED "")
check_dtc_flag("-Wunique_unit_address_if_enabled" check)
if (check)
  set(DTC_WARN_UNIT_ADDR_IF_ENABLED "-Wunique_unit_address_if_enabled")
endif()

set(DTC_NO_WARN_UNIT_ADDR "")
check_dtc_flag("-Wno-unique_unit_address" check)
if (check)
  set(DTC_NO_WARN_UNIT_ADDR "-Wno-unique_unit_address")
endif()

set(VALID_EXTRA_DTC_FLAGS "")
foreach(extra_opt ${EXTRA_DTC_FLAGS})
  check_dtc_flag(${extra_opt} check)
  if (check)
    list(APPEND VALID_EXTRA_DTC_FLAGS ${extra_opt})
  endif()
endforeach()
set(EXTRA_DTC_FLAGS ${VALID_EXTRA_DTC_FLAGS})

execute_process(
  COMMAND ${DTC}
  -O dts
  -o - # Write output to stdout, which we discard below
  -b 0
  -E unit_address_vs_reg
  ${DTC_NO_WARN_UNIT_ADDR}
  ${DTC_WARN_UNIT_ADDR_IF_ENABLED}
  ${EXTRA_DTC_FLAGS} # User settable
  ${ZEPHYR_DTS}
  OUTPUT_QUIET # Discard stdout
  WORKING_DIRECTORY ${PROJECT_BINARY_DIR}
  RESULT_VARIABLE ret
  ERROR_VARIABLE stderr
  )

if(NOT "${ret}" STREQUAL "0")
  message(FATAL_ERROR "dtc failed with return code: ${ret}")
elseif(stderr)
  # dtc printed warnings on stderr but did not fail.
  # Display them as CMake warnings to draw attention.
  message(WARNING "dtc raised one or more warnings:\n${stderr}")
endif()
endif(DTC)

build_info(devicetree files PATH ${dts_files})
build_info(devicetree include-dirs PATH ${DTS_ROOT_SYSTEM_INCLUDE_DIRS})
build_info(devicetree bindings-dirs PATH ${DTS_ROOT_BINDINGS})
