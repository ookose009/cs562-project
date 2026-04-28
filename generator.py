"""
CS 562 - The Project
MF / EMF query processing engine.

Authors: Olisa Okose & Hans Iselborn      CWIDs: 20020781, 

Reads the 6 Phi operands from a file or interactively, then writes a
Python program (_generated.py) that runs the H-table algorithm against
the sales table cursor-by-cursor.
"""
import re
import subprocess
import sys


# Hard-coded sales schema.
SCHEMA = {
    'cust':  'varchar',
    'prod':  'varchar',
    'day':   'int',
    'month': 'int',
    'year':  'int',
    'state': 'varchar',
    'quant': 'int',
    'date':  'date',
}


def parse_phi(text):
    """Parse the 6 Phi operands out of a text block."""
    raw = {'S': '', 'n': '', 'V': '', 'F': '', 'sigma': [], 'G': ''}
    current = None

    headers = [
        ('SELECT CONDITION',    'sigma'),
        ('SELECT ATTRIBUTE',    'S'),
        ('NUMBER OF GROUPING',  'n'),
        ('GROUPING ATTRIBUTES', 'V'),
        ('F-VECT',              'F'),
        ('FVECT',               'F'),
        ('HAVING',              'G'),
    ]

    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith(';'):
            continue

        norm = ' '.join(line.upper().split())
        hit = None
        for prefix, key in headers:
            if norm.startswith(prefix):
                hit = key
                break
        if hit is not None:
            current = hit
            continue
        if current is None:
            continue

        if current == 'sigma':
            raw['sigma'].append(line)
        elif current == 'G':
            raw['G'] = (raw['G'] + ' ' + line).strip()
        elif current == 'n':
            raw['n'] = line
        else:
            raw[current] = (raw[current] + ' ' + line).strip()

    return {
        'S':     [x.strip() for x in raw['S'].split(',') if x.strip()],
        'n':     int(raw['n']) if raw['n'] else 0,
        'V':     [x.strip() for x in raw['V'].split(',') if x.strip()],
        'F':     [x.strip() for x in raw['F'].split(',') if x.strip()],
        'sigma': raw['sigma'],
        'G':     raw['G'],
    }


def read_interactive():
    """Walk the user through entering each Phi operand."""
    print("Enter the Phi-operator expression below.")
    print("(Press <Enter> on an empty line to finish multi-line sections.)\n")

    parts = []
    parts.append("SELECT ATTRIBUTE(S):")
    parts.append(input("  S (comma-separated): "))
    parts.append("NUMBER OF GROUPING VARIABLES(n):")
    parts.append(input("  n: "))
    parts.append("GROUPING ATTRIBUTES(V):")
    parts.append(input("  V (comma-separated): "))
    parts.append("F-VECT([F]):")
    parts.append(input("  F (comma-separated, format <gv>_<func>_<attr>): "))

    parts.append("SELECT CONDITION-VECT([sigma]):")
    print("  predicates (one per line, blank line to end):")
    while True:
        line = input("    ")
        if not line.strip():
            break
        parts.append(line)

    parts.append("HAVING_CONDITION(G):")
    parts.append(input("  G (blank for none): "))

    return '\n'.join(parts)


def convert_expr(expr):
    """Translate a Phi expression (predicate or HAVING clause) into Python."""
    s = expr

    # SQL keywords
    s = re.sub(r'\bAND\b', 'and', s, flags=re.IGNORECASE)
    s = re.sub(r'\bOR\b',  'or',  s, flags=re.IGNORECASE)
    s = re.sub(r'\bNOT\b', 'not', s, flags=re.IGNORECASE)

    # aggregate refs like 1_sum_quant -- avg derived from sum/count
    def agg_repl(m):
        gv, func, attr = m.group(1), m.group(2), m.group(3)
        if func == 'avg':
            return (f"(entry['{gv}_sum_{attr}'] / entry['{gv}_count_{attr}'] "
                    f"if entry['{gv}_count_{attr}'] else 0)")
        return f"entry['{gv}_{func}_{attr}']"
    s = re.sub(r'\b(\d+)_(sum|avg|min|max|count)_(\w+)\b', agg_repl, s)

    # qualified refs: 1.state -> row['state']
    s = re.sub(r'\b\d+\.([A-Za-z_]\w*)', r"row['\1']", s)

    # bare schema columns (skip identifier chars and quoted strings)
    for col in SCHEMA:
        s = re.sub(rf"(?<![\w']){col}(?![\w'])", f"row['{col}']", s)

    # operators
    s = s.replace('<>', '!=')
    s = re.sub(r'(?<![<>=!])=(?!=)', '==', s)

    return s


