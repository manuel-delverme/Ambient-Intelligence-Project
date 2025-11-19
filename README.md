# Ambient-Intelligence-Project

This repository now contains a reusable HVAC sizing helper (`hvac_core.py`)
that mirrors the Grasshopper components discussed in the prompt.  You can run
everything from scripts as before or launch the new Flask UI (`app.py`) to
play with the inputs interactively.

## Running the front-end

```bash
python app.py
```

Visit `http://localhost:5000` and you will see cards that let you:

1. Configure the IT load, whitespace counts, redundancy strings, and all
   mechanical parameters.
2. Inspect how the load is divided across white spaces / rows plus the thermal
   cascade from CRAH ➜ pumps ➜ chillers.
3. Review the automatic power-string balancing table plus per-string failure
   summaries that highlight how the load is redistributed and which units were
   lost.

The lightweight HTTP server uses only the Python standard library and calls
into `hvac_core`, so CLI scripts and the UI always stay in sync.

## HVAC helper module

The module exposes:

* `distribute_it_load` – sits between the IT and CRAH stages.  It takes the
  total IT thermal load, the number of white spaces, and an IT row redundancy
  string (e.g. `"6+2"`).  The function returns the white-space descriptions plus
  per-row `PowerConsumer` objects so you can connect them to downstream
  components or the global power-string aggregator.
* `size_crah`, `size_pumps`, `size_chillers` – identical to the original logic
  but CRAH sizing can now accept the whitespace output so that the CRAH units
  are multiplied by the number of white spaces.  Pump sizing gracefully allows
  the pressure drop / head to be driven to zero which in turn zeroes out the
  hydraulic and electrical power.
* `aggregate_power_strings` – merges the IT rows, CRAHs, pumps, and chillers
  while automatically balancing and dual-feeding the units across the required
  number of strings so that the live strings share the load evenly while any
  extra strings remain on standby for redundancy.  It returns a
  `PowerStringAggregate` dataclass with the full per-string breakdown.
* `build_power_string_report` – consumes the aggregate output and produces a
  table per string listing each connected unit with its load/capacity.  The
  helper now enumerates the failure impact for every string automatically by
  routing units to their secondary feeds (or the least-loaded survivor when no
  secondary is defined).

The legacy exercises (`Lab_2.py`, `first_exercise.py`) are left untouched.
