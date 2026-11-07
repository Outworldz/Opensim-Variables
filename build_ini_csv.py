#!/usr/bin/env python3
"""
build_ini_csv.py - catalog every Nini INI Name=Value parameter OpenSimulator reads.

Scans C# sources for IConfig reads (GetString/GetInt/GetBoolean/GetFloat/GetDouble/
GetLong/Get/Contains, Util.GetConfigVarFromSections<T>, Util.GetFirstConfigVarFromSections<T>,
Util.GetConfigVarWithDefaultSection, BulletSim ParameterDefn<T>), scans the shipped INI
files for comment blocks and example values, merges by (Section, Name) and writes INI.CSV.

Usage:  python build_ini_csv.py <opensim_root> [output_csv]
opensim_root is an OpenSim source tree (a git checkout); output defaults to INI.CSV next to this script.
Read-only with respect to .cs/.ini files.
"""
import csv
import os
import re
import subprocess
import sys
from collections import defaultdict, OrderedDict

HERE = os.path.dirname(os.path.abspath(__file__))
if len(sys.argv) < 2:
    sys.exit('Usage: python build_ini_csv.py <opensim_root> [output_csv]')
ROOT = os.path.abspath(sys.argv[1])
OUT = os.path.abspath(sys.argv[2]) if len(sys.argv) > 2 else os.path.join(HERE, 'INI.CSV')

SKIP_DIRS = {'obj', 'bin', '.git', 'tmp', 'Prebuild', 'Tests', 'Test', 'ThirdParty', 'doc'}
INI_SKIP_PREFIXES = ('bin/Regions/', 'bin/Estates/', 'OpenSim/Data/Tests/')


# --------------------------------------------------------------------------- file lists
def list_files():
    files = []
    try:
        for src in ('OpenSim', 'addon-modules'):
            out = subprocess.run(['git', '-C', ROOT, 'ls-files', '-co', '--exclude-standard', '--', src],
                                 capture_output=True, text=True, check=True).stdout
            files += [l.strip() for l in out.splitlines() if l.strip()]
        # submodules (e.g. addon-modules/PhysX) show only as one gitlink entry above: list each one's own files
        out = subprocess.run(['git', '-C', ROOT, 'ls-files', '-s', '--', 'OpenSim', 'addon-modules'],
                             capture_output=True, text=True, check=True).stdout
        for l in out.splitlines():
            if l.startswith('160000'):
                sub = l.split('\t', 1)[1].strip()
                if sub in files:
                    files.remove(sub)
                sout = subprocess.run(['git', '-C', os.path.join(ROOT, sub), 'ls-files', '-co', '--exclude-standard'],
                                      capture_output=True, text=True).stdout
                files += [sub + '/' + x.strip() for x in sout.splitlines() if x.strip()]
        # bin/: list directly (ignored-but-live files such as OpenSim.ini matter, and
        # git walking the huge untracked bin/ tree is very slow)
        b = os.path.join(ROOT, 'bin')
        for fn in os.listdir(b):
            p = os.path.join(b, fn)
            if os.path.isfile(p):
                files.append('bin/' + fn)
            elif fn.startswith('config') or fn == 'addon-modules':
                for dp, dn, fns in os.walk(p):
                    rel = os.path.relpath(dp, ROOT).replace('\\', '/')
                    files += [rel + '/' + x for x in fns]
    except Exception:
        for dp, dn, fn in os.walk(ROOT):
            rel = os.path.relpath(dp, ROOT).replace('\\', '/')
            if rel.startswith('bin/Regions') or rel.startswith('.git'):
                dn[:] = []
                continue
            for f in fn:
                files.append((rel + '/' + f).lstrip('./'))
    return sorted(set(f.replace('\\', '/') for f in files))


def cs_files(all_files):
    res = []
    for f in all_files:
        if not f.endswith('.cs'):
            continue
        parts = f.split('/')
        if any(p in SKIP_DIRS for p in parts[:-1]):
            continue
        res.append(f)
    return res


def ini_files(all_files):
    res = []
    for f in all_files:
        low = f.lower()
        if f.startswith(INI_SKIP_PREFIXES):
            continue
        if '/obj/' in f:
            continue
        in_bin_config = re.match(r'bin/config[^/]*/', f) is not None
        is_ini = low.endswith('.ini') or low.endswith('.ini.example') or low.endswith('.ini.proto')
        if is_ini or (in_bin_config and not low.endswith(('.bak', '.dll', '.exe'))):
            res.append(f)
    return res


def ini_priority(f):
    order = ['bin/OpenSimDefaults.ini', 'bin/OpenSim.ini.example', 'bin/Robust.HG.ini.example',
             'bin/Robust.ini.example']
    if f in order:
        return order.index(f)
    if f.endswith('.example'):
        return 10
    if f.startswith('bin/config-include/'):
        return 20
    if f.startswith('bin/'):
        return 30
    return 40


