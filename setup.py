
import os
from setuptools import setup

from invoke import run
from pipenv.utils import convert_deps_to_pip
from pipenv.project import Project


# Access to pipfile
# NOTE Shouldn't need to chdir (and default is True)
pipfile = Project(chdir=False).parsed_pipfile


setup(
    # Essentials
    name='npmenv',
    version=os.environ['NPMENV_VERSION'],
    py_modules=['npmenv'],

    # Get dependencies from Pipfile (does not include dev packages)
    # NOTE `r=False` prevents a requirements file being created and returned
    install_requires=convert_deps_to_pip(pipfile['packages'], r=False),

    # Add empty marker file to identity package as being typed
    package_data={'npmenv': ['py.typed']},

    # Support Pipfile version and future minor releases (but not major)
    python_requires='~={}'.format(pipfile['requires']['python_version']),

    # Auto create platform-specific script to run the CLI
    entry_points={'console_scripts': ['npmenv = npmenv:_cli']},

    # Metadata
    author='shadow-light',
    author_email='42055707+shadow-light@users.noreply.github.com',
    description=("A wrapper for npm that stores node_modules outside of project and provides easy access to them."),  # noqa: E501 WARN Also hard-coded in GitHub and README.md
    long_description=run('python dev.py doc').stdout,
    long_description_content_type='text/markdown',
    license='MIT',
    url='https://github.com/shadow-light/npmenv',
)
