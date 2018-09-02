
import sys
import json
import platform
from pathlib import Path
from contextlib import contextmanager

import pytest

import npmenv


# TEST DATA


EXAMPLE_PACKAGE = 'to-no-case'  # Tiny package with no dependencies
PACKAGE_JSON = '''{
  "private": true,
  "dependencies": {"to-no-case": "1.0.2"}
}'''
LOCK_JSON = '''{
  "requires": true,
  "lockfileVersion": 1,
  "dependencies": {
    "to-no-case": {
      "version": "1.0.2",
      "resolved": "https://registry.npmjs.org/to-no-case/-/to-no-case-1.0.2.tgz",
      "integrity": "sha1-xyKQcWTvaxeBMsjmmTAhLRtKoWo="
    }
  }
}'''


# UTILS


@contextmanager
def assert_exit_with_success(success):
    """ Contextmanager for asserting block raises SystemExit with success True/False """
    with pytest.raises(SystemExit) as exc:
        yield
    assert success == (exc.value.code in (None, 0))


# FIXTURES


@pytest.fixture(autouse=True)
def sandbox(request, tmpdir_factory, monkeypatch):
    """ Provide tmp dirs for project/envs and update CWD/NPMENV_DIR respectively

    NOTE Apply to all tests to avoid accidental filesystem changes
    WARN Even if some methods don't touch filesystem now, they may in future!

    """

    # Do nothing if test marked with 'sandbox_disable'
    # NOTE Only use for tests that do not touch filesystem and need to check real paths
    if 'sandbox_disable' in request.keywords:
        return

    # Create tmp project
    proj_dir = tmpdir_factory.mktemp('npmenv_test_project')
    proj_dir = Path(str(proj_dir))

    # Create tmp envs location
    envs = tmpdir_factory.mktemp('npmenv_test_envs')
    envs = Path(str(envs))

    # Override NPMENV_DIR and change into project dir (monkeypatch undoes these later)
    monkeypatch.setattr(npmenv, 'NPMENV_DIR', envs)
    monkeypatch.chdir(str(proj_dir))

    # Provide paths
    # NOTE pytest will handle cleanup
    # WARN Can't mix above `return` with a `yield` here, so beware if doing custom cleanup
    env_dir = envs / npmenv._get_env_id(proj_dir)
    return {
        'envs': envs,
        'proj_dir': proj_dir,
        'proj_package': proj_dir / 'package.json',
        'proj_lock': proj_dir / 'package-lock.json',
        'env_dir': env_dir,
        'env_package': env_dir / 'package.json',
        'env_lock': env_dir / 'package-lock.json',
        'env_module': env_dir / 'node_modules' / EXAMPLE_PACKAGE,
    }


@pytest.fixture()
def insert_project_files(sandbox):
    """ Fixture factory for inserting config files into tmp project dir """
    def inner(package=False, lock=False):
        if package:
            sandbox['proj_package'].write_text(PACKAGE_JSON)
        if lock:
            sandbox['proj_lock'].write_text(LOCK_JSON)
        return sandbox  # Forward sandbox paths as own return value
    return inner


@pytest.fixture()
def fake_project():
    """ Provide paths for a fake project that doesn't override NPMENV_DIR """
    proj_dir = '/tmp/fake'
    env_id = 'fake-SHCEzZKG'
    data_dir = '.local/share'
    if platform.system() == 'Darwin':
        data_dir = 'Library/Application Support'
    if platform.system() == 'Windows':
        proj_dir = 'C:' + proj_dir
        env_id = 'fake-60Sq7Ynp'
        data_dir = 'AppData/Local/shadow-light'
    return {
        'proj_dir': Path(proj_dir),
        'env_id': env_id,
        'env_dir': Path(Path.home(), data_dir, 'npmenv', env_id),
    }


# PRIVATE


def test__cd(sandbox):
    assert Path.cwd() != sandbox['envs']
    with npmenv._cd(sandbox['envs']):
        assert Path.cwd() == sandbox['envs']
    assert Path.cwd() != sandbox['envs']


@pytest.mark.sandbox_disable
def test__get_env_id(fake_project):
    assert npmenv._get_env_id(fake_project['proj_dir']) == fake_project['env_id']


@pytest.mark.sandbox_disable
def test__get_env_dir(fake_project):
    assert npmenv._get_env_dir(fake_project['proj_dir']) == fake_project['env_dir']


