#!/usr/bin/env python3
"""Build a separate validator against the existing HJ Tree-sitter checkout."""
import argparse
from pathlib import Path
import shutil
import subprocess
import sys


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--tree-sitter',type=Path,
                   default=Path.home()/'PL/codecompletion_benchmarks/smallbasic/tree-sitter')
    p.add_argument('--output',type=Path,
                   default=Path(__file__).resolve().parent/'bin/validate_span')
    args=p.parse_args()
    core=args.tree_sitter.expanduser().resolve()
    output=args.output.expanduser().resolve()
    if output.exists():
        raise FileExistsError('Validator sudah ada; gunakan output baru jika ingin rebuild.')
    compiler=shutil.which('gcc')
    if not compiler:
        raise ValueError('gcc tidak ditemukan di Ubuntu-D.')
    for name in ['lib/include/tree_sitter/api.h','lib/src/lib.c']:
        if not (core/name).is_file():
            raise FileNotFoundError(core/name)
    output.parent.mkdir(parents=True,exist_ok=True)
    temporary=output.with_suffix('.building')
    if temporary.exists():
        raise FileExistsError(temporary)
    command=[compiler,'-std=c11','-D_DEFAULT_SOURCE','-O2',
             '-I',str(core/'lib/include'),'-I',str(core/'lib/src'),
             str(Path(__file__).resolve().parent/'validate_span.c'),
             str(core/'lib/src/lib.c'),'-ldl','-pthread','-o',str(temporary)]
    try:
        subprocess.run(command,check=True)
        temporary.rename(output)
    finally:
        temporary.unlink(missing_ok=True)
    print('Validator siap:',output)
    print('Source HJ tidak diubah. Tidak ada API call.')


if __name__=='__main__':
    try:main()
    except (ValueError,OSError,subprocess.CalledProcessError) as exc:
        print('ERROR:',exc,file=sys.stderr);sys.exit(1)
