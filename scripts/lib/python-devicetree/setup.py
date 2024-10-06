# Copyright (c) 2021, Nordic Semiconductor ASA
# Copyright (c) 2024, The Zephyr Project
#
# SPDX-License-Identifier: Apache-2.0

import setuptools

version = "0.1.0"

setuptools.setup(
    author="The Zephyr Project, a Linux Foundation project",
    author_email="devel@lists.zephyrproject.org",
    name="devicetree",
    version=version,
    description="Python library to parse and validate devicetree source files",
    long_description=open("README.md").read(),
    long_description_content_type="text/markdown",
    url="https://github.com/zephyrproject/zephyr/scripts/lib/python-devicetree",
    package_dir={"": "src"},
    classifiers=[
        "Programming Language :: Python :: 3 :: Only",
        "License :: OSI Approved :: Apache Software License",
        "Operating System :: POSIX :: Linux",
        "Operating System :: MacOS :: MacOS X",
        "Operating System :: Microsoft :: Windows",
    ],
    install_requires=[
        "PyYAML>=6.0",
    ],
    python_requires=">=3.6",
)
