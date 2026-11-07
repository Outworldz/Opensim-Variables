# Opensim-Variables

`INI.CSV` lists every setting OpenSimulator reads from its ini files, one row per setting, with its default value and what it does. It covers:
- the OpenSim core;
- the physics engines: NVIDIA PhysX, BulletSim and ubODE;
- add-on modules such as Diva/Wifi, OpenSimBirds, Gloebit and DTL-PayPal.

Open it in any spreadsheet and filter by section or search for a name. It answers questions like "what is that setting called, and what does it default to?"

It currently has **1,933 settings**. The list comes from the DreamGrid build of OpenSim 0.9.3.x (.NET 8).

## Columns

| Column | What it holds |
|---|---|
| **Section** | The ini section, e.g. `Startup` for `[Startup]`. See "Special section names" for the entries in braces or brackets. |
| **Name** | The setting's name, e.g. `physics`. |
| **Value** | The default (see below). |
| **Type** | What the code reads it as: `string`, `int`, `float`, `bool`, `double`, `long`. |
| **Description** | Taken from the comment above the setting in OpenSim's shipped ini files, or from the code. See below. |
| **SourceFiles** | The C# file and line where OpenSim reads it, so you can see what it really does. |
| **IniFiles** | The ini files and lines where it appears. |

### The Value column
- **One value:** the code default and the shipped ini files agree.
- **`code: X / ini: Y`:** the default in the code is X, but the shipped ini files set Y. Y is what you get unless you change it.
- **`ini: Y (code: expr)`:** the code default isn't a plain value (for example a constant or a calculation), so the ini value is shown.
- **`***`:** the setting holds a password, key or token. You choose your own.

### Where descriptions come from
1. The comment above the setting in `OpenSimDefaults.ini` or an `*.example` file. Most rows (about 1,200) come from these.
2. If there's none, a comment next to the code that reads it, marked "From code:".
3. If there's none either, a description made from the name and code, marked "Inferred from code:". Treat these as hints.

Notes in square brackets add details, for example:
- that a setting is also looked up in other sections;
- that its key is built at runtime;
- that it appears only in ini files and no C# reads it. It may be read by another program, or be obsolete.

### Special section names
- **`(unresolved)`:** the code reads the setting, but the section can't be worked out from the source. These rows are rare.
- **`{name}`:** the section is chosen at runtime, from a variable in the code.
- **`<RegionName>`:** a per-region setting, where the section is the region's name.
- **`Const`:** these values are substituted into other settings as `${Const|Name}`.

## Building it again

`build_ini_csv.py` scans an OpenSim source tree:
- the C# `IConfig` reads (`GetString`, `GetInt`, `GetBoolean`, `GetFloat`, `Util.GetConfigVarFromSections`, BulletSim's parameter table and so on);
- the ini files and their comments.

It also looks inside git submodules such as `addon-modules/PhysX`.

```
python build_ini_csv.py <path to OpenSim source> [output.csv]
```

The output defaults to `INI.CSV` next to the script. It needs Python 3 and `git`, and only reads the source tree.

**Values never come from a grid's own config.** They are taken only from the code, `bin/OpenSimDefaults.ini` and the `*.ini.example` files. A grid's own ini files hold its passwords, host names and IP addresses, and this list is public.

## License

BSD 3-Clause, like OpenSimulator. The descriptions are quoted from OpenSimulator's ini files and source.