# --------------------------------------------------------------------------- C# lexing helpers
def strip_comments(src):
    """Return code with comments blanked (same length/newlines) so offsets stay valid."""
    out = list(src)
    i, n = 0, len(src)
    while i < n:
        c = src[i]
        if c == '/' and i + 1 < n and src[i + 1] == '/':
            j = src.find('\n', i)
            j = n if j < 0 else j
            for k in range(i, j):
                out[k] = ' '
            i = j
        elif c == '/' and i + 1 < n and src[i + 1] == '*':
            j = src.find('*/', i + 2)
            j = n if j < 0 else j + 2
            for k in range(i, j):
                if out[k] != '\n':
                    out[k] = ' '
            i = j
        elif c == '@' and i + 1 < n and src[i + 1] == '"':
            i += 2
            while i < n:
                if src[i] == '"':
                    if i + 1 < n and src[i + 1] == '"':
                        i += 2
                        continue
                    i += 1
                    break
                i += 1
        elif c == '"':
            i += 1
            while i < n and src[i] != '"' and src[i] != '\n':
                if src[i] == '\\':
                    i += 1
                i += 1
            i += 1
        elif c == "'":
            i += 1
            while i < n and src[i] != "'" and src[i] != '\n':
                if src[i] == '\\':
                    i += 1
                i += 1
            i += 1
        else:
            i += 1
    return ''.join(out)


def split_args(code, open_idx):
    """code[open_idx] == '('. Return (list_of_arg_strings, close_idx)."""
    depth = 0
    args, cur = [], []
    i, n = open_idx, len(code)
    while i < n:
        c = code[i]
        if c == '"':
            j = i + 1
            while j < n and code[j] != '"':
                if code[j] == '\\':
                    j += 1
                j += 1
            cur.append(code[i:j + 1])
            i = j + 1
            continue
        if c in '([{':
            depth += 1
            if depth > 1:
                cur.append(c)
        elif c in ')]}':
            depth -= 1
            if depth == 0:
                args.append(''.join(cur).strip())
                return [a for a in args if a != ''] if args != [''] else [], i
            cur.append(c)
        elif c == ',' and depth == 1:
            args.append(''.join(cur).strip())
            cur = []
        else:
            if depth >= 1:
                cur.append(c)
        i += 1
    return args, n


def receiver_before(code, dot_idx):
    """Walk back from the '.' of a call to get the receiver expression."""
    i = dot_idx - 1
    while i >= 0 and code[i] in ' \t\r\n':
        i -= 1
    if i >= 0 and code[i] == '?':
        i -= 1
    end = i + 1
    while i >= 0:
        c = code[i]
        if c.isalnum() or c in '_.?':
            i -= 1
        elif c in ')]' and (i == end - 1 or code[i + 1] in '.?['):
            close = c
            opn = '(' if c == ')' else '['
            depth = 0
            while i >= 0:
                if code[i] == close:
                    depth += 1
                elif code[i] == opn:
                    depth -= 1
                    if depth == 0:
                        break
                i -= 1
            i -= 1
        else:
            break
    return code[i + 1:end].strip()


STR_LIT = re.compile(r'^@?"((?:[^"\\]|\\.)*)"$')


def lit_string(expr):
    m = STR_LIT.match(expr.strip())
    return m.group(1) if m else None


LITERAL_RE = re.compile(r'^(-?\d+(\.\d+)?([eE][-+]?\d+)?[fFdDmMlLuU]*|true|false|null|@?"(?:[^"\\]|\\.)*"|'
                        r'[Ss]tring\.Empty|-?\d*\.\d+[fFdD]?)$')


def is_literal(expr):
    return bool(LITERAL_RE.match(expr.strip()))


def display_literal(expr):
    e = expr.strip()
    s = lit_string(e)
    if s is not None:
        return s if s != '' else '""'
    if e in ('String.Empty', 'string.Empty'):
        return '""'
    e = re.sub(r'^(-?[\d.]+(?:[eE][-+]?\d+)?)[fFdDmMlLuU]+$', r'\1', e)
    return e


