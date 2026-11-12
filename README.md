# Code Completion Lab Benchmark

This project evaluates C and Microsoft Small Basic completions with and without candidate guidance from the HJ Tree-sitter pipeline. For each selected cursor position, the runner generates three completions without guidance and one completion for each of the three highest-ranked HJ candidates.

## 1. Get the code and benchmark assets

- [GitHub repository](https://github.com/burhansa25/code-completion-lab)
- [Benchmark assets on Google Drive](https://drive.google.com/drive/folders/1d95-yj7ysrPYE5agrE9aQ9HM_7niqy9Q?usp=sharing)

These instructions target Ubuntu or Ubuntu under WSL2. Use a Linux environment compatible with the parser and grammar binaries in the archive. Run the commands in Bash inside Ubuntu. For Windows users who already have WSL, open the Ubuntu distribution before running them. For a fresh Windows setup, open PowerShell as administrator and run:

```powershell
wsl --install -d Ubuntu
```

Restart Windows if prompted, then launch Ubuntu and create your Linux user. See [Microsoft's WSL instructions](https://learn.microsoft.com/en-us/windows/wsl/basic-commands).

Download the benchmark archive containing the top-level `PL/` directory. If Drive asks you to request access, ask the archive owner. Extract the archive into your Linux home directory. For a `.tar.gz` archive, paste its **absolute Ubuntu path** when prompted. In WSL, a file in Windows Downloads is typically under `/mnt/c/Users/<Windows-user>/Downloads/`.

```bash
(
  set -e
  read -erp 'Absolute Ubuntu path to the PL .tar.gz archive: ' pl_archive
  tar -tzf "$pl_archive" > /dev/null
  tar -xzf "$pl_archive" -C "$HOME"
)
```

If the archive is a ZIP, use an archive manager and place the extracted `PL` folder in your Linux home directory. The expected paths include:

```text
~/PL/
├── codecompletion_benchmarks/
│   ├── c/TEST/
│   └── smallbasic/
│       ├── code-completion-extension/resources/
│       ├── tree-sitter/reports/
│       └── tree-sitter-smallbasic/SB_Sample/
└── tree-sitter-c/c.so
```

## 2. Install dependencies and clone the repository

```bash
sudo apt update
sudo apt install -y git python3 python3-venv gcc file

git clone https://github.com/burhansa25/code-completion-lab.git "$HOME/code-completion-lab"
cd "$HOME/code-completion-lab"
git checkout deda840

python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -c 'import sacrebleu; print(sacrebleu.__version__)'
```

If you already have a clone, run `git fetch origin` inside it before checking out `deda840`. Use this commit to reproduce the setup documented here.

## 3. Check the local assets

```bash
hj_base="$HOME/PL/codecompletion_benchmarks/smallbasic"

ls -ld "$HOME/PL/codecompletion_benchmarks/c/TEST" \
  "$hj_base/tree-sitter/reports/c" \
  "$hj_base/tree-sitter/reports/smallbasic" \
  "$hj_base/tree-sitter-smallbasic/SB_Sample"

ls -l "$hj_base/code-completion-extension/resources/c/candidates.json" \
  "$hj_base/code-completion-extension/resources/smallbasic/candidates.json" \
  "$hj_base/code-completion-extension/resources/smallbasic/token_mapping.json"

file "$hj_base/tree-sitter/TreeSitterCutFile" \
  "$hj_base/tree-sitter/TreeSitterCutFile.exe" \
  "$hj_base/tree-sitter-smallbasic/smallbasic.so" \
  "$HOME/PL/tree-sitter-c/c.so"
```

C uses `TreeSitterCutFile`. The Small Basic commands below select `TreeSitterCutFile.exe` to match the recorded lab run. The suffix alone does not identify the binary format. If a required parser is a compatible Linux executable but lacks execute permission, restore it with `chmod u+x <path-to-parser>`. If you get `Exec format error`, use a Linux build compatible with your machine.

## 4. Run the C benchmark

### Prepare inputs locally

Preparation reads the original test sources, reference reports, and candidate database. It makes no API calls. From the repository root:

```bash
.venv/bin/python run_hj_c_batch.py prepare --out results/c-full-infill-v1
```

Check `results/c-full-infill-v1/summary.json` for the number of files, preparation failures, and scheduled requests. Use a new output directory if you need to prepare again after a successful run. If your assets are in a different layout, run `.venv/bin/python run_hj_c_batch.py prepare --help` for path overrides.

### Test one file with Grok

This step makes paid API calls. Use your own xAI key. The key is entered without echoing and is available only inside the block.

```bash
(
  set -e
  read -rsp 'xAI API key: ' XAI_API_KEY
  printf '\n'
  export XAI_API_KEY

  .venv/bin/python run_hj_c_batch.py run \
    --out results/c-full-infill-v1 \
    --model grok --delay 0 --limit-files 1
)
```

For GPT-4o, replace `XAI_API_KEY` with `OPENAI_API_KEY` in the block and use `--model gpt4o`. The runner uses the model IDs configured in the pinned commit. Check that your API account can access the selected model.

### Run all prepared files

Remove `--limit-files 1` from the same command after checking the request count and expected API cost. The lab preparation scheduled **262,008 requests per model across 106 files**. The number for your copy is in `summary.json`. The batch runner automatically skips files with completed scores and resumes partial response files if you rerun the same command with the same inputs and model. Keep one runner per model and output directory.

Under `results/c-full-infill-v1/files/`, each file directory contains the prepared cases and provider-specific outputs such as `grok_responses.jsonl` and `grok_scores.csv`. C's batch command generates completions and text scores. Syntax validation is a separate step.

## 5. Run the Small Basic benchmark

### Prepare and map the cases locally

Paste this block from the repository root. It defaults to the Hello World smoke test. To prepare the full dataset, change `sb_scope=smoke` to `sb_scope=full` before running the block.

```bash
(
  set -e
  sb_scope=smoke
  sb_dir="results/sb-${sb_scope}"
  sb_parser="$HOME/PL/codecompletion_benchmarks/smallbasic/tree-sitter/TreeSitterCutFile.exe"

  if [ "$sb_scope" = full ]; then
    selection=(--all)
  else
    selection=(--file 01_HelloWorld.sb)
  fi

  .venv/bin/python prepare_hj_benchmark.py \
    --language smallbasic --executable "$sb_parser" --mode 0 \
    "${selection[@]}" --output "$sb_dir/prepared.jsonl" \
    || [ "$?" -eq 2 ]

  .venv/bin/python - "$sb_dir" <<'PY'
import json
import sys
from collections import Counter
from pathlib import Path

directory = Path(sys.argv[1])
with (directory / "prepared.jsonl").open(encoding="utf-8") as stream:
    rows = [json.loads(line) for line in stream if line.strip()]

errors = [row for row in rows if row["status"] == "error"]
if errors:
    first = errors[0]
    raise SystemExit(f"Preparation failed at {first['source_relative']}: {first['error']}")

eligible = [
    row for row in rows
    if row["status"] == "prepared" and len(row["top_candidates"]) == 3
]
if not eligible:
    raise SystemExit("No eligible cases with exactly three candidates.")

with (directory / "eligible.jsonl").open("x", encoding="utf-8") as stream:
    for row in eligible:
        print(json.dumps(row, ensure_ascii=False), file=stream)

print(f"Preparation statuses: {dict(Counter(row['status'] for row in rows))}")
print(f"Eligible positions: {len(eligible)}")
print(f"Requests if all eligible positions are selected: {6 * len(eligible)}")
PY

  .venv/bin/python map_hj_candidates.py \
    --input "$sb_dir/eligible.jsonl" --output "$sb_dir/cases_mapped.jsonl"
)
```

The preparer may return exit code 2 when some positions are ineligible. The filter retains prepared positions with three candidates and stops if the preparer recorded a parser error. The full lab input retained **1,053 positions**, or **6,318 requests per model**. Use a fresh directory for a new preparation. Keep the existing mapped input when resuming an API run.

### Generate and score

The following block defaults to Grok and one cursor position, producing **six paid requests**. For a full run, change `sb_scope=smoke` to `sb_scope=full` after preparing the full input. To use GPT-4o, change `provider=grok` to `provider=gpt4o` before pasting.

```bash
(
  set -e
  sb_scope=smoke
  provider=grok
  sb_dir="results/sb-${sb_scope}"

  case "$provider" in
    grok)
      runner=run_hj_xai.py
      model=grok-4.20-0309-non-reasoning
      key_var=XAI_API_KEY
      ;;
    gpt4o)
      runner=run_hj_openai_sb_resume.py
      model=gpt-4o
      key_var=OPENAI_API_KEY
      ;;
    *) echo "Unknown provider: $provider"; exit 1 ;;
  esac

  input="$sb_dir/cases_mapped.jsonl"
  responses="$sb_dir/${provider}_responses.jsonl"
  scores="$sb_dir/${provider}_scores.csv"
  test -f "$input"
  if [ -f "$scores" ]; then
    printf 'Scores already exist: %s\n' "$scores"
    exit 0
  fi

  if [ "$sb_scope" = full ]; then
    selection=(--all-cases)
  else
    selection=(--cursor 10)
  fi
  resume=()
  if [ -f "$responses" ]; then
    resume=(--resume)
  fi

  read -rsp "Enter ${key_var}: " "$key_var"
  printf '\n'
  export "$key_var"

  .venv/bin/python "$runner" run \
    --input "$input" --output "$responses" --model "$model" \
    "${selection[@]}" --temperature 0.5 --max-tokens 512 --delay 0 \
    "${resume[@]}"

  .venv/bin/python "$runner" score \
    --input "$responses" --output "$scores" --sacrebleu
)
```

Run the same block with the same settings to resume an interrupted run. A response error that repeatedly stops the runner needs inspection before another retry. For Small Basic GPT-4o, the resume runner explicitly records zero-token empty completions in the score CSV as `empty_completion`.

### Optional offline syntax validation for Grok Small Basic

After Grok finishes, build the validator once. It uses the Tree-sitter checkout in the benchmark assets.

```bash
.venv/bin/python offline_eval_v1/build_validator.py
```

Validate the saved completions. Change `sb_dir` to `results/sb-full` for the full run.

```bash
sb_dir=results/sb-smoke
.venv/bin/python offline_eval_v1/offline_evaluate.py \
  --cases "$sb_dir/cases_mapped.jsonl" \
  --responses "$sb_dir/grok_responses.jsonl" \
  --scores "$sb_dir/grok_scores.csv" \
  --output-dir "$sb_dir/grok_validation"
```

This step needs the original Small Basic source files and grammar at the paths recorded in the cases. It makes no API calls. The current validator requires a successful generation for each scheduled task, so the command above targets a complete Grok run. A GPT-4o run with an `empty_completion` needs explicit handling of those failures before syntax evaluation.

## 6. Share or inspect the results

Share the pinned commit and the benchmark archive with collaborators. Keep API keys local. Share response and validation archives separately if collaborators need to review already-generated results.

Cases generated on another machine contain that machine's absolute asset paths. For a fresh run, prepare new inputs on your machine. For an existing run, retain its original input JSONL alongside its responses so the manifest hash still matches.

Text scores compare completions with a saved reference. Tree-sitter validation checks parse validity after inserting a completion into the reference span. Neither score establishes runtime behavior. A best-of-three score that uses the reference measures the best available suggestion in hindsight.
