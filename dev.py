""" Test and release tasks """

from invoke import task, Program, Collection


@task
def test(inv):
    test_lint(inv)
    test_unit(inv)


@task
def test_lint(inv):
    inv.run('flake8 .')


@task
def test_unit(inv):
    inv.run('pytest .')


@task
def release(inv):
    raise NotImplentedError()


program = Program('dev', Collection(test, test_lint, test_unit, release))
if __name__ == '__main__':
    program.run()