# --------------------------------------------------------------------------- per-file resolver
class CsFile:
    def __init__(self, rel, raw):
        self.rel = rel
        self.raw = raw
        self.code = strip_comments(raw)
        self.lines = raw.split('\n')
        self.line_starts = [0]
        for m in re.finditer('\n', raw):
            self.line_starts.append(m.end())
        self._const_cache = {}
        self.iconfig_names = set(re.findall(r'\bIConfig\s+(\w+)', self.code))
        self.iconfig_names |= set(m.group(1) for m in re.finditer(
            r'\bvar\s+(\w+)\s*=\s*[^;]*?Configs\s*\[', self.code))

    def lineno(self, idx):
        lo, hi = 0, len(self.line_starts) - 1
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if self.line_starts[mid] <= idx:
                lo = mid
            else:
                hi = mid - 1
        return lo + 1

    def resolve_const(self, name):
        """Resolve an identifier to a string literal defined in this file."""
        name = name.strip()
        if name.startswith('this.'):
            name = name[5:]
        if name in self._const_cache:
            return self._const_cache[name]
        val = None
        n = re.escape(name.split('.')[-1])
        pats = [
            r'\b(?:const\s+)?string\s+' + n + r'\s*=\s*("(?:[^"\\]|\\.)*")',
            r'\b' + n + r'\s*\{\s*(?:\[[^\]]*\]\s*)?get\s*\{\s*return\s*("(?:[^"\\]|\\.)*")',
            r'\b' + n + r'\s*\{\s*(?:\[[^\]]*\]\s*)?get\s*=>\s*("(?:[^"\\]|\\.)*")',
            r'\b' + n + r'\s*=>\s*("(?:[^"\\]|\\.)*")',
            r'(?<![\w.])' + n + r'\s*=\s*("(?:[^"\\]|\\.)*")\s*;',
        ]
        for p in pats:
            m = re.search(p, self.code, re.S)
            if m:
                val = lit_string(m.group(1))
                break
        if val is None:
            # multi-line property: get { ... return "X"; }
            m = re.search(r'\b' + n + r'\s*\{[^{}]*get\s*\{[^{}]*?return\s*("(?:[^"\\]|\\.)*")', self.code, re.S)
            if m:
                val = lit_string(m.group(1))
        self._const_cache[name] = val
        return val

    def resolve_default(self, expr):
        """Return (display, is_literal)."""
        e = expr.strip()
        if is_literal(e):
            return display_literal(e), True
        # cast like (float)3
        m = re.match(r'^\(\s*\w+\s*\)\s*(.+)$', e)
        if m and is_literal(m.group(1)):
            return display_literal(m.group(1)), True
        if re.match(r'^[\w.]+$', e):
            nm = re.escape(e.split('.')[-1])
            m = re.search(r'(?<![\w.])' + nm + r'\s*=\s*([^;=\n]+?)\s*;', self.code)
            if m and is_literal(m.group(1)):
                return display_literal(m.group(1)), True
        return e, False

    def section_of_expr(self, expr, pos, depth=0):
        """Sections referenced by an expression that yields an IConfig."""
        res = []
        for m in re.finditer(r'Configs\s*\[\s*([^\]]+?)\s*\]', expr):
            inner = m.group(1)
            s = lit_string(inner)
            if s is None and re.match(r'^[\w.]+$', inner):
                s = self.resolve_const(inner)
            res.append(s if s is not None else '{' + inner + '}')
            break
        if res:
            return res
        if re.search(r'ScriptEngine\s*\.\s*Config\b', expr):
            return ['YEngine']
        m = re.search(r'AddConfig\s*\(\s*("(?:[^"\\]|\\.)*")', expr)
        if m:
            return [lit_string(m.group(1))]
        e = expr.strip()
        if depth < 3 and re.match(r'^(this\.)?\w+$', e) and e != expr[:0]:
            return self.section_of_var(e, pos, depth + 1)
        return []

    def section_of_var(self, var, pos, depth=0):
        var = var[5:] if var.startswith('this.') else var
        v = re.escape(var)
        cands = []
        for m in re.finditer(r'(?<![\w.])(?:IConfig\s+)?' + v + r'\s*=(?!=)\s*([^;]+);', self.code):
            secs = self.section_of_expr(m.group(1), m.start(), depth)
            if secs:
                cands.append((m.start(), secs))
        for m in re.finditer(r'foreach\s*\(\s*IConfig\s+' + v + r'\s+in\b', self.code):
            cands.append((m.start(), ['{any section}']))
        if cands:
            before = [c for c in cands if c[0] <= pos]
            return (before[-1] if before else cands[0])[1]
        # method parameter?  ... Method(..., IConfig var, ...)
        secs = self.section_of_param(var, pos, depth)
        return secs

    def section_of_param(self, var, pos, depth):
        if depth > 3:
            return []
        sig = None
        for m in re.finditer(r'(\w+)\s*\(([^()]*\bIConfig\s+' + re.escape(var) + r'\b[^()]*)\)\s*(?:\{|:)',
                             self.code):
            if m.start() <= pos:
                sig = m
        if not sig:
            return []
        meth = sig.group(1)
        params = [p.strip() for p in sig.group(2).split(',')]
        idx = next((i for i, p in enumerate(params) if re.search(r'\b' + re.escape(var) + r'$', p)), None)
        if idx is None:
            return []
        found = []
        for m in re.finditer(r'(?<![\w])' + re.escape(meth) + r'\s*\(', self.code):
            if m.start() == sig.start():
                continue
            args, _ = split_args(self.code, m.end() - 1)
            if len(args) > idx and not re.match(r'^\s*IConfig\b', args[idx]):
                s = self.section_of_expr(args[idx], m.start(), depth + 1)
                for x in s:
                    if x not in found:
                        found.append(x)
        if not found and depth == 0 and len(meth) > 4:
            # cross-file: look for callers of this method in other source files
            pat = re.compile(r'(?<![\w])' + re.escape(meth) + r'\s*\(')
            for rel, raw in RAW.items():
                if rel == self.rel or meth not in raw:
                    continue
                other = get_csfile(rel)
                for m in pat.finditer(other.code):
                    args, _ = split_args(other.code, m.end() - 1)
                    if len(args) > idx and not re.match(r'^\s*IConfig\b', args[idx]):
                        for x in other.section_of_expr(args[idx], m.start(), depth + 2):
                            if x not in found and not x.startswith('{'):
                                found.append(x)
        return found

    def sections_list(self, expr, pos):
        e = expr.strip()
        lits = re.findall(r'"((?:[^"\\]|\\.)*)"', e)
        if lits and ('{' in e or '[' in e):
            return lits
        if re.match(r'^\w+$', e):
            cands = []
            for m in re.finditer(r'(?<![\w.])' + re.escape(e) + r'\s*=\s*(new\s+[^;]*|\{[^;]*\}|\[[^;]*\])\s*;',
                                 self.code):
                ls = re.findall(r'"((?:[^"\\]|\\.)*)"', m.group(1))
                if ls:
                    cands.append((m.start(), ls))
            if cands:
                before = [c for c in cands if c[0] <= pos]
                return (before[-1] if before else cands[0])[1]
        return []

    def nearby_comment(self, line):
        """Trailing // comment on the line, or contiguous // comment lines just above."""
        raw = self.lines[line - 1]
        m = re.search(r'//\s*(.+)$', raw)
        if m and '"' not in raw[m.start():] and raw[:m.start()].count('"') % 2 == 0:
            t = m.group(1).strip()
            if not looks_like_code(t):
                return t
        out = []
        i = line - 2
        while i >= 0 and len(out) < 4:
            s = self.lines[i].strip()
            if s.startswith('//'):
                t = s.lstrip('/').strip()
                if t and not looks_like_code(t) and not t.startswith('<'):
                    out.insert(0, t)
                elif t.startswith('<'):
                    pass
                else:
                    break
                i -= 1
            else:
                break
        return ' '.join(out)

    def assigned_var(self, line_text, call_text):
        m = re.search(r'([\w.\[\]]+)\s*(?:=|\?\?=)\s*[^=]*$', line_text[:line_text.find(call_text)]) \
            if call_text in line_text else None
        if m:
            v = m.group(1).split('.')[-1]
            if v not in ('var', 'string', 'int', 'bool', 'float', 'double'):
                return v
        return None

    def decl_comment(self, var):
        """XML <summary> or // comment above a field declaration of var."""
        if not var:
            return ''
        for m in re.finditer(r'^[ \t]*(?:(?:public|private|protected|internal|static|readonly|const|volatile)[ \t]+)+'
                             r'[\w<>\[\],.?]+[ \t]+' + re.escape(var) + r'[ \t]*(=|;|\{)', self.code, re.M):
            ln = self.lineno(m.start())
            out = []
            i = ln - 2
            while i >= 0:
                s = self.lines[i].strip()
                if s.startswith('//'):
                    t = re.sub(r'</?\w+[^>]*>', ' ', s.lstrip('/')).strip()
                    if t and not looks_like_code(t):
                        out.insert(0, t)
                    i -= 1
                elif s.startswith('['):
                    i -= 1
                else:
                    break
            t = ' '.join(out).strip()
            if t:
                return t
        return ''


