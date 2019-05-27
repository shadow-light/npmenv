import os
import sys
import shlex
import subprocess
from shutil import rmtree
from typing import Union, Sequence, Generator, Any, List, Tuple, Optional
from base64 import urlsafe_b64encode
from pathlib import Path
from hashlib import sha256
from contextlib import contextmanager

from appdirs import user_data_dir


# CUSTOM TYPES


Path_or_str = Union[Path, str]


# MODULE LEVEL


__version__: str = "source"  # Replaced when packaged


HELP: str = f"""
npmenv {__version__}

env-list            List all currently existing environments
env-location        Output path to env for current dir (may not exist yet)
env-modules [name]  List items in node_modules (recursive if package name given)
env-run cmd [args]  Run command with env's bin dir in start of PATH
env-rm [env_id]     Remove the env for current dir (or env with given id)
env-cleanup         Remove envs for projects that no longer exist
*any npm command*
"""


NPMENV_DIR: Path = Path(user_data_dir("npmenv", "shadow-light"))


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


def _list_all_files(root: Path_or_str) -> List[Path]:
    """ Return list of files found recursively in given path """
    files = []
    for dirpath, dirnames, filenames in os.walk(root):
        for filename in filenames:
            files.append(Path(dirpath).joinpath(filename))
    return files


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
    env_args = sys.argv[2:]

    # Special case: help command prints npmenv commands and then hands over to npm
    if cmd in (None, "help", "--help", "-h"):
        print(HELP + "\n----------\n\n")

    # Helper for issues
    def issue_to_str(issue: str = None) -> str:
        if issue == "missing":
            return "(no longer exists)"
        if issue == "no_config":
            return "(no package.json or lock)"
        return issue or ""

    # Exit if args given to argless commands
    if cmd in ("env-list", "env-cleanup", "env-location") and env_args:
        sys.exit(f"{cmd} doesn't take any arguments")

    # Run npmenv commands, otherwise handing over to npm
    try:
        if cmd == "env-list":
            for env_id, proj_dir, issue in env_list():
                print(f"{env_id}: {proj_dir} {issue_to_str(issue)}")

        elif cmd == "env-cleanup":
            print("The following environments have been removed:")
            for env_id, proj_dir, issue in env_cleanup():
                print(f"{env_id}: {proj_dir} {issue_to_str(issue)}")

        elif cmd == "env-location":
            # NOTE No trailing newline so scripts can use without needing to strip
            print(env_location(), end="")

        elif cmd == "env-modules":
            if len(env_args) > 1:
                sys.exit("env-modules was given too many arguments")
            root = env_location() / "node_modules"
            if env_args:
                root = root / env_args[0]
            if not root.exists():
                sys.exit(f"Does not exist: {root}")
            print(f"Found in {root}\n")
            paths = _list_all_files(root) if env_args else root.iterdir()
            paths_as_strings = [str(p.relative_to(root)) for p in sorted(paths)]
            print("\n".join(paths_as_strings))

        elif cmd == "env-run":
            if not env_args:
                sys.exit("env-run requires a command to be given")
            result = env_run(_args_to_str(env_args))
            # Reflect return code of subprocess in own exit
            sys.exit(result.returncode)

        elif cmd == "env-rm":
            if len(env_args) > 1:
                sys.exit("env-rm was given too many arguments")
            proj_dir = env_rm(*env_args)
            print(f"Removed environment for {proj_dir}")

        else:
            result = env_npm(_args_to_str(sys.argv[1:]))
            # Reflect return code of subprocess in own exit
            sys.exit(result.returncode)

    except NpmenvException as exc:
        sys.exit(exc)


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
        else:
            # Since project file doesn't exist, remove symlink if it was created
            if ef.is_symlink():
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


def env_rm(identifier: Path_or_str = None) -> Path:
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
        join = "with id" if isinstance(identifier, str) else "for dir"
        value = identifier if identifier else env_dir
        raise NpmenvException(f"No env exists {join} {value}")

    # Do some double checks since this is a dangerous operation
    assert env_dir.is_absolute()
    assert env_dir.parent == NPMENV_DIR

    # Remove the env dir (returning project dir for the env as a str)
    proj_dir = Path(env_dir.joinpath(".project").read_text())
    rmtree(env_dir)
    return proj_dir


def env_cleanup() -> List[Tuple[str, Path, str]]:
    """ Remove envs for projects that no longer exist (no package or lock file) """
    removed = []
    for env_id, env_dir, issue in env_list():
        if issue:
            env_rm(env_id)
            removed.append((env_id, env_dir, issue))
    return removed


def env_list() -> List[Tuple[str, Path, Optional[str]]]:
    """ Return list of tuples (env id, project dir, issue with project existance) """
    envs = []
    for item in NPMENV_DIR.iterdir():
        # NOTE Ignores any files or dirs that don't have a .project file
        path_file = item.joinpath(".project")
        if path_file.is_file():
            # Determine paths from path file
            proj_dir = Path(path_file.read_text())
            proj_config = proj_dir.joinpath("package.json")
            proj_lock = proj_dir.joinpath("package-lock.json")
            # Also return if any issue with project
            issue = None
            if not proj_dir.is_dir():
                issue = "missing"
            elif not proj_config.is_file() and not proj_lock.is_file():
                issue = "no_config"
            # Add to list
            envs.append((item.name, proj_dir, issue))
    return envs


def env_location(proj_dir: Path_or_str = None) -> Path:
    """ Return env dir path for given project dir (may/may not exist yet) """
    return _get_env_dir(_resolve_proj_dir(proj_dir))


def env_run(
    args: str, proj_dir: Path_or_str = None, run_kwargs: Any = {}
) -> subprocess.CompletedProcess:
    """ Run a command with node_modules/.bin at start of PATH environment variable """

    # NOTE If node is installed as a package then it should be used to run scripts
    # WARN Scripts may depend on system binaries so should not clear existing PATH value

    # Get path to env's .bin
    proj_dir = _resolve_proj_dir(proj_dir)
    env_dir = _get_env_dir(proj_dir)
    print(f"env_dir: {env_dir}")  # TODO: Remove later on
    if not env_dir.is_dir():
        raise NpmenvException("Env does not exist (run `npmenv install`)")
    bin_dir = env_dir / "node_modules" / ".bin"
    if not bin_dir.is_dir():
        raise NpmenvException(
            "Env does not have a .bin dir (install a package with an executable first)"
        )

    # Copy env variables and add the env's bin dir to start of PATH
    process_env = os.environ.copy()
    new_path = str(bin_dir) + os.pathsep + process_env["PATH"]
    print(f"new_path: {new_path}")
    new_path_joined = os.path.join(bin_dir, os.pathsep, process_env["PATH"])
    print(f"new_path via os.path.join: {new_path_joined}")
    process_env["PATH"] = str(bin_dir) + os.pathsep + process_env["PATH"]  # TODO

    # Run the given args with the modified env
    return _shell(args, env=process_env, **run_kwargs)


if __name__ == "__main__":
    _cli()
