import os
import sys
import shlex
import subprocess
from typing import Union, Sequence, Generator, Any
from base64 import urlsafe_b64encode
from pathlib import Path
from hashlib import sha256
from contextlib import contextmanager


# CUSTOM TYPES
Path_or_str = Union[Path, str]


# MODULE LEVEL

__version__: str = "source"  # Replaced when packaged

HELP: str = f"""
npmenv {__version__}

*any npm command*
"""

NPMENV_DIR: Path = Path(os.getenv("NODE_PATH", ".venv/lib/node_modules/"))


class NpmenvException(Exception):
    """ Exception for npmenv-related issues """


# PRIVATE


@contextmanager
def _cd(path: Path_or_str) -> Generator[Path, None, None]:
    """ Temporarily change to a certain dir """
    path = Path(path)
    cwd = Path.cwd()
    os.chdir(path)
    try:
        yield path
    finally:
        os.chdir(cwd)


def _shell(args: str, **kwargs: Any) -> subprocess.CompletedProcess:
    """ Run a command in a shell for cross-platform support """
    # WARN If shell=False on Windows then must give full path to the executable!
    return subprocess.run(args, shell=True, **kwargs)


def _args_to_str(args: Sequence[str]) -> str:
    """ Take list of args and return string that can safely be executed """
    return " ".join([shlex.quote(arg) for arg in args])


def _get_env_id(proj_dir: Path) -> str:
    """ Return env id for the given project dir """
    # WARN Only take Path for arg as hash would change if e.g. trailing newline in str
    assert proj_dir.is_absolute()
    hash = sha256(str(proj_dir).encode()).digest()
    hash_sample = urlsafe_b64encode(hash).decode()[:8]
    return f"{proj_dir.parent.name}__{proj_dir.name}__{hash_sample}"


def _get_env_dir(proj_dir: Path) -> Path:
    """ Return path of env dir for given project dir """
    return NPMENV_DIR.joinpath(_get_env_id(proj_dir))


def _resolve_proj_dir(given_proj_dir: Path_or_str = None) -> Path:
    """ Return a resolved Path obj for given project dir (defaulting to CWD)

    Should use for any user-given path to ensure env id consistent
    WARN Path may not exist (as is the case in `env_rm`)

    """
    if given_proj_dir is None:
        given_proj_dir = Path.cwd()
    return Path(given_proj_dir).resolve()


def _cli() -> None:  # noqa: C901 (complexity)
    """ Process argv and wrap npm or execute custom command """
    cmd = None if len(sys.argv) == 1 else sys.argv[1]

    # Special case: help command prints npmenv commands and then hands over to npm
    if cmd in (None, "help", "--help", "-h", "-?"):
        print(HELP + "\n----------\n\n")
        sys.exit(0)

    # Run npmenv commands, otherwise handing over to npm
    try:
        args = _args_to_str(sys.argv[1:])
        if args.startswith("npm"):
            result = env_npm(args)
        else:
            result = env_run(args)

    except NpmenvException as exc:
        sys.exit(exc)

    else:
        # Reflect return code of subprocess in own exit
        sys.exit(result.returncode)


# PUBLIC


def env_npm(
    args: str = "", proj_dir: Path_or_str = None
) -> subprocess.CompletedProcess:
    """ Execute npm with given args in env dir of given project dir """

    # Determine paths
    proj_dir = _resolve_proj_dir(proj_dir)
    proj_config = proj_dir.joinpath("package.json")
    proj_lock = proj_dir.joinpath("package-lock.json")
    env_dir = _get_env_dir(proj_dir)
    env_config = env_dir.joinpath("package.json")
    env_lock = env_dir.joinpath("package-lock.json")
    env_pathfile = env_dir.joinpath(".project")

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
        elif ef.is_symlink():
            ef.unlink()

    # Execute npm in env dir
    with _cd(env_dir):
        result = _shell(f"npm {args}")

    # If config/lock files have just been created, move to project and symlink
    for pf, ef in ((proj_config, env_config), (proj_lock, env_lock)):
        if not pf.exists() and ef.exists():
            ef.rename(target=pf)
            ef.symlink_to(pf)

    # Return result of subprocess
    return result


def env_run(
    args: str, proj_dir: Path_or_str = None, run_kwargs: Any = {}
) -> subprocess.CompletedProcess:
    """ Run a command with node_modules/.bin at start of PATH environment variable """

    # NOTE If node is installed as a package then it should be used to run scripts
    # WARN Scripts may depend on system binaries so should not clear existing PATH value

    # Get path to env's .bin
    proj_dir = _resolve_proj_dir(proj_dir)
    env_dir = _get_env_dir(proj_dir)
    if not env_dir.is_dir():
        raise NpmenvException("Env does not exist (run `npmenv install`)")
    bin_dir = env_dir / "node_modules" / ".bin"
    if not bin_dir.is_dir():
        raise NpmenvException(
            "Env does not have a .bin dir (install a package with an executable first)"
        )

    # Copy env variables and add the env's bin dir to start of PATH
    process_env = os.environ.copy()
    process_env["PATH"] = os.path.join(bin_dir, os.pathsep, process_env["PATH"])

    # Run the given args with the modified env
    return _shell(args, env=process_env, **run_kwargs)


if __name__ == "__main__":
    _cli()