RAW = {}
_CSF = {}


def get_csfile(rel):
    if rel not in _CSF:
        _CSF[rel] = CsFile(rel, RAW[rel])
    return _CSF[rel]


def looks_like_code(t):
    t = t.strip()
    if t.endswith(';') or t.endswith('{') or t.endswith('}'):
        return True
    if re.search(r'\w+\s*\(.*\)\s*;?$', t) and ('=' in t or '.' in t) and ' ' not in t.split('(')[0].strip():
        return True
    if re.match(r'^(if|else|return|var|for|foreach|while|m_\w+\s*=)\b', t):
        return True
    return False


# --------------------------------------------------------------------------- code scan
TYPED = {'GetString': 'string', 'GetInt': 'int', 'GetBoolean': 'bool', 'GetFloat': 'float',
         'GetDouble': 'double', 'GetLong': 'long', 'Get': 'string', 'Contains': '(presence check)'}
CALL_RE = re.compile(r'\.\s*(GetString|GetInt|GetBoolean|GetFloat|GetDouble|GetLong|Get|Contains)\s*\(')
UTIL_RE = re.compile(r'\b(?:Util\s*\.\s*)?(GetConfigVarFromSections|GetFirstConfigVarFromSections)\s*<\s*([\w.]+)\s*>\s*\(')
UTIL2_RE = re.compile(r'\b(?:Util\s*\.\s*)?GetConfigVarWithDefaultSection\s*\(')
PDEF_RE = re.compile(r'new\s+ParameterDefn\s*<\s*([\w.]+)\s*>\s*\(')
CFGNAME_RE = re.compile(r'(config|cfg|cnf|conf|section|startup|settings|ini)', re.I)

