""" Development tasks """

import os
import sys
import json
import pydoc
import shlex
import inspect
from random import randint
from urllib import request
from pathlib import Path
from getpass import getpass
from tempfile import TemporaryDirectory
from contextlib import contextmanager

from invoke import task, Program, Collection


# UTILS


@contextmanager
def _cd(path):
    """ Temporarily change to a certain dir """
    path = Path(path)
    cwd = Path.cwd()
    os.chdir(path)
    try:
        yield path
    finally:
        os.chdir(cwd)


def _documentation(inv):
    """ Return auto-generated documentation in Markdown """

    # Start with README.md
    doc = Path('README.md').read_text() + '\n\n'

    # Rename link name for PyPI (as misleading when viewed on PyPI itself)
    assert doc.count('[Documentation]') == 1
    doc = doc.replace('[Documentation]', '[PyPI package]')

    # Add help text
    import npmenv
    doc += f"## CLI usage\n```{npmenv.HELP}```\n\n"

    # Add API documentation
    doc += "## Module API\n```\n"
    for name, value in inspect.getmembers(npmenv):
        # Skip any builtin or imported members
        # NOTE This also ignores anything without a __module__ attribute (e.g. variables)
        if getattr(value, '__module__', None) != 'npmenv':
            continue
        # Skip any private members
        if name.startswith('_'):
            continue
        # Print only the docstring for the exception (rather than all methods)
        if name == 'NpmenvException':
            exc_doc = value.__doc__.strip()
            doc += f'class NpmenvException(builtins.Exception)\n    {exc_doc}\n\n'
            continue
        # Hand rendering over to pydoc
        doc += pydoc.plaintext.document(value) + '\n'

    # Close doc block
    doc += '```\n\n'

    # Add version history
    versions_cmd = 'git tag --list "*.*.*" -n99 --sort "-version:refname"'
    history = inv.run(versions_cmd, hide='both').stdout
    doc += f'## Version history\n```\n{history}```\n'

    # Done
    return doc


def _get_ci_status(commit):
    """ Return CI status as boolean for given commit (None if not finished) """
    url = f'https://api.github.com/repos/shadow-light/npmenv/commits/{commit}/status'
    headers = {'Accept': 'application/vnd.github.v3+json'}
    resp = request.urlopen(request.Request(url, headers=headers))
    state = json.loads(resp.read().decode())['state']
    return None if state == 'pending' else state == 'success'


def _get_new_version(last_str):
    """ Return new version number by bumping the requested version level """

    # Special case of first version
    if not last_str:
        print("This is the first release!")
        version = input("Please enter the first version number: ")
        assert len(version.split('.')) == 3
        return version

    # Inform user of last version
    print(f"Last version is: {last_str}")

    # Convert the given version string to digits
    last_digits = (int(n) for n in last_str.split('.'))
    assert len(last_digits) == 3

    # Loop until user bumps version correctly
    while True:
        digits = list(last_digits)

        # Bump the chosen level
        levels = ('major', 'minor', 'patch')
        level = input(f"Is this release {'/'.join(levels)}? ")
        if level not in levels:
            print("Incorrect level given")
            continue
        level = levels.index(level)
        digits[level] += 1
        version = '.'.join(digits)

        # Confirm version is correct
        if input(f'Is {version} correct? (y/n): ') == 'y':
            return version


@contextmanager
def _set_version_in_module(version):
    """ Temporarily change the version in npmenv.py for packaging """
    path = Path('npmenv.py')
    original = path.read_text()
    version_line_old = "__version__ = 'source'"
    version_line_new = f"__version__ = '{version}'"
    versioned = original.replace(version_line_old, version_line_new, 1)
    assert version_line_new in versioned
    path.write_text(versioned)
    try:
        yield
    finally:
        path.write_text(original)


# TASKS


@task
def test(inv, python=None):
    """ Run all tests """
    if python:
        # Require a certain version of Python
        # NOTE Mainly for CI where pyenv/pipenv may silently fallback on diff version
        python = tuple(int(n) for n in python.split('.'))
        if sys.version_info[:len(python)] != python:
            sys.exit(f"Python {python} required, but {sys.version_info} used")
    test_lint(inv)
    test_unit(inv)


@task
def test_lint(inv):
    """ Run lint and type tests """
    # NOTE mypy separated from flake8 as flake8-mypy was buggy (and no 3.7 support)
    inv.run('flake8 .')
    for file in Path().glob('**/*.py'):  # Mypy doesn't support globbing
        inv.run(f'mypy {file}')


@task
def test_unit(inv, pdb=False, failed=False):
    """ Run unit tests """
    pdb = '--pdb' if pdb else ''
    failed = '--last-failed' if failed else ''  # Only run tests that previously failed
    inv.run(f'pytest {pdb} {failed} .')


@task
def doc(inv):
    """ Print documentation """
    print(_documentation(inv))


