# CS 562 - Project: Query Processing Engine for Ad-Hoc OLAP (MF / EMF)

This is a query processing engine for Ad-Hoc OLAP queries expressed as
operands of the relational Phi (Phi) operator (MF and EMF queries, per
Chatziantoniou & Ross). The engine reads the six Phi operands - either
from a text file or interactively - and emits a stand-alone Python
program (`_generated.py`) that implements the H-table (mf-structure)
evaluation algorithm. The generated program scans the PostgreSQL `sales`
table cursor-by-cursor (no DBMS-side aggregation), maintains the
mf-structure in memory, applies the SUCH-THAT predicates per grouping
variable, evaluates the HAVING clause, and prints the result as a table.

## Schema

The engine targets the `sales` table from the project description:

```
sales(cust, prod, day, month, year, state, quant, date)
```

## Setup

1. Install the runtime dependencies:

   ```
   pip install -r requirements.txt
   ```

2. Copy `.env.example` to `.env` and edit it with your PostgreSQL
   credentials:

   ```
   USER=...
   PASSWORD=...
   DBNAME=...
   ```

   The generated program loads these via `python-dotenv`.

## Phi input format

Each query is described as the six operands of the Phi operator, one
section per header:

```
SELECT ATTRIBUTE(S):
cust, 1_sum_quant, 2_sum_quant, 3_sum_quant
NUMBER OF GROUPING VARIABLES(n):
3
GROUPING ATTRIBUTES(V):
cust
F-VECT([F]):
1_sum_quant, 1_avg_quant, 2_sum_quant, 3_sum_quant, 3_avg_quant
SELECT CONDITION-VECT([sigma]):
1.state='NY'
2.state='NJ'
3.state='CT'
HAVING_CONDITION(G):
1_sum_quant > 2 * 2_sum_quant or 1_avg_quant > 3_avg_quant
```

Aggregate columns use the convention `<gv>_<agg>_<col>` where `<gv>` is
the grouping-variable index (`0` for the group itself / scan 0, and
`1..n` for the n grouping variables). Lines beginning with `;` are
comments.

## Usage

Run the engine with a Phi text file:

```
python generator.py samples/emf_three_gv.txt
```

Or run it interactively (the engine prompts for each section, end each
section with a blank line):

```
python generator.py
```

After generation, the engine compiles and executes `_generated.py`
automatically and prints the query result. You can also re-run the
generated program on its own:

```
python _generated.py
```

## Sample queries

Three sample inputs are included under `samples/`, one for each
milestone:

- `samples/simple_groupby.txt` - plain group-by with a WHERE filter
  (`n=0`, scan-0 only).
- `samples/mf_single_gv.txt` - single grouping variable (one extra
  scan).
- `samples/emf_three_gv.txt` - three grouping variables with a HAVING
  clause (the example from the project description).

## Files

- `generator.py` - the query processing engine (Phi parser + Python
  code generator + driver).
- `_generated.py` - placeholder; overwritten on every run with the
  program emitted for the current Phi input.
- `samples/` - sample Phi inputs.
- `.env.example` - template for the PostgreSQL credentials file.
- `requirements.txt` - runtime dependencies.