stats = defaultdict(int)


def scan_cs(rel):
    path = os.path.join(ROOT, rel)
    try:
        raw = open(path, encoding='utf-8-sig', errors='replace').read()
    except OSError:
        return []
    if 'Configs' not in raw and 'IConfig' not in raw and 'ParameterDefn' not in raw \
            and 'ConfigVar' not in raw:
        return []
    RAW.setdefault(rel, raw)
    f = get_csfile(rel)
    code = f.code
    reads = []

    def add(name, sections, typ, default, pos, call_text, desc=None, key_dynamic=False, fallback=False):
        sections = ['<RegionName>' if (x and x.startswith('{') and 'RegionName' in x) else x for x in sections]
        line = f.lineno(pos)
        lt = f.lines[line - 1]
        var = f.assigned_var(lt, call_text.split('(')[0]) if call_text else None
        reads.append(dict(name=name, sections=sections, type=typ, default=default, file=rel, line=line,
                          var=var, comment=desc if desc else f.nearby_comment(line),
                          declc=f.decl_comment(var) if var and not desc else '', dyn=key_dynamic, fallback=fallback,
                          ctx=lt.strip()))

    def key_of(arg):
        s = lit_string(arg)
        if s is not None:
            return s, False
        if re.match(r'^[\w.]+$', arg):
            r = f.resolve_const(arg)
            if r is not None:
                return r, False
        return '{' + arg + '}', True

    # IConfig.GetX(...)
    for m in CALL_RE.finditer(code):
        meth = m.group(1)
        recv = receiver_before(code, m.start())
        if not recv:
            continue
        args, close = split_args(code, m.end() - 1)
        if not args:
            continue
        base = recv[5:] if recv.startswith('this.') else recv
        base_last = re.sub(r'\?$', '', base)
        is_cfg = ('Configs[' in recv.replace(' ', '')) or base_last in f.iconfig_names
        if not is_cfg:
            if meth in ('Get', 'Contains'):
                continue
            # typed getter with literal key on an unknown receiver: accept only config-ish names
            if lit_string(args[0]) is None or not CFGNAME_RE.search(base_last) or len(args) > 2:
                continue
            if re.search(r'(Encoding|UTF8|ASCII|Resources|reader|Reader|row|Row|map|Map|args|osd|OSD)', base_last):
                continue
            stats['unknown_receiver_accepted'] += 1
        if meth in ('Get', 'Contains', 'GetString') and lit_string(args[0]) is None and \
                not re.match(r'^[\w.]+$', args[0]):
            pass
        name, dyn = key_of(args[0])
        if 'Configs[' in recv.replace(' ', ''):
            secs = f.section_of_expr(recv, m.start())
        else:
            secs = f.section_of_var(base_last, m.start())
        default = args[1] if len(args) > 1 else ''
        add(name, secs, TYPED[meth], default, m.start(), code[m.start() + 1:m.end()], key_dynamic=dyn)

    # Util.GetConfigVarFromSections<T>(config, "Name", sections, default)
    for m in UTIL_RE.finditer(code):
        if re.search(r'static\s+T\s*$', code[max(0, m.start() - 30):m.start()]):
            continue
        args, close = split_args(code, m.end() - 1)
        if len(args) < 3:
            continue
        name, dyn = key_of(args[1])
        secs = f.sections_list(args[2], m.start())
        default = args[3] if len(args) > 3 else ''
        add(name, secs, m.group(2), default, m.start(), m.group(1), key_dynamic=dyn, fallback=True)

    for m in UTIL2_RE.finditer(code):
        if re.search(r'static\s+string\s*$', code[max(0, m.start() - 30):m.start()]):
            continue
        args, close = split_args(code, m.end() - 1)
        if len(args) < 3:
            continue
        name, dyn = key_of(args[1])
        s2 = lit_string(args[2]) or f.resolve_const(args[2]) if re.match(r'^@?"|^[\w.]+$', args[2]) else None
        secs = ['Startup'] + ([s2] if s2 else ['{' + args[2] + '}'])
        add(name, secs, 'string', '""', m.start(), 'GetConfigVarWithDefaultSection', key_dynamic=dyn, fallback=True)

    # BulletSim parameter table
    if 'ParameterDefn' in code and 'BulletS' in rel:
        for m in PDEF_RE.finditer(code):
            args, close = split_args(code, m.end() - 1)
            if len(args) < 3:
                continue
            name = lit_string(args[0])
            if not name:
                continue
            desc = lit_string(args[1]) or ''
            add(name, ['BulletSim'], m.group(1), args[2], m.start(), None, desc=desc)
    return [(r, f) for r in reads]