def collect_aggregates(phi):
    """Return ordered (gv, func, attr) triples that mf_struct must hold.

    Pulls from F-VECT, plus anything mentioned in SELECT or HAVING.
    Each avg implicitly adds its sum and count companions.
    """
    pat = re.compile(r'\b(\d+)_(sum|avg|min|max|count)_(\w+)\b')
    triples = []

    for f in phi['F']:
        m = pat.fullmatch(f)
        if not m:
            raise ValueError(f"Cannot parse aggregate function: {f!r}")
        triples.append((int(m.group(1)), m.group(2), m.group(3)))

    for s in phi['S']:
        m = pat.fullmatch(s)
        if m:
            triples.append((int(m.group(1)), m.group(2), m.group(3)))

    for m in pat.finditer(phi['G'] or ''):
        triples.append((int(m.group(1)), m.group(2), m.group(3)))

    seen, ordered = set(), []
    for t in triples:
        if t in seen:
            continue
        ordered.append(t)
        seen.add(t)
        gv, func, attr = t
        if func == 'avg':
            for extra in [(gv, 'sum', attr), (gv, 'count', attr)]:
                if extra not in seen:
                    ordered.append(extra)
                    seen.add(extra)
    return ordered


def emit_update(gv, aggs, indent):
    """Per-row update statements for grouping variable `gv`."""
    pad = ' ' * indent
    body = []
    for (g, func, attr) in aggs:
        if g != gv:
            continue
        key = f"'{g}_{func}_{attr}'"
        if func == 'sum':
            body.append(f"{pad}entry[{key}] += row['{attr}']")
        elif func == 'count':
            body.append(f"{pad}entry[{key}] += 1")
        elif func == 'min':
            body.append(
                f"{pad}entry[{key}] = (row['{attr}'] if entry[{key}] is None "
                f"else min(entry[{key}], row['{attr}']))"
            )
        elif func == 'max':
            body.append(
                f"{pad}entry[{key}] = (row['{attr}'] if entry[{key}] is None "
                f"else max(entry[{key}], row['{attr}']))"
            )
        # avg has no accumulator -- derived from sum/count at output time
    return '\n'.join(body) if body else f"{pad}pass"


