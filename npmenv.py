
import os
import sys
import subprocess
from shutil import rmtree
from typing import Union, Sequence
from base64 import urlsafe_b64encode
from pathlib import Path
from hashlib import sha256
from contextlib import contextmanager

from appdirs import user_data_dir


# CUSTOM TYPES


Path_or_str = Union[Path, str]


# MODULE LEVEL


__version__ = 'dev'


NPMENV_DIR = Path(user_data_dir('npmenv', 'shadow-light'))


class NpmenvException(Exception):
    pass


# PRIVATE


@contextmanager
def _cd(path:Path_or_str) -> None:
    """ Temporarily change to a certain dir """
    cwd = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(cwd)


def _get_env_id(proj_dir:Path) -> str:
    """ Return env id for the given project dir """
    # WARN Only take Path for arg as hash would change if e.g. trailing newline in str
    assert proj_dir.is_absolute()
    hash = sha256(str(proj_dir).encode()).digest()
    hash_sample = urlsafe_b64encode(hash).decode()[:8]
    return f'{proj_dir.name}-{hash_sample}'


def _get_env_dir(proj_dir:Path) -> Path:
    """ Return path of env dir for given project dir """
    return NPMENV_DIR.joinpath(_get_env_id(proj_dir))


def _resolve_proj_dir(given_proj_dir:Path_or_str=None) -> Path:
    """ Return a resolved Path obj for given project dir (defaulting to CWD)

    WARN Should use for any user-given path to ensure env id consistent

    """
    if given_proj_dir is None:
        given_proj_dir = Path.cwd()
    return Path(given_proj_dir).resolve()


def _cli() -> None:
    """ Process argv and wrap npm or execute custom command """
    cmd = sys.argv[1]

    # Special case: help command prints npmenv commands and then hands over to npm
    if cmd in ('help', '--help', '-h'):
        help = (
            f"npmenv {__version__}",
            "env-list            List all currently existing environments",
            "env-location        Output path to env for current dir (may not exist yet)",
            "env-run cmd [args]  Run command with env's bin dir in start of PATH",
            "env-rm [env_id]     Remove the env for current dir (or env with given id)",
            "----------",
        )
        print('\n'.join(help) + '\n')

    # Run npmenv commands, otherwise handing over to npm
    try:
        if cmd == 'env-list':
            if len(sys.argv) > 2:
                sys.exit("env-list doesn't take any arguments")
            for env_id, proj_dir in env_list():
                print(f'{env_id}: {proj_dir}')
        elif cmd == 'env-location':
            if len(sys.argv) > 2:
                sys.exit("env-location doesn't take any arguments")
            # NOTE No trailing newline so scripts can use without needing to strip
            print(env_location(), end='')
        elif cmd == 'env-run':
            if len(sys.argv) < 3:
                sys.exit("env-run requires a command to be given")
            env_run(sys.argv[2:])
        elif cmd == 'env-rm':
            if len(sys.argv) > 3:
                sys.exit("env-rm was given too many arguments")
            proj_dir = env_rm(None if len(sys.argv) < 3 else sys.argv[2])
            print(f"Removed environment for {proj_dir}")
        else:
            env_npm(sys.argv[1:])
    except NpmenvException as exc:
        sys.exit(exc)


# PUBLIC


def env_npm(args:Sequence, proj_dir:Path_or_str=None) -> None:
    """ Execute npm with given args in env dir of given project dir """

    # Determine paths
    proj_dir = _resolve_proj_dir(proj_dir)
    proj_config = proj_dir.joinpath('package.json')
    proj_lock = proj_dir.joinpath('package-lock.json')
    env_dir = _get_env_dir(proj_dir)
    env_config = env_dir.joinpath('package.json')
    env_lock = env_dir.joinpath('package-lock.json')
    env_pathfile = env_dir.joinpath('.project')

    # Init env dir if doesn't exist
    if not env_dir.exists():
        env_dir.mkdir(parents=True)
        env_pathfile.write_text(str(proj_dir))

    # Adjust to any changes to project file existance
    # WARN `exists()` returns false for broken symlinks
    for pf, ef in ((proj_config, env_config), (proj_lock, env_lock)):
        if pf.exists():
            # Symlink to project file if haven't already
            if not ef.is_symlink():
                ef.symlink_to(pf)
        else:
            # Since project file doesn't exist, remove symlink if it was created
            if ef.is_symlink():
                ef.unlink()

    # Execute npm in env dir
    with _cd(env_dir):
        subprocess.run(['npm', *args])

    # If config/lock files have just been created, move to project and symlink
    for pf, ef in ((proj_config, env_config), (proj_lock, env_lock)):
        if not pf.exists() and ef.exists():
            ef.rename(target=pf)
            ef.symlink_to(pf)


def env_rm(identifier:Path_or_str=None) -> str:
    """ Remove the env for given project dir or env id (defaults to CWD) """

    # Get env id from project path if not given
    if isinstance(identifier, str):
        env_id = identifier
    else:
        env_id = _get_env_id(_resolve_proj_dir(identifier))

    # Determine env dir
    env_dir = NPMENV_DIR / env_id
    if not env_dir.exists():
        # Raise helpful exception
        join = 'with id' if isinstance(identifier, str) else 'for dir'
        value = identifier if identifier else env_dir
        raise NpmenvException(f"No env exists {join} {value}")

    # Do some double checks since this is a dangerous operation
    assert env_dir.is_absolute()
    assert env_dir.parent == NPMENV_DIR

    # Remove the env dir (returning project dir for the env as a str)
    proj_dir = env_dir.joinpath('.project').read_text()
    rmtree(env_dir)
    return proj_dir


def env_list() -> list:
    """ Return list of npmenv ids and their corresponding project dirs """
    envs = []
    for item in NPMENV_DIR.iterdir():
        # NOTE Ignores any files or dirs that don't have a .project file
        path_file = item.joinpath('.project')
        if path_file.is_file():
            envs.append((item.name, path_file.read_text()))
    return envs


def env_location(proj_dir:Path_or_str=None) -> Path:
    """ Return env dir path for given project dir

    NOTE The env may not exist yet; this just reports where it would be if it did

    """
    return _get_env_dir(_resolve_proj_dir(proj_dir))


def env_run(args:Sequence, proj_dir:Path_or_str=None) -> None:
    """ Run a command with node_modules/.bin at start of PATH environment variable

    NOTE If node is installed as a package then it should be used to run scripts
        WARN Scripts may depend on system binaries so should not clear existing PATH value

    """

    # Get path to env's .bin
    proj_dir = _resolve_proj_dir(proj_dir)
    env_dir = _get_env_dir(proj_dir)
    if not env_dir.is_dir():
        raise NpmenvException("Env does not exist (run `npmenv install`)")
    bin_dir = env_dir / 'node_modules/.bin'
    if not bin_dir.is_dir():
        raise NpmenvException(
            "Env does not have a .bin dir (install a package with an executable first)")

    # Copy env variables and add the env's bin dir to start of PATH
    process_env = os.environ.copy()
    process_env['PATH'] = str(bin_dir) + os.pathsep + process_env['PATH']

    # Run the given args with the modified env
    subprocess.run(args, env=process_env)


# EXECUTE


if __name__ == '__main__':
    _cli()