# --------------------------------------------------------------------------- INI scan
SEC_RE = re.compile(r'^\s*\[\s*([^\]]+?)\s*\]')
LIVE_KEY_RE = re.compile(r'^\s*([A-Za-z_][\w.\-:]*)\s*=\s*(.*?)\s*$')
CMT_KEY_RE = re.compile(r'^\s*;\s?([A-Za-z_][\w.\-:]*)\s*=\s*(.*?)\s*$')
META_RE = re.compile(r'^\s*;#\s*(.*)$')


def clean_value(v):
    v = v.strip()
    if v.startswith('"'):
        e = v.find('"', 1)
        if e > 0:
            return v[1:e]
    i = v.find(' ;')
    if i >= 0:
        v = v[:i]
    return v.strip()


def parse_meta(text):
    groups = re.findall(r'\{([^}]*)\}', text)
    rest = re.sub(r'\{[^}]*\}', '', text).strip()
    parts = []
    if len(groups) >= 3 and groups[2].strip():
        parts.append(groups[2].strip().rstrip('.') + '.')
    if len(groups) >= 4 and groups[3].strip():
        parts.append('Choices: ' + groups[3].strip() + '.')
    if len(groups) >= 2 and groups[1].strip():
        parts.append('Depends on: ' + groups[1].strip() + '.')
    if rest:
        parts.append('Default: ' + rest + '.')
    return ' '.join(parts)


def clean_comment(lines):
    out = []
    for l in lines:
        t = l.strip()
        mm = META_RE.match(t)
        if mm:
            pm = parse_meta(mm.group(1))
            if pm:
                out.append(pm)
            continue
        t = re.sub(r'^[;#]+', '', t).strip()
        if not t or re.match(r'^[;#=*\-_~ ]+$', t):
            continue
        out.append(t)
    s = ' '.join(out)
    return re.sub(r'\s+', ' ', s).strip()


def scan_ini(rel):
    path = os.path.join(ROOT, rel)
    try:
        lines = open(path, encoding='utf-8-sig', errors='replace').read().split('\n')
    except OSError:
        return []
    entries = []
    section = '(no section)'
    keyline = {}
    kinds = []
    for i, l in enumerate(lines):
        if SEC_RE.match(l) and not l.strip().startswith(';'):
            kinds.append('sec')
            continue
        s = l.strip()
        if not s:
            kinds.append('blank')
        elif s.startswith(';;') or s.startswith(';#') or s.startswith('#'):
            kinds.append('cmt')
        elif s.startswith(';'):
            kinds.append('ckey' if CMT_KEY_RE.match(l) and ' ' not in CMT_KEY_RE.match(l).group(1) else 'cmt')
        else:
            kinds.append('key' if LIVE_KEY_RE.match(l) else 'other')
    for i, l in enumerate(lines):
        k = kinds[i]
        if k == 'sec':
            section = SEC_RE.match(l).group(1)
            if ('OpenSimBirds' in rel or 'OpenSimTide' in rel) and section.lower() != 'startup':
                section = '<RegionName>'
            continue
        if k not in ('key', 'ckey'):
            continue
        m = (LIVE_KEY_RE if k == 'key' else CMT_KEY_RE).match(l)
        name, val = m.group(1), clean_value(m.group(2))
        # comment block above (skip over sibling key lines until a comment is found)
        j = i - 1
        block = []
        while j >= 0:
            kj = kinds[j]
            if kj in ('key', 'ckey'):
                if block:
                    break
                j -= 1
                continue
            if kj == 'cmt':
                block.insert(0, lines[j])
                j -= 1
                continue
            break
        if section.lower() == 'gridservice' and name.startswith('Region_'):
            name = 'Region_<RegionName>'
        entries.append(dict(section=section, name=name, value=val, live=(k == 'key'),
                            comment=clean_comment(block), file=rel, line=i + 1))
    # drop region-definition sections (region files: [RegionName] holding RegionUUID)
    regsecs = set(e['section'] for e in entries if e['name'].lower() == 'regionuuid')
    return [e for e in entries if e['section'] not in regsecs]


# --------------------------------------------------------------------------- descriptions
def humanize(name):
    s = re.sub(r'[_\-.]+', ' ', name)
    s = re.sub(r'(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])', ' ', s)
    return s.strip().lower()


def synth_desc(name, typ, section, var, cls):
    h = humanize(name)
    where = 'read by ' + cls if cls else ''
    tgt = ' (stored in ' + var + ')' if var else ''
    if typ == 'bool':
        if re.match(r'^(Enable|Enabled|Use|Allow|Show|Log|Debug|Disable|Force|Is|Should|Can|Auto)', name):
            first = 'If true, ' + h + '.'
        else:
            first = 'If true, enables "' + h + '".'
    elif typ == '(presence check)':
        first = 'Presence of this key changes behaviour (' + h + ').'
    elif re.search(r'(URI|URL|Url|Uri|Address|Host|Server)$', name):
        first = 'Address/URL for ' + h + '.'
    elif re.search(r'(Port)$', name):
        first = 'Port number for ' + h + '.'
    elif re.search(r'(Timeout|Interval|Delay|Time|Seconds|Period|MS|Ms)$', name):
        first = 'Time value for ' + h + '.'
    elif re.search(r'(Module|Service|Provider|Connector|Plugin|Engine|Handler)s?$', name):
        first = 'Selects the ' + h + ' (DLL:Class or module name).'
    elif re.search(r'(Max|Min|Limit|Count|Size|Number)', name):
        first = 'Limit/size setting: ' + h + '.'
    else:
        first = 'Sets ' + h + '.'
    return 'Inferred from code: ' + first + ' ' + typ + tgt + ', ' + where + '.'


