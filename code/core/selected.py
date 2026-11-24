"""Record the PID that will become the selected simulator ELF."""
import os
from pathlib import Path
import sys

Path('target.pid').write_text(str(os.getpid()))
os.execv(sys.argv[1],sys.argv[1:])
