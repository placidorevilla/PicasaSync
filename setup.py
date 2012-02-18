#! /usr/bin/env python
# vim: set fileencoding=utf8 :

import os
from setuptools import setup

def read(fname):
    return open(os.path.join(os.path.dirname(__file__), fname)).read()

setup(
    name = "PicasaSync",
    version = "0.1",
    author = "Plácido Revilla",
    author_email = "placido.revilla@gmail.com",
    description = ("Sync a local directory of albums with your picasa account"),
    license = "Unlicense",
    keywords = "picasa sync photo album",
    url = "http://github.com/placidorevilla/PicasaSync",
    packages = ['PicasaSync'],
    install_requires = ['googlecl'],
    long_description = read('README'),
    classifiers = [
        "Development Status :: 3 - Alpha",
        "Environment :: Console",
        "Topic :: Utilities",
    ],
    entry_points = {
    'console_scripts': [
        'picasasync = PicasaSync:main',
    ],
},
)