def class_of(rel):
    return os.path.splitext(os.path.basename(rel))[0]


# --------------------------------------------------------------------------- main
# INI.CSV is published. Values come only from the defaults OpenSim ships, never from a grid's own ini files,
# and a value whose name looks like a secret is shown as ***.
SECRET_NAME = re.compile(r'pass|secret|token|apikey|privatekey|readkey|writekey|cert_pass', re.I)


def public_value_file(f):
    return f == 'bin/OpenSimDefaults.ini' or f.endswith('.ini.example')


def main():
    all_files = list_files()
    csl = cs_files(all_files)
    inis = sorted(ini_files(all_files), key=lambda f: (ini_priority(f), f))
    print('C# files: %d   INI files: %d' % (len(csl), len(inis)))

    for rel in csl:
        try:
            raw = open(os.path.join(ROOT, rel), encoding='utf-8-sig', errors='replace').read()
        except OSError:
            continue
        if 'IConfig' in raw or 'Configs' in raw:
            RAW[rel] = raw
    code_reads = []
    for rel in csl:
        code_reads.extend(scan_cs(rel))

    ini_entries = []
    for rel in inis:
        ini_entries.extend(scan_ini(rel))

    # index INI by name for section inference
    ini_by_name = defaultdict(set)
    ini_by_key = defaultdict(list)
    for e in ini_entries:
        ini_by_name[e['name'].lower()].add(e['section'])
        ini_by_key[(e['section'].lower(), e['name'].lower())].append(e)

    rows = OrderedDict()

    def row_for(section, name):
        k = (section.lower(), name.lower())
        if k not in rows:
            rows[k] = dict(section=section, name=name, types=[], defaults=[], literal=[], src=[],
                           code_desc=[], ini=[], also=set(), dyn=False, sect_note=set(), cls=[], vars=[], callers=set())
        return rows[k]

    unresolved = 0
    for r, f in code_reads:
        if r['dyn'] and r['file'].endswith('OpenSim/Framework/Util.cs'):
            continue  # the generic config helpers themselves
        secs = [s for s in r['sections'] if s]
        note = None
        if len(secs) == 1 and secs[0].startswith('{'):
            cand = ini_by_name.get(r['name'].lower(), set())
            if len(cand) == 1:
                note = 'is chosen at runtime in code (%s); INI files show [%s]' % (secs[0], next(iter(cand)))
                secs = [next(iter(cand))]
        if not secs:
            cand = ini_by_name.get(r['name'].lower(), set())
            if len(cand) == 1:
                secs = [next(iter(cand))]
                note = 'section inferred from INI files'
            else:
                secs = ['(unresolved)']
                unresolved += 1
        for s in secs:
            row = row_for(s, r['name'])
            if r['type'] not in row['types']:
                row['types'].append(r['type'])
            dv, lit = f.resolve_default(r['default']) if r['default'] != '' else ('', True)
            if dv not in row['defaults'] and r['default'] != '':
                row['defaults'].append(dv)
                row['literal'].append(lit)
            loc = '%s:%d' % (r['file'], r['line'])
            if loc not in row['src']:
                row['src'].append(loc)
            c = r['comment'] or r['declc']
            if c and c not in row['code_desc']:
                row['code_desc'].append(c)
            if len(secs) > 1:
                row['also' if r['fallback'] else 'callers'].update(x for x in secs if x != s)
            if note:
                row['sect_note'].add(note)
            row['dyn'] = row['dyn'] or r['dyn']
            if class_of(r['file']) not in row['cls']:
                row['cls'].append(class_of(r['file']))
            if r['var'] and r['var'] not in row['vars']:
                row['vars'].append(r['var'])

    # attach INI data; INI-only keys get rows too
    for k, ents in ini_by_key.items():
        if k in rows:
            rows[k]['ini'] = ents
        else:
            e0 = ents[0]
            row = row_for(e0['section'], e0['name'])
            row['ini'] = ents

    out_rows = []
    counts = defaultdict(int)
    for k, row in rows.items():
        ents = sorted(row['ini'], key=lambda e: (ini_priority(e['file']), e['file'], e['line']))
        ini_desc = ''
        for e in ents:
            if e['comment']:
                ini_desc = e['comment']
                break
        ini_vals = []
        for e in ents:
            if not public_value_file(e['file']):
                continue  # a grid's own ini holds its passwords, hosts and IPs
            if e['value'] not in ini_vals and e['value'] != '':
                ini_vals.append(e['value'])
        # value
        code_lit = [d for d, l in zip(row['defaults'], row['literal']) if l]
        code_nonlit = [d for d, l in zip(row['defaults'], row['literal']) if not l]
        code_part = ', '.join(code_lit) if code_lit else ''
        ini_part = ', '.join(ini_vals[:6]) + (' ...' if len(ini_vals) > 6 else '')
        if code_part and ini_part:
            same = [v.strip('"') for v in code_lit] == [v.strip('"') for v in ini_vals] or \
                (len(code_lit) == 1 and code_lit[0].strip('"').lower() in [v.lower() for v in ini_vals[:1]])
            value = code_part if same else 'code: %s / ini: %s' % (code_part, ini_part)
        elif code_part:
            value = code_part
        elif ini_part:
            value = ini_part if not code_nonlit else 'ini: %s (code: %s)' % (ini_part, ', '.join(code_nonlit))
        else:
            value = ('code: ' + ', '.join(code_nonlit)) if code_nonlit else ''
        if value and SECRET_NAME.search(row['name']):
            value = '***'

        # description
        if ini_desc:
            desc = ini_desc
            counts['ini'] += 1
        elif row['code_desc']:
            desc = 'From code: ' + ' / '.join(row['code_desc'][:2])
            counts['code_comment'] += 1
        elif row['src'] and not row['dyn']:
            desc = synth_desc(row['name'], (row['types'] or ['string'])[0], row['section'],
                              row['vars'][0] if row['vars'] else None, ', '.join(row['cls'][:3]))
            counts['code_synth'] += 1
        elif row['src']:
            desc = 'No documentation; key is computed at runtime; read in ' + row['src'][0]
            counts['undoc'] += 1
        else:
            desc = 'No documentation in INI comments.'
            counts['undoc'] += 1
        extras = []
        if not row['src']:
            extras.append('Appears only in INI files; no literal read found in C# (may be read via a dynamic key, by another program, or obsolete).')
        if row['also']:
            extras.append('Also looked up in: ' + ', '.join(sorted(row['also'])) + ' (later sections override earlier).')
        if row['callers']:
            extras.append('Same code also reads it from: ' + ', '.join(sorted(row['callers'])) + ' (section depends on the caller).')
        sl = row['section'].lower()
        if sl == 'const':
            extras.append('[Const] values are substituted into other settings via ${Const|' + row['name'] + '}.')
        elif sl == 'ossl' and row['name'].startswith('Allow_'):
            extras.append('Read dynamically as "Allow_" + OSSL function name (per-function permission).')
        elif sl == 'clientstack.lindencaps' and row['name'].startswith('Cap_'):
            extras.append('Read dynamically as "Cap_" + capability name; value is "" (disabled), "localhost" or a URL.')
        elif sl == 'gridservice' and row['name'] == 'Region_<RegionName>':
            extras.append('One key per region, read dynamically; value is a comma list of region flags.')
        elif sl == '<regionname>':
            extras.append('Per-region setting: section name is the region name.')
        if row['sect_note']:
            extras.append('Section ' + '; '.join(sorted(row['sect_note'])) + '.')
        if row['dyn']:
            extras.append('Key name is not a literal in code.')
        if extras:
            desc = desc + ' [' + ' '.join(extras) + ']'
        typ = ' | '.join(row['types']) if row['types'] else ''
        inifiles = []
        for e in ents:
            t = '%s:%d' % (e['file'], e['line'])
            if t not in inifiles:
                inifiles.append(t)
        out_rows.append([row['section'], row['name'], value, typ, desc, ' | '.join(row['src']),
                         ' | '.join(inifiles)])
        if row['section'] == '(unresolved)':
            counts['unresolved_rows'] += 1
        if row['src']:
            counts['code_rows'] += 1
        else:
            counts['ini_only'] += 1

    out_rows.sort(key=lambda r: (r[0].lower(), r[1].lower()))
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, 'w', encoding='utf-8-sig', newline='') as fh:
        w = csv.writer(fh, quoting=csv.QUOTE_MINIMAL)
        w.writerow(['Section', 'Name', 'Value', 'Type', 'Description', 'SourceFiles', 'IniFiles'])
        w.writerows(out_rows)

    print('Code reads found: %d' % len(code_reads))
    print('Rows: %d  (with code reads: %d, INI-only: %d)' % (len(out_rows), counts['code_rows'], counts['ini_only']))
    print('Descriptions: INI comment %d | code comment %d | name-inferred %d | undocumented %d'
          % (counts['ini'], counts['code_comment'], counts['code_synth'], counts['undoc']))
    print('Unresolved-section reads: %d  -> rows: %d' % (unresolved, counts['unresolved_rows']))
    print('Unknown-receiver typed reads accepted: %d' % stats['unknown_receiver_accepted'])
    print('Wrote', OUT)


if __name__ == '__main__':
    main()
