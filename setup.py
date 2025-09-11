#!/usr/bin/env python3
"""
Setup script for kick-monitor package.
Supports editable installation for development.
"""

from setuptools import setup, find_packages
import os

# Read the contents of README file
this_directory = os.path.abspath(os.path.dirname(__file__))
with open(os.path.join(this_directory, "README.md"), encoding="utf-8") as f:
    long_description = f.read()

setup(
    name="kick-monitor",
    use_scm_version=True,
    setup_requires=["setuptools_scm"],
    description="Real-time monitoring service for Kick.com streamer status",
    long_description=long_description,
    long_description_content_type="text/markdown",
    author="Kick Monitor Team",
    author_email="team@example.com",
    url="https://github.com/example/kick-monitor",
    packages=find_packages(where="src"),
    package_dir={"": "src"},
    python_requires=">=3.11",
    install_requires=[
        "asyncio-mqtt>=0.16.0",
        "websockets>=12.0",
        "psycopg[binary]>=3.1.0", 
        "pydantic>=2.5.0",
        "python-dotenv>=1.0.0",
        "rich>=13.7.0",
        "aiohttp>=3.9.0",
        "click>=8.1.0",
    ],
    extras_require={
        "dev": [
            "pytest>=7.4.0",
            "pytest-asyncio>=0.21.0",
            "pytest-cov>=4.1.0",
            "black>=23.0.0",
            "flake8>=6.0.0",
            "mypy>=1.7.0",
            "pre-commit>=3.5.0",
        ],
        "test": [
            "pytest>=7.4.0",
            "pytest-asyncio>=0.21.0",
            "pytest-mock>=3.12.0",
            "httpx>=0.25.0",
        ],
    },
    entry_points={
        "console_scripts": [
            "kick-monitor=src.cli.main:main",
        ],
    },
    classifiers=[
        "Development Status :: 4 - Beta",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: MIT License",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
        "Operating System :: OS Independent",
        "Topic :: System :: Monitoring",
        "Topic :: Internet :: WWW/HTTP :: Dynamic Content",
    ],
    keywords="kick streaming monitor websocket real-time",
    project_urls={
        "Bug Reports": "https://github.com/example/kick-monitor/issues",
        "Source": "https://github.com/example/kick-monitor",
        "Documentation": "https://github.com/example/kick-monitor/blob/main/README.md",
    },
)