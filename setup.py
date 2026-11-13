from setuptools import setup, find_packages

from version import __version__

NAME = 'VideoLingo'

with open('requirements.txt', encoding='utf-8') as f:
    # requirements.txt 含说明性注释与空行。setuptools 会把每一行都当作
    # PEP 508 依赖解析，必须过滤，否则 install_requires 含非法条目，
    # `pip install -e .` / egg_info 会失败或生成畸形元数据。
    requirements = [
        line.strip() for line in f
        if line.strip() and not line.lstrip().startswith('#')
    ]

setup(
    name=NAME,
    version=__version__,
    packages=find_packages(include=[NAME, f'{NAME}.*']),
    install_requires=requirements
)