def test__resolve_proj_dir(sandbox):
    # Test passing no arg (sandbox cd'd into project dir already)
    assert npmenv._resolve_proj_dir() == sandbox['proj_dir']
    # Test passing a relative path
    with npmenv._cd(sandbox['proj_dir'].parent):
        relative_to_parent = sandbox['proj_dir'].relative_to(sandbox['proj_dir'].parent)
        assert npmenv._resolve_proj_dir(relative_to_parent) == sandbox['proj_dir']


class TestCli:
    """ Should call methods for respective arguments and exit/print as result

    NOTE Tests should be simple as most things tested within the method tests already

    """

    def _patch_argv(self, monkeypatch, args):
        """ Patch argv and give dud first arg (script path not used by `_cli()`) """
        monkeypatch.setattr(sys, 'argv', [None, *args])

    def test_env_list(self, sandbox, monkeypatch, capfd):
        # Trigger env creation
        npmenv.env_npm('help')
        capfd.readouterr()
        # Test
        self._patch_argv(monkeypatch, ['env-list'])
        npmenv._cli()
        assert str(sandbox['proj_dir']) in capfd.readouterr().out

    def test_env_cleanup(self, monkeypatch, sandbox, insert_project_files, tmpdir):
        # Test two projects, only one having config files
        proj1 = str(sandbox['proj_dir'])
        proj2 = str(tmpdir)
        insert_project_files(package=True)  # Into proj1 (sandbox)
        npmenv.env_npm(proj_dir=proj1)
        npmenv.env_npm(proj_dir=proj2)
        self._patch_argv(monkeypatch, ['env-cleanup'])
        npmenv._cli()
        stdout = capfd.readouterr().out
        assert proj1 not in stdout
        assert proj2 in stdout

    def test_env_location(self, monkeypatch, sandbox, capfd):
        self._patch_argv(monkeypatch, ['env-location'])
        npmenv._cli()
        assert str(sandbox['env_dir']) == capfd.readouterr().out

    def test_env_rm(self, monkeypatch):
        # Confirm exit if removing env that doesn't exist (also test arg taking)
        self._patch_argv(monkeypatch, ['env-rm', '/tmp/fake'])
        with pytest.raises(SystemExit):
            npmenv._cli()
        # Confirm no exit if removing env dir that does exist (also test no arg)
        npmenv.env_npm('help')
        self._patch_argv(monkeypatch, ['env-rm'])
        npmenv._cli()

    def test_env_run(self, monkeypatch):
        # Just test failure due to env not existing (success case tested elsewhere)
        self._patch_argv(monkeypatch, ['env-run', 'node'])
        with assert_exit_with_success(False):
            npmenv._cli()

    def test_npm(self, monkeypatch, capfd):
        # Confirm calls npm and adds own help info to npm's
        self._patch_argv(monkeypatch, ['help'])
        with assert_exit_with_success(True):
            npmenv._cli()
        stdout = capfd.readouterr().out
        assert 'npmenv' in stdout  # Own help text
        assert 'publish' in stdout  # npm's help text

    def test_args(self, monkeypatch):
        # Confirm failure when wrong amount of args
        for args in (['env-list', 0], ['env-location', 0], ['env-run'], ['env-rm', 0, 0]):
            self._patch_argv(monkeypatch, args)
            with pytest.raises(SystemExit):
                npmenv._cli()


# PUBLIC


