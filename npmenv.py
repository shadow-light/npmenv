
import sys
import subprocess
from shutil import rmtree
from base64 import urlsafe_b64encode
from pathlib import Path
from hashlib import sha256

from appdirs import user_data_dir


__version__ = '0'


NPMENV_DIR = Path(user_data_dir('npmenv', 'shadow-light'))


class NpmenvException(Exception):
    pass


def _get_env_id(proj_dir):
    """ Return env id for the given project dir """
    hash = urlsafe_b64encode(sha256(str(proj_dir)).digest())[:8]
    return f'{proj_dir.name}-{hash}'


def _get_env_dir(proj_dir):
    """ Return path of env dir for given project dir """
    return NPMENV_DIR.joinpath(_get_env_id(proj_dir))


def _resolve_proj_dir(given_proj_dir=None):
    """ Return a resolved Path obj for given project dir (defaulting to CWD)

    WARN Should use for any user-given path to ensure env id consistent

    """
    if given_proj_dir is None:
        given_proj_dir = Path.cwd()
    return Path(given_proj_dir).resolve()


def _cli():
    """ Process argv and wrap npm or execute custom command """
    cmd = sys.argv[1]

    # Special case: help command prints npmenv commands and then hands over to npm
    if cmd == 'help':
        print(f"npmenv [{__version__}]\nenv-list\n")

    # Run npmenv commands, otherwise handing over to npm
    if cmd == 'env-list':
        env_list()
    else:
        npm(sys.argv[1:])


def npm(args, proj_dir=None):
    """ Execute npm with given args in env dir of given project dir """

    # Determine paths
    proj_dir = _resolve_proj_dir(proj_dir)
    proj_config = proj_dir.joinpath('package.json')
    proj_lock = proj_dir.joinpath('package-lock.json')
    env_dir = _get_env_dir(proj_dir)
    env_config = env_dir.joinpath('package.json')
    env_lock = env_dir.joinpath('package-lock.json')
    env_pathfile = env_dir.joinpath('PROJECT_PATH')

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
    with env_dir:
        subprocess.run(['npm'] + args)

    # If config/lock files have just been created, move to project and symlink
    for pf, ef in ((proj_config, env_config), (proj_lock, env_lock)):
        if not pf.exists() and ef.exists():
            ef.rename(target=pf)
            ef.symlink_to(pf)


def env_rm(env_id=None):
    """ Remove the env for current dir, or for id if given """
    exc_suffix = f"with id {env_id}" if env_id else "for current dir"
    if not env_id:
        env_id = _get_env_id(_resolve_proj_dir())
    env_dir = NPMENV_DIR / env_id
    if env_dir.exists():
        # Do some double checks since this is a dangerous operation
        assert env_dir.is_absolute()
        assert env_dir.parent == NPMENV_DIR
        assert env_dir.joinpath('PROJECT_PATH').is_file()
        rmtree(env_dir)
    else:
        raise NpmenvException(f"No env exists {exc_suffix}")


def env_list():
    """ Print list of npmenv ids and their corresponding target dirs """
    for item in NPMENV_DIR.iterdir():
        # NOTE Ignores any files or dirs that don't have a PROJECT_PATH file
        path_file = item.joinpath('PROJECT_PATH')
        if path_file.is_file():
            path = path_file.read_text()
            print(f'{item.name}: {path}')


def env_location(proj_dir=None):
    """ Print env dir path for given project dir (without ending newline) """
    print(_get_env_dir(_resolve_proj_dir(proj_dir)), end='')


def env_run():
    pass


if __name__ == '__main__':
    _cli()
