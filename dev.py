""" Test and release tasks """

import os
import sys
import json
from uuid import uuid4
from urllib import request
from pathlib import Path
from getpass import getpass
from tempfile import TemporaryDirectory
from contextlib import contextmanager

from invoke import task, Program, Collection


# UTILS


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
    inv.run('flake8 .')
    # NOTE mypy separated from flake8 as flake8-mypy was buggy (and no 3.7 support)
    inv.run('mypy npmenv.py npmenv_test.py')  # Can't use glob


@task
def test_unit(inv, pdb=False, failed=False):
    """ Run unit tests """
    pdb = '--pdb' if pdb else ''
    failed = '--last-failed' if failed else ''  # Only run tests that previously failed
    inv.run(f'pytest {pdb} {failed} .')


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
    test_version = version + '-' + str(uuid4())[:6]
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
        with inv.cd(str(tmpdir)):
            # Install npmenv from test PyPI
            import_npmenv = 'run python -c "import npmenv"'
            assert sub_pipenv(import_npmenv, warn=True, hide='both').failed
            sub_pipenv('install appdirs')  # Can't get from test PyPI
            pypi_mirror = 'https://test.pypi.org/simple/'
            sub_pipenv(f'install npmenv=={test_version} --pypi-mirror {pypi_mirror}')
            sub_pipenv(import_npmenv)  # Should now be able to import
            # Confirm npmenv executable works
            assert sub_pipenv('run npmenv env-list').ok
            # Copy in tests and run
            Path('npmenv_test.py').write_text(tests_path.read_text())
            sub_pipenv('install pytest')
            sub_pipenv('run pytest npmenv_test.py')

    # Tag commit with version
    inv.run('git tag --sign')

    # Produce package for real PyPI and upload
    package(inv, version)
    inv.run(twine_cmd)


# CLI


program = Program('source', Collection(test, test_lint, test_unit, package, release))
if __name__ == '__main__':
    program.run()