@task
def package(inv, version=None):
    """ Package module for distribution """

    # Generate a version number based on commits if none given
    if not version:
        version = inv.run('git describe --always --dirty').stdout.strip()

    # Modify env of setup.py
    env_override = {
        # Pass version to setup.py via env
        'NPMENV_VERSION': version,
        # Undo pipenv preventing pyc file creation
        # NOTE Will be removed later, see https://git.io/fA8fl
        'PYTHONDONTWRITEBYTECODE': '',
    }

    # Remove old files
    for file in Path('dist').iterdir():
        if file.suffix in ('.whl', '.gz', '.asc'):
            file.unlink()
    assert len(list(Path('dist').iterdir())) == 0

    # Set the version in actual module, package, then unset it
    with _set_version_in_module(version):
        inv.run('python setup.py sdist bdist_wheel', env=env_override)

    # Cleanup tmp files created by setup.py
    inv.run('python setup.py clean --all', env=env_override)
    inv.run('rm -R npmenv.egg-info')

    # Confirm expected packages created
    assert len(list(Path('dist').iterdir())) == 2


@task
def release(inv):
    """ Release a new version of the module """

    # Helper to get git stdout
    def git_out(cmd):
        result = inv.run(f'git {cmd}', warn=True, pty=False, hide='both')
        if result.failed:
            return None
        return result.stdout.strip()

    # Confirm no uncommited changes
    if git_out('status --porcelain'):
        sys.exit("Commit all changes before release")

    # Confirm on master branch
    if git_out('symbolic-ref --short HEAD') != 'master':
        sys.exit("Can only release from master branch")

    # Confirm there are changes since last version
    if git_out('describe --exact-match'):
        sys.exit("No commits since last release")

    # Confirm all commits have been pushed (so CIs can test)
    if git_out('log origin/master..master'):
        sys.exit("Push latest changes so CIs can test them")

    # Confirm all CI builds passed
    ci_status = _get_ci_status(git_out('rev-parse HEAD'))
    if ci_status is None:
        sys.exit("CI tests haven't finished yet")
    if ci_status is False:
        sys.exit("CI tests failed")

    # Determine the new version number
    last_version = git_out('describe --abbrev=0')
    version = _get_new_version(last_version)

    # Produce packages for test PyPI (with random version so can reupload on error)
    test_version = f'{version}.dev{randint(0, 99)}'  # Compatible with PEP 440
    package(inv, test_version)

    # Upload to test pypi
    twine_cmd = 'twine upload --sign --username shadow-light dist/*'  # WARN reused later
    os.environ['TWINE_PASSWORD'] = getpass('PyPI password: ')
    inv.run(twine_cmd + ' --repository-url https://test.pypi.org/legacy/')

    # Form new PATH value for subprocess with current venv removed
    path_without_venv = os.environ['PATH'].split(os.pathsep)
    assert 'virtualenvs' in path_without_venv[0]
    del path_without_venv[0]
    path_without_venv = os.pathsep.join(path_without_venv)

    # Helper for running pipenv commands in sub env
    def sub_pipenv(cmd, **kwargs):
        sub_env = {
            'PATH': path_without_venv,
            'PIP_PYTHON_PATH': '',
            'PIPENV_VENV_IN_PROJECT': '1',  # Store venv in project so removed when done
        }
        return inv.run(f'pipenv {cmd}', env=sub_env, **kwargs)

    # Install and test in a tmpdir (auto-removed)
    tests_path = Path('npmenv_test.py').resolve()
    with TemporaryDirectory() as tmpdir:

        # Work in a subdir since pipenv fails in a subdir of /tmp for some reason
        tmpdir = Path(tmpdir) / 'subdir'
        tmpdir.mkdir()
        with _cd(tmpdir):

            # Confirm module not available
            import_npmenv = 'run python -c "import npmenv"'
            assert sub_pipenv(import_npmenv, warn=True, hide='both').failed

            # Install npmenv from test PyPI
            # NOTE Using `--extra-index-url` so dependencies downloaded from normal index
            # NOTE The required options for install not supported in pipenv, so using pip
            install_args = '--pre --extra-index-url https://test.pypi.org/simple/'
            install_cmd = f'run pip install npmenv=={test_version} {install_args}'
            while True:
                if sub_pipenv(install_cmd, warn=True).ok:
                    break
                if input("Version not available yet. Retry? (y/n): ") != 'y':
                    sys.exit("Release aborted")

            # Confirm can now import the module
            sub_pipenv(import_npmenv)

            # Confirm npmenv executable works
            assert sub_pipenv('run npmenv env-list').ok

            # Copy in tests and run
            Path('npmenv_test.py').write_text(tests_path.read_text())
            sub_pipenv('install pytest')
            sub_pipenv('run pytest npmenv_test.py')

    # Get version message
    print('\n\n\nList of commits since last release:\n\n')
    inv.run('git log --date-order {}..HEAD'.format(last_version or ''))  # Avoid None
    while True:
        msg = input("Version message (used in tag and documentation): ")
        print(msg)
        if input("Are you happy with the above message? (y/n): ") == 'y':
            break

    # Confirm release
    if input(f"All looks good. Do the release? (y/n): ") == 'y':
        # Produce package for real PyPI and upload
        package(inv, version)
        inv.run(twine_cmd)
        # Tag commit with version
        inv.run(f'git tag --sign {version} -m {shlex.quote(msg)}')
    else:
        sys.exit("Release aborted")


# CLI


program = Program('source', Collection(test, test_lint, test_unit, doc, package, release))
if __name__ == '__main__':
    program.run()
