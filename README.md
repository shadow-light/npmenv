# npmenv

A wrapper for npm that stores node_modules outside of project and provides easy access to them.

`npmenv` is a Python module inspired by `pipenv` in that it stores packages outside of projects (in an OS-specific dir) to avoid cluttering projects. It also has a `run` command that puts `node_modules/.bin` in `PATH` before running the given command. If you `install node` in a project then it will have the added benefit of using that node version to run your code and third-party scripts. You can then lock down your node version per-project and upgrade them individually when desired.

__Install:__ `pip install npmenv`
__Supports:__ All platforms (Linux, MacOS, Windows)
__Requires:__ Python 3.6+

[Source](https://github.com/shadow-light/npmenv)
[Documentation](https://pypi.org/project/npmenv/)
