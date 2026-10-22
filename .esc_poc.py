import os
import pathlib
import subprocess
import sys

target = pathlib.Path(sys.argv[1]).resolve()
sb = pathlib.Path(sys.argv[2]).resolve()

inner = (
    "import os, pathlib\n"
    "p = pathlib.Path(r'%s')\n"
    "os.chmod(p, 0o600)\n"
    "p.write_text('def test_vacuous():' + chr(10) + '    assert True' + chr(10))\n"
    "print('wrote', p)\n" % str(target)
)

r = subprocess.run([sys.executable, "-c", inner], cwd=str(sb),
                   capture_output=True, text=True)
print("rc:", r.returncode)
print("stdout:", r.stdout.strip())
print("stderr:", r.stderr.strip()[:300])
print("pristine content after ->", repr(target.read_text()))
