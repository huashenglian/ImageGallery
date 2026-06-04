from setuptools import setup, Extension

ext = Extension(
    "_thumbnail",
    sources=["src/thumbnail.cpp"],
    libraries=["windowscodecs", "ole32"],
    language="c++",
    extra_compile_args=["/std:c++17", "/O2"],
)

setup(
    name="cpp_ext_thumbnail",
    version="0.1.0",
    ext_modules=[ext],
)