class TestEnvNpm:
    """ `env_npm` should link to existing config, call npm, and transfer new config """

    def test_no_files_init(self, sandbox):
        """ `env_npm` should transfer new package file created by npm init """
        npmenv.env_npm('init --yes').check_returncode()
        assert sandbox['env_package'].resolve(strict=True) == sandbox['proj_package']
        assert not sandbox['env_lock'].is_symlink()

    def test_no_files_install(self, sandbox):
        """ `env_npm` should transfer new lock file created by npm install """
        npmenv.env_npm(f'install {EXAMPLE_PACKAGE}').check_returncode()
        assert sandbox['env_lock'].resolve(strict=True) == sandbox['proj_lock']
        assert sandbox['env_module'].is_dir()

    def test_only_package(self, insert_project_files):
        """ `env_npm` should use existing package file """
        sandbox = insert_project_files(package=True)
        npmenv.env_npm('install').check_returncode()
        # Confirm original file not modified
        assert json.loads(sandbox['proj_package'].read_text()) == json.loads(PACKAGE_JSON)
        # Confirm links created
        assert sandbox['env_package'].resolve(strict=True) == sandbox['proj_package']
        assert sandbox['env_lock'].resolve(strict=True) == sandbox['proj_lock']
        # Confirm module installed
        assert sandbox['env_module'].is_dir()

    def test_both_files(self, insert_project_files):
        """ `env_npm` should use existing package file """
        sandbox = insert_project_files(package=True, lock=True)
        npmenv.env_npm('install').check_returncode()
        # Confirm original files not modified
        assert json.loads(sandbox['proj_package'].read_text()) == json.loads(PACKAGE_JSON)
        assert json.loads(sandbox['proj_lock'].read_text()) == json.loads(LOCK_JSON)
        # Confirm links
        assert sandbox['env_package'].resolve() == sandbox['proj_package']
        assert sandbox['env_lock'].resolve() == sandbox['proj_lock']
        # Confirm module installed
        assert sandbox['env_module'].is_dir()

    def test_unlink(self, insert_project_files):
        """ `env_npm` should remove links to files that don't exist anymore """
        sandbox = insert_project_files(package=True, lock=True)
        # Trigger linking
        npmenv.env_npm('help')
        assert sandbox['env_package'].is_symlink()
        assert sandbox['env_lock'].is_symlink()
        # Remove originals
        sandbox['proj_package'].unlink()
        sandbox['proj_lock'].unlink()
        # Confirm links removed
        npmenv.env_npm('help')
        assert not sandbox['env_package'].is_symlink()
        assert not sandbox['env_lock'].is_symlink()


def test_env_rm(sandbox):
    # Helper
    def rm_with_checks(*args):
        assert not npmenv.env_list()
        npmenv.env_npm('help')
        assert npmenv.env_list()
        npmenv.env_rm(*args)
        assert not npmenv.env_list()

    # Test different args
    rm_with_checks()
    rm_with_checks(sandbox['proj_dir'])
    rm_with_checks(npmenv._get_env_id(sandbox['proj_dir']))

    # Test failure when env doesn't exist
    assert not npmenv.env_list()
    with pytest.raises(npmenv.NpmenvException):
        npmenv.env_rm()


def test_env_list():
    # Fresh env so should be none
    assert not npmenv.env_list()
    # Trigger creating env for CWD
    npmenv.env_npm('help')
    # Ensure list gives new env
    assert npmenv.env_list()[0][1] == str(Path.cwd())


def test_env_cleanup(sandbox, insert_project_files, tmpdir_factory):
    # Put lock file in sandbox project
    insert_project_files(lock=True)
    npmenv.env_npm()

    # Create additional project with no config
    npmenv.env_npm(proj_dir=str(tmpdir_factory.mktemp()))

    # Create additional project and then delete
    proj3 = Path(str(tmpdir_factory.mktemp()))
    npmenv.env_npm(proj_dir=proj3)
    proj3.rmdir()

    # Confirm issues
    issues = sorted([env[2] for env in env_list()])
    assert issues == [None, 'missing', 'no_config']

    # Remove envs with issues
    removed = env_cleanup()

    # Confirm only removed envs with issues
    assert len(removed) == 2
    assert None not in (env[2] for env in removed)


def test_env_location(sandbox):
    # Simple test (just made of already tested private methods anyway)
    env_dir = npmenv.env_location()
    env_dir.relative_to(sandbox['envs'])  # Raises ValueError if can't
    assert env_dir.name.startswith(sandbox['proj_dir'].name + '-')


def test_env_run(sandbox, capfd):
    # Confirm exception if no bin dir
    with pytest.raises(npmenv.NpmenvException):
        npmenv.env_run('username --help')
    npmenv.env_npm('help')
    with pytest.raises(npmenv.NpmenvException):
        npmenv.env_run('username --help')
    # Confirm runs executable from .bin dir
    # Install a tiny CLI program that shouldn't exist on system yet
    npmenv.env_npm('install "username-cli@2.0.0"').check_returncode()
    capfd.readouterr()
    npmenv.env_run('username --help').check_returncode()
    assert 'sindresorhus' in capfd.readouterr().out
