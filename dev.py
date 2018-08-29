""" Test and release tasks """

from invoke import task, Program, Collection


@task
def test(inv):
    test_lint(inv)
    test_unit(inv)


@task
def test_lint(inv):
    inv.run('flake8 .')
    # NOTE mypy separated from flake8 as flake8-mypy was buggy (and no 3.7 support)
    inv.run('mypy npmenv.py npmenv_test.py')  # Can't use glob


@task
def test_unit(inv, pdb=False, failed=False):
    pdb = '--pdb' if pdb else ''
    failed = '--last-failed' if failed else ''  # Only run tests that previously failed
    inv.run(f'pytest {pdb} {failed} .', pty=True)


@task
def release(inv):
    raise NotImplementedError()


program = Program('dev', Collection(test, test_lint, test_unit, release))
if __name__ == '__main__':
    program.run()
