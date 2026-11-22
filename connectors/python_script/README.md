# python_script

Runs one of the world's Python scripts on a stage's input file and keeps what it prints as the
stage's output. No coding session and no agent: the stage is

```
python3 <assets>/<script> [<further inputs>...] < <input> > <output>
```

so a number, a ranking or any other rule-bound decision comes from code a person can read and
rerun, never from an agent's judgement. A swarm stage writes the input JSON, this stage runs the
script, and a later swarm stage reads the output artifact and applies it.

It is a Connector Package (ADR 0019) installed with Swarmify from this folder but not enabled by
default: enable it for a world on the Connectors page (or `PUT /api/worlds/{id}/packages/python_script`).

## The stage

```yaml
- connector: score
  calls: python_script
  input: score-in.json        # an earlier stage's file, the script's stdin
  output: score-out.json      # the script's stdout, an artifact of this stage
  params:
    script: tools/score.py    # a .py file in the world's assets
    seconds: 60               # the time limit, 1 to 600
```

- `script` must be a `.py` file inside the world's assets folder; any other path fails the stage.
- `input` is stdin (none: empty stdin). With `inputs` too, each of those is an argument after
  the script; with `inputs` alone, the first is stdin and the rest arguments.
- The script runs with the connector's own Python (3.12 or later), standard library only, in the
  output's folder, with a bare environment (no Setup values, no keys).
- A non-zero exit or a script past its `seconds` fails the stage with the last 20 stderr lines,
  and no output is written. A rerun (send-back) simply runs it again.

## Sandbox

It runs in the connector sandbox (ADR 0021) and the script with it: no network at all; reads of
the world's assets (`permissions.folders`) and the run's folders; writes of its own stage folder
only. Its test (`test_python_script.py`) runs a script through the installed package that tries
to write into the assets and dial out, and both are refused.
