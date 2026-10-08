"""Task generation pipeline: GLM-5.1 authors tasks, Docker validates them, GLM-5.3-flash probes them, a gate ships them.

Each batch slot authors a task from a versioned prompt (generator/taskgen/authoring.py), validates it in Docker with
GLM-5.1 repairs (repair.py, docker_checks.py), probes it with five GLM-5.3-flash attempts (probing.py), routes the
result through the GLM-5.1 fairness gate or the 0/5 audit (gate.py, candidate.py), and records its verdict. What the
loop learns lives in learning.py; batches, resume and the command line in cli.py.

This module re-exports generator/taskgen/ and is the command-line entry point, so `python3 generator/fast_author.py`
and `from generator import fast_author` keep working.
"""
import sys
import time  # noqa: F401  (tests patch fast_author.time)
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from generator.taskgen.core import *  # noqa: E402,F401,F403
from generator.taskgen.taskfiles import *  # noqa: E402,F401,F403
from generator.taskgen.docker_checks import *  # noqa: E402,F401,F403
from generator.taskgen.learning import *  # noqa: E402,F401,F403
from generator.taskgen.families import *  # noqa: E402,F401,F403
from generator.taskgen.grid_family import *  # noqa: E402,F401,F403
from generator.taskgen.authoring import *  # noqa: E402,F401,F403
from generator.taskgen.repair import *  # noqa: E402,F401,F403
from generator.taskgen.probing import *  # noqa: E402,F401,F403
from generator.taskgen.tuning import *  # noqa: E402,F401,F403
from generator.taskgen.gate import *  # noqa: E402,F401,F403
from generator.taskgen.candidate import *  # noqa: E402,F401,F403
from generator.taskgen.cli import *  # noqa: E402,F401,F403
from generator.taskgen.core import robust, store  # noqa: E402,F401

if __name__ == "__main__":
    main()