def generate_code(phi):
    """Return the source text of the generated query program."""
    n     = phi['n']
    V     = phi['V']
    S     = phi['S']
    sigma = phi['sigma']
    G     = phi['G']

    if not V:
        raise ValueError("GROUPING ATTRIBUTES (V) must contain at least one column")

    aggs = collect_aggregates(phi)

    # bucket SUCH-THAT predicates by grouping-variable index
    pred_by_gv = {}
    for p in sigma:
        m = re.match(r'\s*(\d+)\.', p)
        if m:
            pred_by_gv.setdefault(int(m.group(1)), []).append(p.strip())

    # initial mf_struct entry body (avg has no accumulator)
    init_lines = [f"                '{v}': row['{v}']," for v in V]
    for (gv, func, attr) in aggs:
        if func == 'avg':
            continue
        init = '0' if func in ('sum', 'count') else 'None'
        init_lines.append(f"                '{gv}_{func}_{attr}': {init},")
    init_block = '\n'.join(init_lines)

    key_tuple = ', '.join(f"row['{v}']" for v in V)

    # scan 0
    if 0 in pred_by_gv:
        gv0_pred = ' and '.join(f"({convert_expr(p)})" for p in pred_by_gv[0])
        gv0_filter = (f"        if not ({gv0_pred}):\n"
                      f"            continue\n")
    else:
        gv0_filter = ''

    scans = []
    scans.append(
        "    # Scan 0: build the mf-structure.\n"
        "    cur.execute('SELECT * FROM sales')\n"
        "    for row in cur:\n"
        f"{gv0_filter}"
        f"        key = ({key_tuple},)\n"
        "        if key not in mf_struct:\n"
        "            mf_struct[key] = {\n"
        f"{init_block}\n"
        "            }\n"
        "        entry = mf_struct[key]\n"
        f"{emit_update(0, aggs, indent=8)}\n"
    )

    # scans 1..n
    for i in range(1, n + 1):
        if i in pred_by_gv:
            pred_expr = ' and '.join(
                f"({convert_expr(p)})" for p in pred_by_gv[i]
            )
        else:
            pred_expr = 'True'
        scans.append(
            f"    # Scan {i}: grouping variable {i}.\n"
            "    cur.execute('SELECT * FROM sales')\n"
            "    for row in cur:\n"
            f"        key = ({key_tuple},)\n"
            "        if key not in mf_struct:\n"
            "            continue\n"
            "        entry = mf_struct[key]\n"
            f"        if {pred_expr}:\n"
            f"{emit_update(i, aggs, indent=12)}\n"
        )

    # output projection
    proj_lines = []
    for col in S:
        if col in V:
            proj_lines.append(f"            '{col}': entry['{col}'],")
            continue
        m = re.fullmatch(r'(\d+)_(sum|avg|min|max|count)_(\w+)', col)
        if not m:
            raise ValueError(
                f"SELECT attribute {col!r} is neither a grouping attribute "
                "nor a parsable aggregate reference"
            )
        gv, func, attr = m.group(1), m.group(2), m.group(3)
        if func == 'avg':
            proj_lines.append(
                f"            '{col}': (entry['{gv}_sum_{attr}'] / "
                f"entry['{gv}_count_{attr}'] "
                f"if entry['{gv}_count_{attr}'] else 0),"
            )
        else:
            proj_lines.append(
                f"            '{col}': entry['{gv}_{func}_{attr}'],"
            )
    proj_block = '\n'.join(proj_lines)

    if G:
        having_check = (f"        if not ({convert_expr(G)}):\n"
                        f"            continue\n")
    else:
        having_check = ''

    # schema comment for mf_struct
    mf_schema_lines = ["# mf_struct entry layout:"]
    for v in V:
        mf_schema_lines.append(f"#   {v:<20} : {SCHEMA.get(v, '?')}")
    for (gv, func, attr) in aggs:
        if func == 'avg':
            continue
        mf_schema_lines.append(f"#   {gv}_{func}_{attr:<14} : int")
    mf_schema_doc = '\n'.join(mf_schema_lines)

    code = f'''"""
Generated query processor for the Phi expression below.
DO NOT EDIT - this file is regenerated by generator.py.

Phi operands:
  S     = {S}
  n     = {n}
  V     = {V}
  F     = {phi['F']}
  sigma = {sigma}
  G     = {G!r}

{mf_schema_doc}
"""
import os
import psycopg2
import psycopg2.extras
import tabulate
from dotenv import load_dotenv


def query():
    """Run the generated EMF/MF query against the 'sales' table."""
    load_dotenv()
    user     = os.getenv('USER')
    password = os.getenv('PASSWORD')
    dbname   = os.getenv('DBNAME')

    conn = psycopg2.connect(
        "dbname=" + dbname + " user=" + user + " password=" + password,
        cursor_factory=psycopg2.extras.DictCursor,
    )
    cur = conn.cursor()

    # H : the mf-structure, keyed by tuple of grouping-attribute values
    mf_struct = {{}}

{''.join(scans)}
    # project, apply HAVING, collect output rows
    out_rows = []
    for key, entry in mf_struct.items():
{having_check}        out_rows.append({{
{proj_block}
        }})

    cur.close()
    conn.close()
    return tabulate.tabulate(out_rows, headers='keys', tablefmt='psql')


def main():
    print(query())


if __name__ == '__main__':
    main()
'''
    return code


def main():
    """Read the Phi input, write _generated.py, then run it."""
    if len(sys.argv) > 1:
        with open(sys.argv[1]) as fh:
            text = fh.read()
    else:
        text = read_interactive()

    phi  = parse_phi(text)
    code = generate_code(phi)

    # Write the generated code to a file
    open("_generated.py", "w").write(code)
    # Execute the generated code
    subprocess.run(["python", "_generated.py"])


if "__main__" == __name__:
    main()
